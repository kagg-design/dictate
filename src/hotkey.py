import keyboard
import threading

from src.logger import logger


class HotkeyManager:
    """Track the physical Ctrl+Win push-to-talk chord."""

    CTRL_NAMES = frozenset({"ctrl", "left ctrl", "right ctrl"})
    WIN_NAMES = frozenset({"windows", "left windows", "right windows"})

    def __init__(self, on_trigger_start, on_trigger_stop):
        self.on_trigger_start = on_trigger_start
        self.on_trigger_stop = on_trigger_stop

        self.is_active = False
        self.win_blocked = False
        self.paused = False
        self.hook_handle = None
        self._hook_handles = []
        self._state_lock = threading.RLock()

        # Keep the sides separate so releasing one Ctrl/Win does not erase the
        # state of the other side when both are held.
        self._pressed_ctrl_keys = set()
        self._pressed_win_keys = set()

    def start_listening(self):
        """Register Ctrl observation and selective Windows-key suppression."""
        logger.info("Registering global modifier key hooks...")
        try:
            self._reset_key_state()
            self._hook_handles = [
                keyboard.hook_key("ctrl", self._on_ctrl_key_event),
                keyboard.hook_key(
                    "left windows", self._on_win_key_event, suppress=True
                ),
                keyboard.hook_key(
                    "right windows", self._on_win_key_event, suppress=True
                ),
            ]
            self.hook_handle = self._hook_handles[0]
            logger.info("Global modifier key hooks registered successfully.")
        except Exception as e:
            for handle in self._hook_handles:
                try:
                    keyboard.unhook(handle)
                except Exception:
                    pass
            self._hook_handles = []
            self.hook_handle = None
            logger.error(f"Failed to register global modifier key hooks: {e}")
            logger.error("Make sure to run the application with Administrator privileges.")
            raise

    def stop_listening(self):
        """Unregister the hook and restore the Windows keys."""
        if self._hook_handles:
            logger.info("Unregistering global modifier key hooks...")
            for handle in self._hook_handles:
                try:
                    keyboard.unhook(handle)
                except Exception as e:
                    logger.error(f"Error unhooking keyboard listener: {e}")
            self._hook_handles = []
            self.hook_handle = None
        elif self.hook_handle:
            try:
                keyboard.unhook(self.hook_handle)
            except Exception as e:
                logger.error(f"Error unhooking keyboard listener: {e}")
            self.hook_handle = None

        self.is_active = False
        self._reset_key_state()
        self._unblock_win_keys()

    def set_paused(self, paused):
        """Pause or resume push-to-talk handling."""
        self.paused = paused

        if paused and self.is_active:
            logger.info("Hotkey manager paused while recording was active. Stopping recording.")
            self.is_active = False
            try:
                self.on_trigger_stop()
            except Exception as e:
                logger.error(f"Error executing stop trigger on pause: {e}")

        # Key-up events are intentionally ignored while paused. Reset on both
        # transitions so those ignored events cannot leave a stale chord behind.
        self._reset_key_state()
        self._unblock_win_keys()

    def cancel_active(self):
        """Reset a forced-stop session and restore all blocked key state."""
        self.is_active = False
        self._reset_key_state()
        self._unblock_win_keys()

    def _on_ctrl_key_event(self, event):
        """Handle Ctrl asynchronously through the library's regular hook path."""
        if self.paused:
            return

        self._handle_modifier_event(event)

    def _on_win_key_event(self, event):
        """Observe every Win transition before selectively suppressing it."""
        if self.paused:
            return True

        with self._state_lock:
            was_active = self.is_active
            self._handle_modifier_event(event)

            # Suppress Win while entering, holding, or leaving the PTT chord.
            # The callback still observes key-up, unlike keyboard.block_key().
            return not (was_active or self.is_active)

    def _handle_modifier_event(self, event):
        """Update physical modifier state and transition the PTT session."""
        with self._state_lock:
            name = event.name
            if name in self.CTRL_NAMES:
                self._update_pressed_keys(self._pressed_ctrl_keys, event)
            elif name in self.WIN_NAMES:
                self._update_pressed_keys(self._pressed_win_keys, event)
            else:
                return

            chord_pressed = bool(self._pressed_ctrl_keys and self._pressed_win_keys)
            if chord_pressed and not self.is_active:
                logger.info("Hotkey combination triggered: Ctrl+Win are now held down.")
                self.is_active = True
                self._block_win_keys()
                try:
                    self.on_trigger_start()
                except Exception as e:
                    logger.error(f"Error executing on_trigger_start callback: {e}")
            elif not chord_pressed and self.is_active:
                logger.info("Hotkey combination released: Ctrl or Win has been released.")
                self.is_active = False
                try:
                    self.on_trigger_stop()
                except Exception as e:
                    logger.error(f"Error executing on_trigger_stop callback: {e}")
                finally:
                    self._unblock_win_keys()

    @staticmethod
    def _update_pressed_keys(pressed_keys, event):
        # Name plus scan code distinguishes left/right modifiers where the
        # library exposes both, while still working with generic names.
        key_id = (event.name, getattr(event, "scan_code", None))
        if event.event_type == "down":
            pressed_keys.add(key_id)
        elif event.event_type == "up":
            pressed_keys.discard(key_id)

    def _reset_key_state(self):
        self._pressed_ctrl_keys.clear()
        self._pressed_win_keys.clear()

    def _block_win_keys(self):
        """Mark Win events for suppression by the permanent blocking hook."""
        if self.win_blocked:
            return

        logger.info("Suppressing Windows-key events for the active PTT chord.")
        self.win_blocked = True

    def _unblock_win_keys(self):
        """Restore Windows keys and release their suppressed OS state."""
        if not self.win_blocked:
            return

        logger.info("Ending Windows-key event suppression.")
        try:
            self.win_blocked = False

            # keyboard marks these generated events as replay events and does
            # not deliver them to our hook. No manual ignore list is needed.
            keyboard.press("ctrl")
            keyboard.release("left windows")
            keyboard.release("right windows")
            keyboard.release("ctrl")
            logger.debug("Sent Win key release events wrapped in Ctrl.")
        except Exception as e:
            self.win_blocked = False
            logger.error(f"Failed to unblock Windows keys: {e}")
