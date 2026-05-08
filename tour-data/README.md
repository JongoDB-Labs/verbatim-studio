# Tour Demo Data

Source files for the optional sample-workspace install in Verbatim's
onboarding tour. Everything here is **packaged into `tour-demo.zip`**
by `scripts/build_tour_demo.py` and uploaded as a release artifact on
each tagged release.

## Why opt-in download

Bundling these files in every Verbatim installer would bloat the
download permanently. Generating them on the fly with a local LLM
would produce visibly synthetic content. Instead the user gets a
short prompt at the start of the tour:

> *"Want a sample workspace to follow along? (~10 MB)"*

If yes, the backend pulls `tour-demo.zip` from the public releases
repo, extracts into a sandboxed project, and tags every row with
`metadata.is_demo = true` so cleanup is one-click.

## Directory layout

```
tour-data/
├── manifest.json         # describes the workspace (projects, recordings, docs, notes, chats)
├── files/                # actual binary content
│   ├── audio/
│   ├── documents/
│   └── images/
└── README.md             # this file (NOT bundled)
```

## Sourcing rules

Every file in `files/` MUST be one of:

- **Public domain** (US government works, expired copyright, explicit dedications)
- **Creative Commons** with redistribution allowed (CC0, CC BY, CC BY-SA)
- **Permissive** (Unsplash license, Pexels license, MIT, etc.)

No AI-generated content. No copyrighted material. Each file's source
+ license is documented in `manifest.json` under `attribution` so
attribution carries through to the packaged release.

## Current sources

| File | Type | Source | License |
|---|---|---|---|
| `audio/apollo11-descent.mp3` | audio | NASA Apollo 11 mission tape | Public Domain (US Gov) |
| `documents/nist-cybersecurity-overview.pdf` | PDF | NIST publication | Public Domain (US Gov) |
| `documents/q4-roadmap-brief.docx` | DOCX | Original; mock content | CC0 (created for Verbatim) |
| `documents/security-presentation.pptx` | PPTX | DHS public slide deck | Public Domain (US Gov) |
| `images/whiteboard-photo.jpg` | JPG | Unsplash | Unsplash License |

## Build + upload

```sh
# Local: build the zip
python scripts/build_tour_demo.py

# CI: uploads to verbatim-studio-releases on tag push (see
# .github/workflows/build-vocab-corpus.yml — same release pipeline)
```

## Versioning

`schema_version` in `manifest.json` is incremented when the install
shape changes (new entity types, schema changes). The backend
reads the version and refuses to install if it doesn't recognize it,
so corrupt or mismatched manifests fail loudly rather than partially
seeding the user's database.
