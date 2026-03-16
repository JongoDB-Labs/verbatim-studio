## What's New in v0.53.2

### 🐛 Bug Fixes
- Fixed crash when uploading multiple recordings simultaneously (GPU memory exhaustion from concurrent model loading)
- Fixed multi-file drag-and-drop on recordings page only uploading the first file

### 🔧 Improvements
- Added GPU serialization lock to prevent concurrent transcription/diarization jobs from exhausting memory
- Added class-level model caching for WhisperX and diarization pipelines so concurrent jobs share loaded models
- Upload dropzone now supports multi-file drag-and-drop and multi-file selection
