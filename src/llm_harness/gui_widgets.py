"""Rounded, shadowed, animated widgets drawn on a Canvas.

Tk has no rounded corners, no shadows and no anti-aliasing: a ttk button is a
flat rectangle and a ttk Combobox still looks like Windows 2000. These widgets
draw themselves instead.

Corners are anti-aliased by hand. For each pixel of a corner square we measure
how much of it falls inside the corner disc and blend the surface colour with
the colour behind it by that coverage, then hand the result to Tk as a small
PhotoImage. Only the corners need this, so the cost does not grow with the size
of a card, and the images are cached per Tk interpreter.

No third-party dependency is used, which is the point: the product ships a bare
CPython plus Tk.
"""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Callable, Sequence


CORNER_SUPERSAMPLE = 4
ANIMATION_STEPS = 6
ANIMATION_INTERVAL_MS = 16


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(channel))):02x}" for channel in rgb)


def mix(start: str, end: str, ratio: float) -> str:
    """Blend two colours; ratio 0 returns start, 1 returns end."""
    ratio = max(0.0, min(1.0, ratio))
    left = hex_to_rgb(start)
    right = hex_to_rgb(end)
    return rgb_to_hex([a + (b - a) * ratio for a, b in zip(left, right)])


def _image_cache(widget: tk.Misc) -> dict[tuple, tk.PhotoImage]:
    """Cache corner images on the Tk interpreter, so they die with it."""
    root = widget._root()  # noqa: SLF001 - the documented way to reach the interpreter
    cache = getattr(root, "_hoh_corner_cache", None)
    if cache is None:
        cache = {}
        root._hoh_corner_cache = cache  # type: ignore[attr-defined]
    return cache


def corner_images(
    widget: tk.Misc, radius: int, fill: str, background: str
) -> tuple[tk.PhotoImage, tk.PhotoImage, tk.PhotoImage, tk.PhotoImage]:
    """Four anti-aliased corner tiles, in order: top-left, top-right, bottom-left, bottom-right."""
    key = (radius, fill, background)
    cache = _image_cache(widget)
    cached = cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    coverage = _corner_coverage(radius)
    palette = [mix(background, fill, value) for value in _COVERAGE_LEVELS]
    top_left = _coverage_image(widget, coverage, palette)
    top_right = _coverage_image(widget, [row[::-1] for row in coverage], palette)
    bottom_left = _coverage_image(widget, coverage[::-1], palette)
    bottom_right = _coverage_image(widget, [row[::-1] for row in coverage[::-1]], palette)
    images = (top_left, top_right, bottom_left, bottom_right)
    cache[key] = images  # type: ignore[assignment]
    return images


_COVERAGE_LEVELS = [index / (CORNER_SUPERSAMPLE**2) for index in range(CORNER_SUPERSAMPLE**2 + 1)]


def _corner_coverage(radius: int) -> list[list[int]]:
    """How much of each pixel of a top-left corner tile lies inside the disc, 0..N*N."""
    samples = CORNER_SUPERSAMPLE
    limit = radius * radius
    rows: list[list[int]] = []
    for y in range(radius):
        row: list[int] = []
        for x in range(radius):
            inside = 0
            for sub_y in range(samples):
                point_y = radius - (y + (sub_y + 0.5) / samples)
                for sub_x in range(samples):
                    point_x = radius - (x + (sub_x + 0.5) / samples)
                    if point_x * point_x + point_y * point_y <= limit:
                        inside += 1
            row.append(inside)
        rows.append(row)
    return rows


def _coverage_image(
    widget: tk.Misc, coverage: list[list[int]], palette: list[str]
) -> tk.PhotoImage:
    height = len(coverage)
    width = len(coverage[0]) if height else 0
    image = tk.PhotoImage(master=widget, width=width, height=height)
    data = " ".join("{" + " ".join(palette[value] for value in row) + "}" for row in coverage)
    image.put(data)
    return image


def paint_rounded(
    canvas: tk.Canvas,
    box: tuple[int, int, int, int],
    *,
    radius: int,
    fill: str,
    background: str,
    tag: str,
) -> None:
    """Draw a rounded rectangle: straight edges as rectangles, corners as blended tiles."""
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return
    radius = max(0, min(radius, width // 2, height // 2))
    if radius == 0:
        canvas.create_rectangle(left, top, right, bottom, fill=fill, outline="", tags=tag)
        return

    canvas.create_rectangle(left + radius, top, right - radius, bottom, fill=fill, outline="", tags=tag)
    canvas.create_rectangle(left, top + radius, left + radius, bottom - radius, fill=fill, outline="", tags=tag)
    canvas.create_rectangle(right - radius, top + radius, right, bottom - radius, fill=fill, outline="", tags=tag)

    top_left, top_right, bottom_left, bottom_right = corner_images(canvas, radius, fill, background)
    canvas.create_image(left, top, image=top_left, anchor="nw", tags=tag)
    canvas.create_image(right, top, image=top_right, anchor="ne", tags=tag)
    canvas.create_image(left, bottom, image=bottom_left, anchor="sw", tags=tag)
    canvas.create_image(right, bottom, image=bottom_right, anchor="se", tags=tag)


@dataclass(frozen=True)
class SurfaceStyle:
    """Colours a drawn surface needs to blend correctly against what is behind it."""

    background: str
    surface: str
    surface_hover: str
    shadow: str
    radius: int = 14


SHADOW_LAYERS = ((3, 5, 0.55), (2, 3, 0.35), (1, 2, 0.2))


class ElevatedCard(tk.Canvas):
    """A rounded card with a soft drop shadow that lifts slightly on hover.

    The content lives in `body`; the canvas sizes itself from it, so the card
    still participates in pack/grid the way the ttk frame it replaces did.
    """

    def __init__(self, master: tk.Misc, style: SurfaceStyle, body_factory: Callable[[tk.Misc], tk.Widget]) -> None:
        super().__init__(
            master,
            highlightthickness=0,
            borderwidth=0,
            background=style.background,
            takefocus=0,
        )
        self.style = style
        self._hover = 0.0
        self._animation: str | None = None
        self._requested_height: int | None = None
        self._window_width: int | None = None
        self.body = body_factory(self)
        self._window = self.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._body_resized, add="+")
        self.bind("<Configure>", self._canvas_resized, add="+")
        self.bind("<Enter>", lambda _event: self._animate_to(1.0), add="+")
        self.bind("<Leave>", lambda _event: self._animate_to(0.0), add="+")

    @property
    def _shadow_extent(self) -> tuple[int, int]:
        return max(offset for offset, _blur, _ratio in SHADOW_LAYERS), max(
            blur for _offset, blur, _ratio in SHADOW_LAYERS
        )

    def _body_resized(self, event: tk.Event) -> None:
        """Grow to fit the content vertically; never argue about width.

        Width flows one way only: the geometry manager gives the card a width, the
        card gives its body that width minus the shadow. Asking the body how wide it
        wants to be and then requesting that width back closes the loop, and a card
        in a uniform grid column then oscillates by a pixel forever.
        """
        right, bottom = self._shadow_extent
        height = event.height + bottom
        options: dict[str, int] = {}
        if height != self._requested_height:
            self._requested_height = height
            options["height"] = height
        if self.winfo_width() <= 1:
            options["width"] = event.width + right
        if options:
            self.configure(**options)
        self._repaint()

    def _canvas_resized(self, _event: tk.Event) -> None:
        right, _bottom = self._shadow_extent
        width = max(1, self.winfo_width() - right)
        if width == self._window_width:
            return
        self._window_width = width
        self.itemconfigure(self._window, width=width)
        self._repaint()

    def _animate_to(self, target: float, step: int = 0) -> None:
        if self._animation is not None:
            self.after_cancel(self._animation)
            self._animation = None
        start = self._hover
        if abs(target - start) < 0.01:
            return

        def advance(index: int) -> None:
            self._hover = start + (target - start) * (index / ANIMATION_STEPS)
            self._repaint()
            if index < ANIMATION_STEPS:
                self._animation = self.after(ANIMATION_INTERVAL_MS, advance, index + 1)
            else:
                self._animation = None

        advance(step + 1)

    def _repaint(self) -> None:
        self.delete("surface")
        width = self.winfo_width()
        height = self.winfo_height()
        right, bottom = self._shadow_extent
        if width <= right or height <= bottom:
            return
        card = (0, 0, width - right, height - bottom)
        for offset, blur, ratio in SHADOW_LAYERS:
            lift = 1.0 + self._hover * 0.5
            colour = mix(self.style.background, self.style.shadow, ratio * (0.6 + 0.4 * self._hover))
            paint_rounded(
                self,
                (
                    card[0] + int(offset * lift) - 1,
                    card[1] + int(offset * lift),
                    card[2] + int(offset * lift),
                    card[3] + int(blur * lift),
                ),
                radius=self.style.radius + 2,
                fill=colour,
                background=self.style.background,
                tag="surface",
            )
        surface = mix(self.style.surface, self.style.surface_hover, self._hover)
        paint_rounded(
            self,
            card,
            radius=self.style.radius,
            fill=surface,
            background=self.style.background,
            tag="surface",
        )
        self.tag_lower("surface", self._window)


@dataclass(frozen=True)
class ButtonStyle:
    background: str
    fill: str
    fill_hover: str
    fill_active: str
    foreground: str
    radius: int = 9
    padding: tuple[int, int] = (18, 11)
    anchor: str = "center"


class RoundedButton(tk.Canvas):
    """A button with rounded corners, a hover fade and a press state.

    It answers `cget("text")` and `configure(text=...)` so callers that read a
    button's label, and the layout smoke that measures it, keep working.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str,
        command: Callable[[], None] | None = None,
        style: ButtonStyle,
        font: Any,
        width: int | None = None,
        selected_style: ButtonStyle | None = None,
    ) -> None:
        super().__init__(
            master,
            highlightthickness=0,
            borderwidth=0,
            background=style.background,
            takefocus=1,
        )
        self.style = style
        self._base_style = style
        self._selected_style = selected_style
        self._selected = False
        self._text = text
        self._font = font
        self._fixed_width = width
        self._hover = 0.0
        self._pressed = False
        self._enabled = True
        self._animation: str | None = None
        self.command = command
        self._measure()
        self.bind("<Configure>", lambda _event: self._repaint(), add="+")
        self.bind("<Enter>", lambda _event: self._animate_to(1.0), add="+")
        self.bind("<Leave>", self._left, add="+")
        self.bind("<ButtonPress-1>", self._press, add="+")
        self.bind("<ButtonRelease-1>", self._release, add="+")
        self.bind("<Key-Return>", lambda _event: self._invoke(), add="+")
        self.bind("<Key-space>", lambda _event: self._invoke(), add="+")

    def _measure(self) -> None:
        metrics = tkfont.Font(root=self, font=self._font)
        pad_x, pad_y = self.style.padding
        text_width = max(metrics.measure(line) for line in self._text.split("\n")) if self._text else 0
        width = self._fixed_width if self._fixed_width is not None else text_width + pad_x * 2
        height = metrics.metrics("linespace") + pad_y * 2
        self.configure(width=max(1, width), height=max(1, height))

    def configure(self, cnf: dict | None = None, **kwargs: Any) -> Any:  # noqa: D102
        options = dict(cnf or {})
        options.update(kwargs)
        text = options.pop("text", None)
        state = options.pop("state", None)
        command = options.pop("command", None)
        options.pop("style", None)
        if command is not None:
            self.command = command
        if state is not None:
            self._enabled = str(state) not in {"disabled", tk.DISABLED}
        result = super().configure(**options) if options else None
        if text is not None:
            self._text = str(text)
            self._measure()
        if text is not None or state is not None:
            self._repaint()
        return result

    config = configure

    def cget(self, key: str) -> Any:  # noqa: D102
        if key == "text":
            return self._text
        if key == "state":
            return "normal" if self._enabled else "disabled"
        return super().cget(key)

    def set_selected(self, selected: bool) -> None:
        """Nav rows keep a persistent highlight; hover is layered on top of it."""
        if self._selected_style is None or selected == self._selected:
            return
        self._selected = selected
        self.style = self._selected_style if selected else self._base_style
        self._repaint()

    def invoke(self) -> None:
        self._invoke()

    def _invoke(self) -> None:
        if self._enabled and self.command is not None:
            self.command()

    def _left(self, _event: tk.Event) -> None:
        self._pressed = False
        self._animate_to(0.0)

    def _press(self, _event: tk.Event) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self.focus_set()
        self._repaint()

    def _release(self, event: tk.Event) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._repaint()
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if was_pressed and inside:
            self._invoke()

    def _animate_to(self, target: float) -> None:
        if not self._enabled:
            return
        if self._animation is not None:
            self.after_cancel(self._animation)
            self._animation = None
        start = self._hover
        if abs(target - start) < 0.01:
            return

        def advance(index: int) -> None:
            self._hover = start + (target - start) * (index / ANIMATION_STEPS)
            self._repaint()
            if index < ANIMATION_STEPS:
                self._animation = self.after(ANIMATION_INTERVAL_MS, advance, index + 1)
            else:
                self._animation = None

        advance(1)

    def _repaint(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            width = int(self.cget("width"))
            height = int(self.cget("height"))
        if self._pressed:
            fill = self.style.fill_active
        else:
            fill = mix(self.style.fill, self.style.fill_hover, self._hover)
        foreground = self.style.foreground
        if not self._enabled:
            fill = mix(self.style.background, fill, 0.45)
            foreground = mix(self.style.background, foreground, 0.45)
        offset = 1 if self._pressed else 0
        paint_rounded(
            self,
            (0, offset, width, height - (1 - offset)),
            radius=self.style.radius,
            fill=fill,
            background=self.style.background,
            tag="all",
        )
        if self.style.anchor == "w":
            position = (self.style.padding[0], height // 2 + offset)
        else:
            position = (width // 2, height // 2 + offset)
        self.create_text(
            *position,
            text=self._text,
            fill=foreground,
            font=self._font,
            anchor=self.style.anchor,
        )


@dataclass(frozen=True)
class SelectStyle:
    background: str
    field: str
    field_hover: str
    border: str
    foreground: str
    muted: str
    popup: str
    popup_hover: str
    accent: str
    radius: int = 9
    padding: tuple[int, int] = (12, 9)


class ModernSelect(tk.Canvas):
    """A drop-down that does not look like a Windows 2000 combobox.

    The field is drawn; the list is a borderless popup with hover highlighting
    and a check on the current value. Accepts the subset of the ttk.Combobox API
    this application actually uses: textvariable, values, state, and the
    <<ComboboxSelected>> virtual event.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        textvariable: tk.StringVar,
        values: Sequence[str] = (),
        style: SelectStyle,
        font: Any,
        width: int | None = None,
        height: int = 9,
        editable: bool = False,
    ) -> None:
        super().__init__(
            master,
            highlightthickness=0,
            borderwidth=0,
            background=style.background,
            takefocus=1,
        )
        self.style = style
        self.variable = textvariable
        self._values = tuple(values)
        self._font = font
        self._visible_rows = max(1, height)
        self._editable = editable
        self._enabled = True
        self._hover = 0.0
        self._open = False
        self._popup: tk.Toplevel | None = None
        self._animation: str | None = None
        self._entry: tk.Entry | None = None
        self._measure(width)
        if editable:
            self._entry = tk.Entry(
                self,
                textvariable=textvariable,
                font=font,
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                background=style.field,
                foreground=style.foreground,
                insertbackground=style.foreground,
            )
            self.create_window(style.padding[0], 0, window=self._entry, anchor="w", tags="entry")
        self.bind("<Configure>", lambda _event: self._repaint(), add="+")
        self.bind("<Enter>", lambda _event: self._animate_to(1.0), add="+")
        self.bind("<Leave>", lambda _event: self._animate_to(0.0), add="+")
        self.bind("<Button-1>", lambda _event: self.toggle(), add="+")
        self.bind("<Key-Return>", lambda _event: self.toggle(), add="+")
        self._trace = textvariable.trace_add("write", lambda *_: self._repaint())
        self.bind("<Destroy>", self._forget_trace, add="+")

    def _forget_trace(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        try:
            self.variable.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass
        self._close()

    def _measure(self, width: int | None) -> None:
        metrics = tkfont.Font(root=self, font=self._font)
        pad_x, pad_y = self.style.padding
        longest = max((metrics.measure(item) for item in self._values), default=0)
        content = width * metrics.measure("0") if width is not None else longest
        self.configure(
            width=max(80, content + pad_x * 2 + 22),
            height=metrics.metrics("linespace") + pad_y * 2,
        )

    def configure(self, cnf: dict | None = None, **kwargs: Any) -> Any:  # noqa: D102
        options = dict(cnf or {})
        options.update(kwargs)
        values = options.pop("values", None)
        state = options.pop("state", None)
        options.pop("style", None)
        if state is not None:
            self._enabled = str(state) not in {"disabled", tk.DISABLED}
            if self._entry is not None:
                self._entry.configure(state="normal" if self._enabled else "disabled")
        result = super().configure(**options) if options else None
        if values is not None:
            self._values = tuple(str(item) for item in values)
        if values is not None or state is not None:
            self._repaint()
        return result

    config = configure

    def cget(self, key: str) -> Any:  # noqa: D102
        if key == "values":
            return self._values
        if key == "state":
            return "normal" if self._enabled else "disabled"
        return super().cget(key)

    def get(self) -> str:
        return self.variable.get()

    def set(self, value: str) -> None:
        self.variable.set(value)

    def toggle(self) -> None:
        if not self._enabled or not self._values:
            return
        self._close() if self._open else self._show()

    def _animate_to(self, target: float) -> None:
        if self._animation is not None:
            self.after_cancel(self._animation)
            self._animation = None
        start = self._hover
        if abs(target - start) < 0.01:
            return

        def advance(index: int) -> None:
            self._hover = start + (target - start) * (index / ANIMATION_STEPS)
            self._repaint()
            if index < ANIMATION_STEPS:
                self._animation = self.after(ANIMATION_INTERVAL_MS, advance, index + 1)
            else:
                self._animation = None

        advance(1)

    def _repaint(self) -> None:
        self.delete("field")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            width = int(self.cget("width"))
            height = int(self.cget("height"))
        fill = mix(self.style.field, self.style.field_hover, self._hover if self._enabled else 0.0)
        paint_rounded(
            self,
            (0, 0, width, height),
            radius=self.style.radius,
            fill=fill,
            background=self.style.background,
            tag="field",
        )
        edge = mix(self.style.border, self.style.accent, self._hover if self._enabled else 0.0)
        paint_rounded(
            self,
            (0, height - 2, width, height),
            radius=0,
            fill=edge,
            background=fill,
            tag="field",
        )
        pad_x, _pad_y = self.style.padding
        if self._entry is None:
            label = self.variable.get()
            colour = self.style.foreground if label else self.style.muted
            self.create_text(
                pad_x,
                height // 2,
                text=label or "—",
                fill=colour if self._enabled else self.style.muted,
                font=self._font,
                anchor="w",
                tags="field",
            )
        else:
            self.itemconfigure("entry", width=max(10, width - pad_x - 26))
            self.coords("entry", pad_x, height // 2)
            self._entry.configure(background=fill)
        self._draw_chevron(width - pad_x - 4, height // 2)
        self.tag_raise("entry")

    def _draw_chevron(self, x: int, y: int) -> None:
        colour = self.style.muted if self._enabled else mix(self.style.field, self.style.muted, 0.4)
        direction = -1 if self._open else 1
        self.create_line(
            x - 6,
            y - 2 * direction,
            x,
            y + 2 * direction,
            x + 6,
            y - 2 * direction,
            fill=colour,
            width=2,
            capstyle="round",
            joinstyle="round",
            tags="field",
        )

    def _show(self) -> None:
        self._close()
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        popup.configure(background=self.style.background)
        rows = tk.Canvas(
            popup,
            highlightthickness=0,
            borderwidth=0,
            background=self.style.background,
            takefocus=0,
        )
        rows.pack(fill="both", expand=True)

        metrics = tkfont.Font(root=self, font=self._font)
        row_height = metrics.metrics("linespace") + 14
        width = max(self.winfo_width(), metrics.measure(max(self._values, key=len, default="")) + 56)
        visible = min(len(self._values), self._visible_rows)
        height = visible * row_height + 12
        rows.configure(width=width, height=height)
        paint_rounded(
            rows,
            (0, 0, width, height),
            radius=self.style.radius,
            fill=self.style.popup,
            background=self.style.background,
            tag="panel",
        )

        current = self.variable.get()
        for index, value in enumerate(self._values[:visible] if len(self._values) > visible else self._values):
            top = 6 + index * row_height
            tag = f"row{index}"
            rows.create_rectangle(
                6, top, width - 6, top + row_height, fill=self.style.popup, outline="", tags=(tag, "rowbg")
            )
            rows.create_text(
                20,
                top + row_height // 2,
                text=value,
                fill=self.style.accent if value == current else self.style.foreground,
                font=self._font,
                anchor="w",
                tags=tag,
            )
            if value == current:
                rows.create_text(
                    width - 20, top + row_height // 2, text="✓", fill=self.style.accent, font=self._font,
                    anchor="e", tags=tag,
                )
            rows.tag_bind(tag, "<Enter>", lambda _e, name=tag: self._highlight(rows, name, True))
            rows.tag_bind(tag, "<Leave>", lambda _e, name=tag: self._highlight(rows, name, False))
            rows.tag_bind(tag, "<Button-1>", lambda _e, chosen=value: self._choose(chosen))

        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        screen_height = self.winfo_screenheight()
        if y + height > screen_height - 40:
            y = max(0, self.winfo_rooty() - height - 4)
        popup.geometry(f"{width}x{height}+{x}+{y}")
        popup.deiconify()
        popup.lift()
        self._popup = popup
        self._open = True
        self._repaint()
        popup.bind("<Escape>", lambda _event: self._close())
        self.winfo_toplevel().bind("<Button-1>", self._click_outside, add="+")

    def _highlight(self, rows: tk.Canvas, tag: str, active: bool) -> None:
        for item in rows.find_withtag(tag):
            if "rowbg" in rows.gettags(item):
                rows.itemconfigure(
                    item, fill=self.style.popup_hover if active else self.style.popup
                )

    def _click_outside(self, event: tk.Event) -> None:
        if self._popup is None:
            return
        if event.widget is self or str(event.widget).startswith(str(self._popup)):
            return
        self._close()

    def _choose(self, value: str) -> None:
        self.variable.set(value)
        self._close()
        self.event_generate("<<ComboboxSelected>>")

    def _close(self) -> None:
        if self._popup is not None:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None
        if self._open:
            self._open = False
            self._repaint()
