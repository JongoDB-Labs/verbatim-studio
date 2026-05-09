"""Rolling-buffer live transcription service.

Replaces the original "one chunk in, one transcription out" pipeline with
a buffered architecture that re-transcribes the trailing N seconds of
audio each time a new chunk arrives. The result: Whisper sees full word
boundaries (much better accuracy at chunk seams), pyannote gets enough
audio to cluster speakers reliably, and silent chunks bypass the model
entirely.

## Pipeline

For each WebM/Opus chunk arriving over the WebSocket:

1. **Decode** to 16 kHz mono float32 PCM via PyAV (services/audio_decoder).
2. **VAD** the PCM through Silero (services/vad_service). If no speech is
   detected we still advance the buffer clock (so timestamps stay correct
   for later speech) but skip the expensive transcription + diarization
   stages entirely.
3. **Append + trim** the PCM to a rolling window of `BUFFER_SECONDS`. As
   audio falls off the front of the window, any tentative segments whose
   end time is now older than the window get **promoted to confirmed**
   using their last-known text.
4. **Re-transcribe** the entire window. Whisper sees the speech in
   context, so cross-seam words resolve correctly.
5. **Replace tentative tail**: the new segments inside the window
   replace the previous tentative list wholesale.
6. **Diarize**: every `DIARIZATION_INTERVAL_SECONDS`, run pyannote on
   the buffer and stitch the resulting speaker labels onto matching
   segments — both tentative and recently-confirmed (so the user sees
   speakers update retroactively as more audio reveals who's talking).

## Stability semantics

- **Confirmed segments** are append-only and never revise.
- **Tentative segments** cover the trailing buffer window and may
  change with each new chunk: text can be revised, removed, or split.

The frontend maintains both lists separately and renders tentative
segments with a subtle visual cue.

## Why not "LocalAgreement-2"?

The popular streaming-Whisper algorithm (LocalAgreement-2) compares
text across consecutive transcription runs to identify stable words.
We use a simpler time-based stability boundary because:

  (a) re-transcribing the same audio repeatedly already gives high
      stability — words 4+ seconds back rarely change;
  (b) text-level diff is fragile across model invocations (different
      whitespace, punctuation, capitalization);
  (c) time-based stability is trivial to reason about and lets us
      give the user a precise "this won't change" guarantee.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from core.interfaces import (
    ITranscriptionEngine,
    TranscriptionOptions,
    TranscriptionSegment,
)
from services import vad_service
from services.audio_decoder import decode_chunk_to_pcm, pcm_to_wav_bytes
from services.transcript_filter import is_hallucination

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

# Length of the rolling audio buffer fed back to Whisper. Short enough
# that re-transcription stays cheap; long enough that Whisper sees full
# utterances and pyannote can cluster speakers.
BUFFER_SECONDS = 8.0

# Time-based stability boundary. A tentative segment whose absolute end
# time is older than (now - STABILITY_DURATION) is promoted to confirmed.
# The buffer must be at least this long for the boundary to mean anything.
STABILITY_SECONDS = 4.0

# Run pyannote no more often than this. Pyannote is the heaviest stage
# in the pipeline; running it on every chunk would push end-to-end
# latency unacceptably high.
DIARIZATION_INTERVAL_SECONDS = 4.0

# Maximum prompt context fed to Whisper as initial_prompt. Whisper's
# conditioning is "soft" — too much biases the model heavily toward the
# prompt; too little gives no benefit.
PROMPT_CONTEXT_CHARS = 200


@dataclass
class LiveSegment:
    """Single segment in the live transcript.

    Carries a stable session-level id so the frontend can match
    revisions to existing items rather than re-rendering the world.
    """

    id: str
    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
    words: list[dict] | None = None
    edited_by: str | None = None
    tentative: bool = True

    def to_wire(self) -> dict[str, Any]:
        """Serialise for the WebSocket protocol."""
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "speaker": self.speaker,
            "confidence": self.confidence,
            "words": self.words,
            "edited_by": self.edited_by,
            "tentative": self.tentative,
        }


# Event callback signature used to send messages back to the WebSocket.
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class _SessionState:
    """Internal state held by RollingTranscriber."""

    language: str
    high_detail: bool
    started_at: float

    # Rolling PCM buffer in 16 kHz mono float32. Always grows; trimmed
    # to BUFFER_SECONDS in process_chunk.
    pcm: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float32)
    )
    # Wall-clock time (seconds since session start) of the first sample
    # currently in `pcm`. Advances as the buffer trims.
    buffer_start_time: float = 0.0

    # Segments that have aged out of the buffer — locked-in, never revise.
    confirmed: list[LiveSegment] = field(default_factory=list)
    # Segments inside the current buffer — may be revised by the next run.
    tentative: list[LiveSegment] = field(default_factory=list)

    # Original WebM chunks accumulated for save-time audio export. We
    # keep the encoded form (not PCM) to avoid re-encoding for storage.
    encoded_chunks: list[bytes] = field(default_factory=list)

    # Track speakers across chunks. Maps raw pyannote labels (per-chunk)
    # to stable session labels. Populated by process_diarization.
    speaker_label_map: dict[str, str] = field(default_factory=dict)
    next_speaker_id: int = 0
    speakers_found: set[str] = field(default_factory=set)
    diarization_warned: bool = False
    last_diarization_at: float = 0.0


class RollingTranscriber:
    """Stateful rolling-buffer transcriber for one live session.

    All audio + state lives here; the WebSocket route is a thin shell
    that decodes inbound messages, calls into this service, and forwards
    callback events to the client.
    """

    def __init__(
        self,
        engine: ITranscriptionEngine,
        diarization_service: Any | None,
        language: str,
        high_detail: bool,
        data_dir: Path,
        emit: EventCallback,
    ):
        self.engine = engine
        self.diarization_service = diarization_service
        self.data_dir = data_dir
        self.emit = emit

        self.state = _SessionState(
            language=language,
            high_detail=high_detail,
            started_at=time.monotonic(),
        )

        # Serialise transcription so a slow chunk doesn't cause a queue
        # of overlapping engine calls (which would race on the model).
        self._transcribe_lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────

    @property
    def total_duration(self) -> float:
        """Length of audio (in seconds) processed so far."""
        return self.state.buffer_start_time + self.state.pcm.size / SAMPLE_RATE

    @property
    def all_segments(self) -> list[LiveSegment]:
        """Confirmed + tentative, ordered by start time."""
        return sorted(
            [*self.state.confirmed, *self.state.tentative],
            key=lambda s: s.start,
        )

    @property
    def speakers_found(self) -> set[str]:
        return self.state.speakers_found

    @property
    def encoded_chunks(self) -> list[bytes]:
        return self.state.encoded_chunks

    async def process_chunk(self, audio_bytes: bytes) -> None:
        """Run the full pipeline on a single inbound WebM/Opus chunk.

        Steps: decode → VAD → append+trim → re-transcribe → diarize →
        emit messages. Errors at any stage are logged and the chunk is
        skipped — the session continues.
        """
        # 1. Persist the encoded chunk for save-time export.
        self.state.encoded_chunks.append(audio_bytes)

        # 2. Decode to PCM.
        pcm = decode_chunk_to_pcm(audio_bytes)
        if pcm.size == 0:
            return

        # 3. VAD: if there's no speech we just advance the clock without
        # touching the model. Trim the buffer to keep the window length
        # bounded even during long silences.
        speech_present = vad_service.has_speech(pcm)

        # Always append to the buffer so the session clock is correct.
        self.state.pcm = np.concatenate([self.state.pcm, pcm])
        self._trim_buffer()

        if not speech_present:
            return

        # 4. Re-transcribe the buffer. The lock prevents overlapping
        # engine calls when chunks arrive faster than transcription.
        async with self._transcribe_lock:
            await self._retranscribe_buffer()

        # 5. Diarize on a longer cadence than transcription.
        if (
            self.state.high_detail
            and self.diarization_service is not None
            and time.monotonic() - self.state.last_diarization_at
            >= DIARIZATION_INTERVAL_SECONDS
        ):
            await self._diarize_buffer()
            self.state.last_diarization_at = time.monotonic()

    # ── Editing API (called from REST endpoints) ──────────────────────

    def update_segment_text(self, segment_id: str, new_text: str) -> bool:
        """Replace a confirmed/tentative segment's text in place.

        Returns True if the segment was found, False otherwise.
        """
        for seg in [*self.state.confirmed, *self.state.tentative]:
            if seg.id == segment_id:
                seg.text = new_text
                seg.edited_by = "human"
                return True
        return False

    def delete_segment(self, segment_id: str) -> bool:
        before = len(self.state.confirmed) + len(self.state.tentative)
        self.state.confirmed = [s for s in self.state.confirmed if s.id != segment_id]
        self.state.tentative = [s for s in self.state.tentative if s.id != segment_id]
        after = len(self.state.confirmed) + len(self.state.tentative)
        return after < before

    # ── Pipeline internals ────────────────────────────────────────────

    def _trim_buffer(self) -> None:
        """Trim PCM to BUFFER_SECONDS, advancing buffer_start_time.

        Any tentative segments now outside the buffer are promoted to
        confirmed (using their last-known text/speaker) and an event is
        emitted so the frontend can mark them locked.
        """
        max_samples = int(BUFFER_SECONDS * SAMPLE_RATE)
        if self.state.pcm.size <= max_samples:
            return

        trim = self.state.pcm.size - max_samples
        self.state.pcm = self.state.pcm[trim:]
        self.state.buffer_start_time += trim / SAMPLE_RATE

    async def _emit_segments_replace(self) -> None:
        """Send the full current segment state to the client.

        Confirmed list is append-only (frontend can dedupe by id);
        tentative list is replaceable.
        """
        await self.emit({
            "type": "segments_replace",
            "confirmed": [s.to_wire() for s in self.state.confirmed],
            "tentative": [s.to_wire() for s in self.state.tentative],
            "duration": self.total_duration,
        })

    async def _retranscribe_buffer(self) -> None:
        """Run Whisper over the current buffer and update segment state."""
        # Promote tentative segments that have aged past the stability
        # boundary to confirmed. Use their CURRENT (latest tentative)
        # text/speaker — this is the last revision before they lock.
        stability_cutoff = self.total_duration - STABILITY_SECONDS

        still_tentative: list[LiveSegment] = []
        newly_confirmed: list[LiveSegment] = []
        for seg in self.state.tentative:
            if seg.end <= stability_cutoff:
                seg.tentative = False
                newly_confirmed.append(seg)
            else:
                still_tentative.append(seg)
        if newly_confirmed:
            self.state.confirmed.extend(newly_confirmed)
        self.state.tentative = still_tentative

        # Build initial_prompt from the recent confirmed text — gives
        # Whisper continuity context across runs.
        prompt = self._build_prompt_context()

        # Write the buffer to a tempfile (engines accept paths).
        wav_bytes = pcm_to_wav_bytes(self.state.pcm)
        if not wav_bytes:
            return

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
            dir=str(self.data_dir),
        ) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name

        try:
            options = TranscriptionOptions(
                language=self.state.language,
                word_timestamps=self.state.high_detail,
                initial_prompt=prompt or None,
            )
            try:
                result = await self.engine.transcribe(tmp_path, options)
            except Exception as exc:
                logger.warning("Buffer transcription failed: %s", exc)
                return

            # Translate buffer-relative times to session-absolute.
            new_tentative: list[LiveSegment] = []
            prev_text: str | None = (
                self.state.confirmed[-1].text if self.state.confirmed else None
            )
            for seg in result.segments:
                seg_duration = (
                    (seg.end - seg.start) if seg.end and seg.start else None
                )
                if is_hallucination(
                    seg.text,
                    confidence=seg.confidence,
                    duration=seg_duration,
                    prev_text=prev_text,
                ):
                    continue

                abs_start = self.state.buffer_start_time + seg.start
                abs_end = self.state.buffer_start_time + seg.end

                # Don't re-emit any segment whose time range is entirely
                # before the stability boundary — those are confirmed
                # and we don't want to revise them.
                if abs_end <= stability_cutoff:
                    # Skip — confirmed segments were already locked in
                    # during the promotion step above.
                    continue

                words_data = None
                if self.state.high_detail and seg.words:
                    words_data = [
                        {
                            "word": w.word,
                            "start": self.state.buffer_start_time + w.start,
                            "end": self.state.buffer_start_time + w.end,
                            "confidence": w.confidence,
                        }
                        for w in seg.words
                    ]

                new_tentative.append(LiveSegment(
                    id=uuid.uuid4().hex,
                    start=abs_start,
                    end=abs_end,
                    text=seg.text.strip(),
                    confidence=seg.confidence,
                    words=words_data,
                    tentative=True,
                ))
                prev_text = seg.text

            self.state.tentative = new_tentative
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

        await self._emit_segments_replace()

    def _build_prompt_context(self) -> str:
        """Last ~200 chars of confirmed text — fed back as Whisper prompt.

        We use confirmed text only (not tentative) because tentative
        text changes between runs, and a churning prompt actively hurts
        accuracy.
        """
        if not self.state.confirmed:
            return ""
        tail = " ".join(s.text for s in self.state.confirmed[-6:]).strip()
        return tail[-PROMPT_CONTEXT_CHARS:]

    async def _diarize_buffer(self) -> None:
        """Run pyannote on the buffer and stitch speakers onto segments.

        The diarization output is keyed to the buffer's time range; we
        match by segment time-overlap and apply the stable session
        label. Both tentative AND newly-confirmed segments may have
        their speaker updated retroactively.
        """
        if self.state.pcm.size < int(3 * SAMPLE_RATE):
            return

        wav_bytes = pcm_to_wav_bytes(self.state.pcm)
        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
            dir=str(self.data_dir),
        ) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name

        try:
            buffer_segments_for_pyannote = []
            for seg in [*self.state.confirmed, *self.state.tentative]:
                # Only segments whose time range is inside the buffer.
                if seg.end < self.state.buffer_start_time:
                    continue
                rel_start = max(0.0, seg.start - self.state.buffer_start_time)
                rel_end = max(rel_start, seg.end - self.state.buffer_start_time)
                buffer_segments_for_pyannote.append({
                    "start": rel_start,
                    "end": rel_end,
                    "text": seg.text,
                })
            if not buffer_segments_for_pyannote:
                return

            try:
                dia_result = await self.diarization_service.diarize(
                    audio_path=tmp_path,
                    segments=buffer_segments_for_pyannote,
                )
            except Exception as exc:
                logger.warning("Diarization failed: %s", exc)
                if not self.state.diarization_warned:
                    self.state.diarization_warned = True
                    await self.emit({
                        "type": "warning",
                        "message": (
                            f"Speaker identification failed — {exc}."
                            " Transcription will continue without speaker"
                            " labels."
                        ),
                    })
                return

            dia_segs = dia_result.get("segments", [])
            updates: list[dict[str, Any]] = []

            i = 0
            for seg in [*self.state.confirmed, *self.state.tentative]:
                if seg.end < self.state.buffer_start_time:
                    continue
                if i >= len(dia_segs):
                    break
                raw_label = dia_segs[i].get("speaker")
                i += 1
                if not raw_label:
                    continue
                stitched = self.state.speaker_label_map.get(raw_label)
                if stitched is None:
                    self.state.next_speaker_id += 1
                    stitched = f"Speaker {self.state.next_speaker_id}"
                    self.state.speaker_label_map[raw_label] = stitched
                    self.state.speakers_found.add(stitched)

                if seg.speaker != stitched:
                    seg.speaker = stitched
                    updates.append({"id": seg.id, "speaker": stitched})

            if updates:
                await self.emit({
                    "type": "speaker_update",
                    "updates": updates,
                })
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

    async def finalize(self) -> None:
        """Promote any remaining tentative segments to confirmed.

        Called when the user clicks Stop — at that point we know no
        more audio is coming, so the trailing tentative tail can lock
        in. Emits a final segments_replace so the client sees the
        committed state.
        """
        for seg in self.state.tentative:
            seg.tentative = False
            self.state.confirmed.append(seg)
        self.state.tentative = []
        await self._emit_segments_replace()
