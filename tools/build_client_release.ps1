param(
    [string]$Version = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Client = Join-Path $Root "client"
$Dist = Join-Path $Client "dist"
$AppDir = Join-Path $Dist "NexHub"
$InstallerDir = Join-Path $Dist "installer"

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
    & $Python -m PyInstaller `
        --noconfirm --clean --onedir --windowed `
        --name NexHub `
        --icon "nexhub\resources\nexhub.ico" `
        --add-data "nexhub\resources;nexhub\resources" `
        --collect-all qfluentwidgets `
        --hidden-import nexhub.plugin_host `
        --hidden-import nexhub.services.memory_optimizer `
        --hidden-import nexhub.npc_admin `
        --hidden-import win32crypt `
        --hidden-import yaml `
        main.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

$Exe = Join-Path $AppDir "NexHub.exe"
if (-not (Test-Path $Exe)) {
    throw "Missing onedir output: $Exe"
}

$Portable = Join-Path $Dist "NexHub-v$Version-win64.zip"
Remove-Item $Portable -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $Portable -Force
Write-Host "Portable: $Portable"

if (-not $SkipInstaller) {
    $Candidates = @(
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    $Iscc = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) {
        throw "Inno Setup 6 not found. Install it or run with -SkipInstaller."
    }
    New-Item -ItemType Directory -Force -Path $InstallerDir | Out-Null
    & $Iscc "/DMyAppVersion=$Version" (Join-Path $Client "installer\NexHub.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
    $Installer = Join-Path $InstallerDir "NexHub-Setup-$Version.exe"
    if (-not (Test-Path $Installer)) {
        throw "Missing installer: $Installer"
    }
    Write-Host "Installer: $Installer"
}

Write-Host "Application folder: $AppDir"
