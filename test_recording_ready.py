# SPDX-License-Identifier: GPL-3.0-or-later
"""Ready acknowledgement must follow audio/hotkey preparation, never precede it."""
import hmac
from copy import deepcopy
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import receiver as r
from test_receiver import DummyAudio, DummyAlt
from settings import DEFAULTS,save_settings

class RecordingReadyTests(unittest.TestCase):
    def exercise(self, behavior, audio=None, timeout=3, controls=None, version=b'0.11.0'):
        a,b=socket.socketpair();key=b'k'*32;challenge=b'n'*16
        audio=audio or DummyAudio();alt=DummyAlt();errors=[]
        def device():
            try:
                with b:
                    r.send_frame(b,1,challenge);r.receive_frame(b)
                    r.send_frame(b,3,hmac.digest(key,challenge+b'device','sha256'))
                    r.send_frame(b,11,version)
                    behavior(b,audio,alt)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=device);thread.start()
        try:
            with patch.object(r,'RECORD_START_TIMEOUT',timeout),a:
                with self.assertRaises((ConnectionError,OSError,r.ProtocolError,RuntimeError)):
                    r.session(a,key,audio,alt,time.monotonic()+5,controls=controls)
        finally:thread.join()
        self.assertFalse(errors,errors)
        return audio,alt

    def ack(self,sock):
        while True:
            kind,data=r.receive_frame(sock)
            if kind==12:r.send_frame(sock,13,data);continue
            if kind==15:r.send_frame(sock,16,data);continue
            if kind!=9:return kind,data

    def test_v012_and_v013_retain_ready_handshake_and_audio(self):
        def behavior(b,audio,alt):
            nonce=b'v012';r.send_frame(b,28,nonce)
            self.assertEqual(self.ack(b),(29,nonce+b'\0'))
            r.send_frame(b,4,nonce);r.send_frame(b,5,bytes(480));r.send_frame(b,6)
            time.sleep(.08)
        for version in (b'0.12.0',b'0.13.0'):
            with self.subTest(version=version):
                audio,alt=self.exercise(behavior,version=version)
                self.assertEqual(audio.received,480)
                self.assertEqual(alt.events,[True,False,False])

    def test_real_settings_require_ptt_and_publish_preparing_then_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);save_settings(DEFAULTS,root/'settings.json')
            with patch.object(r,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *e:None)):
                controls=r.DesktopControls(root/'settings.json',root/'status.json')
            def behavior(b,audio,alt):
                nonce=b'cfg!';r.send_frame(b,28,nonce)
                self.assertEqual(self.ack(b),(29,nonce+b'\0'))
                self.assertEqual(controls.state['recording_stage'],'preparing')
                r.send_frame(b,4,nonce);time.sleep(.05)
                self.assertEqual(controls.state['recording_stage'],'recording')
                r.send_frame(b,6);time.sleep(.05)
            self.exercise(behavior,controls=controls)
            self.assertEqual(controls.state['recording_stage'],'idle')

    def test_shortcut_only_mode_rejects_ready_without_pressing_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);value=deepcopy(DEFAULTS);value['buttons']['mic']['mode']='hold'
            save_settings(value,root/'settings.json')
            with patch.object(r,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *e:None)):
                controls=r.DesktopControls(root/'settings.json',root/'status.json')
            def behavior(b,audio,alt):
                nonce=b'nope';r.send_frame(b,28,nonce)
                self.assertEqual(self.ack(b),(29,nonce+b'\1'))
                self.assertNotIn(True,alt.events)
            self.exercise(behavior,controls=controls)

    def test_ack_after_audio_and_hotkey_then_prompt_commit_allows_pcm(self):
        class Audio(DummyAudio):
            started=False
            def start(self):self.started=True
        def behavior(b,audio,alt):
            nonce=b'1234';r.send_frame(b,28,nonce)
            self.assertEqual(self.ack(b),(29,nonce+b'\0'))
            self.assertTrue(audio.started);self.assertEqual(alt.events,[True])
            self.assertEqual(audio.received,0)
            r.send_frame(b,4,nonce);r.send_frame(b,5,bytes(480));r.send_frame(b,6)
            time.sleep(.05)
        audio,alt=self.exercise(behavior,Audio())
        self.assertEqual(audio.received,480);self.assertEqual(alt.events,[True,False,False])

    def test_cancel_before_prompt_commit_releases_shortcut_without_pcm(self):
        def behavior(b,audio,alt):
            nonce=b'abcd';r.send_frame(b,28,nonce);self.ack(b)
            r.send_frame(b,30,nonce);time.sleep(.05)
        audio,alt=self.exercise(behavior)
        self.assertEqual(audio.received,0);self.assertEqual(alt.events,[True,False,False])

    def test_audio_start_failure_does_not_acknowledge_ready(self):
        class Broken(DummyAudio):
            def start(self):raise RuntimeError('audio unavailable')
        def behavior(b,audio,alt):
            r.send_frame(b,28,b'fail')
            self.assertEqual(self.ack(b),(10,b''))
            self.assertNotIn(True,alt.events)
        self.exercise(behavior,Broken())

    def test_disconnect_during_preparation_releases_owned_key(self):
        def behavior(b,audio,alt):
            r.send_frame(b,28,b'drop');self.ack(b)
        audio,alt=self.exercise(behavior)
        self.assertEqual(alt.events,[True,False]);self.assertTrue(audio.cleared)

    def test_no_device_start_confirmation_times_out_and_releases_key(self):
        def behavior(b,audio,alt):
            r.send_frame(b,28,b'wait');self.ack(b);time.sleep(.15)
            r.send_frame(b,7)
            self.assertEqual(self.ack(b),(10,b''))
        audio,alt=self.exercise(behavior,timeout=.1)
        self.assertEqual(alt.events,[True,False]);self.assertEqual(audio.received,0)

    def test_audio_before_prompt_commit_is_rejected_and_key_released(self):
        def behavior(b,audio,alt):
            r.send_frame(b,28,b'bad!');self.ack(b);r.send_frame(b,5,bytes(480))
            self.assertEqual(self.ack(b),(10,b''))
        audio,alt=self.exercise(behavior)
        self.assertEqual(audio.received,0);self.assertEqual(alt.events,[True,False])

    def test_cable_start_checks_callback_error_and_stopped_endpoint(self):
        audio=object.__new__(r.CableAudio);audio.error=RuntimeError('callback failure')
        with self.assertRaisesRegex(RuntimeError,'callback failure'):audio.start()
        audio.error=None;audio.stream=SimpleNamespace(active=False)
        with self.assertRaisesRegex(RuntimeError,'not active'):audio.start()

if __name__=='__main__':unittest.main()
