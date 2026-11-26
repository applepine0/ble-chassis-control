# ble-chassis-control

> Windows 底盘上位机：通过 HC-05 经典蓝牙串口或 BLE 透传连接底盘 MCU，下发速度矢量与辅助执行器指令，接收四轮转速与状态反馈。
> 状态：开发/联调中。

## 1. 这是什么

- 串口主链路：HC-05/06 经典蓝牙 SPP 虚拟 COM 口，默认 `115200 8N1`；
- BLE 备选链路：透传特征 FFE1（write + notify），兼容 NUS；
- 控制：`VEL` 速度矢量、`STOP` 底盘急停、`SET` 参数、`LIFT/AUX/CYL` 辅助执行器；
- 反馈：文本 `TEL` + 11 字节四轮转速二进制帧；
- 界面：tkinter + ttk + sv-ttk，亮/暗主题，可调字体，四轮实时曲线；
- 不包含：MCU 固件、运动学逆解、CAN/电机驱动。

## 2. 架构

```text
tkinter GUI (frontend.py + ui/)
        │ 事件 / 状态 / 日志
        ▼
ChassisLink 抽象 (link.py)
        ├── SerialBackend (backend_serial.py)  HC-05 SPP
        └── BLEBackend    (backend.py)         BLE 透传
        │
        ▼
Protocol / StreamDemux (protocol.py)
        ├── 文本行：VEL / STOP / SET / LIFT / AUX / CYL / TEL
        └── 二进制：0xAA + seq + 4×int16 + CRC8
```

### 2.1 模块职责

| 路径 | 职责 |
| :--- | :--- |
| `config.py` | 链路、串口、UUID、超时、协议范围、UI 参数 |
| `protocol.py` | 文本编解码、11B 帧、CRC8、流式解复用 |
| `motion.py` | θ 时间积分与定频下发 |
| `link.py` | `ChassisLink` 抽象：连接/发送/回调/统计 |
| `backend_serial.py` | 串口后端：读线程、写超时、自动重连 |
| `backend.py` | BLE 后端：扫描、GATT 解析、通知 |
| `chart.py` | 四轮曲线与共享 `WheelData` |
| `frontend.py` | 控制器：事件、异步、状态机 |
| `ui/` | 主题、布局、曲线窗口、字体设置、辅助执行器面板 |
| `logging_setup.py` | 滚动文件日志 |

### 2.2 线程模型

- Tk 主线程负责界面；
- asyncio 事件循环在后台守护线程；
- 串口读线程阻塞读，收到数据经队列回传；
- 后台回调通过 `queue.Queue` 回到 GUI 线程，由 `after(50)` 消费。

### 2.3 协议摘要

完整线格式见 [spec/API.md](spec/API.md)；MCU 实现要求见 [spec/MCU-INTERFACE.md](spec/MCU-INTERFACE.md)。

```text
PC → MCU:
  VEL:<v_mmps>,<deg_tenths>,<omega_mradps>\n
  STOP\n
  SET:<名>:<整数>\n
  LIFT:<0..500>\n
  AUX:1:<0..3600>\n
  CYL:1:<0|1>\n

MCU → PC:
  TEL:<键>:<值>[,<键>:<值>...]\n
  0xAA + seq + 4×int16(LE rpm) + CRC8    # 11B 四轮帧
```

关键语义：

- `VEL` 是锁存设定值，MCU 持续执行到新 `VEL` 或 `STOP`；
- `STOP` 只停旧底盘驱动，不改变 `LIFT/AUX/CYL`；
- `LIFT` 相对标定零点，1 mm 分辨率；
- `AUX` 绝对角，0.1° 占位；
- 气缸只有伸/缩，不报中间过程。

## 3. 装配与接线

| 项 | 说明 |
| :--- | :--- |
| 主链路 | HC-05/06 配对后生成虚拟 COM 口；模块与 MCU 均 `115200` |
| 串口独占 | 连接时关闭 VOFA+、串口助手、Arduino IDE 等占用程序 |
| BLE 模块 | 广播 FFE0，透传 FFE1（write + notify）；备选 NUS |
| 供电/电平 | 模块与 MCU 共地，TX/RX 交叉，电平匹配 |
| 安全 | `STOP` 只停底盘；升降/辅助轮/气缸由各自控制，整机急停未实现 |

已知硬件限制：本机 JDY-23（V81-02）与 Windows 的配对/GATT 枚举不兼容，BLE 代码保留，实际优先串口。

## 4. 快速开始

```powershell
uv sync
uv run ble-chassis-control
```

界面步骤：

1. 选择“串口 (HC-05)”；
2. COM 下拉选择或手填口号；
3. 点“连接”；
4. 调速度/方向/自转，点“发送一次”；急停按 `Esc`；
5. 四轮反馈出现后，曲线自动刷新；可“放大”或“导出 CSV”。

环境、测试、调试与当前任务见本地开发文档 `docs/dev.md`（本机，未跟踪）。

## 5. 当前状态

| 模块 | 状态 | 说明 |
| :--- | :--- | :--- |
| 文本协议 / 11B 帧 | ✅ | 编解码与 CRC/seq 测试通过 |
| 串口链路 | ✅ | HC-05 真机验证 |
| BLE 链路 | ⚠️ | 代码完成；本机 JDY-23 不兼容 |
| 运动下发 | ✅ | 定频 θ 积分；`test_motion` 9/10 |
| 四轮曲线 | ✅ | 共享数据、暂停、时间轴、CSV、放大 |
| 字体/主题 | ✅ | 亮/暗、可调字体、持久化 |
| 辅助执行器 | 🚧 | 上位机控件与协议完成，等待 MCU 联调 |
| 文件日志 | ✅ | 滚动日志 + 目录入口 |

测试基线：

```text
test_smoke / wheel / chart / serial / gui  -> 通过
test_gui_flow                              -> 18 条路径
test_motion                                -> 9/10（50Hz 实测约 32Hz）
```

## 6. 目录

```text
ble-chassis-control/
├── README.md
├── pyproject.toml / uv.lock
├── src/ble_chassis_control/
│   ├── config.py
│   ├── protocol.py
│   ├── motion.py
│   ├── link.py
│   ├── backend.py
│   ├── backend_serial.py
│   ├── chart.py
│   ├── frontend.py
│   ├── logging_setup.py
│   └── ui/
│       ├── theme.py
│       ├── layout.py
│       ├── chart_window.py
│       ├── settings_window.py
│       └── extensions.py
├── spec/
│   ├── API.md
│   └── MCU-INTERFACE.md
├── tests/
└── tools/
```

## 7. 文档

| 文档 | 内容 | 跟踪 |
| :--- | :--- | :--- |
| `README.md` | 项目介绍、架构、接线、快速开始、当前状态 | 显式 |
| [spec/API.md](spec/API.md) | PC 侧 API、线格式、类参考 | 显式 |
| [spec/MCU-INTERFACE.md](spec/MCU-INTERFACE.md) | MCU 侧协议、解析、CRC、验收 | 显式 |
| `docs/dev.md` | 本地环境、命令、调试、当前任务 | 隐式 |
| `docs/devlog.md` | 开发过程记录 | 隐式 |

> `docs/` 与 `AGENTS.md` 是本机开发资料，不进入版本库；需要跨机器共享时再显式提交。
