# LangExtract Integration — Implementation Plan

**Issue:** #130
**Timeline:** May–June 2026
**Goal:** Add structured entity extraction from transcripts with source grounding, using Google's LangExtract library with a custom llama-cpp-python provider (no Ollama dependency).

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  User clicks "Extract Entities" in transcript view              │
│                         ↓                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Tool: extract_entities                                    │   │
│  │  1. Load segments for transcript                          │   │
│  │  2. Join segment text into continuous document            │   │
│  │  3. Build char-offset-to-segment mapping                  │   │
│  │  4. Call LangExtract with domain template                 │   │
│  │  5. Map extraction char offsets → segment IDs + timestamps│   │
│  │  6. Return structured entities                            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                         ↓                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ LangExtract Pipeline                                      │   │
│  │  prompting → chunking → annotation → resolver             │   │
│  │       ↓                                                   │   │
│  │  LlamaCppProvider (custom, wraps existing LlamaCppAI)     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                         ↓                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ UI: Entity Highlights Panel                               │   │
│  │  - Color-coded entity tags on transcript segments         │   │
│  │  - Entity list sidebar with type/value/source span        │   │
│  │  - Click entity → scroll to source segment                │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Step 1: Evaluate google-genai Dependency (Day 1)

LangExtract has a hard dependency on `google-genai>=1.39.0` even when not using Gemini. This is a concern for a privacy-first product.

**Action:** Test if LangExtract can be imported and used with only Ollama/custom provider without triggering any Google API calls.

```python
# Test in isolated env
pip install langextract
python -c "from langextract import extraction; print('OK')"
# Check if google-genai makes any network calls at import time
```

**If google-genai phones home at import:** Consider vendoring/forking the LangExtract core modules (extraction, chunking, resolver, annotation) without the Google provider. Apache 2.0 license allows this.

**If google-genai is passive:** Proceed with standard pip install, add to `pyproject.toml` extras.

---

## Step 2: Custom llama-cpp-python Provider (Days 2-4)

LangExtract's provider system uses Python entry points. We write a provider that routes inference to the existing `LlamaCppAIService`.

**File:** `packages/backend/services/langextract_provider.py`

```python
"""LangExtract provider that wraps Verbatim's existing llama-cpp-python backend."""

from __future__ import annotations

import json
import logging
from typing import Any

from langextract.providers import registry
from langextract.inference import InferenceResult

logger = logging.getLogger(__name__)


@registry.register(name="llama-cpp", pattern=r"^(granite|llama|mistral|local)")
class LlamaCppProvider:
    """Routes LangExtract inference to Verbatim's llama-cpp-python service."""

    def __init__(self, model_name: str, **kwargs):
        self.model_name = model_name
        self._llm = None

    def _get_llm(self):
        """Lazy-load the llama.cpp model via Verbatim's existing service."""
        if self._llm is None:
            from services.ai.llama_cpp_service import get_or_create_llm
            self._llm = get_or_create_llm()
        return self._llm

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        **kwargs,
    ) -> InferenceResult:
        """Generate a response using the local LLM."""
        llm = self._get_llm()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = llm.create_chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content = response["choices"][0]["message"]["content"]
        usage = response.get("usage", {})

        return InferenceResult(
            content=content,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )

    @property
    def supports_schema_constraints(self) -> bool:
        """llama.cpp supports JSON grammar constraints."""
        return True

    def get_schema_config(self, schema: dict) -> dict:
        """Return grammar-based JSON constraint for llama.cpp."""
        return {"response_format": {"type": "json_object", "schema": schema}}
```

**Registration** in `pyproject.toml`:
```toml
[project.entry-points."langextract.providers"]
llama-cpp = "services.langextract_provider:LlamaCppProvider"
```

---

## Step 3: Segment-to-Character Offset Mapping (Days 3-5)

The key engineering challenge: LangExtract returns character offsets in the joined text, but we need to map those back to transcript segment IDs and timestamps.

**File:** `packages/backend/services/tools/entity_extraction.py`

```python
"""Entity extraction tool using LangExtract with segment offset mapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SegmentSpan:
    """Maps a character range to a transcript segment."""
    segment_id: str
    start_time: float
    end_time: float
    char_start: int  # in joined text
    char_end: int    # in joined text


def build_segment_map(segments: list) -> tuple[str, list[SegmentSpan]]:
    """Join segments into continuous text, tracking char-to-segment mapping.

    Returns:
        (joined_text, segment_spans) where each span maps a character range
        to its source segment.
    """
    parts: list[str] = []
    spans: list[SegmentSpan] = []
    offset = 0

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue

        # Add speaker prefix if available
        speaker = getattr(seg, "speaker", None) or ""
        if speaker:
            line = f"[{speaker}] {text}"
        else:
            line = text

        char_start = offset
        char_end = offset + len(line)

        spans.append(SegmentSpan(
            segment_id=seg.id,
            start_time=getattr(seg, "start_time", 0.0) or 0.0,
            end_time=getattr(seg, "end_time", 0.0) or 0.0,
            char_start=char_start,
            char_end=char_end,
        ))

        parts.append(line)
        offset = char_end + 1  # +1 for newline

    joined_text = "\n".join(parts)
    return joined_text, spans


def map_extraction_to_segments(
    char_start: int,
    char_end: int,
    spans: list[SegmentSpan],
) -> list[SegmentSpan]:
    """Find which segments an extraction's character interval overlaps."""
    matched = []
    for span in spans:
        if span.char_end > char_start and span.char_start < char_end:
            matched.append(span)
    return matched
```

---

## Step 4: Tool Registry Integration (Days 5-7)

**File:** `packages/backend/services/tools/entity_extraction.py` (continued)

```python
async def handle_extract_entities(args: dict, ctx: ToolContext) -> ToolResult:
    """Extract structured entities from a transcript using LangExtract."""
    transcript_id = args.get("transcript_id", "")
    template = args.get("template", "meeting")  # medical, legal, meeting, custom
    custom_prompt = args.get("extraction_prompt")
    custom_example = args.get("example")

    from sqlalchemy import select
    from persistence.models import Transcript, Segment

    # Load segments
    result = await ctx.db.execute(
        select(Segment)
        .where(Segment.transcript_id == transcript_id)
        .order_by(Segment.start_time)
    )
    segments = result.scalars().all()
    if not segments:
        return ToolResult(content="No segments found for this transcript.")

    # Build joined text with segment mapping
    joined_text, spans = build_segment_map(segments)

    # Load domain template or use custom prompt
    prompt, example = _get_template(template, custom_prompt, custom_example)

    # Run LangExtract
    import langextract as lx
    extractions = lx.extract(
        text=joined_text,
        prompt=prompt,
        example=example,
        model="granite-8b",  # routes to our custom provider
        max_char_buffer=4000,
        context_window_chars=500,
    )

    # Map extractions to segments
    results = []
    for ext in extractions:
        if ext.char_interval:
            matched_segments = map_extraction_to_segments(
                ext.char_interval[0], ext.char_interval[1], spans
            )
            timestamp = matched_segments[0].start_time if matched_segments else None
            segment_ids = [s.segment_id for s in matched_segments]
        else:
            timestamp = None
            segment_ids = []

        results.append({
            "class": ext.extraction_class,
            "text": ext.extraction_text,
            "attributes": ext.attributes or {},
            "grounded": ext.char_interval is not None,
            "timestamp": timestamp,
            "segment_ids": segment_ids,
        })

    # Format response
    grounded = [r for r in results if r["grounded"]]
    ungrounded = [r for r in results if not r["grounded"]]

    output_lines = [f"Extracted {len(results)} entities ({len(grounded)} grounded, {len(ungrounded)} unverified):\n"]
    for r in results:
        marker = "✓" if r["grounded"] else "?"
        ts = f" at {r['timestamp']:.1f}s" if r["timestamp"] else ""
        attrs = ", ".join(f"{k}={v}" for k, v in r["attributes"].items())
        output_lines.append(f"  [{marker}] {r['class']}: \"{r['text']}\"{ts}")
        if attrs:
            output_lines.append(f"       {attrs}")

    return ToolResult(
        content="\n".join(output_lines),
        artifacts=[Artifact(type="entity_extractions", data={"entities": results})]
    )
```

---

## Step 5: Domain Templates (Days 6-8)

**File:** `packages/backend/services/tools/extraction_templates.py`

```python
"""Few-shot extraction templates for domain-specific entity extraction."""

TEMPLATES = {
    "medical": {
        "prompt": "Extract all medications mentioned, including dosage, route, and frequency.",
        "example": {
            "text": "Patient was started on Metformin 500mg PO twice daily and Lisinopril 10mg daily.",
            "extractions": [
                {
                    "extraction_class": "medication",
                    "extraction_text": "Metformin 500mg PO twice daily",
                    "attributes": {"drug": "Metformin", "dosage": "500mg", "route": "PO", "frequency": "twice daily"},
                },
                {
                    "extraction_class": "medication",
                    "extraction_text": "Lisinopril 10mg daily",
                    "attributes": {"drug": "Lisinopril", "dosage": "10mg", "route": "oral", "frequency": "daily"},
                },
            ],
        },
    },
    "legal": {
        "prompt": "Extract all legal entities: parties, dates, case references, rulings, and objections.",
        "example": {
            "text": "On March 15, 2026, counsel for Smith Corp objected to Exhibit 14. Judge Martinez sustained the objection citing Rule 403.",
            "extractions": [
                {"extraction_class": "party", "extraction_text": "Smith Corp", "attributes": {"role": "defendant"}},
                {"extraction_class": "date", "extraction_text": "March 15, 2026", "attributes": {}},
                {"extraction_class": "ruling", "extraction_text": "Judge Martinez sustained the objection", "attributes": {"judge": "Martinez", "decision": "sustained", "basis": "Rule 403"}},
            ],
        },
    },
    "meeting": {
        "prompt": "Extract action items, decisions, topics discussed, and key participants with their roles.",
        "example": {
            "text": "Sarah agreed to send the Q3 report by Friday. The team decided to postpone the launch to November.",
            "extractions": [
                {"extraction_class": "action_item", "extraction_text": "Sarah agreed to send the Q3 report by Friday", "attributes": {"owner": "Sarah", "task": "send Q3 report", "deadline": "Friday"}},
                {"extraction_class": "decision", "extraction_text": "decided to postpone the launch to November", "attributes": {"topic": "launch", "outcome": "postponed to November"}},
            ],
        },
    },
}
```

---

## Step 6: Frontend — Entity Highlights Panel (Days 8-14)

### New Components

**`packages/frontend/src/components/entities/EntityPanel.tsx`**
- Sidebar panel (similar to Comments/Highlights panels)
- Lists extracted entities grouped by class
- Click entity → scroll transcript to source segment
- Color-coded by entity class (medication=blue, action_item=green, etc.)

**`packages/frontend/src/components/entities/EntityHighlight.tsx`**
- Inline highlight decoration on transcript segments
- Tooltip shows entity details (class, attributes)
- Multiple entities per segment supported

**`packages/frontend/src/components/entities/ExtractionTemplateSelector.tsx`**
- Dropdown: Medical, Legal, Meeting, Custom
- Custom mode: textarea for prompt + example

### API Endpoint

```
POST /api/ai/extract-entities
{
  "transcript_id": "...",
  "template": "medical",
  "custom_prompt": null,
  "custom_example": null
}

Response (SSE stream):
event: extraction_progress
data: {"status": "processing", "chunk": 3, "total_chunks": 12}

event: extraction_complete
data: {"entities": [...], "grounded_count": 15, "total_count": 18}
```

---

## Step 7: Testing (Days 12-16)

### Unit Tests
- `test_segment_map.py`: Verify char-to-segment mapping with various segment lengths
- `test_langextract_provider.py`: Mock llama.cpp, verify prompt construction
- `test_extraction_templates.py`: Validate template format

### Integration Tests
- Run extraction on real transcripts from test database
- Verify entities map to correct segments
- Test with each domain template
- Test with custom prompts

### Quality Validation
- Compare Granite 8B extraction quality vs Gemini (benchmark)
- If Granite struggles with complex schemas, document limitations and recommend model upgrades

---

## Risk Mitigations

| Risk | Mitigation |
|------|------------|
| google-genai phones home | Test at import; vendor core modules if needed |
| Granite 8B too weak for complex extraction | Start with simpler schemas (meeting); recommend larger models for medical/legal |
| Character alignment failures on non-English | Use exact matching first, fuzzy as fallback; document language limitations |
| Long transcripts slow to process | Show progress via SSE; limit to 50K chars initially with "process more" option |
