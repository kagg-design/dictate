"""Recognize audio while the recorder continues to collect it."""

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

from faster_whisper.vad import get_speech_timestamps
import numpy as np

from src.logger import logger

if TYPE_CHECKING:
    from src.inserter import InsertionTarget


def _words(text):
    return re.findall(r"[^\W_]+", text.casefold())


def _contains_words(words, phrase):
    return any(words[i:i + len(phrase)] == phrase for i in range(len(words) - len(phrase) + 1))


@dataclass
class CompletedCapture:
    live: "LiveTranscription"
    audio: np.ndarray
    save_wav: bool = False
    insert: bool = True
    postprocess_model: str = ""
    target: "InsertionTarget | None" = None


class LiveTranscription:
    """Keep one ordered text result and an audio offset for the active capture."""

    def __init__(self, sample_rate, *, rolling=False, show_preview=False):
        self.sample_rate = sample_rate
        self.blocks_seen = 0
        self._pending = []
        self._samples_seen = 0
        self._last_inspection = 0
        self._empty_cut = None
        self._processed = 0
        self._parts = []
        self._failed = False
        self._rolling = RollingTranscription(sample_rate) if rolling else None
        self._whisper_preview = show_preview and not rolling and sample_rate == 16000
        self._draft = ""
        self._draft_opening = []
        self._next_preview = int(1.5 * sample_rate)
        self._preview_speech_end = 0

    def feed(self, blocks, transcriber, *, preview_active=None):
        self.blocks_seen += len(blocks)
        if not blocks or self._failed:
            return

        self._pending.extend(blocks)
        self._samples_seen += sum(map(len, blocks))
        pending_samples = self._samples_seen - self._processed
        new_samples = self._samples_seen - self._last_inspection
        try:
            cut = None
            speech_end = None
            if self.sample_rate == 16000 and pending_samples >= 20000 and new_samples >= 4000:
                self._last_inspection = self._samples_seen
                cut, speech_end = self._find_pause(pending_samples, max(64000, new_samples + 32000))
            if cut is None or cut == self._empty_cut:
                if self._rolling is not None:
                    self._rolling.feed(blocks, transcriber, self._pending_audio)
                elif self._whisper_preview and speech_end is not None:
                    self._update_draft(transcriber, self._processed + speech_end, preview_active)
                return

            pending = self._pending_audio()
            text = self._decode_chunk(pending[:cut], transcriber).strip()
            if not text:
                # A pause does not guarantee that the preceding speech was decoded.
                self._empty_cut = cut
                if self._rolling is not None:
                    self._rolling.feed(blocks, transcriber, self._pending_audio)
                return
            self._parts.append(text)
            tail = pending[cut:]
            self._pending = [tail.copy()] if len(tail) else []
            self._processed += cut
            self._empty_cut = None
            self._draft = ""
            self._draft_opening = []
            self._preview_speech_end = self._processed
            if self._rolling is not None:
                self._rolling = RollingTranscription(self.sample_rate)
                self._rolling.feed(self._pending, transcriber, self._pending_audio)
            logger.info("Recognized a phrase during capture. Processed %.2fs of audio.", self._processed / self.sample_rate)
        except Exception:
            self._failed = True
            self._pending.clear()
            logger.exception("Live transcription failed. Will retry the full recording on release.")

    def _pending_audio(self):
        return np.concatenate(self._pending, axis=0)

    def preview(self):
        tail = self._rolling.preview() if self._rolling is not None else self._draft
        parts = [*self._parts, tail] if tail else self._parts
        return " ".join(parts)[-180:].lstrip()

    def _update_draft(self, transcriber, speech_end, preview_active):
        if self._samples_seen < self._next_preview or speech_end <= self._preview_speech_end:
            return
        if preview_active is not None and not preview_active():
            return
        self._next_preview = self._samples_seen + int(1.35 * self.sample_rate)
        self._preview_speech_end = speech_end
        try:
            text = transcriber.transcribe_preview(self._recent_audio(4 * self.sample_rate)).strip()
            if text:
                self._draft = text
                if not self._draft_opening and self._samples_seen - self._processed <= 4 * self.sample_rate:
                    words = _words(text)
                    if len(words) >= 2:
                        self._draft_opening = words[:8]
        except Exception:
            self._whisper_preview = False
            logger.exception("Could not update the Whisper preview.")

    def _decode_chunk(self, audio, transcriber, *, whole=False):
        result = transcriber.transcribe(audio) if whole else transcriber.transcribe_chunk(audio)
        if not self._draft_opening:
            return result
        words = _words(result)
        beginning = self._draft_opening[:2]
        if _contains_words(words[:4], beginning):
            return result
        if len(words) >= len(self._draft_opening) and not _contains_words(self._draft_opening[1:], words[:2]):
            return result

        # Both passes use the original audio. Only accept a longer result
        # that preserves the observed opening and the ordinary pass's ending.
        try:
            recovered = transcriber.transcribe_unfiltered(audio)
            recovered_words = _words(recovered)
            if (len(recovered_words) > len(words) and _contains_words(recovered_words[:4], beginning)
                    and _contains_words(recovered_words, words[-2:])):
                logger.info("Recovered a missing Whisper opening from the original audio.")
                return recovered
        except Exception:
            logger.exception("Could not retry the Whisper opening.")
        return result

    def _recent_audio(self, window_size):
        blocks = []
        length = 0
        for block in reversed(self._pending):
            blocks.append(block)
            length += len(block)
            if length >= window_size:
                break
        return np.concatenate(blocks[::-1], axis=0)[-window_size:]

    def _find_pause(self, pending_samples, window_size):
        window = self._recent_audio(window_size)
        offset = pending_samples - len(window)
        speech = get_speech_timestamps(
            window.reshape(-1).astype(np.float32) / 32768.0,
            min_speech_duration_ms=100,
            min_silence_duration_ms=400,
            speech_pad_ms=0,
        )
        # Split in the middle of at least 400 ms of silence. Keep a margin on
        # both sides so the next decoder gets the onset of the next utterance.
        cut = None
        next_start = len(window)
        for region in reversed(speech):
            if next_start - region["end"] >= 6400:
                candidate = offset + region["end"] + 3200
                if candidate >= 16000:
                    cut = candidate
                    break
            next_start = region["start"]
        return cut, offset + speech[-1]["end"] if speech else None

    def finish(self, audio, transcriber):
        if self._failed:
            return transcriber.transcribe(audio)
        if not self._processed and self._rolling is None:
            return self._decode_chunk(audio, transcriber, whole=True)

        remaining = audio[self._processed:]
        if len(remaining):
            if self._rolling is None:
                text = self._decode_chunk(remaining, transcriber).strip()
            else:
                text = self._rolling.finish(remaining, transcriber)
            if text:
                self._parts.append(text)
        return transcriber.clean_text(" ".join(self._parts))


@dataclass(frozen=True)
class _TimedWord:
    text: str
    start: int
    end: int

    @property
    def key(self):
        return self.text.casefold().strip(".,!?…:;«»“”\"()")


class RollingTranscription:
    """Decode overlapping windows and retain words that have enough right context."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self._audio = np.empty(0, dtype=np.int16)
        self._audio_start = 0
        self._confirmed = []
        self._latest = None
        self._next_decode = 2 * sample_rate
        self._stride = int(0.9 * sample_rate)
        self._lookahead = int(0.65 * sample_rate)
        self._left_context = 3 * sample_rate
        self._alignment_tolerance = int(0.35 * sample_rate)
        self._failed = False

    def feed(self, blocks, transcriber, full_audio=None):
        if not blocks or self._failed:
            return

        self._audio = np.concatenate((self._audio, *(block.reshape(-1) for block in blocks)))
        end = self._audio_start + len(self._audio)
        if end < self._next_decode:
            return
        self._next_decode = end + self._stride

        try:
            words = self._decode(self._audio, self._audio_start, transcriber)
            merged = self._merge(words, self._audio_start)
            if merged is None and len(self._audio) >= 7 * self.sample_rate and full_audio is not None:
                # Recover context while the microphone continues recording.
                self._audio = full_audio().reshape(-1)
                self._audio_start = 0
                words = self._decode(self._audio, 0, transcriber)
                merged = words
            self._latest = (end, self._audio_start, words)
            if merged is not None:
                stable_end = end - self._lookahead
                self._confirmed = [word for word in merged if word.end <= stable_end]
                self._trim_audio(merged, stable_end)
                logger.info(
                    "Mini decoded %.2fs during capture. Confirmed words: %d",
                    end / self.sample_rate, len(self._confirmed),
                )
            if merged is None and len(self._audio) > 12 * self.sample_rate:
                raise RuntimeError("No reliable alignment within the recent speech window")
        except Exception:
            self._failed = True
            self._audio = np.empty(0, dtype=np.int16)
            logger.exception("Rolling transcription will retry the complete recording on release.")

    def _decode(self, audio, offset, transcriber):
        return [
            _TimedWord(
                word["w"],
                offset + round(word["start"] * self.sample_rate),
                offset + round(word["end"] * self.sample_rate),
            )
            for word in transcriber.transcribe_words(audio)
        ]

    def preview(self):
        if self._latest is None:
            return ""
        _, start, words = self._latest
        merged = self._merge(words, start)
        return " ".join(word.text for word in (merged if merged is not None else self._confirmed)[-45:])

    def _merge(self, words, start):
        if start == 0:
            return words
        if not self._confirmed or start >= self._confirmed[-1].end:
            return [*self._confirmed, *words]

        # Match a recent word by spelling and time so repeated words remain
        # distinct. An earlier match lets the next window correct the seam.
        candidates = []
        for i in range(max(0, len(self._confirmed) - 3), len(self._confirmed)):
            anchor = self._confirmed[i]
            for j, word in enumerate(words):
                distance = abs(word.end - anchor.end)
                if word.key and word.key == anchor.key and distance <= self._alignment_tolerance:
                    agreement = 1
                    for back in range(1, min(i, j, 2) + 1):
                        left, right = self._confirmed[i - back], words[j - back]
                        if left.key != right.key or abs(left.end - right.end) > self._alignment_tolerance:
                            break
                        agreement += 1
                    candidates.append((agreement, i, -distance, j))
        if not candidates:
            return None

        _, i, _, j = max(candidates)
        return [*self._confirmed[:i], *words[j:]]

    def _trim_audio(self, words, stable_end):
        if len(self._audio) < 2 * self._left_context:
            return
        context_start = self._audio_start
        if self._confirmed:
            last = self._confirmed[-1]
            anchor = self._confirmed[max(0, len(self._confirmed) - 3)]
            context_start = min(last.end - self._left_context, anchor.start - self._alignment_tolerance)

        # Release old audio after a sustained gap while retaining the most
        # recent context for the next audible words.
        last_word_end = words[-1].end if words else self._audio_start
        if stable_end - last_word_end >= 3 * self.sample_rate:
            context_start = stable_end - self._left_context

        if context_start > self._audio_start:
            self._audio = self._audio[context_start - self._audio_start:].copy()
            self._audio_start = context_start

    def finish(self, audio, transcriber):
        if self._failed or self._latest is None:
            return transcriber.transcribe(audio)

        end, start, words = self._latest
        try:
            if end != len(audio):
                start = self._audio_start
                # A short, silent right margin lets Mini finish the last word
                # without waiting for another microphone block after release.
                final_audio = np.pad(audio[start:].reshape(-1), (0, self.sample_rate // 4))
                words = self._decode(final_audio, start, transcriber)
            merged = self._merge(words, start)
            if merged:
                return transcriber.clean_text(" ".join(word.text for word in merged))
        except Exception:
            logger.exception("Could not complete rolling transcription from the final audio.")
        return transcriber.transcribe(audio)
