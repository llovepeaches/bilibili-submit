# 校验编译出来的安装向导确实是中文的。
#
# 为什么不扫 setup.exe 的字节（走过弯路，别再走回去）：
#   试过。[Messages] 编进 setup.exe 时是**压缩**的（lzma2），明文不落盘。
#   CI 上实测：未压缩的 SetupLdr 存根里能字节搜到「Inno Setup」
#   （自证锚点命中，证明搜索方法本身没问题），但「选择目标位置」
#   一条都搜不到——那三个字根本不在文件里。搜索算法再怎么优化也没用。
#
# 现在这条路的形状：
#   1. 以 /LANGCHECK 静默安装一遍（探针，只在有这个开关时才动作）
#   2. installer.iss 的 [Code] 段用 {cm:...} 取出**运行时真正生效**的
#      那几条消息，在进程内判定「有没有汉字」，把 ASCII 结论写进
#      %TEMP%\lang-report.txt
#   3. 这里读那个文件
#
# 为什么让 Pascal 判定而不把中文原样写出来：
#   SaveStringToFile 按系统 ACP 编码，GitHub runner 是 1252（西欧），
#   中文会全变问号。CI 读到「???」时分不清是「不是中文」还是
#   「编码路过损了」——两种情况的排查方向完全不同。
#
# 为什么判「有没有汉字」而不判「等不等于某句中文」：
#   措辞改了就得同步改这里，忘了改的表现是 CI 报「不是中文」，
#   而实际只是翻译换了个词。判有无汉字与措辞无关。
#
# 为什么用 /LANGCHECK 开关：
#   正常用户安装不该凭空多出一个文件。这是探针，不是功能。

param(
    [Parameter(Mandatory = $true)]
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'

# 主路径是 app 目录那一份（权威位置）。它缺席时先看系统临时目录的
# 副本——探针会往两处都写，两处的有无组合能区分三种情况：
#   两处都没有 → 探针压根没跑（没传 /LANGCHECK，或 [Code] 没编进去）
#   只有临时目录有 → 探针跑了，但 app 目录当时还不存在/不可写
#   两处都有 → 正常
# 判据本身不变：读到的报告要满足的条件一条没少。
$fallback = Join-Path $env:TEMP 'lang-report.txt'
$actual = $ReportPath
if (-not (Test-Path -LiteralPath $actual)) {
    if (Test-Path -LiteralPath $fallback) {
        Write-Host ("主路径没有报告，但系统临时目录有副本：$fallback —— " +
            '探针跑了，app 目录那一份没写成（目录当时不存在或不可写）')
        $actual = $fallback
    } else {
        throw ("两处都找不到语言报告：$ReportPath 与 $fallback —— " +
            '安装器里的 /LANGCHECK 探针没跑起来。可能是 [Code] 段没编进去，' +
            '也可能是静默安装那一步漏传了 /LANGCHECK。先看安装日志，别直接改判据。')
    }
}

$report = @{}
foreach ($line in Get-Content -LiteralPath $actual -Encoding ASCII) {
    if ($line -match '^([a-z_]+)=(.+)$') {
        $report[$Matches[1]] = $Matches[2].Trim()
    }
}
Write-Host "语言报告内容：$($report | Out-String)"

# 0) 探针自己报错了 —— 这是**先**要看的。探针跑在安装路径上，
#    它一崩安装就中止（退出码 3 = 「用户取消」，跟探针毫无关系，
#    排查方向会被带偏）。所以它自带 try/except 把原因写进报告。
#    这一条必须排在最前面，否则下面会报「语言是 chinese 但四项
#    都缺」，而真原因是探针压根没跑完。
if ($report.ContainsKey('probe_error')) {
    throw ("安装器里的探针自己抛异常了（异常消息长度 " +
        "$($report['probe_error_len']) 字符）。看 %TEMP% 下 Inno 自己" +
        '的安装日志（CI 那步传了 /LOG）——消息是中文的，ACP 编码后' +
        '这里读不到内容。**不是向导的问题，是探针没跑成**。')
}

# 1) 语言名。Name 写的是 chinese，但那只是下拉框显示的名字，
#    MessagesFile 才是决定文案的那一半——这个坑栽过。
if ($report['lang'] -ne 'chinese') {
    throw "语言是 $($report['lang'])，不是 chinese —— [Languages] 段被改动了？"
}

# 2) 四条向导文案都得含汉字。四条分别对应向导第一页、开始菜单页、
#    完成页标题与完成页正文，覆盖安装器的主要人机界面。
$mustHave = @(
    'seldir_cjk',    # WizardSelectDir
    'selgroup_cjk',  # WizardSelectProgramGroup
    'ready_cjk',     # FinishedHeadingLabel
    'finish_cjk'     # FinishedLabel
)
$bad = @()
foreach ($key in $mustHave) {
    if (-not $report.ContainsKey($key)) {
        throw "报告里没有 $key 这一项——[Code] 段可能被改残了，先看 installer.iss"
    }
    if ($report[$key] -ne '1') {
        $bad += $key
    }
}
if ($bad.Count -gt 0) {
    throw ("这几条向导文案里没有汉字：$($bad -join ', ') —— " +
        'MessagesFile 没生效，装出来是英文向导')
}

Write-Host '安装器向导文案已是中文（4/4 条含汉字，语言名 chinese）'
