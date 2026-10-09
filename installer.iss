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
#define AppVersion "0.2.8"
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
; 简体中文消息文件放在仓库的 installer_languages\ 下，不用官方的
; compiler:Languages\ChineseSimplified.isl —— 后者依赖「构建机的 Inno Setup
; 装没装、装的哪一版、语言文件在不在那个目录」，本地能编 CI 编不了，
; 或者反过来。仓库自带这份就断了这个依赖。
;
; ⚠️ 这里曾经写着 Name: "chinese"; MessagesFile: "compiler:Default.isl"
;    —— 语言名叫 chinese（所以语言名显示得对），消息文件却指向**英文**
;    的 Default.isl，装出来整个向导全是英文。更糟的是没有任何一个测试
;    能发现「向导不是中文的」这件事：没有测试关心语言。
;    现在 tests/test_packaging.py 里钉住了「MessagesFile 不得指向英文」。
;
; 文件必须是 UTF-8 带 BOM（ISCC 靠 BOM 认编码，不带 BOM 按 ANSI 读，
; 中文会乱码且不报错）。installer.iss 自己也一样，两个文件同一个坑。
Name: "chinese"; MessagesFile: "installer_languages\ChineseSimplified.isl"

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
;  [Code] 段：只干一件事——**把「向导用的是哪份消息」报给 CI**。
;
;  ⚠️ 这里曾经**故意不写** [Code] 段，理由已写在下面的注释里。
;     现在加回来，是因为它做的是完全另一件事：不碰 {src}、不碰产物
;     存在性、只在传了 /LANGCHECK 时才动手。
;
;  为什么不改成扫 setup.exe 的字节：
;    试过了，扫不出来。Inno Setup 把 [Messages] 编进 setup.exe 的数据段
;    时是**压缩**的（lzma2），明文不落盘。CI 上实测：未压缩的 SetupLdr
;    存根里能字节搜到「Inno Setup」（自证锚点命中），但
;    「选择目标位置」一条都搜不到——不是搜索方法不对，是那三个字
;    根本不在文件里。这个方向再优化搜索算法也没用。
;
;  为什么不能把中文原样写进文件让 CI 去读：
;    SaveStringToFile 按**系统 ACP** 编码。GitHub runner 的 ACP 是
;    1252（西欧），中文会全变成问号——CI 读到一堆「???」，分不清是
;    「不是中文」还是「编码路过损了」。让 Pascal 自己在进程内判定
;    「这条消息里有没有中日韩字符」，只把 ASCII 结论写出来，编码这层
;    就不参与经过了。
;
;  为什么只在 /LANGCHECK 时才跑：
;    正常用户安装不该多出一个文件。这是给 CI 的探针，不是功能。
; ============================================================
[Code]
function ParamExists(const S: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
  begin
    if CompareText(ParamStr(I), S) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ 这条消息里有没有中日韩统一表意文字（U+4E00–U+9FFF）？
  不查「等不等于某句中文」——那句话改个措辞就得同步改这里，
  忘了改的表现是 CI 报「不是中文」，而实际只是措辞变了。
  判「有没有汉字」则与具体措辞无关。

  直接返回 '1'/'0' 字符串而不是 Boolean：调用侧要用 Ord() 转成
  数字，而 Ord() 在这套 Pascal Script 里是面向字符/整数这些类型的，
  传 Boolean 进去不保证被接受。与其赌编译期，不如一开始就不
  绕那一圈——反正写出去的就是 ASCII 文本。 }
function CjkFlag(const S: String): String;
var
  I, C: Integer;
begin
  { Result 先兜底成 '0'，命中就改成 '1' 再 Exit——Inno 的 Pascal
    Script 支持不带参数的 Exit（返回 Result 当前值），但把这条
    依赖写出来更好：改的人一眼能看到「Exit 前必须先给 Result 赋值」。 }
  Result := '0';
  for I := 1 to Length(S) do
  begin
    C := Ord(S[I]);
    if (C >= $4E00) and (C <= $9FFF) then
    begin
      Result := '1';
      Exit;
    end;
  end;
end;

procedure WriteLangReport;
var
  F: String;
  { cm: 展开的是**当前生效的消息文件**里那一条。这是整个问题的关键：
    MessagesFile 指向 Default.isl 时，这里展开出来的就是英文。 }
  SelDir, SelGroup, Ready, Finish: String;
begin
  SelDir  := ExpandConstant('{cm:WizardSelectDir}');
  SelGroup := ExpandConstant('{cm:WizardSelectProgramGroup}');
  Ready   := ExpandConstant('{cm:FinishedHeadingLabel}');
  Finish  := ExpandConstant('{cm:FinishedLabel}');

  F := 'lang=' + ActiveLanguage + #13#10
     + 'seldir_cjk='   + CjkFlag(SelDir)  + #13#10
     + 'selgroup_cjk=' + CjkFlag(SelGroup) + #13#10
     + 'ready_cjk='    + CjkFlag(Ready)   + #13#10
     + 'finish_cjk='   + CjkFlag(Finish)  + #13#10;
  { 只写 0/1 与 ASCII 键名：这份报告的**全部内容**都保证与文件编码
    无关，见上面关于 ACP 的那段。 }
  { 第三个参数 Append 必须显式给 False——SaveStringToFile 是三参数的
    形式，省掉它编译期就报 "Invalid number of parameters"（CI 上真报在
    这一行，报错文案完全指不到「少了第三个参数」上）。}
  { 落在安装目录而不是安装临时目录：后者的路径形如
    %TEMP%\is-XXXXX\，安装一结束就被删掉。写到那儿的话，CI 读的时候
    文件已经没了，而报错是「找不到语言报告」——指向「探针没跑起来」，
    而真原因是「跑了，但写到了会被删掉的地方」。
    安装目录在卸载之前一直存在，CI 装完立刻读得到。

    ⚠️ 这段注释里刻意不写 Inno 的花括号常量名。Pascal Script 的注释
    用花括号包起来，而花括号**不配对**：注释里出现一个左花括号之后，
    解析会一直吃到下一个右花括号，把中间的字面量当代码，编译期报
    "Syntax error" 且行号落在**下一行**（本次 CI 报在 232 行
    Column 14，真凶是上面那行注释里的一个左花括号）。百分号与
    反斜杠都没问题，只有花括号有这个坑。}
  SaveStringToFile(ExpandConstant('{app}\lang-report.txt'), F, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Err: String;
begin
  { ssInstall 是「真的要开始装了」，此时消息文件已经定下来。
    更早的 InitializeSetup 也行，但 CurStepChanged 顺带能确认
    前面几页都没中止掉——探针跑在真正安装的路径上，测的才是
    用户会遇到的那条路。 }
  if (CurStep <> ssInstall) or not ParamExists('/LANGCHECK') then
    Exit;

  { ⚠️ 探针**必须**吞掉自己的异常。
    不吞的话，它一出问题（常量没展开、临时目录不可写……）就会连带
    中止整个安装，CI 看到的是「静默安装失败 (3)」——退出码 3 是
    「用户取消」，跟探针八竿子打不着，排查方向会被彻底带偏。
    而探针报错时最该做的是**把原因说出来**，不是让安装崩掉。

    这段 try/except 不是防御性冗余，是这个探针能存在的条件：
    它跑在用户的安装路径上，任何未捕获的异常都会变成「装不上」。

    CI 上真栽过：第一版探针没包 try/except，ISCC 编译通过、
    静默安装 3 秒就退出码 3，日志里什么都看不到。 }
  try
    WriteLangReport;
  except
    { 异常消息本身可能含中文（Inno 自己的报错就是中文的），而
      SaveStringToFile 按 ACP 编码，写出去会变问号——所以只把它
      当「有没有出错」的信号，不指望读到内容。要看原文得让安装器
      带 /LOG，那会另开一个日志文件。 }
    Err := GetExceptionMessage;
    SaveStringToFile(ExpandConstant('{app}\lang-report.txt'),
      'lang=' + ActiveLanguage + #13#10 + 'probe_error=1' + #13#10
      + 'probe_error_len=' + IntToStr(Length(Err)) + #13#10, False);
  end;
end;
