"""
文件交付插件（对标 ChatGPT 的文件能力）：
AI 可以写文件并给出下载链接、打包 zip、发送已有文件、打包 Python 脚本为 exe。
所有产物写入 files/ 目录，通过 /files/xxx 直接下载。
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agent-tutorial/
FILES_DIR = os.path.join(BASE, "files")
WORKSPACE = os.path.join(BASE, "workspace")
os.makedirs(FILES_DIR, exist_ok=True)
os.makedirs(WORKSPACE, exist_ok=True)


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]', "_", str(name))[:60] or "file"


def _resolve_src(path):
    """源文件解析：限定在 workspace / files / 插件 / 技能 / 项目路径内（安全沙箱）"""
    p = os.path.abspath(str(path))
    roots = [FILES_DIR, WORKSPACE, os.path.join(BASE, "plugins"), os.path.join(BASE, "skills")]
    try:
        with open(os.path.join(BASE, "projects.json"), encoding="utf-8") as f:
            for pr in json.load(f).get("projects", []):
                if pr.get("path"):
                    roots.append(os.path.abspath(pr["path"]))
    except Exception:
        pass
    for r in roots:
        if p == r or p.startswith(r + os.sep):
            return p
    raise PermissionError(f"路径不在允许范围：{path}")


def _url(name):
    return f"http://127.0.0.1:8000/files/{name}"


def _make_diff(old, new, name, max_lines=60):
    """生成文件变更摘要与行级 diff（旧→新）"""
    import difflib
    old_lines = (old or "").splitlines(keepends=True)
    new_lines = (new or "").splitlines(keepends=True)
    adds = deletes = 0
    diff_lines = []
    for line in difflib.unified_diff(old_lines, new_lines, fromfile=name, tofile=name, lineterm="\n"):
        if line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletes += 1
        diff_lines.append(line.rstrip("\n"))
    diff_text = "\n".join(diff_lines)
    if len(diff_lines) > max_lines:
        diff_text = "\n".join(diff_lines[:max_lines]) + f"\n…（共 {len(diff_lines)} 行，已截断）"
    return adds, deletes, diff_text


def _check_perm(action):
    """读取权限设置（对标 opencode）：write_files / run_command → allow/ask/deny"""
    try:
        import gui_server
        perms = (gui_server.load_settings().get("permissions") or {})
        return perms.get(action, "allow")
    except Exception:
        return "allow" if action == "write_files" else "ask"


def write_file_with_link(args):
    """写一个文件（Markdown/文本/代码等）并返回下载链接，回答中带上链接即可让用户点击下载"""
    perm = _check_perm("write_files")
    if perm == "deny":
        return "错误：文件写入权限被拒绝（可在 设置 → 通用 → 权限 中修改为「允许」）"
    if perm == "ask" and not args.get("_confirmed"):
        # 权限=询问：系统级弹窗确认（对标 opencode：写文件前询问用户）
        path_t = str(args.get("path", ""))
        return json.dumps({
            "error": "文件写入需要用户确认。",
            "need_confirmation": True,
            "safety": f"将写入文件：{path_t}（{len(str(args.get('content', '')))} 字符）。是否允许？",
        }, ensure_ascii=False)
    path = str(args.get("path", ""))
    content = str(args.get("content", ""))
    # 办公格式必须用专门工具（手写文本 → 文件损坏/乱码）
    low = path.lower()
    if low.endswith((".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls")):
        return ("错误：.{ext} 是二进制办公格式，不能当作文本直接写入（会损坏/乱码）。"
                "请改用 create_pptx 工具生成 PPT；Word/Excel 可用 write_file_with_link 写 .md 或文本格式，"
                "或告知用户当前不支持直接生成该格式。").format(ext=low.rsplit(".", 1)[-1])
    p = _resolve_src(path) if os.path.isabs(path) else os.path.join(WORKSPACE, _safe(path))
    # 写前读旧内容（生成 diff，展示更改情况）
    old = ""
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                old = f.read()
        except Exception:
            old = ""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    name = os.path.basename(p)
    if os.path.dirname(p) != FILES_DIR:
        shutil.copy2(p, os.path.join(FILES_DIR, name))
    # 写入后验证（对标 opencode 文件变更观察）：重读确认内容一致 + 报告字节/行数
    verify = ""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            read_back = f.read()
        ok = read_back == content
        size = os.path.getsize(p)
        lines = read_back.count("\n") + (1 if read_back and not read_back.endswith("\n") else 0)
        verify = "已验证 %d 字节 / %d 行%s" % (size, lines, "（内容一致 ✓）" if ok else "（⚠ 内容不一致！）")
    except Exception as e:
        verify = "验证失败：%s" % str(e)[:60]
    result = "文件已写入：%s\n%s\n下载链接：%s" % (p, verify, _url(name))
    if old != content:
        adds, deletes, diff_text = _make_diff(old, content, name)
        result += f"\n【文件变更】{name} +{adds} -{deletes}\n{diff_text}"
    return result


def create_zip(args):
    """把多个文件打包成 zip 并返回下载链接"""
    zip_name = _safe(str(args.get("zip_name", "打包文件"))) + ".zip"
    sources = args.get("files") or []
    if not sources:
        return "错误：files 参数不能为空（给出要打包的文件路径列表）"
    zpath = os.path.join(FILES_DIR, zip_name)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in sources:
            sp = _resolve_src(str(s))
            if os.path.isfile(sp):
                zf.write(sp, os.path.basename(sp))
            else:
                return f"错误：文件不存在 {sp}"
    return f"已打包 {len(sources)} 个文件。\n下载链接：{_url(zip_name)}"


def send_file(args):
    """把已有的文件（工作区/项目里）复制到下载区，返回下载链接"""
    sp = _resolve_src(str(args.get("path", "")))
    if not os.path.isfile(sp):
        return f"错误：文件不存在 {sp}"
    name = _safe(os.path.basename(sp))
    shutil.copy2(sp, os.path.join(FILES_DIR, name))
    return f"文件已就绪。\n下载链接：{_url(name)}"


def build_exe(args):
    """把 Python 脚本打包成 exe（PyInstaller）并返回下载链接"""
    script = _resolve_src(str(args.get("script_path", "")))
    if not script.endswith(".py") or not os.path.isfile(script):
        return f"错误：需要有效的 .py 脚本路径（当前：{script}）"
    work = os.path.join(BASE, ".pybuild")
    os.makedirs(work, exist_ok=True)
    try:
        out = subprocess.run(
            ["pyinstaller", "--onefile", "--noconfirm",
             "--distpath", FILES_DIR, "--workpath", work, "--specpath", work,
             script],
            capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        return "错误：未安装 PyInstaller。请先运行 pip install pyinstaller"
    except subprocess.TimeoutExpired:
        return "错误：打包超时（10 分钟）"
    if out.returncode != 0:
        return f"打包失败：{out.stderr[-300:]}"
    exe = os.path.join(FILES_DIR, os.path.splitext(os.path.basename(script))[0] + ".exe")
    if os.path.isfile(exe):
        return f"打包完成！\n下载链接：{_url(os.path.basename(exe))}"
    return "打包完成，但未找到 exe 输出，请检查脚本是否有语法错误"


PLUGIN_TOOLS = [
    {"name": "write_file_with_link",
     "description": "把内容写入文件并返回下载/预览链接（写文件、创建文档/代码/报告/笔记/Markdown 等必用它）。"
                    "当用户要求『写文件、创建文件、保存到文件、生成文档/报告/代码文件、给我一个文件』时，"
                    "必须调用本工具把完整内容写入文件并给出链接，而不是只在对话里输出文字。"
                    "参数：path=文件名或相对路径（如 说明文档.md）；content=完整文件内容。"
                    "写完后在回答里引用返回的链接，用户点击即可下载/预览。",
     "parameters": {"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件名或相对路径（写到工作区，可下载）"},
                        "content": {"type": "string", "description": "文件完整内容"}
                    }, "required": ["path", "content"]}, "handler": write_file_with_link},
    {"name": "create_zip", "description": "把多个文件打包成 zip 并返回下载链接（发送文件包）",
     "parameters": {"type": "object",
                    "properties": {
                        "zip_name": {"type": "string", "description": "zip 文件名（不含扩展名）"},
                        "files": {"type": "array", "items": {"type": "string"}, "description": "要打包的文件路径列表"}
                    }, "required": ["zip_name", "files"]}, "handler": create_zip},
    {"name": "send_file", "description": "把工作区/项目里已有的文件复制到下载区，返回下载链接（发送文件给用户）",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string", "description": "文件绝对路径"}},
                    "required": ["path"]}, "handler": send_file},
    {"name": "build_exe", "description": "把 Python 脚本打包成 Windows exe（PyInstaller）并返回下载链接",
     "parameters": {"type": "object",
                    "properties": {"script_path": {"type": "string", "description": ".py 脚本绝对路径"}},
                    "required": ["script_path"]}, "handler": build_exe},
]
