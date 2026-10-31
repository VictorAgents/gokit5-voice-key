# SPDX-License-Identifier: GPL-3.0-or-later
"""Create a release from compiler outputs only, never from a device flash dump."""
import argparse
import hashlib
import json
from pathlib import Path

NAMES=[(0,'voice_key.ino.bootloader.bin','bootloader.bin'),(0x8000,'voice_key.ino.partitions.bin','partitions.bin'),
       (0xE000,'boot_app0.bin','boot_app0.bin'),(0x10000,'voice_key.ino.bin','application.bin')]


def build(source,destination):
    source,destination=Path(source),Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    files=[]
    for offset,original,name in NAMES:
        data=(source/original).read_bytes()
        if name in ('bootloader.bin','application.bin') and (data[0]!=0xE9 or int.from_bytes(data[12:14],'little')!=9):
            raise ValueError('Not an ESP32-S3 compiler output')
        (destination/name).write_bytes(data)
        files.append({'offset':offset,'name':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    # Build-time assertion only. Post-build device acceptance belongs in RELEASE.json.
    manifest={'version':'0.13.0-alpha.1','profile':'gizwits-4key-s3-16m-8m','flash_bytes':16*1024*1024,
              'maturity':'alpha','second_device_verified':False,'files':files}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('build_dir',type=Path);p.add_argument('destination',type=Path)
    args=p.parse_args();build(args.build_dir,args.destination)
