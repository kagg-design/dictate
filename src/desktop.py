"""The Dictate window and recording overlay share one Tk event loop."""

from queue import Empty, SimpleQueue
import ctypes
from ctypes import wintypes
import os
import threading
import tkinter as tk
from tkinter import ttk
import wave
import winsound

from PIL import Image, ImageTk
import yaml

from src.config import CUE_VOLUME_OPTIONS, save_settings
from src.desktop_theme import (
    APPEARANCES,
    DesktopStyles,
    style_combobox_list,
    style_windows_titlebar,
    system_appearance,
)
from src.logger import logger
from src.history import WAV_RETENTION_OPTIONS
from src.hotkey import MODES
from src.overlay import RecordingOverlay
from src.paths import APP_ROOT
from src.postprocess import installed_models
from src.recording_cues import RecordingCuePlayer
from src.recorder import list_microphones
from src.settings_view import SCROLL_STEP, SettingsView
from src.startup import set_start_at_login, startup_shortcut
from src.window_position import WindowPosition


SYSTEM_DEFAULT = "System default"
PRE_ROLL_MS = (0, 250, 500, 1000)
ICON = APP_ROOT / "icon.ico"


class DesktopApp:
    def __init__(self):
        self.thread = None
        self._cue_player = None
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

    def start(self, app, *, appearance="System", window_position=None, window_page="History",
              replacements_window_position=None, collapsed_settings_sections=()):
        self._cue_player = RecordingCuePlayer(
            lambda: app.recording_sounds, lambda: app.recording_cue_volume / 100,
            lambda: app.duck_background_audio,
        )
        self.thread = threading.Thread(
            target=self._run,
            args=(app, appearance, window_position, window_page, replacements_window_position, collapsed_settings_sections),
            name="DesktopUI", daemon=True,
        )
        self.thread.start()

    def show(self, icon=None, item=None):
        self._commands.put("show")

    def show_running_instance(self):
        return bool(self._activation and self._win32.SetEvent(self._activation))

    def set_recording(self, active):
        player = self._cue_player
        if player is not None:
            player.set_recording(active)
        self._commands.put(active)

    def close(self):
        if self._cue_player is not None:
            self._cue_player.close()
            self._cue_player = None
        self._commands.put("close")
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self._activation and not (self.thread and self.thread.is_alive()):
            self._win32.CloseHandle(self._activation)
            self._activation = None

    def _run(self, app, appearance, window_position, window_page, replacements_window_position,
             collapsed_settings_sections):
        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            window = DictateWindow(
                root, app, appearance, window_position, window_page, replacements_window_position,
                collapsed_settings_sections=collapsed_settings_sections,
            )
            overlay = RecordingOverlay(root, font=window._styles.font)
            show_pending = False
            recording = False

            def dispatch():
                nonlocal show_pending, recording
                if self._activation and self._win32.WaitForSingleObject(self._activation, 0) == 0:
                    show_pending = True
                while True:
                    try:
                        command = self._commands.get_nowait()
                    except Empty:
                        break
                    if command == "close":
                        window.close_replacements()
                        root.quit()
                        return
                    if command == "show":
                        show_pending = True
                    elif command != recording:
                        recording = command
                        overlay.set_recording(recording)
                        if recording:
                            window.stop_audio()
                overlay.update_feedback(app.preview_text, app.state == "transcribing", show_preview=app.live_preview)
                if show_pending and not app.dictation_busy:
                    window.show()
                    show_pending = False
                root.after(33, dispatch)

            root.after(0, dispatch)
            root.mainloop()
            window._save_window_preferences()
            window.position.save()
            window.stop_audio()
        except Exception:
            logger.exception("Dictate desktop window failed")
        finally:
            if root is not None:
                root.destroy()


class DictateWindow:
    def __init__(self, root, app, appearance="System", window_position=None, window_page="History",
                 replacements_window_position=None, *, collapsed_settings_sections=()):
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.title("Dictate")
        if ICON.is_file():
            self.window.iconbitmap(str(ICON))
        self.window.geometry("1080x690")
        self.window.minsize(850, 360)
        self.position = WindowPosition(self.window, "window_position", window_position)
        self._replacements_position = replacements_window_position
        self._close_replacements = None
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.bind("<Escape>", lambda event: self.hide())
        self.window.bind("<Control-f>", self._focus_search)
        self.window.bind("<FocusOut>", lambda event: self.window.after_idle(self._check_shortcut_focus), add="+")

        self._entries = ()
        self._visible_entries = {}
        self._selected = None
        self._audio_timer = None
        self._poll_timer = None
        self._presented_page = None
        self._window_preferences_timer = None
        self._status_snapshot = None
        self._settings_snapshot = None
        self._last_level = None
        self._shortcut_capture = None
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
        self._build_layout(collapsed_settings_sections)
        self._page = window_page if isinstance(window_page, str) and window_page in self.pages else "History"
        self._saved_window_preferences = self._window_preferences()
        self.window.bind("<MouseWheel>", self._scroll_settings, add="+")
        self.window.bind("<FocusIn>", self._reveal_settings_control, add="+")
        self.window.bind("<Tab>", lambda event: self._move_settings_focus(event, 1), add="+")
        self.window.bind("<Shift-Tab>", lambda event: self._move_settings_focus(event, -1), add="+")

    def _resolve_theme(self):
        return system_appearance() if self._appearance == "System" else self._appearance.lower()

    def _sync_appearance(self):
        theme = self._resolve_theme()
        if theme != self._theme:
            self._theme = theme
            self.palette = self._styles.apply(theme)
            self.window.configure(bg=self.palette.app_background)
            self.settings_canvas.apply_palette(self.palette)
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

    def _build_layout(self, collapsed_settings_sections):
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
        self.pages = {"History": self._build_history(), "Settings": self._build_settings(collapsed_settings_sections)}

    def _panel(self, parent, padding=8):
        shell = ttk.Frame(parent, style="Card.TFrame", padding=8)
        body = ttk.Frame(shell, style="Surface.TFrame", padding=padding)
        body.pack(fill="both", expand=True)
        return shell, body

    def _build_history(self):
        page = ttk.Frame(self.content)
        header = ttk.Frame(page)
        header.pack(fill="x")
        ttk.Label(header, text="Dictation history", style="Heading.TLabel").pack(side="left")
        self.recordings_folder = ttk.Button(
            header, text="Open recordings folder", command=self._open_recordings_folder, state="disabled",
        )
        self.recordings_folder.pack(side="right")
        searchbar = ttk.Frame(page)
        searchbar.pack(fill="x", pady=(17, 12))
        ttk.Label(searchbar, text="Search").pack(side="left", padx=(0, 9))
        self.search = tk.StringVar(self.window)
        self.search_box = ttk.Entry(searchbar, textvariable=self.search, width=28)
        self.search_box.pack(side="left")
        ttk.Button(searchbar, text="Clear", command=lambda: self.search.set("")).pack(side="left", padx=(7, 0))
        self.pinned_only = tk.BooleanVar(self.window)
        ttk.Checkbutton(
            searchbar, text="Pinned only", variable=self.pinned_only, command=self._refresh_history,
        ).pack(side="left", padx=(14, 0))
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

        header = ttk.Frame(details, style="Surface.TFrame")
        header.pack(fill="x")
        self.details_title = ttk.Label(header, style="Section.TLabel")
        self.details_title.pack(side="left")
        self.pin_button = ttk.Button(header, text="Pin", command=self._toggle_pin)
        self.pin_button.pack(side="right")
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

    def _build_settings(self, collapsed_settings_sections):
        page = ttk.Frame(self.content)
        canvas = SettingsView(
            page, font=self._styles.font, display_font=self._styles.display_font, palette=self.palette,
            collapsed=collapsed_settings_sections, on_section_change=self._schedule_window_preferences_save,
        )
        self.settings_canvas = canvas
        scroll = ttk.Scrollbar(page, style="Settings.Vertical.TScrollbar", command=canvas.yview)
        scroll.pack(side="right", fill="y", padx=(10, 0))

        def update_scrollbar(first, last):
            scroll.set(first, last)
            if float(first) == 0 and float(last) == 1:
                scroll.state(("disabled",))
            else:
                scroll.state(("!disabled",))

        canvas.configure(yscrollcommand=update_scrollbar)
        canvas.pack(side="left", fill="both", expand=True)
        audio = canvas.add_section("audio", "Audio and recognition")
        row = self._settings_row(audio, "Microphone")
        self.microphone = self._settings_combo(row, 28, (), self._change_microphone)
        ttk.Button(row, style="Settings.TButton", text="Refresh", command=self._refresh_microphones).pack(
            side="left", padx=(8, 0)
        )
        meter = canvas.add_row(audio, "Input level")
        self.level_label = meter.label
        self.level = ttk.Progressbar(meter.controls, style="Input.Horizontal.TProgressbar", maximum=100, length=214)
        self.level.pack()
        row = self._settings_row(
            audio, "Microphone pre-roll",
            "Above 0 ms, Dictate keeps the microphone open and holds only the selected amount of recent audio "
            "in memory. Windows may show its mic indicator. At 0 ms, the mic opens only while dictating.",
        )
        self.pre_roll = self._settings_combo(row, 9, (), self._change_pre_roll)
        row = self._settings_row(audio, "Speech model")
        self.backend = ttk.Label(row, style="SettingsDetail.TLabel")
        self.backend.pack(side="left", padx=(0, 10))
        self.model_labels = {key: label for key, (label, _) in self.app.models.items()}
        self.model = self._settings_combo(row, 24, self.model_labels.values(), self._change_model)
        row = self._settings_row(audio, "Recording cue volume")
        self.cue_volume = self._settings_combo(
            row, 8, (f"{volume}%" for volume in CUE_VOLUME_OPTIONS), self._save_audio_feedback,
        )
        self.duck_audio = tk.BooleanVar(self.window)
        self.duck_option = self._settings_switch(
            audio, "Lower background audio while recording", self.duck_audio, self._save_audio_feedback,
        )

        controls = canvas.add_section("dictation", "Dictation controls")
        self.shortcut_button = self._shortcut_button(controls, "Dictation shortcut", "shortcut")
        self.insert_last_button = self._shortcut_button(controls, "Insert last shortcut", "insert_last_shortcut")
        row = self._settings_row(controls, "Activation mode")
        self.mode_choice = self._settings_combo(row, 21, MODES.values(), self._save_dictation_preferences)
        self.escape_cancels = tk.BooleanVar(self.window)
        self.preview = tk.BooleanVar(self.window)
        self.recording_sounds = tk.BooleanVar(self.window)
        self.cursor_context = tk.BooleanVar(self.window)
        self.dictation_options = [
            self._settings_switch(controls, label, setting, self._save_dictation_preferences, detail=detail)
            for label, setting, detail in (
                ("Cancel with Esc", self.escape_cancels, "Keep recognized text in History."),
                ("Show live transcript", self.preview, None),
                ("Play recording sounds", self.recording_sounds, None),
                ("Use text around the cursor", self.cursor_context, None),
            )
        ]

        processing = canvas.add_section("postprocessing", "Local post-processing")
        row = self._settings_row(processing, "Ollama model")
        self.ollama_model = self._settings_combo(row, 24, (), self._save_postprocessing)
        self.ollama_model.set(self.app.postprocess_model)
        self.ollama_model.pack(side="left")
        self.refresh_ollama = ttk.Button(row, text="Refresh", style="Settings.TButton", command=self._refresh_ollama_models)
        self.refresh_ollama.pack(side="left", padx=(4, 0))
        self.ollama_status = ttk.Label(row, style="SettingsDetail.TLabel")
        self.ollama_status.pack(side="left", padx=(8, 0))
        self._ollama_results = SimpleQueue()
        self._loading_ollama = False
        self._requested_ollama = False
        self.postprocess_button = self._shortcut_button(processing, "Dictate with editing", "postprocess_shortcut")
        self.settings_canvas.add_row(processing, "Press the editing shortcut once to start and again to finish.")

        history = canvas.add_section("history", "History and startup")
        self.save_audio = tk.BooleanVar(self.window)
        self.wav_option = self._settings_switch(
            history, "Save WAV audio locally", self.save_audio,
            lambda: self.app.set_save_wav(self.save_audio.get()),
            detail="Play back audio saved with new dictations in History.",
        )
        row = self._settings_row(
            history, "Delete unpinned WAV audio after",
            "Keeps the transcript available in History.",
        )
        self.wav_retention = self._settings_combo(row, 22, WAV_RETENTION_OPTIONS.values(), self._set_wav_retention)
        self.start_at_login = tk.BooleanVar(self.window)
        login = self._settings_switch(history, "Start Dictate at login", self.start_at_login, self._toggle_start_at_login)
        if self._startup_path is None:
            login.state(("disabled",))
        row = self._settings_row(history, "Text replacements")
        ttk.Button(row, style="Settings.TButton", text="Manage replacements…",
                   command=self._manage_replacements).pack()
        return page

    def _settings_row(self, section, text, detail=None):
        return self.settings_canvas.add_row(section, text, detail).controls

    def _set_wav_retention(self, event=None):
        self.app.set_wav_retention(tuple(WAV_RETENTION_OPTIONS)[self.wav_retention.current()])
        self._refresh_settings(force=True)

    def _save_audio_feedback(self, event=None):
        self.app.set_audio_feedback(CUE_VOLUME_OPTIONS[self.cue_volume.current()], self.duck_audio.get())
        self._refresh_settings(force=True)

    def _settings_switch(self, parent, label, variable, command, *, detail=None):
        row = self._settings_row(parent, label, detail)
        switch = ttk.Checkbutton(row, text=label, style="Switch.TCheckbutton", variable=variable, command=command)
        switch.pack()
        return switch

    def _settings_combo(self, parent, width, values, command):
        choice = ttk.Combobox(parent, style="Settings.TCombobox", state="readonly", values=tuple(values), width=width)
        choice.configure(postcommand=lambda: style_combobox_list(choice, self.palette))
        choice.pack(side="left")
        choice.bind("<<ComboboxSelected>>", command)
        choice.bind("<MouseWheel>", self._scroll_settings)
        return choice

    def _in_settings_view(self, widget):
        while widget is not None:
            if widget is self.settings_canvas:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _scroll_settings(self, event):
        if self._page == "Settings" and event.delta and self._in_settings_view(event.widget):
            self.settings_canvas.yview_scroll(-int(event.delta / 40) or (-1 if event.delta > 0 else 1), "units")
            return "break"

    def _move_settings_focus(self, event, direction):
        if (self._page == "Settings" and self._in_settings_view(event.widget)
                and self.settings_canvas.focus_adjacent_control(event.widget, direction)):
            return "break"

    def _reveal_settings_control(self, event):
        canvas = self.settings_canvas
        widget = event.widget
        if self._page != "Settings" or widget is canvas or not self._in_settings_view(widget):
            return
        top = widget.winfo_rooty() - canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        if top < 0:
            canvas.yview_scroll(top // SCROLL_STEP, "units")
        elif bottom > canvas.winfo_height():
            clipped = bottom - canvas.winfo_height()
            canvas.yview_scroll((clipped + SCROLL_STEP - 1) // SCROLL_STEP, "units")

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
        dialog.withdraw()
        dialog.title("Text replacements")
        dialog.transient(self.window)
        dialog.geometry("580x340")
        dialog.minsize(460, 280)
        position = WindowPosition(dialog, "replacements_window_position", self._replacements_position)

        def close():
            self._replacements_position = position.save()
            self._close_replacements = None
            dialog.destroy()

        self._close_replacements = close
        dialog.protocol("WM_DELETE_WINDOW", close)
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
        dialog.bind("<Escape>", lambda event: close())
        refresh()
        position.show()
        if not dialog.winfo_viewable():
            dialog.wait_visibility()
        dialog.grab_set()
        dialog.wait_window()

    def close_replacements(self):
        if self._close_replacements is not None:
            self._close_replacements()

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
        fuzzy = tk.BooleanVar(dialog, recognized in self.app.replacements.fuzzy_sources)
        ttk.Checkbutton(body, text="Also correct similar spellings", variable=fuzzy).pack(anchor="w", pady=(0, 8))
        status = ttk.Label(body, style="Muted.TLabel", wraplength=360)
        status.pack(anchor="w")
        saved = False

        def save(event=None):
            nonlocal saved
            try:
                rules = self.app.replacements.update(
                    first.get(), replacement.get(), previous=existing[0] if existing else None,
                    fuzzy=fuzzy.get(),
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

    def _shortcut_button(self, parent, label, slot):
        row = self._settings_row(parent, label)
        button = ttk.Button(
            row, style="Shortcut.Settings.TButton", width=19, command=lambda: self._capture_shortcut(slot)
        )
        if slot != "shortcut":
            disable = ttk.Button(
                row, text="Turn off", style="Settings.TButton", command=lambda: self._save_shortcut(slot, None)
            )
            disable.pack(side="right", padx=(4, 0))
            if slot == "insert_last_shortcut":
                self.disable_insert_last = disable
            else:
                self.disable_postprocess = disable
        button.pack(side="right")
        return button

    def _capture_shortcut(self, slot):
        if self._shortcut_capture is not None:
            self._cancel_shortcut_capture()
            return
        results = SimpleQueue()
        if self.app.hotkey_manager.begin_capture(results):
            self._shortcut_capture = (slot, results)
            self.app.settings_error = ""
            self._refresh_settings(force=True)

    def _cancel_shortcut_capture(self):
        if self._shortcut_capture is not None:
            self.app.hotkey_manager.cancel_capture()
            self._shortcut_capture = None
            self._refresh_settings(force=True)

    def _check_shortcut_focus(self):
        if self._shortcut_capture is not None and self.window.focus_displayof() is None:
            self._cancel_shortcut_capture()

    def _save_shortcut(self, slot, binding):
        self._cancel_shortcut_capture()
        if slot == "postprocess_shortcut":
            self.app.set_postprocessing(self.ollama_model.get(), binding)
            self._refresh_settings(force=True)
        else:
            self._save_dictation_preferences(**{slot: binding})

    def _save_postprocessing(self, event=None):
        self.app.set_postprocessing(self.ollama_model.get(), self.app.hotkey_manager.postprocess_shortcut)
        self._refresh_settings(force=True)

    def _refresh_ollama_models(self):
        if self._loading_ollama:
            return
        self._requested_ollama = self._loading_ollama = True
        self.refresh_ollama.configure(state="disabled")

        def load():
            try:
                self._ollama_results.put((installed_models(), None))
            except ValueError as exc:
                self._ollama_results.put(((), str(exc)))

        threading.Thread(target=load, name="DictateOllamaModels", daemon=True).start()

    def _poll_ollama_models(self):
        try:
            models, error = self._ollama_results.get_nowait()
        except Empty:
            return
        self._loading_ollama = False
        self.ollama_model.configure(values=models)
        self.refresh_ollama.configure(state="normal")
        self.ollama_status.configure(text="Ollama unavailable" if error else ("No local models" if not models else ""))
        self._refresh_settings(force=True)

    def _poll_shortcut_capture(self):
        if self._shortcut_capture is None:
            return
        slot, results = self._shortcut_capture
        try:
            binding, error = results.get_nowait()
        except Empty:
            return
        self._shortcut_capture = None
        if binding is not None:
            self._save_shortcut(slot, binding)
        else:
            self.app.settings_error = error
            self._refresh_settings(force=True)

    def _save_dictation_preferences(self, event=None, **bindings):
        hotkeys = self.app.hotkey_manager
        self.app.set_dictation_preferences(
            shortcut=bindings.get("shortcut", hotkeys.shortcut),
            mode=tuple(MODES)[self.mode_choice.current()],
            insert_last_shortcut=bindings.get("insert_last_shortcut", hotkeys.insert_last_shortcut),
            escape_cancels=self.escape_cancels.get(),
            live_preview=self.preview.get(),
            recording_sounds=self.recording_sounds.get(),
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
        self._cancel_shortcut_capture()
        changed = name != self._page
        self._page = name
        if self._presented_page != name:
            if self._presented_page is not None:
                self.pages[self._presented_page].pack_forget()
                self._nav[self._presented_page].configure(style="Nav.TButton")
            self.pages[name].pack(fill="both", expand=True)
            self._nav[name].configure(style="Selected.Nav.TButton")
            self._presented_page = name
        if changed:
            self._schedule_window_preferences_save()
        if name == "Settings":
            self._refresh_microphones()
            if not self._requested_ollama:
                self._refresh_ollama_models()
        else:
            self._refresh_history()

    def _window_preferences(self):
        return {"window_page": self._page, "collapsed_settings_sections": self.settings_canvas.collapsed_sections()}

    def _schedule_window_preferences_save(self):
        if self._window_preferences_timer is not None:
            self.window.after_cancel(self._window_preferences_timer)
        self._window_preferences_timer = self.window.after(250, self._save_window_preferences)

    def _save_window_preferences(self):
        if self._window_preferences_timer is not None:
            self.window.after_cancel(self._window_preferences_timer)
            self._window_preferences_timer = None
        preferences = self._window_preferences()
        if preferences == self._saved_window_preferences:
            return
        try:
            save_settings(preferences)
        except (OSError, ValueError, yaml.YAMLError):
            logger.exception("Could not save the Dictate window preferences")
        else:
            self._saved_window_preferences = preferences

    def _focus_search(self, event=None):
        self._select_page("History")
        self.search_box.focus_set()
        self.search_box.selection_range(0, tk.END)
        return "break"

    def show(self):
        self.position.show()
        style_windows_titlebar(self.window, self._theme == "dark")
        self.window.lift()
        self.window.focus_force()
        if self._poll_timer is None:
            logger.info("Opened the Dictate window")
            self._select_page(self._page)
            self._check_system_appearance()
            self._poll()

    def hide(self):
        self.position.save()
        self._cancel_shortcut_capture()
        self._save_window_preferences()
        self.window.withdraw()
        logger.info("Closed the Dictate window to the tray")
        if self._poll_timer is not None:
            self.window.after_cancel(self._poll_timer)
            self._poll_timer = None
        self._stop_appearance_timer()
        self.stop_audio()

    def _poll(self):
        self._poll_ollama_models()
        self._poll_timer = None
        self._update_status()
        if self._page == "Settings":
            self._poll_shortcut_capture()
            self._refresh_settings()
        elif self._entries != self.app.history.recent():
            self._refresh_history()
        self._poll_timer = self.window.after(170, self._poll)

    def _update_status(self):
        shortcut = self.app.hotkey_manager.shortcut.label
        if self.shortcut_summary.cget("text") != shortcut:
            self.shortcut_summary.configure(text=shortcut)
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
        values = (SYSTEM_DEFAULT, *choices)
        if self.microphone.cget("values") != values:
            self.microphone.configure(values=values)
        self._refresh_settings()

    def _change_microphone(self, event=None):
        name = self.microphone.get()
        self.app.select_microphone(None if name == SYSTEM_DEFAULT else name)
        self._refresh_settings(force=True)

    def _change_pre_roll(self, event=None):
        milliseconds = int(self.pre_roll.get().removesuffix(" ms"))
        self.app.select_pre_roll(milliseconds / 1000)
        self._refresh_settings(force=True)

    def _change_model(self, event=None):
        index = self.model.current()
        if index >= 0:
            self.app.select_model(tuple(self.model_labels)[index])
            self._refresh_settings(force=True)

    def _refresh_settings(self, *, force=False):
        recorder = self.app.recorder
        level = min(100, round(155 * recorder.microphone_level() ** 0.6))
        if level != self._last_level:
            self.level.configure(value=level)
            self._last_level = level
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
            hotkeys.shortcut, hotkeys.mode, hotkeys.insert_last_shortcut, hotkeys.escape_cancels,
            hotkeys.postprocess_shortcut, self.app.postprocess_model,
            self.app.live_preview, self.app.recording_sounds, self.app.use_cursor_context,
            self.app.recording_cue_volume, self.app.duck_background_audio,
        )
        if not force and snapshot == self._settings_snapshot:
            return
        self._settings_snapshot = snapshot

        self.microphone.configure(state="disabled" if busy else "readonly")
        self.microphone.set(recorder.microphone or SYSTEM_DEFAULT)
        choices = sorted(set(PRE_ROLL_MS) | {pre_roll_ms})
        self.pre_roll.configure(state="disabled" if busy else "readonly", values=[f"{value} ms" for value in choices])
        self.pre_roll.set(f"{pre_roll_ms} ms")
        self.settings_canvas.itemconfigure(
            self.level_label, text="Input level" if pre_roll_ms else "Input level (while dictating)"
        )
        self.model.configure(state="disabled" if busy or not self.model_labels else "readonly")
        self.model.set(self.model_labels.get(self.app.model_name, ""))
        self.backend.configure(text=device)
        self.wav_option.configure(state="disabled" if busy else "normal")
        self.save_audio.set(self.app.save_wav)
        self.mode_choice.configure(state="disabled" if busy else "readonly")
        self.mode_choice.set(MODES[hotkeys.mode])
        capture_slot = self._shortcut_capture[0] if self._shortcut_capture is not None else None
        for slot, button, binding in (
            ("shortcut", self.shortcut_button, hotkeys.shortcut),
            ("insert_last_shortcut", self.insert_last_button, hotkeys.insert_last_shortcut),
            ("postprocess_shortcut", self.postprocess_button, hotkeys.postprocess_shortcut),
        ):
            button.configure(
                text="Press 2 or 3 keys…" if capture_slot == slot else (binding.label if binding else "Off"),
                state="disabled" if busy or (capture_slot is not None and capture_slot != slot) else "normal",
            )
        self.disable_insert_last.configure(
            state="disabled" if busy or capture_slot is not None or hotkeys.insert_last_shortcut is None else "normal"
        )
        self.disable_postprocess.configure(
            state="disabled" if busy or capture_slot is not None or hotkeys.postprocess_shortcut is None else "normal"
        )
        self.ollama_model.configure(state="disabled" if busy else "readonly")
        self.ollama_model.set(self.app.postprocess_model)
        for option in self.dictation_options:
            option.configure(state="disabled" if busy else "normal")
        self.escape_cancels.set(hotkeys.escape_cancels)
        self.preview.set(self.app.live_preview)
        self.recording_sounds.set(self.app.recording_sounds)
        self.cue_volume.configure(state="disabled" if busy or not self.app.recording_sounds else "readonly")
        self.cue_volume.set(f"{self.app.recording_cue_volume}%")
        self.duck_option.configure(state="disabled" if busy else "normal")
        self.duck_audio.set(self.app.duck_background_audio)
        self.wav_retention.set(WAV_RETENTION_OPTIONS[self.app.history.audio_retention_days])
        self.cursor_context.set(self.app.use_cursor_context)
        self.start_at_login.set(bool(self._startup_path and self._startup_path.is_file()))
        self.settings_canvas.set_notice(notice or "", kind)

    def _refresh_history(self):
        old_selection = self._selected
        query = self.search.get().strip().casefold()
        pinned = self.pinned_only.get()
        follow_newest = not (query or pinned) and self._entries and old_selection == self._entries[0]
        self._entries = self.app.history.recent()
        entries = [entry for entry in self._entries if (entry.pinned or not pinned) and query in entry.text.casefold()]
        self.recordings_folder.configure(state="normal" if self.app.history.audio_dir.is_dir() else "disabled")

        self.history_list.delete(*self.history_list.get_children())
        self._visible_entries.clear()
        selection = None
        for index, entry in enumerate(entries):
            item = str(index)
            self._visible_entries[item] = entry
            summary = " ".join(entry.text.split())
            if entry.pinned:
                summary = "★ " + summary
            self.history_list.insert("", "end", iid=item, values=(entry.created_at.strftime("%d %b · %H:%M"), summary))
            if old_selection and old_selection.id == entry.id and not follow_newest:
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
        if not self._selected or not entry or self._selected.id != entry.id:
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
            empty_label = "No pinned dictations" if self.pinned_only.get() else "No dictations yet"
            self.details_title.configure(text="No matches" if self.search.get().strip() else empty_label)
            self.details_model.configure(text="")
        self.copy_button.configure(state="normal" if entry else "disabled")
        self.replacement_button.configure(state="normal" if entry else "disabled")
        self.pin_button.configure(
            text="Unpin" if entry and entry.pinned else "Pin", state="normal" if entry else "disabled"
        )
        can_play = bool(entry and self.app.history.audio_path(entry))
        self.audio_button.configure(state="normal" if can_play else "disabled")
        self.history_feedback.configure(text="")

    def _toggle_pin(self):
        entry = self._selected
        if entry and self.app.history.set_pinned(entry, not entry.pinned):
            self._refresh_history()
        elif entry:
            self.history_feedback.configure(text="Could not update this pin.")

    def _open_recordings_folder(self):
        try:
            os.startfile(self.app.history.audio_dir)
        except OSError:
            self.history_feedback.configure(text="Could not open the recordings folder.")

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
