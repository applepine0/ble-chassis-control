"""二进制四轮速度帧(方案 B,11B)测试:编解码、CRC、流式解析、丢帧统计。

单位 rpm(缩放 1);轮序:右前 → 左前 → 左后 → 右后(俯视逆时针)。

帧格式:[0]=0xAA [1]=seq [2:10]=4×int16 LE [10]=crc8(0x07)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ble_chassis_control import config  # noqa: E402
from ble_chassis_control.protocol import (  # noqa: E402
    FRAME_HEAD,
    FRAME_LEN,
    WHEEL_COUNT,
    Protocol,
    StreamDemux,
    WheelFrameParser,
    crc8,
    decode_wheel_frame,
    encode_wheel_frame,
)


def mk(seq: int, vals) -> bytes:
    return encode_wheel_frame(vals, seq=seq)


def test_crc8_standard_vector() -> None:
    """CRC-8/ATM 标准测试向量。"""
    assert crc8(b"123456789") == 0xF4, hex(crc8(b"123456789"))
    assert crc8(b"") == 0x00
    print("1) CRC-8/ATM 标准向量 (b'123456789' -> 0xF4): OK")


def test_frame_layout() -> None:
    """帧长 = 11,帧头 = 0xAA,CRC 位于末字节。"""
    f = mk(7, [1.5, -2.25, 0.0, 3.75])
    assert len(f) == FRAME_LEN == 11, len(f)
    assert f[0] == FRAME_HEAD == 0xAA
    assert f[1] == 7
    assert crc8(f[:-1]) == f[-1], "末字节应为前 10 字节的 CRC"
    print(f"2) 帧结构(11B,头 0xAA,seq=7,CRC 末字节): {f.hex(' ')}: OK")


def test_roundtrip() -> None:
    """编码→解码应还原物理量(rpm,整数)。"""
    vals = [120.0, -250.0, 0.0, 385.0]
    d = decode_wheel_frame(mk(3, vals))
    assert d is not None
    assert d.seq == 3
    for a, b in zip(d.speeds, vals):
        assert abs(a - b) < 1e-9, (a, b)
    assert d.unit == config.WHEEL_SPEED_UNIT
    print(f"3) 编解码回环 {vals} -> {d.speeds} {d.unit}: OK")


def test_wheel_order_labeling() -> None:
    """轮序应为:右前 → 左前 → 左后 → 右后。"""
    d = decode_wheel_frame(mk(0, [100.0, 200.0, 300.0, 400.0]))
    assert d is not None
    labels = d.labeled()
    assert [n for n, _ in labels] == ["右前", "左前", "左后", "右后"], labels
    assert [v for _, v in labels] == [100.0, 200.0, 300.0, 400.0], labels
    assert d.unit == "rpm"
    print(f"4) 轮序标注 {labels}: OK")


def test_crc_rejects_corruption() -> None:
    """任意单字节篡改都应被 CRC 拒绝。"""
    f = bytearray(mk(1, [1.0, 2.0, 3.0, 4.0]))
    for i in range(len(f) - 1):        # 不动 CRC 本身
        bad = bytearray(f)
        bad[i] ^= 0xFF
        assert decode_wheel_frame(bytes(bad)) is None, f"第 {i} 字节篡改未被发现"
    # 长度不符
    assert decode_wheel_frame(bytes(f[:-1])) is None
    assert decode_wheel_frame(b"\x55" + bytes(f[1:])) is None
    print("5) CRC 拒绝篡改(10 个位置逐一验证)+ 长度/帧头检查: OK")


def test_parser_whole_and_split() -> None:
    """整帧与半包(逐字节)喂入都应正确解析。"""
    got: list = []
    p = WheelFrameParser(on_frame=lambda f: got.append(f.seq))
    p.feed(mk(0, [1, 1, 1, 1]) + mk(1, [2, 2, 2, 2]))
    assert got == [0, 1], got
    # 逐字节喂入第 2 帧
    f2 = mk(2, [3, 3, 3, 3])
    for b in f2[:-1]:
        p.feed(bytes([b]))
    assert got == [0, 1], f"半包不应提前出帧: {got}"
    p.feed(f2[-1:])
    assert got == [0, 1, 2], got
    print("6) 解析器:整帧 + 半包拼接 + 逐字节喂入: OK")


def test_parser_loss_detection() -> None:
    """seq 跳变应推断出丢帧数。"""
    p = WheelFrameParser()
    p.feed(mk(0, [0, 0, 0, 0]))
    p.feed(mk(1, [0, 0, 0, 0]))
    assert p.stats.frames_lost == 0
    p.feed(mk(5, [0, 0, 0, 0]))       # 丢了 2,3,4
    assert p.stats.frames_lost == 3, p.stats.frames_lost
    assert p.stats.frames_ok == 3
    assert 0.49 < p.stats.loss_rate < 0.51, p.stats.loss_rate
    print(f"7) 丢帧检测: 跳过 2,3,4 -> 丢帧={p.stats.frames_lost},"
          f" 丢帧率={p.stats.loss_rate:.0%}: OK")


def test_parser_seq_wraparound() -> None:
    """255 -> 0 回绕不应被误判为丢 255 帧。"""
    p = WheelFrameParser()
    p.feed(mk(255, [1, 1, 1, 1]))
    p.feed(mk(0, [1, 1, 1, 1]))
    assert p.stats.frames_lost == 0, p.stats.frames_lost
    assert p.stats.frames_ok == 2
    print("8) seq 8bit 回绕(255->0)不误报丢帧: OK")


def test_parser_garbage_and_recovery() -> None:
    """前置垃圾字节、坏帧后应能恢复同步。"""
    p = WheelFrameParser()
    p.feed(b"junk!!" + mk(6, [6, 6, 6, 6]))
    assert p.stats.frames_ok == 1, p.stats
    assert p.stats.garbage_bytes >= 6, p.stats.garbage_bytes
    # 坏帧 + 好帧
    bad = bytearray(mk(7, [7, 7, 7, 7]))
    bad[5] ^= 0xFF
    p.feed(bytes(bad) + mk(8, [8, 8, 8, 8]))
    assert p.stats.frames_ok == 2, p.stats
    assert p.stats.crc_errors >= 1, p.stats
    print(f"9) 垃圾字节({p.stats.garbage_bytes}B)+坏帧恢复"
          f"(CRC 错 {p.stats.crc_errors} 次): OK")


def test_mixed_with_text_lines() -> None:
    """二进制帧与文本行混流时,帧解析不受干扰。"""
    frames: list = []
    p = WheelFrameParser(on_frame=lambda f: frames.append(f.seq))
    p.feed(mk(9, [1, 1, 1, 1]) + b"TEL:BAT:7.4\n" + mk(10, [2, 2, 2, 2]))
    assert frames == [9, 10], frames
    assert p.stats.frames_ok == 2
    print("10) 与 TEL 文本行混流互不干扰: OK")


def test_stream_demux_mixed_frames_and_text() -> None:
    """P0 回归:100Hz 帧 + 10Hz TEL 混流,帧与 TEL 都必须完整解析。"""
    frames: list[int] = []
    lines: list[str] = []
    d = StreamDemux(on_frame=lambda f: frames.append(f.seq), on_text=lines.append)
    for seq in range(100):
        # 10/20/-30/40 rpm 的帧内含 0x0A 字节,旧实现会污染文本行
        d.feed(mk(seq, [10.0, 20.0, -30.0, 40.0]))
        if seq % 10 == 0:
            d.feed(b"TEL:BAT:7.4,SPD:120\n")
    assert frames == list(range(100)), frames[:10]
    tels = [line for line in lines if line.startswith("TEL:")]
    assert len(tels) == 10, lines
    assert all(Protocol.parse_line(line).kind == "tel" for line in tels), tels
    assert d.stats.garbage_bytes == 0, d.stats.garbage_bytes
    assert d.stats.crc_errors == 0, d.stats.crc_errors
    print("12) StreamDemux 混流:100 帧 + 10 条 TEL 全部正确,无假杂散: OK")


def test_stream_demux_interleaved_and_partial() -> None:
    """文本/帧交错、半帧拼接都不能串行。"""
    frames: list[int] = []
    lines: list[str] = []
    d = StreamDemux(on_frame=lambda f: frames.append(f.seq), on_text=lines.append)
    d.feed(b"hello\n")
    f = mk(7, [1.0, 2.0, 3.0, 4.0])
    d.feed(f[:-1])
    assert frames == [], "半帧不应提前出帧"
    d.feed(f[-1:] + b"world\n")
    assert frames == [7], frames
    assert lines == ["hello", "world"], lines
    print("13) StreamDemux 文本/帧交错 + 半帧拼接: OK")


def test_stream_demux_crc_recovery() -> None:
    """坏帧后仍能恢复,且残字节不会污染后续 TEL 行。"""
    frames: list[int] = []
    lines: list[str] = []
    d = StreamDemux(on_frame=lambda f: frames.append(f.seq), on_text=lines.append)
    bad = bytearray(mk(1, [1.0, 1.0, 1.0, 1.0]))
    bad[5] ^= 0xFF
    d.feed(bytes(bad) + mk(2, [2.0, 2.0, 2.0, 2.0]) + b"TEL:BAT:7.4\n")
    assert frames == [2], frames
    assert d.stats.crc_errors >= 1, d.stats.crc_errors
    assert lines == ["TEL:BAT:7.4"], lines
    print(f"14) StreamDemux 坏帧恢复(CRC 错 {d.stats.crc_errors} 次)+ 后续 TEL 干净: OK")


def test_zero_and_extremes() -> None:
    """零值与 int16 边界值应正确编解码。"""
    for vals in ([0, 0, 0, 0], [32767.0, -32768.0, 32767.0, -32768.0]):
        d = decode_wheel_frame(mk(0, vals))
        assert d is not None
    # 超范围应被钳位而非溢出
    d = decode_wheel_frame(mk(0, [40000.0, -40000.0, 0, 0]))
    assert d is not None and abs(d.raw[0]) <= 32767
    print("11) 零值/int16 边界/超范围钳位: OK")


if __name__ == "__main__":
    test_crc8_standard_vector()
    test_frame_layout()
    test_roundtrip()
    test_wheel_order_labeling()
    test_crc_rejects_corruption()
    test_parser_whole_and_split()
    test_parser_loss_detection()
    test_parser_seq_wraparound()
    test_parser_garbage_and_recovery()
    test_mixed_with_text_lines()
    test_zero_and_extremes()
    test_stream_demux_mixed_frames_and_text()
    test_stream_demux_interleaved_and_partial()
    test_stream_demux_crc_recovery()
    print("\n二进制帧(方案 B,11B)测试全部通过 ✓")
