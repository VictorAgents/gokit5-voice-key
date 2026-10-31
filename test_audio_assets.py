# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import numpy as np
import audio_assets as a
import receiver as r
from settings import DEFAULTS,atomic_json,save_settings
from test_receiver import DummyAudio,DummyAlt


def wav(path,seconds=.2):
    t=np.arange(int(44100*seconds))/44100
    samples=(24000*np.sin(2*np.pi*440*t)).astype('<i2')
    stereo=np.column_stack((samples,samples)).tobytes()
    with wave.open(str(path),'wb') as out:
        out.setparams((2,2,44100,0,'NONE','not compressed'));out.writeframes(stereo)


class AudioAssetTests(unittest.TestCase):
    def test_native_wav_conversion_limits_duration_peak_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'我的 音效.wav';wav(path,7)
            before=hashlib.sha256(path.read_bytes()).digest()
            with patch.object(a.shutil,'which',return_value=None):data,meta=a.convert_audio(path)
            self.assertEqual(len(data),a.MAX_BYTES);self.assertTrue(meta['trimmed'])
            samples=np.frombuffer(data,dtype='<i2')
            self.assertLessEqual(np.max(np.abs(samples.astype(np.int32))),7000)
            self.assertEqual((samples[0],samples[-1]),(0,0))
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)

    @unittest.skipUnless(shutil.which('ffmpeg'),'optional FFmpeg not installed')
    def test_real_mp3_decoder_with_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source.wav';dest=Path(temp)/'我的 开机.mp3';wav(source)
            subprocess.run([shutil.which('ffmpeg'),'-nostdin','-v','error','-i',str(source),str(dest)],check=True,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            data,meta=a.convert_audio(dest)
            self.assertTrue(data);self.assertLess(len(data),a.MAX_BYTES)
            self.assertEqual(meta['name'],dest.name);self.assertFalse(meta['trimmed'])

    def asset(self,root,data=b'\1\0'*601):
        digest=hashlib.sha256(data).hexdigest();folder=root/'audio'
        a.store_asset(folder,data,{'sha256':digest})
        return {'request_id':77,'event':'startup','sha256':digest,'created':time.time()},folder,data

    def test_upload_requires_every_offset_and_commit_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            request,folder,data=self.asset(Path(temp));upload=a.AssetUpload(request,folder)
            kind,begin=upload.begin();self.assertEqual((kind,len(begin)),(20,42))
            offset=0;packet=upload.ack(struct.pack('!IBI',77,0,0));collected=bytearray()
            while packet[0]==21:
                nonce,actual=struct.unpack('!II',packet[1][:8]);self.assertEqual((nonce,actual),(77,offset))
                chunk=packet[1][8:];collected.extend(chunk);offset+=len(chunk)
                packet=upload.ack(struct.pack('!IBI',77,0,offset))
            self.assertEqual(bytes(collected),data);self.assertEqual(packet,(22,struct.pack('!I',77)))
            self.assertFalse(upload.done)
            self.assertIsNone(upload.ack(struct.pack('!IBI',77,1,len(data))))
            self.assertTrue(upload.done);self.assertFalse(upload.error)

    def test_wrong_nonce_is_ignored_and_wrong_offset_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            request,folder,_=self.asset(Path(temp));upload=a.AssetUpload(request,folder)
            self.assertIsNone(upload.ack(struct.pack('!IBI',78,0,0)));self.assertFalse(upload.done)
            self.assertIsNone(upload.ack(struct.pack('!IBI',77,0,480)));self.assertTrue(upload.error)

    def test_file_tampering_or_bad_hash_is_never_reported_success(self):
        with tempfile.TemporaryDirectory() as temp:
            request,folder,_=self.asset(Path(temp));upload=a.AssetUpload(request,folder)
            upload.ack(struct.pack('!IBI',77,4,0));self.assertTrue(upload.done);self.assertTrue(upload.error)
            (folder/(request['sha256']+'.pcm')).write_bytes(b'changed')
            with self.assertRaises(ValueError):a.AssetUpload(request,folder)
            request['sha256']='../'+'a'*61
            with self.assertRaises(ValueError):a.AssetUpload(request,folder)

    def test_device_inventory_validates_size(self):
        data=b'\1'+(b'\0'+bytes(36))*2
        self.assertFalse(a.decode_assets(data)['startup']['available'])
        with self.assertRaises(ValueError):a.decode_assets(data[:-1])
        bad=bytearray(data);bad[2:6]=(a.MAX_BYTES+2).to_bytes(4,'big')
        with self.assertRaises(ValueError):a.decode_assets(bad)

    def test_six_second_boundary_preserves_shorter_files(self):
        with tempfile.TemporaryDirectory() as temp:
            for seconds in (.5,6,7):
                path=Path(temp)/f'{seconds}.wav';wav(path,seconds)
                with patch.object(a.shutil,'which',return_value=None):data,meta=a.convert_audio(path)
                self.assertEqual(len(data),int(min(seconds,6)*16000)*2)
                self.assertEqual(meta['trimmed'],seconds>6)
                self.assertEqual(meta['seconds'],min(seconds,6))

    def test_full_six_second_upload_is_rejected_by_legacy_firmware(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);request,folder,data=self.asset(root,bytes(192000))
            save_settings(DEFAULTS,root/'settings.json')
            with patch.object(r,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *e:None)):
                controls=r.DesktopControls(root/'settings.json',root/'status.json')
            for firmware in ('0.11.0','0.12.0','0.13.0'):
                controls.state['firmware']=firmware
                atomic_json(root/'audio-upload-request.json',request)
                upload=controls.next_upload()
                if firmware=='0.11.0':
                    self.assertIsNone(upload)
                    self.assertIn('0.12',controls.state['upload_result'])
                else:
                    self.assertEqual(len(upload.data),192000)

    def test_socket_session_upload_and_inventory_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);request,folder,data=self.asset(root)
            save_settings(DEFAULTS,root/'settings.json')
            atomic_json(root/'audio-upload-request.json',request)
            with patch.object(r,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *e:None)):
                controls=r.DesktopControls(root/'settings.json',root/'status.json')
            left,right=socket.socketpair();key=b'k'*32;nonce=b'n'*16;errors=[]
            def device():
                received=bytearray()
                try:
                    with right:
                        r.send_frame(right,1,nonce);r.receive_frame(right)
                        r.send_frame(right,3,hmac.digest(key,nonce+b'device','sha256'))
                        r.send_frame(right,11,b'0.9.0');r.send_frame(right,26,b'\1'+bytes(74))
                        while True:
                            kind,payload=r.receive_frame(right)
                            if kind==9:continue
                            if kind in (12,15):r.send_frame(right,13 if kind==12 else 16,payload)
                            elif kind==20:
                                self.assertEqual(payload[10:],hashlib.sha256(data).digest())
                                r.send_frame(right,23,struct.pack('!IBI',77,0,0))
                            elif kind==21:
                                self.assertEqual(int.from_bytes(payload[4:8],'big'),len(received))
                                received.extend(payload[8:]);r.send_frame(right,23,struct.pack('!IBI',77,0,len(received)))
                            elif kind==22:
                                self.assertEqual(bytes(received),data)
                                r.send_frame(right,23,struct.pack('!IBI',77,1,len(data)))
                                r.send_frame(right,26,b'\1\1'+len(data).to_bytes(4,'big')+hashlib.sha256(data).digest()+bytes(37))
                                time.sleep(.08);break
                            else:raise AssertionError(kind)
                except BaseException as exc:errors.append(exc)
            worker=threading.Thread(target=device);worker.start()
            try:
                with left,self.assertRaises((OSError,ConnectionError)):
                    r.session(left,key,DummyAudio(),DummyAlt(),time.monotonic()+5,controls=controls)
            finally:worker.join()
            self.assertFalse(errors);self.assertTrue(controls.state['upload_done'])
            self.assertEqual(controls.state['upload_progress'],100)
            self.assertEqual(controls.state['custom_assets']['startup']['sha256'],hashlib.sha256(data).hexdigest())


if __name__=='__main__':unittest.main()
