# Windows 安装与维护流程

与 `../SKILL.md` 配套。以下命令和按钮基于 **0.13.0-alpha.1**；这是预发布版本，不能用只返回稳定版的“latest”地址代替。

## 1. 获取程序并校验

发布页：[v0.13.0-alpha.1](https://github.com/VictorAgents/gokit5-voice-key/releases/tag/v0.13.0-alpha.1)。普通用户下载 `Gokit5Community.exe` 和 `SHA256SUMS.txt` 即可；源码ZIP不是安装程序。EXE未做代码签名，不能把它描述为已签名软件，也不能绕过用户或系统安全限制。

可用PowerShell完成下载和校验。使用新的任务目录，不覆盖用户已有文件；下面目录名含随机后缀，无需管理员权限：

```powershell
$taskDir = Join-Path ([IO.Path]::GetTempPath()) ('gokit5-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskDir -ErrorAction Stop | Out-Null
$releaseBase = 'https://github.com/VictorAgents/gokit5-voice-key/releases/download/v0.13.0-alpha.1'
$installerPath = Join-Path $taskDir 'Gokit5Community.exe'
$checksumPath = Join-Path $taskDir 'SHA256SUMS.txt'
Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri ($releaseBase + '/Gokit5Community.exe') -OutFile $installerPath -ErrorAction Stop
Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri ($releaseBase + '/SHA256SUMS.txt') -OutFile $checksumPath -ErrorAction Stop
$checksumLines = @(Get-Content -LiteralPath $checksumPath | Where-Object { $_ -match '^[0-9a-fA-F]{64}\s+Gokit5Community\.exe$' })
if ($checksumLines.Count -ne 1) { throw '校验清单缺少唯一的EXE条目，停止运行' }
$expectedHash = ($checksumLines[0] -split '\s+', 2)[0]
$actualHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256 -ErrorAction Stop).Hash
if ($actualHash -ine $expectedHash) { throw 'EXE校验失败，停止运行' }
# 额外核对这份技能已检查过的固定发布产物，其他版本不能沿用此值。
if ($actualHash -ine '04dd5257735ff584bf2094cb7ae6c434cd2a9011f107379a8dbe15199ac46223') { throw '与本技能适配的发布产物不同，停止并核对版本' }
& $installerPath --self-test
if ($LASTEXITCODE -ne 0) { throw '程序自检失败，停止' }
```

网络下载过慢或超时则停止该次尝试，让用户从同一Release用浏览器下载；不要无限重试或换来历不明的镜像。已有完整本机文件时，跳过创建目录和两条下载命令，将`$installerPath`、`$checksumPath`指向用户提供的文件，再运行相同校验与自检部分。残缺下载不能直接运行。

下载与散列核对不代表用户已授权刷机。校验失败保留错误阶段，勿改预期散列“让它通过”。程序自检导入依赖并校验编译固件包，不读取或刷写硬件，也不替代实物验收。

当前已实现的入口：

| 参数 | 实际作用 |
|---|---|
| 无参数或 `--installer` | 图形安装器，包含首次安装、应用更新和恢复 |
| `--self-test` | 程序/依赖/固件包自检，输出JSON，不打开GUI |
| `--cable-probe` | 检查虚拟音频环境，输出JSON，不安装驱动 |
| `--audio-setup` | 图形语音环境向导 |
| `--wifi` | 本机USB配网窗口；未选设备时转安装器 |
| `--panel` | 控制台；未选设备时转安装器 |
| `--bootstrap` | 后台接收启动器，可能持续重试，不作为只读检查运行 |

不要猜测 `--help`、`--version`、`--flash`、`--install --yes` 或串口/密码参数。这个版本未知动作可能转入GUI，不是通用命令行安装器。`--esptool`是内部工作进程入口，不是可省略安全检查的一键刷机接口。

得到安装授权后打开图形安装器：

```powershell
Start-Process -FilePath $installerPath -ArgumentList '--installer' -WindowStyle Hidden
```

程序会显示图形窗口；只有终端能力的Agent此时转为指导用户，不另写脚本直接刷Flash。

## 2. 检测USB与板型

- 数据线直连电脑、开机、一次一台；安装器只列出USB VID/PID **303A:1001** 的候选串口。不要照抄示例COM号；蓝牙虚拟串口不作为刷机端口。
- 如需只读检查：`Get-PnpDevice -PresentOnly`，按USB VID/PID筛选，再查看名称/状态；输出可能包含私人设备身份，不整体贴到聊天。
- 仍需用户的同款四键板依据：电源/麦克风/＋/－、文档确认接线；芯片识别通过不能代替此确认。
- 无端口先换确认能传数据的线、直连端口、确认开机并关闭串口占用；不要盲装CH340/CP210x驱动、拆机或短接未知焊盘。

## 3. 语音环境

安装器显示“语音环境：已就绪（CABLE Input / Output）”可跳过。若未就绪，打开 **语音环境检测 / 安装**：

| 结果 | 下一步 |
|---|---|
| ready | 双端点与48kHz双声道可用；还需输入法与真实转写验收 |
| missing | 取得用户同意，由向导准备官网包，用户完成管理员确认和Install Driver |
| not_ready | 已检测到驱动但端点未完整就绪；先按向导建议重启/复检，不反复覆盖安装 |
| error | 记录错误阶段并排查；不能当成未安装直接重装 |

VB-CABLE是VB-Audio的独立Donationware，不随项目打包。向导校验下载哈希和Windows安装器签名；不导入证书、不假设静默参数、不自动重启。用户保存工作后自行重启，再打开安装器复检。只要蓝牙快捷键时不需要VB-CABLE。

## 4. 选择正确刷写模式

### 首次改装

选实际串口，确认板型及原厂功能替换/备份说明，点 **一键备份并刷入**。工具先探测芯片和安全状态，双读完整16MB并核对SHA256，复核身份与包校验后写配套四段；最后显示“刷入完成”。期间不要拔线、关机、强杀程序。

安装数据默认位于 `%LOCALAPPDATA%\Gokit5Community`：

- `app/`：安装后的程序；
- `devices/<设备ID>/`：各设备配置、设置与音效；
- `backups/<时间>/backup.json`、`factory-1.bin`、`factory-2.bin`：本次快照与清单；
- `logs/`：本机运行记录。

两份镜像应各16777216字节且SHA256相同，并与清单一致。保留第一次改装前的整套文件，另存独立介质；不能被之后的升级备份替代。清单 `stock_firmware_verified=false` 表示未鉴定必为原厂，`restore_tested=false`表示未实测恢复，不要擅自改成true。

开发者可用 `GOKIT5_DATA_DIR`隔离测试数据。普通用户保持默认，不创建临时数据目录后再声称登录自启可永久找到配置。

### 已有本项目固件更新

下载新版本的配套EXE；旧控制台保存关闭、接收停止、USB接回。点 **已有本项目固件：备份并更新（保留配网、按键与音效）**。双份备份与身份/安全检查仍执行；现有分区匹配才只写应用区，保留NVS、蓝牙、音效等。分区不同会停止，不自动改首次安装或强刷。

## 5. USB配网与首次蓝牙

**Wi-Fi**：保持USB，选本机设备，点 **USB配网**。用户只在本机窗口输入2.4GHz网络名称/密码，点 **通过USB配置Wi-Fi**，等“配网成功”；电脑可以用网线，但与设备必须局域网互通。当前窗口要求密码8～63字节，不支持随意假设开放网络/企业认证。超时依据窗口失败原因排查信号、密码和网络隔离；修正后再试，不索取密码到聊天，不删除pending配置或重刷当作通用修复。

**蓝牙**：让用户先松开所有Alt与设备按键，同时按＋／－约3秒，灯闪后松开。在Windows **蓝牙和设备→添加设备→蓝牙**选 **Gokit5 Keys-xxxxxx**。后缀用来区别设备，不固定为某台维护者的名字。失败可在用户确认后删除对应失败/旧配对条目，再重配，不能批量清除其他设备。

## 6. 控制台和验收

打开控制台、点 **开始接收**，等“设备已连接”。修改后 **保存并应用**并等“已同步到设备”：

| 设置 | 可修改内容 |
|---|---|
| 麦克风键 | 快捷键；按住说话/按住快捷键/点按快捷键 |
| 加减键及同时短按 | 各自快捷键；加减可点按或按住，组合只点按；同时长按3秒保留配对 |
| 声音 | 开/关机关闭、内置或自定义；最长6秒、音量0～100；PCM WAV可直接导入，MP3等需FFmpeg |
| 灯光 | 关闭、固定色、呼吸、彩虹、按键反馈；颜色、亮度、空闲熄灯（0表示常亮） |
| 电脑端 | 麦克风增益、后台接收、登录自动运行、虚拟麦克风检查 |

电源键保留开关机，不映射普通快捷键。电量采样无效时显示“暂不可读”，不能据此判断电池坏或显示0%。

用户输入法选择 **CABLE Output**录音端（不是CABLE Input），快捷键右Alt；用设备近嘴、电脑麦克风远离的方式验证音源。请用户准备空白正文，按住设备麦克风，听到就绪音结束再说通用句，松手确认停止并转文字；再测试已配置的快捷键和仅电池供电。不要向用户已有私人文档注入测试按键。

接收端离线时，已配对设备直接发送蓝牙右Alt，使用电脑麦克风；输入法必须选到电脑麦克风。“自动检测”不保证每台电脑选对。同一次长按不切换音源，掉线/恢复后需松手重按；120秒安全上限与输入法自身限制仍存在。

## 7. 故障与恢复

先定位失败阶段：下载/自检、USB探测、完整读取、写入、启动、配网、蓝牙、音频输入、真实转写。无新证据时不要反复重装/重刷。备份失败可能让设备留在下载模式；确认没有写入进程后才安排用户重新启动设备，不在刷写途中拔线。

恢复只在用户明确要求时进行：USB接原设备，点 **从本机备份恢复**，选择要返回状态的 `backup.json`。工具核对双镜像长度/散列、原设备身份及安全状态，再完整写回。恢复会覆盖当前设备设置与音效，**恢复入口不会自动再备份当前Flash**；需要保留当前状态时先单独安排备份，不能把会写入的“更新”按钮当作只读备份。

第一次改装前快照用于回到当时状态；后续更新备份只能回到更新前。早期维护者的 `manifest.json/factory-read*.bin` 不属于当前恢复GUI格式，不能仅改名伪造 `backup.json`。无原设备/无完整可信快照就停止，不下载他人的原厂镜像替代。完整恢复与原厂云端/账号功能目前尚未实机验收。
