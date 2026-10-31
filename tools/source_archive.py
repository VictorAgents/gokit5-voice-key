# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit allowlist exporter: user data is never a source artifact."""
import argparse
from pathlib import Path
import zipfile

FILES=['app.py','runtime.py','flasher.py','installer.py','receiver.py','hotkeys.py','settings.py','hid_config.py','cable_setup.py','device_options.py','audio_assets.py','battery_status.py',
       'control_panel.py','startup.py','run_receiver.py','setup_wifi.py','requirements.txt','requirements-build.txt','requirements-lock.txt',
       'README.md','AUDIO_SETUP_README.md','requirements-audio-setup.txt','Gokit5Community.spec','LICENSE','THIRD_PARTY.md','RELEASE.json','AGENTS.md','.gitignore','.gitattributes']
FOLDERS=['firmware','firmware_bundle','licenses','tools','third_party','docs','.github']
FORBIDDEN={'backups','local-config','private-data','devices','__pycache__','.venv','logs','downloads','vault'}


def archive(root,target):
    root=Path(root).resolve();target=Path(target).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    selected=[root/name for name in FILES if (root/name).is_file()]
    selected.extend(root.glob('test_*.py'))
    for folder in FOLDERS:
        selected.extend(p for p in (root/folder).rglob('*') if p.is_file() and not any(x in FORBIDDEN for x in p.relative_to(root).parts))
    expected={'manifest.json','bootloader.bin','partitions.bin','boot_app0.bin','application.bin'}
    if (root/'firmware_bundle').exists():
        if any(p.name not in expected or p.parent!=root/'firmware_bundle' for p in (root/'firmware_bundle').rglob('*') if p.is_file()):
            raise ValueError('Unexpected file in firmware bundle; source export stopped')
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as output:
        for path in sorted(set(selected)):
            if path.is_symlink() or not path.resolve().is_relative_to(root): raise ValueError('Source path escapes project')
            relative=path.relative_to(root)
            if any(part in FORBIDDEN for part in relative.parts): raise ValueError('Private directory in source export')
            output.write(path,'Gokit5Community/'+relative.as_posix())


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('target',type=Path);args=p.parse_args()
    archive(Path(__file__).resolve().parents[1],args.target)
