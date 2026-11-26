import queue
import threading
from dataclasses import dataclass
from PIL import Image, ImageDraw
import pystray
from src.logger import logger
from src.inserter import paste_text
from src.clipboard import copy_text
from src.history import DictationHistory
from src.config import save_recognition_model, save_settings
from src.live_transcription import CompletedCapture, LiveTranscription
from src.replacements import TextReplacements


REFRESH_HISTORY_MENU = object()


@dataclass(frozen=True)
class ModelSelection:
    model: str
    persist: bool


@dataclass(frozen=True)
class MicrophoneSelection:
    microphone: str | None


@dataclass(frozen=True)
class PreRollSelection:
    duration: float


class SystemTrayApp:
    def __init__(
        self, transcriber, recorder, hotkey_manager, on_exit_callback,
        show_startup_notifications=True, history=None, models=None,
        open_window=None, save_wav=False, live_preview=False, use_cursor_context=True,
        text_replacements=None, fuzzy_replacements=(), wav_retention_days=0,
    ):
        """
        Coordinates the system tray interface, state changes, and background tasks.
        :param transcriber: Active speech engine, if already loaded.
        :param recorder: AudioRecorder instance.
        :param hotkey_manager: HotkeyManager instance.
        :param on_exit_callback: Cleanup function on app shutdown.
        :param show_startup_notifications: Boolean flag to enable/disable tray startup balloon notifications.
        """
        self.transcriber = transcriber
        self.recorder = recorder
        self.hotkey_manager = hotkey_manager
        self.on_exit_callback = on_exit_callback
        self.show_startup_notifications = show_startup_notifications
        self.history = history if history is not None else DictationHistory(audio_retention_days=wav_retention_days)
        self.models = models or {}
        self.model_name = None
        self._capture_lock = threading.Lock()
        self._capture = None
        self._pending_captures = 0
        self._loading_model = False
        self._changing_microphone = False
        self._changing_pre_roll = False
        self.settings_error = ""
        self.save_wav = save_wav
        self.live_preview = live_preview
        self.use_cursor_context = use_cursor_context
        self.replacements = TextReplacements.from_config(text_replacements, fuzzy_replacements)
        self.preview_text = ""
        
        self.state = 'idle'
        self.is_paused = False
        
        # Thread-safe queue for processing recorded audio buffers
        self.task_queue = queue.Queue()
        self.worker_thread = None
        
        # Thread-safe queue and thread for sequential UI icon updates (fixes Win32 thread-affinity bugs)
        self.ui_queue = queue.Queue()
        self.ui_thread = None
        
        self.running = True
        
        if open_window is None:
            first_item = pystray.MenuItem("Dictation History", pystray.Menu(self._history_menu_items))
        else:
            first_item = pystray.MenuItem("Open Dictate", open_window, default=True)

        self.menu = pystray.Menu(
            first_item,
            pystray.MenuItem(
                "Copy Last Dictation", self.copy_latest, enabled=lambda item: bool(self.history.recent())
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                text=lambda item: "Resume Dictation" if self.is_paused else "Pause Dictation",
                action=self.toggle_pause
            ),
            pystray.MenuItem(
                text="Exit",
                action=self.exit_app
            )
        )
        
        # Setup the pystray icon
        self.icon = pystray.Icon(
            name="dictate",
            icon=self._generate_icon_image('idle'),
            title="Dictate Tool",
            menu=self.menu
        )

    @property
    def settings_busy(self):
        return bool(self.settings_activity or self.dictation_busy)

    @property
    def dictation_busy(self):
        return bool(self._capture or self._pending_captures)

    @property
    def settings_activity(self):
        if self._loading_model:
            return "Loading speech model…"
        if self._changing_microphone:
            return "Switching microphone…"
        if self._changing_pre_roll:
            return "Updating microphone pre-roll…"
        return ""

    def select_model(self, model, *, persist=True):
        with self._capture_lock:
            if not self.running or model not in self.models or model == self.model_name or self.settings_busy:
                return
            self.settings_error = ""
            self._loading_model = True
            self.task_queue.put(ModelSelection(model, persist))

    def _load_model(self, selection):
        label, create = self.models[selection.model]
        engine = None
        self.show_notification("Loading Speech Model", label)
        try:
            engine = create()
            engine.load_model()
            if selection.persist:
                save_recognition_model(selection.model)
        except Exception as exc:
            if engine is not None:
                self._close_engine(engine)
            self.settings_error = f"Could not load {label}. {exc}"
            logger.exception("Failed to switch to %s", label)
            self.show_notification("Could Not Load Speech Model", str(exc))
        else:
            previous, self.transcriber = self.transcriber, engine
            self.model_name = selection.model
            if previous is not None:
                self._close_engine(previous)
            self.icon.title = f"Dictate · {label} ({engine.device.upper()})"
            logger.info("Speech model ready: %s (%s)", label, engine.device)
            self.show_notification("Dictate Ready", f"{label} on {engine.device.upper()}.")
        finally:
            with self._capture_lock:
                self._loading_model = False
            self.ui_queue.put(REFRESH_HISTORY_MENU)

    def select_microphone(self, microphone):
        with self._capture_lock:
            if not self.running or self.settings_busy:
                return
            if microphone == self.recorder.microphone and self.recorder.microphone_ready:
                return
            self.settings_error = ""
            self._changing_microphone = True
            self.task_queue.put(MicrophoneSelection(microphone))

    def _change_microphone(self, selection):
        try:
            self.recorder.select_microphone(
                selection.microphone,
                persist=lambda: save_settings({"microphone": selection.microphone}),
            )
        except Exception as exc:
            self.settings_error = f"Could not switch microphones. {exc}"
            logger.exception("Failed to switch the input device")
        finally:
            with self._capture_lock:
                self._changing_microphone = False

    def select_pre_roll(self, duration):
        with self._capture_lock:
            if not self.running or self.settings_busy or duration == self.recorder.ring_buffer_duration:
                return
            self.settings_error = ""
            self._changing_pre_roll = True
            self.task_queue.put(PreRollSelection(duration))

    def _change_pre_roll(self, selection):
        try:
            self.recorder.set_pre_roll(
                selection.duration,
                persist=lambda: save_settings({"ring_buffer_duration": selection.duration}),
            )
        except Exception as exc:
            self.settings_error = f"Could not change microphone pre-roll. {exc}"
            logger.exception("Failed to change microphone pre-roll")
        finally:
            with self._capture_lock:
                self._changing_pre_roll = False

    def set_save_wav(self, enabled):
        try:
            save_settings({"save_wav": enabled})
        except Exception as exc:
            self.settings_error = f"Could not save the history preference. {exc}"
            logger.exception("Failed to save the WAV preference")
        else:
            self.save_wav = enabled
            self.settings_error = ""

    def set_dictation_preferences(self, *, shortcut, mode, escape_cancels, live_preview, use_cursor_context):
        with self._capture_lock:
            if self.settings_busy or self.hotkey_manager.is_active:
                return
            try:
                save_settings({
                    "hotkey": shortcut, "hotkey_mode": mode, "escape_cancels": escape_cancels,
                    "live_preview": live_preview, "use_cursor_context": use_cursor_context,
                })
            except Exception as exc:
                self.settings_error = f"Could not save dictation settings. {exc}"
                logger.exception("Failed to save dictation settings")
            else:
                self.hotkey_manager.configure(shortcut, mode, escape_cancels)
                self.live_preview = live_preview
                self.use_cursor_context = use_cursor_context
                self.settings_error = ""

    def set_replacements(self, replacements):
        save_settings({
            "text_replacements": dict(replacements.rules),
            "fuzzy_replacements": sorted(replacements.fuzzy_sources),
        })
        self.replacements = replacements

    def set_wav_retention(self, days):
        try:
            save_settings({"wav_retention_days": days})
        except (OSError, ValueError):
            logger.exception("Could not save the audio retention setting")
            self.settings_error = "Could not save audio retention."
        else:
            saved = self.history.set_audio_retention(days)
            self.settings_error = "" if saved else "Could not clean up older recordings."

    @staticmethod
    def _close_engine(engine):
        try:
            engine.close()
        except Exception:
            logger.exception("Failed to release the previous speech engine")

    def start_capture(self, on_limit_reached, on_audio_start):
        with self._capture_lock:
            if not self.running or self._capture:
                return False
            if self.settings_activity or self.transcriber is None:
                message = (
                    self.settings_activity or "Select a speech model in Dictate Settings."
                )
                self.show_notification("Dictation Unavailable", message)
                return False

            capture = LiveTranscription(
                self.recorder.sample_rate, rolling=self.model_name == "fluid-speech-mini"
            )

            def show_recording():
                # PortAudio must be able to finish its callback while stop()
                # closes an on-demand stream under the capture lock.
                if not self._capture_lock.acquire(blocking=False):
                    return False
                try:
                    if self._capture is capture:
                        on_audio_start()
                finally:
                    self._capture_lock.release()

            def finish_at_limit():
                if self.finish_capture(expected=capture):
                    on_limit_reached()

            self.recorder.start(on_limit_reached=finish_at_limit, on_audio_start=show_recording)
            self.preview_text = ""
            self._capture = capture
            return True

    def finish_capture(self, *, expected=None, insert=True, keep_in_history=True):
        with self._capture_lock:
            if self._capture is None or (expected is not None and self._capture is not expected):
                return False
            capture, self._capture = self._capture, None
            audio = self.recorder.stop()
            self.preview_text = ""
            if self.recorder.capture_interrupted is True and keep_in_history:
                self.show_notification("Microphone Interrupted", "Part of this dictation may be missing.")
            if audio is None or not keep_in_history:
                self._restore_tray_state()
            else:
                self._pending_captures += 1
                self.set_state("transcribing")
                self.task_queue.put(CompletedCapture(capture, audio, self.save_wav, insert=insert))
            return True

    def _process_live_audio(self):
        with self._capture_lock:
            capture = self._capture
            if capture is None:
                return
            blocks = self.recorder.recorded_blocks_from(capture.blocks_seen)
        self.recorder.check_capture_input()
        capture.feed(blocks, self.transcriber)
        if self.live_preview and blocks:
            preview = capture.preview()
            with self._capture_lock:
                if self._capture is capture:
                    self.preview_text = preview

    def copy_latest(self, icon=None, item=None):
        entries = self.history.recent()
        if entries:
            self._copy_history_action(entries[0])(icon, item)

    def _history_menu_items(self):
        entries = self.history.recent()
        if not entries:
            return (pystray.MenuItem("No saved dictations yet", None, enabled=False),)
        return (
            pystray.MenuItem("Select a phrase to copy", None, enabled=False),
            pystray.Menu.SEPARATOR,
            *(pystray.MenuItem(entry.menu_label, self._copy_history_action(entry)) for entry in entries),
        )

    def _copy_history_action(self, entry):
        # Bind the full entry, not its index: a new dictation must not change
        # which text an already-open menu item copies.
        def copy_selected(icon, item):
            try:
                self.copy_history_entry(entry)
                logger.info("Copied history entry to clipboard: %d characters", len(entry.text))
                self.show_notification(
                    "Dictation Copied",
                    "Switch to the desired field and press Ctrl+V.",
                )
            except Exception:
                logger.exception("Failed to copy dictation history entry")
                self.show_notification(
                    "Could Not Copy Dictation",
                    "Try again. The text is still in history.",
                )
        return copy_selected

    def copy_history_entry(self, entry):
        copy_text(entry.text, getattr(self.icon, "_hwnd", None))

    def start(self, setup_callback=None):
        """
        Starts the worker thread, UI thread, and the pystray main loop.
        """
        logger.info("Initializing background transcription worker thread...")
        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            name="TranscriptionWorker",
            daemon=True
        )
        self.worker_thread.start()
        logger.info("Background transcription worker started.")
        
        logger.info("Initializing background UI update thread...")
        self.ui_thread = threading.Thread(
            target=self._ui_update_loop,
            name="UIUpdateWorker",
            daemon=True
        )
        self.ui_thread.start()
        logger.info("Background UI update thread started.")
        
        logger.info("Running pystray system tray loop (blocks main thread)...")
        # Run system tray icon (blocking call, runs tray event loop)
        if setup_callback:
            def setup_wrapper(icon):
                logger.info("Setting tray icon visibility to True in setup wrapper...")
                icon.visible = True
                setup_callback(icon)
            self.icon.run(setup=setup_wrapper)
        else:
            self.icon.visible = True
            self.icon.run()

    def set_state(self, state):
        """
        Queues the tray icon state change for the UI thread to execute.
        """
        self.state = state
        self.ui_queue.put(state)
        logger.info(f"System tray state changed to: '{state.upper()}'")

    def toggle_pause(self, icon=None, item=None):
        """
        Toggles dictation listening on/off.
        """
        self.is_paused = not self.is_paused
        self.hotkey_manager.set_paused(self.is_paused)
        
        if self.is_paused:
            self.set_state('paused')
            self.show_notification("Dictation Paused", "PTT hotkey has been disabled.")
        else:
            self.set_state('idle')
            self.show_notification("Dictation Resumed", "Press Ctrl+Win to begin dictating.")
            
        # Force a context menu update to reflect label change immediately
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception as e:
                logger.debug(f"Non-critical: Failed to update tray menu manually: {e}")

    def show_notification(self, title, message):
        """
        Sends a balloon/toast notification to the Windows system tray.
        """
        if not self.show_startup_notifications:
            return
        if self.icon and pystray.Icon.HAS_NOTIFICATION:
            try:
                self.icon.notify(message, title)
            except Exception as e:
                logger.error(f"Failed to display tray notification: {e}")

    def exit_app(self, icon=None, item=None):
        """
        Stops listeners, cleanups modules, and terminates the tray loop.
        """
        logger.info("Shutdown requested. Initiating cleanup operations...")
        self.running = False
        
        # Stop keyboard hooks
        self.hotkey_manager.stop_listening()
        
        # Stop recording stream if active
        with self._capture_lock:
            self._capture = None
            self.recorder.stop()
        
        # Stop the tray icon loop
        if self.icon:
            self.icon.stop()
            
        # Signal worker and UI threads termination
        self.task_queue.put(None)
        self.ui_queue.put(None)
        
        # Invoke root main cleanup callback
        if self.on_exit_callback:
            try:
                self.on_exit_callback()
            except Exception as e:
                logger.error(f"Error executing exit callback: {e}")

    def _ui_update_loop(self):
        """
        Background loop running on a single dedicated thread to execute all 
        tray icon state changes. This ensures all HICON resources are created 
        and destroyed on the same thread, preventing Win32 cursor handle errors.
        """
        while self.running:
            try:
                state = self.ui_queue.get(timeout=1.0)
                if state is None:
                    # Termination sentinel received
                    break
                try:
                    if state is REFRESH_HISTORY_MENU:
                        self.icon.update_menu()
                    else:
                        self.icon.icon = self._generate_icon_image(state)
                except Exception as e:
                    logger.debug(f"UI thread failed to update icon handle: {e}")
                self.ui_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in UI update loop: {e}")

    def _worker_loop(self):
        """Serialize speech inference, model changes and the final insertion."""
        try:
            while self.running:
                try:
                    task = self.task_queue.get(timeout=0.15)
                except queue.Empty:
                    self._process_live_audio()
                    continue
                try:
                    if task is None:
                        break
                    if isinstance(task, ModelSelection):
                        self._load_model(task)
                    elif isinstance(task, MicrophoneSelection):
                        self._change_microphone(task)
                    elif isinstance(task, PreRollSelection):
                        self._change_pre_roll(task)
                    else:
                        self._deliver_transcript(task)
                finally:
                    self.task_queue.task_done()
        finally:
            if self.transcriber is not None:
                self._close_engine(self.transcriber)

    def _deliver_transcript(self, task):
        entry = None
        try:
            if isinstance(task, CompletedCapture):
                text = task.live.finish(task.audio, self.transcriber)
            else:
                text = self.transcriber.transcribe(task)
            if text and self.running:
                text = self.replacements.apply(text)
                # Save first, so the user can recover the text if delivery fails.
                model = self.models[self.model_name][0] if self.model_name in self.models else None
                entry = self.history.add(text, model=model)
                self.ui_queue.put(REFRESH_HISTORY_MENU)
                if not isinstance(task, CompletedCapture) or task.insert:
                    paste_text(text, use_cursor_context=self.use_cursor_context)
            else:
                logger.info("Transcription yielded no text to insert.")
        except Exception as exc:
            logger.exception("Could not finish dictation")
            self.show_notification("Transcription Error", str(exc))
        finally:
            with self._capture_lock:
                if isinstance(task, CompletedCapture):
                    self._pending_captures -= 1
                self._restore_tray_state()
            if entry and isinstance(task, CompletedCapture) and task.save_wav:
                self.history.save_audio(entry, task.audio, self.recorder.sample_rate)

    def _restore_tray_state(self):
        if self.is_paused:
            self.set_state("paused")
        elif self._capture is None:
            self.set_state("transcribing" if self._pending_captures else "idle")

    def _generate_icon_image(self, state):
        """
        Procedurally draws high-contrast rounded square microphone icons (32x32).
        """
        # Create RGBA canvas for smooth circle corners
        image = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # State styling definitions (background rounded square badge + foreground microphone)
        if state == 'idle':
            bg_color = (51, 58, 77, 255)        # Dark bluish-gray matching icon.png
            fg_color = (172, 255, 255, 255)    # Light cyan matching icon.png
        elif state == 'recording':
            bg_color = (180, 10, 10, 255)       # Red square
            fg_color = (255, 220, 220, 255)    # Pinkish-white microphone
        elif state == 'transcribing':
            bg_color = (10, 100, 180, 255)     # Sky blue square
            fg_color = (220, 240, 255, 255)    # Light blue microphone
        elif state == 'paused':
            bg_color = (30, 30, 30, 180)        # Translucent dark square
            fg_color = (128, 128, 128, 255)    # Gray microphone
        else:
            bg_color = (51, 58, 77, 255)
            fg_color = (172, 255, 255, 255)
            
        # Draw background badge rounded rectangle (centered, 28x28 pixels)
        draw.rounded_rectangle([2, 2, 29, 29], radius=6, fill=bg_color)
        
        # Draw Microphone Shape:
        # 1. Elongated rounded rectangle capsule (Center x: 15.5, y range: 4-15)
        draw.rounded_rectangle([13, 4, 18, 15], radius=3, fill=fg_color)
        
        # 2. Stand cradle (horizontal circle arc under capsule, sides hugging higher)
        draw.arc([10, 9, 21, 20], start=-20, end=200, fill=fg_color, width=2)
        
        # 3. Support stem (vertical line connecting base and cradle)
        draw.line([16, 20, 16, 25], fill=fg_color, width=2)
        
        # 4. Base stand (horizontal plate)
        draw.line([12, 25, 19, 25], fill=fg_color, width=2)
        
        # Draw status dot in top-right area for active states
        if state == 'recording':
            draw.ellipse([22, 5, 27, 10], fill=(255, 60, 60, 255))
        elif state == 'transcribing':
            draw.ellipse([22, 5, 27, 10], fill=(0, 191, 255, 255))
            
        return image
