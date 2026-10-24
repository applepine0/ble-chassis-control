# ble-chassis-control

底盘上位机:支持 **HC-05 串口(经典蓝牙 SPP)** 与 **BLE** 双链路,实现控制指令下发与数据回传显示。

- 技术栈:Python 3.13 + tkinter;串口用 pyserial、BLE 用 Bleak;uv 管理,src layout
- 硬件:HC-05(经典蓝牙,**推荐**)/ BLE 模块 + USB-TTL,电脑自带蓝牙

---

## ⚡ 快速开始(Windows)

```powershell
uv sync                        # 安装依赖(bleak + pyserial)
uv run ble-chassis-control     # 启动 GUI
# 或
uv run python -m ble_chassis_control
```

### 首次使用 HC-05(推荐路线)

1. **Windows 配对**:设置 → 蓝牙和其他设备 → 添加设备 → 蓝牙 → 选 **HC-05** → PIN `1234` 或 `0000`;
2. 配对后:设备管理器 → **端口(COM 和 LPT)** 会出现"标准串行 over 蓝牙链接 (**COMx**)"标记;
3. 启动 GUI → 确保选中 **串口 (HC-05)** → 填 COM 号(留空自动检测)→ 点 **连接**;
4. 用速度滑块控制底盘;命令行验证可用 `verify_serial.py`。

> ⚠️ **串口独占**:连接时请关闭 VOFA+ / 串口助手 / Arduino IDE 等占用同一 COM 口的程序,否则报"串口被占用"。

## 界面功能

| 区域 | 功能 |
| :--- | :--- |
| **链路选择** | **串口 (HC-05)** / **BLE** 单选切换,自动重建链路对象 |
| 目标栏 | 串口模式填 COM 号(留空自动检测)+ **刷新口**;BLE 模式填模块地址 |
| 连接/断开/自检 | 串口自检 = 列出可用串口;BLE 自检 = 扫描目标广播信息 |
| 底盘控制 | 速度 m/s / 方向 deg / 自转 rad/s 三控件、发送一次、急停 |
| **旋转前进(定频下发)** | **启动运动/停止运动** + 频率设置;按 `θ(t)=θ₀−∫ω·dt` 反向补偿车体自转,使**世界系方向恒定** |
| SET 命令 | 通用参数下发,如 `SET:MAX_SPEED:0.8` |
| 手动发送 | 任意透传文本行发送(自动补 `\n`) |
| 通信日志 | TX/RX 分色显示、带时间戳、自动滚动、可清空 |
| 链路详情 | 串口:端口 + 波特率参数;BLE:GATT 服务树 |

## 通信协议(文本行 + 定点整数,以 `\n` 结尾)

透传链路上字节透明转发,协议走文本行便于调试;**串口与 BLE 共用同一协议**,MCU 端只需实现一次。

### 主指令:速度矢量(底盘类型无关)

```text
VEL:<v_mmps>,<deg_tenths>,<omega_mradps>
```

| 字段 | 物理含义 | 线上单位 | 换算示例 |
| :--- | :--- | :--- | :--- |
| `v_mmps` | 速度**大小**(标量,非负) | mm/s 整数 | 0.5 m/s → `500` |
| `deg_tenths` | 运动**方向角** | 0.1° 整数 | 90.0° → `900` |
| `omega_mradps` | **自转角速度** | mrad/s 整数 | 1.570 rad/s → `1570` |

| 示例 | 含义 |
| :--- | :--- |
| `VEL:500,900,0` | 0.5 m/s,朝 90.0°,不转 |
| `VEL:0,0,1570` | 原地以 1.570 rad/s 自转 |
| `VEL:800,450,-500` | 0.8 m/s 朝 45.0°,同时 -0.5 rad/s 自转 |
| `STOP` | 急停(三量归零,短帧优先) |
| `SET:<参数名>:<数值>` | 通用参数,如 `SET:MAX_SPEED:0.8` |

**设计要点**

- **全整数定点**:MCU 端零浮点解析(`atoi`/手写整数),无浮点库依赖;
- **精度**:1 mm/s、0.1°、0.001 rad/s;
- **协议中立**:不涉及参考系(车头/世界)、正方向、底盘类型(舵轮/麦轮/差速)——
  这些属于 **MCU 侧运动学语义**,由 MCU 做逆解,上位机只表达"意图";
- **UI 约定**(不进协议):方向按车头坐标系、0°=正前、逆时针为正;
- **带宽**:`VEL:500,900,0\n` ≈ 15 字节 → @9600 约 64 条/s;**@115200(当前)约 768 条/s**,20~100Hz 控制无压力。

### 兼容保留(旧差速接口)

| 指令 | 格式 | 说明 |
| :--- | :--- | :--- |
| 差速速度 | `MOT:<左>,<右>\n` | 百分比,范围 [-100,100] |

### 回传(MCU → PC)

| 指令 | 格式 | 说明 |
| :--- | :--- | :--- |
| 遥测 | `TEL:<键>:<值>[,<键>:<值>...]\n` | 如 `TEL:BAT:7.4,SPD_L:12.3`,界面结构化显示 |
| 其他 | 任意文本行 | 原样显示在日志 |

编码默认 utf-8;协议定义集中在 `src/ble_chassis_control/protocol.py`。

### ⚠️ MCU 端实现要点

- 必须用**半角** `:` 和 `,` —— 全角会导致解析失败;
- 每条指令以 `\n` 结尾作为分帧依据;
- 收到 `VEL:` 后:**除以缩放系数还原物理量**
  (`v/1000.0` m/s、`deg/10.0`、`omega/1000.0` rad/s),再做舵轮逆解;
- 波特率与模块/MCU 一致(**当前 115200**;HC-06 默认 9600,可用 `AT+BAUD8` 改);
- `v` 为标量大小,负值非法(上位机已钳为 0)。

## 接口文档

完整 API 文档见 **[`docs/API.md`](docs/API.md)**,包含:

- 协议线格式(VEL/STOP/SET/TEL)与定点换算
- `Protocol` / `ChassisLink` / `SerialBackend` / `BLEBackend` / `App` 全部 API
- 配置项速查表、异常说明、典型示例
- **只下发模式(不接收回传)的支持情况与限制**

## 代码结构

```
src/ble_chassis_control/
├── config.py          # 链路选择 / 串口参数 / 地址 / UUID / 超时重试 / UI 范围
├── protocol.py        # 协议:VEL(主)/MOT(兼容)/STOP/SET 编码 + TEL 解析
├── motion.py          # 定频运动下发:θ(t)=θ₀+SIGN·∫ω·dt(SIGN=-1 世界系定向)
├── link.py            # 链路抽象基类 ChassisLink(统一 BLE 与串口的接口)
├── backend.py         # BLE 后端(Bleak):扫描→连接→UUID 自动解析→收发
├── backend_serial.py  # 串口后端(pyserial):HC-05/06 / USB-TTL,读线程+自动重连
├── frontend.py        # tkinter 界面(速度/方向/自转 三控件 + 日志)
└── __main__.py        # python -m 入口
```

**架构要点**:前端只依赖 `ChassisLink` 抽象 —— 换链路(串口 ↔ BLE)或换模块只需增删一个后端实现,**前端零修改**;换底盘类型只需改 MCU 逆解,**协议与上位机零修改**。

## BLE 连接诊断结论(实测,2025-10)

当前卡点:GATT 服务枚举超时(`TimeoutError`)。已在本机通过多组探针复现并排除/确认:

| 探针 | 结果 | 结论 |
| :--- | :--- | :--- |
| 扫描 | 正常,RSSI -26 | 模块广播正常 |
| 广播包 | service_uuids=[FFE0, FEE7],地址类型= public(0) | 协议 UUID 已确认;模块按 public 广播 |
| 连接(默认/UNCACHED/public) | 链路连上,GATT 枚举 >30s 超时 | 非缓存、非地址类型、非超时参数问题 |
| 显式 random 地址 | 设备找不到 | 确认 Windows 按 public 缓存 |
| 连接前配对 | `Could not pair: FAILED` | 模块不响应安全握手 |
| 重试 3 次 × 40s | 全部同样超时 | **稳定复现,非 Windows 偶发** |
| 定向拉取 FFE0 / FEE7 服务 | 全部超时 | Windows 另一条 GATT 查询路径同样挂起 |
| 读 Windows 已有缓存(CACHED) | 超时 | Windows 端无历史 GATT 缓存 |
| 单次等待 90s | 超时 | 不是模块响应慢,是**完全不应答** |
| **裸 WinRT 调用(绕开 Bleak/GattSession)** | 全量默认 / UNCACHED / 定向 FFE0,均 >25s 超时;且 `connection_status=0` | **连系统 API 层都不应答 —— 证据链闭环,确证模块侧无 GATT 会话** |
| **广播类型诊断(不连接)** | `CONNECTABLE_UNDIRECTED` + `GENERAL_DISCOVERABLE`,应答扫描请求 | 模块固件层面**声明可连接且存活**;排除"不可连接广播";指向:连接唤醒固件握手未完成 或 被其他主设备占用 |
| **对照实验(同机其他 BLE 设备)** | DARKRIM SHADOW 连接成功、GATT 枚举成功(1800/AE00 两服务、read/write/notify);iKF 耳机超时(配对前闸住属正常) | **PC 蓝牙栈(Realtek+WinRT+bleak)完全健康 —— 问题 100% 在 JDY-23 模块侧** |
| **手机 LightBlue 交叉验证** | 手机可连、可见 FFE0/FFE1、串口→BLE 透传双向通、+CONN/+DISC 事件正常 | **模块 GATT 与透传功能完好** —— 问题精确锁定在"Windows ↔ JDY-23(V81-02 固件)配对/安全层不兼容" |
| **Windows 系统级手动配对** | 配对失败;固件无 AT+TYPE/AT+PASS 开关;注册表无缓存残留;社区无已知解 | **结论:JDY-23 V81-02 与 Windows 蓝牙栈不兼容,无软件修复路径 → 替换主机(USB 蓝牙)或替换模块(HM-10/正片 nRF52)** |

**结论:链路层能连、广播正常,但所有 GATT 查询(全量/定向/缓存/90s 长等)全部无应答、配对被拒 —— 模块当前没有在维护一个可用的 GATT 会话**,指向模块侧,最可能是:

1. **模块正被其他设备占用**(JDY-23 仅支持单连接):手机上的透传 App / 微信小程序还连着模块时,新连接能建立但 GATT 请求无人应答;
2. **模块处于低功耗休眠**:`AT+STARTEN 0`(上电休眠)时可能只广播不完整应答 GATT;
3. 蓝牙适配器/驱动异常(较小概率,因扫描与链路均正常)。

## 根因线索:JDY-23 休眠模式(数据手册 3.3 节)

手册原文要点(实测症状完全吻合):

| 模式 | 行为 | 与实测的对应 |
| :--- | :--- | :--- |
| `AT+STARTEN 1` 上电唤醒 | 上电即正常广播,可被连接;可用 `AT+SLEEP` 主动休眠 | — |
| `AT+STARTEN 0` 上电休眠 | 功耗极低,广播电流最低 3µA,**休眠中仍广播但**;PWRC 引脚低电平唤醒;唤醒后若 `1 秒内` 串口无数据且未被连接,自动再次休眠(已连接则保持唤醒) | 广播正常、连接后 GATT 无应答、`connection_status=0`、配对被拒 |

**结论:若模块当前为 `AT+STARTEN 0`(上电休眠),则会"广播但不服务连接"——这正是本机 3 轮探针的全部症状。**

### 修复步骤(USB-TTL,AT 命令以 `\r\n` 结尾)

1. `AT+STARTEN?` → 若返回 `0`,执行 `AT+STARTEN1`(改为上电唤醒,**参数断电保存**);
2. `AT+HOSTEN?` → 确认从机模式(非 iBeacon,BLE 从机应返回 `0` 或对应参数);
3. 模块断电重新上电,等待 10s;
4. 重跑 `uv run python tools/ble_probe4.py` 验证 GATT 枚举。

> 备选(不改配置):给 PWRC 引脚一个低电平脉冲唤醒后,**立即**发起连接(唤醒后 1s 内未连接会再睡)。

## 现场排查清单(按顺序)

> JDY-23 的 AT 指令经 USB-TTL 发送,**必须以 `\r\n` 结尾**;模块仅在未连接时响应 AT。

1. **关闭手机上所有连接该模块的 App / 小程序**(JDY-23 只支持单连接,被占用时新连接能建立但 GATT 无人应答),必要时开飞行模式再关;
2. USB-TTL 联机执行 AT 查询(每条回车换行):

   | 指令 | 期望 | 含义 |
   | :--- | :--- | :--- |
   | `AT+STAT` | 返回未连接参数 | 确认无其他主设备占用 |
   | `AT+STARTEN?` | 建议为 `1` | `1`=上电唤醒,`0`=上电休眠(休眠时广播正常但 GATT 可能不应答) |
   | `AT+HOSTEN?` | 从机(=0 或对应透传从机模式) | 若为 iBeacon 模式则根本没有 GATT 服务 |
   | `AT+VER` | 记录固件版本 | 排查固件问题用 |

3. **模块重新上电**,等待 5~10s 再连接(排除休眠/僵尸链路);
4. 用手机 nRF Connect 连接模块做对照:若能枚举出 FFE0/FFE1 服务 → 问题在 Windows 蓝牙栈,执行第 5/6 步;若手机也超时 → 问题在模块/固件/iBeacon 模式;
5. 设备管理器 → 蓝牙适配器(Realtek `USB\VID_0489&PID_E122`)→ **禁用再启用**(需要管理员权限,脚本无法代做);
6. 重启电脑;
7. 仍失败:考虑模块固件版本(AT+VER),联系供应商。

连接成功后,主程序会自动完成:GATT 服务树展示 → FFE1 写入/订阅 → MOT/STOP/SET 控制 → TEL 回传结构化显示。

### 诊断脚本(仓库根目录)

```powershell
uv run python tools/test_scan_ble.py     # 扫描
uv run python tools/verify_serial.py     # 串口链路验证(HC-05/USB-TTL):打开→发指令→收数据
uv run python tools/verify_link.py       # BLE 全链路验证:扫描→连接→GATT→写 FFE1→收通知
uv run python tools/ble_probe3.py        # 广播包分析 + 重试连接
uv run python tools/ble_diag2.py         # 多参数组合自动尝试(核查连接)
uv run python tools/ble_probe4.py        # 定向拉取 GATT 服务(绕行方案核查)
uv run python tools/ble_probe5.py        # 缓存读取 + 90s 长等(最终核查)
uv run python tools/ble_probe6.py        # 裸 WinRT 调用,绕开 Bleak(证据链闭环核查)
uv run python tools/ble_probe7.py        # 广播类型诊断(不连接判断模块可否被连)
uv run python tools/ble_probe8.py        # 快速连发连接(唤醒固件尝试)
uv run python tools/ble_probe9.py        # 对照实验:同机其他 BLE 设备(PC 栈健康性判定)
uv run python tools/ble_probe10.py       # 热连接:捕获广播瞬间立即连(排除时序窗口问题)
uv run python tools/ble_probe11.py       # GATT 冲刷:8 次短会话密集枚举(排除偶发可用)
uv run python tools/ble_probe12.py       # 同会话内 4 次反复枚举(排除会话内重试路径)
uv run python tests/test_smoke.py        # 协议/后端/GUI 冒烟(不连硬件)
uv run python tests/test_gui_flow.py     # GUI 功能测试:模拟后端跑通连接/发送/回传/显示
uv run python tests/test_motion.py       # MotionRunner 测试:θ 累积/世界系定向/定频(10 项)
uv run python tests/test_serial_backend.py  # 串口后端功能测试(6 项,含只下发模式)
```

## 参考资料

- [JDY-23 官方数据手册 (V2.1 PDF)](https://cxemka.com/upload/art/jdy23/f/JDY-23-V2.1_BLE_5.0_Datasheet.pdf)
- [Bleak 文档](https://bleak.readthedocs.io/)