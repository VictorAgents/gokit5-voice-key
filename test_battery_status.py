# SPDX-License-Identifier: GPL-3.0-or-later
import hmac
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from battery_status import PACKET, decode_battery, battery_text
import receiver as r
from settings import DEFAULTS, save_settings
from test_receiver import DummyAudio, DummyAlt

class BatteryTests(unittest.TestCase):
    def test_report_ranges_and_invalid_sensor(self):
        data=PACKET.pack(1,3,49,2077,1800,3854,123456,1)
        value=decode_battery(data)
        self.assertEqual((value['percent'],value['millivolts'],value['uptime_ms']),(49,3854,123456))
        self.assertTrue(value['estimated'])
        self.assertFalse(decode_battery(PACKET.pack(1,2,255,0,0,0,1,1))['available'])
        for packet in (data[:-1],PACKET.pack(1,3,101,2077,1800,3854,1,1),
                       PACKET.pack(1,3,49,2077,1800,5000,1,1),
                       PACKET.pack(1,3,10,1788,1800,3854,1,1),
                       PACKET.pack(2,3,49,2077,1800,3854,1,1)):
            with self.assertRaises(ValueError):decode_battery(packet)

    def test_stale_or_disconnected_never_shows_old_percentage(self):
        value=decode_battery(PACKET.pack(1,7,10,1844,1600,3426,123,1))
        self.assertIn('电量偏低',battery_text(value,True,True))
        self.assertNotIn('10%',battery_text(value,True,False))
        self.assertNotIn('10%',battery_text(value,False,True))
        self.assertEqual(battery_text({},True,True),'电量：暂不可读')

    def test_charge_state_is_independent_of_unavailable_percentage(self):
        for flags,expected in ((26,'充电中'),(42,'充电已完成')):
            value=decode_battery(PACKET.pack(1,flags,255,343,248,531,123,1))
            self.assertIn(expected,battery_text(value,True,True))
            self.assertNotIn('%',battery_text(value,True,True))
        for flags in (58,18):
            with self.assertRaises(ValueError):decode_battery(PACKET.pack(1,flags,255,343,248,531,1,1))

    def test_new_firmware_sync_and_battery_during_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);save_settings(DEFAULTS,root/'settings.json')
            with patch.object(r,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *e:None)):
                controls=r.DesktopControls(root/'settings.json',root/'status.json')
            a,b=socket.socketpair();key=b'k'*32;nonce=b'n'*16;errors=[]
            audio,alt=DummyAudio(),DummyAlt()
            report=PACKET.pack(1,3,49,2077,1800,3854,123456,1)
            def device():
                try:
                    with b:
                        r.send_frame(b,1,nonce);r.receive_frame(b)
                        r.send_frame(b,3,hmac.digest(key,nonce+b'device','sha256'))
                        r.send_frame(b,11,b'0.10.0')
                        seen=set()
                        while len(seen)<2:
                            kind,payload=r.receive_frame(b)
                            if kind==12:
                                self.assertEqual(len(payload),41);r.send_frame(b,13,payload);seen.add(kind)
                            if kind==15:r.send_frame(b,16,payload);seen.add(kind)
                        r.send_frame(b,4);r.send_frame(b,5,bytes(480))
                        r.send_frame(b,27,report)
                        r.send_frame(b,5,bytes(480));r.send_frame(b,6)
                        time.sleep(.1)
                except BaseException as exc:errors.append(exc)
            thread=threading.Thread(target=device);thread.start()
            try:
                with a,self.assertRaises((ConnectionError,OSError)):
                    r.session(a,key,audio,alt,time.monotonic()+5,controls=controls)
            finally:thread.join()
            self.assertFalse(errors)
            self.assertEqual(controls.state['battery']['percent'],49)
            self.assertGreater(controls.state['battery_updated'],0)
            self.assertEqual(audio.received,960)
            self.assertEqual(alt.events,[True,False,False])

if __name__=='__main__':unittest.main()
