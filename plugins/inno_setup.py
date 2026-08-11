# -*- coding: utf-8 -*-
"""Inno Setup 打包插件：让问墨能把 Windows 软件项目一键打成标准安装包。

三个工具：
  inno_check            —— 检测本机是否安装 Inno Setup，定位 ISCC.exe 与版本
  inno_generate_script  —— 根据项目参数生成专业级 .iss 安装脚本
                           （LZMA2 极限压缩 / 界面语言自适应 / 桌面图标可选 / 干净卸载 / 自动 GUID）
  inno_compile          —— 调用 ISCC.exe 编译 .iss，产出安装包 Setup_*.exe
"""

import os
import re
import subprocess
import time
import uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.join(BASE, "workspace")
SCRIPT_DIR = os.path.join(WORKSPACE, "inno_setup")
DEFAULT_OUTPUT = os.path.join(WORKSPACE, "inno_output")
for _d in (SCRIPT_DIR, DEFAULT_OUTPUT):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass

_ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    r"C:\Program Files\Inno Setup 5\ISCC.exe",
    r"C:\Program Files\Inno Setup\ISCC.exe",
]


def _find_iscc():
    """定位 ISCC.exe：环境变量 → 常见安装路径 → PATH → 注册表"""
    for env in ("INNO_SETUP", "ISCC"):
        v = (os.environ.get(env) or "").strip().strip('"')
        if v:
            p = v if v.lower().endswith("iscc.exe") else os.path.join(v, "ISCC.exe")
            if os.path.isfile(p):
                return p
    for p in _ISCC_CANDIDATES:
        if os.path.isfile(p):
            return p
    for d in (os.environ.get("PATH") or "").split(os.pathsep):
        d = d.strip().strip('"')
        if not d:
            continue
        p = os.path.join(d, "ISCC.exe")
        if os.path.isfile(p):
            return p
    try:
        import winreg
        subs = (
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup_is1",
        )
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in subs:
                try:
                    with winreg.OpenKey(root, sub) as k:
                        loc, _ = winreg.QueryValueEx(k, "InstallLocation")
                    p = os.path.join(loc, "ISCC.exe")
                    if os.path.isfile(p):
                        return p
                except OSError:
                    continue
    except Exception:
        pass
    return None


def _iscc_version(iscc):
    try:
        r = subprocess.run([iscc, "/V"], capture_output=True, text=True,
                           timeout=30, errors="replace")
        text = (r.stdout or r.stderr or "").strip()
        return text.splitlines()[0] if text else "版本未知"
    except Exception:
        return "版本未知"


def _safe(v):
    """去掉会破坏 .iss 语法的字符（引号/换行）"""
    return re.sub(r'["\r\n]', "", str(v or "")).strip()


def _language_block(lang_choice, iscc):
    """按语言参数 + 本机可用语言文件生成 [Languages] 段。返回 (段内容, 提示)"""
    langs_dir = os.path.join(os.path.dirname(iscc), "Languages") if iscc else None

    def has(fname):
        return bool(langs_dir) and os.path.isfile(os.path.join(langs_dir, fname))

    zh_line = 'Name: "chinesesimplified"; MessagesFile: "compiler:Languages\\ChineseSimplified.isl"'
    en_line = 'Name: "english"; MessagesFile: "compiler:Default.isl"'
    zh_missing_note = (
        "本机 Inno Setup 未安装中文语言文件（ChineseSimplified.isl），本次安装包界面为英文"
        "（不影响功能）。如需中文界面：下载 ChineseSimplified.isl 并复制到 Inno Setup 安装目录的 "
        "Languages 文件夹后重新生成脚本。"
    )

    choice = (lang_choice or "auto").strip().lower()
    if choice in ("chinese", "chinesesimplified", "zh-cn", "zh_cn", "cn"):
        if has("ChineseSimplified.isl"):
            return zh_line, ""
        return en_line, zh_missing_note
    if choice == "auto":
        if has("ChineseSimplified.isl"):
            return zh_line, ""
        return en_line, zh_missing_note
    return en_line, "未识别的语言参数“%s”，已使用英文界面。" % (lang_choice or "")


def inno_check_handler(arguments: dict) -> dict:
    """检测本机 Inno Setup 安装情况"""
    iscc = _find_iscc()
    if not iscc:
        return {
            "installed": False,
            "message": "未检测到 Inno Setup。请前往官网 https://jrsoftware.org/isdl.php 下载安装 Inno Setup 6"
                       "（一路默认安装即可）。安装完成后重新检测，或把安装目录加入 PATH。",
            "hint": "默认安装位置：C:\\Program Files (x86)\\Inno Setup 6",
        }
    ver = _iscc_version(iscc)
    langs_dir = os.path.join(os.path.dirname(iscc), "Languages")
    has_zh = os.path.isfile(os.path.join(langs_dir, "ChineseSimplified.isl"))
    return {
        "installed": True,
        "iscc_path": iscc,
        "version": ver,
        "chinese_ui_available": has_zh,
        "message": "已检测到 Inno Setup：%s（%s）%s，可以直接生成脚本并编译安装包。" % (
            iscc, ver,
            "，含简体中文界面" if has_zh else "（无简体中文语言文件，安装包界面将用英文）"),
    }


def inno_generate_script_handler(arguments: dict) -> dict:
    """根据项目参数生成专业级 .iss 安装脚本"""
    app_name = _safe(arguments.get("app_name"))
    version = _safe(arguments.get("version")) or "1.0.0"
    publisher = _safe(arguments.get("publisher")) or ""
    app_url = _safe(arguments.get("app_url")) or ""
    exe_name = _safe(arguments.get("exe_name")) or (app_name + ".exe" if app_name else "")
    source_dir = _safe(arguments.get("source_dir"))
    output_dir = _safe(arguments.get("output_dir")) or DEFAULT_OUTPUT
    icon_path = _safe(arguments.get("icon_path")) or ""
    excludes = _safe(arguments.get("excludes")) or "*.pdb,_nuitka_temp.exe"
    desktop_icon = str(arguments.get("desktop_icon", "true")).lower() not in ("false", "0", "no")
    language = arguments.get("language") or "auto"

    if not app_name:
        return {"error": "缺少必填参数 app_name（软件显示名称，如：我的软件）"}
    if not source_dir:
        return {"error": "缺少必填参数 source_dir（要打包的源文件目录绝对路径，如 D:\\project\\dist）"}
    if not os.path.isdir(source_dir):
        return {"error": "源目录不存在：%s" % source_dir}

    source_dir = os.path.abspath(source_dir)
    output_dir = os.path.abspath(output_dir)
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        return {"error": "输出目录创建失败：%s" % e}

    iscc = _find_iscc()
    lang_lines, lang_note = _language_block(language, iscc)

    guid = str(uuid.uuid4()).upper()          # 形如 {XXXXXXXX-XXXX-...}
    iss_guid = "{{%s}}" % guid                 # iss 语法 AppId={{GUID}}

    icon_lines = ""
    if icon_path and os.path.isfile(icon_path):
        icon_lines = (
            'SetupIconFile="%s"\nUninstallDisplayIcon={app}\\{#MyAppExeName}' % icon_path
        )

    run_lines = ""
    if exe_name:
        run_lines = (
            '[Run]\n'
            'Filename: "{app}\\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; '
            'Flags: nowait postinstall skipifsilent'
        )

    script = (
        '; =====================================================================\n'
        ';  %s v%s —— Inno Setup 安装脚本（由 问墨·code 自动生成）\n'
        ';  特性：LZMA2 极限压缩 | 中/英文界面自适应 | 完整元数据 | 干净卸载\n'
        '; =====================================================================\n'
        '\n'
        '; --- 1. 参数定义 ---\n'
        '#define MyAppName        "%s"\n'
        '#define MyAppVersion     "%s"\n'
        '#define MyAppPublisher   "%s"\n'
        '#define MyAppURL         "%s"\n'
        '#define MyAppExeName     "%s"\n'
        '#define MySourceDir      "%s"\n'
        '#define MyOutputDir      "%s"\n'
        '\n'
        '[Setup]\n'
        '; --- 身份识别（每次生成独立 GUID，避免覆盖冲突） ---\n'
        'AppId=%s\n'
        'AppName={#MyAppName}\n'
        'AppVersion={#MyAppVersion}\n'
        'AppPublisher={#MyAppPublisher}\n'
        'AppPublisherURL={#MyAppURL}\n'
        'AppSupportURL={#MyAppURL}\n'
        'AppUpdatesURL={#MyAppURL}\n'
        '\n'
        '; --- 安装路径与权限 ---\n'
        'DefaultDirName={autopf}\\{#MyAppName}\n'
        'DefaultGroupName={#MyAppName}\n'
        'DisableDirPage=no\n'
        'DisableProgramGroupPage=no\n'
        'PrivilegesRequired=admin\n'
        '\n'
        '; --- 输出设置 ---\n'
        'OutputDir={#MyOutputDir}\n'
        'OutputBaseFilename=Setup_{#MyAppName}_v{#MyAppVersion}\n'
        '\n'
        '; --- 视觉体验 ---\n'
        'WizardStyle=modern\n'
        '%s\n'
        '\n'
        '; --- 核心压缩（极限） ---\n'
        'Compression=lzma2/ultra64\n'
        'SolidCompression=yes\n'
        'LZMAUseSeparateProcess=yes\n'
        '\n'
        '[Languages]\n'
        '%s\n'
        '\n'
        '[Files]\n'
        'Source: "{#MySourceDir}\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs '
        'createallsubdirs; Excludes: "%s"\n'
        '\n'
        '; --- 干净卸载：安装目录整体清除 ---\n'
        '[UninstallDelete]\n'
        'Type: filesandordirs; Name: "{app}\\*"\n'
        '\n'
        '[Icons]\n'
        'Name: "{autodesktop}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"; Tasks: desktopicon\n'
        'Name: "{group}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"\n'
        'Name: "{group}\\卸载 {#MyAppName}"; Filename: "{uninstallexe}"\n'
        '\n'
        '[Tasks]\n'
        'Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; '
        'GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked\n'
        '\n'
        '%s\n'
    ) % (
        app_name, version,
        app_name, version, publisher, app_url, exe_name, source_dir, output_dir,
        iss_guid,
        icon_lines,
        lang_lines,
        excludes,
        run_lines,
    )

    fname = "%s_v%s.iss" % (re.sub(r'[\\/:*?"<>|]', "_", app_name), version)
    fpath = os.path.join(SCRIPT_DIR, fname)
    try:
        with open(fpath, "w", encoding="utf-8-sig") as f:
            f.write(script)
    except Exception as e:
        return {"error": "脚本写入失败：%s" % e}

    note = "已生成 Inno Setup 脚本：%s。下一步调用 inno_compile 编译。" % fpath
    if lang_note:
        note += "\n" + lang_note
    return {
        "ok": True,
        "script_path": fpath,
        "output_dir": output_dir,
        "output_exe": "Setup_%s_v%s.exe" % (app_name, version),
        "language": "chinese" if "chinesesimplified" in lang_lines else "english",
        "language_note": lang_note,
        "note": note,
    }


def inno_compile_handler(arguments: dict) -> dict:
    """调用 ISCC.exe 编译 .iss 脚本，产出安装包"""
    script = str(arguments.get("script", "")).strip()
    if not script:
        return {"error": "缺少参数 script（.iss 脚本路径，可用 inno_generate_script 生成）"}
    if not os.path.isfile(script):
        # 尝试在 SCRIPT_DIR 下匹配
        alt = os.path.join(SCRIPT_DIR, os.path.basename(script))
        if os.path.isfile(alt):
            script = alt
        else:
            return {"error": "脚本文件不存在：%s" % script}

    iscc = _find_iscc()
    if not iscc:
        return {
            "error": "未检测到 Inno Setup，无法编译。请先安装 Inno Setup 6（https://jrsoftware.org/isdl.php），"
                     "然后用 inno_check 确认环境。",
            "need_install": True,
        }

    timeout = max(30, min(int(arguments.get("timeout") or 300), 300))
    try:
        r = subprocess.run(
            [iscc, "/Qp", script],
            capture_output=True, text=True, timeout=timeout, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"error": "编译超时（%ds）。安装包可能较大，可加大 timeout 重试。" % timeout}
    except Exception as e:
        return {"error": "编译启动失败：%s" % e}

    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return {"error": "编译失败（exit=%d）：\n%s" % (r.returncode, err[-3000:])}

    # 在输出目录找最新生成的 exe
    out_dir = str(arguments.get("output_dir") or "").strip()
    candidates = [out_dir, DEFAULT_OUTPUT]
    exe_path = None
    for d in candidates:
        if not d or not os.path.isdir(d):
            continue
        hits = []
        for root, _, files in os.walk(d):
            for f in files:
                if f.lower().endswith(".exe"):
                    p = os.path.join(root, f)
                    hits.append((os.path.getmtime(p), p))
        if hits:
            hits.sort(reverse=True)
            exe_path = hits[0][1]
            break
    if not exe_path:
        return {
            "ok": True,
            "message": "编译完成（exit=0），但未在输出目录找到安装包，请检查脚本中的 OutputDir。",
            "script": script,
        }
    size_mb = os.path.getsize(exe_path) / 1048576
    return {
        "ok": True,
        "installer": exe_path,
        "size_mb": round(size_mb, 2),
        "message": "安装包编译成功：%s（%.2f MB）。可直接分发到其他 Windows 电脑安装。" % (exe_path, size_mb),
    }


PLUGIN_TOOLS = [
    {
        "name": "inno_check",
        "description": "检测本机是否安装 Inno Setup：自动定位 ISCC.exe（环境变量/常见安装路径/PATH/注册表）"
                       "并返回版本、是否支持简体中文界面。未安装时给出下载地址。打包前先调用本工具确认环境。",
        "parameters": {"type": "object", "properties": {}},
        "handler": inno_check_handler,
    },
    {
        "name": "inno_generate_script",
        "description": "生成专业级 Inno Setup 安装脚本 .iss（LZMA2 极限压缩 / 界面语言自适应 / 桌面图标可选 / "
                       "干净卸载 / 自动 GUID）。参数：app_name=软件显示名称（必填）；version=版本号（默认1.0.0）；"
                       "publisher=发布者/公司名；app_url=官网；exe_name=主程序文件名（默认=软件名.exe）；"
                       "source_dir=要打包的源目录绝对路径（必填，如 D:\\project\\dist）；"
                       "output_dir=安装包输出目录（默认 workspace/inno_output）；icon_path=.ico 图标路径（可选）；"
                       "excludes=排除文件（默认 *.pdb,_nuitka_temp.exe）；desktop_icon=是否创建桌面图标（默认 true）；"
                       "language=界面语言：auto 自动（本机有中文语言包则中文否则英文）/ chinese 中文 / english 英文（默认 auto）。"
                       "生成脚本后调用 inno_compile 编译。",
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "软件显示名称（必填）"},
                "version": {"type": "string", "description": "版本号，默认 1.0.0"},
                "publisher": {"type": "string", "description": "发布者/公司名"},
                "app_url": {"type": "string", "description": "官网地址（可选）"},
                "exe_name": {"type": "string", "description": "主程序文件名，默认 软件名.exe"},
                "source_dir": {"type": "string", "description": "要打包的源文件目录绝对路径（必填）"},
                "output_dir": {"type": "string", "description": "安装包输出目录（默认 workspace/inno_output）"},
                "icon_path": {"type": "string", "description": ".ico 图标文件路径（可选）"},
                "excludes": {"type": "string", "description": "排除的文件模式，默认 *.pdb,_nuitka_temp.exe"},
                "desktop_icon": {"type": "boolean", "description": "是否创建桌面图标，默认 true"},
                "language": {"type": "string", "description": "界面语言：auto/chinese/english，默认 auto"}
            },
            "required": ["app_name", "source_dir"],
        },
        "handler": inno_generate_script_handler,
    },
    {
        "name": "inno_compile",
        "description": "调用 ISCC.exe 编译 .iss 脚本，产出安装包 Setup_*.exe。"
                       "参数：script=.iss 脚本路径（inno_generate_script 生成）；"
                       "output_dir=输出目录（可选，默认 workspace/inno_output）；timeout=超时秒数（默认300）。"
                       "需本机已安装 Inno Setup（先用 inno_check 确认）。",
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": ".iss 脚本路径"},
                "output_dir": {"type": "string", "description": "输出目录（可选）"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 300"}
            },
            "required": ["script"],
        },
        "handler": inno_compile_handler,
    },
]
