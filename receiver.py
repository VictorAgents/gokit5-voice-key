# SPDX-License-Identifier: GPL-3.0-or-later
"""Authenticated local Wi-Fi audio receiver for Gokit5 and VB-CABLE."""
import argparse
from collections import deque
import ctypes
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time

from runtime import CONFIG_PATH, active_id
from hotkeys import ShortcutRouter, WindowsKeyboard
from hid_config import config_packet, packet_revision
from device_options import options_packet, preview_packet
from audio_assets import AssetUpload, decode_assets
from battery_status import decode_battery
from settings import (LiveSettings, SETTINGS_PATH, STATUS_PATH, atomic_json,
                      revision)

HEADER = struct.Struct('!4sBBH')
RECORD_START_TIMEOUT = 3


class ProtocolError(Exception):
    pass


class SingleInstance:
    """Prevent audio-only and shortcut modes competing for the same device."""
    def __init__(self, name='Local\\Gokit5CommunityReceiver'):
        from ctypes import wintypes as w
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        self.kernel.CreateMutexW.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.handle = self.kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        if error == 183:
            self.close()
            raise RuntimeError('Another Gokit5 receiver is running; stop it before starting a new mode')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def send_frame(sock, kind, data=b''):
    sock.sendall(HEADER.pack(b'GK5P', 1, kind, len(data)) + data)


def receive_frame(sock, timeout=6):
    deadline = time.monotonic() + timeout

    def exact(size):
        chunks = bytearray()
        while len(chunks) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Device heartbeat timeout')
            sock.settimeout(remaining)
            data = sock.recv(size - len(chunks))
            if not data:
                raise ConnectionError('Device disconnected')
            chunks.extend(data)
        return bytes(chunks)

    magic, version, kind, size = HEADER.unpack(exact(HEADER.size))
    if magic != b'GK5P' or version != 1 or size > 1024:
        raise ProtocolError('Invalid frame')
    return kind, exact(size)


def authenticate(sock, key):
    kind, nonce = receive_frame(sock, 8)
    if kind != 1 or len(nonce) != 16:
        raise ProtocolError('Invalid challenge')
    send_frame(sock, 2, hmac.digest(key, nonce + b'client', 'sha256'))
    kind, proof = receive_frame(sock, 8)
    expected = hmac.digest(key, nonce + b'device', 'sha256')
    if kind != 3 or not hmac.compare_digest(proof, expected):
        raise ProtocolError('Device authentication failed')


class RightAlt:
    def __init__(self, enabled):
        self.enabled, self.down = enabled, False

    def set(self, down):
        if down == self.down:
            return
        if self.enabled:
            from ctypes import wintypes as w

            class Keyboard(ctypes.Structure):
                _fields_ = [('vk', w.WORD), ('scan', w.WORD), ('flags', w.DWORD),
                            ('time', w.DWORD), ('extra', ctypes.c_size_t)]

            class Mouse(ctypes.Structure):
                _fields_ = [('x', w.LONG), ('y', w.LONG), ('data', w.DWORD),
                            ('flags', w.DWORD), ('time', w.DWORD), ('extra', ctypes.c_size_t)]

            class Payload(ctypes.Union):
                _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]

            class Input(ctypes.Structure):
                _fields_ = [('kind', w.DWORD), ('payload', Payload)]

            flags = 0x0008 | 0x0001 | (0x0002 if not down else 0)
            event = Input(1, Payload(keyboard=Keyboard(0, 0x38, flags, 0, 0)))
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
            user32.SendInput.restype = w.UINT
            if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
                raise ctypes.WinError(ctypes.get_last_error())
        self.down = down


class CableAudio:
    def __init__(self, gain=4.0):
        import sounddevice as sd
        import numpy as np
        self.np = np
        # Load/initialize numeric code before connecting or holding any shortcut.
        np.repeat(np.zeros(240, dtype='<i2'), 3)
        self.gain = gain
        self.queue = deque()
        self.lock = threading.Lock()
        self.started = 0
        self.error = None
        self.underflows = 0
        hosts = sd.query_hostapis()
        device = next((i for i, d in enumerate(sd.query_devices())
                       if d['name'].startswith('CABLE Input (VB-Audio Virtual Cable)')
                       and hosts[d['hostapi']]['name'] == 'Windows WASAPI'
                       and d['max_output_channels'] >= 2), None)
        if device is None:
            raise RuntimeError('VB-CABLE WASAPI playback endpoint missing')
        self.stream = sd.RawOutputStream(device=device, samplerate=48000, channels=2,
                                        dtype='int16', blocksize=720, callback=self.callback)
        self.stream.start()

    def callback(self, out, frames, info, status):
        try:
            with self.lock:
                chunk = self.queue.popleft() if self.queue and time.monotonic()-self.started >= .25 else None
            if chunk is None:
                out[:] = bytes(len(out))
            elif len(chunk) != len(out):
                raise RuntimeError('Unexpected audio frame size')
            else:
                out[:] = chunk
            if status.output_underflow:
                self.underflows += 1
        except BaseException as exc:
            self.error = exc
            out[:] = bytes(len(out))

    def start(self):
        if self.error:
            raise self.error
        if not self.stream.active:
            raise RuntimeError('VB-CABLE audio stream is not active')
        with self.lock:
            self.queue.clear()
            self.started = time.monotonic()

    def push(self, pcm):
        np = self.np
        if len(pcm) != 480:
            raise ProtocolError('Expected 240 mono PCM16 samples')
        mono = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
        scaled = np.clip(mono*self.gain, -32768, 32767).astype('<i2')
        stereo = np.repeat(np.repeat(scaled, 3)[:, None], 2, axis=1).tobytes()
        with self.lock:
            if len(self.queue) >= 60:
                raise RuntimeError('Audio backlog exceeded 900ms; releasing shortcut')
            self.queue.append(stereo)
        if self.error:
            raise self.error

    def drain(self):
        deadline = time.monotonic()+1.3
        while time.monotonic() < deadline:
            with self.lock:
                if not self.queue:
                    break
            time.sleep(.01)
        else:
            raise RuntimeError('Audio drain timeout')
        time.sleep(.12)

    def clear(self):
        with self.lock:
            self.queue.clear()

    def close(self):
        self.stream.stop()
        self.stream.close()


class ConfigurationOnlyAudio:
    """Retry audio initialization while keeping BLE configuration available."""
    def __init__(self,gain=4):
        self.gain=gain;self.output=None;self.next_retry=0
    @property
    def available(self):return self.output is not None
    def refresh(self,active=False):
        if self.output is None and not active and time.monotonic()>=self.next_retry:
            self.next_retry=time.monotonic()+5
            try:self.output=CableAudio(self.gain)
            except Exception:pass
    def start(self):
        self.refresh()
        if self.output is None:raise RuntimeError('Virtual microphone unavailable; BLE configuration remains available')
        self.output.start()
    def push(self,data):
        if self.output is None:raise RuntimeError('Virtual microphone unavailable')
        self.output.gain=self.gain;self.output.push(data)
    def drain(self):
        if self.output:self.output.drain()
    def clear(self):
        if self.output:
            self.output.clear()
            if self.output.error:self.output.close();self.output=None
    def close(self):
        if self.output:self.output.close();self.output=None


class DesktopControls:
    """Live mappings and a non-secret status snapshot for the desktop panel."""
    def __init__(self, settings_path=SETTINGS_PATH, status_path=STATUS_PATH,
                 enabled=True, gain_override=None):
        self.settings = LiveSettings(settings_path)
        self.router = ShortcutRouter(WindowsKeyboard(enabled).emit)
        self.status_path = status_path
        self.gain_override = gain_override
        self.pressed = set()
        self.device_keyboard = False
        self.last_write = 0
        self.last_preview_id = None
        self.preview_path = Path(status_path).with_name('preview-request.json')
        self.upload_path = Path(status_path).with_name('audio-upload-request.json')
        self.state = {'pid': os.getpid(), 'device_id': active_id(), 'state': 'connecting', 'firmware': '',
                      'recording': False, 'buttons': {}, 'level': 0, 'last_event': '',
                      'settings_revision': revision(self.settings.value), 'settings_error': ''}

    def next_preview(self):
        claimed=self.preview_path.with_name(f'preview-request.{os.getpid()}.claimed')
        try:
            self.preview_path.replace(claimed)
            request=json.loads(claimed.read_text(encoding='utf-8'))
            if request.get('device_id')!=self.state.get('device_id'):
                raise ValueError('音效请求属于另一台设备，已忽略')
            nonce=request['request_id']
            if nonce==self.last_preview_id:
                return None
            self.last_preview_id=nonce
            age=time.time()-request['created']
            if not 0<=age<=15:
                return None
            payload=preview_packet(request)
            self.publish(force=True, preview_request_id=nonce, preview_result='正在试听…')
            return payload
        except (OSError, ValueError, KeyError, TypeError):
            return None
        finally:
            try: claimed.unlink(missing_ok=True)
            except OSError: pass

    def next_upload(self):
        claimed=self.upload_path.with_name(f'audio-upload.{os.getpid()}.claimed')
        request={}
        try:
            self.upload_path.replace(claimed)
            request=json.loads(claimed.read_text(encoding='utf-8'))
            if request.get('device_id')!=self.state.get('device_id'):
                raise ValueError('音效请求属于另一台设备，已忽略')
            if not 0<=time.time()-request['created']<=120:
                raise ValueError('上传请求已过期，请重新导入')
            upload=AssetUpload(request,self.settings.path.parent/'audio')
            if len(upload.data)>96000 and self.state.get('firmware') not in ('0.12.0','0.13.0'):
                raise ValueError('超过 3 秒的音效需要先升级到固件 0.12')
            self.publish(force=True,upload_request_id=upload.nonce,upload_event=upload.event,
                         upload_progress=0,upload_result='正在上传…',upload_done=False)
            return upload
        except FileNotFoundError:
            if request:
                self.publish(force=True,upload_request_id=request.get('request_id'),upload_result='找不到已转换的音效，请重新导入',upload_done=True)
            return None
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.publish(force=True,upload_request_id=request.get('request_id'),upload_result=str(exc),upload_done=True)
            return None
        finally:
            try:claimed.unlink(missing_ok=True)
            except OSError:pass

    def publish(self, force=False, **changes):
        self.state.update(changes)
        now = time.monotonic()
        if force or now-self.last_write >= .3:
            self.state['updated'] = time.time()
            try:
                atomic_json(self.status_path, self.state)
            except OSError:
                # A busy status file must never interrupt the microphone path.
                pass
            self.last_write = now

    def refresh(self, audio, active):
        if hasattr(audio,'refresh'):
            audio.refresh(active)
            self.state['audio_available']=audio.available
        self.settings.refresh(active or bool(self.pressed) or bool(self.router.held))
        audio.gain = self.gain_override if self.gain_override is not None else self.settings.value['gain']
        self.publish(settings_revision=revision(self.settings.value), settings_error=self.settings.error or '')

    def button(self, name, down, device_event=False):
        if down:
            if name in self.pressed:
                raise ProtocolError('Duplicate button down')
            self.pressed.add(name)
            binding = self.settings.value['buttons'][name]
            if not self.device_keyboard or (name == 'mic' and not device_event and binding['mode'] == 'ptt'):
                self.router.press(name, binding['shortcut'], binding['mode'])
        else:
            if name not in self.pressed:
                raise ProtocolError('Button up without down')
            self.router.release(name)
            self.pressed.discard(name)
        self.publish(force=True, buttons={key: key in self.pressed for key in ('mic','plus','minus')},
                     last_event=f'{name}: {"DOWN" if down else "UP"}')

    def reset(self):
        self.router.release_all()
        self.pressed.clear()
        self.publish(force=True, recording=False, recording_stage='idle', buttons={}, level=0)


class MappedMicrophone:
    def __init__(self, controls):
        self.controls, self.down = controls, False

    def set(self, down):
        if down == self.down:
            return
        self.controls.button('mic', down)
        self.down = down
        self.controls.publish(force=True, recording=down, level=0)


def session(sock, key, audio, alt, deadline, should_stop=lambda: False, controls=None):
    authenticate(sock, key)
    active = False
    heartbeat_stop = threading.Event()
    heartbeat_failed = threading.Event()
    heartbeat_stats = {'sent': 0, 'last': 0}
    send_lock = threading.Lock()
    def send(kind, data=b''):
        with send_lock:
            send_frame(sock, kind, data)

    def heartbeat():
        while not heartbeat_stop.is_set():
            try:
                send(9)
                heartbeat_stats['sent'] += 1
                heartbeat_stats['last'] = time.time()
            except OSError:
                heartbeat_failed.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            heartbeat_stop.wait(.5)

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    audio_bytes = 0
    peak = 0
    utterance_bytes = 0
    utterance_peak = 0
    audio_enabled = True
    sent_config = None
    config_sent_at = 0
    sent_options = None
    options_sent_at = 0
    upload = None
    device_version = ''
    request_nonce = None
    awaiting_start = False
    prepared_at = 0
    preview_until = 0
    try:
        print('Device authenticated; ready for microphone button', flush=True)
        if controls:
            controls.device_keyboard = False
            controls.publish(force=True, state='connected', firmware='', error='', device_revision='', options_revision='', ble_connected=False,
                             battery={}, battery_updated=0, recording_stage='idle')
        while time.monotonic() < deadline and not should_stop():
            if awaiting_start and time.monotonic()-prepared_at>RECORD_START_TIMEOUT:
                raise TimeoutError('Recording start confirmation timeout')
            if controls:
                controls.state['heartbeat_sent'] = heartbeat_stats['sent']
                controls.state['heartbeat_last_sent'] = heartbeat_stats['last']
                controls.refresh(audio, active)
                if controls.device_keyboard and not active and not controls.pressed:
                    schema = 2 if controls.state.get('firmware') in ('0.7.0','0.8.0','0.9.0','0.10.0','0.11.0','0.12.0','0.13.0') else 1
                    wanted = config_packet(controls.settings.value, schema)
                    if wanted != sent_config or (controls.state.get('device_revision') != packet_revision(wanted) and time.monotonic()-config_sent_at > 5):
                        send(12, wanted)
                        sent_config = wanted
                        config_sent_at = time.monotonic()
                    if controls.state.get('firmware') in ('0.8.0','0.9.0','0.10.0','0.11.0','0.12.0','0.13.0'):
                        wanted_options=options_packet(controls.settings.value['device'])
                        compatible=controls.state.get('firmware') in ('0.9.0','0.10.0','0.11.0','0.12.0','0.13.0') or max(wanted_options[1:3])<3
                        if compatible and (wanted_options!=sent_options or (controls.state.get('options_revision')!=packet_revision(wanted_options) and time.monotonic()-options_sent_at>5)):
                            send(15,wanted_options);sent_options=wanted_options;options_sent_at=time.monotonic()
                        if upload is None:
                            preview=controls.next_preview()
                            if preview is not None and (controls.state.get('firmware') in ('0.9.0','0.10.0','0.11.0','0.12.0','0.13.0') or preview[2]<3):
                                send(17,preview)
                                # Six seconds of synchronous device playback plus DMA drain.
                                # Keep the normal six-second failure detection outside preview.
                                preview_until=time.monotonic()+10
                            if controls.state.get('firmware') in ('0.9.0','0.10.0','0.11.0','0.12.0','0.13.0'):
                                upload=controls.next_upload()
                                if upload is not None:send(*upload.begin())
            if heartbeat_failed.is_set():
                raise ConnectionError('Heartbeat sender failed')
            kind, data = receive_frame(sock,max(6,preview_until-time.monotonic()))
            if kind == 28 and len(data)==4 and not active and device_version in ('0.11.0','0.12.0','0.13.0'):
                audio_enabled = controls is None or controls.settings.value['buttons']['mic']['mode']=='ptt'
                if not audio_enabled:
                    send(29,data+b'\1')
                    continue
                utterance_bytes = utterance_peak = 0
                audio.start()
                alt.set(True)
                active=True;request_nonce=data;awaiting_start=True;prepared_at=time.monotonic()
                if controls:
                    controls.publish(force=True,recording_stage='preparing')
                # Success is sent only after CABLE is active and the owned
                # shortcut has been pressed. The device plays its prompt first.
                send(29,data+b'\0')
                print('MIC PREPARING; PC ready acknowledged',flush=True)
            elif kind == 4 and len(data)==4 and active and awaiting_start and data==request_nonce:
                awaiting_start=False
                if controls:controls.publish(force=True,recording_stage='recording')
                print('MIC DOWN; device cue complete',flush=True)
            elif kind == 30 and len(data)==4:
                if active and data==request_nonce:
                    alt.set(False);audio.clear();active=False;request_nonce=None;awaiting_start=False
                    if controls:controls.publish(force=True,recording_stage='idle')
                    print('MIC START CANCELLED; shortcut released',flush=True)
            elif kind == 4 and not data and not active:
                utterance_bytes = utterance_peak = 0
                audio_enabled = controls is None or controls.settings.value['buttons']['mic']['mode'] == 'ptt'
                if audio_enabled:
                    audio.start()
                key_started = time.monotonic()
                if audio_enabled:
                    alt.set(True)
                active = True
                print(f'MIC DOWN; keypress_ms={(time.monotonic()-key_started)*1000:.0f}', flush=True)
            elif kind == 5 and active and not awaiting_start:
                if audio_enabled:
                    audio.push(data)
                elif len(data) != 480:
                    raise ProtocolError('Expected 240 mono PCM16 samples')
                audio_bytes += len(data)
                samples = struct.unpack('<240h', data)
                block_peak = max(abs(x) for x in samples)
                peak = max(peak, block_peak)
                utterance_bytes += len(data)
                utterance_peak = max(utterance_peak, block_peak)
                if controls:
                    controls.publish(level=min(1, block_peak*audio.gain/32768))
            elif kind == 6 and not data and active:
                if audio_enabled:
                    audio.drain()
                    alt.set(False)
                active = False
                request_nonce=None;awaiting_start=False
                if controls:controls.publish(force=True,recording_stage='idle')
                print(f'MIC UP; PCM bytes={utterance_bytes}, peak={utterance_peak}', flush=True)
            elif kind == 7 and not data:
                pass
            elif kind == 8 and len(data) == 2 and data[0] in (1, 2, 3) and data[1] in (0, 1):
                name = {1:'mic',2:'plus',3:'minus'}[data[0]]
                if controls:
                    controls.button(name, bool(data[1]), device_event=True)
                print(f'BUTTON {name.upper()} {"DOWN" if data[1] else "UP"}', flush=True)
            elif kind == 10 and not data:
                raise ConnectionError('Device powering off')
            elif kind == 11 and data in (b'0.4.0', b'0.5.0', b'0.7.0', b'0.8.0', b'0.9.0', b'0.10.0', b'0.11.0', b'0.12.0', b'0.13.0'):
                device_version=data.decode('ascii')
                if controls:
                    controls.device_keyboard = data in (b'0.5.0', b'0.7.0', b'0.8.0', b'0.9.0', b'0.10.0', b'0.11.0', b'0.12.0', b'0.13.0')
                    controls.publish(force=True, firmware=data.decode('ascii'))
            elif kind == 13 and len(data) in (31, 41) and controls:
                if data == sent_config:
                    controls.publish(force=True, device_revision=packet_revision(data))
            elif kind == 14 and data in (b'\0', b'\1'):
                if controls:
                    controls.publish(force=True, ble_connected=data == b'\1')
            elif kind == 16 and len(data)==10 and controls:
                if data==sent_options:
                    controls.publish(force=True, options_revision=packet_revision(data))
            elif kind == 18 and len(data)==5 and data[4] in range(6):
                nonce=int.from_bytes(data[:4],'big')
                if controls and nonce==controls.last_preview_id:
                    preview_until=0
                    message=('设备已播放','该提示音已关闭','请松开设备按键后重试','设备喇叭未就绪','设备播放失败','请先上传此开/关机音效')[data[4]]
                    controls.publish(force=True, preview_request_id=nonce, preview_result=message)
            elif kind == 23 and len(data)==9:
                if upload is not None:
                    next_frame=upload.ack(data)
                    if controls:
                        controls.publish(force=True,upload_progress=round(100*upload.offset/len(upload.data)),
                                         upload_done=upload.done,upload_result=upload.error or ('上传完成，请保存启用' if upload.done else '正在上传…'))
                    if next_frame is not None:send(*next_frame)
                    if upload.done:
                        if upload.error:send(24,struct.pack('!I',upload.nonce))
                        upload=None
            elif kind == 26 and controls:
                try:assets=decode_assets(data)
                except ValueError as exc:raise ProtocolError(str(exc)) from exc
                controls.publish(force=True,custom_assets=assets)
            elif kind == 27:
                try: battery=decode_battery(data)
                except ValueError as exc: raise ProtocolError(str(exc)) from exc
                if controls:
                    controls.publish(force=True,battery=battery,battery_updated=time.time())
            else:
                raise ProtocolError('Unexpected message or recording state')
    finally:
        if upload is not None and controls:
            controls.publish(force=True,upload_done=True,upload_result='连接中断，原音效保留；请重新导入')
        try:
            alt.set(False)
        finally:
            audio.clear()
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=3)
            if controls:
                controls.reset()
        try:
            sock.settimeout(.2)
            send_frame(sock, 10)  # Explicit goodbye; older device firmware closes unknown control frames too.
        except OSError:
            pass
    return audio_bytes, peak


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    parser.add_argument('--no-hotkey', action='store_true', help='Verify audio only; never inject Alt')
    parser.add_argument('--seconds', type=float, default=float('inf'))
    parser.add_argument('--gain', type=float, default=None, help='Optional gain override; desktop settings otherwise')
    parser.add_argument('--settings', type=Path, default=SETTINGS_PATH)
    parser.add_argument('--status-file', type=Path, default=STATUS_PATH)
    parser.add_argument('--stop-file', type=Path, help='Exit cleanly if this local control file appears')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    key = bytes.fromhex(config['key'])
    if len(key) != 32 or (args.gain is not None and not .25 <= args.gain <= 16):
        raise SystemExit('Invalid pairing key or gain')
    if not ipaddress.ip_address(config['host']).is_private:
        raise SystemExit('Only local network device addresses are allowed')
    instance = SingleInstance()
    controls = DesktopControls(args.settings, args.status_file, not args.no_hotkey, args.gain)
    audio = ConfigurationOnlyAudio(args.gain if args.gain is not None else controls.settings.value['gain'])
    alt = MappedMicrophone(controls)
    deadline = time.monotonic()+args.seconds
    should_stop = lambda: args.stop_file is not None and args.stop_file.exists()
    try:
        while time.monotonic() < deadline and not should_stop():
            phase = 'connect'
            try:
                controls.publish(force=True, state='connecting', firmware='', level=0)
                with socket.create_connection((config['host'], 8765), timeout=3) as sock:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    phase = 'session'
                    session(sock, key, audio, alt, deadline, should_stop, controls)
            except (OSError, ProtocolError, RuntimeError) as exc:
                alt.set(False)
                controls.reset()
                audio.clear()
                controls.publish(force=True, state='reconnecting', error=type(exc).__name__)
                print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} Connection closed during {phase}: {type(exc).__name__}: {exc}; shortcut released', flush=True)
                time.sleep(min(1, max(0, deadline-time.monotonic())))
    except KeyboardInterrupt:
        pass
    finally:
        try:
            alt.set(False)
        finally:
            try:
                controls.reset()
                audio.close()
            finally:
                controls.publish(force=True, state='stopped', recording=False, level=0)
                instance.close()
        print('Receiver stopped; shortcut released', flush=True)
