"""Display scaling and font selection for the desktop GUI.

Tk is not DPI-aware by default. On Windows that means either a window that occupies
a third of a 4K screen at 100%, or a bitmap-stretched blurry one at 150-200%. And a
hardcoded "Segoe UI" silently falls back to whatever Tk picks on Linux and macOS.
"""

from __future__ import annotations

from dataclasses import dataclass
import os


# Ordered by preference. The first family Tk actually has wins.
REGULAR_FAMILIES = (
    "Segoe UI Variable Text",
    "Segoe UI",
    "SF Pro Text",
    "Helvetica Neue",
    "Inter",
    "Cantarell",
    "Ubuntu",
    "Noto Sans",
    "DejaVu Sans",
)
EMPHASIS_FAMILIES = (
    "Segoe UI Semibold",
    "SF Pro Text Semibold",
    "Inter SemiBold",
)

MIN_WINDOW = (1040, 680)
MAX_WINDOW = (1680, 1050)
SCREEN_FRACTION = 0.72


@dataclass(frozen=True)
class FontScheme:
    """The families this machine actually has, and how to render emphasis with them."""

    regular: str
    emphasis: str
    emphasis_is_semibold: bool

    def regular_font(self, size: int) -> tuple[str, int]:
        return (self.regular, size)

    def emphasis_font(self, size: int) -> tuple:
        if self.emphasis_is_semibold:
            return (self.emphasis, size)
        return (self.emphasis, size, "bold")


def enable_dpi_awareness() -> bool:
    """Tell Windows this process scales itself. Must run before the first Tk window."""
    if os.name != "nt":
        return False
    import ctypes

    try:
        # 2 == PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except (AttributeError, OSError):
        pass
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False


def apply_tk_scaling(root) -> float:
    """Point-to-pixel scaling for the current display, so text keeps its physical size."""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001 - any Tk failure here must not stop the GUI
        return 1.0
    if dpi <= 0:
        return 1.0
    scaling = max(1.0, min(dpi / 72.0, 4.0))
    try:
        root.tk.call("tk", "scaling", scaling)
    except Exception:  # noqa: BLE001
        return 1.0
    return scaling


def resolve_font_scheme(root) -> FontScheme:
    import tkinter.font as tkfont

    try:
        available = {name.strip() for name in tkfont.families(root)}
    except Exception:  # noqa: BLE001
        available = set()
    regular = next((family for family in REGULAR_FAMILIES if family in available), "TkDefaultFont")
    emphasis = next((family for family in EMPHASIS_FAMILIES if family in available), "")
    if emphasis:
        return FontScheme(regular, emphasis, True)
    return FontScheme(regular, regular, False)


def preferred_window_geometry(screen_width: int, screen_height: int) -> tuple[int, int]:
    """A window sized from the actual screen, not from a constant tuned for one laptop.

    The comfortable minimum yields to the screen: on a display smaller than that
    minimum, a window larger than the screen has its controls off the edge, which is
    worse than a cramped one.
    """
    width = max(MIN_WINDOW[0], min(int(screen_width * SCREEN_FRACTION), MAX_WINDOW[0]))
    height = max(MIN_WINDOW[1], min(int(screen_height * SCREEN_FRACTION), MAX_WINDOW[1]))
    return min(width, max(320, screen_width - 40)), min(height, max(240, screen_height - 80))


def parse_window_geometry(value: str) -> tuple[int, int] | None:
    """Read a remembered "<width>x<height>" value, ignoring anything malformed."""
    text = (value or "").strip().casefold()
    if "x" not in text:
        return None
    width_text, _, height_text = text.partition("x")
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError:
        return None
    if width < MIN_WINDOW[0] or height < MIN_WINDOW[1]:
        return None
    if width > MAX_WINDOW[0] * 2 or height > MAX_WINDOW[1] * 2:
        return None
    return width, height


def format_window_geometry(width: int, height: int) -> str:
    return f"{int(width)}x{int(height)}"
