"""Global shortcuts for dictating, canceling and reusing the last transcript."""

from queue import SimpleQueue
from threading import Lock, Thread
from time import monotonic

import keyboard

from src.inserter import mask_start_menu
from src.logger import logger
from src.shortcuts import (
    COPY_LAST, DEFAULT_INSERT_LAST_SHORTCUT, DEFAULT_SHORTCUT, DICTATION_DEFAULT, INSERT_LAST_DEFAULT,
    MODIFIERS, Shortcut, load_shortcut, validate_shortcuts,
)


MODES = {"hold": "Hold to dictate", "toggle": "Press to start / stop"}
KEEP_INSERT_SHORTCUT = object()


def validate_postprocess_shortcut(binding, dictation, insert_last):
    if binding and (binding.key is None or any(binding.conflicts(other) for other in (dictation, insert_last, COPY_LAST))):
        raise ValueError("Choose a separate post-processing shortcut with a non-modifier key.")


class HotkeyManager:
    def __init__(self, on_trigger_start, on_trigger_stop, *, on_cancel=None, on_copy=None, on_insert=None,
                 shortcut=DEFAULT_SHORTCUT, mode="hold", escape_cancels=True,
                 insert_last_shortcut=DEFAULT_INSERT_LAST_SHORTCUT, on_postprocess_start=None, postprocess_shortcut=None):
        self.on_trigger_start = on_trigger_start
        self.on_trigger_stop = on_trigger_stop
        self.on_cancel = on_cancel
        self.on_copy = on_copy
        self.on_insert = on_insert
        self.on_postprocess_start = on_postprocess_start
        self.shortcut = load_shortcut(shortcut, DICTATION_DEFAULT)
        self.mode = mode if isinstance(mode, str) and mode in MODES else "hold"
        self.escape_cancels = escape_cancels
        self.insert_last_shortcut = load_shortcut(insert_last_shortcut, INSERT_LAST_DEFAULT)
        if insert_last_shortcut == "off":
            self.insert_last_shortcut = None
        try:
            validate_shortcuts(self.shortcut, self.insert_last_shortcut)
        except ValueError:
            # Keep the application usable even if settings were edited by hand.
            if self.shortcut == COPY_LAST:
                self.shortcut = DICTATION_DEFAULT
            self.insert_last_shortcut = None
        self.postprocess_shortcut = load_shortcut(postprocess_shortcut, None)
        try:
            validate_postprocess_shortcut(self.postprocess_shortcut, self.shortcut, self.insert_last_shortcut)
        except ValueError:
            self.postprocess_shortcut = None
        self._postprocessing_active = False
        self.is_active = False
        self.paused = False
        self._pressed = {}
        self._suppressed = set()
        self._chord_down = False
        self._toggle_armed = False
        self._insert_armed = False
        self._hold_started = 0.0
        self._capture = None
        self._capture_size = 0
        self._capture_result = None
        self._state_lock = Lock()
        self._hook = None
        self._actions = None

    def configure(self, shortcut, mode, escape_cancels, *, insert_last_shortcut=KEEP_INSERT_SHORTCUT):
        with self._state_lock:
            self.shortcut = shortcut if isinstance(shortcut, Shortcut) else Shortcut.from_setting(shortcut)
            self.mode = mode
            self.escape_cancels = escape_cancels
            if insert_last_shortcut is not KEEP_INSERT_SHORTCUT:
                self.insert_last_shortcut = (
                    insert_last_shortcut if isinstance(insert_last_shortcut, Shortcut) or insert_last_shortcut is None
                    else load_shortcut(insert_last_shortcut, None)
                )
            # Disarm a combination already held while applying the settings.
            self._chord_down = self._matches(self.shortcut, MODIFIERS.intersection(self._pressed.values()))
            self._toggle_armed = self._insert_armed = False

    def begin_capture(self, results):
        with self._state_lock:
            if self.is_active or self._capture is not None:
                return False
            self._capture = results
            self._capture_size = 0
            self._capture_result = None
            self._toggle_armed = self._insert_armed = False
            return True

    def configure_postprocessing(self, binding):
        with self._state_lock:
            self.postprocess_shortcut = binding

    def cancel_capture(self):
        with self._state_lock:
            self._capture = None

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
            self._pressed.clear()
            self._suppressed.clear()
            self.is_active = self._chord_down = self._toggle_armed = self._insert_armed = False
            self._postprocessing_active = False
            self._capture = None

    def set_paused(self, paused):
        with self._state_lock:
            self.paused = paused
            stop = paused and self.is_active
            if stop:
                self.is_active = False
                self._postprocessing_active = False
            self._toggle_armed = False
        if stop:
            self._schedule(self.on_trigger_stop)

    def cancel_active(self):
        """Disarm recording after the recorder's hard time limit."""
        with self._state_lock:
            self.is_active = False
            self._toggle_armed = False
            self._postprocessing_active = False

    def _matches(self, shortcut, modifiers):
        return shortcut.modifiers == modifiers and (shortcut.key is None or shortcut.key in self._pressed)

    def _capture_event(self, down, repeat, name, modifiers):
        if down and not repeat and name == "esc" and not modifiers:
            self._capture.put((None, ""))
            self._capture = None
        elif down and not repeat and len(self._pressed) >= self._capture_size:
            self._capture_size = len(self._pressed)
            ordinary = [(key, label) for key, label in self._pressed.items() if label not in MODIFIERS]
            try:
                if len(ordinary) > 1 or self._capture_size != len(modifiers) + len(ordinary):
                    raise ValueError("Use at most one ordinary key with the modifiers.")
                key, label = ordinary[0] if ordinary else (None, "")
                self._capture_result = (Shortcut(modifiers, key, label.title()), "")
            except ValueError as exc:
                self._capture_result = (None, str(exc))
        elif not down and not self._pressed and self._capture_result is not None:
            self._capture.put(self._capture_result)
            self._capture = None

    def _insert_triggered(self, down, repeat, key, name, modifiers):
        shortcut = self.insert_last_shortcut
        if shortcut is None or self.on_insert is None:
            return False
        if shortcut.key is not None:
            return down and not repeat and key == shortcut.key and modifiers == shortcut.modifiers
        if down and not repeat:
            self._insert_armed = (
                name in MODIFIERS and modifiers == shortcut.modifiers
                and all(value in MODIFIERS for value in self._pressed.values())
            )
        elif not down and self._insert_armed and modifiers != shortcut.modifiers:
            self._insert_armed = False
            return True
        return False

    def _handle_event(self, event):
        # The Windows hook only tracks keys and queues actions. Recording and
        # clipboard work can block, so they run on the ordered worker instead.
        if event.scan_code == -0xFF:
            return True  # neutral Win-menu mask sent by Dictate
        name = (event.name or f"Key {event.scan_code}").lower()
        side_independent = name.removeprefix("left ").removeprefix("right ")
        if side_independent in MODIFIERS or side_independent == "windows":
            name = side_independent
        if name == "windows":
            name = "win"
        elif name == "escape":
            name = "esc"
        # Win has distinct scan codes for left and right. Its reported name can
        # differ across layouts, so a key-up must match by the physical code.
        key = (
            event.scan_code,
            (name if name == "win" else event.name) if name in MODIFIERS else bool(getattr(event, "is_keypad", False)),
        )
        down = event.event_type == "down"
        actions = []
        with self._state_lock:
            repeat = key in self._pressed
            if down:
                self._pressed[key] = name
            else:
                self._pressed.pop(key, None)
            modifiers = MODIFIERS.intersection(self._pressed.values())
            consume = key in self._suppressed
            if self._capture is not None:
                self._capture_event(down, repeat, name, modifiers)
                self._chord_down = self._matches(self.shortcut, modifiers)
                if down and (name == "win" or name not in MODIFIERS):
                    self._suppressed.add(key)
                    return False
                if not down:
                    self._suppressed.discard(key)
                return not consume
            previous, self._chord_down = self._chord_down, self._matches(self.shortcut, modifiers)
            copying = (
                down and not repeat and key == COPY_LAST.key and modifiers == COPY_LAST.modifiers
                and self.on_copy is not None
            )
            inserting = self._insert_triggered(down, repeat, key, name, modifiers)
            post = self.postprocess_shortcut
            processing = (
                post is not None and self.on_postprocess_start is not None and not self.paused
                and down and not repeat and key == post.key and modifiers == post.modifiers
                and (not self.is_active or self._postprocessing_active)
            )
            activating = (
                self._chord_down and not previous and down and not repeat and not self.paused
                and not self._postprocessing_active
                and (self.shortcut.key is None or key == self.shortcut.key)
            )

            if processing:
                consume = True
                self.is_active = not self.is_active
                self._postprocessing_active = self.is_active
                self._toggle_armed = False
                actions.append((self.on_postprocess_start if self.is_active else self.on_trigger_stop, ()))
            elif copying:
                consume = True
                self._toggle_armed = False
                if (self.is_active and not self._postprocessing_active and self.shortcut == DICTATION_DEFAULT and self.mode == "hold"
                        and monotonic() - self._hold_started < 0.35):
                    self.is_active = False
                    actions.append((self.on_cancel, (False,)))
                actions.append((self.on_copy, ()))
            elif inserting:
                consume |= down and name not in MODIFIERS
                actions.append((self.on_insert, ()))
            elif (down and not repeat and name == "esc" and not activating
                  and self.escape_cancels and self.is_active and self.on_cancel):
                consume = True
                self.is_active = self._toggle_armed = False
                self._postprocessing_active = False
                actions.append((self.on_cancel, (True,)))
            elif not self.paused and not self._postprocessing_active:
                if activating:
                    consume |= self.shortcut.key is not None
                    if self.mode == "toggle" and self.shortcut.key is None:
                        # Decide on release so a key after the modifiers can select another action.
                        self._toggle_armed = True
                    else:
                        self.is_active = not self.is_active
                        if self.mode == "hold":
                            self._hold_started = monotonic()
                        actions.append((self.on_trigger_start if self.is_active else self.on_trigger_stop, ()))
                elif self._toggle_armed and down and name not in MODIFIERS:
                    self._toggle_armed = False
                elif previous and not self._chord_down:
                    if self._toggle_armed and not down:
                        self.is_active = not self.is_active
                        actions.append((self.on_trigger_start if self.is_active else self.on_trigger_stop, ()))
                    elif self.mode == "hold" and self.is_active:
                        self.is_active = False
                        actions.append((self.on_trigger_stop, ()))
                    self._toggle_armed = False

            # Block Win only if we can block its first down event. If Windows
            # saw that press, it must also receive its physical release.
            if name == "win" and activating and self.shortcut.key is None:
                consume = True
            if consume and down:
                self._suppressed.add(key)
            elif not down:
                self._suppressed.discard(key)
            mask_menu = (copying or inserting or activating or processing or self._insert_armed) and any(
                pressed_name == "win" and pressed_key not in self._suppressed
                for pressed_key, pressed_name in self._pressed.items()
            )

        for callback, args in actions:
            self._schedule(callback, *args)
        if mask_menu:
            # Send the neutral key before returning from the chord's key-down.
            # keyboard.send would put the entire hook into replay mode and
            # could drop a real Win or Ctrl key-up during that interval.
            mask_start_menu()
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
            if callback in (self.on_trigger_start, self.on_postprocess_start) and result is False:
                self.cancel_active()
        except Exception:
            logger.exception("Dictate shortcut failed")
