# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
import zipfile
import runtime
import receiver
import setup_wifi
import queue
import json
import hashlib
import time
from types import SimpleNamespace
from settings import DEFAULTS,save_settings,atomic_json
from tools.source_archive import archive


class RuntimeTests(unittest.TestCase):
    def test_wifi_failure_reports_reason_without_credentials(self):
        self.assertIn('认证失败',setup_wifi.connection_failure(202))
        self.assertIn('未找到',setup_wifi.connection_failure(201))
        self.assertIn('安全模式',setup_wifi.connection_failure(210))
        self.assertIn('IP',setup_wifi.connection_failure(0))

    def test_wifi_timeout_reuses_pending_key_for_this_device_only(self):
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'receiver.json';key='ab'*32
            pending=config.with_name('provision-pending.json')
            pending.write_text(json.dumps({'device_id':'020000000001','key':key}))
            with patch.object(setup_wifi,'EXPECTED_DEVICE','020000000001'):
                self.assertEqual(setup_wifi.provisioning_key(config)[0],key)
            with patch.object(setup_wifi,'EXPECTED_DEVICE','020000000002'):
                with self.assertRaises(RuntimeError):setup_wifi.provisioning_key(config)

    def test_current_wifi_status_is_recognized(self):
        import re
        status='WIRELESS v0.13 build=0.13.0 configured=1 wifi=3 ip=192.0.2.4 auth=0'
        self.assertIsNotNone(re.search(r'WIRELESS v0\.(?:[3-9]|1[0-3])\b .*wifi=3 ip=(\d+\.\d+\.\d+\.\d+)',status))
    def test_audio_files_use_device_folder_and_requests_are_identity_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);identity='020000000001';settings=root/'devices'/identity/'settings.json';save_settings(DEFAULTS,settings)
            data=bytes(480);digest=hashlib.sha256(data).hexdigest();folder=settings.parent/'audio';folder.mkdir();(folder/(digest+'.pcm')).write_bytes(data)
            with patch.object(receiver,'active_id',return_value=identity),patch.object(receiver,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *args:None)):
                controls=receiver.DesktopControls(settings,root/'status.json')
            request={'device_id':identity,'request_id':7,'event':'startup','created':time.time(),'sha256':digest}
            atomic_json(controls.upload_path,{**request,'device_id':'020000000002'})
            self.assertIsNone(controls.next_upload());self.assertIn('另一台',controls.state['upload_result'])
            atomic_json(controls.upload_path,request)
            upload=controls.next_upload();self.assertEqual(upload.data,data)

    def test_stale_preview_from_other_device_is_never_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);save_settings(DEFAULTS,root/'settings.json')
            with patch.object(receiver,'active_id',return_value='020000000001'),patch.object(receiver,'WindowsKeyboard',return_value=SimpleNamespace(emit=lambda *args:None)):
                controls=receiver.DesktopControls(root/'settings.json',root/'status.json')
            atomic_json(controls.preview_path,{'device_id':'020000000002','request_id':8,'created':time.time(),'event':'startup','tone':'soft','volume':25})
            self.assertIsNone(controls.next_preview())
    def test_source_background_entry_uses_windowless_python_but_worker_keeps_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            python=Path(tmp)/'python.exe';python.write_bytes(b'python')
            pythonw=Path(tmp)/'pythonw.exe';pythonw.write_bytes(b'pythonw')
            with patch.object(runtime.sys,'frozen',False,create=True),patch.object(runtime.sys,'executable',str(python)),patch.object(runtime.os,'name','nt'):
                self.assertEqual(runtime.launch_command('--bootstrap')[0],str(pythonw))
                self.assertEqual(runtime.launch_command('--esptool')[0],str(python))

    def test_old_wifi_window_never_sends_credentials_to_newly_selected_device(self):
        updates=queue.Queue()
        with patch.object(setup_wifi,'EXPECTED_DEVICE','020000000001'),patch.object(setup_wifi,'active_id',return_value='020000000002'),patch.object(setup_wifi.serial,'Serial') as serial:
            setup_wifi.provision('test-network','test-password',updates)
            serial.assert_not_called()
            self.assertEqual(updates.get()[0],'error')

    def test_second_device_install_does_not_overwrite_identical_running_executable(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'DATA_ROOT',Path(tmp)):
            source=Path(tmp)/'download.exe';source.write_bytes(b'test-program')
            target=Path(tmp)/'app/Gokit5Community.exe';target.parent.mkdir();target.write_bytes(source.read_bytes())
            with patch.object(runtime.sys,'frozen',True,create=True),patch.object(runtime.sys,'executable',str(source)),patch.object(runtime.shutil,'copyfile') as copy:
                self.assertEqual(runtime.install_runtime(),target)
                copy.assert_not_called()

    def test_devices_are_isolated_and_invalid_ids_do_not_escape(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'DATA_ROOT',Path(tmp)):
            runtime.set_active('02:00:00:00:00:01')
            first=runtime.device_dir()
            runtime.set_active('02:00:00:00:00:02')
            self.assertNotEqual(first,runtime.device_dir())
            self.assertEqual(runtime.device_dir().name,'020000000002')
            with self.assertRaises(ValueError):runtime.device_dir('../../other')

    def test_source_export_never_includes_private_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir()
            (root/'README.md').write_text('public')
            for name in ('backups','local-config','logs','private-data'):
                path=root/name;path.mkdir();(path/'private.txt').write_text('never-export')
            target=Path(tmp)/'source.zip';archive(root,target)
            with zipfile.ZipFile(target) as zip:
                self.assertEqual(zip.namelist(),['Gokit5Community/README.md'])
                self.assertNotIn(b'never-export',zip.read(zip.namelist()[0]))

    def test_missing_audio_does_not_block_ble_settings_and_recovers(self):
        output=MagicMock();output.error=None
        with patch.object(receiver,'CableAudio',side_effect=[RuntimeError('not ready'),output]) as factory, \
             patch.object(receiver.time,'monotonic',return_value=0):
            audio=receiver.ConfigurationOnlyAudio();audio.refresh()
            self.assertFalse(audio.available)
            with self.assertRaises(RuntimeError):audio.start()
            with patch.object(receiver.time,'monotonic',return_value=6):audio.refresh()
            self.assertTrue(audio.available);audio.start();output.start.assert_called_once()
            self.assertEqual(factory.call_count,2)


if __name__=='__main__':unittest.main()
