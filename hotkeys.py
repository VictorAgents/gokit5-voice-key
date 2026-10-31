# SPDX-License-Identifier: GPL-3.0-or-later
"""Shortcut parsing and balanced Windows key ownership for device buttons."""
from collections import Counter
import ctypes

MODIFIERS = ('Ctrl', 'RightCtrl', 'Shift', 'RightShift', 'Alt', 'RightAlt', 'Win')
VKEYS = {
    'Ctrl': 0xA2, 'RightCtrl': 0xA3, 'Shift': 0xA0, 'RightShift': 0xA1,
    'Alt': 0xA4, 'RightAlt': 0xA5, 'Win': 0x5B,
    'Enter': 0x0D, 'Tab': 0x09, 'Space': 0x20, 'Escape': 0x1B,
    'Backspace': 0x08, 'Delete': 0x2E, 'Insert': 0x2D,
    'Home': 0x24, 'End': 0x23, 'PageUp': 0x21, 'PageDown': 0x22,
    'Left': 0x25, 'Up': 0x26, 'Right': 0x27, 'Down': 0x28,
    'VolumeUp': 0xAF, 'VolumeDown': 0xAE, 'VolumeMute': 0xAD,
    'MediaPlayPause': 0xB3, 'MediaNext': 0xB0, 'MediaPrevious': 0xB1,
    'Minus': 0xBD, 'Equal': 0xBB, 'Comma': 0xBC, 'Period': 0xBE,
    'Slash': 0xBF, 'Semicolon': 0xBA, 'Apostrophe': 0xDE,
    'BracketLeft': 0xDB, 'BracketRight': 0xDD, 'Backslash': 0xDC,
}
VKEYS.update({c: ord(c) for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'})
VKEYS.update({f'F{i}': 0x6F+i for i in range(1, 25)})
ALIASES = {key.lower(): key for key in VKEYS}
ALIASES.update({
    'control': 'Ctrl', 'control_l': 'Ctrl', 'control_r': 'RightCtrl',
    'shift_l': 'Shift', 'shift_r': 'RightShift', 'alt_l': 'Alt', 'alt_r': 'RightAlt',
    'leftalt': 'Alt', 'lalt': 'Alt', 'ralt': 'RightAlt', 'altgr': 'RightAlt',
    'super_l': 'Win', 'super_r': 'Win', 'windows': 'Win',
    'return': 'Enter', 'esc': 'Escape', 'prior': 'PageUp', 'next': 'PageDown',
    'minus': 'Minus', 'equal': 'Equal', 'comma': 'Comma', 'period': 'Period',
    'slash': 'Slash', 'semicolon': 'Semicolon', 'apostrophe': 'Apostrophe',
    'bracketleft': 'BracketLeft', 'bracketright': 'BracketRight', 'backslash': 'Backslash',
    '-': 'Minus', '=': 'Equal', ',': 'Comma', '.': 'Period', '/': 'Slash',
    ';': 'Semicolon', "'": 'Apostrophe', '[': 'BracketLeft', ']': 'BracketRight', '\\': 'Backslash',
    'iso_level3_shift': 'RightAlt',
})


def parse_shortcut(text):
    if not isinstance(text, str):
        raise ValueError('快捷键必须是文字')
    if not text.strip():
        return ()
    pieces = text.split('+')
    if any(not part.strip() for part in pieces):
        raise ValueError('请使用 Ctrl+Shift+A 这样的格式；加号键请填 Equal')
    try:
        keys = [ALIASES[part.strip().lower()] for part in pieces]
    except KeyError as exc:
        raise ValueError(f'不支持的按键：{exc.args[0]}') from None
    if len(set(keys)) != len(keys) or len(keys) > 5:
        raise ValueError('快捷键重复或过长')
    base = [key for key in keys if key not in MODIFIERS]
    if len(base) > 1:
        raise ValueError('一个组合键最多包含一个普通按键')
    return tuple(key for key in MODIFIERS if key in keys) + tuple(base)


def normalize_shortcut(text):
    return '+'.join(parse_shortcut(text))


class WindowsKeyboard:
    def __init__(self, enabled=True):
        self.enabled = enabled
        if not enabled:
            return
        from ctypes import wintypes as w

        class Keyboard(ctypes.Structure):
            _fields_ = [('vk', w.WORD), ('scan', w.WORD), ('flags', w.DWORD),
                        ('time', w.DWORD), ('extra', ctypes.c_size_t)]

        class Mouse(ctypes.Structure):
            _fields_ = [('x', w.LONG), ('y', w.LONG), ('data', w.DWORD),
                        ('flags', w.DWORD), ('time', w.DWORD), ('extra', ctypes.c_size_t)]

        class Payload(ctypes.Union):
            _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]

        class Input(ctypes.Structure):
            _fields_ = [('kind', w.DWORD), ('payload', Payload)]

        self.Keyboard, self.Payload, self.Input = Keyboard, Payload, Input
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user32.SendInput.restype = w.UINT

    def emit(self, key, down):
        if not self.enabled:
            return
        flags = 0 if down else 0x0002
        if key == 'RightAlt':  # Preserve the physically verified WeType scan code.
            vk, scan, flags = 0, 0x38, flags | 0x0008 | 0x0001
        else:
            vk, scan = VKEYS[key], 0
            if key in ('RightCtrl', 'Win', 'Delete', 'Insert', 'Home', 'End',
                       'PageUp', 'PageDown', 'Left', 'Right', 'Up', 'Down'):
                flags |= 0x0001
        event = self.Input(1, self.Payload(keyboard=self.Keyboard(vk, scan, flags, 0, 0)))
        if self.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(self.Input)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())


class ShortcutRouter:
    """Snapshot each pressed binding and share overlapping modifiers safely."""
    def __init__(self, emit):
        self.emit = emit
        self.held = {}
        self.owners = Counter()

    def press(self, button, shortcut, mode='hold'):
        if button in self.held:
            return
        keys = parse_shortcut(shortcut)
        if not keys:
            return
        self.held[button] = []
        try:
            for key in keys:
                if not self.owners[key]:
                    self.emit(key, True)
                self.owners[key] += 1
                self.held[button].append(key)
        except BaseException:
            self.release(button)
            raise
        if mode == 'tap':
            self.release(button)

    def release(self, button):
        keys = self.held.get(button, [])
        error = None
        for key in reversed(tuple(keys)):
            try:
                if self.owners[key] == 1:
                    self.emit(key, False)
                self.owners[key] -= 1
                keys.remove(key)
            except OSError as exc:
                error = exc
        if not keys:
            self.held.pop(button, None)
        if error:
            raise error

    def release_all(self):
        error = None
        for button in tuple(self.held):
            try:
                self.release(button)
            except OSError as exc:
                error = exc
        if error:
            raise error
