# Bundled Vocabulary — Redesign

**Status:** Approved (2026-05-07)
**Owner:** TBD
**Date:** 2026-05-07
**Supersedes (parts of):** `2026-05-06-domain-vocabulary-accuracy.md`

## Decisions (2026-05-07)

1. **Ship full ~370 MB bundle out of the box.** Lean/expanded packs become a future paid-tier carve-out; architecture supports it (`ATTACH DATABASE` per pack), but v1 ships everything.
2. **Manual term-add ripped out.** Doc upload is the only user-input path.
3. **Auto-learn from corrections stays.** Trigger is "correcting a transcript," not "managing a list" — editorial cost is zero.
4. **Bundled DB tied to app versions.**
5. **All categories on by default. No category delineation in app UI.** Categories exist for editorial discipline at corpus-build time only.
6. **Expanded category list** beyond original 6: military, slang, entertainment (music/film/TV/games), sports, government/civic, aviation, law enforcement, cooking/food, education, religious/cultural, chemistry/science, real estate/brands, languages, math/stats.

## Why the v0.64.x model is wrong

v0.64.0 / v0.64.1 makes the user the editorial owner of their dictionary. They pick categories, priorities, sounds-like alternates, and run a CSV import. Auto-learn fills gaps but the primary affordance is "you build a list."

Users won't do this. The leaders that solved this — Wispr Flow's persistent context, Otter's conversation memory, Speechmatics' default lexicons — all bundle large vocabularies up front and let the user *narrow* them with project context, not *build them from zero*. We need to flip the model: ship the dictionary, let the user upload context.

## New product model

1. **At install** — Verbatim ships with a curated corpus of ~150k–200k domain terms across general, tech, medical, legal, proper-nouns, and business. Out of the box, transcription benefits from the corpus without the user touching a settings page.
2. **At project create** — User writes a one-paragraph description of what the project is about (already exists). That description drives **context-aware retrieval** — the system pulls the most-relevant 100 terms from the bundled corpus per recording.
3. **Optional power-user path** — User uploads a relevant document (docx/pdf/csv/txt). Backend uses Granite-Tiny to extract acronyms and proper nouns, dedupes against the bundled corpus, adds new terms to a user-additions table.
4. **No category dropdown, no priority slider, no CSV term-import.** The bundled corpus is already categorized. Priority is computed from `(popularity_score, project_relevance, recency)`. The CSV path goes away — the doc-upload path replaces it.

## Architecture: SQLite + sqlite-vec hybrid

A single `vocab.db` per app install, with three logical layers:

### Layer 1 — Bundled corpus (read-only, ships with app)

```
vocab_bundled (
  id INTEGER PK,
  term TEXT NOT NULL,
  category TEXT NOT NULL,        -- general|tech|medical|legal|names|business
  subcategory TEXT,              -- e.g. "k8s", "cardiology", "Latin-legal"
  canonical_form TEXT NOT NULL,  -- the spelling we want in transcripts
  sounds_like TEXT,              -- comma list, optional
  metaphone_primary TEXT,        -- precomputed at corpus build
  metaphone_secondary TEXT,
  context_blurb TEXT,            -- 5-15 word example for embedding
  popularity_score REAL,         -- prior weight from corpus assembly
  source TEXT                    -- attribution: "MeSH|GeoNames|Wikidata|..."
)

vocab_bundled_fts (term, canonical_form, context_blurb)  -- FTS5 BM25
vocab_bundled_vec (term_id, embedding FLOAT[768])         -- sqlite-vec, Nomic
```

### Layer 2 — User additions (read/write)

```
vocab_user (   -- same shape as bundled, plus:
  ...,
  added_at TIMESTAMP,
  source_kind TEXT,         -- "doc-extract|auto-learn|manual"
  source_doc_id TEXT,       -- nullable FK to documents.id
  source_evidence TEXT,     -- the sentence the term was extracted from
  bundled_dedupe_id INTEGER -- if the term already exists in bundled, points there
)
vocab_user_fts (...)
vocab_user_vec (...)
```

`bundled_dedupe_id` lets a user "claim" a bundled term (override casing or `sounds_like`) without duplicating storage. Retrieval UNIONs bundled + user, deduped by `term-or-bundled_dedupe_id`.

### Layer 3 — Project context cache

```
project_context_embedding (
  project_id TEXT PK,
  embedding FLOAT[768],
  context_hash TEXT,        -- hash of inputs that produced this embedding
  last_built_at TIMESTAMP
)
```

Computed by the existing Nomic embedder over: project description (1.0×), project + recording titles (0.8×), AI summaries of recent transcripts in the project (0.6×), audio domain inferred from a 10-second first-pass tiny-Whisper if no description (0.4×), document titles (0.3×). Weights encoded by repetition.

Cache invalidation: project description changes, new doc upload, dictionary edit, recording title change. Otherwise reused across sessions.

## Why SQLite + sqlite-vec over the alternatives

| Option | Why we said no |
|---|---|
| Flat file + hash sets | No BM25 ranking. Project context becomes a linear scan. |
| SQLite + FTS5 only | BM25 is *literal* matching. "kubernetes pod orchestration" doesn't pull `kubelet`, `etcd`, `kube-proxy`. Loses semantic recall. |
| Vector DB only (chromadb / lancedb) | Awkward exact-lookup path; 768-dim embeddings for 200k terms is ~600 MB if loaded; another runtime dependency. |
| Granite-Tiny prompt builder | Wrong layer — that's a generation problem, this is a ranking problem. Slow per session (1-3s). |
| **sqlite-vec hybrid** | **One file, one extension. BM25 for lexical recall + cosine for semantic. ~30-80ms retrieval. ~280MB extra RAM (Nomic) we already pay for semantic search.** |

`sqlite-vec` is the maintained successor to `sqlite-vss`, has a Windows-compatible binary, and at 200k entries doesn't even need HNSW — brute-force cosine is fast enough. ([asg017/sqlite-vec](https://github.com/asg017/sqlite-vec))

## Retrieval at transcription time

```
candidates = (
  TOP 200 by FTS5 BM25(project_text + recent_transcript_text)
    UNION
  TOP 200 by sqlite-vec cosine(project_context_embedding)
    UNION
  ALL user_additions WHERE project_id = :pid OR project_id IS NULL
    UNION
  ALL bundled WHERE category IN :user_enabled_categories
      AND popularity_score > :hot_threshold
)

ranked = candidates ordered by:
  α * normalized_bm25
  + β * cosine_similarity
  + γ * (popularity_score)
  + δ * (1 if user_addition else 0)
  + ε * usage_count

LIMIT 100
```

Initial coefficients: α=0.30, β=0.45, γ=0.10, δ=0.10, ε=0.05. Tunable per project later if useful.

User additions get a hard floor — they always pass the relevance filter. Bundled terms compete on score.

The 100 retrieved terms feed the existing `services/custom_dictionary.py:build_initial_prompt` (token-aware, priority-ordered, natural-prose). That code stays untouched.

## Bundled corpus composition

All categories ship together. Categories serve only as build-time editorial buckets to keep source attribution and quality control disciplined; the app does not show them to the user. Single SQLite database, single retrieval index. All sources permissively licensed (CC0, CC-BY, CC-BY-SA, public-domain US gov work) — full attribution in `THIRD_PARTY_LICENSES.md`.

| Category | Sources | Term count | Plaintext | With embeddings |
|---|---|---|---|---|
| General (negative gate) | SCOWL-60 + top-50k Norvig frequency list | 50k | 5 MB | 15 MB |
| General (homophone seeds) | CMUdict-derived pairs | 5k pairs | 60 KB | 200 KB |
| Tech | awesome-cli + sindresorhus/awesome + StackOverflow top-10k tags | 30k | 3 MB | 9 MB |
| Medical | MeSH preferred terms + RxNorm SAB=RXNORM + ICD-10-CM (CDC) | 80k | 8 MB | 24 MB |
| Legal | CourtListener top-50k cases + Latin legal terms + USCourts glossary | 25k | 2.5 MB | 8 MB |
| Proper nouns | GeoNames cities5000 + Wikidata top-200k notable entities | 100k | 10 MB | 30 MB |
| Business | SEC EDGAR current filers + finance abbrevs + NASA Acronyms (filtered) | 20k | 2 MB | 6 MB |
| **Military** | DoD Dictionary (JP 1-02 successor) + DoD equipment lists + Wikidata military bases + DTA rank tables | 10k | 1.5 MB | 5 MB |
| **Slang** | kaikki.org Wiktionary (slang/AAVE/internet/informal tags) + 2020s slang glossary | 4k | 400 KB | 1.5 MB |
| **Entertainment** (trimmed top tier) | MusicBrainz top 10k artists + Wikidata top 5k films/shows + 2k games | 17k | 5 MB | 15 MB |
| **Sports** | Wikidata pro athletes + teams worldwide + jargon | 60k | 5 MB | 12 MB |
| **Government / civic** | DOJ acronyms + NSA glossary + Wikipedia federal LE | 3k | 300 KB | 1 MB |
| **Aviation** | OurAirports + FAA Form 5010 + airline names + aircraft model #s | 60k | 3 MB | 8 MB |
| **Law enforcement** | Wikipedia ten-codes + APCO + agency acronyms | 1.5k | 150 KB | 500 KB |
| **Cooking / food** | Wikidata food taxonomy + grocer brand names | 11k | 1 MB | 3 MB |
| **Education** | Wikidata universities + IPEDS + degree/test abbrevs | 5.5k | 500 KB | 1.5 MB |
| **Religious / cultural** | Wikipedia denominations + holidays + scripture book names | 3k | 300 KB | 1 MB |
| **Chemistry / science** | PubChem (top 10k) + NIST units + IUPAC | 11k | 1 MB | 3 MB |
| **Real estate / brands** | Wikidata brands by category + RE acronyms | 5k | 500 KB | 1.5 MB |
| **Languages** | SIL ISO 639 (all variants) | 8k | 600 KB | 2 MB |
| **Math / stats** | Wikipedia named theorems + distributions + Greek-spelled-out | 500 | 50 KB | 200 KB |
| **Misrecognition seeds** | Courtside paper pairs + ECCC corpus + CMUdict homophones | 8k pairs | 200 KB | 700 KB |
| **TOTAL** | | **~620k** | **~50 MB** | **~150 MB** |

After Nomic embeddings (768 × 4 bytes per term, 8-bit quantized) + FTS5 indexes + sqlite-vec overhead: **~150 MB on disk** for the always-on bundle. Far under the 600 MB ceiling. Future "Expanded" packs (full MusicBrainz, full PubChem, full ICD-10, full CourtListener) gated behind paid plan when monetization lands — additional ~200-300 MB on demand.

Refresh cadence per category (informs build-script triggers):
- Slang, entertainment, sports: refresh quarterly/annually (fast drift)
- Tech: refresh annually
- Government, military, aviation, chemistry, medical, legal: refresh per major release (slow drift)
- Religious, education, language, math, general: rarely (effectively stable)

**Disk budget mitigation**: ship the 50-80k highest-popularity subset by default (~150 MB). Let the user opt into "Expanded Medical / Expanded Legal / Expanded Tech" packs that download as a one-time fetch — same UX as Whisper model picker.

License attribution consolidated in `THIRD_PARTY_LICENSES.md`. CC-BY (GeoNames, NASA, Norvig) requires attribution; CC-BY-SA (Wikipedia/CourtListener) requires attribution but the share-alike does not propagate to closed-source application code.

## Document-upload extraction pipeline

```
docx/pdf/csv/txt → existing services/document_processor.py text extraction
                → Granite-Tiny:
                   "Extract proper nouns, acronyms, and domain-specific
                    technical terms from this text. Return one per line
                    with a short evidence phrase. No commentary."
                → JSONL: {term, evidence_phrase}

For each extracted term:
  1. Normalize (case, strip)
  2. Exact match against vocab_bundled.term:
       hit → mark vocab_user row with bundled_dedupe_id
  3. Phonetic + edit-distance < 2 against vocab_bundled:
       suggest dedupe; auto-add as user term anyway (preserves user's casing)
  4. Otherwise: insert vocab_user with source_kind='doc-extract'
  5. Embed (term + evidence_phrase) via Nomic, insert vocab_user_vec
  6. Compute Double Metaphone, store on row
```

Granite-Tiny processes a 30-page PDF in ~3-8s as a background job. No impact on transcription latency.

## Cold start, disk, memory

- **First launch ever**: bundled DB mmap'd in ~150 ms (precomputed embeddings, FTS5 indexes, metaphone codes — nothing built at runtime).
- **First retrieval**: needs Nomic embedder loaded (~2s Mac, ~4s cold Windows CPU). Fall back to user-only path until embedder warm.
- **Subsequent launches**: ~100 ms.
- **Runtime memory**: ~280 MB once Nomic loads (we already pay this for semantic search).
- **Disk**: 150 MB lean / up to 600 MB with expanded packs.

## Update strategy

Bundled DB is **append-only between releases**:
- Ship `vocab_bundled_v{N}.db` with each release.
- On launch, if `v{N-1}` is on disk, run `INSERT OR IGNORE INTO vocab_bundled SELECT * FROM new_db.vocab_bundled WHERE id NOT IN (SELECT id FROM old)`. Embeddings + FTS rebuild only for new rows.
- `vocab_user` untouched.
- Stable bundled IDs via hash of `(canonical_form, category)`.

Mid-cycle deltas: ~1-5 MB per category per quarter via the existing auto-update channel. Attached via `ATTACH DATABASE` and merged. Same pattern as Sublime's syntax definitions.

## Privacy

- Project context embedding: local Nomic, already bundled.
- Retrieval: local SQLite query.
- Document extraction: local Granite-Tiny, no network.
- Bundled DB ships with app, no live download required.
- Auto-update is the only network hop, pull-only, never sends transcript data.

Stronger than Wispr Flow (who sync personal dictionary to the cloud) and matches Otter's local-only enterprise tier.

## Migration: what we keep, replace, throw away from v0.64.x

### Keep (verbatim — don't touch)

- `services/custom_dictionary.py:build_initial_prompt` — token-budgeted, priority-ordered, natural-prose. Verified against research, working well.
- `services/vocab_correction.py` — Phase 2 phonetic correction with the 3-gate test. **Swap input source only.**
- `services/llm_vocab_correction.py` — Phase 3 diff-bounded LLM cleanup. **Swap input source only.**
- `services/auto_learn.py` — proper-noun classifier + Descript counter. Output goes to `vocab_user` instead of `custom_dictionary`.
- `services/embedding.py` — Nomic embedder (already used for semantic search).
- `migrations/add_segment_corrections_json.py` — audit trail per correction.
- `sounds_like` UX, `usage_count` field — both transferred to `vocab_user`.

### Replace

- `custom_dictionary` table → renamed to `vocab_user`, plus:
  - Add `metaphone_primary`, `metaphone_secondary`, `context_blurb`, `source_kind`, `source_doc_id`, `source_evidence`, `bundled_dedupe_id` columns
  - Companion `vocab_user_fts` (FTS5) and `vocab_user_vec` (sqlite-vec) tables
  - One-way migration script copies existing rows with `source_kind='manual'`
- `services/custom_dictionary.py:load_dictionary_entries` → `services/vocab_retrieval.py:retrieve_for_project(project_id, recording_id) -> list[CustomDictionaryEntry]`. Old function becomes a thin shim that delegates and is marked deprecated.

### Throw away

- The "user manages priority" UX (Settings → Custom Vocabulary CRUD page). Becomes a thin manual-add escape hatch only, not the primary affordance.
- The CSV bulk-import endpoint and UI button. Replaced by the document-upload-extraction path.
- The category dropdown when adding a term — bundled categories are pre-set; user-added terms auto-classify via Granite-Tiny extraction or default to `general`.
- Per-term priority (Normal/Important/Critical) — folded into `popularity_score` for bundled, hard-floor inclusion for user terms.

## Phase ordering

### Phase A (1-2 weeks) — Corpus + storage layer

1. Build the corpus assembly script (`scripts/build_vocab_corpus.py`):
   - Download each source
   - Filter / dedupe
   - Compute Metaphone codes
   - Compute Nomic embeddings
   - Build SQLite + FTS5 + sqlite-vec
   - Output `assets/vocab_bundled.db` (~150 MB lean)
2. Wire `vocab_bundled.db` into the build (`apps/electron/electron-builder*.yml` extraResources).
3. Migration: at first launch, copy bundled DB into user data dir if newer than installed.
4. Add `sqlite-vec` extension to bundled Python deps (verify Windows wheel).

### Phase B (1 week) — Retrieval service

1. New `services/vocab_retrieval.py`:
   - `retrieve_for_project(project_id, recording_id) -> list[CustomDictionaryEntry]`
   - Hybrid BM25 + cosine query
   - Project-context embedding cache (new table)
   - Cache invalidation hooks
2. Swap `services/jobs.py:handle_transcription` to call `retrieve_for_project` instead of `load_dictionary_entries`.
3. Swap `services/voice_agent.py:WhisperSTTAdapter.with_dictionary` similarly.

### Phase C (1 week) — Document-upload extraction

1. New endpoint: `POST /api/dictionary/extract-from-document` (accepts file, returns extraction job ID).
2. New job handler `services/jobs.py` action: extract text → Granite-Tiny → JSONL → write to `vocab_user`.
3. Frontend: replace CSV-import button with "Add terms from document". Show extraction results in a confirm-before-saving dialog.

### Phase D (1 week) — Cleanup + UX simplification

1. Migrate `custom_dictionary` → `vocab_user` (one-way; existing rows tagged `source_kind='manual'`).
2. Remove the priority slider, category dropdown, CSV-import button, and most of the Settings → Custom Vocabulary page.
3. Replace with a leaner panel: "Bundled vocabulary: 150,000 terms across 6 categories. Add more by uploading a document." + a list of user-added terms with revert.
4. Per-project category enable/disable toggle (medical user disables legal, etc.) so retrieval doesn't over-include irrelevant categories.

### Phase E (later) — Expanded vocabulary packs

Optional opt-in downloads for "Expanded Medical / Legal / Tech" — same UX as Whisper model picker.

## Acceptance criteria

- Out-of-box: a user opens Verbatim for the first time, creates a project with description "Marine Corps administrative meetings," records audio that mentions MCTSSA, ADSEP, Marforpac. Whisper produces them correctly **without the user adding anything to a dictionary**. (Validation: bundled corpus must contain those terms via Wikidata military-units + GeoNames + DoD lists.)
- Document upload: user uploads a Marine Corps OPORD PDF. Within 30 seconds, ~50-200 acronyms and proper nouns appear in `vocab_user`, marked with the source doc. Future transcriptions in the project get the benefit.
- Phase 2 + Phase 3 still work, fed by retrieval instead of full dictionary scan. False-positive rate measurably *better* than v0.64.x because candidate pool is project-relevant.
- Disk: lean install ≤ 200 MB additional. Expanded packs gated behind explicit user fetch.
- Privacy: no transcript or document content leaves the box.

## Risks

1. **Bundled corpus quality.** Garbage-in-garbage-out. Mitigation: editorial-discipline source list; versioned corpus build script; release notes with category counts; manual review of top-1000 entries per category before each release.
2. **Embedding quality on rare terms.** Nomic wasn't trained heavily on `Marforpac`. Mitigation: BM25 leg of retrieval catches lexical hits the embedder misses. Lexical and semantic complement each other.
3. **Project context drift over a long recording.** Project description was about k8s but the recording is about git. Mitigation: blend in audio-derived domain signal from the 10s first-pass.
4. **`sqlite-vec` Windows distribution maturity.** Mitigation: ship prebuilt binary alongside DB; fall back to NumPy brute-force cosine if extension load fails (~3× slower, still <300 ms for 200k vectors).
5. **Dedup near-misses.** "MCTSSA" vs "MCTSA" typed variation. Mitigation: surface "looks similar to bundled" warning in UI, ask if merge or keep both.
6. **Disk size.** Mitigation: ship lean default + opt-in expanded packs.

## Open questions for sign-off

1. **Does the user want to ship at the 150 MB lean default or full ~450-600 MB out of the box?** Lean is faster install + smaller download but means medical/legal users have to opt into the full pack.
2. **Should we keep manual term-add at all?** I lean yes — power-user escape hatch — but it means keeping a CRUD endpoint we'd otherwise rip out. Alternative: remove entirely; users who want a specific term not in the bundled corpus must put it in a doc and upload.
3. **Is auto-learn from manual transcript edits still in scope?** It conflicts slightly with the "user shouldn't be editorial" thesis — but the trigger is *correcting a transcript*, not adding to a dictionary, so the editorial cost is zero. I lean yes, keep.
4. **Versioning of bundled DB.** Do we tie corpus versions to app versions, or release them separately? Tying-to-app is simpler. Separate cadence lets us refresh medical/legal terms without shipping a full app update.
5. **Categories per project — opt-in or opt-out?** I lean: all on by default, user can disable categories that produce false positives in their domain.

## What's NOT being changed

- Phase 1 prompt builder (`build_initial_prompt`) — works correctly, no reason to touch.
- Phase 2 phonetic correction — works correctly, just gets a better candidate set.
- Phase 3 LLM cleanup — works correctly, same.
- Phase 4 corrections audit + per-correction undo + re-correct + auto-learn — all keep working with `vocab_user` as the backing table.
- Existing voice chat / live transcription — STT still pulls from `retrieve_for_project`.

The user-facing change is dramatic; the engine-level change is surgical.

---

## References

Research underpinning this plan:
- [OpenAI Whisper Prompting Guide](https://developers.openai.com/cookbook/examples/whisper_prompting_guide) — 224-token budget
- [arXiv 2410.18363](https://arxiv.org/html/2410.18363v1) — end-of-prompt attention
- [arXiv 2305.10222](https://arxiv.org/abs/2305.10222) — retrieval-augmented ASR
- [arXiv 2306.16007](https://arxiv.org/html/2306.16007v1) — TCPGen contextual biasing
- [Whisper: Courtside Edition (arXiv 2602.18966)](https://arxiv.org/abs/2602.18966) — 17% WER reduction with retrieval+LLM
- [`sqlite-vec`, Alex Garcia](https://github.com/asg017/sqlite-vec)
- [SCOWL](https://wordlist.aspell.net/), [CMUdict](https://github.com/cmusphinx/cmudict), [MeSH](https://www.nlm.nih.gov/databases/download/mesh.html), [RxNorm](https://www.nlm.nih.gov/research/umls/rxnorm/index.html), [ICD-10-CM](https://www.cdc.gov/nchs/icd/icd-10-cm/files.html), [GeoNames](https://www.geonames.org/export/), [Wikidata](https://www.wikidata.org/wiki/Wikidata:Database_download), [CourtListener](https://www.courtlistener.com/help/api/bulk-data/), [SEC EDGAR](https://www.sec.gov/files/company_tickers.json), [NASA Acronyms](https://github.com/nasa/NASA-Acronyms)
