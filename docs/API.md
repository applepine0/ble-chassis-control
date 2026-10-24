# ble-chassis-control 接口文档

> 版本:v0.1(协议:VEL 速度矢量)
> 适用:`src/ble_chassis_control/`

---

## 一、分层总览

```
┌─────────────────────────────────────────────────────────────┐
│ ① 应用层 API   Protocol / ParsedMessage  (protocol.py)      │
│    纯函数:编码指令、解析回传,无 IO、可单测                    │
├─────────────────────────────────────────────────────────────┤
│ ② 运动下发     MotionRunner (motion.py)  θ 时间积分 + 定频    │
├─────────────────────────────────────────────────────────────┤
│ ③ 链路抽象     ChassisLink (link.py)                        │
│    统一接口:connect/disconnect/send_* + 分帧/解析/回调        │
├─────────────────────────────────────────────────────────────┤
│ ④ 链路实现     BLEBackend / SerialBackend                   │
│    BLE(Bleak)/ 串口(pyserial,HC-05/06/USB-TTL)              │
├─────────────────────────────────────────────────────────────┤
│ ⑤ 界面        App (frontend.py)  tkinter + asyncio 后台线程  │
│     └─ WheelChart (chart.py)  四轮转速实时曲线                │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、协议接口(线格式)

### 2.1 主指令:速度矢量(PC → MCU)

```
VEL:<v_mmps>,<deg_tenths>,<omega_mradps>\n
```

| 字段 | 类型 | 单位(线上) | 物理单位 | 范围 | 说明 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `v_mmps` | int | mm/s | m/s | 0 ~ 32767 | 速度**大小**(标量,负值非法) |
| `deg_tenths` | int | 0.1° | deg | -32768 ~ 32767 | 运动方向角(不归一化) |
| `omega_mradps` | int | mrad/s | rad/s | -32768 ~ 32767 | 自转角速度,正负表转向 |

**缩放系数**:`v × 1000`、`deg × 10`、`omega × 1000`(四舍五入 + 钳位)

**静态示例**

```
VEL:500,900,0        0.5 m/s,90.0°,不转(直行)
VEL:0,0,1570         原地 1.570 rad/s 自转
VEL:800,450,-500     0.8 m/s 朝 45.0°,-0.5 rad/s 自转
```

#### ★ θ 时间语义(SIGN 符号约定)

MCU 用 ``ω`` 驱动**车体自转**,车头姿态 ``φ(t) = ∫ω dt``。世界系运动方向::

    θ_world(t) = θ_body(t) + ∫ω dt

上位机按以下关系累积 ``θ_body`` 并定频下发::

    θ_body(t) = θ₀ + SIGN · ∫ω dt        (deg,归一化到 [-180, 180))

| SIGN | θ_world 行为 | 物理效果 |
| :--- | :--- | :--- |
| **−1(默认)** | 恒定 = θ₀ | **车沿固定世界方向平移 + 车体自转** |
| +1 | 以 2ω 变化 | 圆弧更急(易表现为"偏心转",一般非期望) |

**为什么默认 −1**:车体自转已经让世界系方向转了 ``∫ω dt``;
若 θ_body 再叠加一份,世界系方向会以 **2ω** 变化 —— 这正是"偏心转"的成因。
故 θ_body 需**反向补偿**才能让世界系方向锁定。

> ⚠️ **MCU 端不得再对 ω 积分**。θ 的积分只有上位机这一处;
> MCU 只按收到的 (v, θ, ω) 做逆解(ω 用于轮子位置补偿)。

**时序示例**(20Hz,`v=0.5 m/s`、`θ₀=0°`、`ω=1.5708 rad/s` = 90°/s,SIGN=−1)

```
t=0.00s  VEL:500,0,1571        θ_body=0°
t=0.05s  VEL:500,-45,1571      θ_body=-4.5°
t=0.10s  VEL:500,-91,1571      θ_body=-9.1°
  ...
t=1.00s  VEL:500,-900,1571     θ_body=-90°  → 世界系方向仍为 0°
```

**配置**:`config.THETA_ACCUM_SIGN`(默认 `-1.0`),或 `MotionRunner(..., sign=±1)` 逐实例指定。

**为什么用真实时钟**(`time.monotonic`)而非"发送计数":定时器晚触发时按实际 Δt 补偿,
频率抖动不产生轨迹偏差。已实测:1 秒累积 −86.2°(期望 −90°,差值来自末帧调度余量),
世界系方向最大偏差 0.41°。

### 2.2 其他指令

| 指令 | 格式 | 说明 |
| :--- | :--- | :--- |
| 急停 | `STOP\n` | 三量归零(短帧,优先响应) |
| 参数设置 | `SET:<名>:<数值>\n` | 如 `SET:MAX_SPEED:0.8` |
| 差速(兼容) | `MOT:<左>,<右>\n` | 百分比 [-100,100] |

### 2.3 ★ 四轮速度反馈(二进制定长帧,11 字节)

```
字节:  [0]    [1]   [2] [3] [4] [5] [6] [7] [8] [9]   [10]
内容:  0xAA   seq   w0    w1    w2    w3(各 int16 小端) crc8
```

| 字段 | 说明 |
| :--- | :--- |
| `0xAA` | 帧头(用于在字节流中定位) |
| `seq` | 序号 0~255 循环,**用于丢帧检测** |
| `w0..w3` | 四轮转速,int16 小端,**单位 rpm**(缩放 1 → 直接是 rpm 整数) |
| `crc8` | CRC-8/ATM(多项式 `0x07`,初值 0),覆盖前 10 字节 |

**单位**:`rpm`(缩放 `1.0` → 线上值即 rpm 整数,范围 ±32767 rpm)。
见 `config.WHEEL_SPEED_SCALE` / `WHEEL_SPEED_UNIT`。

**轮序约定**(俯视,车头朝上,逆时针递增):

| 字段 | 轮位 |
| :--- | :--- |
| `w0` | **右前(RF)** |
| `w1` | 左前(LF) |
| `w2` | 左后(LR) |
| `w3` | 右后(RR) |

解码后可用 `frame.labeled()` 得到带轮位名的结果:`[('右前',120.0), ('左前',-250.0), ...]`。

#### 为什么需要 seq

| 字段 | 防的问题 |
| :--- | :--- |
| **crc8** | 帧内**比特错误**(误码)→ 检出错帧并丢弃 |
| **seq** | 整帧**丢失**(缓冲溢出/链路调度)→ 靠序号跳变发现 |

蓝牙 SPP 是**字节流**,HC-06 缓冲溢出会**静默丢弃**数据,写成功 ≠ 对端收到。
没有 seq 就永远不知道丢了多少帧。上位机据此统计**丢帧率**:

```
期望 = (last_seq + 1) & 0xFF
丢失 = (seq - 期望) & 0xFF      ← 掩码减法,正确处理 255→0 回绕
```

#### 带宽(@115200)

| 频率 | 占用 |
| ---: | ---: |
| 50 Hz | 550 B/s(4.8%) |
| **100 Hz** | **1100 B/s(9.5%)** ← 推荐 |
| 200 Hz | 2200 B/s(19%) |

#### 与文本行混用

接收侧**两者并行解析**:二进制帧由独立状态机处理(遇 `0xAA` 尝试定长解析,
CRC 不过则丢弃首字节继续扫描),文本行按 `\n` 分帧。

> ⚠️ **已知限制**:当前接收层把同一份字节同时交给帧解析器与文本分帧器,
> 帧内字节可能污染文本行(实测 100Hz 反馈 + `TEL` 混流时 `TEL` 解析失败)。
> 修复方向见 [`DEVELOPMENT.md`](DEVELOPMENT.md) 的“已知问题”。

#### 上层接口

```python
# 帧回调(收到一帧四轮速度)
link = SerialBackend("COM15", 115200, on_wheel=lambda f: print(f.seq, f.speeds))
# 统计回调(丢帧率/CRC 错误,可用于链路健康度显示)
link = SerialBackend(..., on_stats=lambda s: print(s.loss_rate, s.crc_errors))

# 统计对象
link.wheel_parser.stats.frames_ok      # 成功帧数
link.wheel_parser.stats.frames_lost    # 推断丢帧数
link.wheel_parser.stats.crc_errors     # 校验失败次数
link.wheel_parser.stats.loss_rate      # 丢帧率
```

#### 编解码工具(回环测试/MCU 参考)

```python
from ble_chassis_control.protocol import encode_wheel_frame, decode_wheel_frame, crc8

f = encode_wheel_frame([120.0, -250.0, 0.0, 385.0], seq=7)  # 11 字节(rpm)
d = decode_wheel_frame(f)                                    # -> WheelFrame 或 None
d.labeled()          # [('右前',120.0), ('左前',-250.0), ('左后',0.0), ('右后',385.0)]
crc8(b"123456789")                                           # 0xF4(标准向量)
```

### 2.4 回传(MCU → PC,文本可选)

| 指令 | 格式 | 说明 |
| :--- | :--- | :--- |
| 遥测 | `TEL:<键>:<值>[,<键>:<值>...]\n` | 如 `TEL:BAT:7.4,V:500` |
| 错误 | `ERR:<原因>\n` | 如 `ERR:BAD_VEL_NUM` |
| 其他 | 任意文本行 | 原样显示 |

**通用约束**:半角 `:` `,`;UTF-8;以 `\n` 分帧。

> 📌 **回传是可选的** —— 详见第九节「只下发模式」。

---

## 三、`protocol.py` API

### 3.1 编码函数(静态方法)

```python
Protocol.encode_vel(speed_mps: float, deg: float, omega_radps: float) -> bytes
```
速度矢量指令。负速度按 0 处理;超范围自动钳位。

```python
>>> Protocol.encode_vel(0.5, 90, 0)
b'VEL:500,900,0\n'
```

```python
Protocol.encode_stop() -> bytes                         # b'STOP\n'
Protocol.encode_mot(left: float, right: float) -> bytes  # 兼容接口
Protocol.encode_set(name: str, value: float) -> bytes
Protocol.encode_raw(text: str) -> bytes                 # 自动补 \n
```

### 3.2 解析函数

```python
Protocol.parse_line(line: str) -> ParsedMessage
```

解析一行(不含换行)。**不抛异常**,错误写入 `.error`。

```python
@dataclass
class ParsedMessage:
    raw: str                     # 原始行
    kind: str                    # vel|mot|stop|set|tel|text|unknown
    command: str | None          # 指令标签(小写)
    params: dict[str, Any]       # 结构化字段
    error: str | None            # 非 None 表示解析失败
```

**各 kind 的 params**

| kind | params 键 |
| :--- | :--- |
| `vel` | `speed_mps: float`、`deg: float`、`omega_radps: float`、`raw_int: tuple[int,int,int]` |
| `mot` | `left: float`、`right: float` |
| `set` | `name: str`、`value: float` |
| `tel` | `values: dict[str, float]` |
| `stop` | (无) |

```python
>>> m = Protocol.parse_line("VEL:500,900,0")
>>> m.kind, m.params["speed_mps"], m.params["deg"]
('vel', 0.5, 90.0)
```

### 3.3 常量与工具

| 名称 | 值 | 说明 |
| :--- | :--- | :--- |
| `SCALE_V` / `SCALE_DEG` / `SCALE_OMEGA` | 1000 / 10 / 1000 | 定点缩放 |
| `VEL_MIN/MAX`、`DEG_MIN/MAX`、`OMEGA_MIN/MAX` | int16 范围 | 钳位边界 |
| `SPEED_MIN/MAX` | -100 / 100 | 旧 MOT 百分比范围 |
| `decode_line(data: bytes) -> str` | — | utf-8 → gbk → latin-1 兜底解码 |

---

## 四、`link.py` — `ChassisLink` 抽象基类

### 4.1 构造

```python
ChassisLink(*, on_line: Callable[[str, ParsedMessage], None] | None = None,
              on_state: Callable[[str], None] | None = None)
```

| 回调 | 触发时机 | 线程 | 可选 |
| :--- | :--- | :--- | :--- |
| `on_line(text, parsed)` | 收到一整行(已解析) | 后端线程 | ✅ 传 `None` 即完全忽略接收 |
| `on_state(text)` | 连接/断开/错误等状态变化 | 后端线程 | ✅ |

> ⚠️ 回调在**后台线程**触发,GUI 使用必须经队列转发(见 `App._poll_queues`)。

### 4.2 属性

| 成员 | 类型 | 说明 |
| :--- | :--- | :--- |
| `kind` | `str` | 类属性,`"ble"` / `"serial"` |
| `is_connected` | `bool` (abstract) | 是否已连接 |
| `target` | `str` | 目标描述(地址 / `COM15@115200`) |
| `detail_text` | `str` | 链路详情(GATT 树 / 串口参数) |
| `mtu` | `int` | BLE 有效;串口为 0 |
| `last_error` | `str` | 最近一次错误描述 |

### 4.3 生命周期

```python
await link.connect() -> bool                # 失败抛 LinkError
await link.disconnect(manual: bool = True) -> None
```

`connect()` 语义:幂等、内置重试(`CONNECT_MAX_ATTEMPTS`);失败抛 `LinkError`。

### 4.4 发送

```python
await link.send(data: bytes) -> None                        # abstract,原始字节
await link.send_vel(speed_mps, deg, omega_radps) -> None    # ★ 主接口
await link.send_stop() -> None
await link.send_text(text: str) -> None
await link.send_set(name: str, value: float) -> None
await link.send_mot(left: float, right: float) -> None      # 兼容
```

未连接时抛 `LinkError`。

### 4.5 受保护方法(实现子类用)

```python
link._emit(text)               # 触发 on_state
link._emit_line(text)          # 解析并触发 on_line
link._feed_bytes(data: bytes)  # 按 \n 分帧(粘包/半包安全)
```

---

## 五、`motion.py` — `MotionRunner`(定频运动下发)

实现「旋转前进」:**上位机按真实时钟累积 θ,定频持续下发**。

### 5.1 构造

```python
MotionRunner(link: ChassisLink, *,
             v: float,             # 速度大小 m/s
             theta0: float,        # 初始方向角 deg(车体系)
             omega: float,         # 自转角速度 rad/s —— θ 以其速率累积
             rate_hz: float | None = None,   # 下发频率,默认 config.MOTION_RATE_HZ
             on_tick: Callable[[MotionTick], None] | None = None,
             stop_on_error: bool = True)
```

### 5.2 方法

```python
await runner.run(send_stop_on_exit: bool = True) -> float   # 阻塞执行,返回运行秒数
runner.stop() -> None                                       # 请求停止(线程安全)
runner.update(*, v=None, omega=None) -> None                # 运行中动态调整
```

| 属性 | 说明 |
| :--- | :--- |
| `is_running` | 是否正在运行 |
| `period` | 下发周期(秒) |
| `last_error` | 最近一次下发失败原因 |

### 5.3 回调数据

```python
@dataclass
class MotionTick:
    elapsed: float      # 自启动以来秒数
    theta_deg: float    # 当前累积方向角
    v_mps: float
    omega_radps: float
    index: int          # 第几次下发(1 起)
```

### 5.4 行为契约

| 行为 | 说明 |
| :--- | :--- |
| θ 累积 | `θ += ω × 实际Δt × (180/π)`,归一化到 `[-180, 180)` |
| 定频下发 | 按 `rate_hz` 持续下发,**数值不变也发**(保证 MCU 控制环稳定输入) |
| 周期补偿 | 睡眠时间 = `period - 本次发送耗时`,频率不随发送延迟漂移 |
| 停止 | 收到 `stop()` 后**自动下发一次 STOP**,再返回 |
| 未连接启动 | 抛 `LinkError` |
| 下发失败 | `stop_on_error=True` 时自动退出(并发 STOP) |
| 并发运行 | 同一实例重复 `run()` 抛 `RuntimeError` |
| 动静态调整 | `update(v=..., omega=...)` 不重置 θ₀,累积连续 |

### 5.5 示例

```python
from ble_chassis_control.motion import MotionRunner

# 以 0.5 m/s、90°/s 的角速度"旋转前进",20Hz 下发
runner = MotionRunner(link, v=0.5, theta0=0.0, omega=1.5708, rate_hz=20)
task = asyncio.create_task(runner.run())
await asyncio.sleep(5.0)      # 转 5 秒(累计 450°)
runner.stop()                 # 自动发 STOP
```

**实测**:1 秒 @20Hz → 20 帧,θ 由 0° 累积到 85.9°(期望 ~90°,差值来自末帧调度余量);
50Hz 设定实测 49.4Hz。详见 `tests/test_motion.py`。

### 5.6 模块函数

```python
normalize_deg(deg: float) -> float    # 归一化到 [-180, 180)
```

---

## 六、`chart.py` — `WheelChart`(四轮转速曲线)

纯 tkinter Canvas 实现,**无第三方依赖**。

### 6.1 构造

```python
WheelChart(master, *,
           names=("右前","左前","左后","右后"),  # 轮位名(图例)
           unit="rpm",                        # 纵轴单位标注
           maxlen=600,                        # 环形缓冲容量(采样点数)
           redraw_ms=50,                      # 重绘节流(ms)→ 20fps
           height=150)                        # 画布高度(px)
```

### 6.2 方法

```python
chart.push((120.0, -250.0, 0.0, 385.0))   # 推入一帧四轮转速(w0..w3 顺序)
chart.clear()                              # 清空曲线
chart.maybe_redraw()                       # 由主循环定期调用(内部按 redraw_ms 节流)
```

### 6.3 行为契约

| 项 | 说明 |
| :--- | :--- |
| **环形缓冲** | `maxlen` 满后自动丢弃最旧采样,内存恒定(600 @100Hz ≈ 6 秒窗口) |
| **自动量程** | 纵轴按数据范围自动缩放并留 10% 余量;**零线始终可见**(轮速有正负) |
| **节流重绘** | 数据可 100Hz 到达,重绘限制为 `redraw_ms`(默认 20fps),不拖慢界面 |
| **无数据提示** | 超过 2 秒无新帧,状态行显示"(无数据)";曲线保持最后状态 |
| **容错** | 空数据/单采样点/不足 4 轮的数据均不崩溃 |

### 6.4 配色

| 轮位 | 颜色 |
| :--- | :--- |
| 右前 (w0) | `#D32F2F` 红 |
| 左前 (w1) | `#1976D2` 蓝 |
| 左后 (w2) | `#388E3C` 绿 |
| 右后 (w3) | `#F57C00` 橙 |

### 6.5 配置项

| 项 | 默认 | 说明 |
| :--- | ---: | :--- |
| `config.CHART_MAXLEN` | 600 | 采样点容量 |
| `config.CHART_REDRAW_MS` | 50 | 重绘节流(ms) |
| `config.CHART_HEIGHT` | 150 | 画布高度(px) |

### 6.6 示例

```python
from ble_chassis_control.chart import WheelChart

chart = WheelChart(parent, names=("右前","左前","左后","右后"), unit="rpm")
chart.grid(row=1, column=0, sticky="ew")

# 收到帧时推入(Chart 由主循环 maybe_redraw() 驱动)
link = SerialBackend("COM15", 115200, on_wheel=lambda f: chart.push(f.speeds))
```

---

## 七、`backend_serial.py` — `SerialBackend`(主用)

```python
SerialBackend(port: str | None = None,
              baudrate: int | None = None,
              *, on_line=None, on_state=None)
```

| 成员 | 说明 |
| :--- | :--- |
| `port` | COM 号;`""` 则自动检测(优先蓝牙 SPP 口) |
| `baudrate` | 默认 `config.SERIAL_BAUDRATE`(**115200**) |
| `kind` | `"serial"` |
| `target` | `f"{port}@{baudrate}"` |

**特性**:独立读线程、`asyncio.to_thread` 包装阻塞写、写入后 flush、掉线自动重连、中文友好报错。
**读线程与发送完全解耦** —— 不订阅/不处理接收也不影响下发。

### 模块级函数

```python
available_ports() -> list[tuple[str, str]]   # [(设备名, 描述)],蓝牙口标 [蓝牙]
guess_port() -> str | None                   # 优先蓝牙 SPP 口,否则唯一口
```

---

## 八、`backend.py` — `BLEBackend`(备用)

```python
BLEBackend(address: str | None = None, *, on_line=None, on_state=None)
```

| 成员 | 说明 |
| :--- | :--- |
| `address` | 模块 MAC(默认 `config.DEVICE_ADDRESS`) |
| `write_uuid` / `notify_uuid` | 自动解析结果 |
| `kind` | `"ble"` |

**连接加固**:先扫描取 `BLEDevice` → `use_cached_services=False` + `address_type="public"` + 40s 超时 → 最多 5 次重试 → GATT 自动识别(FFE1 → NUS → 启发式)。

**只下发支持**:若模块**没有 notify 特征**,连接不会失败 —— 仅打印
`警告:未找到 notify 特征,只能下发、无法接收回传`,写入功能照常可用。

---

## 九、`config.py` 配置项

| 分组 | 项 | 默认 | 说明 |
| :--- | :--- | :--- | :--- |
| 链路 | `LINK_KIND` | `"serial"` | `serial` / `ble` |
| 串口 | `SERIAL_PORT` | `""` | 空=自动 |
| | `SERIAL_BAUDRATE` | **115200** | 需与模块/MCU 一致 |
| | `SERIAL_TIMEOUT` / `SERIAL_WRITE_TIMEOUT` | 0.2 / 2.0 | 秒 |
| 连接 | `SCAN_TIMEOUT` | 10.0 | BLE 扫描 |
| | `CONNECT_OP_TIMEOUT` | 40.0 | BLE GATT 操作 |
| | `CONNECT_MAX_ATTEMPTS` / `CONNECT_RETRY_DELAY` | 5 / 2.0 | 重试 |
| | `RECONNECT_ON_DROP` | True | 掉线自动重连 |
| 协议 | `ENCODING` / `LINE_END` | utf-8 / `"\n"` | |
| UI | `VEL_UI_MAX` | 2.0 | 速度滑块上限 m/s |
| | `DEG_UI_MIN/MAX` | ±180 | 方向角范围 |
| | `OMEGA_UI_MIN/MAX` | ±6.28 | 自转范围 |

---

## 十、`frontend.py` — `App`

```python
App()            # 构造:启动 asyncio 后台线程 + 建界面
app.mainloop()   # 阻塞运行
main()           # 模块级入口(console_scripts 指向)
```

| 成员 | 说明 |
| :--- | :--- |
| `backend: ChassisLink` | 当前链路 |
| `link_kind: StringVar` | `"serial"` / `"ble"` |
| `speed_var` / `deg_var` / `omega_var` | 三个控制量 |
| `addr_var` | 目标(COM 号 / 地址) |

| 方法 | 说明 |
| :--- | :--- |
| `on_connect()` / `on_disconnect()` | 连接/断开(含重复点击防护) |
| `on_send_vel()` | 发送速度矢量 |
| `on_stop()` | 急停 |
| `on_send_set()` / `on_send_raw()` | SET / 手动行 |
| `on_self_check()` | 自检(串口列表 / BLE 扫描) |
| `log_sys/tx/rx/err(text)` | 写日志 |

---

## 十一、只下发模式(不接收回传)

**结论:当前架构完全支持只下发,无需任何改动、无兼容风险。**

### 11.1 为什么支持(设计依据)

| 层 | 依据 |
| :--- | :--- |
| 协议 | 下发(`VEL/STOP/SET`)与回传(`TEL`)是**独立方向**;MCU 不实现 `TEL` 不影响收指令 |
| `ChassisLink` | `on_line` / `on_state` 均为**可选参数**,内部有 `if self._on_line:` / `if self._on_state:` 守卫,传 `None` 即静默 |
| `SerialBackend` | 发送走 `send()` → `asyncio.to_thread(ser.write)`;接收是**独立读线程**,两者完全解耦 |
| `BLEBackend` | `_resolve_characteristics()` 只强制要求**写特征**;缺 notify 仅打警告,连接不失败 |
| `App`(GUI) | 无回传时日志区不显示 RX 行,发送/连接功能不受影响 |

**已实测**:HC-06 未接 MCU 时,连续发送 `VEL:500,900,0` 全部成功(0 失败),
30 秒基准测试中 0 掉线 —— 对应"对端不回传"场景。

### 11.2 使用方式

**脚本(显式忽略接收)**

```python
import asyncio
from ble_chassis_control.backend_serial import SerialBackend

async def main():
    # 不传 on_line → 收到的数据被静默丢弃(仍会组帧,但不回调)
    link = SerialBackend("COM15", 115200, on_state=lambda s: print(s))
    await link.connect()
    await link.send_vel(0.5, 90, 0)
    await link.disconnect()

asyncio.run(main())
```

**GUI**:直接使用 `发送速度` / `急停` 按钮即可,日志区没有 RX 行属正常现象。

### 11.3 行为对照表

| 场景 | 只下发时的表现 |
| :--- | :--- |
| 连接 | ✅ 正常(串口/BLE 均不受影响) |
| 发送 `VEL` / `STOP` / `SET` | ✅ 正常,不依赖任何回传 |
| 日志 | 只有 TX 与系统行,无 RX 行 |
| 状态栏 | 正常显示"已连接 (COM15@115200)" |
| BLE 模块无 notify 特征 | ⚠️ 打一条警告,连接与写入仍可用 |
| 断线检测 | ⚠️ **仅靠写失败/串口异常**,无心跳超时机制(见 11.4) |

### 11.4 已知限制(只下发模式特有)

| 限制 | 影响 | 建议 |
| :--- | :--- | :--- |
| **无应用层心跳** | 链路"假连接"(物理断了但系统未报)无法及时发现 | 需要时加周期性 `SET:PING:1` 或依赖 MCU 回传 |
| **无发送确认** | 串口/BLE 写入成功 ≠ MCU 已执行 | 关键动作建议 MCU 回 `ACK` |
| **无法感知 MCU 状态** | 不知道底盘是否就绪、是否有故障 | 若需状态,启用最简 `TEL:` 上报 |

> 以上限制**不影响手动遥控**;若做自动驾驶/闭环控制,建议启用轻量回传。

### 11.5 推荐最小回传(可选,后期再加)

若将来想低成本加回传,MCU 侧只需每 100ms 输出一行:

```text
TEL:V:500,DEG:900,OMEGA:0
```

上位机日志会自动结构化显示为 `TEL V=500, DEG=900, OMEGA=0`。

---

## 十二、异常

```python
class LinkError(Exception)   # link.py;BLEError 为其别名
```

| 场景 | 消息要点 |
| :--- | :--- |
| 串口被占用 | 提示关闭 VOFA+/串口助手 |
| 串口不存在 | 提示检查配对与设备管理器 |
| 未连接时发送 | `"串口未连接"` / `"未连接"` |
| BLE 超时 | 提示占用/休眠/适配器 |

---

## 十三、典型使用示例

### 脚本方式(只下发)

```python
import asyncio
from ble_chassis_control.backend_serial import SerialBackend

async def main():
    link = SerialBackend("COM15", 115200, on_state=lambda s: print("状态:", s))
    await link.connect()
    await link.send_vel(0.5, 90, 0)     # 0.5 m/s 朝 90°
    await asyncio.sleep(1)
    await link.send_vel(0, 0, 1.57)     # 原地自转
    await asyncio.sleep(1)
    await link.send_stop()
    await link.disconnect()

asyncio.run(main())
```

### 脚本方式(带回传)

```python
link = SerialBackend("COM15", 115200,
                     on_line=lambda t, p: print("RX:", t),
                     on_state=lambda s: print("状态:", s))
```

### 纯协议(无硬件)

```python
from ble_chassis_control.protocol import Protocol
Protocol.encode_vel(0.8, 45, -0.5)                     # b'VEL:800,450,-500\n'
Protocol.parse_line("TEL:BAT:7.4").params["values"]    # {'BAT': 7.4}
```
