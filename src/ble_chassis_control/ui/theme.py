"""UI 主题层:只负责外观(DPI、sv-ttk、字体、配色)。

不依赖 frontend / backend;layout 与 chart 只消费这里的 ``Palette``。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from tkinter import font
from typing import Any

try:  # pragma: no cover - 运行时依赖,pyproject 已声明
    import sv_ttk
except Exception:  # pragma: no cover
    sv_ttk = None


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


def _configure_fonts(root: Any) -> None:
    family = _pick_family(root, ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"))
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            f = font.nametofont(name)
            if family:
                f.configure(family=family)
            f.configure(size=10)
        except Exception:
            continue
    try:
        font.nametofont("TkFixedFont").configure(family="Consolas", size=10)
    except Exception:
        pass


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
    _configure_fonts(root)
    try:
        root.configure(bg=palette.bg)
    except Exception:
        pass
    return palette
