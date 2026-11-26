"""Persisted physical key bindings for the Windows dictation hook."""

from dataclasses import dataclass, field


MODIFIER_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
MODIFIERS = frozenset(MODIFIER_LABELS) | {"alt gr"}
DEFAULT_SHORTCUT = "ctrl+win"
DEFAULT_INSERT_LAST_SHORTCUT = "ctrl+alt+f9"

# Settings stored by earlier versions used names for these keys.
NAMED_KEYS = {"space": (57, False), "v": (47, False), "f9": (67, False), "f10": (68, False)}

# Show the Latin QWERTY label for letter keys regardless of the active layout.
LETTER_LABELS = {
    scan_code: letter
    for start, row in ((0x10, "QWERTYUIOP"), (0x1E, "ASDFGHJKL"), (0x2C, "ZXCVBNM"))
    for scan_code, letter in enumerate(row, start=start)
}


@dataclass(frozen=True)
class Shortcut:
    modifiers: frozenset[str]
    key: tuple[int, bool] | None = None
    key_name: str = field(default="", compare=False)

    def __post_init__(self):
        size = len(self.modifiers) + (self.key is not None)
        if not 2 <= size <= 3 or not self.modifiers <= MODIFIER_LABELS.keys():
            raise ValueError("Press two or three keys together, including a modifier.")

    @property
    def key_label(self):
        if self.key is not None and not self.key[1]:
            return LETTER_LABELS.get(self.key[0], self.key_name)
        return self.key_name

    @property
    def label(self):
        labels = [label for name, label in MODIFIER_LABELS.items() if name in self.modifiers]
        if self.key is not None:
            labels.append(self.key_label)
        return "+".join(labels)

    def conflicts(self, other):
        if other is None:
            return False
        if self.key is None:
            return self.modifiers <= other.modifiers
        if other.key is None:
            return other.modifiers <= self.modifiers
        return self == other

    def to_setting(self):
        value = {"modifiers": sorted(self.modifiers)}
        if self.key is not None:
            value.update(scan_code=self.key[0], keypad=self.key[1], label=self.key_label)
        return value

    @classmethod
    def from_setting(cls, value):
        if isinstance(value, str):
            parts = value.split("+")
            key = parts.pop() if parts[-1] in NAMED_KEYS else None
            return cls(frozenset(parts), NAMED_KEYS.get(key), key.title() if key else "")
        if isinstance(value, dict) and isinstance(value.get("modifiers"), list):
            modifiers = frozenset(value["modifiers"])
            scan_code = value.get("scan_code")
            if scan_code is None:
                return cls(modifiers)
            label = value.get("label")
            if type(scan_code) is int and scan_code and isinstance(label, str) and label:
                return cls(modifiers, (scan_code, value.get("keypad") is True), label)
        raise ValueError("Unrecognized shortcut.")


DICTATION_DEFAULT = Shortcut.from_setting(DEFAULT_SHORTCUT)
INSERT_LAST_DEFAULT = Shortcut.from_setting(DEFAULT_INSERT_LAST_SHORTCUT)
COPY_LAST = Shortcut.from_setting("ctrl+win+v")


def load_shortcut(value, fallback):
    try:
        return Shortcut.from_setting(value)
    except (TypeError, ValueError):
        return fallback


def validate_shortcuts(dictation, insert_last):
    if dictation == COPY_LAST or (insert_last and insert_last.conflicts(COPY_LAST)):
        raise ValueError("Ctrl+Win+V is assigned to Copy Last Dictation.")
    if dictation.conflicts(insert_last):
        raise ValueError("Choose distinct shortcuts for dictation and Insert Last Dictation.")
