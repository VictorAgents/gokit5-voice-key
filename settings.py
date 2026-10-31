# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-secret, validated and atomic desktop settings."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path

from hotkeys import normalize_shortcut
from device_options import DEFAULT_DEVICE, validate_device

from runtime import CODE_ROOT, DATA_ROOT, device_dir
ROOT = CODE_ROOT
SETTINGS_PATH = device_dir()/'settings.json'
STATUS_PATH = DATA_ROOT/'receiver-status.json'
STOP_PATH = DATA_ROOT/'receiver.stop'
DEFAULTS = {
    'version': 2, 'gain': 4.0,
    'device': deepcopy(DEFAULT_DEVICE),
    'buttons': {
        'mic': {'shortcut': 'RightAlt', 'mode': 'ptt'},
        'plus': {'shortcut': '', 'mode': 'tap'},
        'minus': {'shortcut': '', 'mode': 'tap'},
        'plus_minus': {'shortcut': '', 'mode': 'tap'},
    },
}


def validate_settings(value):
    if not isinstance(value, dict) or value.get('version', 1) not in (1, 2):
        raise ValueError('设置文件格式不正确')
    result = deepcopy(DEFAULTS)
    gain = value.get('gain', result['gain'])
    if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain) or not .25 <= gain <= 16:
        raise ValueError('麦克风增益应在 0.25～16 倍之间')
    result['gain'] = float(gain)
    result['device'] = validate_device(value.get('device', {}))
    buttons = value.get('buttons', {})
    if not isinstance(buttons, dict) or set(buttons)-set(result['buttons']):
        raise ValueError('按键设置不正确')
    for name, default in result['buttons'].items():
        binding = buttons.get(name, default)
        if not isinstance(binding, dict):
            raise ValueError('按键设置不正确')
        shortcut = normalize_shortcut(binding.get('shortcut', default['shortcut']))
        mode = binding.get('mode', default['mode'])
        if name == 'mic' and value.get('version', 1) == 1 and mode == 'hold':
            mode = 'ptt'
        if mode not in ('hold', 'tap', 'ptt') or (name != 'mic' and mode == 'ptt'):
            raise ValueError('无效按键模式')
        if name == 'plus_minus' and mode != 'tap':
            raise ValueError('加减组合键只支持点按；按住 3 秒保留为蓝牙配对')
        result['buttons'][name] = {'shortcut': shortcut, 'mode': mode}
    return result


def load_settings(path=SETTINGS_PATH):
    path = Path(path)
    if not path.exists():
        return deepcopy(DEFAULTS)
    return validate_settings(json.loads(path.read_text(encoding='utf-8')))


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_settings(value, path=SETTINGS_PATH):
    value = validate_settings(value)
    atomic_json(path, value)
    return value


class LiveSettings:
    def __init__(self, path=SETTINGS_PATH):
        self.path = Path(path)
        self.value = load_settings(path)
        self.signature = None
        self.error = None

    def refresh(self, busy):
        if busy:
            return False
        try:
            signature = self.path.stat().st_mtime_ns if self.path.exists() else 0
            if signature == self.signature:
                return False
            value = load_settings(self.path)
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            return False
        self.signature = signature
        self.error = None
        changed = value != self.value
        self.value = value
        return changed
