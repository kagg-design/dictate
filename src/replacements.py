"""Apply exact replacements and optional dictionary spellings."""

import re
import unicodedata


TERM_WORD = re.compile(r"(?<![\w./@\\-])[^\W\d_]+(?![\w/@\\-]|\.\w)")


def _one_typo(word, term):
    if len(term) < 5 or abs(len(word) - len(term)) > 1:
        return False
    if len(word) == len(term):
        differences = [i for i, (left, right) in enumerate(zip(word, term)) if left != right]
        if len(differences) != 2:
            return len(differences) == 1
        left, right = differences
        return right == left + 1 and word[left] == term[right] and word[right] == term[left]
    shorter, longer = sorted((word, term), key=len)
    index = next((i for i, (left, right) in enumerate(zip(shorter, longer)) if left != right), len(shorter))
    return shorter[index:] == longer[index + 1:]


def _normalized(text):
    return unicodedata.normalize("NFC", text.lower())


class TextReplacements:
    def __init__(self, rules=(), fuzzy_sources=()):
        self.rules = tuple(sorted(rules, key=lambda rule: rule[0].casefold()))
        enabled = {source.casefold() for source in fuzzy_sources}
        self.fuzzy_sources = frozenset(source for source, _ in self.rules if source.casefold() in enabled)
        ordered = sorted(self.rules, key=lambda rule: -len(rule[0]))
        self._values = tuple(value for _, value in ordered)
        alternatives = "|".join(f"({re.escape(source)})" for source, _ in ordered)
        self._pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE) if ordered else None
        terms = {}
        for source, value in self.rules:
            if source in self.fuzzy_sources:
                words = tuple(_normalized(word) for word in value.split())
                if words and all(word.isalpha() for word in words):
                    terms.setdefault(len(words), set()).add((words, value))
        self._terms = sorted(terms.items(), reverse=True)

    @classmethod
    def from_config(cls, raw, fuzzy_sources=()):
        if not isinstance(raw, dict):
            return cls()
        rules = {}
        for source, value in raw.items():
            if isinstance(source, str) and isinstance(value, str) and source.strip() and value.strip():
                source = source.strip()
                rules[source.casefold()] = (source, value.strip())
        if not isinstance(fuzzy_sources, (tuple, list, set, frozenset)):
            fuzzy_sources = ()
        return cls(rules.values(), (key for key in fuzzy_sources if isinstance(key, str)))

    def update(self, source, value, *, previous=None, fuzzy=None):
        source, value = source.strip(), value.strip()
        if not source or not value:
            raise ValueError("Enter both the recognized text and its replacement.")
        if fuzzy is True and not all(_normalized(word).isalpha() for word in value.split()):
            raise ValueError("Similar-spelling corrections support words made of letters.")
        rules = {key: text for key, text in self.rules if key != previous}
        if any(key.casefold() == source.casefold() for key in rules):
            raise ValueError("A replacement for this text already exists.")
        rules[source] = value
        fuzzy_sources = self.fuzzy_sources - {previous}
        if fuzzy is True or (fuzzy is None and previous in self.fuzzy_sources):
            fuzzy_sources = fuzzy_sources | {source}
        return TextReplacements(rules.items(), fuzzy_sources)

    def remove(self, source):
        return TextReplacements((rule for rule in self.rules if rule[0] != source), self.fuzzy_sources - {source})

    def apply(self, text):
        if self._pattern is None:
            return text
        if not self._terms:
            return self._pattern.sub(lambda match: self._values[match.lastindex - 1], text)
        result = []
        end = 0
        for match in self._pattern.finditer(text):
            result.append(self._correct_terms(text, end, match.start()))
            result.append(self._values[match.lastindex - 1])
            end = match.end()
        result.append(self._correct_terms(text, end, len(text)))
        return "".join(result)

    def _correct_terms(self, text, start, end):
        matches = []
        for match in TERM_WORD.finditer(text, start):
            if match.end() > end:
                break
            matches.append(match)
        words = [_normalized(match[0]) for match in matches]
        corrected = []
        index = 0
        while index < len(matches):
            for length, terms in self._terms:
                limit = index + length
                if limit > len(matches) or any(
                    not re.fullmatch(r"[ \t]+", text[matches[i].end():matches[i + 1].start()])
                    for i in range(index, limit - 1)
                ):
                    continue
                candidate = words[index:limit]
                exact = set()
                close = set()
                for term, value in terms:
                    differences = [(word, target) for word, target in zip(candidate, term) if word != target]
                    if not differences:
                        exact.add(value)
                    elif len(differences) == 1 and _one_typo(*differences[0]):
                        close.add(value)
                spellings = exact or close
                if spellings:
                    if len(spellings) == 1:
                        first, last = matches[index].start(), matches[limit - 1].end()
                        corrected.extend((text[start:first], spellings.pop()))
                        start = last
                    index = limit - 1
                    break
            index += 1
        corrected.append(text[start:end])
        return "".join(corrected)
