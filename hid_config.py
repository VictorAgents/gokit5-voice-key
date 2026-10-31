# SPDX-License-Identifier: GPL-3.0-or-later
"""Encode legacy 31-byte or four-binding 41-byte device configurations."""
import hashlib
from hotkeys import parse_shortcut

USAGES = {c: i+4 for i, c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}
USAGES.update({c: i+30 for i, c in enumerate('1234567890')})
USAGES.update({f'F{i}': 57+i for i in range(1, 13)})
USAGES.update({f'F{i}': 91+i for i in range(13, 25)})
USAGES.update(dict(Enter=40, Escape=41, Backspace=42, Tab=43, Space=44,
                  Minus=45, Equal=46, BracketLeft=47, BracketRight=48,
                  Backslash=49, Semicolon=51, Apostrophe=52, Comma=54,
                  Period=55, Slash=56, Insert=73, Home=74, PageUp=75,
                  Delete=76, End=77, PageDown=78, Right=79, Left=80, Down=81, Up=82))
MODS = dict(Ctrl=1, Shift=2, Alt=4, Win=8, RightCtrl=16, RightShift=32, RightAlt=64)
CONSUMERS = dict(VolumeUp=0xE9, VolumeDown=0xEA, VolumeMute=0xE2,
                 MediaPlayPause=0xCD, MediaNext=0xB5, MediaPrevious=0xB6)


def config_packet(settings, schema=1):
    if schema not in (1, 2):
        raise ValueError('Unknown device configuration schema')
    result = bytearray([schema])
    names = ('mic', 'plus', 'minus') + (('plus_minus',) if schema == 2 else ())
    for name in names:
        binding = settings['buttons'].get(name, {'mode': 'tap', 'shortcut': ''})
        mode = {'tap': 1, 'hold': 2, 'ptt': 3}[binding['mode']]
        mods, usages, consumer = 0, [], 0
        for key in parse_shortcut(binding['shortcut']):
            if key in MODS:
                mods |= MODS[key]
            elif key in CONSUMERS:
                consumer = CONSUMERS[key]
            else:
                usages.append(USAGES[key])
        result.extend(bytes([mode, mods])+bytes(usages).ljust(6, b'\0')+consumer.to_bytes(2, 'little'))
    return bytes(result)


def packet_revision(packet):
    return hashlib.sha256(packet).hexdigest()[:12]
