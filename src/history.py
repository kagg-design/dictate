"""Keep complete transcripts recoverable independently of text insertion."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from uuid import uuid4
import wave

from src.logger import logger
from src.paths import LOG_DIR


TRANSCRIPT_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) .*?"
    r" - Resulting transcript text: (.*)$"
)
WAV_RETENTION_OPTIONS = {0: "Keep with transcript", 7: "7 days", 30: "30 days", 90: "90 days"}


@dataclass(frozen=True)
class HistoryEntry:
    created_at: datetime
    text: str
    model: str | None = None
    audio_file: str | None = None
    pinned: bool = False
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def menu_label(self):
        preview = " ".join(self.text.split())
        if len(preview) > 65:
            preview = preview[:65] + "…"
        # Ampersands are menu mnemonics in Win32; display literal text instead.
        preview = preview.replace("&", "&&")
        return f"{self.created_at:%d.%m %H:%M} — {preview}"


class DictationHistory:
    def __init__(self, path=None, max_entries=100, *, audio_retention_days=0):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.path = Path(path or LOG_DIR / "dictation-history.json")
        self.audio_dir = self.path.parent / "dictation-audio"
        self._max_entries = max_entries
        self._entries = []
        self.audio_retention_days = audio_retention_days if (
            type(audio_retention_days) is int and audio_retention_days in WAV_RETENTION_OPTIONS
        ) else 0
        self._persisted_audio = set()
        self._lock = threading.Lock()
        self._persist_enabled = True
        self._load()
        if self.audio_retention_days:
            self.set_audio_retention(self.audio_retention_days)

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data["version"] != 1:
                    raise ValueError("Unsupported dictation history version")
                entries = []
                for item in data["entries"]:
                    if not isinstance(item["text"], str) or not item["text"].strip():
                        raise ValueError("Invalid transcript in dictation history")
                    audio_file = item.get("audio_file")
                    if not isinstance(audio_file, str) or not re.fullmatch(r"[0-9a-f]{32}\.wav", audio_file):
                        audio_file = None
                    ident = item.get("id")
                    if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident):
                        ident = uuid4().hex
                    entries.append(HistoryEntry(
                        datetime.fromisoformat(item["created_at"]), item["text"], item.get("model"), audio_file,
                        item.get("pinned") is True, ident,
                    ))
                self._entries = self._within_limit(entries)
                self._persisted_audio = {entry.audio_file for entry in entries if entry.audio_file}
                return
            except (OSError, ValueError, TypeError, KeyError):
                # Preserve a damaged/unreadable file for recovery. New text is
                # still available from memory and the regular application log.
                self._persist_enabled = False
                logger.exception("Cannot load dictation history; preserving its existing file")

        # Bootstrap only when there is no usable history. Older log backups
        # come first so the limit retains the newest complete results.
        for name in ("app.log.3", "app.log.2", "app.log.1", "app.log"):
            source = self.path.parent / name
            if not source.exists():
                continue
            try:
                with source.open(encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        match = TRANSCRIPT_LINE.match(line.rstrip("\r\n"))
                        if match and match[2].strip():
                            stamp = datetime.fromisoformat(match[1].replace(",", ".")).astimezone()
                            self._entries.append(HistoryEntry(stamp, match[2]))
                            if len(self._entries) > self._max_entries:
                                self._entries.pop(0)
            except (OSError, ValueError):
                logger.exception("Cannot recover previous transcripts from %s", source.name)
        if self._entries:
            logger.info("Recovered %d dictations from application logs", len(self._entries))
            self._persist()

    def recent(self):
        """Return an immutable snapshot, newest first, for menu callbacks."""
        with self._lock:
            return tuple(reversed(self._entries))

    def add(self, text, *, model=None):
        if not text or not text.strip():
            return None
        entry = HistoryEntry(datetime.now().astimezone(), text, model)
        with self._lock:
            self._entries = self._within_limit([*self._entries, entry])
            self._expire_audio()
            self._persist()
        return entry

    def _within_limit(self, entries):
        excess = sum(not entry.pinned for entry in entries) - self._max_entries
        if excess <= 0:
            return entries
        retained = []
        for entry in entries:
            if excess > 0 and not entry.pinned:
                excess -= 1
            else:
                retained.append(entry)
        return retained

    def _index(self, entry):
        return next((i for i, current in enumerate(self._entries) if current.id == entry.id), None)

    def set_pinned(self, entry, pinned):
        with self._lock:
            index = self._index(entry)
            if index is None:
                return False
            previous = self._entries
            updated = list(previous)
            updated[index] = replace(updated[index], pinned=pinned)
            self._entries = self._within_limit(updated)
            self._expire_audio()
            if self._persist():
                return True
            self._entries = previous
            return False

    def set_audio_retention(self, days):
        with self._lock:
            self.audio_retention_days = days
            previous = list(self._entries)
            if self._expire_audio() and not self._persist():
                self._entries = previous
                return False
            return True

    def _expire_audio(self):
        if not self.audio_retention_days:
            return False
        cutoff = datetime.now().astimezone() - timedelta(days=self.audio_retention_days)
        changed = False
        for index, entry in enumerate(self._entries):
            if entry.audio_file and not entry.pinned and entry.created_at.astimezone() < cutoff:
                self._entries[index] = replace(entry, audio_file=None)
                changed = True
        return changed

    def audio_path(self, entry):
        if entry.audio_file:
            path = self.audio_dir / entry.audio_file
            if path.is_file():
                return path
        return None

    def save_audio(self, entry, audio, sample_rate):
        """Attach a WAV after text delivery has completed."""
        if not self._persist_enabled or entry.audio_file:
            return
        name = uuid4().hex + ".wav"
        try:
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            with wave.open(str(self.audio_dir / name), "wb") as output:
                output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
                output.writeframes(audio.astype("<i2", copy=False).tobytes())
        except (OSError, ValueError, wave.Error):
            self._remove_audio(name)
            logger.exception("Cannot save the dictation audio")
            return

        with self._lock:
            position = self._index(entry)
            if position is not None and not self._entries[position].audio_file:
                current = self._entries[position]
                self._entries[position] = replace(current, audio_file=name)
                if self._persist():
                    return
                self._entries[position] = current
            self._remove_audio(name)

    def _remove_audio(self, name):
        try:
            (self.audio_dir / name).unlink(missing_ok=True)
            return True
        except OSError:
            logger.warning("Cannot remove an unused dictation audio file")
            return False

    def _persist(self):
        if not self._persist_enabled:
            return False
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix="dictation-history-", suffix=".tmp", delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump({
                    "version": 1,
                    "entries": [
                        {
                            "created_at": entry.created_at.isoformat(),
                            "id": entry.id,
                            "text": entry.text,
                            **({"pinned": True} if entry.pinned else {}),
                            **({"model": entry.model} if entry.model else {}),
                            **({"audio_file": entry.audio_file} if entry.audio_file else {}),
                        }
                        for entry in self._entries
                    ],
                }, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
            active_audio = {entry.audio_file for entry in self._entries if entry.audio_file}
            pending = {name for name in self._persisted_audio - active_audio if not self._remove_audio(name)}
            self._persisted_audio = active_audio | pending
            return True
        except OSError:
            logger.exception("Cannot save dictation history; text remains available in this session")
            return False
        finally:
            if temporary_path and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    logger.warning("Cannot remove temporary history file: %s", temporary_path)
