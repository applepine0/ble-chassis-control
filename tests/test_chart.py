"""WheelChart 数据逻辑测试(用 tkinter 桩,无需真实显示环境)。

验证:环形缓冲、保留最新、绘制产出、清空、空数据容错、纵轴量程。
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


# ---------------------------------------------------------------------------
# tkinter 桩:必须在 import chart 之前装好
# ---------------------------------------------------------------------------
class _FakeCanvas:
    def __init__(self, *a, **k):
        self.items = []

    def winfo_width(self):
        return 400

    def winfo_height(self):
        return 150

    def delete(self, *a):
        self.items.clear()

    def create_line(self, *a, **k):
        self.items.append(("line", a, k))

    def create_text(self, *a, **k):
        self.items.append(("text", a, k))

    def bind(self, *a, **k):
        pass

    def grid(self, *a, **k):
        pass


class _FakeFrame:
    """ttk 风格控件的桩。

    ⚠️ 关键:ttk.Frame 等 styled widget **不支持 cget("background")**,
    真实环境会抛 TclError: unknown option "-background"。
    这里如实模拟该行为,以便捕获此类错误(曾因桩太宽松而漏掉)。
    """

    #: 允许 cget 的选项(ttk styled widget 的 background 不在其中)
    _ALLOWED_CGET = ()

    def __init__(self, *a, **k):
        # 校验传入选项:若给 ttk 控件传了 background 也应报错(与真实一致)
        for key in k:
            if key in ("background", "bg"):
                # tk.Label 允许;ttk 控件不允许 —— 由子类覆盖 _ALLOWED_CGET 决定
                if "background" not in self._ALLOWED_CGET:
                    raise TypeError(
                        f"unknown option '-{key}' (ttk widget 不支持背景色)"
                    )

    def columnconfigure(self, *a, **k):
        pass

    def rowconfigure(self, *a, **k):
        pass

    def grid(self, *a, **k):
        pass

    def cget(self, key, *a):
        key = str(key).lstrip("-")
        if key not in self._ALLOWED_CGET:
            raise RuntimeError(f"unknown option '-{key}'")
        return "#dddddd"


class _FakeTkLabel(_FakeFrame):
    """tk.Label 支持背景色。"""

    _ALLOWED_CGET = ("background",)


class _FakeTtkLabel(_FakeFrame):
    """ttk.Label 不支持 background。"""

    _ALLOWED_CGET = ()


def _install_tk_stub() -> None:
    tk = types.ModuleType("tkinter")
    tk.Canvas = _FakeCanvas
    tk.Label = _FakeTkLabel                      # tk.Label:支持 bg
    tk.StringVar = lambda **k: types.SimpleNamespace(get=lambda: "", set=lambda v: None)
    tk.BooleanVar = lambda **k: types.SimpleNamespace(get=lambda: True, set=lambda v: None)
    ttk = types.ModuleType("tkinter.ttk")
    ttk.Frame = _FakeFrame                       # ttk:不支持 bg
    ttk.Label = _FakeTtkLabel
    for name in ("Checkbutton", "Button", "LabelFrame"):
        setattr(ttk, name, _FakeFrame)
    tk.ttk = ttk
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk


def _load_chart():
    _install_tk_stub()
    import importlib

    return importlib.import_module("ble_chassis_control.chart")


NAMES = ("右前", "左前", "左后", "右后")


def test_ring_buffer() -> None:
    """环形缓冲应限制容量并保留最新数据。"""
    ch = _load_chart()
    c = ch.WheelChart(None, names=NAMES, unit="rpm", maxlen=10)
    for i in range(25):
        c.push((i, -i, 0, i * 2))
    lens = [len(d) for d in c._series]
    assert lens == [10, 10, 10, 10], lens
    assert c._series[0][-1] == 24, "应保留最新值"
    assert c._series[1][-1] == -24
    print(f"1) 环形缓冲(推 25 帧 / 容量 10): 长度={lens},最新={c._series[0][-1]}: OK")


def test_partial_push() -> None:
    """推送长度不足 4 时不应崩溃(只填已给的轮位)。"""
    ch = _load_chart()
    c = ch.WheelChart(None, names=NAMES, maxlen=10)
    c.push((1.0, 2.0))
    assert len(c._series[0]) == 1 and len(c._series[1]) == 1
    assert len(c._series[2]) == 0 and len(c._series[3]) == 0
    print("2) 部分数据推送(仅 2 轮)容错: OK")


def test_draw_produces_curves() -> None:
    """绘制应产出四条曲线(颜色区分)+ 网格/刻度。"""
    ch = _load_chart()
    c = ch.WheelChart(None, names=NAMES, unit="rpm", maxlen=100)
    for i in range(50):
        c.push((100 + i, -50 - i, 0, 200 + i))
    c._draw(force=True)
    lines = [it for it in c.canvas.items if it[0] == "line"]
    texts = [it for it in c.canvas.items if it[0] == "text"]
    for color in ch.WHEEL_COLORS:
        assert any(color in str(it[2]) for it in lines), f"缺颜色 {color} 的曲线"
    assert len(texts) >= 5, "应有刻度与单位标注"
    print(f"3) 绘制产出: {len(lines)} 线 + {len(texts)} 文本,四色曲线齐全: OK")


def test_clear_and_empty_draw() -> None:
    """清空后数据归零;空数据绘制不应报错。"""
    ch = _load_chart()
    c = ch.WheelChart(None, names=NAMES, maxlen=10)
    for i in range(5):
        c.push((i, i, i, i))
    c.clear()
    assert all(len(d) == 0 for d in c._series)
    c._draw(force=True)          # 空数据
    print("4) 清空 + 空数据绘制容错: OK")


def test_zero_line_visible() -> None:
    """有正负数据时,零线应在量程内(过零可见)。"""
    ch = _load_chart()
    c = ch.WheelChart(None, names=NAMES, maxlen=10)
    for v in (100.0, -100.0):
        c.push((v, v, v, v))
    allv = [x for d in c._series for x in d]
    assert min(allv) < 0 < max(allv)
    c._draw(force=True)
    # 零线使用虚线
    lines = [it for it in c.canvas.items if it[0] == "line"]
    assert any(it[2].get("dash") for it in lines), "应有零线(虚线)"
    print("5) 正负数据下零线可见(虚线): OK")


def test_single_sample_no_crash() -> None:
    """只有 1 个采样点时不应尝试连线。"""
    ch = _load_chart()
    c = ch.WheelChart(None, names=NAMES, maxlen=10)
    c.push((10.0, 10.0, 10.0, 10.0))
    c._draw(force=True)          # n<2,应跳过曲线
    print("6) 单采样点不连线(容错): OK")


def test_no_ttk_background_option() -> None:
    """回归:不得对 ttk 控件使用 background/bg(真实环境抛 TclError)。

    桩已如实模拟:ttk 控件传 bg → TypeError;cget('background') → RuntimeError。
    """
    ch = _load_chart()
    # 构造过程若使用非法选项,会在此抛错
    ch.WheelChart(None, names=NAMES, unit="rpm", maxlen=10)
    # 直接验证桩行为确实会拦截(确保测试本身有效)
    import tkinter as tk
    try:
        tk.Label(None, text="x", bg="#fff")          # tk.Label 允许
    except TypeError:
        raise AssertionError("tk.Label 应允许背景色")
    try:
        tk.ttk.Frame(None, background="#fff")        # ttk 不允许
    except TypeError:
        pass
    else:
        raise AssertionError("桩未拦截 ttk 背景色,测试无效")
    print("7) 回归:不使用 ttk 不支持的 background 选项: OK")


if __name__ == "__main__":
    test_ring_buffer()
    test_partial_push()
    test_draw_produces_curves()
    test_clear_and_empty_draw()
    test_zero_line_visible()
    test_single_sample_no_crash()
    test_no_ttk_background_option()
    print("\nWheelChart 测试全部通过 ✓")
