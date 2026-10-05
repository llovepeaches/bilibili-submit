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
#define AppVersion "0.2.6"
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
; 开始菜单固定一项
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式做成可选项，默认不打勾
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; 装完不自动启动（见 Setup 段说明）。要改的话把下面两行去掉注释：
;Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉 Program Files 下的残留。
; 注意刻意**不删**用户数据（cookie、偏好、投稿历史）——那些在
;   %USERPROFILE%\.config\bilibili_submit\
; 删掉等于让人重新扫码登录，一次痛苦的误操作。不删残留只多占
; 几十 KB，比让人丢 cookie 好得多。
Type: filesandordirs; Name: "{app}\_internal"

[Code]
// 校验装进去的 exe 真的存在。
// 少了这一步，「安装成功但双击闪退」要等到用户手动去找日志才发现。
//
// 路径必须由 {#BuildDir} / {#AppExeName} 拼出来，**不要写死**
// "dist\bilibili-submit-gui\..."：改了 BuildDir 这里会跟着变，
// 而写死的话只有真正编译失败才看得出问题——ISCC 不检查文件是否存在，
// 它照抄 [Files] 的通配路径，装出一个缺文件的安装器。
//
// 但**不能直接**用 '{#BuildDir}' 拼在 {src} 后面：两者的基准不同——
//   · {src}            = 安装器 exe 所在目录，也就是 dist\
//   · [Files] 的 Source = 相对于 .iss 所在目录，也就是仓库根
// 所以 '{src}\' + '{#BuildDir}' 会拼成 dist\dist\bilibili-submit-gui\，
// 文件当然不存在。CI 上真挂过：InitializeSetup 返回 False 让安装中止，
// 而 /SUPPRESSMSGBOXES 又把 MsgBox 压掉了，表现为无声挂起。
// 用 ExtractFileName 取末段，既避开重复前缀，又和 BuildDir 同源。
function InitializeSetup(): Boolean;
var
  BuiltExe: String;
begin
  Result := True;
  BuiltExe := ExpandConstant('{src}\') + ExtractFileName('{#BuildDir}') + '\{#AppExeName}';
  if not FileExists(BuiltExe) then
  begin
    MsgBox('找不到打包产物：' + BuiltExe + #13#10 +
           '请先运行 build_windows.bat（或 CI）打包，再编译安装器。' + #13#10#13#10 +
           '安装器会跳过本次安装。', mbError, MB_OK);
    Result := False;
  end;
end;

// 同上，检查 ffmpeg 在不在。缺了它程序还能跑，只是「cover: auto」
// 自动抽帧用不了——值得提醒但不必拒绝安装。
//
// 注意是 **procedure** 不是 function：Inno Setup 的 InitializeWizard
// 没有返回值，写成 `function ... : Boolean` 时 ISCC 报
// "Invalid prototype for 'InitializeWizard'"——而这类错误只有真正跑
// ISCC 才看得到，本项目的静态检查器查不出来（它不解析 Pascal 原型）。
procedure InitializeWizard();
var
  FFmpegPath: String;
begin
  // 同 InitializeSetup：{src} 已经是 dist\，不能再拼一层 dist\
  FFmpegPath := ExpandConstant('{src}\') + ExtractFileName('{#BuildDir}') + '\ffmpeg.exe';
  if not FileExists(FFmpegPath) then
    Log('提示：产物里没有 ffmpeg.exe，自动抽帧将不可用（不影响其他功能）');
end;
