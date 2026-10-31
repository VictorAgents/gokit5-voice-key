# SPDX-License-Identifier: GPL-3.0-or-later
"""Backup-first flashing engine; all subprocess arguments are structured."""
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from datetime import datetime

from runtime import CODE_ROOT, DATA_ROOT, device_dir, normalize_id, launch_command

FLASH_BYTES = 16*1024*1024
PROFILE = 'gizwits-4key-s3-16m-8m'


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    port: str
    device_id: str
    chip: str
    flash_bytes: int
    features: str
    secure_boot: bool
    encrypted: bool


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def parse_probe(port, flash_output, security_output):
    mac = re.search(r'(?im)^MAC:\s*([0-9a-f:]{17})\s*$', flash_output)
    chip = re.search(r'(?im)^Chip type:\s*(.+)$', flash_output)
    features = re.search(r'(?im)^Features:\s*(.+)$', flash_output)
    size = re.search(r'(?im)^Detected flash size:\s*(\d+)\s*MB\s*$', flash_output)
    secure = re.search(r'(?im)^Secure Boot:\s*(Disabled|Enabled)\s*$', security_output)
    encrypted = re.search(r'(?im)^Flash Encryption:\s*(Disabled|Enabled)\s*$', security_output)
    security_mac = re.search(r'(?im)^MAC:\s*([0-9a-f:]{17})\s*$', security_output)
    if not all((mac, chip, features, size, secure, encrypted, security_mac)):
        raise InstallError('无法完整确认芯片、容量和安全状态，已停止，未刷写')
    identity = normalize_id(mac[1])
    if normalize_id(security_mac[1]) != identity:
        raise InstallError('检查期间设备身份改变，已停止')
    return Device(port, identity, chip[1].strip(), int(size[1])*1024*1024,
                  features[1].strip(), secure[1] != 'Disabled', encrypted[1] != 'Disabled')


def check_profile(device, confirmed_board):
    if not confirmed_board:
        raise InstallError('须确认四键板外观；芯片型号不能证明GPIO兼容')
    if not device.chip.startswith('ESP32-S3 ') or device.flash_bytes != FLASH_BYTES or 'Embedded PSRAM 8MB' not in device.features:
        raise InstallError('当前只支持已确认的ESP32-S3/16MB Flash/8MB内置PSRAM四键板')
    if device.secure_boot or device.encrypted:
        raise InstallError('设备启用了安全启动或Flash加密，不支持一键改写；未绕过保护')


def load_bundle(root=None):
    root = Path(root or CODE_ROOT/'firmware_bundle').resolve()
    try:
        manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        if manifest['profile'] != PROFILE or manifest['flash_bytes'] != FLASH_BYTES:
            raise InstallError('固件目标板型不匹配')
        files = manifest['files']
        if [item['offset'] for item in files] != [0, 0x8000, 0xE000, 0x10000]:
            raise InstallError('固件写入布局不匹配')
        previous_end = 0
        for item in files:
            path = (root/item['name']).resolve()
            if path.parent != root or path.name != item['name'] or not path.is_file():
                raise InstallError('固件文件路径不安全或文件缺失')
            size = path.stat().st_size
            if size != item['bytes'] or digest(path) != item['sha256']:
                raise InstallError('固件校验失败，未刷写')
            if item['offset'] < previous_end or item['offset']+size > FLASH_BYTES:
                raise InstallError('固件写入范围错误')
            previous_end = item['offset']+size
        # Partition image fits in its sector and does not overlap NVS at 0x9000.
        if files[1]['bytes'] > 0x1000 or files[0]['bytes'] > 0x8000 or files[2]['bytes'] != 0x2000:
            raise InstallError('启动布局不安全')
        for item in (files[0],files[3]):
            header=(root/item['name']).read_bytes()[:24]
            if len(header)!=24 or header[0]!=0xE9 or int.from_bytes(header[12:14],'little')!=9:
                raise InstallError('发布包包含非ESP32-S3映像')
        manifest['_root'] = root
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InstallError('固件发布包无效或不完整') from exc


class Runner:
    def __init__(self, notify=lambda text: None):
        self.notify = notify

    def __call__(self, args, log_path):
        env = dict(__import__('os').environ, PYTHONIOENCODING='utf-8', NO_COLOR='1')
        env.pop('ESPRESSIF_IDE_WS',None)
        command = launch_command('--esptool', *args)
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if __import__('os').name == 'nt' else {}
        with Path(log_path).open('a', encoding='utf-8') as log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding='utf-8', errors='replace', env=env, **kwargs)
            lines = []
            for line in process.stdout:
                lines.append(line)
                log.write(line)
                log.flush()
                self.notify(line.strip())
            result = process.wait()
        if result:
            raise InstallError('设备工具失败，已停止；请保留本地备份和日志后重试')
        return ''.join(lines)


class Flasher:
    def __init__(self, runner=None, bundle_root=None, data_root=None):
        self.run = runner or Runner()
        self.bundle = load_bundle(bundle_root)
        self.data_root = Path(data_root or DATA_ROOT)

    def probe(self, port, folder, before='default-reset'):
        if not re.fullmatch(r'COM\d+', port, re.I):
            raise InstallError('无效串口')
        folder.mkdir(parents=True, exist_ok=True)
        base = ['--port', port, '--before', before, '--after', 'no-reset']
        first = self.run([*base, 'flash-id'], folder/'device-tool.log')
        second = self.run(['--chip', 'esp32s3', '--port', port, '--before', 'no-reset', '--after', 'no-reset', 'get-security-info'], folder/'device-tool.log')
        return parse_probe(port, first, second)

    @staticmethod
    def same_device(before, after):
        if before != after:
            raise InstallError('设备身份、容量或安全状态发生变化，已停止')

    def install(self, port, confirmed_board, *, application_only=False):
        if not confirmed_board:
            raise InstallError('请先确认四键板型，尚未连接设备')
        folder = self.data_root/'backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        folder.mkdir(parents=True, exist_ok=False)
        # Read-only chip probing leaves the board in download mode until backup completes.
        device = self.probe(port, folder)
        check_profile(device, confirmed_board)
        if shutil.disk_usage(folder).free < FLASH_BYTES*3 + 128*1024*1024:
            raise InstallError('磁盘空间不足，未备份或刷写')
        for number in (1, 2):
            path = folder/f'factory-{number}.bin'
            self.run(['--chip','esp32s3','--port',port,'--baud','460800','--before','no-reset',
                      '--after','no-reset','read-flash','0',str(FLASH_BYTES),str(path)], folder/'device-tool.log')
            if not path.is_file() or path.stat().st_size != FLASH_BYTES:
                raise InstallError('完整备份长度错误，禁止刷写')
        first, second = folder/'factory-1.bin', folder/'factory-2.bin'
        checksum = digest(first)
        if checksum != digest(second):
            raise InstallError('两次完整备份不一致，禁止刷写；备份已保留')
        info = {'schema':1,'device':asdict(device),'profile':PROFILE,'double_read_verified':True,
                'snapshot_kind':'pre-install-full-flash','stock_firmware_verified':False,
                'restore_tested':False,'bytes':FLASH_BYTES,'sha256':checksum,
                'images':['factory-1.bin','factory-2.bin']}
        (folder/'backup.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        if application_only:
            # Own-profile updates only. Preserve NVS, audio, bonds and boot metadata.
            expected=(self.bundle['_root']/'partitions.bin').read_bytes()
            with first.open('rb') as image:
                image.seek(0x8000);current=image.read(len(expected))
            if current!=expected:
                raise InstallError('现有分区与此版本不同，禁止仅更新应用；完整备份已保留，请核对原固件后使用首次安装流程')
        self.same_device(device, self.probe(port, folder, before='no-reset'))
        check_profile(device, confirmed_board)
        # Recheck packaged files immediately before the only destructive operation.
        bundle = load_bundle(self.bundle['_root'])
        args = ['--chip','esp32s3','--port',port,'--baud','460800','--before','no-reset',
                '--after','hard-reset','write-flash','--flash-mode','dio','--flash-freq','80m','--flash-size','16MB']
        for item in bundle['files']:
            if application_only and item['offset']!=0x10000:continue
            args.extend([hex(item['offset']),str(bundle['_root']/item['name'])])
        self.run(args, folder/'device-tool.log')
        private = self.data_root/'devices'/device.device_id
        private.mkdir(parents=True,exist_ok=True)
        (private/'device.json').write_text(json.dumps({'device':asdict(device),'profile':PROFILE,
            'version':bundle['version'],'backup_manifest':str(folder/'backup.json'),'application_only':application_only},ensure_ascii=False,indent=2),encoding='utf-8')
        return device, folder/'backup.json'

    def restore(self, port, manifest_path):
        path = Path(manifest_path).resolve()
        info = json.loads(path.read_text(encoding='utf-8'))
        if info.get('schema') != 1 or info.get('profile') != PROFILE or info.get('double_read_verified') is not True or info.get('bytes') != FLASH_BYTES:
            raise InstallError('不是已验证的完整备份清单')
        images = []
        if info.get('images') != ['factory-1.bin','factory-2.bin']:
            raise InstallError('双份备份清单无效')
        for name in info.get('images', []):
            image = (path.parent/name).resolve()
            if image.parent != path.parent or not image.is_file() or image.stat().st_size != FLASH_BYTES or digest(image) != info['sha256']:
                raise InstallError('备份路径、长度或散列无效')
            images.append(image)
        if len(images) != 2:
            raise InstallError('缺少双份备份')
        current = self.probe(port,path.parent)
        expected = Device(**info['device'])
        # COM names may change, but hardware identity and security must match.
        if current.device_id != expected.device_id or current.flash_bytes != expected.flash_bytes:
            raise InstallError('备份属于另一台设备，禁止恢复')
        check_profile(current,True)
        self.run(['--chip','esp32s3','--port',port,'--baud','460800','--before','no-reset',
                  '--after','hard-reset','write-flash','0',str(images[0])],path.parent/'device-tool.log')
