# SPDX-License-Identifier: GPL-3.0-or-later
"""VB-CABLE prerequisite wizard. Downloads only the pinned original vendor ZIP.

No driver, private device data, security changes or reboot commands are bundled.
The official visible installer owns installation and Windows owns UAC approval.
"""
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import subprocess
import sys
import tempfile
import threading
import urllib.request
import webbrowser
import zipfile

PAGE='https://vb-audio.com/Cable/'
TERMS='https://vb-audio.com/Services/licensing.htm'
URL='https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip'
SHA256='b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb'
MAX_DOWNLOAD=4*1024*1024


def driver_registered():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SYSTEM\CurrentControlSet\Services\VBAudioVACMME'):return True
    except FileNotFoundError:return False


def probe_cable(sd=None):
    if sd is None:import sounddevice as sd
    try:
        hosts=sd.query_hostapis();devices=sd.query_devices()
        def endpoint(prefix,direction):
            return next((i for i,d in enumerate(devices)
                         if d['name'].startswith(prefix+' (') and 'VB-Audio Virtual Cable' in d['name']
                         and hosts[d['hostapi']]['name']=='Windows WASAPI'
                         and d['max_'+direction+'_channels']>=2),None)
        output=endpoint('CABLE Input','output');capture=endpoint('CABLE Output','input')
        result={'state':'missing','playback':output is not None,'capture':capture is not None,
                'registered':driver_registered(),'detail':''}
        if output is not None and capture is not None:
            sd.check_output_settings(device=output,samplerate=48000,channels=2,dtype='int16')
            sd.check_input_settings(device=capture,samplerate=48000,channels=2,dtype='int16')
            result['state']='ready'
        elif result['registered'] or result['playback'] or result['capture']:result['state']='not_ready'
        return result
    except Exception:
        # A broken audio service is not evidence that the driver is absent.
        return {'state':'error','playback':False,'capture':False,'detail':'音频检测失败，请检查 Windows 音频服务后重新检测。'}


def probe_command():
    if getattr(sys,'frozen',False):return [sys.executable,'--cable-probe']
    exe=Path(sys.executable);console=exe.with_name('python.exe')
    if exe.name.lower()=='pythonw.exe' and console.exists():exe=console
    return [str(exe),str(Path(__file__).resolve()),'--cable-probe']


def fresh_probe():
    # PortAudio caches the endpoint list. A new process sees a newly installed driver.
    result=subprocess.run(probe_command(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('音频检查进程失败，请重新检测。')
    return json.loads(result.stdout.decode('utf-8'))


class VendorRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        from urllib.parse import urlsplit
        target=urlsplit(newurl)
        if target.scheme!='https' or target.hostname!='download.vb-audio.com':
            raise RuntimeError('官网重定向到未经核对的地址，已停止下载。')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def verify_archive(path):
    path=Path(path)
    if path.stat().st_size>MAX_DOWNLOAD or hashlib.sha256(path.read_bytes()).hexdigest()!=SHA256:
        raise RuntimeError('安装包校验失败，未运行任何程序；请重新下载。')


def download_package(cache,progress=lambda message:None):
    cache=Path(cache).resolve();cache.mkdir(parents=True,exist_ok=True)
    archive=cache/'VBCABLE_Driver_Pack45.zip'
    if archive.exists():
        try:verify_archive(archive);return archive
        except RuntimeError:pass
    part=cache/'VBCABLE_Driver_Pack45.zip.part'
    try:
        opener=urllib.request.build_opener(VendorRedirects())
        with opener.open(URL,timeout=30) as response,part.open('wb') as output:
            total=0
            while block:=response.read(65536):
                total+=len(block)
                if total>MAX_DOWNLOAD:raise RuntimeError('下载文件大小异常，已停止。')
                output.write(block);progress(f'正在从 VB-Audio 官网下载… {total//1024} KB')
        verify_archive(part);os.replace(part,archive);return archive
    finally:part.unlink(missing_ok=True)


def extract_package(archive,cache):
    verify_archive(archive)
    cache=Path(cache).resolve()
    with zipfile.ZipFile(archive) as package:
        infos=package.infolist()
        if len(infos)>80 or sum(i.file_size for i in infos)>12*1024*1024:raise RuntimeError('安装包结构异常。')
        for info in infos:
            name=PurePosixPath(info.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename or ':' in info.filename or (info.external_attr>>16)&0o170000==0o120000:
                raise RuntimeError('安装包含不安全路径。')
        folder=Path(tempfile.mkdtemp(prefix='vendor-',dir=cache)).resolve()
        if not folder.is_relative_to(cache):raise RuntimeError('解压路径超出缓存目录。')
        package.extractall(folder)
    required=['VBCABLE_Setup_x64.exe','VBCABLE_Setup.exe','readme.txt']
    if not all((folder/n).is_file() for n in required):raise RuntimeError('官方安装包文件不完整。')
    return folder


def verify_signature(path):
    class GUID(ctypes.Structure):
        _fields_=[('Data1',w.DWORD),('Data2',w.WORD),('Data3',w.WORD),('Data4',ctypes.c_ubyte*8)]
    class FileInfo(ctypes.Structure):
        _fields_=[('cbStruct',w.DWORD),('pcwszFilePath',w.LPCWSTR),('hFile',w.HANDLE),('pgKnownSubject',ctypes.c_void_p)]
    class TrustData(ctypes.Structure):
        _fields_=[('cbStruct',w.DWORD),('pPolicyCallbackData',ctypes.c_void_p),('pSIPClientData',ctypes.c_void_p),
                  ('dwUIChoice',w.DWORD),('fdwRevocationChecks',w.DWORD),('dwUnionChoice',w.DWORD),
                  ('pFile',ctypes.POINTER(FileInfo)),('dwStateAction',w.DWORD),('hWVTStateData',w.HANDLE),
                  ('pwszURLReference',w.LPCWSTR),('dwProvFlags',w.DWORD),('dwUIContext',w.DWORD)]
    action=GUID(0x00AAC56B,0xCD44,0x11D0,(ctypes.c_ubyte*8)(0x8c,0xc2,0,0xc0,0x4f,0xc2,0x95,0xee))
    file=FileInfo(ctypes.sizeof(FileInfo),str(Path(path).resolve()),None,None)
    data=TrustData();data.cbStruct=ctypes.sizeof(data);data.dwUIChoice=2;data.dwUnionChoice=1;data.pFile=ctypes.pointer(file)
    data.dwProvFlags=0x1000  # Cached certificate retrieval; don't change trust stores.
    trust=ctypes.WinDLL('wintrust').WinVerifyTrust
    trust.argtypes=[w.HWND,ctypes.POINTER(GUID),ctypes.POINTER(TrustData)];trust.restype=ctypes.c_long
    if trust(None,ctypes.byref(action),ctypes.byref(data))!=0:raise RuntimeError('官方安装程序签名未通过验证，已停止。')


def run_vendor_installer(executable):
    # Visible vendor setup, with no undocumented silent switches or UI clicking.
    # Do not elevate the Gokit5 application or install certificates.
    class ExecuteInfo(ctypes.Structure):
        _fields_=[('cbSize',w.DWORD),('fMask',ctypes.c_ulong),('hwnd',w.HWND),('lpVerb',w.LPCWSTR),
                  ('lpFile',w.LPCWSTR),('lpParameters',w.LPCWSTR),('lpDirectory',w.LPCWSTR),('nShow',ctypes.c_int),
                  ('hInstApp',w.HINSTANCE),('lpIDList',ctypes.c_void_p),('lpClass',w.LPCWSTR),('hkeyClass',w.HKEY),
                  ('dwHotKey',w.DWORD),('hIcon',w.HANDLE),('hProcess',w.HANDLE)]
    info=ExecuteInfo();info.cbSize=ctypes.sizeof(info);info.fMask=0x40;info.lpVerb='runas'
    info.lpFile=str(Path(executable).resolve());info.lpDirectory=str(Path(executable).resolve().parent);info.nShow=1
    shell=ctypes.WinDLL('shell32',use_last_error=True);shell.ShellExecuteExW.argtypes=[ctypes.POINTER(ExecuteInfo)];shell.ShellExecuteExW.restype=w.BOOL
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        if ctypes.get_last_error()==1223:raise RuntimeError('已取消管理员确认，未安装驱动。')
        raise ctypes.WinError(ctypes.get_last_error())
    kernel=ctypes.WinDLL('kernel32');kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)];kernel.CloseHandle.argtypes=[w.HANDLE]
    try:
        if kernel.WaitForSingleObject(info.hProcess,600000)!=0:raise RuntimeError('官方安装器仍未结束，请检查其窗口；未强制终止它。')
        code=w.DWORD();kernel.GetExitCodeProcess(info.hProcess,ctypes.byref(code));return code.value
    finally:kernel.CloseHandle(info.hProcess)


def prepare_install(cache,progress=lambda message:None):
    state=fresh_probe()
    if state['state']=='ready':return {'state':'ready','already_installed':True}
    if state['state']!='missing':raise RuntimeError('驱动已有记录或音频检测异常。请先重启并重新检测，避免重复安装。')
    folder=extract_package(download_package(cache,progress),cache)
    executable=folder/('VBCABLE_Setup_x64.exe' if os.environ.get('PROCESSOR_ARCHITEW6432') or os.environ.get('PROCESSOR_ARCHITECTURE','').upper() in ('AMD64','ARM64') else 'VBCABLE_Setup.exe')
    verify_signature(executable)
    # Another installer may have completed while this download was running.
    state=fresh_probe()
    if state['state']=='ready':return {'state':'ready','already_installed':True}
    if state['state']!='missing':raise RuntimeError('音频状态已变化，请先重启并重新检测。')
    # Keep the publisher's notice accessible beside the unmodified installer.
    progress('请在 Windows 管理员确认中选择“是”，再在官方窗口点击 Install Driver；完成后关闭官方安装器。')
    code=run_vendor_installer(executable)
    result=fresh_probe();result['installer_exit_code']=code
    result['reboot_recommended']=True
    # Even code 0 is not proof that both endpoints work. Never reboot automatically.
    return result


def main():
    if '--cable-probe' in sys.argv or '--probe-json' in sys.argv:
        print(json.dumps(probe_cable()));return
    if getattr(sys,'frozen',False):ctypes.WinDLL('kernel32').FreeConsole()
    import tkinter as tk
    from tkinter import ttk
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,w.BOOL,w.LPCWSTR];kernel.CreateMutexW.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    mutex=kernel.CreateMutexW(None,False,'Local\\Gokit5CableSetupWizard')
    if not mutex:raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error()==183:
        from tkinter import messagebox
        existing=tk.Tk();existing.withdraw()
        messagebox.showinfo('向导已打开','请使用已打开的语音环境向导，避免重复安装。',parent=existing)
        existing.destroy();kernel.CloseHandle(mutex);return
    root=tk.Tk();root.title('Gokit5 · 语音环境准备');root.geometry('550x480');root.minsize(520,460)
    events=queue.Queue();busy=[False];state=[{}]
    frame=ttk.Frame(root,padding=22);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='准备无线语音输入',font=('Microsoft YaHei UI',18,'bold')).pack(anchor='w')
    status=tk.StringVar(value='正在检查虚拟麦克风…');detail=tk.StringVar(value='')
    ttk.Label(frame,textvariable=status,font=('Microsoft YaHei UI',12),wraplength=490).pack(anchor='w',pady=(18,8))
    ttk.Label(frame,textvariable=detail,wraplength=490).pack(anchor='w')
    ttk.Label(frame,text='CABLE Input 接收设备声音 → CABLE Output 提供给输入法。\n程序自动检查、下载、校验和解压；安装时只需确认管理员权限并点击 Install Driver。',wraplength=490).pack(anchor='w',pady=12)
    consent=tk.BooleanVar(value=False)
    ttk.Checkbutton(frame,text='我已了解：VB-CABLE 是 VB-Audio 的独立 Donationware，\n安装受其许可条款约束，欢迎捐赠支持作者。',variable=consent,command=lambda:update()).pack(anchor='w')
    links=ttk.Frame(frame);links.pack(anchor='w',pady=8)
    ttk.Button(links,text='官网与捐赠',command=lambda:webbrowser.open(PAGE)).pack(side='left')
    ttk.Button(links,text='许可条款',command=lambda:webbrowser.open(TERMS)).pack(side='left',padx=8)
    buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=10)
    def update():
        install['state']='normal' if not busy[0] and state[0].get('state')=='missing' and consent.get() else 'disabled'
        check['state']='disabled' if busy[0] else 'normal'
    def work(installing=False):
        if busy[0]:return
        busy[0]=True;update();status.set('正在准备官方安装包…' if installing else '正在检测音频端点…')
        def worker():
            try:
                cache=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Gokit5Community/dependencies/vbcable'
                result=prepare_install(cache,lambda message:events.put(('progress',message))) if installing else fresh_probe()
                events.put(('result',result))
            except Exception as exc:events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    check=ttk.Button(buttons,text='重新检测',command=lambda:work());check.pack(side='left')
    install=ttk.Button(buttons,text='自动准备并安装 VB-CABLE',command=lambda:work(True));install.pack(side='left',padx=10)
    ttk.Label(frame,text='安装后请保存工作并重启电脑，再打开本程序复检。\n已安装可用时自动跳过；只用蓝牙快捷键无需安装。',wraplength=490).pack(anchor='w',pady=8)
    def poll():
        while not events.empty():
            kind,value=events.get_nowait()
            if kind=='progress':detail.set(value);continue
            busy[0]=False
            if kind=='error':state[0]={'state':'error'};status.set('尚未完成');detail.set(value)
            else:
                state[0]=value
                status.set({'ready':'✓ 虚拟麦克风已就绪','missing':'尚未安装 VB-CABLE','not_ready':'已检测到驱动，音频端点尚未就绪','error':'暂时无法检查音频设备'}.get(value['state'],'待检查'))
                detail.set('已核对 CABLE Input / Output 与 48 kHz 双声道格式。请在输入法选择 CABLE Output，并做一次说话测试。' if value['state']=='ready' else value.get('detail') or '请安装；若刚安装过，请先重启电脑，再回来重新检测。')
                if value.get('reboot_recommended'):detail.set(detail.get()+' 安装器已结束，仍建议按官方要求重启；程序不会替你重启。')
            update()
        root.after(150,poll)
    def close():
        if busy[0]:detail.set('正在检测或安装，请等待结束后再关闭；不会强制终止官方安装器。')
        else:root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);root.after(150,poll);root.after(200,work)
    try:root.mainloop()
    finally:kernel.CloseHandle(mutex)


if __name__=='__main__':main()
