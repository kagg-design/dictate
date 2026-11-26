"""Draw the settings rows in one viewport and keep the form controls native."""

from dataclasses import dataclass, field
import tkinter as tk
from tkinter import ttk


SCROLL_STEP = 20


@dataclass
class _Row:
    label: int
    detail: int | None
    controls: ttk.Frame
    window: int
    separator: int
    control_size: tuple[int, int] = (0, 0)


@dataclass
class _Section:
    key: str
    button: ttk.Button
    window: int
    background: int
    expanded: bool
    rows: list[_Row] = field(default_factory=list)

    @property
    def content_tag(self):
        return f"section-{self.key}"


class SettingsView(tk.Canvas):
    def __init__(self, parent, *, font, display_font, palette, collapsed=(), on_section_change=None):
        super().__init__(parent, highlightthickness=0, borderwidth=0, yscrollincrement=SCROLL_STEP, takefocus=False)
        self._font = font
        self._collapsed = (
            {value for value in collapsed if isinstance(value, str)}
            if isinstance(collapsed, (list, tuple)) else set()
        )
        self._on_section_change = on_section_change
        self._sections = []
        self._layout_pending = False
        self._notice_kind = "Info"
        self._title = self.create_text(0, 0, text="Settings", anchor="nw", font=(display_font, 25, "bold"),
                                       tags="label")
        self._notice_bg = self.create_rectangle(0, 0, 0, 0, outline="", state="hidden")
        self._notice = self.create_text(0, 0, anchor="nw", font=(font, 10), state="hidden")
        self.apply_palette(palette)
        self.bind("<Configure>", self._schedule_layout)

    def apply_palette(self, palette):
        self._palette = palette
        self.configure(background=palette.app_background)
        self.itemconfigure("label", fill=palette.foreground)
        self.itemconfigure("detail", fill=palette.muted_foreground)
        self.itemconfigure("card", fill=palette.card, outline=palette.border)
        self.itemconfigure("separator", fill=palette.border)
        self._update_notice_colors()

    def add_section(self, key, title):
        background = self.create_polygon(0, 0, 0, 0, 0, 0, smooth=True,
                                         fill=self._palette.card, outline=self._palette.border, tags="card")
        button = ttk.Button(self, text=title, style="SettingsHeader.TButton")
        window = self.create_window(0, 0, anchor="nw", window=button)
        section = _Section(key, button, window, background, key not in self._collapsed)
        button.configure(command=lambda: self._toggle_section(section))

        def activate(event):
            button.invoke()
            return "break"

        button.bind("<Return>", activate)
        if section.expanded:
            button.state(("selected",))
        self._sections.append(section)
        return section

    def collapsed_sections(self):
        return [section.key for section in self._sections if not section.expanded]

    def _toggle_section(self, section):
        if section.expanded:
            focused = self.focus_get()
            if focused is not None and any(focused.master is row.controls for row in section.rows):
                section.button.focus_set()
        section.expanded = not section.expanded
        section.button.state(("selected" if section.expanded else "!selected",))
        self.itemconfigure(section.content_tag, state="normal" if section.expanded else "hidden")
        self._schedule_layout()
        if self._on_section_change is not None:
            self._on_section_change()

    def add_row(self, section, label, detail=None):
        tag = section.content_tag
        state = "normal" if section.expanded else "hidden"
        separator = self.create_line(0, 0, 0, 0, fill=self._palette.border, tags=("separator", tag), state=state)
        title = self.create_text(0, 0, anchor="nw", text=label, font=(self._font, 11),
                                 fill=self._palette.foreground, tags=("label", tag), state=state)
        description = None
        if detail:
            description = self.create_text(0, 0, anchor="nw", text=detail, font=(self._font, 10),
                                           width=410, fill=self._palette.muted_foreground,
                                           tags=("detail", tag), state=state)
        controls = ttk.Frame(self, style="Surface.TFrame")
        window = self.create_window(0, 0, anchor="e", window=controls, tags=tag, state=state)
        row = _Row(title, description, controls, window, separator)
        section.rows.append(row)

        def controls_resized(event):
            size = (event.width, event.height)
            if size != row.control_size:
                row.control_size = size
                self._schedule_layout()

        controls.bind("<Configure>", controls_resized)
        self._schedule_layout()
        return row

    def set_notice(self, text, kind):
        if self.itemcget(self._notice, "text") == text and self._notice_kind == kind:
            return
        self.itemconfigure(self._notice, text=text, state="normal" if text else "hidden")
        self.itemconfigure(self._notice_bg, state="normal" if text else "hidden")
        self._notice_kind = kind
        self._update_notice_colors()
        self._schedule_layout()

    def focus_adjacent_control(self, current, direction):
        # Tk leaves window items outside the viewport unmapped. Reveal the next
        # row before focusing its control so Tab can reach the whole form.
        controls = []
        for section in self._sections:
            controls.append((section.window, section.button))
            if section.expanded:
                controls.extend(
                    (row.window, widget)
                    for row in section.rows for widget in row.controls.winfo_children()
                    if isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Combobox))
                    and not widget.instate(("disabled",))
                )
        for index, (_, widget) in enumerate(controls):
            if widget is current:
                target = index + direction
                if not 0 <= target < len(controls):
                    return False
                window, following = controls[target]
                _, top, _, bottom = self.bbox(window)
                offset = self.canvasy(0)
                if top < offset:
                    self.yview_scroll(int((top - offset) // SCROLL_STEP), "units")
                elif bottom > offset + self.winfo_height():
                    clipped = bottom - offset - self.winfo_height()
                    self.yview_scroll(int((clipped + SCROLL_STEP - 1) // SCROLL_STEP), "units")
                self.after_idle(following.focus_set)
                return True
        return False

    def _update_notice_colors(self):
        kind = self._notice_kind.lower()
        self.itemconfigure(self._notice, fill=getattr(self._palette, kind))
        self.itemconfigure(self._notice_bg, fill=getattr(self._palette, kind + "_surface"))

    def _schedule_layout(self, event=None):
        if not self._layout_pending:
            self._layout_pending = True
            self.after_idle(self._layout)

    def _text_height(self, item):
        bounds = self.bbox(item)
        return bounds[3] - bounds[1] if bounds else 0

    def _layout(self):
        self._layout_pending = False
        viewport = self.winfo_width()
        viewport_height = self.winfo_height()
        if viewport <= 1 or viewport_height <= 1:
            return
        width = min(940, viewport - 2)
        left = (viewport - width) // 2
        right = left + width
        self.coords(self._title, left, 0)
        y = self._text_height(self._title) + 15
        if self.itemcget(self._notice, "text"):
            self.itemconfigure(self._notice, width=width - 24)
            self.coords(self._notice, left + 12, y + 8)
            height = self._text_height(self._notice) + 16
            self.coords(self._notice_bg, left, y, right, y + height)
            y += height + 12
        for section in self._sections:
            top = y
            header_height = max(38, section.button.winfo_reqheight())
            self.coords(section.window, left + 5, y + 4)
            self.itemconfigure(section.window, width=width - 10, height=header_height)
            y += header_height + 8
            if section.expanded:
                for row in section.rows:
                    self.coords(row.separator, left + 16, y, right - 16, y)
                    y += 1
                    y += self._place_row(row, left, right, y)
            bottom = y + (3 if section.expanded else 0)
            radius = 9
            self.coords(
                section.background,
                left + radius, top, right - radius, top, right, top, right, top + radius,
                right, bottom - radius, right, bottom, right - radius, bottom, left + radius, bottom,
                left, bottom, left, bottom - radius, left, top + radius, left, top,
            )
            y = bottom + 10
        # A scroll region shorter than the viewport lets Tk scroll above y=0.
        bounds = (0, 0, viewport, max(y, viewport_height))
        if tuple(map(int, self.cget("scrollregion").split())) != bounds:
            self.configure(scrollregion=bounds)
        if y <= viewport_height and self.canvasy(0) != 0:
            self.yview_moveto(0)

    def _place_row(self, row, left, right, y):
        label_height = self._text_height(row.label)
        detail_height = 0
        if row.detail is not None:
            available = right - left - 48 - row.controls.winfo_reqwidth()
            self.itemconfigure(row.detail, width=min(410, max(50, available)))
            detail_height = self._text_height(row.detail) + 3
        text_height = label_height + detail_height
        height = max(46, text_height + 18, row.controls.winfo_reqheight() + 14)
        text_top = y + (height - text_height) // 2
        self.coords(row.label, left + 16, text_top)
        if row.detail is not None:
            self.coords(row.detail, left + 16, text_top + label_height + 3)
        self.coords(row.window, right - 16, y + height // 2)
        return height
