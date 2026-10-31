# Run in an environment with requirements-build.txt installed.
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $project
try {
    python -m unittest discover -s . -p 'test_*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    python app.py --self-test
    if ($LASTEXITCODE -ne 0) { throw 'Firmware bundle invalid' }
    python -m PyInstaller --noconfirm --clean --onefile --console --name Gokit5Community --add-data 'firmware_bundle;firmware_bundle' --add-data 'licenses;licenses' --add-data 'LICENSE;.' --add-data 'THIRD_PARTY.md;.' --collect-all esptool --collect-all esp_pylib --collect-all _sounddevice_data --copy-metadata esptool --hidden-import receiver --hidden-import control_panel --hidden-import setup_wifi --hidden-import run_receiver --hidden-import sounddevice --hidden-import numpy --hidden-import cable_setup --hidden-import device_options --hidden-import audio_assets --hidden-import battery_status app.py
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
    & '.\dist\Gokit5Community.exe' --self-test
    if ($LASTEXITCODE -ne 0) { throw 'Packaged self-test failed' }
    Get-FileHash '.\dist\Gokit5Community.exe' -Algorithm SHA256
} finally { Pop-Location }
