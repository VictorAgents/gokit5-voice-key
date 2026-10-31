# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows-first, backup-before-flash installation and recovery UI."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from serial.tools import list_ports
from flasher import Flasher, Runner, InstallError
from runtime import DATA_ROOT, CODE_ROOT, VERSION, launch_command, set_active, active_id, install_runtime


class Installer:
    def __init__(self, root):
        self.root, self.busy = root, False
        self.events = queue.Queue()
        root.title('Gokit5 Community 安装器 · 测试版')
        root.geometry('780x760')
        root.minsize(740, 700)
        main = ttk.Frame(root, padding=24)
        main.pack(fill='both', expand=True)
        ttk.Label(main,text='Gokit5 Community',font=('Microsoft YaHei UI',22,'bold')).pack(anchor='w')
        ttk.Label(main,text=f'{VERSION} · 仅限已确认的四键 Wi-Fi 版 · 未验证的测试版').pack(anchor='w',pady=(6,18))
        ttk.Label(main,text='准备：USB数据线直连；一次只接一台；关闭可能占用串口的程序。',wraplength=720).pack(anchor='w')
        line=ttk.Frame(main);line.pack(fill='x',pady=15)
        ttk.Label(line,text='目标USB串口').pack(side='left')
        self.port=tk.StringVar()
        self.ports=ttk.Combobox(line,textvariable=self.port,state='readonly',width=18)
        self.ports.pack(side='left',padx=12)
        self.refresh_button=ttk.Button(line,text='刷新',command=self.refresh);self.refresh_button.pack(side='left')
        self.board=tk.BooleanVar(value=False)
        self.backup=tk.BooleanVar(value=False)
        ttk.Checkbutton(main,text='我确认是同款四键板：电源、麦克风、＋、－；并非仅凭芯片名判断',variable=self.board).pack(anchor='w',pady=4)
        ttk.Checkbutton(main,text='我理解原厂功能会被替换；保留个人备份，恢复也要在原设备上验证',variable=self.backup).pack(anchor='w',pady=4)
        ttk.Label(main,text='流程：安全检查 → 双份完整备份校验 → 刷入 → 安装电脑端 → 本机配网。\n电脑端安装到当前用户数据目录。备份和凭据不上传，首次蓝牙配对需在Windows完成。',wraplength=710).pack(anchor='w',pady=(12,15))
        self.flash=ttk.Button(main,text='一键备份并刷入',command=self.install)
        self.flash.pack(anchor='w',ipadx=14,ipady=8)
        self.update_button=ttk.Button(main,text='已有本项目固件：备份并更新（保留配网、按键与音效）',command=lambda:self.install(True))
        self.update_button.pack(anchor='w',pady=(6,0))
        self.progress=ttk.Progressbar(main,mode='indeterminate');self.progress.pack(fill='x',pady=(18,10))
        self.status=tk.StringVar(value='请选择实际USB串口；尚未对任何设备执行操作。')
        ttk.Label(main,textvariable=self.status,wraplength=720).pack(anchor='w')
        self.log=tk.Text(main,height=6,wrap='word',state='disabled')
        self.log.pack(fill='both',expand=True,pady=12)
        tools=ttk.Frame(main);tools.pack(fill='x')
        self.restore_button=ttk.Button(tools,text='从本机备份恢复',command=self.restore)
        self.restore_button.pack(side='left')
        self.wifi=ttk.Button(tools,text='USB配网',command=lambda:self.launch('--wifi'));self.wifi.pack(side='left',padx=10)
        self.panel=ttk.Button(tools,text='打开控制台',command=lambda:self.launch('--panel'));self.panel.pack(side='left')
        ttk.Button(tools,text='语音环境检测 / 安装',command=self.audio_setup).pack(side='left',padx=10)
        self.audio_status=tk.StringVar(value='语音环境：正在检测 CABLE Input / Output…')
        ttk.Label(main,textvariable=self.audio_status,wraplength=710).pack(anchor='w',pady=(5,0))
        select=ttk.Frame(main);select.pack(fill='x',pady=(12,0))
        ttk.Label(select,text='本机已安装设备').pack(side='left')
        self.saved=tk.StringVar(value=active_id() or '')
        self.saved_list=ttk.Combobox(select,textvariable=self.saved,state='readonly',width=18)
        self.saved_list.pack(side='left',padx=10)
        ttk.Button(select,text='选择此设备',command=self.select_device).pack(side='left')
        ttk.Button(select,text='本机数据位置',command=lambda:os.startfile(DATA_ROOT)).pack(side='left',padx=10)
        ttk.Label(main,text='GPL-3.0-or-later · 无担保 · 非官方项目。源码、许可和已知限制见随包文档。',wraplength=710).pack(anchor='w',pady=(12,0))
        self.refresh()
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.after(150,self.poll)
        self.check_audio()

    def audio_setup(self):
        if not self.busy:subprocess.Popen(launch_command('--audio-setup'),creationflags=subprocess.CREATE_NO_WINDOW)

    def check_audio(self):
        def worker():
            try:
                from cable_setup import fresh_probe
                result=fresh_probe()
                text={'ready':'语音环境：已就绪（CABLE Input / Output）',
                      'missing':'语音环境：未安装。点击“语音环境检测 / 安装”自动准备。',
                      'not_ready':'语音环境：驱动未就绪。安装后先重启，再点击检测。',
                      'error':'语音环境：检测失败。请打开环境向导检查。'}[result['state']]
            except Exception:text='语音环境：暂时无法检测。请打开环境向导。'
            self.events.put(('audio',text))
        threading.Thread(target=worker,daemon=True).start()

    def refresh(self):
        candidates=[p.device for p in list_ports.comports() if p.vid==0x303A and p.pid==0x1001]
        self.ports['values']=candidates
        if self.port.get() not in candidates: self.port.set(candidates[0] if len(candidates)==1 else '')
        devices=sorted(p.name for p in (DATA_ROOT/'devices').glob('*') if (p/'device.json').is_file())
        self.saved_list['values']=devices
        if not self.saved.get() and active_id() in devices:self.saved.set(active_id())

    def select_device(self):
        if self.busy: return
        if not self.saved.get(): return
        from receiver import SingleInstance
        try:check=SingleInstance()
        except RuntimeError:
            messagebox.showinfo('请先停止接收','停止当前接收端并关闭控制台后，再切换设备。',parent=self.root);return
        check.close()
        set_active(self.saved.get())
        self.status.set('已选择设备；请停止旧接收端，并关闭旧控制台后重新打开。同一时刻接收一台。')

    def launch(self,action):
        if self.busy: return
        if not active_id():
            messagebox.showinfo('未选择设备','请先完成安装，或从已安装设备中选择。',parent=self.root);return
        subprocess.Popen(launch_command(action),creationflags=subprocess.CREATE_NO_WINDOW)

    def start(self,work):
        self.busy=True;self.flash['state']='disabled';self.update_button['state']='disabled';self.restore_button['state']='disabled';self.refresh_button['state']='disabled'
        self.progress.start()
        def worker():
            try: work()
            except Exception as exc:
                self.events.put(('error',str(exc) if isinstance(exc,InstallError) else '操作失败，备份已保留。请检查本机日志后重试。'))
        threading.Thread(target=worker,daemon=True).start()

    def install(self,application_only=False):
        if not self.port.get() or not self.board.get() or not self.backup.get():
            messagebox.showinfo('尚未开始','请选串口，并确认板型和备份说明。',parent=self.root);return
        port=self.port.get()
        self.start(lambda:self.flash_worker(port,application_only))

    def flash_worker(self,port,application_only=False):
        runner=Runner(lambda text:self.events.put(('line',text)))
        device,backup=Flasher(runner).install(port,True,application_only=application_only)
        install_runtime()
        set_active(device.device_id)
        next_step='已保留配网和设备设置，可打开控制台。' if application_only else '下一步点击USB配网。'
        self.events.put(('done',f'刷入完成。设备 {device.device_id}；备份 {backup}。{next_step}'))
        self.events.put(('audio_prompt',''))

    def restore(self):
        if not self.port.get(): return
        path=filedialog.askopenfilename(title='选择本机backup.json清单',initialdir=DATA_ROOT/'backups',filetypes=[('备份清单','*.json')])
        if not path: return
        if not messagebox.askyesno('恢复原设备','将校验备份与当前芯片身份，并完整写回备份。恢复效果仍需实机验证。继续？',parent=self.root): return
        port=self.port.get()
        def work():
            Flasher(Runner(lambda line:self.events.put(('line',line)))).restore(port,path)
            self.events.put(('done','安装前的完整快照已写回，请验证备份时程序的启动、按键与联网；原厂服务需另行核验。'))
        self.start(work)

    def poll(self):
        while not self.events.empty():
            kind,text=self.events.get_nowait()
            if kind=='audio':self.audio_status.set(text);continue
            if kind=='audio_prompt':self.audio_setup();continue
            self.status.set(text)
            self.log.configure(state='normal');self.log.insert('end',text+'\n');self.log.see('end');self.log.configure(state='disabled')
            if kind in ('done','error'):
                self.busy=False;self.progress.stop();self.flash['state']='normal';self.update_button['state']='normal';self.restore_button['state']='normal';self.refresh_button['state']='normal';self.refresh()
        self.root.after(150,self.poll)

    def close(self):
        if self.busy:
            self.status.set('正在备份或刷写，请等待完成；本窗口不会强制结束写入。')
        else:self.root.destroy()


def main():
    DATA_ROOT.mkdir(parents=True,exist_ok=True)
    from receiver import SingleInstance
    root=tk.Tk();root.withdraw()
    try:instance=SingleInstance('Local\\Gokit5CommunityInstaller')
    except RuntimeError:
        messagebox.showinfo('安装器已打开','请使用已有安装器，避免重复操作同一设备。',parent=root);root.destroy();return
    try:Installer(root);root.deiconify();root.mainloop()
    finally:instance.close()


if __name__=='__main__':main()
