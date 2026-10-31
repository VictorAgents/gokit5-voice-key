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

from device_options import DEFAULT_DEVICE, options_packet, preview_packet, validate_device
from hid_config import config_packet, packet_revision
from settings import DEFAULTS, save_settings, validate_settings, atomic_json
from test_receiver import DummyAudio, DummyAlt
import receiver as r


class DeviceOptionsTests(unittest.TestCase):
    def test_upgrade_preserves_old_bindings_without_changing_saved_file(self):
        old={'version':2,'gain':4,'buttons':{'minus':{'mode':'tap','shortcut':'Space'}}}
        result=validate_settings(old)
        self.assertEqual(result['device'],DEFAULT_DEVICE)
        self.assertEqual(result['buttons']['minus'],old['buttons']['minus'])
        self.assertNotIn('device',old)
        self.assertEqual(options_packet(result['device']),bytes([1,1,1,25,1,32,5,32,85,255]))

    def test_invalid_options_are_rejected(self):
        for group,field,bad in [('sounds','volume',True),('sounds','volume',101),('sounds','volume',1.5),
                                ('sounds','startup',[]),('sounds','shutdown','file.wav'),
                                ('lighting','template',{}),('lighting','brightness',-1),
                                ('lighting','idle_seconds',61),('lighting','color','#XYZ123')]:
            value=deepcopy(DEFAULT_DEVICE);value[group][field]=bad
            with self.subTest(field=field,bad=bad),self.assertRaises(ValueError): validate_device(value)

    def test_all_templates_encode_and_sound_toggles_are_independent(self):
        for i,name in enumerate(('off','solid','breathe','rainbow','keys')):
            value=deepcopy(DEFAULT_DEVICE);value['lighting'].update(template=name,color='#aA12ef',idle_seconds=0)
            value['sounds'].update(startup='off',shutdown='double',volume=0)
            packet=options_packet(value)
            self.assertEqual(packet,bytes([1,0,2,0,i,32,0,170,18,239]))

    def controls(self,root):
        path=root/'settings.json';save_settings(DEFAULTS,path)
        with patch.object(r,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *e:None)):
            return r.DesktopControls(path,root/'status.json')

    def test_preview_is_temporary_consumed_once_and_expires(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);controls=self.controls(root);before=(root/'settings.json').read_bytes()
            request={'request_id':123,'created':time.time(),'event':'shutdown','tone':'double','volume':20}
            atomic_json(controls.preview_path,request)
            self.assertEqual(controls.next_preview(),bytes([1,1,2,20,0,0,0,123]))
            self.assertIsNone(controls.next_preview())
            self.assertFalse(controls.preview_path.exists())
            # Restarting receiver must not replay a recently consumed request.
            self.assertIsNone(self.controls(root).next_preview())
            request.update(request_id=124,created=time.time()-16)
            atomic_json(controls.preview_path,request)
            self.assertIsNone(controls.next_preview())
            self.assertEqual((root/'settings.json').read_bytes(),before)

    def test_real_socket_sync_and_preview_ack_do_not_inject_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);controls=self.controls(root)
            request={'request_id':789,'created':time.time(),'event':'startup','tone':'soft','volume':25}
            atomic_json(controls.preview_path,request)
            a,b=socket.socketpair();key=b'k'*32;nonce=b'n'*16;errors=[]
            def device():
                try:
                    with b:
                        r.send_frame(b,1,nonce);r.receive_frame(b)
                        r.send_frame(b,3,hmac.digest(key,nonce+b'device','sha256'))
                        r.send_frame(b,11,b'0.8.0')
                        seen=set()
                        while len(seen)<3:
                            kind,payload=r.receive_frame(b)
                            if kind==9:continue
                            if kind==12:
                                self.assertEqual(payload,config_packet(DEFAULTS,2));r.send_frame(b,13,payload)
                            elif kind==15:
                                self.assertEqual(payload,options_packet(DEFAULT_DEVICE));r.send_frame(b,16,payload)
                            elif kind==17:
                                self.assertEqual(payload,preview_packet(request));r.send_frame(b,18,payload[-4:]+b'\0')
                            else:raise AssertionError(kind)
                            seen.add(kind)
                        time.sleep(.1)
                except BaseException as exc:errors.append(exc)
            thread=threading.Thread(target=device);thread.start()
            try:
                with a,self.assertRaises((OSError,ConnectionError)):
                    r.session(a,key,DummyAudio(),DummyAlt(),time.monotonic()+5,controls=controls)
            finally:thread.join()
            self.assertFalse(errors)
            self.assertEqual(controls.state['options_revision'],packet_revision(options_packet(DEFAULT_DEVICE)))
            self.assertEqual(controls.state['preview_request_id'],789)
            self.assertEqual(controls.state['preview_result'],'设备已播放')


if __name__=='__main__': unittest.main()
