; 问墨·code 一键安装包脚本（Inno Setup 6）
; 用 ISCC.exe 编译: ISCC.exe wenmo_installer.iss
; 产出: dist/问墨_installer.exe（一键安装）
; v2 修改：新增 DisableDirPage=no —— 安装时始终显示"选择安装位置"页面，可自由更改安装目录

#define MyAppName "问墨·code"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "问墨"
#define MyAppExeName "问墨.exe"
#define MyAppId "WENMO_CODE_2026_8F3A2C9B"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\问墨
DefaultGroupName=问墨·code
DisableProgramGroupPage=yes
DisableDirPage=no
UsePreviousAppDir=yes
OutputDir=dist
OutputBaseFilename=问墨_installer
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 安装到 Program Files 需要管理员权限（含 python 环境）
PrivilegesRequired=admin
; 允许覆盖旧版本（升级安装）
CloseApplications=yes
RestartApplications=no
; 数据目录在 %APPDATA%，卸载不删用户数据（除非用户选）
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
; 中文界面（语言文件与 .iss 同目录）
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked
Name: "startup"; Description: "开机自动启动问墨"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
; 整个 dist/问墨 目录（含 _internal 依赖）— 相对 .iss 所在目录（agent-tutorial/）
Source: "dist\问墨\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后可选启动
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动问墨·code"; Flags: nowait postinstall skipifsilent

[Registry]
; 卸载时保留用户数据标记（数据在 %APPDATA%\问墨，正常卸载不清除）
Root: HKCU; Subkey: "Software\问墨"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletekey

[UninstallDelete]
; 卸载时删除程序目录残留（数据目录 %APPDATA%\问墨 保留，提示用户手动删）
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\history"
Type: filesandordirs; Name: "{app}\files"
