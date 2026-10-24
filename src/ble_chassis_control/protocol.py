"""底盘通信协议(文本行协议,以换行结尾 —— 透传串口天然匹配)。

★ 主指令(方案 C:文本 + 定点整数,底盘类型无关)
    VEL:<v_mmps>,<deg_tenths>,<omega_mradps>\\n
        v_mmps      速度大小,单位 mm/s(0.5 m/s → 500)
        deg_tenths  运动方向角,单位 0.1°(90.0° → 900)
        omega_mradps 自转角速度,单位 mrad/s(1.570 rad/s → 1570)
     示例:VEL:500,900,0      → 0.5 m/s、90.0°、不转
          VEL:0,0,1570       → 原地以 1.570 rad/s 自转
          VEL:800,450,-500   → 0.8 m/s 朝 45.0°,同时 -0.5 rad/s 自转

    约定:全整数(MCU 端零浮点解析);缩放定点保证精度(1mm/s、0.1°、0.001rad/s)。
    角度按原值传输、不做归一化 —— 参考系(车头/世界)与正方向属于 MCU 侧运动学
    语义,不进协议层。底盘类型(舵轮/麦轮/差速)亦由 MCU 侧逆解,本协议只表达意图。

    STOP\\n                         急停(三量归零,短帧优先)
    SET:<参数名>:<整数>\\n           通用参数设置,按参数缩放(0.8 m/s → SET:MAX_SPEED:800)

兼容保留(旧差速接口):
    MOT:<左轮速度>,<右轮速度>\\n    差速底盘速度命令,单位 %,范围 [-100,100]

约定(MCU -> PC 回传):
    TEL:<键>:<值>[,<键>:<值>...]\\n  遥测,例如 TEL:BAT:7.4,SPD_L:12.3
    其余文本行原样显示(透传日志)。

    ★ 二进制定长帧(四轮速度反馈,方案 B,11 字节):
        [0]    0xAA            帧头
        [1]    seq             序号(0~255 循环,用于丢帧检测)
        [2:10] w0 w1 w2 w3     四轮转速,int16 小端,单位 rpm(缩放见 config)
        [10]   crc8            校验(多项式 0x07,覆盖 [0..9])

      轮序约定(俯视,车头朝上,逆时针递增):
        w0 = 右前(RF)  w1 = 左前(LF)  w2 = 左后(LR)  w3 = 右后(RR)

      与文本行**可混用**:接收侧按 0xAA 帧头优先识别,其余按文本行处理。

注意:MCU 端与 PC 端必须使用一致的换行与编码(默认 utf-8)。
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Any

from . import config

# 标签常量
TAG_VEL = "VEL"
TAG_MOT = "MOT"
TAG_STOP = "STOP"
TAG_SET = "SET"
TAG_TEL = "TEL"

# 速度范围(百分比,旧 MOT 接口)
SPEED_MIN, SPEED_MAX = -100.0, 100.0

# ---------------------------------------------------------------------------
# 二进制四轮速度帧(方案 B)
# ---------------------------------------------------------------------------
FRAME_HEAD = 0xAA          # 帧头
FRAME_LEN = 11             # 帧总长:1+1+8+1
WHEEL_COUNT = 4            # 轮数
_WHEEL_FMT = "<4h"         # 4 个 int16 小端

# ---------------------------------------------------------------------------
# VEL 定点缩放与范围
#   物理值 × SCALE_xxx = 线上整数值(四舍五入)
# ---------------------------------------------------------------------------
SCALE_V = 1000        # m/s    → mm/s
SCALE_DEG = 10        # deg    → 0.1°
SCALE_OMEGA = 1000    # rad/s  → mrad/s

# 线上整数范围(int16;留 0.1% 余量避免边界溢出)
VEL_MIN, VEL_MAX = 0, 32767                       # mm/s   : 0 ~ 32.767 m/s
DEG_MIN, DEG_MAX = -32768, 32767                  # 0.1°   : -3276.8° ~ +3276.7°
OMEGA_MIN, OMEGA_MAX = -32768, 32767              # mrad/s : ±32.767 rad/s


def _fmt(value: float) -> str:
    """数字 -> 紧凑字符串,例如 25.0 -> "25",25.5 -> "25.5"(兼容旧 MOT)。"""
    return f"{value:g}"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _fixed(value: float, scale: int, lo: int, hi: int) -> int:
    """物理值 → 定点整数(四舍五入 + 钳位)。"""
    scaled = int(round(float(value) * scale))
    return max(lo, min(hi, scaled))


@dataclass
class ParsedMessage:
    """从一行文本解析出的消息。"""

    raw: str                      # 原始行(不含换行)
    kind: str = "text"            # vel|mot|stop|set|tel|text|unknown
    command: str | None = None    # 下发指令标签(小写)
    params: dict[str, Any] = field(default_factory=dict)  # 结构化字段
    error: str | None = None      # 解析错误说明(非 None 表示无法结构化解析)


class Protocol:
    """文本行协议:编码下发指令 / 解析回传数据。"""

    # ------------------------------------------------------------------
    # 编码(PC -> MCU)
    # ------------------------------------------------------------------
    @staticmethod
    def encode_vel(speed_mps: float, deg: float, omega_radps: float) -> bytes:
        """速度矢量指令(主指令,底盘类型无关)。

        Args:
            speed_mps:   速度大小,m/s(标量,负值按 0 处理)
            deg:         运动方向角,度(按原值传输,不归一化)
            omega_radps: 自转角速度,rad/s(正负表转向)

        Returns:
            bytes: 形如 b"VEL:500,900,0\\n"
        """
        v = _fixed(max(0.0, float(speed_mps)), SCALE_V, VEL_MIN, VEL_MAX)
        d = _fixed(deg, SCALE_DEG, DEG_MIN, DEG_MAX)
        w = _fixed(omega_radps, SCALE_OMEGA, OMEGA_MIN, OMEGA_MAX)
        return f"{TAG_VEL}:{v},{d},{w}{config.LINE_END}".encode(config.ENCODING)

    @staticmethod
    def encode_stop() -> bytes:
        """急停(三量归零)。"""
        return f"{TAG_STOP}{config.LINE_END}".encode(config.ENCODING)

    @staticmethod
    def encode_mot(left: float, right: float) -> bytes:
        """差速底盘速度命令(兼容旧接口)。速度单位:% ,范围 [-100,100]。"""
        l = _clamp(float(left), SPEED_MIN, SPEED_MAX)
        r = _clamp(float(right), SPEED_MIN, SPEED_MAX)
        return f"{TAG_MOT}:{_fmt(l)},{_fmt(r)}{config.LINE_END}".encode(config.ENCODING)

    @staticmethod
    def encode_set(name: str, value: float, scale: int | None = None) -> bytes:
        """通用参数设置(方案 A:线上整数,按参数缩放)。

        Args:
            name:  参数名(如 ``MAX_SPEED``),按 ``config.SET_PARAMS`` 查缩放
            value: 物理值(如 0.8 m/s)
            scale: 缩放系数;``None`` 时查表,未登记参数按 ×1

        Returns:
            bytes: 形如 ``b"SET:MAX_SPEED:800\\n"``(0.8 m/s ×1000)
        """
        name = str(name).strip()
        s = int(config.set_scale(name) if scale is None else scale)
        if s <= 0:
            raise ValueError(f"SET 缩放必须为正整数: {scale!r}")
        raw = int(round(float(value) * s))
        # 与 MCU 端 int32 解析范围对齐,避免溢出
        raw = max(-(2**31), min(2**31 - 1, raw))
        return f"{TAG_SET}:{name}:{raw}{config.LINE_END}".encode(config.ENCODING)

    @staticmethod
    def encode_raw(text: str) -> bytes:
        """按原样发送一行(自动补换行,若未以换行结尾)。"""
        if not text.endswith(config.LINE_END):
            text = text + config.LINE_END
        return text.encode(config.ENCODING)

    # ------------------------------------------------------------------
    # 解析(MCU -> PC)
    # ------------------------------------------------------------------
    @staticmethod
    def parse_line(line: str) -> ParsedMessage:
        """解析一行(不含换行)。无法结构化时就近归类为 text / unknown。"""
        line = line.strip()
        if not line:
            return ParsedMessage(raw=line, kind="text")

        tag, _, rest = line.partition(":")
        tag_upper = tag.strip().upper()

        # 急停
        if tag_upper == TAG_STOP:
            return ParsedMessage(raw=line, kind="stop", command="stop")

        # 速度矢量(主指令)
        if tag_upper == TAG_VEL:
            msg = ParsedMessage(raw=line, kind="vel", command="vel")
            parts = [p.strip() for p in rest.split(",")]
            if len(parts) != 3:
                msg.error = "VEL 需要 3 个参数: VEL:<mm/s>,<0.1deg>,<mrad/s>"
                return msg
            try:
                v = int(parts[0])
                d = int(parts[1])
                w = int(parts[2])
            except ValueError:
                msg.error = "VEL 参数必须是整数(定点缩放值)"
                return msg
            msg.params["speed_mps"] = v / SCALE_V
            msg.params["deg"] = d / SCALE_DEG
            msg.params["omega_radps"] = w / SCALE_OMEGA
            msg.params["raw_int"] = (v, d, w)
            return msg

        # 差速速度(兼容旧接口)
        if tag_upper == TAG_MOT:
            msg = ParsedMessage(raw=line, kind="mot", command="mot")
            parts = [p.strip() for p in rest.split(",")]
            if len(parts) != 2:
                msg.error = "MOT 需要 2 个参数: MOT:<左>,<右>"
                return msg
            try:
                msg.params["left"] = float(parts[0])
                msg.params["right"] = float(parts[1])
            except ValueError:
                msg.error = "MOT 参数不是合法数字"
            return msg

        # 通用参数设置
        if tag_upper == TAG_SET:
            msg = ParsedMessage(raw=line, kind="set", command="set")
            name, _, value = rest.partition(":")
            msg.params["name"] = name.strip()
            try:
                msg.params["value"] = float(value.strip())
            except ValueError:
                msg.error = "SET 格式应为 SET:<参数名>:<整数>"
            return msg

        # 遥测回传
        if tag_upper == TAG_TEL:
            msg = ParsedMessage(raw=line, kind="tel", command="tel")
            pairs: dict[str, float] = {}
            bad: list[str] = []
            for item in re.split(r"[,;]", rest):
                item = item.strip()
                if not item:
                    continue
                key, _, val = item.partition(":")
                try:
                    pairs[key.strip()] = float(val.strip())
                except ValueError:
                    bad.append(item)
            msg.params["values"] = pairs
            if bad:
                msg.error = f"无法解析的遥测项: {', '.join(bad)}"
            return msg

        return ParsedMessage(raw=line, kind="unknown")


# ===========================================================================
# 二进制四轮速度帧(方案 B,11 字节)
# ===========================================================================

def crc8(data: bytes, poly: int = 0x07, init: int = 0x00) -> int:
    """CRC-8/ATM(多项式 0x07)。用于帧完整性校验。

    与 MCU 端实现必须一致;查表法或逐位法结果相同。
    """
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poly) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


@dataclass
class WheelFrame:
    """解码后的四轮速度帧。

    轮序(w0~w3,俯视车头朝上逆时针):右前 → 左前 → 左后 → 右后。
    """

    seq: int                       # 序号(0~255)
    raw: tuple[int, int, int, int]  # 四轮原始定点整数
    speeds: tuple[float, float, float, float]  # 换算后的物理量
    unit: str = ""                 # 单位标识(便于显示)

    def labeled(self) -> list[tuple[str, float]]:
        """带轮位名称的结果:[('右前', 120.0), ('左前', ...), ...]。"""
        names = config.WHEEL_NAMES
        return [(names[i] if i < len(names) else f"w{i}", v)
                for i, v in enumerate(self.speeds)]


def encode_wheel_frame(
    speeds: tuple[float, float, float, float] | list[float], seq: int = 0
) -> bytes:
    """编码四轮速度帧(主要给 MCU 参考 / 上位机做回环测试用)。

    Args:
        speeds: 四轮转速 rpm,顺序 = 右前,左前,左后,右后
                (按 config.WHEEL_SPEED_SCALE 缩放为 int16)
        seq:    序号 0~255

    Returns:
        11 字节帧:b'\\xaa' + seq + 4×int16(LE) + crc8
    """
    if len(speeds) != WHEEL_COUNT:
        raise ValueError(f"需要 {WHEEL_COUNT} 个轮速,实际 {len(speeds)}")
    raw = [
        max(-32768, min(32767, int(round(float(s) * config.WHEEL_SPEED_SCALE))))
        for s in speeds
    ]
    body = bytes([FRAME_HEAD, seq & 0xFF]) + struct.pack(_WHEEL_FMT, *raw)
    return body + bytes([crc8(body)])


def decode_wheel_frame(frame: bytes) -> WheelFrame | None:
    """解码 11 字节帧;校验失败或长度不符返回 None。"""
    if len(frame) != FRAME_LEN or frame[0] != FRAME_HEAD:
        return None
    if crc8(frame[:-1]) != frame[-1]:
        return None
    raw = struct.unpack(_WHEEL_FMT, frame[2:10])
    speeds = tuple(v / config.WHEEL_SPEED_SCALE for v in raw)
    return WheelFrame(
        seq=frame[1],
        raw=raw,                       # type: ignore[arg-type]
        speeds=speeds,                 # type: ignore[arg-type]
        unit=config.WHEEL_SPEED_UNIT,
    )


@dataclass
class LinkStats:
    """接收链路统计(丢帧/校验错误),供界面显示链路健康度。"""

    frames_ok: int = 0        # 成功解码帧数
    crc_errors: int = 0       # 校验失败次数
    frames_lost: int = 0      # 由 seq 推断的丢帧数
    garbage_bytes: int = 0    # 无法归类的字节数
    _last_seq: int | None = None

    def note_ok(self, seq: int) -> int:
        """记录一帧成功接收,返回本帧新推断出的丢帧数。"""
        lost = 0
        if self._last_seq is not None:
            expected = (self._last_seq + 1) & 0xFF
            if seq != expected:
                # 用掩码减法处理 8-bit 回绕(255 -> 0 不算丢 255 帧)
                lost = (seq - expected) & 0xFF
        self._last_seq = seq
        self.frames_ok += 1
        self.frames_lost += lost
        return lost

    def reset(self) -> None:
        self.frames_ok = self.crc_errors = self.frames_lost = self.garbage_bytes = 0
        self._last_seq = None

    @property
    def loss_rate(self) -> float:
        """丢帧率 = 丢失 / (成功 + 丢失)。"""
        total = self.frames_ok + self.frames_lost
        return self.frames_lost / total if total else 0.0


class WheelFrameParser:
    """字节流 → 四轮速度帧的状态机解析器。

    与文本行**可混用**:遇到 0xAA 尝试按定长帧解析;
    校验失败则把该字节当作其它数据(计入 garbage)继续扫描。

    用法::

        parser = WheelFrameParser(on_frame=cb, on_stats=cb2)
        parser.feed(data)          # 可多次喂入任意长度
    """

    def __init__(self, on_frame=None, on_stats=None) -> None:
        from typing import Callable

        self.on_frame: Callable[[WheelFrame], None] | None = on_frame
        self.on_stats: Callable[[LinkStats], None] | None = on_stats
        self.stats = LinkStats()
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        """喂入字节;内部缓存并尽量切出完整帧。"""
        self._buf += data
        while self._buf:
            # 1) 找帧头
            if self._buf[0] != FRAME_HEAD:
                idx = self._buf.find(bytes([FRAME_HEAD]))
                if idx < 0:
                    self.stats.garbage_bytes += len(self._buf)
                    self._buf.clear()
                    self._maybe_report()
                    return
                self.stats.garbage_bytes += idx
                del self._buf[:idx]

            # 2) 等够整帧
            if len(self._buf) < FRAME_LEN:
                return

            # 3) 尝试解码
            candidate = bytes(self._buf[:FRAME_LEN])
            frame = decode_wheel_frame(candidate)
            if frame is None:
                # 校验失败:丢弃首字节(可能是数据里的 0xAA),继续扫描
                self.stats.crc_errors += 1
                del self._buf[:1]
                self._maybe_report()
                continue

            # 4) 成功
            del self._buf[:FRAME_LEN]
            lost = self.stats.note_ok(frame.seq)
            if loss := lost:
                self._maybe_report(force=True)
            if self.on_frame:
                self.on_frame(frame)
            self._maybe_report()

    def _maybe_report(self, force: bool = False) -> None:
        if self.on_stats:
            self.on_stats(self.stats)


class StreamDemux:
    """字节流解复用:二进制帧优先,其余字节按文本行分帧。

    背景
    ----
    蓝牙 SPP/BLE 都是字节流,四轮速度帧(11B,载荷可含任意字节)与文本行
    (``VEL/TEL/ERR...``,以 ``\\n`` 结尾)共用同一条流。若把同一份字节同时
    喂给帧解析器和文本分帧器,帧内字节(尤其 ``0x0A``)会污染文本行,文本
    字节也会被帧解析器误计为“杂散”。

    本类用**单缓冲 + 帧优先**解决:

    * 缓冲以 ``0xAA`` 开头且凑够 11B → 尝试解码;成功则整帧消费;
    * 有帧头但 CRC 失败 → 丢弃整个 11B 候选帧继续扫描(计入 ``crc_errors``);
    * 非 ``0xAA`` 开头 → 取到下一个 ``0xAA``(或缓冲末尾)作为文本字节,
      按 ``\\n`` 切行后回调 ``on_text``。

    用法::

        demux = StreamDemux(on_frame=cb, on_text=line_cb, on_stats=stats_cb)
        demux.feed(data)      # 可多次喂入任意长度
    """

    _HEAD = bytes([FRAME_HEAD])

    def __init__(self, on_frame=None, on_stats=None, on_text=None) -> None:
        from typing import Callable

        self.on_frame: Callable[[WheelFrame], None] | None = on_frame
        self.on_stats: Callable[[LinkStats], None] | None = on_stats
        self.on_text: Callable[[str], None] | None = on_text
        self.stats = LinkStats()
        self._buf = bytearray()
        self._text = bytearray()

    def reset(self, *, clear_stats: bool = True) -> None:
        """清空接收缓冲(重连/重新订阅时调用)。"""
        self._buf.clear()
        self._text.clear()
        if clear_stats:
            self.stats.reset()

    def feed(self, data: bytes) -> None:
        """喂入字节;内部缓存并尽量切出完整帧与完整文本行。"""
        self._buf += data
        while self._buf:
            # 1) 帧头:凑够整帧再尝试,避免把半帧误判为文本
            if self._buf[0] == FRAME_HEAD:
                if len(self._buf) < FRAME_LEN:
                    return
                frame = decode_wheel_frame(bytes(self._buf[:FRAME_LEN]))
                if frame is None:
                    # 坏帧:整帧丢弃,避免残字节污染后面的文本行
                    self.stats.crc_errors += 1
                    self.stats.garbage_bytes += FRAME_LEN
                    del self._buf[:FRAME_LEN]
                    self._maybe_report()
                    continue
                del self._buf[:FRAME_LEN]
                lost = self.stats.note_ok(frame.seq)
                if lost:
                    self._maybe_report(force=True)
                if self.on_frame:
                    self.on_frame(frame)
                self._maybe_report()
                continue

            # 2) 文本:一直取到下一个帧头之前
            idx = self._buf.find(self._HEAD)
            if idx < 0:
                self._text += self._buf
                self._buf.clear()
            else:
                self._text += self._buf[:idx]
                del self._buf[:idx]
            self._drain_text()

    def _drain_text(self) -> None:
        while True:
            idx = self._text.find(b"\n")
            if idx < 0:
                return
            raw = bytes(self._text[:idx])
            del self._text[: idx + 1]
            if raw and self.on_text:
                self.on_text(decode_line(raw))

    def _maybe_report(self, force: bool = False) -> None:
        if self.on_stats:
            self.on_stats(self.stats)


def decode_line(data: bytes) -> str:
    """字节 -> 文本,兼容 utf-8 之外的常见透传编码。"""
    for enc in (config.ENCODING, "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")