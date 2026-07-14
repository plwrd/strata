<#
.SYNOPSIS
    Run Strata in development mode.

.DESCRIPTION
    Starts the Vite dev server and points the desktop shell at it, so the frontend
    hot-reloads while still talking to the *real* Python bridge. Development mode
    does not mock Python: a mock you develop against is a mock you ship against.

.EXAMPLE
    .\scripts\dev.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
Push-Location $root

try {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { throw 'Run: python -m venv .venv; .\.venv\Scripts\pip install -e ".[dev]"' }

    & $python scripts\sync_qwebchannel.py

    Write-Host '==> Starting the Vite dev server' -ForegroundColor Cyan
    $vite = Start-Process -FilePath 'npm' -ArgumentList '--prefix', 'frontend', 'run', 'dev' -PassThru -NoNewWindow

    # Wait for the port rather than sleeping blind.
    $deadline = (Get-Date).AddSeconds(40)
    do {
        Start-Sleep -Milliseconds 400
        $ready = Test-NetConnection -ComputerName 127.0.0.1 -Port 5173 -InformationLevel Quiet -WarningAction SilentlyContinue
    } while (-not $ready -and (Get-Date) -lt $deadline)

    if (-not $ready) { throw 'the Vite dev server did not start on 127.0.0.1:5173' }

    Write-Host '==> Starting the desktop shell against the dev server' -ForegroundColor Cyan
    $env:STRATA_ENV = 'development'
    $env:STRATA_DEV_SERVER = 'http://127.0.0.1:5173'
    & $python -m app.main
}
finally {
    if ($vite -and -not $vite.HasExited) { Stop-Process -Id $vite.Id -Force }
    Remove-Item Env:\STRATA_DEV_SERVER -ErrorAction SilentlyContinue
    Remove-Item Env:\STRATA_ENV -ErrorAction SilentlyContinue
    Pop-Location
}
