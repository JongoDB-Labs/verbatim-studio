# Mobile App — Implementation Plan

**Issue:** #131
**Timeline:** June–August 2026
**Goal:** Create iOS/Android mobile apps using Capacitor wrapping the existing React frontend, with secure tunnel to desktop backend and optional on-device ML.

---

## Architecture

```
┌────────────────────────────┐
│     Capacitor Mobile App   │
│                            │
│  ┌──────────────────────┐  │
│  │  React Frontend      │  │
│  │  (packages/frontend) │  │
│  │                      │  │
│  │  - Audio recording   │  │
│  │  - Transcript viewer │  │
│  │  - Search            │  │
│  │  - AI chat           │  │
│  │  - Project browser   │  │
│  └──────────┬───────────┘  │
│             │              │
│  ┌──────────▼───────────┐  │
│  │  API Client          │  │
│  │  (configurable URL)  │  │
│  │                      │  │
│  │  localhost:52780     │  │  ← Desktop mode
│  │  vpn-ip:52780       │  │  ← Tunnel mode
│  │  api.verbatim.pro   │  │  ← Cloud mode
│  └──────────────────────┘  │
│                            │
│  ┌──────────────────────┐  │
│  │  Native Plugins      │  │
│  │  - Audio recording   │  │
│  │  - File access       │  │
│  │  - Whisper.cpp (P2)  │  │
│  └──────────────────────┘  │
└────────────────────────────┘
         │
         │ HTTPS / WSS
         ▼
┌────────────────────────────┐
│  Backend Server            │
│  (Desktop or Cloud)        │
│  - Transcription           │
│  - LLM chat                │
│  - Search                  │
│  - Export                  │
└────────────────────────────┘
```

---

## Phase 1: Capacitor Setup (Weeks 1-3)

### Step 1: Add Capacitor to frontend

```bash
cd packages/frontend
npm install @capacitor/core @capacitor/cli
npx cap init "Verbatim Studio" "com.verbatimstudio.mobile"
npm install @capacitor/ios @capacitor/android
npx cap add ios
npx cap add android
```

### Step 2: Configure Vite for mobile

**`packages/frontend/vite.config.ts`** additions:
```typescript
export default defineConfig({
  // ... existing config
  server: {
    // Allow mobile device to connect during dev
    host: '0.0.0.0',
  },
  define: {
    // Platform detection
    __CAPACITOR__: JSON.stringify(!!process.env.CAPACITOR),
  },
});
```

### Step 3: Platform abstraction layer

Replace `window.electronAPI` calls with a platform-agnostic abstraction.

**File:** `packages/frontend/src/lib/platform.ts`

```typescript
import { Capacitor } from '@capacitor/core';

export type Platform = 'electron' | 'capacitor' | 'web';

export function getPlatform(): Platform {
  if (typeof window !== 'undefined' && window.electronAPI) return 'electron';
  if (Capacitor.isNativePlatform()) return 'capacitor';
  return 'web';
}

export const isDesktop = () => getPlatform() === 'electron';
export const isMobile = () => getPlatform() === 'capacitor';
export const isWeb = () => getPlatform() === 'web';
```

### Step 4: API client — configurable backend URL

**Modify:** `packages/frontend/src/lib/api.ts`

```typescript
// Current: hardcoded to localhost
// New: configurable, stored in localStorage/Capacitor preferences

import { Preferences } from '@capacitor/preferences';

async function getBackendUrl(): Promise<string> {
  // Electron: always localhost
  if (isDesktop()) return 'http://127.0.0.1:52780';

  // Mobile: user-configured
  const { value } = await Preferences.get({ key: 'backendUrl' });
  return value || 'http://127.0.0.1:52780';
}
```

### Step 5: Replace Electron-specific APIs

| Electron API | Capacitor Equivalent | Package |
|---|---|---|
| `electronAPI.minimize/maximize/close` | N/A (mobile has no window controls) | — |
| `electronAPI.openFileDialog` | `FilePicker.pickFiles()` | `@capawesome/capacitor-file-picker` |
| `electronAPI.openDirectoryDialog` | N/A (use file picker) | — |
| `electronAPI.captureScreenshot` | N/A (not needed on mobile) | — |
| `electronAPI.checkForUpdates` | App Store handles this | — |
| Audio recording | `VoiceRecorder` plugin | `capacitor-voice-recorder` |
| Persistent storage | `Preferences` / `Filesystem` | `@capacitor/preferences`, `@capacitor/filesystem` |

**Pattern:** Wrap each in the platform abstraction:
```typescript
export async function pickAudioFile(): Promise<File | null> {
  if (isDesktop()) {
    return window.electronAPI.openFileDialog({ filters: [{ name: 'Audio', extensions: ['mp3', 'wav', 'm4a'] }] });
  }
  if (isMobile()) {
    const result = await FilePicker.pickFiles({ types: ['audio/*'] });
    return result.files[0] || null;
  }
  // Web: standard file input
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'audio/*';
    input.onchange = () => resolve(input.files?.[0] || null);
    input.click();
  });
}
```

### Step 6: Mobile-specific UI adjustments

- Hide window control buttons on mobile
- Use bottom tab navigation instead of sidebar
- Full-screen transcript viewer with swipe gestures
- Simplified recording interface for mobile microphone
- Touch-friendly segment selection and playback controls

---

## Phase 2: Secure Tunnel Configuration (Weeks 3-4)

### Server Connection UI

**New settings page:** "Connect to Server"

```
┌─────────────────────────────────┐
│  Connect to Verbatim Server     │
│                                 │
│  Connection Type:               │
│  ○ Same Network (LAN)           │
│  ○ Tailscale VPN                │
│  ○ Cloudflare Tunnel            │
│  ○ Custom URL                   │
│                                 │
│  Server URL: [192.168.1.50:52780]│
│                                 │
│  [Test Connection]  ✓ Connected │
│                                 │
│  Server Info:                   │
│  Version: 0.57.1                │
│  Models: Whisper large-v3, ...  │
│  Available: 3/10 transcription  │
│             slots               │
└─────────────────────────────────┘
```

### QR Code Pairing (nice-to-have)

Desktop app shows a QR code containing:
```json
{
  "url": "http://100.64.0.5:52780",  // Tailscale IP
  "token": "jwt-auth-token",
  "version": "0.57.1"
}
```

Mobile app scans QR → auto-configures connection.

### Health check endpoint

```
GET /api/system/mobile-handshake
Response: {
  "version": "0.57.1",
  "models": ["whisper-large-v3", "granite-8b"],
  "capabilities": ["transcribe", "chat", "search", "export"],
  "transcription_slots": { "available": 3, "total": 10 },
  "auth_required": false
}
```

---

## Phase 3: Audio Recording on Mobile (Weeks 4-5)

### Capacitor audio recording plugin

```bash
npm install capacitor-voice-recorder
```

```typescript
import { VoiceRecorder } from 'capacitor-voice-recorder';

export class MobileRecorder {
  async startRecording() {
    const permission = await VoiceRecorder.requestAudioRecordingPermission();
    if (!permission.value) throw new Error('Microphone permission denied');
    await VoiceRecorder.startRecording();
  }

  async stopRecording(): Promise<Blob> {
    const result = await VoiceRecorder.stopRecording();
    // Convert base64 to Blob
    const response = await fetch(`data:${result.value.mimeType};base64,${result.value.recordDataBase64}`);
    return response.blob();
  }

  async uploadForTranscription(audioBlob: Blob, backendUrl: string) {
    const formData = new FormData();
    formData.append('file', audioBlob, 'recording.m4a');

    const response = await fetch(`${backendUrl}/api/recordings/upload`, {
      method: 'POST',
      body: formData,
    });
    return response.json();
  }
}
```

### Streaming transcription (WebSocket)

For real-time mobile recording → desktop transcription:

```typescript
const ws = new WebSocket(`wss://${backendUrl}/api/live/stream`);

// Send audio chunks from microphone
mediaRecorder.ondataavailable = (event) => {
  if (event.data.size > 0) {
    ws.send(event.data);
  }
};

// Receive partial transcriptions
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'partial') {
    updatePartialTranscript(data.text);
  } else if (data.type === 'final') {
    appendFinalSegment(data.segment);
  }
};
```

---

## Phase 4: App Store Submission (Weeks 5-6)

### iOS (App Store)

1. Open `ios/App/App.xcworkspace` in Xcode
2. Configure signing with Apple Developer team
3. Add required Info.plist keys:
   - `NSMicrophoneUsageDescription` — "Record audio for transcription"
   - `NSLocalNetworkUsageDescription` — "Connect to your Verbatim server"
4. Set minimum iOS version: 16.0
5. Create App Store Connect listing
6. Archive → Upload → Submit for review

### Android (Google Play)

1. Configure signing key in `android/app/build.gradle`
2. Add permissions to `AndroidManifest.xml`:
   - `RECORD_AUDIO`
   - `INTERNET`
   - `ACCESS_NETWORK_STATE`
3. Build release APK/AAB: `cd android && ./gradlew bundleRelease`
4. Create Google Play Console listing
5. Upload AAB → Submit for review

### App Store Review Notes
- "This app connects to a self-hosted server for AI transcription processing"
- "No user data is transmitted to third-party cloud services"
- "Audio recording is used exclusively for speech-to-text transcription"

---

## Phase 5: On-Device ML (Stretch — Months 3-6)

### whisper.cpp native plugin

**Capacitor plugin:** `packages/mobile-plugins/capacitor-whisper/`

```typescript
// TypeScript interface
export interface WhisperPlugin {
  loadModel(options: { model: 'tiny' | 'base' | 'small' }): Promise<void>;
  transcribe(options: { audioPath: string }): Promise<{ text: string; segments: Segment[] }>;
  isModelLoaded(): Promise<{ loaded: boolean }>;
}
```

**Swift implementation (iOS):**
```swift
@objc func transcribe(_ call: CAPPluginCall) {
    let audioPath = call.getString("audioPath") ?? ""
    // Use whisper.cpp C API via bridging header
    let ctx = whisper_init_from_file(modelPath)
    // ... transcribe and return segments
}
```

### Model sizes for mobile bundling

| Model | Download Size | Memory at Runtime | Ship With App? |
|-------|--------------|-------------------|----------------|
| tiny-en | 39MB | ~30MB | Yes (embedded) |
| base | 74MB | ~180MB | Optional download |
| small | 244MB | ~500MB | Optional download |

**Strategy:** Ship tiny-en embedded for instant offline use. Offer base/small as optional downloads in Settings.

### Hybrid decision logic

```typescript
async function transcribe(audioFile: File): Promise<TranscriptResult> {
  const onDeviceAvailable = await WhisperPlugin.isModelLoaded();
  const serverAvailable = await checkServerConnection();

  if (serverAvailable) {
    // Always prefer server for quality
    return serverTranscribe(audioFile);
  } else if (onDeviceAvailable) {
    // Fallback to on-device
    return localTranscribe(audioFile);
  } else {
    throw new Error('No transcription engine available. Connect to a server or download a local model.');
  }
}
```

---

## Testing Plan

1. **Capacitor build:** Verify React frontend builds and runs in iOS Simulator + Android Emulator
2. **API connectivity:** Test against desktop backend on same network
3. **Tunnel connectivity:** Test with Tailscale on real devices
4. **Audio recording:** Record → upload → transcribe → view result on mobile
5. **Offline behavior:** Verify graceful degradation when server unavailable
6. **Performance:** Measure UI responsiveness, audio upload latency, WebSocket streaming quality
