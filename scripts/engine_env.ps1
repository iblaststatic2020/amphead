<#
    Build the amphead2 engine venv. Idempotent: run it again to repair or upgrade.

    The venv lives outside the repo on purpose. `hermes plugins validate` walks the directory it is
    handed rather than the tracked file set, so a 1.2 GB venv inside the plugin tree turned the
    plugin's security scan into a venv scan that never finished. Nothing in the plugin reads this
    path; the engine module is spawned with it, so override it there if you keep the venv elsewhere.

    Usage:  pwsh -File scripts/engine_env.ps1
#>

$ErrorActionPreference = 'Stop'

$EngineHome = if ($env:AMPHEAD_ENGINE_VENV) { $env:AMPHEAD_ENGINE_VENV }
              else { Join-Path $env:LOCALAPPDATA 'amphead\engine-venv' }
$Python = Join-Path $EngineHome 'Scripts\python.exe'
$Pins = @('pedalboard==0.9.26', 'numpy', 'httpx')

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

# The versions are the point: pedalboard 0.9.26 is the one that hosts the NAM VST3 headlessly, and a
# silent upgrade to a newer audio backend is the kind of change that shows up as a dropout mid-take.
& $Python -c "import sys, pedalboard, numpy, httpx; print(sys.version.split()[0], pedalboard.__version__, numpy.__version__, httpx.__version__)"

if ($env:GITHUB_ACTIONS -or $env:CI) { Write-Host 'engine venv ready' }
