# SPDX-License-Identifier: GPL-3.0-or-later
"""Local Gokit5 settings panel; no server, credentials or cloud connection."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import secrets
import time
import threading
import queue
import tkinter as tk
from tkinter import messagebox, ttk, colorchooser, filedialog

from runtime import DATA_ROOT, CONFIG_PATH, launch_command, active_id
from hotkeys import ALIASES, MODIFIERS, normalize_shortcut
from receiver import SingleInstance
from hid_config import config_packet, packet_revision
from device_options import options_packet
from audio_assets import convert_audio, store_asset
from battery_status import battery_text
import startup
from settings import (DEFAULTS, ROOT, SETTINGS_PATH, STATUS_PATH, STOP_PATH,
                      load_settings, revision, save_settings, atomic_json, validate_settings)

PRESETS = ('', 'RightAlt', 'Alt', 'Space', 'Enter', 'Escape', 'Ctrl+Space', 'Ctrl+Shift+Space',
           'Ctrl+C', 'Ctrl+V', 'Ctrl+Z', 'Ctrl+Shift+S', 'F13', 'F14',
           'VolumeUp', 'VolumeDown', 'VolumeMute', 'MediaPlayPause',
           'MediaPrevious', 'MediaNext')
BUTTON_NAMES = {'mic': '麦克风', 'plus': '＋ 加号键', 'minus': '－ 减号键', 'plus_minus': '＋ 和 －'}
MODE_NAMES = {'hold': '按住快捷键', 'tap': '点按快捷键', 'ptt': '按住说话'}
SOUND_NAMES = {'off': '关闭', 'soft': '轻提示', 'double': '双音提示', 'custom': '自定义文件'}
LIGHT_NAMES = {'off': '关闭', 'solid': '固定颜色', 'breathe': '呼吸', 'rainbow': '彩虹流动', 'keys': '按键反馈'}


class ShortcutDialog(tk.Toplevel):
    def __init__(self, parent, on_accept):
        super().__init__(parent)
        self.title('录入快捷键')
        self.geometry('440x220')
        self.resizable(False, False)
        self.transient(parent)
        self.on_accept = on_accept
        self.pressed = set()
        self.shortcut = ''
        self.display = tk.StringVar(value='请按一个键，或按组合键')
        ttk.Label(self, text='按下你想绑定的快捷键', font=('Microsoft YaHei UI', 14, 'bold')).pack(pady=(22, 12))
        ttk.Label(self, textvariable=self.display, font=('Segoe UI', 16)).pack(pady=10)
        ttk.Label(self, text='例如右 Alt、Ctrl + Shift + A。支持单独的修饰键。').pack(pady=6)
        self.accept = ttk.Button(self, text='使用此快捷键', command=self.finish, state='disabled')
        self.accept.pack(pady=12)
        self.bind('<KeyPress>', self.key_down)
        self.bind('<KeyRelease>', self.key_up)
        self.grab_set()
        self.after(100, self.focus_force)

    def key_down(self, event):
        key = ALIASES.get(event.keysym.lower())
        if key:
            self.pressed.add(key)
            modifiers = [k for k in MODIFIERS if k in self.pressed]
            bases = [k for k in self.pressed if k not in MODIFIERS]
            if len(bases) <= 1:
                self.shortcut = normalize_shortcut('+'.join(modifiers+bases))
                self.display.set(self.shortcut)
                self.accept.configure(state='normal')
        else:
            self.display.set('暂不支持此键，请换一个按键')
        return 'break'

    def key_up(self, event):
        self.pressed.discard(ALIASES.get(event.keysym.lower()))
        return 'break'

    def finish(self):
        if self.shortcut:
            self.on_accept(self.shortcut)
            self.destroy()


class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.identity = active_id()
        self.child = None
        self.child_log = None
        self.dirty = False
        self.loading = True
        self.saved_revision = ''
        self.saved_device_revision = ''
        self.saved_options_revision = ''
        self.preview_id = None
        self.preview_started = 0
        self.import_events=queue.Queue()
        self.importing=False
        self.upload_id=None
        self.upload_event=None
        self.upload_handled=None
        self.status_data = {}
        root.title('Gokit5 Community 控制台')
        root.geometry('560x690')
        root.minsize(520, 600)
        root.configure(bg='#f3f5f8')
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#f3f5f8')
        style.configure('TLabel', background='#f3f5f8', foreground='#263247')
        style.configure('Card.TFrame', background='white')
        style.configure('Card.TLabel', background='white')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 18, 'bold'))
        style.configure('Heading.TLabel', font=('Microsoft YaHei UI', 12, 'bold'))
        style.configure('Primary.TButton', padding=(14, 7), foreground='white', background='#2563eb')
        style.map('Primary.TButton', background=[('active', '#1d4ed8')])
        style.configure('TButton', padding=(8, 5))
        style.configure('TNotebook', background='#f3f5f8', borderwidth=0)
        style.configure('TNotebook.Tab', padding=(20, 8), background='#e8edf4', foreground='#68778b')
        style.map('TNotebook.Tab', background=[('selected', 'white')], foreground=[('selected', '#2563eb')])
        style.configure('Audio.Horizontal.TProgressbar', background='#22a87a', troughcolor='#e7edf4')

        content = ttk.Frame(root, padding=16)
        content.pack(fill='both', expand=True)
        # Status and Save stay visible; each category scrolls independently.
        self.tab_canvases = []
        def page(title):
            shell = ttk.Frame(self.notebook)
            self.notebook.add(shell, text=title)
            canvas = tk.Canvas(shell, bg='#f3f5f8', highlightthickness=0)
            canvas.pack(side='left', fill='both', expand=True)
            body = ttk.Frame(canvas, padding=(0, 12, 8, 8))
            frame_id = canvas.create_window((0, 0), window=body, anchor='nw')
            body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<Configure>', lambda e: canvas.itemconfigure(frame_id, width=e.width))
            self.tab_canvases.append(canvas)
            return body
        def scroll(event):
            if event.widget.winfo_class() not in ('TCombobox','TSpinbox','Entry','TEntry'):
                canvas = self.tab_canvases[self.notebook.index('current')]
                if canvas.bbox('all')[3] > canvas.winfo_height():
                    canvas.yview_scroll(-int(event.delta/120),'units')
                return 'break'
        root.bind('<MouseWheel>', scroll)
        ttk.Label(content, text='Gokit5 控制台', style='Title.TLabel').pack(anchor='w')
        ttk.Label(content, text='无线语音与快捷键', foreground='#68778b').pack(anchor='w', pady=(0, 10))
        top = ttk.Frame(content, style='Card.TFrame', padding=12)
        top.pack(fill='x', pady=(0, 12))
        self.connection = tk.StringVar(value='接收端未启动')
        self.firmware = tk.StringVar(value='连接后显示设备版本')
        self.battery = tk.StringVar(value='电量：待连接')
        self.last_event = tk.StringVar(value='按一下设备按键，这里会显示反馈')
        ttk.Label(top, textvariable=self.connection, style='Card.TLabel', font=('Microsoft YaHei UI', 12, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(top, textvariable=self.firmware, style='Card.TLabel', foreground='#718096', wraplength=460).grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.battery_label = ttk.Label(top, textvariable=self.battery, style='Card.TLabel', foreground='#718096', wraplength=460)
        self.battery_label.grid(row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))
        actions = ttk.Frame(top, style='Card.TFrame')
        actions.grid(row=0, column=1, padx=(8, 0))
        self.start_button = ttk.Button(actions, text='开始接收', command=self.start_receiver)
        self.start_button.pack(side='left', padx=(0, 8))
        self.stop_button = ttk.Button(actions, text='停止接收', command=self.stop_receiver)
        self.stop_button.pack(side='left')
        top.columnconfigure(0, weight=1)
        bottom = ttk.Frame(content, padding=(0, 12, 0, 0))
        bottom.pack(side='bottom', fill='x')
        ttk.Button(bottom, text='保存并应用', style='Primary.TButton', command=self.save).pack(side='right')
        ttk.Button(bottom, text='恢复默认', command=lambda: self.fill(deepcopy(DEFAULTS), dirty=True)).pack(side='right', padx=8)
        self.save_state = tk.StringVar(value='')
        ttk.Label(bottom, textvariable=self.save_state, foreground='#2563eb', wraplength=230).pack(side='left')
        self.notebook = ttk.Notebook(content)
        self.notebook.pack(fill='both', expand=True)
        keys_page, sound_page, light_page = page('按键'), page('声音'), page('灯光')
        self.canvas = self.tab_canvases[0]
        ttk.Label(keys_page, text='按键绑定', style='Heading.TLabel').pack(anchor='w', pady=(0, 5))
        ttk.Label(keys_page, text='填写或录入快捷键，留空表示不发送。', foreground='#68778b').pack(anchor='w', pady=(0, 8))
        self.bindings = {}
        for name in ('mic', 'plus', 'minus', 'plus_minus'):
            row = ttk.Frame(keys_page, style='Card.TFrame', padding=(10, 10))
            row.pack(fill='x', pady=3)
            ttk.Label(row, text=BUTTON_NAMES[name], style='Card.TLabel', width=9).grid(row=0, column=0, sticky='w')
            shortcut, mode = tk.StringVar(), tk.StringVar()
            entry = ttk.Combobox(row, textvariable=shortcut, values=PRESETS, width=12)
            entry.grid(row=0, column=1, sticky='ew', padx=(4, 8))
            ttk.Button(row, text='录入', width=4, command=lambda n=name: self.record(n)).grid(row=0, column=2, padx=(0, 6))
            if name == 'mic':
                values=('按住说话','按住快捷键','点按快捷键')
            elif name == 'plus_minus':
                values=('点按快捷键',)
            else:
                values=('点按快捷键','按住快捷键')
            ttk.Combobox(row, textvariable=mode, values=values, state='readonly', width=10).grid(row=0, column=3)
            row.columnconfigure(1, weight=1)
            self.bindings[name] = (shortcut, mode)
            shortcut.trace_add('write', self.mark_dirty)
            mode.trace_add('write', self.mark_dirty)
        ttk.Label(keys_page, textvariable=self.last_event, foreground='#68778b').pack(anchor='w', pady=(8, 13))
        audio = ttk.Frame(keys_page)
        audio.pack(fill='x')
        ttk.Label(audio, text='麦克风增益').pack(side='left')
        self.gain = tk.DoubleVar(value=4)
        ttk.Spinbox(audio, from_=.25, to=16, increment=.25, textvariable=self.gain, width=6).pack(side='left', padx=(10, 4))
        ttk.Label(audio, text='倍').pack(side='left')
        self.gain.trace_add('write', self.mark_dirty)
        ttk.Label(audio, text='电平').pack(side='left', padx=(16, 8))
        self.meter = ttk.Progressbar(audio, style='Audio.Horizontal.TProgressbar', length=90, maximum=1)
        self.meter.pack(side='left', fill='x', expand=True)
        ttk.Label(sound_page, text='开关机提示音', style='Heading.TLabel').pack(anchor='w', pady=(0,8))
        sound_card = ttk.Frame(sound_page, style='Card.TFrame', padding=12)
        sound_card.pack(fill='x')
        self.sound_modes = {}
        self.asset_labels={}
        for row, (name,label) in enumerate((('startup','开机声音'),('shutdown','关机声音'))):
            row *= 2
            ttk.Label(sound_card, text=label, style='Card.TLabel', width=8).grid(row=row,column=0,sticky='w',pady=4)
            mode = tk.StringVar()
            ttk.Combobox(sound_card,textvariable=mode,values=tuple(SOUND_NAMES.values()),state='readonly',width=12).grid(row=row,column=1,sticky='ew',padx=6)
            ttk.Button(sound_card,text='试听',command=lambda n=name:self.preview_sound(n)).grid(row=row,column=2,padx=4)
            ttk.Button(sound_card,text='导入音效…',command=lambda n=name:self.import_audio(n)).grid(row=row,column=3,padx=4)
            label=tk.StringVar(value='未上传自定义文件')
            ttk.Label(sound_card,textvariable=label,style='Card.TLabel',foreground='#718096').grid(row=row+1,column=1,columnspan=3,sticky='w',padx=6,pady=(0,12))
            self.asset_labels[name]=label
            self.sound_modes[name]=mode
            mode.trace_add('write',self.mark_dirty)
        self.sound_volume = tk.IntVar(value=25)
        sound_card.columnconfigure(1, weight=1)
        ttk.Label(sound_card,text='音量',style='Card.TLabel').grid(row=4,column=0,sticky='w',pady=(8,0))
        ttk.Spinbox(sound_card,from_=0,to=100,textvariable=self.sound_volume,width=6).grid(row=4,column=1,sticky='w',padx=6,pady=(8,0))
        ttk.Label(sound_card,text='0～100 · 设备喇叭',style='Card.TLabel').grid(row=4,column=2,columnspan=2,sticky='w',pady=(8,0))
        self.sound_volume.trace_add('write',self.mark_dirty)
        self.preview_state = tk.StringVar(value='试听使用当前选择，不会自动保存；录音中不可试听开关机音效。')
        ttk.Label(sound_page,textvariable=self.preview_state,foreground='#68778b',wraplength=450).pack(anchor='w',pady=(10,0))
        self.upload_state=tk.StringVar(value='可导入 WAV/MP3 等；自动转换，最长取开头 6 秒，原文件不改动。')
        ttk.Label(sound_page,textvariable=self.upload_state,foreground='#68778b',wraplength=450).pack(anchor='w',pady=(7,0))
        ttk.Label(sound_page,text='录音就绪提示：按住说话，收到电脑确认后响一声，提示音结束后再说。沿用提示音音量，设为 0 可静音。',foreground='#68778b',wraplength=450).pack(anchor='w',pady=(12,0))

        ttk.Label(light_page,text='灯光模板',style='Heading.TLabel').pack(anchor='w',pady=(0,8))
        light_card=ttk.Frame(light_page,style='Card.TFrame',padding=12)
        light_card.pack(fill='x')
        self.light_template,self.light_color=tk.StringVar(),tk.StringVar()
        self.light_brightness,self.light_idle=tk.IntVar(value=32),tk.IntVar(value=5)
        ttk.Label(light_card,text='模板',style='Card.TLabel',width=11).grid(row=0,column=0,sticky='w',pady=4)
        ttk.Combobox(light_card,textvariable=self.light_template,values=tuple(LIGHT_NAMES.values()),state='readonly',width=16).grid(row=0,column=1,sticky='w',padx=8)
        self.color_button=tk.Button(light_card,text='选择颜色',command=self.choose_color,relief='flat',padx=10)
        self.color_button.grid(row=0,column=2,padx=8)
        ttk.Entry(light_card,textvariable=self.light_color,width=10).grid(row=0,column=3,sticky='w')
        ttk.Label(light_card,text='亮度',style='Card.TLabel').grid(row=1,column=0,sticky='w',pady=8)
        ttk.Spinbox(light_card,from_=0,to=100,textvariable=self.light_brightness,width=7).grid(row=1,column=1,sticky='w',padx=8)
        ttk.Label(light_card,text='0～100',style='Card.TLabel').grid(row=1,column=2,sticky='w',padx=8)
        ttk.Label(light_card,text='空闲熄灯',style='Card.TLabel').grid(row=2,column=0,sticky='w',pady=4)
        ttk.Spinbox(light_card,from_=0,to=60,textvariable=self.light_idle,width=7).grid(row=2,column=1,sticky='w',padx=8)
        ttk.Label(light_card,text='秒；0 表示保持亮起',style='Card.TLabel').grid(row=2,column=2,columnspan=2,sticky='w',padx=8)
        ttk.Label(light_page,text='固定色和呼吸使用所选颜色，彩虹自动变色。\n按键反馈：麦克风红色，加减绿色。',foreground='#68778b',wraplength=440).pack(anchor='w',pady=(10,0))
        ttk.Label(light_page,text='空闲只熄灯，设备继续待机。按键会重新点亮；0 秒表示常亮。',foreground='#68778b',wraplength=450).pack(anchor='w',pady=(8,0))
        for value in (self.light_template,self.light_color,self.light_brightness,self.light_idle):
            value.trace_add('write',self.mark_dirty)
        self.autostart = tk.BooleanVar(value=startup.is_enabled())
        ttk.Button(keys_page,text='检查 / 安装虚拟麦克风',command=self.audio_setup).pack(anchor='w',pady=(12,0))
        ttk.Checkbutton(keys_page, text='登录 Windows 后自动后台接收语音', variable=self.autostart,
                        command=self.change_startup).pack(anchor='w', pady=(15,0))
        ttk.Label(keys_page, text='快捷键走蓝牙，设备语音走 Wi-Fi。＋／－同时短按发送组合键，按住 3 秒配对。', foreground='#68778b', wraplength=450).pack(anchor='w', pady=(12, 0))
        ttk.Label(keys_page, text='电源键按住 2 秒再松开关机。空闲熄灯后仍可直接按键使用。', foreground='#68778b', wraplength=450).pack(anchor='w', pady=(8, 0))
        ttk.Label(keys_page, text='固件 0.13：按住说话时，接收端未连接会改发蓝牙右 Alt；松手后再按，才重新选择通道。备用录音需微信选中电脑麦克风。', foreground='#68778b', wraplength=450).pack(anchor='w', pady=(8, 0))
        try:
            value = load_settings()
        except (OSError, ValueError) as exc:
            value = deepcopy(DEFAULTS)
            messagebox.showerror('设置读取失败', str(exc), parent=root)
        self.fill(value)
        self.saved_revision = revision(value)
        self.saved_device_revision = packet_revision(config_packet(value, 2))
        self.saved_options_revision = packet_revision(options_packet(value['device']))
        root.after(100, self.poll)

    def fill(self, value, dirty=False):
        self.loading = True
        for name, (shortcut, mode) in self.bindings.items():
            shortcut.set(value['buttons'][name]['shortcut'])
            mode.set(MODE_NAMES[value['buttons'][name]['mode']])
        self.gain.set(value['gain'])
        sounds,lights=value['device']['sounds'],value['device']['lighting']
        for name,variable in self.sound_modes.items(): variable.set(SOUND_NAMES[sounds[name]])
        self.sound_volume.set(sounds['volume'])
        self.light_template.set(LIGHT_NAMES[lights['template']])
        self.light_color.set(lights['color'])
        self.light_brightness.set(lights['brightness'])
        self.light_idle.set(lights['idle_seconds'])
        self.color_button.configure(bg=lights['color'])
        self.loading = False
        self.dirty = dirty
        if dirty:
            self.save_state.set('默认值待保存')

    def mark_dirty(self, *_):
        if not self.loading:
            self.dirty = True
            self.save_state.set('有未保存的修改')

    def record(self, name):
        if self.status_data.get('recording'):
            messagebox.showinfo('请先松开麦克风键', '语音结束后再录入快捷键。', parent=self.root)
            return
        ShortcutDialog(self.root, self.bindings[name][0].set)

    def choose_color(self):
        current=self.light_color.get()
        try: color=colorchooser.askcolor(color=current,parent=self.root,title='选择灯光颜色')[1]
        except tk.TclError: color=colorchooser.askcolor(parent=self.root,title='选择灯光颜色')[1]
        if color:
            self.light_color.set(color.upper());self.color_button.configure(bg=color)

    def device_value(self):
        return {'sounds':{**{name:{v:k for k,v in SOUND_NAMES.items()}[var.get()] for name,var in self.sound_modes.items()},'volume':self.sound_volume.get()},
                'lighting':{'template':{v:k for k,v in LIGHT_NAMES.items()}[self.light_template.get()],
                            'color':self.light_color.get(),'brightness':self.light_brightness.get(),'idle_seconds':self.light_idle.get()}}

    def audio_setup(self):
        subprocess.Popen(launch_command('--audio-setup'),creationflags=subprocess.CREATE_NO_WINDOW)

    def preview_sound(self,name):
        if active_id()!=self.identity:
            messagebox.showinfo('设备已切换','请关闭控制台后重开。',parent=self.root);return
        if not self.is_running() or self.status_data.get('state')!='connected' or self.status_data.get('firmware') not in ('0.8.0','0.9.0','0.10.0','0.11.0','0.12.0','0.13.0'):
            messagebox.showinfo('请先连接设备','试听需要固件 0.8 或更新版和正在运行的语音接收端。',parent=self.root);return
        if self.status_data.get('recording'):
            messagebox.showinfo('请先松开麦克风键','录音期间不播放提示音。',parent=self.root);return
        try:
            device=self.device_value()
            value=validate_settings({'version':2,'device':device})['device']
            if value['sounds'][name]=='custom' and not self.status_data.get('custom_assets',{}).get(name,{}).get('available'):
                raise ValueError('请先为这个开/关机声音导入音效并等待上传完成')
            self.preview_id=secrets.randbits(32)
            self.preview_started=time.time()
            atomic_json(STATUS_PATH.with_name('preview-request.json'),{'device_id':self.identity,'request_id':self.preview_id,'created':time.time(),
                        'event':name,'tone':value['sounds'][name],'volume':value['sounds']['volume']})
            self.preview_state.set('等待设备试听…')
        except (ValueError,OSError,KeyError,tk.TclError) as exc:
            messagebox.showerror('无法试听',str(exc),parent=self.root)

    def import_audio(self,name):
        if active_id()!=self.identity:
            messagebox.showinfo('设备已切换','请关闭控制台后重开。',parent=self.root);return
        if self.importing or (self.upload_id is not None and self.upload_handled!=self.upload_id):
            messagebox.showinfo('正在处理','请等当前音效上传完成后再导入。',parent=self.root);return
        if not self.is_running() or self.status_data.get('state')!='connected' or self.status_data.get('firmware') not in ('0.9.0','0.10.0','0.11.0','0.12.0','0.13.0'):
            messagebox.showinfo('请先连接设备','自定义文件需要固件 0.9 和正在运行的接收端。',parent=self.root);return
        if self.status_data.get('recording'):
            messagebox.showinfo('请先结束录音','请松开麦克风键后导入。',parent=self.root);return
        filename=filedialog.askopenfilename(parent=self.root,title='选择开机音效' if name=='startup' else '选择关机音效',
                    filetypes=[('音频文件','*.wav *.mp3 *.m4a *.aac *.ogg *.flac'),('所有文件','*.*')])
        if not filename:return
        self.importing=True;self.upload_state.set('正在转换音频…')
        def worker():
            try:
                data,meta=convert_audio(filename)
                store_asset(SETTINGS_PATH.parent/'audio',data,meta)
                self.import_events.put(('ready',name,meta))
            except Exception as exc:self.import_events.put(('error',name,str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def poll_imports(self):
        try:
            while True:
                kind,event,value=self.import_events.get_nowait();self.importing=False
                if active_id()!=self.identity:
                    self.upload_state.set('设备已切换，未向其他设备上传；请关闭窗口后重开。');continue
                if kind=='error':
                    self.upload_state.set('导入失败');messagebox.showerror('导入失败',value,parent=self.root);continue
                self.upload_id=secrets.randbits(32);self.upload_event=event
                request={**value,'device_id':self.identity,'request_id':self.upload_id,'event':event,'created':time.time()}
                try:
                    atomic_json(STATUS_PATH.with_name('audio-upload-request.json'),request)
                    self.upload_state.set(f'等待上传 {value["name"]}（{value["seconds"]:.2f} 秒）'+('，已截取开头 6 秒' if value['trimmed'] else ''))
                except OSError as exc:
                    self.upload_handled=self.upload_id;messagebox.showerror('无法上传',str(exc),parent=self.root)
        except queue.Empty:pass

    def save(self):
        if active_id()!=self.identity:
            messagebox.showinfo('设备已切换','请关闭控制台后重开，未保存到另一台设备。',parent=self.root);return
        try:
            value = {'version': 2, 'gain': self.gain.get(), 'buttons': {},'device':self.device_value()}
            for name in ('startup','shutdown'):
                if value['device']['sounds'][name]=='custom' and not self.status_data.get('custom_assets',{}).get(name,{}).get('available'):
                    raise ValueError('请先为对应的开/关机声音导入文件并等上传完成')
            for name, (shortcut, mode) in self.bindings.items():
                value['buttons'][name] = {'shortcut': shortcut.get(), 'mode': {v:k for k,v in MODE_NAMES.items()}[mode.get()]}
            value = save_settings(value)
        except (ValueError, OSError, tk.TclError) as exc:
            messagebox.showerror('未保存', str(exc), parent=self.root)
            return
        self.fill(value)
        self.saved_revision = revision(value)
        self.saved_device_revision = packet_revision(config_packet(value, 2))
        self.saved_options_revision = packet_revision(options_packet(value['device']))
        self.save_state.set('已保存，等待生效')

    def change_startup(self):
        try:
            startup.set_enabled(self.autostart.get())
        except OSError as exc:
            self.autostart.set(startup.is_enabled())
            messagebox.showerror('登录启动未修改', str(exc), parent=self.root)

    def start_receiver(self):
        if active_id()!=self.identity:
            messagebox.showinfo('设备已切换','请关闭控制台后重开。',parent=self.root);return
        if self.is_running():
            return
        if not CONFIG_PATH.exists():
            messagebox.showinfo('请先配网', '请先在安装器中选择设备并完成 USB 配网。', parent=self.root)
            return
        STOP_PATH.unlink(missing_ok=True)
        log_path = DATA_ROOT/'logs/receiver-panel.txt'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        try:
            with log_path.open('a', encoding='utf-8') as log:
                log.write(f'\n--- Receiver start {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n')
                log.flush()
                self.child = subprocess.Popen(launch_command('--receiver','--stop-file',STOP_PATH), cwd=ROOT, env=env,
                                              creationflags=subprocess.CREATE_NO_WINDOW,
                                              stdout=log, stderr=subprocess.STDOUT)
            self.connection.set('正在启动接收端…')
        except OSError as exc:
            messagebox.showerror('启动失败', str(exc), parent=self.root)

    def stop_receiver(self):
        STOP_PATH.parent.mkdir(parents=True, exist_ok=True)
        STOP_PATH.write_text('stop\n', encoding='ascii')
        self.connection.set('正在停止并释放按键…')

    def is_running(self):
        if self.status_data.get('device_id') not in (None,self.identity):return False
        return time.time()-self.status_data.get('updated', 0) < 15 and self.status_data.get('state') not in ('stopped', None)

    def poll(self):
        self.poll_imports()
        try:
            data = json.loads(STATUS_PATH.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                self.status_data = data
        except (OSError, ValueError):
            pass
        running = self.is_running()
        data = self.status_data
        state = data.get('state') if running else 'stopped'
        names = {'connected': '● 设备已连接', 'connecting': '正在连接设备…',
                 'reconnecting': '连接中断，正在重连…', 'stopped': '接收端已停止'}
        self.connection.set(('● 正在准备录音…' if data.get('recording_stage')=='preparing' else '● 正在录音') if running and data.get('recording') else names.get(state, '正在连接…'))
        version = data.get('firmware')
        fresh = 0 <= time.time()-data.get('battery_updated',0) < 15
        self.battery.set(battery_text(data.get('battery',{}),state=='connected',fresh))
        if state=='connected' and version and version not in ('0.10.0','0.11.0','0.12.0','0.13.0'):
            self.battery.set('电量显示需要升级设备固件')
        self.battery_label.configure(foreground='#c2410c' if state=='connected' and fresh and data.get('battery',{}).get('low') else '#718096')
        self.firmware.set((f'固件 {version} · 蓝牙已连接' if data.get('ble_connected') else f'固件 {version} · 请同时按住＋／－3 秒，再配对 Gokit5 Keys-xxxxxx') if running and version in ('0.5.0','0.7.0','0.8.0','0.9.0','0.10.0','0.11.0','0.12.0','0.13.0') else
                          f'固件 {version} · 快捷键仍依赖电脑' if running and version == '0.4.0' else
                          '旧版固件：加减键需升级' if state == 'connected' else '同一局域网内自动连接')
        if running and data.get('audio_available') is False:
            self.firmware.set('虚拟麦克风未就绪：请打开音频环境向导')
        event = data.get('last_event', '')
        if event:
            name, _, action = event.partition(': ')
            self.last_event.set(f'最近按键：{BUTTON_NAMES.get(name, name)} · {"按下" if action == "DOWN" else "松开"}')
        self.meter['value'] = data.get('level', 0) if running else 0
        self.start_button.configure(state='disabled' if running or (self.child and self.child.poll() is None) else 'normal')
        self.stop_button.configure(state='normal' if running or (self.child and self.child.poll() is None) else 'disabled')
        if self.preview_id is not None and data.get('preview_request_id')==self.preview_id:
            self.preview_state.set(data.get('preview_result',''))
        if self.preview_id is not None and time.time()-self.preview_started>15 and self.preview_state.get() in ('等待设备试听…','正在试听…'):
            self.preview_state.set('设备未响应，请确认连接后重新试听。')
        for name,variable in self.asset_labels.items():
            asset=data.get('custom_assets',{}).get(name,{})
            variable.set(f'设备文件 {asset.get("bytes",0)/32000:.2f} 秒' if asset.get('available') else '未上传自定义文件')
        if self.upload_id is not None and data.get('upload_request_id')==self.upload_id:
            self.upload_state.set(f'{data.get("upload_result","")} {data.get("upload_progress",0)}%')
            if data.get('upload_done') and self.upload_handled!=self.upload_id:
                self.upload_handled=self.upload_id
                if data.get('upload_result')=='上传完成，请保存启用':
                    self.sound_modes[self.upload_event].set(SOUND_NAMES['custom'])
                    self.upload_state.set('上传完成，已选自定义文件；请点击“保存并应用”启用。')
        if not self.dirty:
            applied = running and data.get('settings_revision') == self.saved_revision
            device_applied = data.get('device_revision') == self.saved_device_revision
            options_applied=data.get('options_revision')==self.saved_options_revision
            self.save_state.set('已同步到设备' if applied and version in ('0.8.0','0.9.0','0.10.0','0.11.0','0.12.0','0.13.0') and device_applied and options_applied else
                                '提示音和灯光需升级至 0.8' if applied and version=='0.7.0' and device_applied else
                                '组合键需要升级固件' if applied and version=='0.5.0' else
                                '已保存，等待设备同步' if applied and version in ('0.7.0','0.8.0','0.9.0','0.10.0','0.11.0','0.12.0','0.13.0') else
                                '设置已生效' if applied else '已保存，待连接/松键' if running else '已保存，启动接收后同步')
        if data.get('settings_error') and running:
            self.save_state.set('配置有误，仍使用上次设置')
        if self.child and self.child.poll() is not None:
            result = self.child.returncode
            self.child = None
            if result:
                messagebox.showerror('接收端启动失败', '请检查是否有旧接收程序正在运行；详细信息见 logs/receiver-panel.txt。', parent=self.root)
        self.root.after(350, self.poll)


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        instance = SingleInstance('Local\\Gokit5Panel-Community')
    except RuntimeError:
        messagebox.showinfo('Gokit5', '控制台已经打开，请切换到已有窗口。', parent=root)
        root.destroy()
        return
    try:
        ControlPanel(root)
        root.deiconify()
        root.mainloop()
    finally:
        instance.close()


if __name__ == '__main__':
    main()
