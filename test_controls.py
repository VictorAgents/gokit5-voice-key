# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
import hmac
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hotkeys import ShortcutRouter, parse_shortcut
import receiver as r
from settings import DEFAULTS, LiveSettings, load_settings, save_settings
from test_receiver import DummyAudio


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.router = ShortcutRouter(lambda key, down: self.events.append((key, down)))

    def test_parser_normalizes_modifier_order_and_rejects_invalid_combos(self):
        self.assertEqual(parse_shortcut('shift+control+a'), ('Ctrl', 'Shift', 'A'))
        self.assertEqual(parse_shortcut('Alt_R'), ('RightAlt',))
        self.assertEqual(parse_shortcut('F24'), ('F24',))
        for text in ('Ctrl++A', 'Ctrl+A+B', 'Ctrl+Ctrl+A', 'MadeUpKey'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_shortcut(text)

    def test_shared_modifier_not_released_until_last_owner(self):
        self.router.press('plus', 'Ctrl+C')
        self.router.press('minus', 'Ctrl+V')
        self.router.release('plus')
        self.assertNotIn(('Ctrl', False), self.events)
        self.router.release('minus')
        self.assertEqual(self.events, [('Ctrl', True), ('C', True), ('V', True),
                                       ('C', False), ('V', False), ('Ctrl', False)])

    def test_tap_balanced_and_hold_duplicate_suppressed(self):
        self.router.press('plus', 'Ctrl+C', 'tap')
        self.assertFalse(self.router.held)
        self.assertEqual(self.events, [('Ctrl', True), ('C', True), ('C', False), ('Ctrl', False)])
        self.events.clear()
        self.router.press('minus', 'F13')
        self.router.press('minus', 'F14')
        self.router.release_all()
        self.assertEqual(self.events, [('F13', True), ('F13', False)])

    def test_partial_press_failure_releases_prior_modifier(self):
        def emit(key, down):
            if key == 'A' and down:
                raise OSError('simulated injection failure')
            self.events.append((key, down))
        router = ShortcutRouter(emit)
        with self.assertRaises(OSError):
            router.press('plus', 'Ctrl+A')
        self.assertEqual(self.events, [('Ctrl', True), ('Ctrl', False)])
        self.assertFalse(router.held)

    def test_release_failure_can_be_retried(self):
        failed = False
        def emit(key, down):
            nonlocal failed
            if key == 'C' and not down and not failed:
                failed = True
                raise OSError('temporary release failure')
            self.events.append((key, down))
        router = ShortcutRouter(emit)
        router.press('plus', 'Ctrl+C')
        with self.assertRaises(OSError):
            router.release_all()
        self.assertTrue(router.held)
        self.assertEqual(self.events[-1], ('Ctrl', False))
        router.release_all()
        self.assertFalse(router.held)
        self.assertEqual(self.events[-1], ('C', False))

    def test_settings_apply_after_release_and_old_mapping_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            value = deepcopy(DEFAULTS)
            value['buttons']['plus'] = {'shortcut': 'Ctrl+A', 'mode': 'hold'}
            save_settings(value, path)
            live = LiveSettings(path)
            self.router.press('plus', live.value['buttons']['plus']['shortcut'])
            value['buttons']['plus']['shortcut'] = 'Ctrl+B'
            save_settings(value, path)
            self.assertFalse(live.refresh(busy=True))
            self.assertEqual(live.value['buttons']['plus']['shortcut'], 'Ctrl+A')
            self.router.release('plus')
            self.assertEqual(self.events[-2:], [('A', False), ('Ctrl', False)])
            self.assertTrue(live.refresh(busy=False))
            self.assertEqual(live.value['buttons']['plus']['shortcut'], 'Ctrl+B')

    def test_invalid_settings_do_not_replace_last_good_or_saved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            save_settings(DEFAULTS, path)
            live = LiveSettings(path)
            original = path.read_bytes()
            bad = deepcopy(DEFAULTS)
            bad['gain'] = float('nan')
            with self.assertRaises(ValueError):
                save_settings(bad, path)
            self.assertEqual(path.read_bytes(), original)
            path.write_text('{broken', encoding='utf-8')
            self.assertFalse(live.refresh(False))
            self.assertEqual(live.value, DEFAULTS)
            self.assertTrue(live.error)

    def test_default_and_custom_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            self.assertEqual(load_settings(path), DEFAULTS)
            value = deepcopy(DEFAULTS)
            value['buttons']['mic']['shortcut'] = 'Shift+Ctrl+Space'
            value['buttons']['minus']['shortcut'] = 'VolumeDown'
            value['gain'] = 2.5
            saved = save_settings(value, path)
            self.assertEqual(load_settings(path), saved)
            self.assertEqual(saved['buttons']['mic']['shortcut'], 'Ctrl+Shift+Space')
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


class DeviceEventTests(unittest.TestCase):
    def exercise(self, invalid=False):
        with tempfile.TemporaryDirectory() as directory:
            events = []
            settings_path = Path(directory)/'settings.json'
            value = deepcopy(DEFAULTS)
            value['buttons']['plus'] = {'shortcut': 'Ctrl+C', 'mode': 'hold'}
            save_settings(value, settings_path)
            status_path = Path(directory)/'status.json'
            with patch.object(r, 'WindowsKeyboard', return_value=SimpleNamespace(emit=lambda k,d: events.append((k,d)))):
                controls = r.DesktopControls(settings_path, status_path)
            alt = r.MappedMicrophone(controls)
            a, b = socket.socketpair()
            key, nonce = b'k'*32, b'n'*16
            device_errors = []
            def device():
                try:
                    with b:
                        r.send_frame(b, 1, nonce)
                        kind, proof = r.receive_frame(b)
                        self.assertEqual((kind, proof), (2, hmac.digest(key, nonce+b'client', 'sha256')))
                        r.send_frame(b, 3, hmac.digest(key, nonce+b'device', 'sha256'))
                        r.send_frame(b, 11, b'0.4.0')
                        r.send_frame(b, 8, bytes([2, 1]))
                        r.send_frame(b, 4)
                        r.send_frame(b, 5, bytes(480))
                        if invalid:
                            r.send_frame(b, 8, bytes([7, 1]))
                        else:
                            r.send_frame(b, 6)
                            r.send_frame(b, 8, bytes([2, 0]))
                        time.sleep(.05)
                except BaseException as exc:
                    device_errors.append(exc)
            worker = threading.Thread(target=device)
            worker.start()
            audio = DummyAudio()
            try:
                with a, self.assertRaises((ConnectionError, OSError, r.ProtocolError)):
                    r.session(a, key, audio, alt, time.monotonic()+3, controls=controls)
            finally:
                worker.join()
            self.assertFalse(device_errors)
            self.assertFalse(controls.router.held)
            self.assertFalse(alt.down)
            self.assertEqual(audio.received, 480)
            self.assertTrue(audio.cleared)
            self.assertEqual(json.loads(status_path.read_text())['firmware'], '0.4.0')
            return events

    def test_authenticated_button_and_mic_events_release_all_keys(self):
        events = self.exercise()
        self.assertEqual(events, [('Ctrl', True), ('C', True), ('RightAlt', True),
                                  ('RightAlt', False), ('C', False), ('Ctrl', False)])

    def test_invalid_button_frame_releases_all_held_keys(self):
        events = self.exercise(invalid=True)
        self.assertEqual(events.count(('RightAlt', False)), 1)
        self.assertEqual(events[-2:], [('C', False), ('Ctrl', False)])


if __name__ == '__main__':
    unittest.main()
