# Standalone prerequisite review tool: does not contain firmware or flash code.
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $project
try {
    python -m unittest test_cable_setup -q
    if ($LASTEXITCODE -ne 0) { throw 'Audio prerequisite tests failed' }
    python -m PyInstaller --noconfirm --clean --onefile --console --name Gokit5AudioSetup --distpath dist/audio-setup-review --workpath build/audio-setup --specpath build/audio-setup --add-data "$project/licenses;licenses" --add-data "$project/LICENSE;." --add-data "$project/THIRD_PARTY.md;." --collect-all _sounddevice_data cable_setup.py
    if ($LASTEXITCODE -ne 0) { throw 'Audio prerequisite executable build failed' }
    & '.\dist\audio-setup-review\Gokit5AudioSetup.exe' --cable-probe
    if ($LASTEXITCODE -ne 0) { throw 'Packaged audio prerequisite probe failed' }
    Get-FileHash '.\dist\audio-setup-review\Gokit5AudioSetup.exe' -Algorithm SHA256
} finally { Pop-Location }
