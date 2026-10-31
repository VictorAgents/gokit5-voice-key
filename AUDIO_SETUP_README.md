# Gokit5 语音环境准备工具（本地审查版）

Windows x64，双击`Gokit5AudioSetup.exe`。无需安装Python或解压驱动包；此工具只准备电脑音频环境，不含固件、不刷设备。

1. 自动检查CABLE Input播放端、CABLE Output录音端和48kHz双声道格式。已就绪直接通过。
2. 未安装时，阅读独立VB-CABLE说明并勾选确认，点击“自动准备并安装VB-CABLE”。程序从官网下载Pack45，验证SHA256和Windows安装器签名并解压。
3. Windows管理员确认中点“是”，在VB-Audio官方窗口点Install Driver，结束后关闭官方安装器。程序不使用未经验证的静默参数。
4. 按官方要求保存工作、重启电脑，重开本工具检测。工具不会自动重启、卸载、导入证书或改默认音频设备。
5. 输入法麦克风需选到CABLE Output；语音转写单独实测。“已就绪”只确认驱动端点及格式，不表示输入法已选对设备。

如果检测到驱动但端点不全，先重启并复检；避免直接重复安装。纯蓝牙快捷键不需要VB-CABLE。下载需要联网与管理员安装权限，已有可用驱动时无需联网下载。

VB-CABLE是VB-Audio独立Donationware，欢迎捐赠支持：https://vb-audio.com/Cable/ 。许可：https://vb-audio.com/Services/licensing.htm 。本工具不附带驱动二进制，不改变其许可，下载原版包的说明和许可保留。

2026-10-31：已验证当前安装环境的双端点、真实官网下载、固定哈希、签名、独立EXE检测及防重复安装等测试。未在全新未安装VB-CABLE的电脑上实测UAC/安装/重启全过程，不把模拟分支视为实装验收。完整主程序已集成本向导，当前整包验证以根目录RELEASE.json为准。

本工具代码GPL-3.0-or-later。分发二进制时同时提供对应源码与许可证；第三方许可见THIRD_PARTY.md及licenses/。

开发构建：Windows / Python 3.12，`python -m pip install -r requirements-audio-setup.txt`后运行`tools/build_audio_setup.ps1`。
