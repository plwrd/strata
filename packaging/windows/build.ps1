<#
.SYNOPSIS
    Build the Windows package for Strata.

.DESCRIPTION
    Builds the frontend, freezes the Python host with PyInstaller, and (when
    Inno Setup is present) produces an installer.

    Signing is optional and off by default. Point -CertPath (or the
    CODE_SIGN_CERT_PATH environment variable) at an Authenticode .pfx and the
    frozen executable -- and the installer, if one is built -- are signed with a
    SHA-256 digest and an RFC-3161 timestamp, exactly as the release pipeline
    does. A trusted signature is what stops SmartScreen (and process monitors
    that score unsigned software as suspicious) from flagging the build; a
    self-signed certificate does not chain to a trusted root and will not help.
    Without a certificate the build is unsigned and says so -- fine for local
    testing. See docs/security and SECURITY.md.

.EXAMPLE
    .\packaging\windows\build.ps1

.EXAMPLE
    .\packaging\windows\build.ps1 -CertPath C:\keys\strata.pfx -CertPassword (Read-Host -AsSecureString)
#>
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$Installer,
    [string]$CertPath = $env:CODE_SIGN_CERT_PATH,
    [string]$CertPassword = $env:CODE_SIGN_PASSWORD,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
Push-Location $root

# Authenticode-sign one file with the configured certificate. A no-op (with a
# one-line notice) when no certificate was supplied, so an ordinary local build
# is unchanged. The digest and timestamp match the release pipeline so a locally
# signed build is verified the same way a released one is.
function Invoke-Sign {
    param([Parameter(Mandatory)][string]$Path)

    if (-not $CertPath) {
        Write-Warning "UNSIGNED: $([System.IO.Path]::GetFileName($Path)) -- pass -CertPath to sign. SmartScreen and process monitors will treat an unsigned build as untrusted."
        return
    }
    if (-not (Test-Path $CertPath)) { throw "certificate not found: $CertPath" }

    $signtool = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName | Select-Object -Last 1
    if (-not $signtool) { throw 'signtool.exe not found (install the Windows 10/11 SDK)' }

    Write-Host "==> Signing $([System.IO.Path]::GetFileName($Path))" -ForegroundColor Cyan
    & $signtool.FullName sign /f $CertPath /p $CertPassword /fd sha256 /tr $TimestampUrl /td sha256 $Path
    if ($LASTEXITCODE -ne 0) { throw "signing failed for $Path" }
    & $signtool.FullName verify /pa $Path
    if ($LASTEXITCODE -ne 0) { throw "signature verification failed for $Path" }
}

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

    # ffmpeg (LGPL) + Deno for YouTube/X video in the web archive: pinned
    # downloads, SHA-256-verified before anything is extracted.
    Write-Host '==> Fetching bundled tools (ffmpeg, Deno)' -ForegroundColor Cyan
    & $python packaging\tools\fetch_tools.py
    if ($LASTEXITCODE -ne 0) { throw 'fetching the bundled tools failed' }

    Write-Host '==> Freezing the Python host' -ForegroundColor Cyan
    & $python -m PyInstaller --noconfirm --clean packaging\pyinstaller\strata.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

    $exe = Join-Path $root 'dist\Strata\Strata.exe'
    if (-not (Test-Path $exe)) { throw "expected $exe to exist" }
    Write-Host "==> Built $exe" -ForegroundColor Green

    # Sign before the smoke test so what we launch is the artifact we ship.
    Invoke-Sign -Path $exe

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

            $setup = Get-ChildItem (Join-Path $root 'dist') -Filter '*setup*.exe' -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime | Select-Object -Last 1
            if ($setup) { Invoke-Sign -Path $setup.FullName }
        }
    }
}
finally {
    Pop-Location
}
