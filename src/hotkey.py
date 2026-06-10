import keyboard
from src.logger import logger

class HotkeyManager:
    def __init__(self, on_trigger_start, on_trigger_stop):
        """
        Manages the global hotkey state machine.
        :param on_trigger_start: Callback when Ctrl+Win are both held.
        :param on_trigger_stop: Callback when either key is released.
        """
        self.on_trigger_start = on_trigger_start
        self.on_trigger_stop = on_trigger_stop
        
        self.is_active = False
        self.win_blocked = False
        self.paused = False
        self.hook_handle = None

    def start_listening(self):
        """
        Registers the keyboard hooks.
        """
        logger.info("Registering global keyboard hook listener...")
        try:
            self.hook_handle = keyboard.hook(self._on_key_event)
            logger.info("Global keyboard hook registered successfully.")
        except Exception as e:
            logger.error(f"Failed to register global keyboard hook: {e}")
            logger.error("Make sure to run the application with Administrator privileges.")
            raise e

    def stop_listening(self):
        """
        Unregisters the keyboard hooks and cleans up blocked keys.
        """
        if self.hook_handle:
            logger.info("Unregistering global keyboard hook listener...")
            try:
                keyboard.unhook(self.hook_handle)
            except Exception as e:
                logger.error(f"Error unhooking keyboard listener: {e}")
            self.hook_handle = None
        self._unblock_win_keys()

    def set_paused(self, paused):
        """
        Pauses or resumes the hotkey triggers.
        """
        self.paused = paused
        if paused and self.is_active:
            logger.info("Hotkey manager paused while recording was active. Stopping recording.")
            self.is_active = False
            try:
                self.on_trigger_stop()
            except Exception as e:
                logger.error(f"Error executing stop trigger on pause: {e}")
            self._unblock_win_keys()

    def _on_key_event(self, event):
        """
        Handles every key press and release event globally.
        """
        if self.paused:
            return

        # Query the current real-time state of the modifiers
        ctrl_pressed = keyboard.is_pressed('ctrl')
        win_pressed = keyboard.is_pressed('windows')

        # If both Ctrl and Win are held down, start recording
        if ctrl_pressed and win_pressed:
            if not self.is_active:
                logger.info("Hotkey combination triggered: Ctrl+Win are now held down.")
                self.is_active = True
                self._block_win_keys()
                try:
                    self.on_trigger_start()
                except Exception as e:
                    logger.error(f"Error executing on_trigger_start callback: {e}")
        else:
            # If we were recording and either key is released, stop recording
            if self.is_active:
                logger.info("Hotkey combination released: Ctrl or Win has been released.")
                self.is_active = False
                try:
                    self.on_trigger_stop()
                except Exception as e:
                    logger.error(f"Error executing on_trigger_stop callback: {e}")
                
                # Unblock and release Windows keys immediately to prevent stuck state
                # by the time transcription completes and paste is executed.
                self._unblock_win_keys()

    def _block_win_keys(self):
        """
        Blocks Windows keys to prevent Start Menu pop-ups.
        """
        if not self.win_blocked:
            logger.info("Temporarily blocking Windows keys (left/right) from reaching OS.")
            try:
                # Intercept keys using low-level keyboard hooks
                keyboard.block_key('left windows')
                keyboard.block_key('right windows')
                self.win_blocked = True
            except Exception as e:
                logger.warning(
                    f"Could not block Windows keys: {e}. "
                    "Start Menu suppression may not work. Run as Administrator to resolve."
                )

    def _unblock_win_keys(self):
        """
        Unblocks Windows keys, restoring default functionality and releasing keys in OS.
        """
        if self.win_blocked:
            logger.info("Restoring Windows keys (left/right) functionality.")
            try:
                keyboard.unblock_key('left windows')
                keyboard.unblock_key('right windows')
                self.win_blocked = False
                
                # Send synthetic release events to clear the stuck modifier state in the OS.
                # To prevent the Start Menu from showing, we wrap the releases in a synthetic Ctrl press.
                keyboard.press('ctrl')
                keyboard.release('left windows')
                keyboard.release('right windows')
                keyboard.release('ctrl')
                logger.debug("Sent synthetic Win key release events wrapped in Ctrl.")
            except Exception as e:
                logger.error(f"Failed to unblock Windows keys: {e}")
