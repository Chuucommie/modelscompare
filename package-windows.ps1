$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot

try {
    python -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the package build dependencies."
    }

    python scripts\build_release.py --skip-install
    if ($LASTEXITCODE -ne 0) {
        throw "The release build failed."
    }
}
finally {
    Pop-Location
}
