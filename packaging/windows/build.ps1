<#
.SYNOPSIS
    Build the Windows package for Strata.

.DESCRIPTION
    Builds the frontend, freezes the Python host with PyInstaller, and (when
    Inno Setup is present) produces an installer.

    Signing is not performed here. A release build must be signed with the
    organisation's certificate — see docs/security and SECURITY.md. An unsigned
    build is for local testing only, and SmartScreen will say so.

.EXAMPLE
    .\packaging\windows\build.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$Installer
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
Push-Location $root

try {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }

    if (-not $SkipFrontend) {
        Write-Host '==> Building the frontend' -ForegroundColor Cyan
        & $python scripts\sync_qwebchannel.py
        npm --prefix frontend ci
        npm --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'frontend build failed' }
    }

    Write-Host '==> Freezing the Python host' -ForegroundColor Cyan
    & $python -m PyInstaller --noconfirm --clean packaging\pyinstaller\strata.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

    $exe = Join-Path $root 'dist\Strata\Strata.exe'
    if (-not (Test-Path $exe)) { throw "expected $exe to exist" }
    Write-Host "==> Built $exe" -ForegroundColor Green

    # A packaged build that has never been started is not a build that works.
    Write-Host '==> Smoke-testing the packaged executable' -ForegroundColor Cyan
    $process = Start-Process -FilePath $exe -PassThru
    Start-Sleep -Seconds 8
    if ($process.HasExited) {
        throw "the packaged application exited immediately (code $($process.ExitCode))"
    }
    Stop-Process -Id $process.Id -Force
    Write-Host '==> Packaged application starts' -ForegroundColor Green

    if ($Installer) {
        $iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
        if (-not $iscc) {
            Write-Warning 'Inno Setup (iscc.exe) is not on PATH; skipping the installer.'
        } else {
            Write-Host '==> Building the installer' -ForegroundColor Cyan
            & $iscc.Source 'packaging\windows\strata.iss'
            if ($LASTEXITCODE -ne 0) { throw 'installer build failed' }
        }
    }
}
finally {
    Pop-Location
}
