<#
    Build the engine venv: Python 3.11 with numpy and sounddevice, the only two dependencies.
    Idempotent, run it again to repair or upgrade. Needs uv (https://docs.astral.sh/uv/).

    Defaults to %LOCALAPPDATA%\amphead\engine-venv so the venv is not inside the plugin tree:
    `hermes plugins validate` walks the directory it is handed rather than the tracked file set,
    so a venv in the plugin directory turns the security scan into a venv scan. Point the plugin at
    the result with AMPHEAD_ENGINE_PYTHON or `amphead_settings action=set engine_python=...`.

    Usage:  pwsh -File scripts/engine_env.ps1
#>

$ErrorActionPreference = 'Stop'

$EngineHome = if ($env:AMPHEAD_ENGINE_VENV) { $env:AMPHEAD_ENGINE_VENV }
              else { Join-Path $env:LOCALAPPDATA 'amphead\engine-venv' }
$Python = Join-Path $EngineHome 'Scripts\python.exe'
$Pins = @('numpy==2.4.6', 'sounddevice==0.5.6')

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is not on PATH. Install it first: https://docs.astral.sh/uv/'
}

if (-not (Test-Path $Python)) {
    Write-Host "creating $EngineHome"
    uv python install 3.11
    uv venv $EngineHome --python 3.11
}

Write-Host "installing $($Pins -join ', ')"
& $Python -m pip install --disable-pip-version-check --quiet @Pins
if ($LASTEXITCODE -ne 0) { throw "pip install failed with $LASTEXITCODE" }

# Exact pins, not floors: numpy 2.4.6 and sounddevice 0.5.6 are the versions this engine has been
# measured with, and a silent upgrade to a newer audio stack is the kind of change that shows up as a
# dropout mid-take. These are the only two downloads in the whole plugin, and this script is the only
# thing that makes them; the plugin itself never installs anything.
& $Python -c "import sys, numpy, sounddevice; print(sys.version.split()[0], numpy.__version__, sounddevice.__version__)"

if ($env:GITHUB_ACTIONS -or $env:CI) { Write-Host 'engine venv ready' }
