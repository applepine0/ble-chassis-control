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
    SET:<参数名>:<数值>\\n           通用参数设置,例如 SET:MAX_SPEED:0.8

兼容保留(旧差速接口):
    MOT:<左轮速度>,<右轮速度>\\n    差速底盘速度命令,单位 %,范围 [-100,100]

约定(MCU -> PC 回传):
    TEL:<键>:<值>[,<键>:<值>...]\\n  遥测,例如 TEL:BAT:7.4,SPD_L:12.3
    其余文本行原样显示(透传日志)。

注意:MCU 端与 PC 端必须使用一致的换行与编码(默认 utf-8)。
"""
from __future__ import annotations

import re
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
    def encode_set(name: str, value: float) -> bytes:
        """通用参数设置,例如 SET:MAX_SPEED:80。"""
        return f"{TAG_SET}:{name}:{_fmt(float(value))}{config.LINE_END}".encode(
            config.ENCODING
        )

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
                msg.error = "SET 格式应为 SET:<参数名>:<数值>"
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


def decode_line(data: bytes) -> str:
    """字节 -> 文本,兼容 utf-8 之外的常见透传编码。"""
    for enc in (config.ENCODING, "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")