# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration compatibility and real socket jitter regressions (no keyboard injection)."""
from copy import deepcopy
import hmac
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import receiver as r
from hid_config import config_packet, packet_revision
from settings import DEFAULTS, validate_settings, save_settings
from test_receiver import DummyAudio, DummyAlt


class GestureProtocolTests(unittest.TestCase):
    def test_old_settings_keep_bindings_and_add_disabled_chord(self):
        old = {'version': 2, 'gain': 2, 'buttons': {
            'mic': {'mode': 'ptt', 'shortcut': 'RightAlt'},
            'plus': {'mode': 'hold', 'shortcut': 'Ctrl+C'},
            'minus': {'mode': 'tap', 'shortcut': 'Space'}}}
        upgraded = validate_settings(old)
        for name, binding in old['buttons'].items():
            self.assertEqual(upgraded['buttons'][name], binding)
        self.assertEqual(upgraded['buttons']['plus_minus'], {'mode': 'tap', 'shortcut': ''})
        self.assertEqual(config_packet(upgraded), config_packet(old))

    def test_new_packet_contains_chord_without_changing_three_bindings(self):
        value = deepcopy(DEFAULTS)
        value['buttons']['plus_minus']['shortcut'] = 'Ctrl+Space'
        old, new = config_packet(value), config_packet(value, 2)
        self.assertEqual(len(new), 41)
        self.assertEqual(new[0], 2)
        self.assertEqual(new[1:31], old[1:])
        self.assertEqual(new[31:35], bytes([1, 1, 44, 0]))
        value['buttons']['plus_minus']['mode'] = 'hold'
        with self.assertRaises(ValueError):
            validate_settings(value)

    def test_new_firmware_gets_full_packet_and_acks_exact_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            value = deepcopy(DEFAULTS)
            value['buttons']['plus_minus']['shortcut'] = 'Ctrl+Space'
            path = Path(temp)/'settings.json'
            save_settings(value, path)
            with patch.object(r, 'WindowsKeyboard', return_value=SimpleNamespace(emit=lambda *e: None)):
                controls = r.DesktopControls(path, Path(temp)/'status.json')
            a, b = socket.socketpair();key=b'k'*32;nonce=b'n'*16
            errors=[]
            def device():
                try:
                    with b:
                        r.send_frame(b, 1, nonce);r.receive_frame(b)
                        r.send_frame(b, 3, hmac.digest(key, nonce+b'device', 'sha256'))
                        r.send_frame(b, 11, b'0.7.0')
                        while True:
                            kind, payload = r.receive_frame(b)
                            if kind == 12: break
                        self.assertEqual(payload, config_packet(value, 2))
                        r.send_frame(b, 13, payload)
                        time.sleep(.08)
                except BaseException as exc: errors.append(exc)
            worker=threading.Thread(target=device);worker.start()
            try:
                with a, self.assertRaises((ConnectionError,OSError)):
                    r.session(a,key,DummyAudio(),DummyAlt(),time.monotonic()+5,controls=controls)
            finally: worker.join()
            self.assertFalse(errors)
            self.assertEqual(controls.state['device_revision'], packet_revision(config_packet(value,2)))

    def test_three_second_gap_does_not_release_held_microphone(self):
        a,b=socket.socketpair();key=b'k'*32;nonce=b'n'*16
        audio,alt=DummyAudio(),DummyAlt();errors=[]
        def device():
            try:
                with b:
                    r.send_frame(b,1,nonce);r.receive_frame(b)
                    r.send_frame(b,3,hmac.digest(key,nonce+b'device','sha256'))
                    r.send_frame(b,4);r.send_frame(b,5,bytes(480))
                    time.sleep(3)
                    self.assertEqual(alt.events,[True])
                    r.send_frame(b,5,bytes(480));r.send_frame(b,6)
                    time.sleep(.08)
            except BaseException as exc: errors.append(exc)
        worker=threading.Thread(target=device);worker.start()
        try:
            with a,self.assertRaises((ConnectionError,OSError)):
                r.session(a,key,audio,alt,time.monotonic()+8)
        finally: worker.join()
        self.assertFalse(errors)
        self.assertEqual(audio.received,960)
        self.assertEqual(alt.events,[True,False,False])


if __name__=='__main__': unittest.main()
