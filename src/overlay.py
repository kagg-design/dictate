import tkinter as tk
import threading
from src.logger import logger

class RecordingOverlay:
    def __init__(self, show_overlay_indicator=True):
        """
        Manages an elegant, frameless, and fully transparent floating overlay icon 
        centered at the bottom of the screen (just above the taskbar) that pulses 
        neon red during recording.
        """
        self.show_overlay_indicator = show_overlay_indicator
        self.root = None
        self.window = None
        self.thread = None
        
        # UI Elements for drawing microphone
        self.canvas = None
        self.capsule = None
        self.cradle = None
        self.stem = None
        self.base = None
        
        self.pulse_val = 0.0
        self.pulse_direction = 1
        
    def start(self):
        """
        Starts the Tkinter event loop in a background daemon thread.
        """
        if not self.show_overlay_indicator:
            logger.info("Overlay recording indicator disabled by configuration.")
            return
            
        logger.info("Initializing minimalist visual overlay indicator thread...")
        self.thread = threading.Thread(
            target=self._run_loop, 
            name="OverlayUIThread", 
            daemon=False
        )
        self.thread.start()
        
    def _draw_rounded_border(self, canvas, x1, y1, x2, y2, r, color, width=2):
        self.border_arcs = []
        self.border_lines = []
        # Arcs
        self.border_arcs.append(canvas.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90, style=tk.ARC, outline=color, width=width))
        self.border_arcs.append(canvas.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90, style=tk.ARC, outline=color, width=width))
        self.border_arcs.append(canvas.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90, style=tk.ARC, outline=color, width=width))
        self.border_arcs.append(canvas.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90, style=tk.ARC, outline=color, width=width))
        # Lines
        self.border_lines.append(canvas.create_line(x1 + r, y1, x2 - r, y1, fill=color, width=width))
        self.border_lines.append(canvas.create_line(x1 + r, y2, x2 - r, y2, fill=color, width=width))
        self.border_lines.append(canvas.create_line(x1, y1 + r, x1, y2 - r, fill=color, width=width))
        self.border_lines.append(canvas.create_line(x2, y1 + r, x2, y2 - r, fill=color, width=width))

    def _run_loop(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()  # Withdraw the root window helper
            
            # Create borderless topmost window
            self.window = tk.Toplevel(self.root)
            self.window.overrideredirect(True)
            self.window.attributes("-topmost", True)
            
            # Setup color key transparency on Windows (makes background completely transparent)
            trans_color = "#181818"
            self.window.configure(bg=trans_color)
            self.window.wm_attributes("-transparentcolor", trans_color)
            
            # Position widget centered horizontally, just above the taskbar
            screen_width = self.window.winfo_screenwidth()
            screen_height = self.window.winfo_screenheight()
            
            # Super-compact 30x30 transparent window (50% of original)
            width = 30
            height = 30
            x = (screen_width - width) // 2
            y = screen_height - height - 52  # Shifted lower, closer to the taskbar top edge
            
            self.window.geometry(f"{width}x{height}+{x}+{y}")
            
            # Canvas covering the entire window
            self.canvas = tk.Canvas(
                self.window, 
                width=width, 
                height=height, 
                bg=trans_color, 
                highlightthickness=0
            )
            self.canvas.pack()
            
            # Draw background badge rounded rectangle border (centered, 26x26 pixels, transparent fill)
            self._draw_rounded_border(self.canvas, 2, 2, 28, 28, 5, "#ff3333", width=2)
            
            # Draw a sleek neon microphone shape scaled down to match icon proportions
            # 1. Elongated capsule (centered at x=15, y range: 5-16)
            self.capsule = self.canvas.create_rectangle(12, 8, 18, 13, fill="#ff3333", outline="", width=0)
            self.capsule_top = self.canvas.create_oval(12, 5, 18, 11, fill="#ff3333", outline="", width=0)
            self.capsule_bottom = self.canvas.create_oval(12, 10, 18, 16, fill="#ff3333", outline="", width=0)
            
            # 2. Stand cradle (arc around capsule, sides hugging higher)
            self.cradle = self.canvas.create_arc(9, 9, 21, 21, start=160, extent=220, style=tk.ARC, outline="#ff3333", width=2)
            
            # 3. Support stem (vertical line connecting base and cradle)
            self.stem = self.canvas.create_line(15, 21, 15, 25, fill="#ff3333", width=2)
            
            # 4. Base stand (horizontal plate)
            self.base = self.canvas.create_line(11, 25, 19, 25, fill="#ff3333", width=2)
            
            # Start hidden
            self.window.withdraw()
            
            # Start elegant breathing pulse animation
            self._animate_pulse()
            
            self.root.mainloop()
            try:
                self.window.destroy()
                self.root.destroy()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error in overlay UI event loop: {e}")
            
    def _animate_pulse(self):
        if self.window and self.window.winfo_exists():
            # Breathe pulse calculations (sinusoidal modulation)
            self.pulse_val += self.pulse_direction * 0.08
            if self.pulse_val >= 1.0:
                self.pulse_val = 1.0
                self.pulse_direction = -1
            elif self.pulse_val <= 0.0:
                self.pulse_val = 0.0
                self.pulse_direction = 1
                
            # Breathing brightness: between dark crimson (#801a1a) and bright red (#ff3333)
            r = int(128 + self.pulse_val * 127)
            g = int(26 + self.pulse_val * 25)
            b = int(26 + self.pulse_val * 25)
            color = f"#{r:02x}{g:02x}{b:02x}"
            
            # Apply color to all parts of the microphone
            self.canvas.itemconfig(self.capsule, fill=color)
            self.canvas.itemconfig(self.capsule_top, fill=color, outline=color)
            self.canvas.itemconfig(self.capsule_bottom, fill=color, outline=color)
            self.canvas.itemconfig(self.cradle, outline=color)
            self.canvas.itemconfig(self.stem, fill=color)
            self.canvas.itemconfig(self.base, fill=color)
            
            # Apply color to the border outline
            if hasattr(self, 'border_arcs'):
                for arc in self.border_arcs:
                    self.canvas.itemconfig(arc, outline=color)
            if hasattr(self, 'border_lines'):
                for line in self.border_lines:
                    self.canvas.itemconfig(line, fill=color)
            
            self.root.after(40, self._animate_pulse)
            
    def show(self):
        """
        Thread-safe method to make the overlay visible.
        """
        if self.root and self.window:
            self.root.after(0, self._show_window)
            
    def hide(self):
        """
        Thread-safe method to hide the overlay.
        """
        if self.root and self.window:
            self.root.after(0, self._hide_window)
            
    def _show_window(self):
        try:
            if self.window:
                self.window.deiconify()
                self.window.lift()
        except Exception as e:
            logger.debug(f"Failed to show overlay window: {e}")
            
    def _hide_window(self):
        try:
            if self.window:
                self.window.withdraw()
        except Exception as e:
            logger.debug(f"Failed to hide overlay window: {e}")
            
    def destroy(self):
        """
        Cleans up and closes the overlay root window.
        """
        if self.root:
            try:
                self.root.after(0, self.root.quit)
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            try:
                self.thread.join(timeout=2.0)
            except Exception:
                pass
