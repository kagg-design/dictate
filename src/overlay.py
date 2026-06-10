import tkinter as tk
import threading
from src.logger import logger

class RecordingOverlay:
    def __init__(self, show_overlay_indicator=True):
        """
        Manages a borderless floating overlay window that pulses red 
        to indicate active audio recording.
        """
        self.show_overlay_indicator = show_overlay_indicator
        self.root = None
        self.window = None
        self.thread = None
        self.circle = None
        self.pulse_radius = 12
        self.pulse_growing = True
        
    def start(self):
        """
        Starts the Tkinter event loop in a background daemon thread.
        """
        if not self.show_overlay_indicator:
            logger.info("Overlay recording indicator disabled by configuration.")
            return
            
        logger.info("Initializing visual overlay indicator thread...")
        self.thread = threading.Thread(
            target=self._run_loop, 
            name="OverlayUIThread", 
            daemon=True
        )
        self.thread.start()
        
    def _run_loop(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()  # Withdraw the root window helper
            
            # Create borderless floating widget
            self.window = tk.Toplevel(self.root)
            self.window.overrideredirect(True)
            self.window.attributes("-topmost", True)
            self.window.attributes("-alpha", 0.92)  # Slight transparency for premium feel
            self.window.configure(bg="#181818")
            
            # Position widget at the bottom right, above standard taskbar notifications
            screen_width = self.window.winfo_screenwidth()
            screen_height = self.window.winfo_screenheight()
            
            width = 160
            height = 44
            x = screen_width - width - 20
            y = screen_height - height - 70
            
            self.window.geometry(f"{width}x{height}+{x}+{y}")
            
            # Draw thin accent border frame
            border_frame = tk.Frame(self.window, bg="#333333", bd=1)
            border_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            
            content_frame = tk.Frame(border_frame, bg="#181818")
            content_frame.place(x=1, y=1, width=width-2, height=height-2)
            
            # Canvas for pulsing recording dot
            self.canvas = tk.Canvas(
                content_frame, 
                width=28, 
                height=28, 
                bg="#181818", 
                highlightthickness=0
            )
            self.canvas.pack(side=tk.LEFT, padx=(10, 5))
            
            # Red pulse circle
            self.circle = self.canvas.create_oval(4, 4, 24, 24, fill="#ff3333", outline="")
            
            # Notification label
            self.label = tk.Label(
                content_frame, 
                text="RECORDING", 
                fg="#f0f0f0", 
                bg="#181818", 
                font=("Segoe UI", 10, "bold")
            )
            self.label.pack(side=tk.LEFT, padx=5)
            
            # Start hidden
            self.window.withdraw()
            
            # Start pulse animation
            self._animate_pulse()
            
            self.root.mainloop()
        except Exception as e:
            logger.error(f"Error in overlay UI event loop: {e}")
            
    def _animate_pulse(self):
        if self.window and self.window.winfo_exists():
            # Update pulse radius
            if self.pulse_growing:
                self.pulse_radius += 0.8
                if self.pulse_radius >= 13:
                    self.pulse_growing = False
            else:
                self.pulse_radius -= 0.8
                if self.pulse_radius <= 7:
                    self.pulse_growing = True
                    
            center = 14
            self.canvas.coords(
                self.circle,
                center - self.pulse_radius,
                center - self.pulse_radius,
                center + self.pulse_radius,
                center + self.pulse_radius
            )
            
            # Smoothly transition color intensity
            brightness = int(180 + (self.pulse_radius - 7) * 12)
            brightness = min(max(brightness, 100), 255)
            color = f"#{brightness:02x}1a1a"
            self.canvas.itemconfig(self.circle, fill=color)
            
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
                # Lift to top again to handle overlapping windows
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
            self.root.after(0, self.root.destroy)
