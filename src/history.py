"""Keep complete transcripts recoverable independently of text insertion."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from src.logger import logger


TRANSCRIPT_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) .*?"
    r" - Resulting transcript text: (.*)$"
)


@dataclass(frozen=True)
class HistoryEntry:
    created_at: datetime
    text: str

    @property
    def menu_label(self):
        preview = " ".join(self.text.split())
        if len(preview) > 65:
            preview = preview[:65] + "…"
        # Ampersands are menu mnemonics in Win32; display literal text instead.
        preview = preview.replace("&", "&&")
        return f"{self.created_at:%d.%m %H:%M} — {preview}"


class DictationHistory:
    def __init__(self, path=None, max_entries=20):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.path = Path(path or Path(__file__).resolve().parents[1] / "logs" / "dictation-history.json")
        self._entries = deque(maxlen=max_entries)
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
                    entries.append(HistoryEntry(datetime.fromisoformat(item["created_at"]), item["text"]))
                self._entries.extend(entries)
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

    def add(self, text):
        if not text or not text.strip():
            return None
        entry = HistoryEntry(datetime.now().astimezone(), text)
        with self._lock:
            self._entries.append(entry)
            self._persist()
        return entry

    def _persist(self):
        if not self._persist_enabled:
            return
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
                        {"created_at": entry.created_at.isoformat(), "text": entry.text}
                        for entry in self._entries
                    ],
                }, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        except OSError:
            logger.exception("Cannot save dictation history; text remains available in this session")
        finally:
            if temporary_path and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    logger.warning("Cannot remove temporary history file: %s", temporary_path)
