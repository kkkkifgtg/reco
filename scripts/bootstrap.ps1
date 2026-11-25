#Requires -Version 5.1
<#
.SYNOPSIS
  reco bootstrap: create the local .venv, install uv inside it, editable-install the
  seven workspace packages and run the full pytest suite.

.DESCRIPTION
  Steps (all idempotent, safe to re-run):
    1. Detect a Python >= 3.12 interpreter (RECO_PYTHON, DSH bundled runtime, py launcher,
       python3.12/python3/python on PATH) and print its path.
    2. Create .venv only when missing.
    3. Install uv inside .venv (never system-wide).
    4. Editable-install packages/reco-{core,store,labels,relations,io,cli,ui}.
    5. Run the full pytest suite from the repo root.

  Caches are redirected into .venv so nothing is written outside the checkout.
  Exit code: 0 on success, non-zero at the first failing step.
#>
[CmdletBinding()]
param(
    [string]$PythonPath = $env:RECO_PYTHON
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$VenvDir = Join-Path $Root '.venv'
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
$UvExe = Join-Path $VenvDir 'Scripts\uv.exe'

function Test-PythonVersion {
    param(
        [string]$Exe,
        [string[]]$Prefix = @()
    )
    if (-not $Exe) { return $false }
    try {
        & $Exe @Prefix -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-Python {
    $candidates = @()
    if ($PythonPath) {
        $candidates += , @{ Exe = $PythonPath; Prefix = @() }
    }
    $dsh = Join-Path $HOME '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $dsh) {
        $candidates += , @{ Exe = $dsh; Prefix = @() }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += , @{ Exe = 'py'; Prefix = @('-3.12') }
        $candidates += , @{ Exe = 'py'; Prefix = @('-3') }
    }
    foreach ($name in @('python3.12', 'python3', 'python')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $candidates += , @{ Exe = $cmd.Source; Prefix = @() }
        }
    }
    foreach ($c in $candidates) {
        if (Test-PythonVersion -Exe $c.Exe -Prefix $c.Prefix) {
            return $c
        }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Error "No Python >= 3.12 found. Set RECO_PYTHON to an interpreter path."
    exit 1
}

$display = (@($python.Exe) + @($python.Prefix)) -join ' '
Write-Host "[1/5] Python interpreter: $display"
$version = & $python.Exe @($python.Prefix) -c 'import sys; print(sys.version.split()[0])'
Write-Host "      version: $version"

if (Test-Path -LiteralPath $VenvPy) {
    Write-Host "[2/5] Reusing existing venv: $VenvDir"
} else {
    Write-Host "[2/5] Creating venv: $VenvDir"
    & $python.Exe @($python.Prefix) -m venv $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPy)) {
        Write-Error "Failed to create venv at $VenvDir"
        exit 1
    }
}

if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $VenvDir '.uv-cache' }
if (-not $env:UV_LINK_MODE) { $env:UV_LINK_MODE = 'copy' }

if (Test-Path -LiteralPath $UvExe) {
    Write-Host "[3/5] uv already installed in .venv"
} else {
    Write-Host "[3/5] Installing uv into .venv (not system-wide)"
    & $VenvPy -m pip install --no-cache-dir --disable-pip-version-check uv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $UvExe)) {
        Write-Error "Failed to install uv into $VenvDir"
        exit 1
    }
}

Write-Host "[4/5] Syncing the locked workspace and dev dependencies"
& $UvExe sync --frozen --inexact --python $VenvPy
if ($LASTEXITCODE -ne 0) {
    Write-Error "Workspace sync failed"
    exit 1
}

Write-Host "[5/5] Running pytest (repo root)"
& $VenvPy -m pytest
$testExit = $LASTEXITCODE
if ($testExit -ne 0) {
    Write-Error "pytest failed with exit code $testExit"
    exit $testExit
}

Write-Host "Bootstrap complete: $VenvPy"
exit 0
