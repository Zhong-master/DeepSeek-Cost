"""把余额文字渲染成顶栏图标（PNG）。

Ubuntu 的 appindicator 扩展对“宽度 >= 1.5 倍高度”的图标会按原图尺寸直接铺在
面板上（indicator-multiload 的做法），所以我们可以把「¥123.45」这样的文字直接
画进 PNG，显示在系统栏右侧。
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

import cairo  # noqa: E402

# 颜色（RGB 0-1）
COLOR_NORMAL = (0.55, 0.93, 0.62)   # 正常：浅绿
COLOR_LOW = (1.00, 0.42, 0.42)      # 低于阈值：红
COLOR_STALE = (0.72, 0.72, 0.72)    # 数据过期（接口异常但保留旧值）：灰
COLOR_WARN = (1.00, 0.72, 0.35)     # 未登录 / 需要处理：橙
COLOR_USAGE = (1.00, 0.84, 0.25)    # 今日用量：黄

OUTLINE = (0.0, 0.0, 0.0, 0.80)


def format_amount(total: float, currency: str = "CNY") -> str:
    """把余额格式化成紧凑的显示文本。"""
    symbol = {"CNY": "¥", "USD": "$"}.get(str(currency).upper(), f"{currency} ")
    value = float(total)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 10000:
        unit = "万" if str(currency).upper() == "CNY" else "k"
        divisor = 10000.0 if unit == "万" else 1000.0
        return f"{sign}{symbol}{value / divisor:.1f}{unit}"
    return f"{sign}{symbol}{value:,.2f}"


def render_text_png(
    path: str | Path,
    text: str,
    height: int = 22,
    scale: int = 1,
    color: tuple[float, float, float] = COLOR_NORMAL,
    font: str = "Sans Bold",
    padding: int = 5,
) -> tuple[int, int]:
    """渲染单行文字到 PNG，返回 (宽, 高)。透明背景 + 深色描边，深浅色主题都清晰。"""
    height = max(12, int(height))
    scale = max(1, int(scale))
    pixel_height = height * scale
    pad = max(2, int(padding)) * scale

    # 先用一个临时 surface 量文字尺寸，自适应字号
    probe = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
    probe_cr = cairo.Context(probe)
    layout = PangoCairo.create_layout(probe_cr)
    layout.set_text(text, -1)
    target_ink = max(6.0, (height - 7) * scale)
    size = float(pixel_height)
    for _ in range(5):
        layout.set_font_description(Pango.FontDescription(f"{font} {size:.2f}px"))
        ink = layout.get_pixel_extents()[1]
        ink_height = ink.height or 1
        if abs(ink_height - target_ink) <= 0.5:
            break
        size = size * target_ink / ink_height
        size = min(size, pixel_height * 4)

    layout.set_font_description(Pango.FontDescription(f"{font} {size:.2f}px"))
    text_width, text_height = layout.get_pixel_size()
    width = int(text_width + pad * 2 + scale)
    height_px = pixel_height

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height_px)
    cr = cairo.Context(surface)
    PangoCairo.update_layout(cr, layout)
    cr.move_to(pad, (height_px - text_height) / 2.0)
    PangoCairo.layout_path(cr, layout)
    cr.set_line_width(2.2 * scale)
    cr.set_source_rgba(*OUTLINE)
    cr.stroke_preserve()
    cr.set_source_rgb(*color)
    cr.fill()

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(path))
    return width, height_px


class PanelIcon:
    """面板文字图标的缓存管理：每次刷新写入新文件名，避免 shell 端图片缓存。"""

    def __init__(self, directory: str | Path, keep: int = 3) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep = max(2, keep)
        self.path: str | None = None
        self._seq = 0

    def update(
        self,
        text: str,
        height: int = 22,
        scale: int = 1,
        color: tuple[float, float, float] = COLOR_NORMAL,
    ) -> str:
        self._seq += 1
        target = self.dir / f"panel-{os.getpid()}-{self._seq}.png"
        render_text_png(target, text, height=height, scale=scale, color=color)
        self.path = str(target)
        self._cleanup()
        return self.path

    def _cleanup(self) -> None:
        try:
            # 只清理本进程生成的图标，避免删掉其它实例正在显示的那张
            files = sorted(
                self.dir.glob(f"panel-{os.getpid()}-*.png"), key=lambda p: p.stat().st_mtime
            )
        except OSError:
            return
        for old in files[: max(0, len(files) - self.keep)]:
            if str(old) == self.path:
                continue
            try:
                old.unlink()
            except OSError:
                pass
