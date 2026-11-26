"""Recording indicator owned by the desktop Tk thread."""

import ctypes
from ctypes import wintypes
from math import cos, radians, sin
from time import monotonic
import tkinter as tk


SIZE = 30
PREVIEW_HEIGHT = 66
PREVIEW_RADIUS = 9
TRANSPARENT = "#181818"
RED = "#ff3333"
BLUE = "#3c9bff"
PROCESSING_DELAY = 0.4


class RecordingOverlay:
    def __init__(self, root, *, font="Segoe UI"):
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=TRANSPARENT)
        self.window.wm_attributes("-transparentcolor", TRANSPARENT)
        x = (self.window.winfo_screenwidth() - SIZE) // 2
        y = self.window.winfo_screenheight() - SIZE - 52
        self.window.geometry(f"{SIZE}x{SIZE}+{x}+{y}")
        self._preview = tk.Toplevel(root)
        self._preview.withdraw()
        self._preview.overrideredirect(True)
        self._preview.attributes("-topmost", True)
        width = min(480, self.window.winfo_screenwidth() - 40)
        self._preview_size = (width, PREVIEW_HEIGHT)
        self._preview.geometry(
            f"{width}x{PREVIEW_HEIGHT}+{(self.window.winfo_screenwidth() - width) // 2}+{y - PREVIEW_HEIGHT - 8}"
        )
        self._preview_label = tk.Label(
            self._preview, bg="#242424", fg="#f5f5f5", font=(font, 11),
            justify="left", wraplength=width - 28, padx=14, pady=8,
        )
        self._preview_label.pack(fill="both", expand=True)
        self._preview_text = ""
        self._processing_since = None

        self.canvas = tk.Canvas(
            self.window, width=SIZE, height=SIZE, bg=TRANSPARENT, highlightthickness=0
        )
        self.canvas.pack()
        self._draw_icon()
        self._animation = None
        self._rotation = None
        self._angle = 0
        self._brightness = 0.0
        self._direction = 1

    def _draw_icon(self):
        canvas = self.canvas
        frame = []
        for x, y, start in ((23, 7, -90), (23, 23, 0), (7, 23, 90), (7, 7, 180)):
            for offset in range(0, 91, 15):
                angle = radians(start + offset)
                frame.extend((x + 5 * cos(angle), y + 5 * sin(angle)))
        canvas.create_line(*frame, *frame[:2], fill=RED, width=2, joinstyle=tk.ROUND, tags="frame")

        canvas.create_rectangle(12, 8, 18, 13, fill=RED, outline="", tags="stroke")
        canvas.create_oval(12, 5, 18, 11, fill=RED, outline="", tags="stroke")
        canvas.create_oval(12, 10, 18, 16, fill=RED, outline="", tags="stroke")
        canvas.create_arc(9, 9, 21, 21, start=160, extent=220,
                          style=tk.ARC, outline=RED, width=2, tags="mic-arc")
        canvas.create_line(15, 21, 15, 25, fill=RED, width=2, tags="stroke")
        canvas.create_line(11, 25, 19, 25, fill=RED, width=2, tags="stroke")

    def _pulse(self):
        self._brightness = max(0.0, min(1.0, self._brightness + self._direction * 0.08))
        if self._brightness in (0.0, 1.0):
            self._direction *= -1
        red = int(128 + self._brightness * 127)
        tint = int(26 + self._brightness * 25)
        color = f"#{red:02x}{tint:02x}{tint:02x}"
        self.canvas.itemconfigure("stroke", fill=color)
        self.canvas.itemconfigure("frame", fill=color)
        self.canvas.itemconfigure("mic-arc", outline=color)
        self._animation = self.window.after(40, self._pulse)

    def set_recording(self, recording):
        if recording:
            self._stop_processing()
            if self._animation is None:
                self._show_without_activation(self.window)
                self._pulse()
        elif self._animation is not None:
            self.window.after_cancel(self._animation)
            self._animation = None
            self.window.withdraw()
            self._set_preview("")

    def update_feedback(self, text, processing, *, show_preview=False):
        recording = self._animation is not None
        if recording or not processing:
            self._processing_since = None
            self._stop_processing()
            self._set_preview(text if recording and show_preview else "")
            return
        now = monotonic()
        if self._processing_since is None:
            self._processing_since = now
        if self._rotation is None and now - self._processing_since >= PROCESSING_DELAY:
            self.canvas.itemconfigure("stroke", state="hidden")
            self.canvas.itemconfigure("mic-arc", state="hidden")
            self.canvas.itemconfigure("frame", fill=BLUE)
            self._angle = 0
            self._spin()
            self._show_without_activation(self.window)

    def _spin(self):
        self.canvas.delete("spinner")
        points = []
        for offset in range(0, 316, 15):
            angle = radians(self._angle + offset)
            points.extend((15 + 6.5 * cos(angle), 15 + 6.5 * sin(angle)))
        self.canvas.create_line(*points, fill=BLUE, width=2, smooth=True,
                                capstyle=tk.ROUND, tags="spinner")
        self._angle = (self._angle + 16) % 360
        self._rotation = self.window.after(40, self._spin)

    def _stop_processing(self):
        if self._rotation is None:
            return
        self.window.after_cancel(self._rotation)
        self._rotation = None
        self.canvas.delete("spinner")
        self.canvas.itemconfigure("stroke", state="normal")
        self.canvas.itemconfigure("mic-arc", state="normal")
        self.canvas.itemconfigure("frame", fill=RED)
        self.window.withdraw()

    def _set_preview(self, text):
        if text == self._preview_text:
            return
        self._preview_text = text
        if text:
            self._preview_label.configure(text=text)
            self._show_without_activation(self._preview, rounded_size=self._preview_size)
        else:
            self._preview.withdraw()

    @staticmethod
    def _show_without_activation(window, *, rounded_size=None):
        if window.winfo_ismapped():
            return
        window.update_idletasks()
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
        user32.SetWindowLongW.restype = ctypes.c_long
        hwnd = user32.GetAncestor(window.winfo_id(), 2)
        # Keep the original text field focused when the indicator appears.
        extended_style = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, extended_style | 0x08000000 | 0x00000020)
        if rounded_size is not None:
            RecordingOverlay._round_preview_window(hwnd, *rounded_size)
        window.deiconify()

    @staticmethod
    def _round_preview_window(hwnd, width, height):
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        create_region = gdi32.CreateRoundRectRgn
        create_region.argtypes = (ctypes.c_int,) * 6
        create_region.restype = wintypes.HRGN
        diameter = 2 * PREVIEW_RADIUS
        # GDI omits the last row and column of a rounded rectangle.
        region = create_region(0, 0, width + 1, height + 1, diameter, diameter)
        if not region:
            return
        user32.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN, wintypes.BOOL)
        user32.SetWindowRgn.restype = ctypes.c_int
        if not user32.SetWindowRgn(hwnd, region, False):
            # Windows takes ownership only when SetWindowRgn succeeds.
            gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
            gdi32.DeleteObject.restype = wintypes.BOOL
            gdi32.DeleteObject(region)
