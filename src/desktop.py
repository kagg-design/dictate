"""The Dictate window and recording overlay share one Tk event loop."""

from pathlib import Path
from queue import Empty, SimpleQueue
import ctypes
from ctypes import wintypes
import threading
import tkinter as tk
from tkinter import ttk
import wave
import winsound

from PIL import Image, ImageTk
import yaml

from src.config import save_settings
from src.desktop_theme import (
    APPEARANCES,
    DesktopStyles,
    bundled_inter,
    style_combobox_list,
    style_windows_titlebar,
    system_appearance,
)
from src.logger import logger
from src.hotkey import MODES, SHORTCUTS
from src.overlay import RecordingOverlay
from src.recorder import list_microphones
from src.startup import set_start_at_login, startup_shortcut


SYSTEM_DEFAULT = "System default"
PRE_ROLL_MS = (0, 250, 500, 1000)
ICON = Path(__file__).resolve().parents[1] / "icon.ico"


class DesktopApp:
    def __init__(self):
        self.thread = None
        self._commands = SimpleQueue()
        self._win32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._win32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        self._win32.CreateEventW.restype = wintypes.HANDLE
        self._win32.SetEvent.argtypes = (wintypes.HANDLE,)
        self._win32.SetEvent.restype = wintypes.BOOL
        self._win32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._win32.WaitForSingleObject.restype = wintypes.DWORD
        self._win32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._activation = self._win32.CreateEventW(None, False, False, r"Local\DictateOpenWindow")

    def start(self, app, *, appearance="System"):
        self.thread = threading.Thread(
            target=self._run, args=(app, appearance), name="DesktopUI", daemon=True
        )
        self.thread.start()

    def show(self, icon=None, item=None):
        self._commands.put("show")

    def show_running_instance(self):
        return bool(self._activation and self._win32.SetEvent(self._activation))

    def set_recording(self, active):
        self._commands.put(active)

    def close(self):
        self._commands.put("close")
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self._activation and not (self.thread and self.thread.is_alive()):
            self._win32.CloseHandle(self._activation)
            self._activation = None

    def _run(self, app, appearance):
        with bundled_inter():
            root = None
            try:
                root = tk.Tk()
                root.withdraw()
                window = DictateWindow(root, app, appearance)
                overlay = RecordingOverlay(root)
                show_pending = False

                def dispatch():
                    nonlocal show_pending
                    if self._activation and self._win32.WaitForSingleObject(self._activation, 0) == 0:
                        show_pending = True
                    while True:
                        try:
                            command = self._commands.get_nowait()
                        except Empty:
                            break
                        if command == "close":
                            root.quit()
                            return
                        if command == "show":
                            show_pending = True
                        else:
                            overlay.set_recording(command)
                    overlay.set_preview(app.preview_text)
                    if show_pending and not app.dictation_busy:
                        window.show()
                        show_pending = False
                    root.after(33, dispatch)

                root.after(0, dispatch)
                root.mainloop()
                window.stop_audio()
            except Exception:
                logger.exception("Dictate desktop window failed")
            finally:
                if root is not None:
                    root.destroy()


class DictateWindow:
    def __init__(self, root, app, appearance="System"):
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.title("Dictate")
        if ICON.is_file():
            self.window.iconbitmap(str(ICON))
        self.window.geometry("1080x690")
        self.window.minsize(850, 640)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.bind("<Escape>", lambda event: self.hide())
        self.window.bind("<Control-f>", self._focus_search)

        self._page = "History"
        self._entries = ()
        self._visible_entries = {}
        self._selected = None
        self._audio_timer = None
        self._poll_timer = None
        self._status_snapshot = None
        self._settings_snapshot = None
        try:
            self._startup_path = startup_shortcut()
        except Exception:
            self._startup_path = None
            logger.exception("Could not locate the Windows Startup folder")
        self._styles = DesktopStyles(self.window)
        self._appearance = appearance if appearance in APPEARANCES else "System"
        self._theme = self._resolve_theme()
        self._appearance_timer = None
        self.palette = self._styles.apply(self._theme)
        self.window.configure(bg=self.palette.app_background)
        self._build_layout()
        self._select_page("History")

    def _resolve_theme(self):
        return system_appearance() if self._appearance == "System" else self._appearance.lower()

    def _sync_appearance(self):
        theme = self._resolve_theme()
        if theme != self._theme:
            self._theme = theme
            self.palette = self._styles.apply(theme)
            self.window.configure(bg=self.palette.app_background)
            self._style_transcript()

    def _check_system_appearance(self):
        self._appearance_timer = None
        if self._appearance == "System":
            self._sync_appearance()
            self._appearance_timer = self.window.after(1000, self._check_system_appearance)

    def _stop_appearance_timer(self):
        if self._appearance_timer is not None:
            self.window.after_cancel(self._appearance_timer)
            self._appearance_timer = None

    def _change_appearance(self, event=None):
        value = self.appearance_choice.get()
        if value not in APPEARANCES or value == self._appearance:
            return
        try:
            save_settings({"appearance": value})
        except (OSError, ValueError, yaml.YAMLError):
            logger.exception("Could not save the Dictate appearance")
            self.appearance_choice.set(self._appearance)
            self.window.bell()
            return
        self._appearance = value
        self._stop_appearance_timer()
        self._sync_appearance()
        if self._poll_timer is not None:
            self._check_system_appearance()

    def _build_layout(self):
        topbar = ttk.Frame(self.window, style="Topbar.TFrame", height=64)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        navigation = ttk.Frame(topbar, style="Topbar.TFrame", padding=(24, 0))
        navigation.pack(fill="both", expand=True)
        if ICON.is_file():
            with Image.open(ICON) as icon:
                mark = icon.convert("RGBA").resize((28, 28), Image.Resampling.LANCZOS)
                self._mark = ImageTk.PhotoImage(mark, master=self.window)
            ttk.Label(navigation, style="Topbar.TLabel", image=self._mark).pack(side="left", padx=(0, 9))
        ttk.Label(navigation, text="Dictate", style="Topbar.TLabel").pack(side="left", padx=(0, 24))

        self._nav = {}
        for name in ("History", "Settings"):
            button = ttk.Button(
                navigation, text=name, style="Nav.TButton", width=9,
                command=lambda page=name: self._select_page(page),
            )
            button.pack(side="left", padx=(0, 4))
            self._nav[name] = button

        self.appearance_choice = ttk.Combobox(navigation, state="readonly", width=8, values=APPEARANCES)
        self.appearance_choice.configure(postcommand=lambda: style_combobox_list(self.appearance_choice, self.palette))
        self.appearance_choice.set(self._appearance)
        self.appearance_choice.pack(side="right")
        self.appearance_choice.bind("<<ComboboxSelected>>", self._change_appearance)
        ttk.Label(navigation, text="Theme", style="Shortcut.TLabel").pack(side="right")
        self.status = ttk.Label(navigation, style="Info.Badge.TLabel")
        self.status.pack(side="right", padx=(0, 16))
        self.shortcut_summary = ttk.Label(navigation, style="Shortcut.TLabel")
        self.shortcut_summary.pack(side="right", padx=(0, 4))
        ttk.Frame(self.window, style="Divider.TFrame", height=1).pack(fill="x")

        self.content = ttk.Frame(self.window, padding=(24, 22, 24, 24))
        self.content.pack(fill="both", expand=True)
        self.pages = {"History": self._build_history(), "Settings": self._build_settings()}

    def _panel(self, parent, padding=8):
        shell = ttk.Frame(parent, style="Card.TFrame", padding=8)
        body = ttk.Frame(shell, style="Surface.TFrame", padding=padding)
        body.pack(fill="both", expand=True)
        return shell, body

    def _build_history(self):
        page = ttk.Frame(self.content)
        ttk.Label(page, text="Dictation history", style="Heading.TLabel").pack(anchor="w")
        searchbar = ttk.Frame(page)
        searchbar.pack(fill="x", pady=(17, 12))
        ttk.Label(searchbar, text="Search").pack(side="left", padx=(0, 9))
        self.search = tk.StringVar(self.window)
        self.search_box = ttk.Entry(searchbar, textvariable=self.search, width=28)
        self.search_box.pack(side="left")
        ttk.Button(searchbar, text="Clear", command=lambda: self.search.set("")).pack(side="left", padx=(7, 0))
        self.entry_count = ttk.Label(searchbar, style="Muted.TLabel")
        self.entry_count.pack(side="right")

        split = ttk.Panedwindow(page, orient="horizontal")
        split.pack(fill="both", expand=True)
        left, listing = self._panel(split, padding=0)
        right, details = self._panel(split, padding=16)
        split.add(left, weight=2)
        split.add(right, weight=3)

        self.history_list = ttk.Treeview(listing, columns=("date", "text"), show="headings", selectmode="browse")
        self.history_list.heading("date", text="RECORDED", anchor="w")
        self.history_list.heading("text", text="DICTATION", anchor="w")
        self.history_list.column("date", width=106, minwidth=95, stretch=False)
        self.history_list.column("text", width=196, minwidth=100)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.history_list.yview)
        self.history_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.history_list.pack(side="left", fill="both", expand=True)
        self.history_list.bind("<<TreeviewSelect>>", self._show_selected)
        self.history_list.bind("<Return>", lambda event: self._copy_selected())

        self.details_title = ttk.Label(details, style="Section.TLabel")
        self.details_title.pack(anchor="w")
        self.details_model = ttk.Label(details, style="PanelMuted.TLabel")
        self.details_model.pack(anchor="w", pady=(3, 10))
        transcript = ttk.Frame(details, style="Surface.TFrame")
        transcript.pack(fill="both", expand=True)
        self.transcript = tk.Text(
            transcript, width=32, height=9, wrap="word", font=(self._styles.font, 11),
            relief="flat", bd=0, highlightthickness=0, padx=0, pady=6,
            state="disabled", exportselection=False,
        )
        self._style_transcript()
        scroll = ttk.Scrollbar(transcript, orient="vertical", command=self.transcript.yview)
        self.transcript.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.transcript.pack(side="left", fill="both", expand=True)

        actions = ttk.Frame(details, style="Surface.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        self.copy_button = ttk.Button(actions, text="Copy text", style="Action.TButton", command=self._copy_selected)
        self.copy_button.pack(side="left")
        self.audio_button = ttk.Button(actions, text="Play audio", command=self._toggle_audio, state="disabled")
        self.audio_button.pack(side="left", padx=(8, 0))
        self.replacement_button = ttk.Button(actions, text="Add replacement…", command=self._add_history_replacement)
        self.replacement_button.pack(side="left", padx=(8, 0))
        self.history_feedback = ttk.Label(details, style="PanelMuted.TLabel", wraplength=350)
        self.history_feedback.pack(anchor="w", pady=(7, 0))
        self.search.trace_add("write", lambda *_: self._refresh_history())
        return page

    def _style_transcript(self):
        self.transcript.configure(
            bg=self.palette.card, fg=self.palette.foreground,
            selectbackground=self.palette.primary_surface, selectforeground=self.palette.primary_strong,
        )

    def _build_settings(self):
        page = ttk.Frame(self.content)
        body = ttk.Frame(page)
        body.place(relx=0.5, y=0, anchor="n", relheight=1)
        page.bind("<Configure>", lambda event: body.place_configure(width=min(event.width, 768)))
        ttk.Label(body, text="Settings", style="Heading.TLabel").pack(anchor="w", pady=(0, 16))

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True, padx=(6, 0))

        shell, audio = self._panel(left)
        shell.pack(fill="x", pady=(0, 12))
        ttk.Label(audio, text="Microphone", style="Section.TLabel").pack(anchor="w")
        row = ttk.Frame(audio, style="Surface.TFrame")
        row.pack(fill="x", pady=(8, 7))
        self.microphone = ttk.Combobox(row, state="readonly")
        self.microphone.configure(postcommand=lambda: style_combobox_list(self.microphone, self.palette))
        self.microphone.pack(side="left", fill="x", expand=True)
        self.microphone.bind("<<ComboboxSelected>>", self._change_microphone)
        ttk.Button(row, text="Refresh", command=self._refresh_microphones).pack(side="right", padx=(8, 0))
        level_row = ttk.Frame(audio, style="Surface.TFrame")
        level_row.pack(fill="x")
        self.level_label = ttk.Label(level_row, text="Input level", style="PanelMuted.TLabel")
        self.level_label.pack(side="left", padx=(0, 10))
        self.level = ttk.Progressbar(level_row, style="Input.Horizontal.TProgressbar", maximum=100, length=125)
        self.level.pack(side="left")
        pre_roll_row = ttk.Frame(audio, style="Surface.TFrame")
        pre_roll_row.pack(fill="x", pady=(8, 0))
        self.pre_roll = ttk.Combobox(pre_roll_row, state="readonly", width=9)
        self.pre_roll.configure(postcommand=lambda: style_combobox_list(self.pre_roll, self.palette))
        self.pre_roll.pack(side="right")
        self.pre_roll.bind("<<ComboboxSelected>>", self._change_pre_roll)
        ttk.Label(pre_roll_row, text="Microphone pre-roll", style="Surface.TLabel").pack(side="left")
        ttk.Label(
            audio,
            text="At 0 ms the ring buffer is off and the mic opens only for dictation. With pre-roll enabled, "
                 "the mic stays open and Windows may show its mic icon continuously. Before dictation, Dictate "
                 "keeps only the selected length of recent audio in memory.",
            style="PanelMuted.TLabel", wraplength=300,
        ).pack(anchor="w", pady=(7, 0))

        shell, models = self._panel(left)
        shell.pack(fill="x", pady=(0, 12))
        heading = ttk.Frame(models, style="Surface.TFrame")
        heading.pack(fill="x")
        ttk.Label(heading, text="Speech model", style="Section.TLabel").pack(side="left")
        self.backend = ttk.Label(heading, style="PanelMuted.TLabel")
        self.backend.pack(side="right")
        self.model = tk.StringVar(self.window)
        self._model_options = []
        for key, (label, _) in self.app.models.items():
            option = ttk.Radiobutton(
                models, text=label, variable=self.model, value=key,
                command=lambda model=key: self.app.select_model(model),
            )
            option.pack(anchor="w", pady=(3, 0))
            self._model_options.append(option)

        self.settings_notice = ttk.Label(left, wraplength=300)

        shell, controls = self._panel(right)
        shell.pack(fill="x", pady=(0, 12))
        ttk.Label(controls, text="Dictation controls", style="Section.TLabel").pack(anchor="w")
        self.shortcut_choice = self._settings_choice(controls, "Shortcut", SHORTCUTS.values())
        self.mode_choice = self._settings_choice(controls, "Mode", MODES.values())
        self.escape_cancels = tk.BooleanVar(self.window)
        self.preview = tk.BooleanVar(self.window)
        self.cursor_context = tk.BooleanVar(self.window)
        self.dictation_options = [
            ttk.Checkbutton(controls, text=label, variable=setting, command=self._save_dictation_preferences)
            for label, setting in (
                ("Esc cancels and keeps text in History", self.escape_cancels),
                ("Show live transcript while dictating", self.preview),
                ("Use text around the cursor", self.cursor_context),
            )
        ]
        for option in self.dictation_options:
            option.pack(anchor="w", pady=(4, 0))

        shell, history = self._panel(right)
        shell.pack(fill="x")
        ttk.Label(history, text="History and audio", style="Section.TLabel").pack(anchor="w")
        self.save_audio = tk.BooleanVar(self.window)
        self.wav_option = ttk.Checkbutton(
            history, text="Save WAV audio locally", variable=self.save_audio,
            command=lambda: self.app.set_save_wav(self.save_audio.get()),
        )
        self.wav_option.pack(anchor="w", pady=(8, 4))
        ttk.Label(
            history, text="Play back new recordings from History. Audio is kept with its transcript.",
            style="PanelMuted.TLabel", wraplength=300,
        ).pack(anchor="w")
        self.start_at_login = tk.BooleanVar(self.window)
        ttk.Checkbutton(
            history, text="Start Dictate at login", variable=self.start_at_login,
            command=self._toggle_start_at_login,
            state="normal" if self._startup_path else "disabled",
        ).pack(anchor="w", pady=(8, 0))
        ttk.Button(history, text="Manage replacements…", command=self._manage_replacements).pack(anchor="w", pady=(8, 0))
        return page

    def _add_history_replacement(self):
        selection = self.transcript.tag_ranges("sel")
        text = self.transcript.get(*selection).strip() if selection else ""
        if not text:
            self.history_feedback.configure(text="Select a word or phrase in the transcript first.")
            return
        existing = next((rule for rule in self.app.replacements.rules if rule[0].casefold() == text.casefold()), None)
        if self._edit_replacement(self.window, existing=existing, source=text):
            self.history_feedback.configure(text="Replacement saved for future dictations.")

    def _manage_replacements(self):
        dialog = tk.Toplevel(self.window)
        dialog.title("Text replacements")
        dialog.transient(self.window)
        dialog.geometry("580x340")
        dialog.minsize(460, 280)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        listing = ttk.Treeview(body, columns=("source", "replacement"), show="headings", selectmode="browse")
        listing.heading("source", text="Recognized text")
        listing.heading("replacement", text="Replacement")
        listing.column("source", width=235, minwidth=125)
        listing.column("replacement", width=235, minwidth=125)
        scroll = ttk.Scrollbar(body, command=listing.yview)
        listing.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(body)
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        scroll.pack(side="right", fill="y")
        listing.pack(fill="both", expand=True)
        status = ttk.Label(actions, style="Muted.TLabel", wraplength=230)
        status.pack(side="right")

        def selected_rule():
            selected = listing.selection()
            return self.app.replacements.rules[int(selected[0])] if selected else None

        def sync():
            for button in (edit, remove):
                button.configure(state="normal" if listing.selection() else "disabled")

        def refresh():
            listing.delete(*listing.get_children())
            for index, rule in enumerate(self.app.replacements.rules):
                listing.insert("", "end", iid=str(index), values=rule)
            sync()

        def edit_rule(existing=None):
            if self._edit_replacement(dialog, existing=existing):
                status.configure(text="")
                refresh()

        def remove_rule():
            rule = selected_rule()
            if rule:
                try:
                    self.app.set_replacements(self.app.replacements.remove(rule[0]))
                except (OSError, ValueError, yaml.YAMLError):
                    logger.exception("Could not remove text replacement")
                    status.configure(text="Could not remove this replacement.")
                else:
                    status.configure(text="")
                    refresh()

        ttk.Button(actions, text="Add…", command=edit_rule).pack(side="left")
        edit = ttk.Button(actions, text="Edit…", command=lambda: edit_rule(selected_rule()))
        edit.pack(side="left", padx=(8, 0))
        remove = ttk.Button(actions, text="Remove", command=remove_rule)
        remove.pack(side="left", padx=(8, 0))
        listing.bind("<<TreeviewSelect>>", lambda event: sync())
        listing.bind("<Double-1>", lambda event: edit_rule(selected_rule()) if listing.identify_row(event.y) else None)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        refresh()
        dialog.wait_visibility()
        dialog.grab_set()
        dialog.wait_window()

    def _edit_replacement(self, parent, *, existing=None, source=""):
        dialog = tk.Toplevel(parent)
        dialog.title("Edit replacement" if existing else "Add replacement")
        dialog.transient(parent)
        dialog.minsize(400, 200)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        recognized, value = existing or (source, "")
        inputs = []
        for label, text in (("Recognized text", recognized), ("Replacement", value)):
            ttk.Label(body, text=label).pack(anchor="w")
            entry = ttk.Entry(body)
            entry.insert(0, text)
            entry.pack(fill="x", pady=(4, 10))
            inputs.append(entry)
        first, replacement = inputs
        status = ttk.Label(body, style="Muted.TLabel", wraplength=360)
        status.pack(anchor="w")
        saved = False

        def save(event=None):
            nonlocal saved
            try:
                rules = self.app.replacements.update(
                    first.get(), replacement.get(), previous=existing[0] if existing else None,
                )
                self.app.set_replacements(rules)
            except ValueError as exc:
                status.configure(text=str(exc))
            except (OSError, yaml.YAMLError):
                logger.exception("Could not save text replacement")
                status.configure(text="Could not save this replacement.")
            else:
                saved = True
                dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Save", style="Action.TButton", command=save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", save)
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        previous_grab = parent.grab_current()
        dialog.wait_visibility()
        dialog.grab_set()
        focus = replacement if recognized else first
        focus.focus_set()
        focus.selection_range(0, tk.END)
        dialog.wait_window()
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        return saved

    def _settings_choice(self, parent, label, values):
        row = ttk.Frame(parent, style="Surface.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text=label, style="Surface.TLabel").pack(side="left")
        choice = ttk.Combobox(row, state="readonly", values=tuple(values), width=23)
        choice.configure(postcommand=lambda: style_combobox_list(choice, self.palette))
        choice.pack(side="right")
        choice.bind("<<ComboboxSelected>>", self._save_dictation_preferences)
        return choice

    def _save_dictation_preferences(self, event=None):
        self.app.set_dictation_preferences(
            shortcut=tuple(SHORTCUTS)[self.shortcut_choice.current()],
            mode=tuple(MODES)[self.mode_choice.current()],
            escape_cancels=self.escape_cancels.get(),
            live_preview=self.preview.get(),
            use_cursor_context=self.cursor_context.get(),
        )
        self._refresh_settings(force=True)

    def _toggle_start_at_login(self):
        try:
            set_start_at_login(self._startup_path, self.start_at_login.get())
        except Exception as exc:
            self.app.settings_error = f"Could not change startup settings. {exc}"
            logger.exception("Could not update the Windows Startup shortcut")
        else:
            self.app.settings_error = ""
        self._refresh_settings(force=True)

    def _select_page(self, name):
        self._page = name
        for key, page in self.pages.items():
            if key == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
            self._nav[key].configure(style="Selected.Nav.TButton" if key == name else "Nav.TButton")
        if name == "Settings":
            self._refresh_microphones()
        else:
            self._refresh_history()

    def _focus_search(self, event=None):
        self._select_page("History")
        self.search_box.focus_set()
        self.search_box.selection_range(0, tk.END)
        return "break"

    def show(self):
        self.window.deiconify()
        style_windows_titlebar(self.window, self._theme == "dark")
        self.window.lift()
        self.window.focus_force()
        if self._poll_timer is None:
            logger.info("Opened the Dictate window")
            self._select_page(self._page)
            self._check_system_appearance()
            self._poll()

    def hide(self):
        self.window.withdraw()
        logger.info("Closed the Dictate window to the tray")
        if self._poll_timer is not None:
            self.window.after_cancel(self._poll_timer)
            self._poll_timer = None
        self._stop_appearance_timer()
        self.stop_audio()

    def _poll(self):
        self._poll_timer = None
        self._update_status()
        if self._page == "Settings":
            self._refresh_settings()
        elif self._entries != self.app.history.recent():
            self._refresh_history()
        self._poll_timer = self.window.after(170, self._poll)

    def _update_status(self):
        self.shortcut_summary.configure(text=SHORTCUTS[self.app.hotkey_manager.shortcut])
        if self.app.is_paused:
            state, kind = "Dictation paused", "Warning"
        elif self.app.settings_activity:
            state, kind = "Getting ready", "Info"
        elif self.app.state == "recording":
            state, kind = "Recording", "Danger"
        elif self.app.state == "transcribing":
            state, kind = "Finishing dictation", "Info"
        elif self.app.transcriber is None or not self.app.recorder.microphone_ready:
            state, kind = "Setup needed", "Warning"
        elif self.app.transcriber.device == "cpu":
            state, kind = "Ready · CPU mode", "Warning"
        else:
            state, kind = "Ready to dictate", "Success"
        if (state, kind) != self._status_snapshot:
            self.status.configure(text="●  " + state, style=f"{kind}.Badge.TLabel")
            self._status_snapshot = (state, kind)

    def _refresh_microphones(self):
        try:
            choices = list(list_microphones())
        except Exception:
            logger.exception("Cannot list the available microphones")
            choices = []
        active = self.app.recorder.microphone
        if active and active not in choices:
            choices.append(active)
        self.microphone.configure(values=(SYSTEM_DEFAULT, *choices))
        self._refresh_settings(force=True)

    def _change_microphone(self, event=None):
        name = self.microphone.get()
        self.app.select_microphone(None if name == SYSTEM_DEFAULT else name)
        self._refresh_settings(force=True)

    def _change_pre_roll(self, event=None):
        milliseconds = int(self.pre_roll.get().removesuffix(" ms"))
        self.app.select_pre_roll(milliseconds / 1000)
        self._refresh_settings(force=True)

    def _refresh_settings(self, *, force=False):
        recorder = self.app.recorder
        self.level.configure(value=min(100, 155 * recorder.microphone_level() ** 0.6))
        busy = self.app.settings_busy
        notice, kind = recorder.input_issue, "Warning"
        if self.app.settings_activity:
            notice, kind = self.app.settings_activity, "Info"
        elif self.app.settings_error:
            notice, kind = self.app.settings_error, "Danger"
        engine = self.app.transcriber
        device = engine.device.upper() if engine else ""
        if not notice and device == "CPU":
            notice, kind = "Whisper is running on the CPU. Dictation may take longer to appear, especially for longer recordings.", "Warning"
        pre_roll_ms = round(recorder.ring_buffer_duration * 1000)
        hotkeys = self.app.hotkey_manager
        snapshot = (
            busy, notice, kind, recorder.microphone, self.app.model_name, device, self.app.save_wav, pre_roll_ms,
            hotkeys.shortcut, hotkeys.mode, hotkeys.escape_cancels, self.app.live_preview, self.app.use_cursor_context,
        )
        if not force and snapshot == self._settings_snapshot:
            return
        self._settings_snapshot = snapshot

        self.microphone.configure(state="disabled" if busy else "readonly")
        self.microphone.set(recorder.microphone or SYSTEM_DEFAULT)
        choices = sorted(set(PRE_ROLL_MS) | {pre_roll_ms})
        self.pre_roll.configure(state="disabled" if busy else "readonly", values=[f"{value} ms" for value in choices])
        self.pre_roll.set(f"{pre_roll_ms} ms")
        self.level_label.configure(text="Input level" if pre_roll_ms else "Input level (while dictating)")
        self.model.set(self.app.model_name or "")
        self.backend.configure(text=device)
        for option in [*self._model_options, self.wav_option]:
            option.configure(state="disabled" if busy else "normal")
        self.save_audio.set(self.app.save_wav)
        for choice in (self.shortcut_choice, self.mode_choice):
            choice.configure(state="disabled" if busy else "readonly")
        self.shortcut_choice.set(SHORTCUTS[hotkeys.shortcut])
        self.mode_choice.set(MODES[hotkeys.mode])
        for option in self.dictation_options:
            option.configure(state="disabled" if busy else "normal")
        self.escape_cancels.set(hotkeys.escape_cancels)
        self.preview.set(self.app.live_preview)
        self.cursor_context.set(self.app.use_cursor_context)
        self.start_at_login.set(bool(self._startup_path and self._startup_path.is_file()))
        if notice:
            self.settings_notice.configure(text=notice, style=f"{kind}.Notice.TLabel")
            self.settings_notice.pack(fill="x", pady=(10, 0))
        else:
            self.settings_notice.pack_forget()

    def _refresh_history(self):
        old_selection = self._selected
        follow_newest = not self.search.get() and self._entries and old_selection == self._entries[0]
        self._entries = self.app.history.recent()
        query = self.search.get().strip().casefold()
        entries = [entry for entry in self._entries if query in entry.text.casefold()]

        self.history_list.delete(*self.history_list.get_children())
        self._visible_entries.clear()
        selection = None
        for index, entry in enumerate(entries):
            item = str(index)
            self._visible_entries[item] = entry
            summary = " ".join(entry.text.split())
            self.history_list.insert("", "end", iid=item, values=(entry.created_at.strftime("%d %b · %H:%M"), summary))
            if old_selection and old_selection.created_at == entry.created_at and not follow_newest:
                selection = item

        self.entry_count.configure(text=f"{len(entries)} dictation" if len(entries) == 1 else f"{len(entries)} dictations")
        if entries:
            current = selection or "0"
            self.history_list.selection_set(current)
            self.history_list.see(current)
        self._show_selected()

    def _show_selected(self, event=None):
        selected = self.history_list.selection()
        entry = self._visible_entries.get(selected[0]) if selected else None
        if not self._selected or not entry or self._selected.created_at != entry.created_at:
            self.stop_audio()
        self._selected = entry
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.insert("1.0", entry.text if entry else "")
        self.transcript.configure(state="disabled")
        if entry:
            self.details_title.configure(text=entry.created_at.strftime("%d %B %Y · %H:%M"))
            self.details_model.configure(text=entry.model or "")
        else:
            self.details_title.configure(text="No matches" if self.search.get().strip() else "No dictations yet")
            self.details_model.configure(text="")
        self.copy_button.configure(state="normal" if entry else "disabled")
        self.replacement_button.configure(state="normal" if entry else "disabled")
        can_play = bool(entry and self.app.history.audio_path(entry))
        self.audio_button.configure(state="normal" if can_play else "disabled")
        self.history_feedback.configure(text="")

    def _copy_selected(self):
        if self._selected:
            try:
                self.app.copy_history_entry(self._selected)
                self.history_feedback.configure(text="Copied")
            except (OSError, RuntimeError, ValueError):
                self.history_feedback.configure(text="Clipboard unavailable. Try copying again.")

    def _toggle_audio(self):
        if self._audio_timer:
            self.stop_audio()
            return
        path = self.app.history.audio_path(self._selected) if self._selected else None
        if path is None:
            self.audio_button.state(("disabled",))
            self.history_feedback.configure(text="Recording unavailable")
            return
        try:
            with wave.open(str(path), "rb") as wav:
                duration_ms = round(1000 * wav.getnframes() / wav.getframerate())
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (OSError, RuntimeError, wave.Error):
            self.history_feedback.configure(text="Could not play this recording")
            return
        self.audio_button.configure(text="Stop audio")
        self._audio_timer = self.window.after(max(1, duration_ms), self.stop_audio)

    def stop_audio(self):
        if self._audio_timer is not None:
            self.window.after_cancel(self._audio_timer)
            self._audio_timer = None
            winsound.PlaySound(None, 0)
            self.audio_button.configure(text="Play audio")
