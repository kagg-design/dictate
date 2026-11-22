"""Keep complete transcripts recoverable independently of text insertion."""

from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
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


@dataclass(frozen=True)
class HistoryEntry:
    created_at: datetime
    text: str
    model: str | None = None
    audio_file: str | None = None

    @property
    def menu_label(self):
        preview = " ".join(self.text.split())
        if len(preview) > 65:
            preview = preview[:65] + "…"
        # Ampersands are menu mnemonics in Win32; display literal text instead.
        preview = preview.replace("&", "&&")
        return f"{self.created_at:%d.%m %H:%M} — {preview}"


class DictationHistory:
    def __init__(self, path=None, max_entries=100):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.path = Path(path or LOG_DIR / "dictation-history.json")
        self.audio_dir = self.path.parent / "dictation-audio"
        self._entries = deque(maxlen=max_entries)
        self._persisted_audio = set()
        self._lock = threading.Lock()
        self._persist_enabled = True
        self._load()

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
                    entries.append(HistoryEntry(
                        datetime.fromisoformat(item["created_at"]), item["text"], item.get("model"), audio_file
                    ))
                self._entries.extend(entries)
                self._persisted_audio = {entry.audio_file for entry in entries if entry.audio_file}
                return
            except (OSError, ValueError, TypeError, KeyError):
                # Preserve a damaged/unreadable file for recovery. New text is
                # still available from memory and the regular application log.
                self._persist_enabled = False
                logger.exception("Cannot load dictation history; preserving its existing file")

        # Bootstrap only when there is no usable history. Older log backups
        # come first so the bounded deque retains the newest complete results.
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
            self._entries.append(entry)
            self._persist()
        return entry

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
            if entry in self._entries:
                position = self._entries.index(entry)
                self._entries[position] = replace(entry, audio_file=name)
                if self._persist():
                    return
                self._entries[position] = entry
            self._remove_audio(name)

    def _remove_audio(self, name):
        try:
            (self.audio_dir / name).unlink(missing_ok=True)
        except OSError:
            logger.warning("Cannot remove an unused dictation audio file")

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
                            "text": entry.text,
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
            for name in self._persisted_audio - active_audio:
                self._remove_audio(name)
            self._persisted_audio = active_audio
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
