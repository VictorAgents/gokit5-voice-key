# SPDX-License-Identifier: GPL-3.0-or-later
"""Local-only Gokit5 provisioning UI. Wi-Fi password goes to USB, never chat."""
import base64
import json
from pathlib import Path
import queue
import re
import secrets
import threading
import time
import tkinter as tk
from tkinter import ttk

import serial
from serial.tools import list_ports

from runtime import DATA_ROOT, CONFIG_PATH, active_id, device_dir
ROOT = DATA_ROOT
CONFIG = CONFIG_PATH
EXPECTED_DEVICE = active_id()


def provisioning_key(config=CONFIG):
    config=Path(config)
    pending=config.with_name('provision-pending.json')
    existing=config if config.exists() else pending
    record=json.loads(existing.read_text(encoding='utf-8')) if existing.exists() else {}
    if record.get('device_id',EXPECTED_DEVICE)!=EXPECTED_DEVICE:raise RuntimeError('本机配网密钥属于另一台设备')
    key=record.get('key') or secrets.token_hex(32)
    if len(bytes.fromhex(key))!=32:raise RuntimeError('本地配对配置格式不正确')
    return key,pending


def connection_failure(reason):
    detail={201:'设备未找到可用网络，请核对2.4GHz网络名、覆盖和隐藏网络设置',
            202:'认证失败，请在本机核对密码和路由器加密设置',
            15:'无线认证握手超时，请检查密码、信号和路由器兼容性',
            204:'无线认证握手超时，请检查密码、信号和路由器兼容性',
            210:'设备未找到兼容的安全模式，请检查路由器加密设置',
            211:'路由器安全模式不符合设备要求，请检查加密设置',
            212:'可用网络信号未达连接要求，请靠近路由器'}.get(reason,'请检查2.4GHz网络、密码、信号和路由器设置')
    return f'连接超时（设备原因码{reason}）：{detail}' if reason else '连接超时：设备尚未取得IP，请核对2.4GHz网络、密码和信号'


def provision(ssid, password, updates):
    try:
        if active_id()!=EXPECTED_DEVICE or not EXPECTED_DEVICE:
            raise RuntimeError('设备选择已切换，请关闭配网窗口后重新打开；未发送密码')
        ports = [p for p in list_ports.comports() if p.vid == 0x303a and p.pid == 0x1001
                 and (p.serial_number or '').replace(':', '').lower() == EXPECTED_DEVICE]
        if len(ports) != 1:
            raise RuntimeError('未找到已确认身份的 Gokit5，请保持 USB 连接')
        key,pending=provisioning_key(CONFIG)
        port = serial.Serial(port=None, baudrate=115200, timeout=.2)
        port.dtr = False
        port.rts = False
        port.port = ports[0].device
        with port:
            # Serial(None) is closed before context entry; open explicitly.
            if not port.is_open:
                port.open()
            port.reset_input_buffer()
            port.write(b's\n')
            firmware_ready = False
            until = time.monotonic()+3
            while time.monotonic() < until:
                if re.search(rb'WIRELESS v0\.(?:[3-9]|1[0-3])\b', port.readline()):
                    firmware_ready = True
                    break
            if not firmware_ready:
                raise RuntimeError('设备未运行兼容固件，未发送 Wi-Fi 密码')
            encode = lambda s: base64.b64encode(s.encode('utf-8')).decode('ascii')
            if active_id()!=EXPECTED_DEVICE:
                raise RuntimeError('设备选择已改变，未发送Wi-Fi配置')
            # Keep only the per-device pairing key on timeout, never the Wi-Fi password.
            pending.parent.mkdir(parents=True,exist_ok=True)
            temporary=pending.with_suffix('.tmp')
            temporary.write_text(json.dumps({'device_id':EXPECTED_DEVICE,'key':key}),encoding='utf-8')
            temporary.replace(pending)
            command = f'CFG {encode(ssid)} {encode(password)} {key}\n'
            port.write(command.encode('ascii'))
            updates.put(('status', '已通过 USB 提交配置，正在等待 Wi-Fi 连接……'))
            deadline = time.monotonic()+40
            last_query = 0
            last_reason=0
            while time.monotonic() < deadline:
                if time.monotonic()-last_query > 1:
                    port.write(b's\n')
                    last_query = time.monotonic()
                line = port.readline().decode('utf-8', errors='replace')
                if 'CONFIG_ERROR' in line:
                    raise RuntimeError('设备拒绝配置，请检查字段长度')
                reason=re.search(r'WIFI_(?:DISCONNECTED|DIAGNOSTIC) reason=(\d+)',line)
                if reason:last_reason=int(reason[1])
                match = re.search(r'WIRELESS v0\.(?:[3-9]|1[0-3])\b .*wifi=3 ip=(\d+\.\d+\.\d+\.\d+)', line)
                if match and match[1] != '0.0.0.0':
                    CONFIG.parent.mkdir(parents=True,exist_ok=True)
                    temporary = CONFIG.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'device_id': EXPECTED_DEVICE, 'host': match[1], 'key': key}, indent=2), encoding='utf-8')
                    temporary.replace(CONFIG)
                    pending.unlink(missing_ok=True)
                    updates.put(('done', '配网成功，接收端配置已保存。可以关闭此窗口。'))
                    return
            raise RuntimeError(connection_failure(last_reason))
    except Exception as exc:
        # Never print command, pairing key, password, or raw serial lines.
        updates.put(('error', str(exc) if isinstance(exc, RuntimeError) else '连接失败，请检查 USB 连接后重试'))


def main():
    app = tk.Tk()
    app.title('Gokit5 Community 本地配网 · '+str(active_id()))
    app.geometry('540x310')
    app.resizable(False, False)
    frame = ttk.Frame(app, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='密码只通过 USB 发给这台设备，不会发到聊天或云端。', wraplength=490).pack(anchor='w', pady=(0, 18))
    ttk.Label(frame, text='2.4GHz Wi-Fi 名称').pack(anchor='w')
    ssid_entry = ttk.Entry(frame, width=55)
    ssid_entry.pack(fill='x', pady=(4, 10))
    ttk.Label(frame, text='Wi-Fi 密码').pack(anchor='w')
    password_entry = ttk.Entry(frame, width=55, show='●')
    password_entry.pack(fill='x', pady=(4, 12))
    status = tk.StringVar(value='请保持 Gokit5 的 USB 数据线连接。')
    updates = queue.Queue()
    busy = False

    def submit():
        nonlocal busy
        ssid, password = ssid_entry.get(), password_entry.get()
        if not 1 <= len(ssid.encode('utf-8')) <= 32 or not 8 <= len(password.encode('utf-8')) <= 63:
            status.set('Wi-Fi 名称须为 1～32 字节，密码须为 8～63 字节。')
            return
        busy = True
        button.config(state='disabled')
        password_entry.delete(0, 'end')
        status.set('正在检查设备固件……')
        threading.Thread(target=provision, args=(ssid, password, updates), daemon=True).start()

    def poll():
        nonlocal busy
        while not updates.empty():
            kind, message = updates.get_nowait()
            status.set(message)
            if kind in ('done', 'error'):
                busy = False
                button.config(state='normal')
        app.after(200, poll)

    def close():
        if busy:
            status.set('正在配网，请等待结果后关闭。')
        else:
            app.destroy()

    button = ttk.Button(frame, text='通过 USB 配置 Wi-Fi', command=submit)
    button.pack(anchor='w')
    ttk.Label(frame, textvariable=status, wraplength=490).pack(anchor='w', pady=(12, 0))
    app.protocol('WM_DELETE_WINDOW', close)
    app.after(200, poll)
    app.mainloop()


if __name__ == '__main__':
    main()
