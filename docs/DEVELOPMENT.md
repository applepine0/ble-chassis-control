# 开发交接文档(DEVELOPMENT)

> 本文档面向**接手本项目的人或 AI 会话**。
> 目标:读完本文 + `README.md` + `docs/API.md`,即可无缝继续开发,无需回溯历史对话。
>
> 最后更新:2026-10-24

---

## 一、项目一句话

**Windows 底盘上位机**:通过 HC-06(经典蓝牙 SPP 虚拟串口)或 BLE 模块与底盘 MCU 通信,
下发速度矢量指令(`v` / `θ` / `ω`),接收四轮转速反馈并绘图。

技术栈:Python 3.13 + tkinter + pyserial(串口)+ Bleak(BLE),uv 管理,src layout。

---

## 二、当前状态(何处可用 / 何处待做)

### ✅ 已完成且已验证

| 模块 | 状态 | 验证方式 |
| :--- | :--- | :--- |
| `protocol.py` 文本协议(VEL/STOP/SET/TEL) | ✅ | `tests/test_smoke.py` |
| `protocol.py` 二进制帧(11B,CRC8+seq) | ✅ | `tests/test_wheel_frame.py`(11 项) |
| `link.py` 链路抽象(分帧/回调/统计) | ✅ | 上述两项覆盖 |
| `backend_serial.py` 串口链路 | ✅ | `tests/test_serial_backend.py`(6 项)+ **真机 HC-06 实测** |
| `motion.py` 定频运动下发(θ 积分) | ✅ | `tests/test_motion.py`(10 项)+ **真机验证** |
| `chart.py` 四轮转速曲线 | ✅ | `tests/test_chart.py`(7 项) |
| `frontend.py` GUI | ✅ | `tests/test_gui_flow.py`(8 路径) |
| `backend.py` BLE 链路 | ⚠️ 代码完成但**本机模块不兼容** | 见第五节 |

> ⚠️ 上表为历史验证结论。2026-10-24 复测发现混流、自动重连、`SET` 小数语义等问题,详见第十一节“已知问题”。

### ⏳ 待做(按优先级)

| # | 事项 | 说明 |
| :--- | :--- | :--- |
| 1 | **MCU 端联调** | 由底盘固件工程按本仓库协议文档实现;本仓库不提供 MCU 侧参考代码 |
| 2 | **验证端到端** | 上位机发 VEL → MCU 逆解 → 舵轮动作 → 回传四轮 rpm → 曲线显示 |
| 3 | **底盘能力限幅** | 目前只有 int16 钳位(±32.7 m/s),**无业务限幅**。建议加 `CHASSIS_MAX_V` / `CHASSIS_MAX_OMEGA` |
| 4 | **git 提交** | 提交本轮文档清理(见第七节) |
| 5 | 可选:延迟测量 | 需要 MCU 回传时间戳(13B 帧方案) |
| 6 | 可选:多链路并行 | 同时开 HC-06 控制 + USB-TTL 观察 |

---

## 三、关键设计决策(为什么这么做)

> ⚠️ 这些决策都有踩坑背景,**改动前请先读懂原因**,否则会重蹈覆辙。

### 决策 1:协议用**文本行 + 定点整数**(而非纯二进制)

```
VEL:<v_mmps>,<deg_tenths>,<omega_mradps>\n
```

- **理由**:透传链路上可被串口助手/VOFA 直接肉眼调试;MCU 端零浮点解析;
- **代价**:比二进制多几字节 —— 但 @115200 下带宽占用仅 2.6%,无所谓;
- **例外**:四轮速度**反馈**用二进制(11B),因为频率高(100Hz)且无需人读。

### 决策 2:协议层**不管**参考系 / 底盘类型

- 协议只传三个数,**不下沉语义**;
- 车头坐标系、正方向、舵轮/麦轮/差速逆解 —— **全部属于 MCU**;
- **好处**:换底盘类型只改 MCU,协议与上位机零修改。

### 决策 3:θ 由**上位机**积分,MCU **无状态**

```
上位机:  θ_body(t) = θ₀ + SIGN · ∫ω·dt
MCU:     收到 (v, θ, ω) 即算即用,不做任何积分
```

**⚠️ 最重要的一条**:MCU 端**绝不能**再对 ω 积分,否则角度累积两次 → 表现为"偏心转"。

### 决策 4:`SIGN = -1`(θ 反向补偿车体自转)

**推导**:

```
世界系方向 = θ_body(t) + ∫ω·dt        (车体自转贡献)
要求世界系方向恒定 θ₀:
    θ_body(t) = θ₀ − ∫ω·dt            ← SIGN = -1
```

| SIGN | 效果 |
| :--- | :--- |
| **−1(默认)** | 车沿**固定世界方向**平移 + 车体自转 |
| +1 | 世界系方向以 2ω 变化 → **"偏心转"**(踩过的坑) |

**配置**:`config.THETA_ACCUM_SIGN`,或 `MotionRunner(sign=±1)`。

### 决策 5:用**真实时钟**(`time.monotonic`)而非发送计数积分

- 定时器晚触发时按实际 Δt 补偿,**不会少积角度**;
- 实测:1 秒累积 −86.2°(期望 −90°,差值来自末帧调度余量);
- 不变量测试:逐帧验证 `θ_body + ∫ω·dt ≡ θ₀`,最大偏差 **0.41°**。

### 决策 6:二进制帧带 **seq**(丢帧检测)

| 字段 | 防的问题 |
| :--- | :--- |
| `crc8` | 帧内**比特错误** |
| `seq` | 整帧**丢失**(HC-06 缓冲溢出会静默丢数据) |

蓝牙 SPP 是字节流,**写成功 ≠ 对端收到** —— 没有 seq 就永远不知道丢了多少帧。
序号回绕用**掩码减法**处理:`lost = (seq - expected) & 0xFF`。

### 决策 7:接收侧**二进制帧与文本行并行解析**

互不干扰:帧由独立状态机处理(遇 `0xAA` 试定长,CRC 不过则丢首字节继续扫描),文本按 `\n` 分帧。

### 决策 8:曲线图**纯 tkinter Canvas**(不引 matplotlib)

- 零第三方依赖,免打包负担;
- 环形缓冲(`deque(maxlen)`)内存恒定;
- **节流重绘**(20fps),100Hz 数据不会拖垮界面。

---

## 四、架构与数据流

```
┌────────────────────────────────────────────────────────────┐
│ ① Protocol (protocol.py)       纯函数:编码 / 解析 / CRC / 帧状态机 │
├────────────────────────────────────────────────────────────┤
│ ② MotionRunner (motion.py)     θ 时间积分 + 定频下发           │
├────────────────────────────────────────────────────────────┤
│ ③ ChassisLink (link.py)        抽象:connect/send + 分帧/回调   │
├────────────────────────────────────────────────────────────┤
│ ④ SerialBackend / BLEBackend   具体链路                       │
├────────────────────────────────────────────────────────────┤
│ ⑤ App (frontend.py) + WheelChart (chart.py)                   │
└────────────────────────────────────────────────────────────┘
```

**线程模型(重要)**:

```
GUI 主线程 ──[run_coroutine_threadsafe]──> asyncio 后台守护线程
     ↑                                            │
     └────────[queue.Queue 轮询 50ms]─────────────┘
                   (回调绝不直接碰 tk 控件)
```

串口读线程另有独立线程,数据经 `_feed_bytes()` → 分帧 → 回调 → 队列 → GUI。

---

## 五、⚠️ 硬件现状与已知限制

### JDY-23(BLE)—— 本机不可用,已判死

**结论**:该模块(V81-02 非标固件)与 Windows 配对/安全层不兼容,PC 端**无法枚举 GATT**。

**证据**(12 组探针,详见 `README.md` 诊断章节):

- 模块广播正常(CONNECTABLE)、GATT 枚举全路径超时(含**裸 WinRT**绕开 Bleak);
- 配对失败;固件无 `AT+TYPE`/`AT+PASS` 等配对开关;
- **同机其他 BLE 设备 GATT 枚举正常** → PC 栈无罪;
- 手机 LightBlue 能连、能收发 → 模块 GATT 数据库完好;
- **仅 Windows/严格主机失败** → 配对层不兼容。

→ 该模块可继续用于手机端遥控,**PC 端请用 HC-06**。

### HC-06 —— 当前主用,已验证

| 项 | 值 |
| :--- | :--- |
| 类型 | 经典蓝牙 SPP(不是 BLE!) |
| 虚拟串口 | `COM15`(随连接出现/消失) |
| 波特率 | **115200**(已用 AT 改过,两端必须一致) |
| 通信状态 | ✅ 已实测双向通,30 秒基准 0 失败 0 掉线 |

**踩过的坑**:RFCOMM TDI 驱动带黄色叹号 → 蓝牙串口整体失效 → 卸载+重启后恢复。
另注册表有畸形配对记录(`" CHASSIS\r"`)。

### 带宽预算(@115200,双向共享 11520 B/s)

| 用途 | @20Hz | @100Hz |
| :--- | ---: | ---: |
| 下发 VEL(15B) | 2.6% | 13% |
| 反馈四轮(11B) | 4.2% | 9.5% |
| **合计** | **6.8%** | **34%** |

**结论**:115200 下带宽不再是瓶颈;建议控制 20~50Hz、反馈 100Hz。

---

## 六、如何运行与验证

```powershell
uv sync                              # 安装依赖(bleak + pyserial)

uv run ble-chassis-control           # 启动 GUI
uv run python -m ble_chassis_control # 等价

# --- 测试(本机:5 套全绿;motion 2 项见第十一节)---
uv run python tests/test_smoke.py         # 协议/后端/GUI 冒烟
uv run python tests/test_gui_flow.py      # GUI 8 条路径(含错误分支)
uv run python tests/test_serial_backend.py # 串口后端 6 项(含只下发模式)
uv run python tests/test_motion.py        # θ 积分/世界系定向 10 项
uv run python tests/test_wheel_frame.py   # 二进制帧 11 项(CRC/丢帧/回绕)
uv run python tests/test_chart.py         # 曲线图 7 项

# --- 工具 ---
uv run python tools/diagnose_serial.py        # 串口列表/占用诊断
uv run python tools/verify_serial.py COM15 115200 --hold   # 链路验证
uv run python tools/bench_hc06.py COM15 115200             # 吞吐基准
```

**GUI 操作**:

```
① 选 "串口 (HC-05)" → 串口号 COM15 → 波特率 115200 → [连接]
② 设 速度 m/s / 方向 deg / 自转 rad/s → [发送一次] 或 [启动运动]
③ [启动运动] = 定频持续下发(θ 按 ω 累积)
④ 四轮转速曲线自动显示(需 MCU 发 11B 帧)
```

**⚠️ 串口独占**:连接前关掉 VOFA+ / 串口助手,否则报"串口被占用"。

---

## 七、当前工作区改动(待提交)

本轮按交付要求删除了 MCU 侧参考实现,并清理文档引用:

```
M README.md
M docs/API.md
M docs/DEVELOPMENT.md
D tools/mcu/chassis_protocol.c
```

改动要点:

- 删除 `tools/mcu/chassis_protocol.c`(及空目录 `tools/mcu/`);
- README 移除“MCU 端参考实现”段落与文档导航行,API 章节数 14 → 13;
- API 删除“MCU 端接口(C 参考)”整章,原第 14 章顺延为第 13 章;
- 本文件移除待办、提交清单、下一步、关键文件表中的 C 文件引用;
- API 第 11 章子节编号 `9.1~9.5` 修正为 `11.1~11.5`;
- README/API 中“混流互不干扰”的表述改为“已知限制”(见第十一节第 1 项);
- 补充第十一节“已知问题”(本轮实测)。

提交建议:`docs: 移除 MCU 参考实现并清理文档引用`。

---

## 八、踩坑史(避免重蹈)

| # | 坑 | 现象 | 根因 | 修法 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **偏心转** | 车转得比预期快、转心偏移 | θ 被积分两次(上位机 + MCU) | MCU 不积分;SIGN=-1 反向补偿 |
| 2 | **读线程自动重连失效** | 掉线后不重连 | 读线程里 `asyncio.get_event_loop()` 必然抛错 | `run_coroutine_threadsafe` + 保存 loop 引用(历史修复;当前仍有自取消问题,见第十一节) |
| 3 | **连接按钮自锁** | 连上后按钮仍可点,二次连接失败 | 成功回调误调 `_ready_buttons()` | 成功只调 `_set_connected()` |
| 4 | **`except` 变量 NameError** | 连接失败时 GUI 崩 | lambda 延迟引用被 Python 销毁的 `exc` | 创建 lambda 时用默认参数绑定值 |
| 5 | **ttk 背景色报错** | `unknown option "-background"` | `ttk.Frame` 不支持 `cget("background")` | 图例用 `tk.Label`,不显式设背景 |
| 6 | **串口被占用重试无意义** | 白等 8 秒 | 占用是持久状态,重试无用 | `PermissionError` 立即放弃 |
| 7 | **HC-06 AT 不响应** | 所有 AT 无回复 | ①模块已连接(串口被切透传)②波特率被改过 | 红灯闪烁时发;8 档波特率扫描 |

---

## 九、下一步建议(给接手者)

1. **先读** `README.md`(协议 + 硬件结论)→ `docs/API.md`(全部 API);
2. **跑测试** 确认环境正常(本机 5 套全绿;motion 2 项受 Windows 定时粒度影响,见第十一节);
3. **接 MCU**:由底盘固件按 `docs/API.md` 第 2 章的线格式实现解析、CRC 与 11B 帧发送;
   本仓库只负责上位机与协议文档,不提供 MCU 侧代码;
4. **端到端验证**:GUI 发 VEL → 底盘动作 → 回传四轮 rpm → 曲线滚动;
5. **加业务限幅**(第 3 项待办),防止误操作把底盘甩飞;
6. **提交 git**。

---

## 十、关键文件速查

| 文件 | 作用 |
| :--- | :--- |
| `src/ble_chassis_control/protocol.py` | 协议定义(文本编码 + 二进制帧 + CRC) |
| `src/ble_chassis_control/motion.py` | θ 时间积分与定频下发(**SIGN 语义在此**) |
| `src/ble_chassis_control/link.py` | 链路抽象(分帧/回调/统计) |
| `src/ble_chassis_control/backend_serial.py` | 串口后端(HC-06 实际用这个) |
| `src/ble_chassis_control/chart.py` | 四轮转速曲线 |
| `src/ble_chassis_control/config.py` | **所有可调参数集中于此** |
| `docs/API.md` | 完整接口文档(13 章) |
| `tests/` | 6 套测试(本机 5 套全绿;motion 8/10,详见第十一节) |

---

## 十一、已知问题(本轮实测)

> 以下为 2026-10-24 在 Windows 本机复测结果。均不影响 HC-05 手动遥控;但第 1、2 项会直接影响回传显示与断线恢复,建议联调前优先处理。

| # | 问题 | 位置 | 实测/现象 | 建议 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **二进制帧污染文本行** | `link.py` `_feed_bytes()` | 100Hz 四轮帧 + 10Hz `TEL` 混流:100 帧正常,但 0/10 条 `TEL` 解析成功;日志出现 `ª...TEL:...` 乱码,`garbage_bytes` 把正常文本也计入 | 改为单一字节流解复用器(帧优先,剩余字节交给文本);补 `_feed_bytes` 混流回归测试 |
| 2 | **串口自动重连不生效** | `backend_serial.py` `_reconnect_loop()` / `disconnect()` | 强制读异常后等待 7s 仍为断开,状态停在“5 秒后自动重连串口 ...”;原因是 `disconnect()` 取消了正在运行的重连任务自身 | 仅手动断开时取消重连任务;重连路径不要调用会取消自身的 `disconnect()` |
| 3 | **`SET` 小数语义未定义** | `protocol.py` `encode_set()` / 协议文档 | 上位机默认发 `SET:MAX_SPEED:0.8`;按整数解析的固件会拒绝(`ERR:BAD_SET_NUM`) | 明确 `SET` 值为 ×1000 定点,或协议层改为整数 + 逐参数缩放 |
| 4 | **断开时 STOP 可能未发出** | `frontend.py` `on_disconnect()` | `motion.stop()` 与 `disconnect()` 并发,串口可能先关闭,运动协程的 STOP 写不出去 | 断开前等待运动协程退出并确认 STOP 写入(复用 `_shutdown_sequence`) |
| 5 | **定频下发频率偏低** | `motion.py` + Windows 定时粒度 | 20Hz 设定约 17 帧/s;50Hz 约 32.2Hz;`test_motion.py` 2 项因此失败 | 可选 `winmm.timeBeginPeriod(1)`;或调整测试容差并标注平台差异 |
| 6 | **`verify_serial.py` 执行两次** | `tools/verify_serial.py` | 文件尾部有两个 `if __name__ == "__main__"` 块,脚本会完整跑两遍 | 删除重复块,并把示例 `MOT:0,0` 改为 `VEL` |
