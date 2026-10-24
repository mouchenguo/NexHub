param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Client = Join-Path $Root "client"
$Dist = Join-Path $Client "dist"
$Work = Join-Path $Client "build\legacy-optimized"

if (-not $Version) {
    $VersionLine = Select-String -Path (Join-Path $Client "nexhub\paths.py") -Pattern 'VERSION = "([^"]+)"'
    $Version = $VersionLine.Matches.Groups[1].Value
}
if (-not $Version) {
    throw "Cannot read NexHub version."
}

$Python = (Get-Command python.exe).Source
Push-Location $Client
try {
    # v0.1.5 及更早客户端只认单文件 exe：保留 onefile，但剔除运行时不需要的
    # 构建/科学计算模块，关闭 UPX，使用 optimize=2 降低解包和启动开销。
    & $Python -m PyInstaller `
        --noconfirm --clean --onefile --windowed --noupx --optimize 2 `
        --name NexHubLegacy `
        --icon "nexhub\resources\nexhub.ico" `
        --add-data "nexhub\resources;nexhub\resources" `
        --collect-all qfluentwidgets `
        --hidden-import nexhub.plugin_host `
        --hidden-import nexhub.services.memory_optimizer `
        --hidden-import nexhub.npc_admin `
        --hidden-import win32crypt `
        --hidden-import yaml `
        --exclude-module setuptools `
        --exclude-module pkg_resources `
        --exclude-module pip `
        --exclude-module wheel `
        --exclude-module tkinter `
        --exclude-module pytest `
        --exclude-module numpy `
        --exclude-module scipy `
        --exclude-module PIL `
        --exclude-module pandas `
        --exclude-module matplotlib `
        --distpath (Join-Path $Dist "legacy-optimized") `
        --workpath $Work `
        main.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller legacy build failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

$Legacy = Join-Path $Dist "legacy-optimized\NexHubLegacy.exe"
if (-not (Test-Path $Legacy)) {
    throw "Missing legacy onefile output: $Legacy"
}
$Target = Join-Path $Dist "NexHub-v$Version.exe"
Move-Item $Legacy $Target -Force
Write-Host "Legacy onefile: $Target"
Write-Host ("Size: {0:N1} MB" -f ((Get-Item $Target).Length / 1MB))
