"""底盘通信协议(文本行协议,以换行结尾 —— 与 JDY-23 透传串口天然匹配)。

约定(PC -> MCU 下发):
    MOT:<左轮速度>,<右轮速度>\\n    差速底盘速度命令,单位 %,范围 [-100,100]
    STOP\\n                         急停(等效 MOT:0,0)
    SET:<参数名>:<数值>\\n           通用参数设置,例如 SET:MAX_SPEED:80

约定(MCU -> PC 回传):
    TEL:<键>:<值>[,<键>:<值>...]\\n  遥测,例如 TEL:BAT:7.4,SPD_L:12.3
    其余文本行原样显示(透传日志)。

注意:数值统一用 `%g` 格式,避免 "25.000000"。
MCU 端与 PC 端必须使用一致的换行与编码(默认 utf-8)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import config

# 标签常量
TAG_MOT = "MOT"
TAG_STOP = "STOP"
TAG_SET = "SET"
TAG_TEL = "TEL"

# 速度范围(百分比)
SPEED_MIN, SPEED_MAX = -100.0, 100.0


def _fmt(value: float) -> str:
    """数字 -> 紧凑字符串,例如 25.0 -> "25",25.5 -> "25.5"。"""
    return f"{value:g}"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class ParsedMessage:
    """从一行文本解析出的消息。"""

    raw: str                      # 原始行(不含换行)
    kind: str = "text"            # mot|stop|set|tel|text|unknown
    command: str | None = None    # 下发指令标签(小写)
    params: dict[str, Any] = field(default_factory=dict)  # 结构化字段
    error: str | None = None      # 解析错误说明(非 None 表示无法结构化解析)


class Protocol:
    """文本行协议:编码下发指令 / 解析回传数据。"""

    # ------------------------------------------------------------------
    # 编码(PC -> MCU)
    # ------------------------------------------------------------------
    @staticmethod
    def encode_mot(left: float, right: float) -> bytes:
        """差速底盘速度命令。速度单位:% ,范围 [-100,100]。"""
        l = _clamp(float(left), SPEED_MIN, SPEED_MAX)
        r = _clamp(float(right), SPEED_MIN, SPEED_MAX)
        return f"{TAG_MOT}:{_fmt(l)},{_fmt(r)}{config.LINE_END}".encode(config.ENCODING)

    @staticmethod
    def encode_stop() -> bytes:
        """急停。"""
        return f"{TAG_STOP}{config.LINE_END}".encode(config.ENCODING)

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

        # 差速速度
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