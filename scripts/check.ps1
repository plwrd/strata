<#
.SYNOPSIS
    Run every gate the Definition of Done requires: lint, format, types, tests, build.

.DESCRIPTION
    Exactly what CI runs. If this is green locally, CI should be green too.

.EXAMPLE
    .\scripts\check.ps1
    .\scripts\check.ps1 -Fix     # apply formatting instead of only checking it
#>
[CmdletBinding()]
param([switch]$Fix)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
Push-Location $root

$failures = @()
function Step($name, $block) {
    Write-Host "==> $name" -ForegroundColor Cyan
    & $block
    if ($LASTEXITCODE -ne 0) {
        $script:failures += $name
        Write-Host "    FAILED: $name" -ForegroundColor Red
    }
}

try {
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }

    if ($Fix) {
        Step 'ruff format' { & $python -m ruff format app tests scripts }
        Step 'ruff --fix' { & $python -m ruff check --fix app tests scripts }
        Step 'prettier' { npm --prefix frontend run format }
    } else {
        Step 'ruff check' { & $python -m ruff check app tests scripts }
        Step 'ruff format --check' { & $python -m ruff format --check app tests scripts }
    }

    Step 'mypy' { & $python -m mypy app }
    Step 'pytest' { $env:QT_QPA_PLATFORM = 'offscreen'; & $python -m pytest tests/unit tests/integration tests/security -q }

    Step 'tsc' { npm --prefix frontend run typecheck }
    Step 'eslint' { npm --prefix frontend run lint }
    Step 'vitest' { npm --prefix frontend test }
    Step 'vite build' { npm --prefix frontend run build }

    Step 'plaintext scanner' { & $python scripts\scan_plaintext.py --self-test }
    Step 'desktop shell (e2e)' { $env:QT_QPA_PLATFORM = 'offscreen'; & $python -m pytest tests/e2e -q }

    Write-Host ''
    if ($failures.Count -gt 0) {
        Write-Host "FAILED: $($failures -join ', ')" -ForegroundColor Red
        exit 1
    }
    Write-Host 'All checks passed.' -ForegroundColor Green
}
finally {
    Pop-Location
}
