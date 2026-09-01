import keyboard

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

        # Keep the sides separate so releasing one Ctrl/Win does not erase the
        # state of the other side when both are held.
        self._pressed_ctrl_keys = set()
        self._pressed_win_keys = set()

    def start_listening(self):
        """Register the global keyboard hook."""
        logger.info("Registering global keyboard hook listener...")
        try:
            self._reset_key_state()
            self.hook_handle = keyboard.hook(self._on_key_event)
            logger.info("Global keyboard hook registered successfully.")
        except Exception as e:
            logger.error(f"Failed to register global keyboard hook: {e}")
            logger.error("Make sure to run the application with Administrator privileges.")
            raise

    def stop_listening(self):
        """Unregister the hook and restore the Windows keys."""
        if self.hook_handle:
            logger.info("Unregistering global keyboard hook listener...")
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

    def _on_key_event(self, event):
        """Handle physical modifier transitions from the keyboard hook."""
        if self.paused:
            return

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
        """Block Windows-key events while the push-to-talk chord is active."""
        if self.win_blocked:
            return

        logger.info("Temporarily blocking Windows keys (left/right) from reaching OS.")
        try:
            keyboard.block_key("left windows")
            keyboard.block_key("right windows")
            self.win_blocked = True
        except Exception as e:
            logger.warning(
                f"Could not block Windows keys: {e}. "
                "Start Menu suppression may not work. Run as Administrator to resolve."
            )

    def _unblock_win_keys(self):
        """Restore Windows keys and release their suppressed OS state."""
        if not self.win_blocked:
            return

        logger.info("Restoring Windows keys (left/right) functionality.")
        try:
            keyboard.unblock_key("left windows")
            keyboard.unblock_key("right windows")
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
