# SPDX-License-Identifier: GPL-3.0-or-later
"""Local prompt conversion and resumeless, acknowledged device upload."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import wave
import warnings

RATE=16000
MAX_SECONDS=6
MAX_BYTES=RATE*2*MAX_SECONDS
CHUNK=480
EVENTS={'startup':0,'shutdown':1}


def convert_audio(path):
    path=Path(path)
    if not path.is_file() or path.stat().st_size>50*1024*1024:
        raise ValueError('请选择小于 50 MB 的音频文件')
    ffmpeg=shutil.which('ffmpeg')
    if ffmpeg:
        args=[ffmpeg,'-nostdin','-v','error','-protocol_whitelist','file,pipe','-i',str(path.resolve()),
              '-map','0:a:0','-t',str(MAX_SECONDS+.001),'-ac','1','-ar',str(RATE),'-f','s16le','pipe:1']
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            raise ValueError('无法解码该音频，请换用 WAV 或 MP3 文件')
        raw=result.stdout
    else:
        if path.suffix.lower()!='.wav':
            raise ValueError('当前环境支持 PCM WAV；MP3/M4A 等格式需安装 FFmpeg')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',DeprecationWarning)
            import audioop
        try:
            with wave.open(str(path),'rb') as source:
                channels,width,rate=source.getnchannels(),source.getsampwidth(),source.getframerate()
                if channels not in (1,2) or width not in (1,2,3,4) or not 8000<=rate<=192000:
                    raise ValueError('WAV 需为单/双声道 PCM，采样率 8～192 kHz')
                raw=source.readframes(int(rate*(MAX_SECONDS+.001))+1)
                if width==1:raw=audioop.bias(raw,1,-128)
                if channels==2:raw=audioop.tomono(raw,width,.5,.5)
                raw=audioop.lin2lin(raw,width,2)
                raw=audioop.ratecv(raw,2,1,rate,RATE,None)[0]
        except (wave.Error,EOFError) as exc:
            raise ValueError('WAV 文件不支持或已损坏') from exc
    if not raw or len(raw)%2:
        raise ValueError('文件没有有效音频')
    trimmed=len(raw)>MAX_BYTES
    raw=raw[:MAX_BYTES]
    import numpy as np
    samples=np.frombuffer(raw,dtype='<i2').astype(np.float64)
    peak=float(np.max(np.abs(samples)))
    if peak>7000:samples*=7000/peak
    fade=min(160,len(samples)//2)
    if fade:
        ramp=np.linspace(0,1,fade)
        samples[:fade]*=ramp;samples[-fade:]*=ramp[::-1]
    data=np.rint(samples).astype('<i2').tobytes()
    return data,{'name':path.name,'bytes':len(data),'seconds':round(len(data)/(RATE*2),3),
                 'sha256':hashlib.sha256(data).hexdigest(),'trimmed':trimmed}


def store_asset(folder,data,metadata):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    filename=metadata['sha256']+'.pcm'
    destination=folder/filename
    if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest()!=metadata['sha256']:
        temp=destination.with_suffix('.tmp');temp.write_bytes(data);temp.replace(destination)
    return filename


class AssetUpload:
    def __init__(self,request,folder):
        self.nonce=request['request_id'];self.event=request['event']
        if type(self.nonce)!=int or not 0<=self.nonce<=0xffffffff or self.event not in EVENTS:
            raise ValueError('上传请求不正确')
        digest=request['sha256']
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('音效校验值不正确')
        self.data=(Path(folder)/(digest+'.pcm')).read_bytes()
        if not self.data or len(self.data)>MAX_BYTES or len(self.data)%2 or hashlib.sha256(self.data).hexdigest()!=digest:
            raise ValueError('音效文件已变更或超出限制，请重新导入')
        self.digest=bytes.fromhex(digest);self.offset=0;self.expected=0
        self.ending=False;self.done=False;self.error=''

    def begin(self):
        return 20,bytes([1,EVENTS[self.event]])+struct.pack('!II',self.nonce,len(self.data))+self.digest

    def ack(self,payload):
        nonce,status,offset=struct.unpack('!IBI',payload)
        if nonce!=self.nonce:return None
        if status>=2:
            self.done=True
            self.error={2:'设备正忙，请松开按键后重新导入',3:'上传格式不正确',4:'文件校验失败，原音效已保留',
                        5:'设备写入失败，原音效已保留',6:'上传中断，原音效已保留'}.get(status,'上传失败')
            return None
        if offset!=self.expected or (status==1)!=self.ending:
            self.done=True;self.error='上传回执不匹配，请重新导入';return None
        self.offset=offset
        if status==1:
            self.done=True;return None
        if self.offset==len(self.data):
            self.ending=True;return 22,struct.pack('!I',self.nonce)
        chunk=self.data[self.offset:self.offset+CHUNK]
        self.expected=self.offset+len(chunk)
        return 21,struct.pack('!II',self.nonce,self.offset)+chunk


def decode_assets(payload):
    if len(payload)!=75 or payload[0]!=1:raise ValueError('设备音效清单不正确')
    result={}
    for event,i in EVENTS.items():
        start=1+i*37;valid=payload[start];size=int.from_bytes(payload[start+1:start+5],'big')
        if valid not in (0,1) or size>MAX_BYTES or size%2:raise ValueError('设备音效清单不正确')
        result[event]={'available':bool(valid),'bytes':size,'sha256':payload[start+5:start+37].hex() if valid else ''}
    return result
