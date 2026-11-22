"""Recording indicator owned by the desktop Tk thread."""

import tkinter as tk


SIZE = 30
TRANSPARENT = "#181818"
RED = "#ff3333"


class RecordingOverlay:
    def __init__(self, root):
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=TRANSPARENT)
        self.window.wm_attributes("-transparentcolor", TRANSPARENT)
        x = (self.window.winfo_screenwidth() - SIZE) // 2
        y = self.window.winfo_screenheight() - SIZE - 52
        self.window.geometry(f"{SIZE}x{SIZE}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.window, width=SIZE, height=SIZE, bg=TRANSPARENT, highlightthickness=0
        )
        self.canvas.pack()
        self._draw_icon()
        self._animation = None
        self._brightness = 0.0
        self._direction = 1

    def _draw_icon(self):
        canvas = self.canvas
        for x, y, start in ((2, 2, 90), (18, 2, 0), (2, 18, 180), (18, 18, 270)):
            canvas.create_arc(x, y, x + 10, y + 10, start=start, extent=90,
                              style=tk.ARC, outline=RED, width=2, tags="outline")
        for line in ((7, 2, 23, 2), (7, 28, 23, 28), (2, 7, 2, 23), (28, 7, 28, 23)):
            canvas.create_line(*line, fill=RED, width=2, tags="stroke")

        canvas.create_rectangle(12, 8, 18, 13, fill=RED, outline="", tags="stroke")
        canvas.create_oval(12, 5, 18, 11, fill=RED, outline="", tags="stroke")
        canvas.create_oval(12, 10, 18, 16, fill=RED, outline="", tags="stroke")
        canvas.create_arc(9, 9, 21, 21, start=160, extent=220,
                          style=tk.ARC, outline=RED, width=2, tags="outline")
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
        self.canvas.itemconfigure("outline", outline=color)
        self._animation = self.window.after(40, self._pulse)

    def set_recording(self, recording):
        if recording:
            if self._animation is None:
                self.window.deiconify()
                self._pulse()
        else:
            if self._animation is not None:
                self.window.after_cancel(self._animation)
                self._animation = None
            self.window.withdraw()
