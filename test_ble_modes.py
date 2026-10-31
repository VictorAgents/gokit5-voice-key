# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy
import json
import hmac
import socket
import threading
import time
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hid_config import config_packet, packet_revision
from settings import DEFAULTS, validate_settings, save_settings
from test_receiver import DummyAudio
import receiver as r
import startup
import run_receiver


class BleModesTests(unittest.TestCase):
    def test_login_launcher_retries_initial_audio_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            clock=[0]
            def sleep(seconds):clock[0]+=seconds
            with patch.object(run_receiver,'ROOT',root), patch.object(run_receiver,'STOP_PATH',root/'receiver.stop'), \
                 patch.object(run_receiver,'SingleInstance'), patch.object(run_receiver,'receiver_running',return_value=False), \
                 patch.object(run_receiver.subprocess,'run',side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(returncode=0)]) as child, \
                 patch.object(run_receiver.time,'sleep',side_effect=sleep), patch.object(run_receiver.time,'monotonic',side_effect=lambda:clock[0]):
                run_receiver.main()
                self.assertEqual(child.call_count,2)

    def test_login_launcher_keeps_retrying_after_two_minutes_and_stop_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);stop=root/'receiver.stop';clock=[0];attempts=[]
            def child(*args,**kwargs):
                attempts.append(clock[0]);clock[0]+=150
                if len(attempts)==2:stop.touch()
                return SimpleNamespace(returncode=1)
            def sleep(seconds):clock[0]+=seconds
            with patch.object(run_receiver,'ROOT',root),patch.object(run_receiver,'STOP_PATH',stop), \
                 patch.object(run_receiver,'SingleInstance'),patch.object(run_receiver,'receiver_running',return_value=False), \
                 patch.object(run_receiver.subprocess,'run',side_effect=child), \
                 patch.object(run_receiver.time,'sleep',side_effect=sleep),patch.object(run_receiver.time,'monotonic',side_effect=lambda:clock[0]):
                run_receiver.main()
            self.assertEqual(len(attempts),2)
            self.assertGreater(attempts[1],120)

    def test_login_launcher_does_not_restart_or_duplicate_existing_receiver(self):
        with tempfile.TemporaryDirectory() as temp:
            stop=Path(temp)/'receiver.stop';stop.write_text('stop')
            with patch.object(run_receiver,'STOP_PATH',stop), patch.object(run_receiver,'SingleInstance'), \
                 patch.object(run_receiver,'receiver_running',return_value=True), patch.object(run_receiver.subprocess,'run') as child:
                run_receiver.main()
                child.assert_not_called()
                self.assertTrue(stop.exists())

    def test_device_sync_ack_and_ble_events_keep_pc_audio_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            value=deepcopy(DEFAULTS)
            value['buttons']['mic']['mode']='hold'
            value['buttons']['plus']['shortcut']='Enter'
            path=Path(temp)/'settings.json'
            save_settings(value,path)
            events=[]
            with patch.object(r,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *e: events.append(e))):
                controls=r.DesktopControls(path,Path(temp)/'status.json')
            a,b=socket.socketpair();key=b'k'*32;nonce=b'n'*16
            errors=[]
            def device():
                try:
                    with b:
                        r.send_frame(b,1,nonce)
                        kind,proof=r.receive_frame(b)
                        self.assertEqual(proof,hmac.digest(key,nonce+b'client','sha256'))
                        r.send_frame(b,3,hmac.digest(key,nonce+b'device','sha256'))
                        r.send_frame(b,11,b'0.5.0')
                        for _ in range(5):
                            kind,payload=r.receive_frame(b)
                            if kind==12: break
                        self.assertEqual((kind,payload),(12,config_packet(value)))
                        r.send_frame(b,13,payload)
                        r.send_frame(b,14,b'\1')
                        r.send_frame(b,8,b'\1\1')
                        r.send_frame(b,8,b'\1\0')
                        # Legacy/in-flight audio after changing to shortcut-only is dropped.
                        r.send_frame(b,4)
                        r.send_frame(b,5,bytes(480))
                        r.send_frame(b,6)
                        time.sleep(.05)
                except BaseException as exc: errors.append(exc)
            t=threading.Thread(target=device);t.start();audio=DummyAudio()
            try:
                with a,self.assertRaises((ConnectionError,OSError)):
                    r.session(a,key,audio,r.MappedMicrophone(controls),time.monotonic()+3,controls=controls)
            finally: t.join()
            self.assertFalse(errors)
            self.assertEqual(events,[])
            self.assertEqual(audio.received,0)
            status=json.loads((Path(temp)/'status.json').read_text())
            self.assertEqual(status['device_revision'],packet_revision(config_packet(value)))
            self.assertTrue(status['ble_connected'])

    def test_old_hold_microphone_migrates_to_voice_not_bluetooth(self):
        old = deepcopy(DEFAULTS)
        old['version'] = 1
        old['buttons']['mic']['mode'] = 'hold'
        migrated = validate_settings(old)
        self.assertEqual(migrated['version'], 2)
        self.assertEqual(migrated['buttons']['mic']['mode'], 'ptt')

    def test_three_modes_encode_distinct_and_right_alt_modifier(self):
        for mode, code in [('tap', 1), ('hold', 2), ('ptt', 3)]:
            value = deepcopy(DEFAULTS)
            value['buttons']['mic']['mode'] = mode
            packet = config_packet(validate_settings(value))
            self.assertEqual(len(packet), 31)
            self.assertEqual(packet[:3], bytes([1, code, 64]))
            self.assertEqual(len(packet_revision(packet)), 12)

    def test_hid_combo_function_and_consumer_usage(self):
        value = deepcopy(DEFAULTS)
        value['buttons']['plus']['shortcut'] = 'Ctrl+Shift+F24'
        value['buttons']['minus']['shortcut'] = 'VolumeDown'
        packet = config_packet(value)
        self.assertEqual(packet[12], 3)
        self.assertEqual(packet[13], 115)
        self.assertEqual(packet[29:31], bytes([0xEA, 0]))

    def test_bluetooth_events_do_not_inject_duplicate_windows_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            value = deepcopy(DEFAULTS)
            value['buttons']['plus']['shortcut'] = 'Enter'
            value['buttons']['mic']['mode'] = 'hold'
            path = Path(temp)/'settings.json'
            save_settings(value, path)
            events = []
            with patch.object(r, 'WindowsKeyboard', return_value=SimpleNamespace(emit=lambda *args: events.append(args))):
                controls = r.DesktopControls(path, Path(temp)/'status.json')
            controls.device_keyboard = True
            for name in ('mic', 'plus'):
                controls.button(name, True, device_event=True)
                controls.button(name, False, device_event=True)
            self.assertEqual(events, [])

    def test_voice_mode_retains_pc_key_for_audio_tail(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'settings.json'
            save_settings(DEFAULTS, path)
            events = []
            with patch.object(r, 'WindowsKeyboard', return_value=SimpleNamespace(emit=lambda *args: events.append(args))):
                controls = r.DesktopControls(path, Path(temp)/'status.json')
            controls.device_keyboard = True
            controls.button('mic', True)
            controls.button('mic', False)
            self.assertEqual(events, [('RightAlt', True), ('RightAlt', False)])

    def test_startup_only_touches_own_value_and_preserves_foreign(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(startup, 'ROOT', Path(temp)):
            with patch.object(startup, 'current_value', return_value='some other existing command'), \
                 patch.object(startup.winreg, 'CreateKey') as create:
                with self.assertRaises(OSError):
                    startup.set_enabled(True)
                create.assert_not_called()
            with patch.object(startup, 'current_value', return_value=None), \
                 patch.object(startup.winreg, 'CreateKey'), \
                 patch.object(startup.winreg, 'SetValueEx') as write:
                startup.set_enabled(True)
                self.assertEqual(write.call_args.args[1], startup.VALUE_NAME)
                self.assertEqual(write.call_args.args[4], startup.command())
                self.assertIsNone(json.loads((Path(temp)/'local-config/startup-before.json').read_text())['before'])


if __name__ == '__main__':
    unittest.main()
