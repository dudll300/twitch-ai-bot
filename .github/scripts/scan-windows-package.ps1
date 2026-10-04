[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $PackagePath,
    [string] $ReportPath,
    # Intended for repeat local checks; CI always updates signatures.
    [switch] $SkipSignatureUpdate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-PackageManifest {
    param([string] $Directory)

    $entries = @(Get-ChildItem -LiteralPath $Directory -Recurse -Force)
    foreach ($entry in $entries) {
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Package contains a reparse point: $($entry.FullName)"
        }
    }
    $files = @($entries | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName)
    if ($files.Count -eq 0) {
        throw 'Package contains no files.'
    }
    foreach ($file in $files) {
        [ordered]@{
            Path = $file.FullName.Substring($Directory.Length + 1).Replace('\', '/')
            Length = $file.Length
            SHA256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
}

function Get-DefenderStatus {
    $status = Get-MpComputerStatus
    if (-not $status.AMServiceEnabled -or -not $status.AntivirusEnabled) {
        throw 'Microsoft Defender Antivirus and its service must be enabled for this check.'
    }
    [ordered]@{
        AMServiceEnabled = $status.AMServiceEnabled
        AntivirusEnabled = $status.AntivirusEnabled
        AMProductVersion = $status.AMProductVersion
        AMEngineVersion = $status.AMEngineVersion
        AntivirusSignatureVersion = $status.AntivirusSignatureVersion
        AntivirusSignatureLastUpdated = $status.AntivirusSignatureLastUpdated.ToString('o')
    }
}

function Get-DefenderScanner {
    $platformRoot = Join-Path $env:ProgramData 'Microsoft\Windows Defender\Platform'
    $candidates = @()
    if (Test-Path -LiteralPath $platformRoot -PathType Container) {
        $candidates = @(Get-ChildItem -LiteralPath $platformRoot -Directory | ForEach-Object {
            if ($_.Name -match '^(\d+\.\d+\.\d+\.\d+)-(\d+)$') {
                $scanner = Join-Path $_.FullName 'MpCmdRun.exe'
                if (Test-Path -LiteralPath $scanner -PathType Leaf) {
                    [pscustomobject]@{
                        Version = [version] $Matches[1]
                        Revision = [int] $Matches[2]
                        Path = $scanner
                    }
                }
            }
        })
    }
    if ($candidates.Count -gt 0) {
        return ($candidates | Sort-Object Version, Revision -Descending | Select-Object -First 1).Path
    }
    $fallback = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
    if (Test-Path -LiteralPath $fallback -PathType Leaf) {
        return $fallback
    }
    throw 'Microsoft Defender command-line scanner is unavailable.'
}

function Invoke-DefenderCommand {
    param([string] $Scanner, [string[]] $Arguments)

    # A custom scan with DisableRemediation returns nonzero for unremediated
    # detections or errors. Never accept a failed scan as a successful build.
    $lines = @(& $Scanner @Arguments 2>&1 | ForEach-Object { $_.ToString() })
    $code = $LASTEXITCODE
    [ordered]@{
        ExitCode = $code
        Output = $lines
    }
}

$report = [ordered]@{
    SchemaVersion = 1
    StartedAtUtc = [DateTime]::UtcNow.ToString('o')
    PackagePath = $null
    ScannerPath = $null
    SignatureUpdateSkipped = [bool] $SkipSignatureUpdate
    SignatureUpdate = $null
    BeforeScan = $null
    AfterScan = $null
    Files = @()
    Scan = $null
    Status = 'failed'
    Error = $null
    CompletedAtUtc = $null
}
$failure = $null
$resolvedReport = $null

try {
    $package = Get-Item -LiteralPath $PackagePath
    if ($package.PSProvider.Name -ne 'FileSystem' -or -not $package.PSIsContainer) {
        throw 'PackagePath must be a filesystem directory.'
    }
    if (($package.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'PackagePath must not be a reparse point.'
    }
    $packageRoot = $package.FullName.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $volumeRoot = [IO.Path]::GetPathRoot($package.FullName).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    if ($packageRoot -eq $volumeRoot) {
        throw 'PackagePath must not be a volume root.'
    }
    $report.PackagePath = $packageRoot
    if ([string]::IsNullOrWhiteSpace($ReportPath)) {
        $repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        $ReportPath = Join-Path $repositoryRoot 'build\security-review\package-scan.json'
    }
    $reportTarget = [IO.Path]::GetFullPath($ReportPath)
    $packagePrefix = $packageRoot + [IO.Path]::DirectorySeparatorChar
    if ($reportTarget.StartsWith($packagePrefix, [StringComparison]::OrdinalIgnoreCase) -or $reportTarget -eq $packageRoot) {
        throw 'ReportPath must be outside the package directory.'
    }
    $resolvedReport = $reportTarget
    $report.ScannerPath = Get-DefenderScanner
    $null = Get-DefenderStatus
    Write-Host "Defender scanner: $($report.ScannerPath)"
    if (-not $SkipSignatureUpdate) {
        Write-Host 'Updating Defender signatures from Microsoft...'
        $report.SignatureUpdate = Invoke-DefenderCommand $report.ScannerPath @('-SignatureUpdate', '-MMPC')
        if ($report.SignatureUpdate.ExitCode -ne 0) {
            throw "Defender signature update failed (exit $($report.SignatureUpdate.ExitCode)): $($report.SignatureUpdate.Output -join ' ')"
        }
    }
    $report.BeforeScan = Get-DefenderStatus
    $report.Files = @(Get-PackageManifest $packageRoot)
    $manifestBefore = ConvertTo-Json -InputObject $report.Files -Depth 4 -Compress
    Write-Host "Scanning $($report.Files.Count) files; signatures $($report.BeforeScan.AntivirusSignatureVersion), engine $($report.BeforeScan.AMEngineVersion)."
    $report.Scan = Invoke-DefenderCommand $report.ScannerPath @('-Scan', '-ScanType', '3', '-File', $packageRoot, '-DisableRemediation')
    if ($report.Scan.ExitCode -ne 0) {
        throw "Defender detected a threat or could not complete the scan (exit $($report.Scan.ExitCode)): $($report.Scan.Output -join ' ')"
    }
    $report.AfterScan = Get-DefenderStatus
    $manifestAfter = ConvertTo-Json -InputObject @(Get-PackageManifest $packageRoot) -Depth 4 -Compress
    if ($manifestBefore -cne $manifestAfter) {
        throw 'Package files changed, disappeared, or were added during the scan.'
    }
    $report.Status = 'passed'
    Write-Host 'Defender scan passed; package hashes are unchanged.'
}
catch {
    $failure = $_.Exception.Message
    $report.Error = $failure
}
finally {
    $report.CompletedAtUtc = [DateTime]::UtcNow.ToString('o')
    if ($null -ne $resolvedReport) {
        try {
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedReport) -Force
            $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resolvedReport -Encoding UTF8
            Write-Host "Scan report: $resolvedReport"
        }
        catch {
            $failure = "Could not save scan report: $($_.Exception.Message)"
        }
    }
}
if ($null -ne $failure) {
    Write-Error $failure -ErrorAction Continue
    exit 1
}
exit 0
