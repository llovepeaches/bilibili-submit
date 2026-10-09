# 校验编译产物里的安装向导确实是中文的。
#
# 为什么不直接在 workflow 里内联这段：
#   build-installer.yml 与 release.yml 都需要它。两份内联副本必然会漂移
#   ——改了一边忘了另一边，于是「发布流程里那一份」悄悄退化成旧的坏版本。
#   抽成脚本后只有一个真相源，workflow 只负责调用。
#
# 为什么用**字节级子序列搜索**，而不是把整个文件解码成字符串再 Contains：
#   Inno Setup 6 的字符串在 setup.exe 里是 UTF-16LE，但起始偏移是任意的
#   （PE 资源段的对齐要求决定的）。而 [System.Text.Encoding]::Unicode
#   .GetString($bytes) 只在偏移 0 对齐时才解得出正确字符——中文向导的
#   UTF-16LE 落在奇数偏移时，解出来的是逐字错位的乱码，Contains 永远
#   匹配不上。CI 上真踩过：程序名 bilibili-submit（纯 ASCII）在字节里
#   明明存在，只因整流解码错位，自证锚点先报了错。
#   字节搜索与偏移无关，是这里唯一可靠的读法。
#
# 两种编码都要搜：Inno 6 编出来是 UTF-16LE，但 UTF-8 也一并覆盖，
#   免得哪天 Inno 改了内部存储就静默失效（自证锚点会兜住）。
#
# 自证锚点是**故意**挑的宽：程序名可能压根没进 setup.exe，压缩过或者
#   被丢进资源里都可能。只要 Inno 自己的品牌串在，就说明这套读法有效，
#   下面的断言才是拿一把好尺子在量东西。

param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = 'Stop'

function Test-ByteSubsequence {
    <#
      在 $Haystack 里找 $Needle 是否作为**连续子序列**出现。
      不用 [Array]::IndexOf 直接比整段（PowerShell 会走结构化比较，
      对大数组慢到不可用），而是先拿首字节筛出候选起点，再逐字节比。
      首字节筛对中文尤其有效：UTF-8 首字节 >= 0x80、UTF-16LE 里
      中文字的低位字节也有明显偏向，候选数量比文件长度小几个量级。
    #>
    param(
        [byte[]]$Haystack,
        [byte[]]$Needle
    )

    if ($Needle.Length -eq 0 -or $Haystack.Length -lt $Needle.Length) {
        return $false
    }

    $limit = $Haystack.Length - $Needle.Length
    $from = 0
    while ($true) {
        $i = [Array]::IndexOf($Haystack, $Needle[0], $from)
        if ($i -lt 0 -or $i -gt $limit) {
            return $false
        }
        $hit = $true
        for ($j = 1; $j -lt $Needle.Length; $j++) {
            if ($Haystack[$i + $j] -ne $Needle[$j]) {
                $hit = $false
                break
            }
        }
        if ($hit) {
            return $true
        }
        $from = $i + 1
    }
}

function Test-AnyUtf {
    param(
        [byte[]]$Haystack,
        [string]$Text
    )

    foreach ($enc in @(
            [System.Text.Encoding]::UTF8,
            [System.Text.Encoding]::Unicode)) {
        if (Test-ByteSubsequence -Haystack $Haystack -Needle $enc.GetBytes($Text)) {
            return $true
        }
    }
    return $false
}

if (-not (Test-Path -LiteralPath $Path)) {
    throw "找不到安装器产物：$Path"
}

$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Path))
Write-Host "已读取 $Path（$($bytes.Length) 字节）"

# 先自证扫描手段本身有效。锚点取的是几个大概率在 setup.exe 里出现的
# 品牌/文件名串——只要有一个能被字节搜索捞到，就说明读法没问题。
$anchors = @('Inno Setup', 'bilibili-submit', 'Setup.exe')
$anchorHit = $false
foreach ($anchor in $anchors) {
    if (Test-AnyUtf -Haystack $bytes -Text $anchor) {
        Write-Host "  自证锚点命中：$anchor"
        $anchorHit = $true
        break
    }
}
if (-not $anchorHit) {
    throw ('在安装器里一个锚点串都没捞到（试过：' + ($anchors -join '、') +
        '）——字节搜索方法不对或产物不对，下面的断言不作数')
}

# 刻意挑三条**不含引号、不含 [name] 占位符**的完整句子：
#   · 不含引号——全角引号在 PowerShell 里不是字符串定界符，写进
#     双引号字符串里会被当成结束引号，整段脚本 ParserError，
#     报错位置还在好几行之后，前面的话一句都没跑到（CI 上踩过两次）；
#   · 不含 [name] —— 编译时它会被替换成实际应用名
#     （本项目是「哔哩哔哩自动投稿程序」），拿带占位符的**原文**
#     去比对永远匹配不上；
#   · 够长——「下一步」这种两三个字的在别处也可能命中，捡到一次
#     假阳性这个检查就废了。
$mustHave = @(
    '选择目标位置',       # WizardSelectDir
    '选择开始菜单文件夹',  # WizardSelectProgramGroup
    '正在创建目录'        # StatusCreateDirs
)
foreach ($phrase in $mustHave) {
    if (-not (Test-AnyUtf -Haystack $bytes -Text $phrase)) {
        throw "安装器里找不到中文文案：$phrase —— 向导多半还是英文的"
    }
    Write-Host "  找到中文文案：$phrase"
}

# 反向断言：这两条是向导第一页与语言页的**英文原文**，出现即说明
# 消息文件没生效（Default.isl 被读了回去）。
$mustNot = @('Select Destination Directory', 'Choose Setup Install Language')
foreach ($phrase in $mustNot) {
    if (Test-AnyUtf -Haystack $bytes -Text $phrase) {
        throw "安装器里出现了英文向导文案：$phrase —— MessagesFile 没生效？"
    }
}
Write-Host '安装器向导文案已是中文（反向断言也通过）'
