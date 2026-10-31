# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated device sound/light settings and authenticated-wire encoders."""
from copy import deepcopy
import re

SOUNDS = {'off': 0, 'soft': 1, 'double': 2, 'custom': 3}
LIGHTS = {'off': 0, 'solid': 1, 'breathe': 2, 'rainbow': 3, 'keys': 4}
DEFAULT_DEVICE = {
    'sounds': {'startup': 'soft', 'shutdown': 'soft', 'volume': 25},
    'lighting': {'template': 'solid', 'color': '#2055FF', 'brightness': 32, 'idle_seconds': 5},
}


def integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label}应为 {low}～{high} 的整数')
    return value


def validate_device(value):
    result = deepcopy(DEFAULT_DEVICE)
    if not isinstance(value, dict) or set(value)-set(result):
        raise ValueError('设备提示音/灯光设置不正确')
    for name, default in result.items():
        group = value.get(name, {})
        if not isinstance(group, dict) or set(group)-set(default):
            raise ValueError('设备提示音/灯光设置不正确')
        default.update(group)
    sounds, lights = result['sounds'], result['lighting']
    if any(not isinstance(sounds[k], str) or sounds[k] not in SOUNDS for k in ('startup','shutdown')):
        raise ValueError('无效提示音')
    integer(sounds['volume'], 0, 100, '提示音音量')
    if not isinstance(lights['template'], str) or lights['template'] not in LIGHTS:
        raise ValueError('无效灯光模板')
    integer(lights['brightness'], 0, 100, '灯光亮度')
    integer(lights['idle_seconds'], 0, 60, '空闲熄灯时间')
    if not isinstance(lights['color'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', lights['color']):
        raise ValueError('颜色应为 #RRGGBB')
    lights['color'] = lights['color'].upper()
    return result


def options_packet(value):
    value = validate_device(value)
    s, l = value['sounds'], value['lighting']
    return bytes([1, SOUNDS[s['startup']], SOUNDS[s['shutdown']], s['volume'],
                  LIGHTS[l['template']], l['brightness'], l['idle_seconds']]) + bytes.fromhex(l['color'][1:])


def preview_packet(request):
    tone = request['tone']
    if not isinstance(tone, str) or tone not in SOUNDS:
        raise ValueError('无效提示音')
    volume = integer(request['volume'], 0, 100, '提示音音量')
    nonce = integer(request['request_id'], 0, 0xFFFFFFFF, '试听编号')
    event = request.get('event', 'startup')
    if event not in ('startup','shutdown'):
        raise ValueError('无效试听类型')
    return bytes([1, int(event=='shutdown'), SOUNDS[tone], volume]) + nonce.to_bytes(4, 'big')
