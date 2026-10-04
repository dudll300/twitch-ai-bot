[CmdletBinding()]
param([string] $Python = 'python', [string] $Version = '1.8.2')

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $repositoryRoot
try {
    $cache = New-Item -ItemType Directory -Path 'build/nuitka-cache' -Force
    $env:NUITKA_CACHE_DIR = $cache.FullName
    & $Python -m nuitka --mode=onefile --onefile-no-dll --enable-plugin=pyside6 --msvc=latest --jobs=2 `
        --windows-console-mode=disable --windows-icon-from-ico=assets/app.ico `
        --include-data-files=assets/app.ico=assets/app.ico `
        --include-data-files=memory.example.json=memory.example.json `
        --output-dir=build/nuitka --output-filename=TwitchAIBot.exe `
        --report=build/nuitka-report.xml --assume-yes-for-downloads `
        "--file-version=$Version" "--product-version=$Version" `
        '--product-name=Twitch AI Bot' '--file-description=Twitch AI Bot Studio' app.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build failed with exit code $LASTEXITCODE"
    }
    $output = New-Item -ItemType Directory -Path 'dist/singlefile' -Force
    Copy-Item -LiteralPath 'build/nuitka/TwitchAIBot.exe' -Destination (Join-Path $output.FullName 'TwitchAIBot.exe') -Force
    Write-Host "Single-file application: $($output.FullName)/TwitchAIBot.exe"
}
finally {
    Pop-Location
}
