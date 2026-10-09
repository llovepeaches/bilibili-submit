; ============================================================
;  哔哩哔哩自动投稿程序 —— Windows 安装器（图形界面版）
;
;  编译（Windows 上，需先装 Inno Setup 6）：
;      ISCC.exe installer.iss
;  或用 build_windows.bat / CI 自动完成。
;
;  装出来是什么：
;      C:\Program Files\bilibili-submit\
;          bilibili-submit-gui.exe     ← 程序
;          ffmpeg.exe             ← 封面抽帧用，可自行替换
;          _internal\                 ← Python 运行时，别手动改
;          config\                     ← 配置样例
;          user-guide.html             ← 使用说明（开始菜单与完成页都有入口）
;          README.md
;
;  装到 Program Files 需要管理员权限。Inno Setup 会自动申请 UAC 提权，
;  用户看到的是标准 Windows 安装向导，不需要管理员的选项也做了
;  （见 PrivilegesRequiredOverridesAllowed）。
;
;  ⚠️ 本文件用 UTF-8 带 BOM 编码。Inno Setup 6 能正确识别；
;     存成不带 BOM 的 UTF-8 或 GBK 都会让中文变成乱码，
;     而且不会报错——只在安装向导里显示出来。
; ============================================================

#define AppName "哔哩哔哩自动投稿程序"
#define AppShortName "bilibili-submit"
#define AppVersion "0.2.7-rc.4"
#define AppExeName "bilibili-submit-gui.exe"
; PyInstaller onedir 输出的目录名，必须与 EXE_NAME 一致
#define BuildDir "dist\bilibili-submit-gui"
#define AppPublisher "bili-submit"
#define AppURL "https://github.com/llovepeaches/bilibili-submit"

[Setup]
AppId={{7C4E8B1A-9D2F-4A63-B5E8-3F1D6C9A2E40}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; 装到 Program Files（64位）。第二个是 32 位 Windows 的对应位置。
DefaultDirName={autopf}\{#AppShortName}
DefaultGroupName={#AppName}

; 输出文件名。GUI 版安装器固定叫这个，README 与 Release 说明都引用它。
OutputBaseFilename=bilibili-submit-setup
OutputDir=dist
SetupIconFile=assets\bilibili-submit.ico
UninstallDisplayIcon={app}\{#AppExeName}

; 安装向导图（可选，缺了也不报错）
WizardStyle=modern

; 要管理员，但允许用户选"仅为我安装"装到用户目录。
; 很多人就是自己用一台机器，让他们在 Program Files 里读到只读文件
; 反而添乱。
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline

; 压到最大：安装包小一点，GUI 启动快一点。PyInstaller 里的 Python
; 字节码和 tkinter 都很吃 LZMA，收益比 default 高。
Compression=lzma2/max
SolidCompression=yes

; 装完不自动启动。批量投完的机器上多一个进程很烦，而且用户
; 可能只想装好放着。
;WizardImageFile 留空表示不换背景图
[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

; 不需要 .NET / 不用管理员就能装的路径（VCL 组件一个都不引）。
; 这里刻意**不设** [Components]：装了组件选择页就得给每条 [Files] 写
; Types: 限定，否则用户勾来勾去对文件毫无影响——一个假装能选、
; 其实不能选的选项比没有更糟。GUI 版就一个 exe + 一个 ffmpeg，
; 没有可拆的部分。
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
; onedir 产物整个目录都装进去。
; recursesubdirs 一定要开：_internal\ 下是 Python 运行时，漏了就起不来。
; 这条最容易踩的坑是只写 "dist\bilibili-submit-gui\*" 而忘了
; recursesubdirs——装出来 exe 在、一堆 dll 没了，表现为双击闪退。
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 安装向导的许可页。这里是本项目自己的许可声明，不引用外部文件，
; 免得打包时漏了文件导致编译失败。
[License]
LicenseText=哔哩哔哩自动投稿程序

本程序用于管理你自己账号的视频投稿。

使用须知：
· 请遵守哔哩哔哩平台的社区规范与相关法律法规。
· 批量投稿会消耗接口频控额度，新号尤其容易触发限流，
  请合理设置任务间隔。
· 本程序按「现状」提供，不对任何直接或间接损失负责。

{break}
本程序为个人开源项目，与哔哩哔哩官方无任何关联。

[Icons]
; 开始菜单固定三项：程序本体、使用说明（安装目录里的 user-guide.html，
; 双击用系统默认浏览器打开，离线可看）、卸载
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\使用说明"; Filename: "{app}\user-guide.html"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式做成可选项，默认不打勾
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; 装完不自动启动程序（见 Setup 段说明），但完成页给一个「查看使用说明」
; 的勾选项——第一次装好的人最需要的不是程序本身，是知道怎么用。
; 必须带 shellexec：HTML 不是可执行文件，让 Windows 挑默认浏览器打开。
; skipifsilent：静默安装（CI 冒烟测试）时跳过，不会在 CI 上弹浏览器。
Filename: "{app}\user-guide.html"; Description: "查看使用说明"; Flags: shellexec nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉 Program Files 下的残留。
; 注意刻意**不删**用户数据（cookie、偏好、投稿历史）——那些在
;   %USERPROFILE%\.config\bilibili_submit\
; 删掉等于让人重新扫码登录，一次痛苦的误操作。不删残留只多占
; 几十 KB，比让人丢 cookie 好得多。
Type: filesandordirs; Name: "{app}\_internal"

; ============================================================
;  这里**故意不写** [Code] 段。
;
;  曾经在这里用 InitializeSetup() 检查打包产物是否存在，找不到就让
;  Result := False 中止安装。本意是好的——「别让产物不全的安装器流出去」，
;  但它跑在**安装器启动那一刻**，而 {src} 是安装器 exe 所在的目录：
;
;    · CI 上：setup.exe 就在 dist\ 下，拼出的路径正好是产物目录 → 通过
;    · 用户机器上：setup.exe 在「下载」文件夹里，于是去找
;      下载\bilibili-submit-gui\bilibili-submit-gui.exe —— 当然没有
;      → 弹框「找不到打包产物，请先运行 build_windows.bat」并中止
;
;  结果就是 CI 永远绿、用户永远装不上，而且报的错对用户毫无意义
;  （他是下载来装的人，不是打包的人）。
;
;  产物齐不齐全应该在**构建期**查，那里才有 dist\ 可看：
;  tools/check_installer.py 会校验 dist\<BuildDir> 下的主程序，
;  编译安装器之前跑它即可（build_windows.bat 与 release.yml 都已接上）。
; ============================================================
