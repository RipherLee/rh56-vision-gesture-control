# RH56 Vision Gesture Control

基于 Google MediaPipe 预训练手势模型，通过电脑摄像头识别静态手势，并经 RS485 控制 Inspire Robots RH56 / RH56DFTP 灵巧手。

本项目不需要采集数据或重新训练模型。默认先以“仅识别”模式运行，连接硬件后仍需在画面窗口按 `A` 才会启用动作。

## 功能

- RS485 EB90 协议驱动，以及实验性 Modbus TCP 驱动
- MediaPipe 预训练手势映射：张开、抓取、指向、点赞、比耶
- 基于 21 点关键点距离的捏取识别
- 连续多帧确认、动作锁存、冷却和通信异常自动停用
- Windows 与 Linux 支持
- 控制台手动调试和无硬件单元测试

## 动作映射

| 视觉输入 | 项目动作 | 灵巧手目标角度 |
|---|---|---|
| `Open_Palm` | `open` | `[1000, 1000, 1000, 1000, 1000, 1000]` |
| `Closed_Fist` | `grab` | `[500, 500, 500, 520, 520, 0]` |
| 指尖靠近规则 | `pinch` | `[0, 0, 500, 520, 520, 0]` |
| `Pointing_Up` | `point` | `[0, 0, 0, 1000, 0, 180]` |
| `Thumb_Up` | `like` | `[0, 0, 0, 0, 1000, 1000]` |
| `Victory` | `victory` | `[0, 0, 1000, 1000, 250, 1000]` |

角度顺序为：小指、无名指、中指、食指、拇指弯曲、拇指旋转。实际参数及前置动作以 [`gesture_config.py`](gesture_config.py) 为准。

## 环境要求

- Python 3.10 或 3.11
- USB-RS485 适配器
- RH56/RH56DFTP 灵巧手及独立电源
- 摄像头（只使用手动控制台时不需要）

## 安装

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Linux

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
```

也可以运行 `scripts/setup.ps1` 或 `scripts/setup.sh`。

## 使用

只验证摄像头识别，不连接灵巧手：

```bash
python vision_gesture_control.py
```

连接灵巧手：

```bash
python vision_gesture_control.py --control-hand --speed 250 --force 200
```

- Windows 默认串口：`COM7`
- Linux 默认串口：`/dev/ttyUSB0`
- 使用 `--port` 或环境变量 `RH56_PORT` 覆盖默认串口

Windows 示例：

```powershell
$env:RH56_PORT = "COM8"
python vision_gesture_control.py --control-hand
```

Linux 示例：

```bash
export RH56_PORT=/dev/ttyUSB1
python vision_gesture_control.py --control-hand
```

Linux 如果串口权限不足，可将当前用户加入 `dialout` 组，重新登录后生效：

```bash
sudo usermod -aG dialout "$USER"
```

窗口按键：

- `A`：启用/停用灵巧手动作
- `R`：重置手势锁存
- `Q` 或 `Esc`：退出

手动控制台：

```bash
python hand_console.py
```

驱动连通性测试：

```bash
python hand_driver.py --probe
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试不连接摄像头和灵巧手。

## 安全提示

- 首次联调先运行纯视觉模式。
- 连接硬件后程序仍为未启用状态，确认画面识别稳定后再按 `A`。
- 从低速度、低力阈值开始调试，并保持急停/断电可触达。
- 更换灵巧手、夹具或物体后必须重新检查 `gesture_config.py` 中的角度，避免机构碰撞。

更多说明见 [`docs/视觉手势识别-使用说明.md`](docs/视觉手势识别-使用说明.md)。

## 许可证

项目代码采用 [MIT License](LICENSE)。MediaPipe 和预训练模型的归属及许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
