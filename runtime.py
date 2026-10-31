# SPDX-License-Identifier: GPL-3.0-or-later
"""Separate immutable application assets from private per-device user data."""
import json
import os
from pathlib import Path
import re
import sys
import shutil
import hashlib

CODE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
DATA_ROOT = Path(os.environ.get('GOKIT5_DATA_DIR', Path(os.environ.get('LOCALAPPDATA', Path.home()))/'Gokit5Community'))
VERSION = '0.13.0-alpha.1'


def normalize_id(value):
    value = str(value).replace(':', '').replace('-', '').lower()
    if not re.fullmatch(r'[0-9a-f]{12}', value):
        raise ValueError('无效设备身份')
    return value


def active_id():
    try:
        return normalize_id(json.loads((DATA_ROOT/'active-device.json').read_text(encoding='utf-8'))['device_id'])
    except (OSError, ValueError, KeyError):
        return None


def device_dir(identity=None):
    identity = normalize_id(identity) if identity is not None else active_id()
    return DATA_ROOT/'devices'/(identity or 'unconfigured')


def set_active(identity):
    identity = normalize_id(identity)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    p = DATA_ROOT/'active-device.json'
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps({'device_id': identity}), encoding='utf-8')
    os.replace(temp, p)


def launch_command(action, *arguments):
    if getattr(sys, 'frozen', False):
        installed=DATA_ROOT/'app/Gokit5Community.exe'
        metadata=installed.with_suffix('.json')
        try: preferred=json.loads(metadata.read_text(encoding='utf-8'))['version']==VERSION and installed.is_file()
        except (OSError,ValueError,KeyError): preferred=False
        return [str(installed) if preferred else sys.executable, action, *map(str, arguments)]
    interpreter=Path(sys.executable)
    windowless=interpreter.with_name('pythonw.exe')
    if os.name=='nt' and action in ('--installer','--panel','--wifi','--bootstrap') and windowless.is_file():
        interpreter=windowless
    return [str(interpreter), str(CODE_ROOT/'app.py'), action, *map(str, arguments)]


CONFIG_PATH = device_dir()/'receiver.json'


def install_runtime():
    if not getattr(sys,'frozen',False): return Path(sys.executable)
    target=DATA_ROOT/'app/Gokit5Community.exe'
    source=Path(sys.executable)
    identical=False
    if target.is_file():
        with source.open('rb') as a,target.open('rb') as b:
            identical=hashlib.file_digest(a,'sha256').digest()==hashlib.file_digest(b,'sha256').digest()
    if source.resolve()!=target.resolve() and not identical:
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_suffix('.tmp')
        shutil.copyfile(sys.executable,temp)
        os.replace(temp,target)
    target.with_suffix('.json').write_text(json.dumps({'version':VERSION}),encoding='utf-8')
    return target
