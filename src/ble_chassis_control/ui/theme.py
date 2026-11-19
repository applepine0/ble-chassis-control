"""UI 主题层:只负责外观(DPI、sv-ttk、字体、配色)。

不依赖 frontend / backend;layout 与 chart 只消费这里的 ``Palette``。
"""

from __future__ import annotations

import ctypes
import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tkinter import font
from typing import Any

try:  # pragma: no cover - 运行时依赖,pyproject 已声明
    import sv_ttk
except Exception:  # pragma: no cover
    sv_ttk = None

# 自定义命名字体:修改一次,所有引用它们的控件同步更新
FONT_SMALL = "AppSmallFont"
FONT_LEGEND = "AppLegendFont"
FONT_BOLD = "AppBoldFont"
FONT_CHART = "AppChartFont"
FONT_CHART_SMALL = "AppChartSmallFont"
FONT_LOG = "TkFixedFont"

# 保持 Font 对象引用,否则 python 侧 GC 会把 Tcl 命名字体一起删掉
_FONT_OBJECTS: dict[str, font.Font] = {}

_FONT_MIN = 6
_FONT_MAX = 40

DEFAULT_UI_FAMILY = "Microsoft YaHei UI"
DEFAULT_UI_SIZE = 10
DEFAULT_MONO_FAMILY = "Consolas"
DEFAULT_MONO_SIZE = 10


@dataclass(frozen=True)
class Palette:
    """一套主题的完整配色。"""

    name: str
    bg: str
    panel: str
    text: str
    muted: str
    accent: str
    ok: str
    warn: str
    err: str
    danger_active: str

    log_bg: str
    log_sys: str
    log_tx: str
    log_rx: str
    log_err: str

    chart_bg: str
    chart_grid: str
    chart_axis: str
    chart_text: str
    wheel_colors: tuple[str, ...]


DARK = Palette(
    name="dark",
    bg="#1F2328",
    panel="#262B33",
    text="#E6EDF3",
    muted="#9DA7B3",
    accent="#4F8EF7",
    ok="#3FB950",
    warn="#D29922",
    err="#F85149",
    danger_active="#B62324",
    log_bg="#161B22",
    log_sys="#9DA7B3",
    log_tx="#58A6FF",
    log_rx="#3FB950",
    log_err="#F85149",
    chart_bg="#161B22",
    chart_grid="#30363D",
    chart_axis="#6E7681",
    chart_text="#C9D1D9",
    wheel_colors=("#FF6B6B", "#4DABF7", "#51CF66", "#FFA94D"),
)

LIGHT = Palette(
    name="light",
    bg="#F6F8FA",
    panel="#FFFFFF",
    text="#1F2328",
    muted="#57606A",
    accent="#0969DA",
    ok="#1A7F37",
    warn="#9A6700",
    err="#CF222E",
    danger_active="#A40E26",
    log_bg="#FFFFFF",
    log_sys="#57606A",
    log_tx="#0969DA",
    log_rx="#1A7F37",
    log_err="#CF222E",
    chart_bg="#FFFFFF",
    chart_grid="#D0D7DE",
    chart_axis="#8C959F",
    chart_text="#24292F",
    wheel_colors=("#D1242F", "#0969DA", "#1A7F37", "#BC4C00"),
)

PALETTES = {"dark": DARK, "light": LIGHT}


@dataclass(frozen=True)
class FontSettings:
    """用户可调的字体设置。"""

    ui_family: str = DEFAULT_UI_FAMILY
    ui_size: int = DEFAULT_UI_SIZE
    mono_family: str = DEFAULT_MONO_FAMILY
    mono_size: int = DEFAULT_MONO_SIZE

    @property
    def small_size(self) -> int:
        return max(_FONT_MIN, self.ui_size - 2)

    @property
    def chart_size(self) -> int:
        return max(_FONT_MIN, self.mono_size - 2)

    @property
    def chart_small_size(self) -> int:
        return max(_FONT_MIN, self.mono_size - 3)

    def clamped(self) -> FontSettings:
        return FontSettings(
            ui_family=(self.ui_family or DEFAULT_UI_FAMILY).strip(),
            ui_size=min(_FONT_MAX, max(_FONT_MIN, int(self.ui_size))),
            mono_family=(self.mono_family or DEFAULT_MONO_FAMILY).strip(),
            mono_size=min(_FONT_MAX, max(_FONT_MIN, int(self.mono_size))),
        )


def settings_path() -> Path:
    """字体设置文件位置:Windows 放 %APPDATA%,其他平台放 home。"""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "ble-chassis-control" / "ui.json"
    return Path.home() / ".ble-chassis-control" / "ui.json"


def load_font_settings(path: Path | None = None) -> FontSettings:
    target = path or settings_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return FontSettings(
            ui_family=str(data.get("ui_family", DEFAULT_UI_FAMILY)),
            ui_size=int(data.get("ui_size", DEFAULT_UI_SIZE)),
            mono_family=str(data.get("mono_family", DEFAULT_MONO_FAMILY)),
            mono_size=int(data.get("mono_size", DEFAULT_MONO_SIZE)),
        ).clamped()
    except Exception:
        return FontSettings()


def save_font_settings(settings: FontSettings, path: Path | None = None) -> Path:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(asdict(settings.clamped()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def font_families(root: Any) -> list[str]:
    try:
        return sorted(font.families(root), key=str.lower)
    except Exception:
        return []


def enable_dpi_awareness() -> None:
    """Windows 高 DPI 感知,避免界面发虚。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _pick_family(root: Any, candidates: tuple[str, ...]) -> str | None:
    try:
        families = set(font.families(root))
    except Exception:
        return None
    for name in candidates:
        if name in families:
            return name
    return None


def _ensure_font(root: Any, name: str, family: str, size: int, weight: str = "normal") -> None:
    try:
        f = font.nametofont(name)
    except Exception:
        f = font.Font(root=root, name=name, exists=False)
        _FONT_OBJECTS[name] = f
    f.configure(family=family, size=size, weight=weight)


def _configure_existing(
    root: Any, name: str, family: str, size: int, weight: str = "normal"
) -> None:
    """只改已存在的命名字体(sv-ttk 的字体不主动创建)。"""
    try:
        f = font.nametofont(name)
    except Exception:
        return
    f.configure(family=family, size=size, weight=weight)


def _clamp_size(size: int) -> int:
    return min(_FONT_MAX, max(_FONT_MIN, size))


def apply_fonts(root: Any, settings: FontSettings | None = None) -> FontSettings:
    """应用字体设置;返回实际生效的设置(family 可能被回退)。"""
    s = (settings or FontSettings()).clamped()
    ui_family = (
        _pick_family(root, (s.ui_family, "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"))
        or s.ui_family
    )
    mono_family = (
        _pick_family(root, (s.mono_family, "Cascadia Mono", "Consolas", "Courier New"))
        or s.mono_family
    )

    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        _ensure_font(root, name, ui_family, s.ui_size)
    _ensure_font(root, "TkFixedFont", mono_family, s.mono_size)

    _ensure_font(root, FONT_SMALL, ui_family, s.small_size)
    _ensure_font(root, FONT_LEGEND, ui_family, s.ui_size)
    _ensure_font(root, FONT_BOLD, ui_family, s.ui_size, weight="bold")
    _ensure_font(root, FONT_CHART, mono_family, s.chart_size)
    _ensure_font(root, FONT_CHART_SMALL, mono_family, s.chart_small_size)

    # sv-ttk 使用自己的命名字体,必须同步,否则 ttk 控件不跟手。
    # 默认 ui_size=10 时映射回 sv-ttk 原有视觉比例(body=7)。
    base = _clamp_size(s.ui_size - 3)
    sv_fonts = (
        ("SunValleyBodyFont", 0, "normal"),
        ("SunValleyBodyStrongFont", 0, "bold"),
        ("SunValleyBodyLargeFont", 2, "normal"),
        ("SunValleyCaptionFont", -1, "normal"),
        ("SunValleySubtitleFont", 3, "bold"),
        ("SunValleyTitleFont", 7, "bold"),
        ("SunValleyDisplayFont", 7, "bold"),
        ("SunValleyTitleLargeFont", 9, "bold"),
    )
    for name, delta, weight in sv_fonts:
        _configure_existing(root, name, ui_family, _clamp_size(base + delta), weight)

    return replace(s, ui_family=ui_family, mono_family=mono_family)


def apply_theme(root: Any, name: str = "dark") -> Palette:
    """应用 sv-ttk 主题并返回配色。

    必须在 Tk root 创建之后、构建控件之前调用。
    """
    theme = name if name in PALETTES else "dark"
    if sv_ttk is not None:
        try:
            sv_ttk.set_theme(theme)
        except Exception:
            pass
    palette = PALETTES[theme]
    apply_fonts(root, FontSettings())
    try:
        root.configure(bg=palette.bg)
    except Exception:
        pass
    return palette
