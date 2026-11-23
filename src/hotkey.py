"""Global shortcuts for dictating, canceling and copying the last transcript."""

from queue import SimpleQueue
from threading import Lock, Thread
from time import monotonic

import keyboard

from src.logger import logger


SHORTCUTS = {
    "ctrl+win": "Ctrl+Win",
    "ctrl+alt+space": "Ctrl+Alt+Space",
    "ctrl+shift+space": "Ctrl+Shift+Space",
}
MODES = {"hold": "Hold to dictate", "toggle": "Press to start / stop"}
DEFAULT_SHORTCUT = "ctrl+win"
COPY_MODIFIERS = frozenset({"ctrl", "win"})
MODIFIERS = frozenset({"ctrl", "win", "alt", "shift", "alt gr"})


class HotkeyManager:
    def __init__(self, on_trigger_start, on_trigger_stop, *, on_cancel=None, on_copy=None,
                 shortcut=DEFAULT_SHORTCUT, mode="hold", escape_cancels=True):
        self.on_trigger_start = on_trigger_start
        self.on_trigger_stop = on_trigger_stop
        self.on_cancel = on_cancel
        self.on_copy = on_copy
        self.shortcut = shortcut if isinstance(shortcut, str) and shortcut in SHORTCUTS else DEFAULT_SHORTCUT
        self.mode = mode if isinstance(mode, str) and mode in MODES else "hold"
        self.escape_cancels = escape_cancels
        self.is_active = False
        self.paused = False
        self._pressed = {}
        self._suppressed = set()
        self._chord_down = False
        self._toggle_armed = False
        self._hold_started = 0.0
        self._win_needs_release = False
        self._state_lock = Lock()
        self._hook = None
        self._actions = None

    def configure(self, shortcut, mode, escape_cancels):
        with self._state_lock:
            self.shortcut = shortcut
            self.mode = mode
            self.escape_cancels = escape_cancels
            # Disarm a combination already held while applying the settings.
            self._chord_down = self._matches()
            self._toggle_armed = False

    def start_listening(self):
        actions = SimpleQueue()
        self._actions = actions
        try:
            self._hook = keyboard.hook(self._handle_event, suppress=True)
        except Exception:
            self._actions = None
            raise
        Thread(target=self._run_actions, args=(actions,), name="DictateHotkeys", daemon=True).start()
        logger.info("Global dictation shortcuts registered.")

    def stop_listening(self):
        if self._hook is not None:
            keyboard.unhook(self._hook)
            self._hook = None
        if self._actions is not None:
            self._actions.put(None)
            self._actions = None
        with self._state_lock:
            restore_win = self._win_needs_release
            self._win_needs_release = False
            self._pressed.clear()
            self._suppressed.clear()
            self.is_active = self._chord_down = self._toggle_armed = False
        if restore_win:
            self._restore_windows_key_state()

    def set_paused(self, paused):
        with self._state_lock:
            self.paused = paused
            stop = paused and self.is_active
            if stop:
                self.is_active = False
            self._toggle_armed = False
        if stop:
            self._schedule(self.on_trigger_stop)

    def cancel_active(self):
        """Disarm recording after the recorder's hard time limit."""
        with self._state_lock:
            self.is_active = False
            self._toggle_armed = False

    def _matches(self):
        keys = set(self._pressed.values())
        required = set(self.shortcut.split("+"))
        return keys & MODIFIERS == required & MODIFIERS and required <= keys

    def _handle_event(self, event):
        # The Windows hook only tracks keys and queues actions. Recording and
        # clipboard work can block, so they run on the ordered worker instead.
        name = (event.name or "").removeprefix("left ").removeprefix("right ")
        if name == "windows":
            name = "win"
        elif name == "escape":
            name = "esc"
        elif event.scan_code == 0x2F:  # physical V, including non-Latin keyboard layouts
            name = "v"
        if name not in MODIFIERS and name not in {"v", "esc", "space"}:
            return True
        key = (event.scan_code, event.name if name in MODIFIERS else name)
        down = event.event_type == "down"
        actions = []
        with self._state_lock:
            repeat = key in self._pressed
            if down:
                self._pressed[key] = name
            else:
                self._pressed.pop(key, None)
            modifiers = MODIFIERS.intersection(self._pressed.values())
            previous, self._chord_down = self._chord_down, self._matches()
            consume = key in self._suppressed

            if down and not repeat and name == "v" and modifiers == COPY_MODIFIERS and self.on_copy:
                consume = True
                self._toggle_armed = False
                if (self.is_active and self.shortcut == DEFAULT_SHORTCUT and self.mode == "hold"
                        and monotonic() - self._hold_started < 0.35):
                    self.is_active = False
                    actions.append((self.on_cancel, (False,)))
                actions.append((self.on_copy, ()))
            elif down and not repeat and name == "esc" and self.escape_cancels and self.is_active and self.on_cancel:
                consume = True
                self.is_active = self._toggle_armed = False
                actions.append((self.on_cancel, (True,)))
            elif not self.paused:
                if self._chord_down and not previous and down:
                    consume |= name == "space"
                    if self.mode == "toggle" and self.shortcut == DEFAULT_SHORTCUT:
                        # Decide on release so a following V can select Copy.
                        self._toggle_armed = True
                    else:
                        self.is_active = not self.is_active
                        if self.mode == "hold":
                            self._hold_started = monotonic()
                        actions.append((self.on_trigger_start if self.is_active else self.on_trigger_stop, ()))
                elif previous and not self._chord_down:
                    if self._toggle_armed and not down:
                        self.is_active = not self.is_active
                        actions.append((self.on_trigger_start if self.is_active else self.on_trigger_stop, ()))
                    elif self.mode == "hold" and self.is_active:
                        self.is_active = False
                        actions.append((self.on_trigger_stop, ()))
                    self._toggle_armed = False

            if name == "win":
                # If Win went down first, Windows saw it. Release it with a
                # Ctrl wrapper after the chord so Start doesn't open.
                if self.shortcut == DEFAULT_SHORTCUT and not self.paused and (previous or self._chord_down):
                    if not consume and (not down or repeat):
                        self._win_needs_release = True
                    consume = True
                elif down and not consume:
                    self._win_needs_release = False
            if self.shortcut == DEFAULT_SHORTCUT and self._chord_down and not previous and name == "ctrl":
                self._win_needs_release |= any(
                    pressed_name == "win" and pressed_key not in self._suppressed
                    for pressed_key, pressed_name in self._pressed.items()
                )
            if self._win_needs_release and previous and not self._chord_down:
                self._win_needs_release = False
                actions.append((self._restore_windows_key_state, ()))
            if consume and down:
                self._suppressed.add(key)
            elif not down:
                self._suppressed.discard(key)

        for callback, args in actions:
            self._schedule(callback, *args)
        return not consume

    def _schedule(self, callback, *args):
        if callback is None:
            return
        actions = self._actions
        if actions is None:
            self._invoke(callback, args)
        else:
            actions.put((callback, args))

    def _run_actions(self, actions):
        while (action := actions.get()) is not None:
            self._invoke(*action)

    def _invoke(self, callback, args):
        try:
            result = callback(*args)
            if callback is self.on_trigger_start and result is False:
                self.cancel_active()
        except Exception:
            logger.exception("Dictate shortcut failed")

    def _restore_windows_key_state(self):
        # keyboard.send suppresses replayed events from the hook. Leave a
        # physically held Ctrl alone so other shortcuts continue to work.
        with self._state_lock:
            ctrl_held = "ctrl" in self._pressed.values()
        if ctrl_held:
            keyboard.send("left windows+right windows", do_press=False)
        else:
            keyboard.send("ctrl", do_release=False)
            keyboard.send("ctrl+left windows+right windows", do_press=False)
