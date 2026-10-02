r"""Interactive check of Dictate's real insertion code, without recording audio.

Run with .venv\Scripts\pythonw.exe scripts\check_text_input.py. Click the test
button, then focus an empty editor before the eight-second countdown ends.
"""

import sys
import logging
import argparse
import ctypes
import time
import threading
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A second process must not hold the running app's rotating log open on Windows.
diagnostic_log = Path(__file__).resolve().parents[1] / "logs" / "text-input-check.log"
diagnostic_log.parent.mkdir(exist_ok=True)
handler = logging.FileHandler(diagnostic_log, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger("dictate").addHandler(handler)
logging.getLogger("dictate").setLevel(logging.INFO)

from src.inserter import paste_text
from src.logger import logger


TEST_TEXT = "Проверка Dictate: русский текст, English, 0123456789 и знаки — !?. " * 10


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-window", type=int)
    args = parser.parse_args()
    root = tk.Tk()
    root.title("Dictate input diagnostic")
    root.geometry("480x180")
    status = tk.StringVar(value="Focus an empty editor after starting the test.")
    tk.Label(root, textvariable=status, wraplength=450).pack(pady=20)

    def insert():
        try:
            if args.target_window is not None:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetForegroundWindow.restype = ctypes.c_void_p
                deadline = time.monotonic() + 60
                while user32.GetForegroundWindow() != args.target_window:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("The requested test window was not focused.")
                    time.sleep(0.1)
            paste_text(TEST_TEXT)
            root.after(0, status.set, "Test submitted. Compare the editor with the expected text.")
        except Exception as error:
            logger.exception("Input diagnostic failed")
            root.after(0, status.set, f"Test failed: {error}")
        finally:
            root.after(0, lambda: button.config(state="normal"))

    def countdown(remaining):
        status.set(f"Insertion in {remaining} seconds. Focus the empty test editor.")
        if remaining:
            root.after(1000, countdown, remaining - 1)
        else:
            threading.Thread(target=insert, daemon=True).start()

    def start():
        button.config(state="disabled")
        countdown(8)

    button = tk.Button(root, text="Test Dictate insertion in 8 seconds", command=start)
    button.pack()
    tk.Label(root, text=f"Expected: {len(TEST_TEXT)} characters. Clipboard is not changed.").pack(pady=10)
    root.mainloop()


if __name__ == "__main__":
    main()
