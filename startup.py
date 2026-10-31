# SPDX-License-Identifier: GPL-3.0-or-later
"""User login startup registration, limited to this project's named Run value."""
import json
import subprocess
import winreg
from settings import atomic_json
from runtime import DATA_ROOT, launch_command
ROOT = DATA_ROOT

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'Gokit5CommunityReceiver'


def command():
    return subprocess.list2cmdline(launch_command('--bootstrap'))


def current_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)[0]
    except FileNotFoundError:
        return None


def is_enabled():
    return current_value() == command()


def set_enabled(enabled):
    before = current_value()
    expected = command()
    if before is not None and before != expected:
        raise OSError('同名登录启动项已存在，未修改；请先核对原有设置')
    snapshot = ROOT/'local-config/startup-before.json'
    if not snapshot.exists():
        atomic_json(snapshot, {'name': VALUE_NAME, 'before': before})
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, expected)
        elif before is not None:
            winreg.DeleteValue(key, VALUE_NAME)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--enable', action='store_true')
    p.add_argument('--disable', action='store_true')
    args = p.parse_args()
    if args.enable or args.disable:
        set_enabled(args.enable)
    print('Gokit5 login startup:', 'enabled' if is_enabled() else 'disabled')
