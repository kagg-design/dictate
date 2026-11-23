"""Apply user spelling corrections once, at word boundaries."""

import re


class TextReplacements:
    def __init__(self, rules=()):
        self.rules = tuple(sorted(rules, key=lambda rule: rule[0].casefold()))
        ordered = sorted(self.rules, key=lambda rule: -len(rule[0]))
        self._values = tuple(value for _, value in ordered)
        alternatives = "|".join(f"({re.escape(source)})" for source, _ in ordered)
        self._pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE) if ordered else None

    @classmethod
    def from_config(cls, raw):
        if not isinstance(raw, dict):
            return cls()
        rules = {}
        for source, value in raw.items():
            if isinstance(source, str) and isinstance(value, str) and source.strip() and value.strip():
                source = source.strip()
                rules[source.casefold()] = (source, value.strip())
        return cls(rules.values())

    def update(self, source, value, *, previous=None):
        source, value = source.strip(), value.strip()
        if not source or not value:
            raise ValueError("Enter both the recognized text and its replacement.")
        rules = {key: text for key, text in self.rules if key != previous}
        if any(key.casefold() == source.casefold() for key in rules):
            raise ValueError("A replacement for this text already exists.")
        rules[source] = value
        return TextReplacements(rules.items())

    def remove(self, source):
        return TextReplacements(rule for rule in self.rules if rule[0] != source)

    def apply(self, text):
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda match: self._values[match.lastindex - 1], text)
