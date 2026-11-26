"""Windows fonts, palettes, and styles for Dictate's native widgets."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from tkinter import TclError
import tkinter.font as tkfont
from tkinter import ttk
import winreg

from PIL import Image, ImageDraw, ImageTk

@dataclass(frozen=True)
class Palette:
    background: str
    app_background: str
    card: str
    foreground: str
    muted: str
    muted_foreground: str
    border: str
    field_border: str
    primary: str
    primary_foreground: str
    primary_strong: str
    primary_surface: str
    success: str
    success_surface: str
    info: str
    info_surface: str
    danger: str
    danger_surface: str
    warning: str
    warning_surface: str


PALETTES = {
    "light": Palette(
        background="#fafbfd", app_background="#f3f5f8", card="#ffffff",
        foreground="#1c222c", muted="#f0f3f7", muted_foreground="#535e6e",
        border="#dfe4ea", field_border="#c9d1db",
        primary="#184eba", primary_foreground="#ffffff", primary_strong="#1451ca",
        primary_surface="#e7effe",
        success="#007a5f", success_surface="#def9f1", info="#005fc6", info_surface="#e5f3ff",
        danger="#c50516", danger_surface="#fff0ee", warning="#af4000", warning_surface="#ffebda",
    ),
    "dark": Palette(
        background="#1b1d22", app_background="#21242a", card="#2a2e36",
        foreground="#f2f4f8", muted="#343a44", muted_foreground="#b9c1ce",
        border="#3c434e", field_border="#515a69",
        primary="#7ba4f0", primary_foreground="#061432", primary_strong="#b1cbfa",
        primary_surface="#1d2d4b",
        success="#66d5ba", success_surface="#0a362c", info="#9dc7fe", info_surface="#182e4b",
        danger="#ff9f94", danger_surface="#4b1e1a", warning="#fea47c", warning_surface="#4c2817",
    ),
}
APPEARANCES = ("System", "Light", "Dark")


def system_appearance():
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


def style_windows_titlebar(window, dark):
    try:
        get_ancestor = ctypes.windll.user32.GetAncestor
        get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
        get_ancestor.restype = wintypes.HWND
        hwnd = get_ancestor(window.winfo_id(), 2)  # GA_ROOT, the Tk window wrapper
        use_dark_mode = wintypes.BOOL(dark)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), 20, ctypes.byref(use_dark_mode), ctypes.sizeof(use_dark_mode)
        )
    except (AttributeError, OSError):
        pass


def style_combobox_list(combobox, palette):
    # Tk caches the listbox after the first opening, so update its colors on each post.
    try:
        popdown = combobox.tk.call("ttk::combobox::PopdownWindow", combobox)
        font = ttk.Style(combobox).lookup("TCombobox", "font")
        combobox.tk.call(
            f"{popdown}.f.l", "configure", "-background", palette.card, "-foreground", palette.foreground,
            "-selectbackground", palette.primary_surface, "-selectforeground", palette.primary_strong, "-font", font,
        )
    except TclError:
        # Leave the native popup usable with Tk builds that use a different layout.
        pass


class DesktopStyles:
    def __init__(self, window):
        self.window = window
        self.style = ttk.Style(window)
        fonts = set(tkfont.families(window))
        self.font = "Segoe UI Variable Text" if "Segoe UI Variable Text" in fonts else "Segoe UI"
        self.display_font = "Segoe UI Variable Display" if "Segoe UI Variable Display" in fonts else self.font
        self._images = []
        for name, palette in PALETTES.items():
            self.style.theme_create(f"dictate-{name}", parent="clam")
            self.style.theme_use(f"dictate-{name}")
            self._configure(palette)

    def apply(self, name):
        self.style.theme_use(f"dictate-{name}")
        style_windows_titlebar(self.window, name == "dark")
        return PALETTES[name]

    def _rounded(self, radius, fill, outline, center, width=1):
        # Tk repeats the center and straight edges, so use a larger tile for panels.
        size = 2 * (radius + 1) + center
        scale = 4
        image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (scale, scale, (size - 1) * scale - 1, (size - 1) * scale - 1),
            radius=radius * scale, fill=fill, outline=outline, width=width * scale,
        )
        result = ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS), master=self.window)
        self._images.append(result)
        return result

    def _surface(self, name, base, radius, fill, border, *, center=16, focus=None, active=None, disabled=None):
        variants = []
        if disabled:
            variants.append(("disabled", self._rounded(radius, disabled, border, center)))
        if focus:
            variants.append(("focus", self._rounded(radius, fill, focus, center, width=2)))
        if active:
            variants.append(("active", self._rounded(radius, active, border, center)))
        element = name + ".surface"
        self.style.element_create(
            element, "image", self._rounded(radius, fill, border, center), *variants,
            border=radius + 1, padding=2, sticky="nsew",
        )
        layout = self.style.layout(base)
        if base == "TCombobox":
            arrow, field = layout
            root = dict(field[1])
            root["children"] = [arrow, *root["children"]]
            layout = [field]
        else:
            root = dict(layout[0][1])
        # A filled image is a background. Tk draws border elements over their children.
        root.pop("border", None)
        self.style.layout(name, [(element, root), *layout[1:]])

    def _switch_image(self, p, *, selected=False, disabled=False, focused=False):
        scale = 4
        image = Image.new("RGBA", (44 * scale, 26 * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        def box(*coordinates):
            return tuple(value * scale for value in coordinates)

        fill = p.primary if selected and not disabled else p.muted
        outline = p.primary if selected and not disabled else p.muted_foreground
        thumb = p.primary_foreground if selected and not disabled else p.muted_foreground
        if disabled:
            outline = thumb = p.field_border
        if focused:
            draw.rounded_rectangle(box(0, 0, 43, 25), radius=12 * scale, outline=p.primary, width=scale)
        draw.rounded_rectangle(box(3, 3, 40, 22), radius=10 * scale, fill=fill, outline=outline, width=scale)
        x = 26 if selected else 7
        draw.ellipse(box(x, 7, x + 11, 18), fill=thumb)
        result = ImageTk.PhotoImage(image.resize((44, 26), Image.Resampling.LANCZOS), master=self.window)
        self._images.append(result)
        return result

    def _chevron_image(self, color, *, expanded=False):
        scale = 4
        image = Image.new("RGBA", (16 * scale, 16 * scale))
        near, far = (10, 6) if expanded else (6, 10)
        ImageDraw.Draw(image).line(
            [(4 * scale, near * scale), (8 * scale, far * scale), (12 * scale, near * scale)],
            fill=color, width=6, joint="curve",
        )
        result = ImageTk.PhotoImage(image.resize((16, 16), Image.Resampling.LANCZOS), master=self.window)
        self._images.append(result)
        return result

    def _configure(self, p):
        style = self.style
        style.configure(".", font=(self.font, 10), background=p.app_background, foreground=p.foreground)
        style.configure("Topbar.TFrame", background=p.background)
        style.configure("Divider.TFrame", background=p.border)
        style.configure("Topbar.TLabel", background=p.background, foreground=p.foreground,
                        font=(self.font, 12, "bold"))
        style.configure("Surface.TFrame", background=p.card)
        style.configure("Card.TFrame", background=p.app_background)
        style.configure("Heading.TLabel", font=(self.font, 18, "bold"))
        style.configure("Section.TLabel", background=p.card, font=(self.font, 12, "bold"))
        style.configure("Surface.TLabel", background=p.card)
        style.configure("Muted.TLabel", foreground=p.muted_foreground)
        style.configure("PanelMuted.TLabel", background=p.card, foreground=p.muted_foreground)
        style.configure("Shortcut.TLabel", background=p.background, foreground=p.muted_foreground,
                        font=(self.font, 9), padding=(8, 4))
        style.configure("SettingsDetail.TLabel", background=p.card, foreground=p.muted_foreground,
                        font=(self.font, 10))

        self._surface("Card.TFrame", "TFrame", 14, p.card, p.border, center=64)
        self._surface("TButton", "TButton", 8, p.card, p.field_border,
                      focus=p.primary, active=p.muted, disabled=p.muted)
        self._surface("Action.TButton", "TButton", 8, p.primary, p.primary,
                      focus=p.primary_strong, active=p.primary_strong, disabled=p.muted)
        self._surface("Nav.TButton", "TButton", 10, p.background, p.background,
                      focus=p.primary, active=p.muted)
        self._surface("Selected.Nav.TButton", "TButton", 10, p.primary_surface, p.primary_surface,
                      focus=p.primary, active=p.primary_surface)
        self._surface("TEntry", "TEntry", 8, p.card, p.field_border, focus=p.primary)
        self._surface("Settings.TButton", "TButton", 6, p.muted, p.field_border,
                      focus=p.primary, active=p.card, disabled=p.card)
        self._surface("SettingsHeader.TButton", "TButton", 5, p.card, p.card,
                      focus=p.primary, active=p.muted)
        style.element_create(
            "SettingsHeader.chevron", "image", self._chevron_image(p.muted_foreground),
            ("selected", self._chevron_image(p.muted_foreground, expanded=True)),
        )
        style.layout("SettingsHeader.TButton", [
            ("SettingsHeader.TButton.surface", {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [
                    ("SettingsHeader.chevron", {"side": "right"}),
                    ("Button.label", {"side": "left", "sticky": "w"}),
                ]}),
            ]}),
        ])
        self._surface("Settings.TCombobox", "TCombobox", 6, p.muted, p.field_border,
                      focus=p.primary, active=p.muted, disabled=p.card)
        style.configure("TButton", background=p.app_background, padding=(12, 7), focuscolor=p.primary)
        style.map("TButton", foreground=[("disabled", p.muted_foreground)])
        style.configure("Action.TButton", foreground=p.primary_foreground)
        style.configure("Nav.TButton", background=p.background, padding=(12, 7))
        style.configure("Selected.Nav.TButton", foreground=p.primary_strong, font=(self.font, 10, "bold"))
        style.configure("Settings.TButton", background=p.card, padding=(10, 5))
        style.configure("SettingsHeader.TButton", background=p.card, foreground=p.foreground,
                        font=(self.font, 12, "bold"), padding=(10, 6))
        style.configure("Shortcut.Settings.TButton", foreground=p.primary_strong, font=(self.font, 10, "bold"))
        style.configure("TEntry", font=(self.font, 10), fieldbackground=p.card, padding=(10, 7), insertcolor=p.foreground)
        style.configure("TCombobox", font=(self.font, 10), background=p.card, fieldbackground=p.card, bordercolor=p.field_border,
                        arrowcolor=p.muted_foreground, lightcolor=p.card, darkcolor=p.card,
                        selectforeground=p.primary_strong, selectbackground=p.primary_surface, padding=(10, 7))
        style.map("TCombobox", background=[("active", p.muted), ("pressed", p.muted), ("readonly", p.card)],
                  fieldbackground=[("disabled", p.muted), ("readonly", p.card)],
                  bordercolor=[("focus", p.primary)], foreground=[("disabled", p.muted_foreground)])
        style.configure("Settings.TCombobox", fieldbackground=p.muted, background=p.muted, padding=(10, 5),
                        lightcolor=p.muted, darkcolor=p.muted, bordercolor=p.muted)
        style.map("Settings.TCombobox", background=[("disabled", p.card), ("readonly", p.muted)],
                  fieldbackground=[("disabled", p.card), ("readonly", p.muted)])
        style.element_create(
            "Switch.indicator", "image", self._switch_image(p),
            ("disabled", "selected", self._switch_image(p, selected=True, disabled=True)),
            ("disabled", self._switch_image(p, disabled=True)),
            ("focus", "selected", self._switch_image(p, selected=True, focused=True)),
            ("focus", self._switch_image(p, focused=True)),
            ("selected", self._switch_image(p, selected=True)),
        )
        style.layout("Switch.TCheckbutton", [("Switch.indicator", {"sticky": ""})])
        style.configure("Switch.TCheckbutton", background=p.card)
        style.configure("ComboboxPopdownFrame", background=p.border, bordercolor=p.border)
        style.configure("TRadiobutton", padding=(0, 6))
        for widget in ("TRadiobutton", "TCheckbutton"):
            style.configure(widget, background=p.card, indicatorbackground=p.card, indicatorsize=14,
                            indicatormargin=(0, 0, 8, 0), indicatorforeground=p.primary,
                            upperbordercolor=p.muted_foreground, lowerbordercolor=p.muted_foreground)
            style.map(widget, background=[("active", p.primary_surface)],
                      indicatorbackground=[("disabled", p.muted), ("selected", p.primary_surface)],
                      foreground=[("disabled", p.muted_foreground)], focuscolor=[("focus", p.primary)])

        for status in ("success", "info", "danger", "warning"):
            foreground, fill = getattr(p, status), getattr(p, status + "_surface")
            label = status.title() + ".Badge.TLabel"
            self._surface(label, "TLabel", 6, fill, fill)
            style.configure(label, background=p.background, foreground=foreground,
                            font=(self.font, 9, "bold"), padding=(7, 3))

        style.configure("Treeview", rowheight=40, background=p.card, fieldbackground=p.card,
                        borderwidth=0, bordercolor=p.border, lightcolor=p.border, darkcolor=p.border,
                        focuscolor=p.primary, foreground=p.foreground)
        style.configure("Treeview.Heading", background=p.muted, foreground=p.muted_foreground,
                        bordercolor=p.border, relief="flat", font=(self.font, 8, "bold"), padding=(8, 8))
        style.map("Treeview", background=[("selected", p.primary_surface)],
                  foreground=[("selected", p.primary_strong)])
        style.configure("TPanedwindow", background=p.app_background, sashthickness=16)
        style.configure("TScrollbar", background=p.border, troughcolor=p.card,
                        bordercolor=p.card, lightcolor=p.border, darkcolor=p.border, arrowsize=10)
        style.layout("Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"sticky": "nsew"}),
            ]}),
        ])
        style.configure("Settings.Vertical.TScrollbar", background=p.field_border, troughcolor=p.app_background,
                        bordercolor=p.app_background, lightcolor=p.app_background, darkcolor=p.app_background)
        style.element_create(
            "Settings.Scrollbar.thumb", "image", self._rounded(3, p.field_border, p.field_border, center=2),
            ("disabled", self._rounded(3, p.app_background, p.app_background, center=2)),
            ("active", self._rounded(3, p.muted_foreground, p.muted_foreground, center=2)),
            border=4, sticky="ns",
        )
        style.layout("Settings.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Settings.Scrollbar.thumb", {"sticky": "ns"}),
            ]}),
        ])
        style.configure("Input.Horizontal.TProgressbar", background=p.primary, troughcolor=p.primary_surface,
                        bordercolor=p.primary_surface, lightcolor=p.primary, darkcolor=p.primary, thickness=6)
