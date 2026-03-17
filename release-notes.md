## What's New in v0.54.0

### ✨ New Features
- **Granite 4.0 H-Tiny** — Upgraded default AI model to IBM's hybrid Mamba-2 architecture (7B total, 1B active). Lower memory usage and faster inference, especially on long contexts

### 🐛 Bug Fixes
- **Chat crash on second message** — Fixed AI chat crashing with "llama_decode returned -1" after the first reply due to conversation history overflowing the context window
- **Legacy model styling** — Legacy models now display with consistent gray styling and deprecation notes in both OCR and AI settings sections

### 🔧 Improvements
- Added automatic conversation history trimming to keep chat within the model's context budget
- Legacy models now appear at the top of their respective sections in Settings for visibility
- Granite 3.3 8B moved to legacy tier with migration note recommending Granite 4.0 H-Tiny
