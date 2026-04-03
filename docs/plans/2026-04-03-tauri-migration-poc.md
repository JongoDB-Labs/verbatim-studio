# Tauri 2 Migration POC — Implementation Plan

**Issue:** #132
**Timeline:** Q3–Q4 2026 (after server deployment and mobile are underway)
**Goal:** Prove that Verbatim Studio's Electron app can migrate to Tauri 2 with the existing React frontend and Python backend as a sidecar, achieving smaller bundles, lower memory, and easier App Store compliance.

---

## Why Tauri

| Metric | Electron (current) | Tauri 2 (target) |
|--------|-------------------|-------------------|
| Idle RAM | 200-300 MB | 30-40 MB |
| Startup time | 1-2 sec | <0.5 sec |
| App bundle (no models) | ~100 MB | <10 MB |
| Runtime | Bundled Chromium | OS native WebView |
| App Store sandbox | Complex (Python subprocess) | Easier (native WebView) |
| Backend integration | child_process.spawn | Sidecar (first-class) |

---

## Migration Scope

### What ports directly (zero changes)
- `packages/frontend/` — entire React/Vite/Tailwind codebase
- All npm dependencies (Zustand, TanStack Query, WaveSurfer, etc.)
- CSS, assets, fonts

### What needs rewriting (~20 IPC methods)

**Current Electron main process files** (`apps/electron/src/main/`):

| File | Purpose | Tauri Equivalent |
|------|---------|------------------|
| `index.ts` | Window creation, app lifecycle | `tauri.conf.json` + Rust `main.rs` |
| `backend.ts` | Python subprocess spawn | Tauri sidecar in `tauri.conf.json` |
| `ipc-handlers.ts` | IPC bridge (20 methods) | Tauri commands (Rust `#[tauri::command]`) |
| `updater.ts` | Auto-update via electron-updater | `@tauri-apps/plugin-updater` |
| `deep-link.ts` | verbatim:// protocol handler | Tauri deep-link plugin |
| `splash.ts` | Splash screen during startup | Tauri splashscreen plugin |
| `store.ts` | Persistent settings | `@tauri-apps/plugin-store` |

### IPC Methods to Port

```
Window controls:     minimize, maximize, close, setTitle
File dialogs:        openFileDialog, openDirectoryDialog, showSaveDialog
System:              getAppVersion, getResourcePath, getPlatform
Backend:             startBackend, stopBackend, getBackendStatus
Updates:             checkForUpdates, downloadUpdate, installUpdate
Deep links:          onDeepLink, handleDeepLink
Screenshots:         captureScreenshot (for bug reports)
Store:               getSetting, setSetting
```

---

## POC Steps

### Step 1: Scaffold Tauri project (Day 1)

```bash
# Create new Tauri app alongside existing Electron
mkdir apps/tauri
cd apps/tauri
npm create tauri-app@latest -- --template vanilla-ts
```

**`tauri.conf.json`:**
```json
{
  "productName": "Verbatim Studio",
  "identifier": "com.verbatimstudio.app",
  "build": {
    "frontendDist": "../../packages/frontend/dist",
    "devUrl": "http://localhost:5173",
    "beforeBuildCommand": "cd ../../packages/frontend && npm run build"
  },
  "app": {
    "windows": [{
      "title": "Verbatim Studio",
      "width": 1200,
      "height": 800,
      "minWidth": 800,
      "minHeight": 600
    }],
    "security": {
      "csp": "default-src 'self'; connect-src 'self' http://127.0.0.1:52780 ws://127.0.0.1:52780"
    }
  },
  "bundle": {
    "active": true,
    "targets": ["dmg", "nsis"],
    "externalBin": ["python-backend"],
    "resources": ["models/*", "ffmpeg/*"]
  }
}
```

### Step 2: Python backend as sidecar (Days 2-3)

**PyInstaller compilation:**
```bash
cd packages/backend
pip install pyinstaller
pyinstaller --onedir --name python-backend \
  --hidden-import services \
  --hidden-import persistence \
  --add-data "services:services" \
  server.py
```

**Tauri sidecar config** in `tauri.conf.json`:
```json
"bundle": {
  "externalBin": ["binaries/python-backend"]
}
```

**Rust sidecar management** (`src-tauri/src/backend.rs`):
```rust
use tauri::api::process::{Command, CommandChild};
use std::sync::Mutex;

pub struct BackendState {
    child: Mutex<Option<CommandChild>>,
}

#[tauri::command]
pub async fn start_backend(state: tauri::State<'_, BackendState>) -> Result<(), String> {
    let (mut rx, child) = Command::new_sidecar("python-backend")
        .expect("failed to create sidecar command")
        .args(["--port", "52780", "--host", "127.0.0.1"])
        .spawn()
        .map_err(|e| e.to_string())?;

    *state.child.lock().unwrap() = Some(child);

    // Wait for backend to be ready
    tokio::spawn(async move {
        while let Some(event) = rx.recv().await {
            // Log backend output
            if let tauri::api::process::CommandEvent::Stdout(line) = event {
                if line.contains("Application startup complete") {
                    break;
                }
            }
        }
    });

    Ok(())
}
```

### Step 3: Port IPC methods (Days 3-5)

**Rust commands** (`src-tauri/src/commands.rs`):
```rust
use tauri_plugin_dialog::DialogExt;

#[tauri::command]
async fn open_file_dialog(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let file = app.dialog()
        .file()
        .add_filter("Audio", &["mp3", "wav", "m4a", "flac", "ogg", "wma"])
        .blocking_pick_file();

    Ok(file.map(|f| f.path.to_string_lossy().to_string()))
}

#[tauri::command]
fn get_app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[tauri::command]
async fn minimize_window(window: tauri::Window) -> Result<(), String> {
    window.minimize().map_err(|e| e.to_string())
}

#[tauri::command]
async fn maximize_window(window: tauri::Window) -> Result<(), String> {
    if window.is_maximized().unwrap_or(false) {
        window.unmaximize().map_err(|e| e.to_string())
    } else {
        window.maximize().map_err(|e| e.to_string())
    }
}
```

### Step 4: Frontend bridge adaptation (Days 4-6)

**File:** `packages/frontend/src/lib/tauri-bridge.ts`

```typescript
import { invoke } from '@tauri-apps/api/core';

// Drop-in replacement for window.electronAPI
export const tauriAPI = {
  minimize: () => invoke('minimize_window'),
  maximize: () => invoke('maximize_window'),
  close: () => invoke('close_window'),
  openFileDialog: (opts: any) => invoke('open_file_dialog', opts),
  openDirectoryDialog: () => invoke('open_directory_dialog'),
  getAppVersion: () => invoke('get_app_version'),
  startBackend: () => invoke('start_backend'),
  checkForUpdates: () => invoke('check_for_updates'),
  getSetting: (key: string) => invoke('get_setting', { key }),
  setSetting: (key: string, value: any) => invoke('set_setting', { key, value }),
};
```

**Platform detection** (update `packages/frontend/src/lib/platform.ts`):
```typescript
export function getAPI() {
  if (window.__TAURI__) return tauriAPI;
  if (window.electronAPI) return window.electronAPI;
  return webFallbackAPI;
}
```

### Step 5: Auto-updater (Day 6)

```bash
cd apps/tauri
cargo add tauri-plugin-updater
```

```json
// tauri.conf.json
"plugins": {
  "updater": {
    "endpoints": ["https://verbatim.studio/updates/tauri/{{target}}/{{arch}}/{{current_version}}"],
    "pubkey": "YOUR_PUBLIC_KEY"
  }
}
```

### Step 6: Benchmark comparison (Day 7)

Run side-by-side:

| Test | Electron | Tauri |
|------|----------|-------|
| Cold startup to UI ready | Measure | Measure |
| Idle RAM (Activity Monitor) | Measure | Measure |
| App bundle size (no models) | Measure | Measure |
| DMG download size | Measure | Measure |
| IPC latency (round-trip) | Measure | Measure |
| Python backend startup | Measure | Measure |
| Audio playback smoothness | Test | Test |
| WaveSurfer rendering | Test | Test |

---

## Decision Criteria

**Ship Tauri if:**
- Startup <0.5s ✓
- Idle RAM <50MB ✓
- All 20 IPC methods working ✓
- PyInstaller sidecar stable on macOS + Windows ✓
- WaveSurfer.js renders correctly in native WebView ✓
- Auto-updater works ✓
- No WebView rendering bugs that affect core UX

**Stay on Electron if:**
- WebView inconsistencies break transcript editor
- PyInstaller sidecar adds more complexity than it saves
- Rust maintenance burden exceeds Electron's overhead
- Bundle size savings are negated by PyInstaller binary size

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| WebView rendering inconsistencies | High | Test WaveSurfer, PDF viewer, markdown rendering early |
| PyInstaller binary size (Python + deps) | Medium | May negate bundle size savings; measure before committing |
| Rust learning curve | Medium | Most commands are thin wrappers; only backend.rs is complex |
| macOS WebView (WebKit) vs Windows (WebView2) differences | Medium | Test both platforms early in POC |
| Code signing for sidecar binaries | Low | Same challenge as Electron; doesn't get worse |
