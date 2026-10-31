# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from flasher import Device,Flasher,InstallError,FLASH_BYTES,PROFILE,load_bundle,parse_probe,check_profile,digest

MAC='02:00:00:00:00:01'
def output(mac=MAC,encrypted=False):
    return (f'Chip type: ESP32-S3 (QFN56) (revision v0.2)\nFeatures: Wi-Fi, BT 5 (LE), Embedded PSRAM 8MB (AP_3v3)\nMAC: {mac}\nDetected flash size: 16MB\n',
            f'MAC: {mac}\nSecure Boot: Disabled\nFlash Encryption: {"Enabled" if encrypted else "Disabled"}\n')


def bundle(root):
    root.mkdir()
    header=bytearray(64);header[0]=0xE9;header[12]=9
    values=[(0,'bootloader.bin',bytes(header)),(0x8000,'partitions.bin',bytes(3072)),
            (0xE000,'boot_app0.bin',bytes(8192)),(0x10000,'application.bin',bytes(header))]
    files=[]
    for offset,name,data in values:
        (root/name).write_bytes(data)
        files.append({'offset':offset,'name':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    (root/'manifest.json').write_text(json.dumps({'profile':PROFILE,'flash_bytes':FLASH_BYTES,'version':'test','files':files}))


class FakeRunner:
    def __init__(self,mismatch=False,encrypted=False,change_device=False,short=False,partition=None):
        self.calls=[];self.probes=0;self.mismatch=mismatch;self.encrypted=encrypted;self.change=change_device;self.short=short
        self.partition=partition
    def __call__(self,args,log):
        self.calls.append(args)
        if 'flash-id' in args:
            self.probes+=1
            return output('02:00:00:00:00:02' if self.change and self.probes>1 else MAC)[0]
        if 'get-security-info' in args:
            return output('02:00:00:00:00:02' if self.change and self.probes>1 else MAC,self.encrypted)[1]
        if 'read-flash' in args:
            path=Path(args[-1])
            with path.open('wb') as file:
                file.truncate(FLASH_BYTES-1 if self.short else FLASH_BYTES)
                if self.partition is not None:file.seek(0x8000);file.write(self.partition)
                if self.mismatch and path.name=='factory-2.bin':file.seek(10);file.write(b'x')
        return 'Hash of data verified.\n'


class FlashSafetyTests(unittest.TestCase):
    def test_application_update_only_writes_app_after_double_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle(root/'bundle');partition=(root/'bundle/partitions.bin').read_bytes()
            runner=FakeRunner(partition=partition);engine=Flasher(runner,root/'bundle',root/'data')
            engine.install('COM9',True,application_only=True)
            writes=[c for c in runner.calls if 'write-flash' in c]
            self.assertEqual(len(writes),1)
            self.assertEqual(writes[0][-2:],[hex(0x10000),str((root/'bundle/application.bin').resolve())])
            self.assertNotIn('0x0',writes[0]);self.assertNotIn('0x8000',writes[0]);self.assertNotIn('0xe000',writes[0])
            self.assertEqual(len([c for c in runner.calls if 'read-flash' in c]),2)

    def test_application_update_partition_mismatch_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle(root/'bundle');runner=FakeRunner(partition=b'x'*3072)
            engine=Flasher(runner,root/'bundle',root/'data')
            with self.assertRaises(InstallError):engine.install('COM9',True,application_only=True)
            self.assertFalse(any('write-flash' in c for c in runner.calls))
    def test_unknown_or_protected_profile_refused(self):
        device=parse_probe('COM9',*output())
        check_profile(device,True)
        for item,confirmed in [(device,False),(parse_probe('COM9',*output(encrypted=True)),True)]:
            with self.assertRaises(InstallError):check_profile(item,confirmed)
        with self.assertRaises(InstallError):parse_probe('COM9','incomplete','')

    def test_no_connection_without_board_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle(root/'bundle');runner=FakeRunner();engine=Flasher(runner,root/'bundle',root/'data')
            with self.assertRaises(InstallError):engine.install('COM9',False)
            self.assertEqual(runner.calls,[])

    def exercise(self,**fault):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle(root/'bundle');runner=FakeRunner(**fault);engine=Flasher(runner,root/'bundle',root/'data')
            if fault:
                with self.assertRaises(InstallError):engine.install('COM9',True)
                self.assertFalse(any('write-flash' in call for call in runner.calls))
            else:
                device,manifest=engine.install('COM9',True)
                self.assertTrue(json.loads(manifest.read_text())['double_read_verified'])
                writes=[c for c in runner.calls if 'write-flash' in c]
                self.assertEqual(len(writes),1)
                reads=[c for c in runner.calls if 'read-flash' in c]
                self.assertEqual(len(reads),2)
                self.assertTrue(all('--after' in c and c[c.index('--after')+1]=='no-reset' for c in reads))
                self.assertFalse(any(x in writes[0] for x in ('erase-flash','--force')))

    def test_double_read_before_single_flash(self):self.exercise()
    def test_mismatched_backup_never_flashes(self):self.exercise(mismatch=True)
    def test_short_backup_never_flashes(self):self.exercise(short=True)
    def test_changed_device_never_flashes(self):self.exercise(change_device=True)
    def test_protected_device_never_flashes(self):self.exercise(encrypted=True)

    def test_tampered_bundle_and_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle(root/'bundle')
            (root/'bundle/application.bin').write_bytes(b'wrong')
            with self.assertRaises(InstallError):load_bundle(root/'bundle')
            manifest=json.loads((root/'bundle/manifest.json').read_text());manifest['files'][0]['name']='../outside.bin'
            (root/'bundle/manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(InstallError):load_bundle(root/'bundle')

    def test_wrong_device_restore_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle(root/'bundle');runner=FakeRunner();engine=Flasher(runner,root/'bundle',root/'data')
            _,manifest=engine.install('COM9',True)
            runner.change=True
            before=len([c for c in runner.calls if 'write-flash' in c])
            with self.assertRaises(InstallError):engine.restore('COM9',manifest)
            self.assertEqual(len([c for c in runner.calls if 'write-flash' in c]),before)


if __name__=='__main__':unittest.main()
