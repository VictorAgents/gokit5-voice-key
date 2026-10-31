# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch,MagicMock
import zipfile
import cable_setup as c


class CableSetupTests(unittest.TestCase):
    def sounddevice(self,playback=True,capture=True):
        sd=MagicMock()
        sd.query_hostapis.return_value=[{'name':'Windows WASAPI'}]
        sd.query_devices.return_value=[]
        if playback:sd.query_devices.return_value.append({'name':'CABLE Input (VB-Audio Virtual Cable)','hostapi':0,'max_output_channels':16,'max_input_channels':0})
        if capture:sd.query_devices.return_value.append({'name':'CABLE Output (VB-Audio Virtual Cable)','hostapi':0,'max_input_channels':16,'max_output_channels':0})
        return sd

    def test_ready_requires_both_endpoints_and_supported_format(self):
        sd=self.sounddevice()
        with patch.object(c,'driver_registered',return_value=True):
            self.assertEqual(c.probe_cable(sd)['state'],'ready')
            sd.check_input_settings.assert_called_once();sd.check_output_settings.assert_called_once()
            sd.check_output_settings.side_effect=RuntimeError('format unavailable')
            self.assertEqual(c.probe_cable(sd)['state'],'error')

    def test_missing_capture_or_disabled_driver_is_not_clean_install(self):
        with patch.object(c,'driver_registered',return_value=False):
            self.assertEqual(c.probe_cable(self.sounddevice(True,False))['state'],'not_ready')
            self.assertEqual(c.probe_cable(self.sounddevice(False,False))['state'],'missing')
        with patch.object(c,'driver_registered',return_value=True):
            self.assertEqual(c.probe_cable(self.sounddevice(False,False))['state'],'not_ready')

    def test_cable_a_and_other_host_apis_do_not_pass(self):
        sd=self.sounddevice();sd.query_devices.return_value[0]['name']='CABLE-A Input (VB-Audio Cable A)'
        with patch.object(c,'driver_registered',return_value=False):
            self.assertNotEqual(c.probe_cable(sd)['state'],'ready')
            sd.query_hostapis.return_value=[{'name':'MME'}]
            self.assertEqual(c.probe_cable(sd)['state'],'missing')

    def test_probe_exception_does_not_recommend_install(self):
        sd=self.sounddevice();sd.query_hostapis.side_effect=RuntimeError('service unavailable')
        self.assertEqual(c.probe_cable(sd)['state'],'error')

    def test_installed_driver_never_downloads_or_elevates(self):
        with patch.object(c,'fresh_probe',return_value={'state':'ready'}),patch.object(c,'download_package') as download,patch.object(c,'run_vendor_installer') as run:
            self.assertTrue(c.prepare_install('unused')['already_installed']);download.assert_not_called();run.assert_not_called()

    def test_partial_or_failed_detection_never_reinstalls(self):
        for state in ('not_ready','error'):
            with patch.object(c,'fresh_probe',return_value={'state':state}),patch.object(c,'download_package') as download:
                with self.assertRaises(RuntimeError):c.prepare_install('unused')
                download.assert_not_called()

    def test_zero_installer_exit_does_not_prove_ready(self):
        with patch.object(c,'fresh_probe',side_effect=[{'state':'missing'},{'state':'missing'},{'state':'not_ready'}]), \
             patch.object(c,'download_package'),patch.object(c,'extract_package',return_value=Path('vendor')), \
             patch.object(c,'verify_signature'),patch.object(c,'run_vendor_installer',return_value=0):
            result=c.prepare_install('unused')
        self.assertEqual(result['state'],'not_ready');self.assertTrue(result['reboot_recommended'])

    def test_concurrent_install_completion_skips_elevation(self):
        with patch.object(c,'fresh_probe',side_effect=[{'state':'missing'},{'state':'ready'}]), \
             patch.object(c,'download_package'),patch.object(c,'extract_package',return_value=Path('vendor')), \
             patch.object(c,'verify_signature'),patch.object(c,'run_vendor_installer') as run:
            self.assertTrue(c.prepare_install('unused')['already_installed'])
            run.assert_not_called()

    def test_bad_signature_cannot_elevate(self):
        with patch.object(c,'fresh_probe',return_value={'state':'missing'}),patch.object(c,'download_package'), \
             patch.object(c,'extract_package',return_value=Path('vendor')),patch.object(c,'verify_signature',side_effect=RuntimeError('bad')), \
             patch.object(c,'run_vendor_installer') as run:
            with self.assertRaises(RuntimeError):c.prepare_install('unused')
            run.assert_not_called()

    def test_download_hash_failure_cleans_partial_and_never_replaces_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'VBCABLE_Driver_Pack45.zip';archive.write_bytes(b'old')
            response=io.BytesIO(b'tampered')
            opener=SimpleNamespace(open=lambda *a,**k:response)
            with patch.object(c.urllib.request,'build_opener',return_value=opener):
                with self.assertRaises(RuntimeError):c.download_package(root)
            self.assertEqual(archive.read_bytes(),b'old');self.assertFalse(list(root.glob('*.part')))

    def test_archive_traversal_and_symlinks_are_rejected(self):
        for filename in ('../outside','/absolute','C:/bad','..\\outside'):
            with tempfile.TemporaryDirectory() as temp:
                archive=Path(temp)/'bad.zip'
                with zipfile.ZipFile(archive,'w') as z:z.writestr(filename,b'bad')
                with patch.object(c,'verify_archive'):
                    with self.assertRaises(RuntimeError):c.extract_package(archive,temp)

    def test_untrusted_redirect_is_refused(self):
        redirect=c.VendorRedirects()
        for url in ('http://download.vb-audio.com/file','https://example.com/file'):
            with self.assertRaises(RuntimeError):redirect.redirect_request(None,None,302,'',{},url)

    def test_fresh_probe_uses_a_new_process_after_install(self):
        result=SimpleNamespace(returncode=0,stdout=json.dumps({'state':'ready'}).encode())
        with patch.object(c.subprocess,'run',return_value=result) as run:
            self.assertEqual(c.fresh_probe()['state'],'ready');self.assertIn('--cable-probe',run.call_args.args[0])


if __name__=='__main__':unittest.main()
