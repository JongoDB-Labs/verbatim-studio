# User Migration & App Store Distribution — Implementation Plan

**Issues:** #125, #126, #127, #128, #133
**Timeline:** April–June 2026
**Goal:** Restore update path for stranded users, then achieve legitimate App Store distribution on Windows and macOS.

---

## Phase 0: Emergency — PAT Auth for Private Repo (#126)

**Timeline:** 1-3 days | **Effort:** Small

This is the fastest possible fix. Users paste a GitHub PAT into Settings and auto-updates work again.

### Backend (Electron main process)

**File:** `apps/electron/src/main/updater.ts` (or wherever autoUpdater is configured)

```typescript
// Read token from electron-store
const store = new Store();
const githubToken = store.get('githubToken', '');

// Configure autoUpdater with token
autoUpdater.setFeedURL({
  provider: 'github',
  owner: 'JongoDB',
  repo: 'verbatim-studio',
  private: true,
  token: githubToken || undefined,
});
```

**IPC handler** (new):
```typescript
ipcMain.handle('set-github-token', async (_event, token: string) => {
  store.set('githubToken', token);
  // Reconfigure autoUpdater with new token
  autoUpdater.setFeedURL({
    provider: 'github',
    owner: 'JongoDB',
    repo: 'verbatim-studio',
    private: true,
    token: token || undefined,
  });
  return { success: true };
});

ipcMain.handle('test-github-token', async (_event, token: string) => {
  try {
    const res = await fetch('https://api.github.com/repos/JongoDB/verbatim-studio/releases/latest', {
      headers: { Authorization: `token ${token}` },
    });
    return { valid: res.ok, status: res.status };
  } catch (e) {
    return { valid: false, error: e.message };
  }
});
```

### Frontend (Settings UI)

**Location:** Settings page, near the existing update check section.

- Password-masked input field for PAT
- "Test Connection" button → calls `test-github-token` IPC
- "Save" button → calls `set-github-token` IPC
- Help text: "Generate a token at github.com/settings/tokens with `repo` scope"
- Green/red status indicator after test

### Testing
1. Build the app without a token → verify update check fails gracefully
2. Add a valid PAT → verify update check succeeds and shows available update
3. Add an invalid PAT → verify clear error message
4. Remove the PAT → verify app still works (just can't update)

---

## Phase 1: Proper Distribution Infrastructure (#125)

**Timeline:** 1-2 weeks | **Effort:** Medium

### Option A: Public Releases Repo (Recommended)

Create `JongoDB/verbatim-studio-releases` (public) that only contains release assets.

1. Create the repo: `gh repo create JongoDB/verbatim-studio-releases --public`
2. Modify `build-electron.yml` to publish releases to both repos
3. Update `electron-builder` config to check the public repo for updates:
   ```json
   "publish": {
     "provider": "github",
     "owner": "JongoDB",
     "repo": "verbatim-studio-releases"
   }
   ```
4. Push a "bridge" release to the old (now private) repo with release notes directing users to the new location

### Option B: Self-Hosted Update Server

Host update manifests on verbatim.studio:
- `https://verbatim.studio/updates/latest-mac.yml`
- `https://verbatim.studio/updates/latest.yml`

electron-builder supports `generic` provider:
```json
"publish": {
  "provider": "generic",
  "url": "https://verbatim.studio/updates"
}
```

Assets served from CDN (Cloudflare R2, S3, etc.).

### Recommendation
**Option A** is simpler — GitHub handles CDN, download counting, and release management. The PAT workaround (#126) handles the interim.

---

## Phase 2: Microsoft Store (#127)

**Timeline:** 2-4 weeks | **Effort:** Medium

### Step 1: Local MSIX Packaging

```bash
# Install winapp CLI
npm install -g @microsoft/winapp-cli

# Test identity injection (for local testing)
winapp node add-electron-debug-identity

# Build your existing app
cd apps/electron && pnpm build

# Create MSIX package
winapp pack --input dist/win-unpacked --output dist/verbatim-studio.msix
```

### Step 2: Partner Center Setup

1. Sign in to [Partner Center](https://partner.microsoft.com/dashboard)
2. Create app reservation: "Verbatim Studio"
3. Configure:
   - App identity (Name, Publisher)
   - Age rating (AI-generated content → likely 12+)
   - Category: Productivity > Office
   - Pricing: Free (with in-app purchase for Pro features later)
4. Generate package manifest values

### Step 3: GitHub Actions Integration

Add to `build-electron.yml` (Windows job):

```yaml
- name: Create MSIX package
  if: startsWith(github.ref, 'refs/tags/v')
  run: |
    npm install -g @microsoft/winapp-cli
    winapp pack --input apps/electron/dist/win-unpacked --output apps/electron/dist/verbatim-studio.msix

- name: Upload MSIX artifact
  uses: actions/upload-artifact@v4
  with:
    name: verbatim-studio-msix
    path: apps/electron/dist/verbatim-studio.msix
```

### Step 4: Store Submission

```bash
winapp store publish ./verbatim-studio.msix --appId <partner-center-app-id>
```

### Step 5: Dual Distribution

Keep NSIS installer for users who prefer direct download. Store version gets auto-updates via Microsoft Store; direct version keeps electron-updater.

---

## Phase 3: Notarized macOS (#128)

**Timeline:** 2-3 weeks (parallel with Phase 2) | **Effort:** Medium-High

### Step 1: Certificate Setup

1. Apple Developer account → Certificates, Identifiers & Profiles
2. Create: **Developer ID Application** certificate
3. Create: **Developer ID Installer** certificate (for pkg if needed)
4. Download and install in Keychain Access
5. Export as .p12 for CI: `security export -t identities -f pkcs12`

### Step 2: Sign All Bundled Binaries

The critical challenge: every `.so`, `.dylib`, and executable in the bundled Python must be signed.

**Script for CI** (`scripts/sign-python-binaries.sh`):
```bash
#!/bin/bash
IDENTITY="Developer ID Application: Your Name (TEAM_ID)"
PYTHON_DIR="$1"

# Sign all shared libraries
find "$PYTHON_DIR" -name "*.so" -o -name "*.dylib" | while read lib; do
  codesign --force --options runtime --sign "$IDENTITY" "$lib"
done

# Sign Python executable
codesign --force --options runtime --sign "$IDENTITY" "$PYTHON_DIR/bin/python3"
```

### Step 3: electron-builder Configuration

```json
"mac": {
  "target": [{ "target": "dmg", "arch": ["arm64"] }],
  "identity": "Developer ID Application: Your Name (TEAM_ID)",
  "hardenedRuntime": true,
  "gatekeeperAssess": false,
  "entitlements": "entitlements.mac.plist",
  "entitlementsInherit": "entitlements.mac.plist",
  "notarize": {
    "teamId": "YOUR_TEAM_ID"
  }
}
```

### Step 4: Entitlements

**`entitlements.mac.plist`:**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.network.client</key><true/>
  <key>com.apple.security.network.server</key><true/>
  <key>com.apple.security.device.audio-input</key><true/>
  <key>com.apple.security.files.user-selected.read-write</key><true/>
</dict>
</plist>
```

### Step 5: GitHub Actions Notarization

```yaml
- name: Sign Python binaries
  run: bash scripts/sign-python-binaries.sh "$PYTHON_RESOURCE_DIR"
  env:
    CSC_LINK: ${{ secrets.MAC_CERT_P12 }}
    CSC_KEY_PASSWORD: ${{ secrets.MAC_CERT_PASSWORD }}

- name: Build and notarize
  run: pnpm electron-builder --mac --arm64
  env:
    CSC_LINK: ${{ secrets.MAC_CERT_P12 }}
    CSC_KEY_PASSWORD: ${{ secrets.MAC_CERT_PASSWORD }}
    APPLE_ID: ${{ secrets.APPLE_ID }}
    APPLE_APP_SPECIFIC_PASSWORD: ${{ secrets.APPLE_APP_PASSWORD }}
    APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
```

### Step 6: Verification

```bash
# Check notarization
xcrun stapler validate "Verbatim Studio.dmg"

# Check signature
codesign --verify --deep --strict "Verbatim Studio.app"

# Simulate Gatekeeper
spctl --assess --verbose "Verbatim Studio.app"
```

---

## Phase 4: Mac App Store (#133) — Long-term

**Timeline:** 6-12 months | **Effort:** High

Deferred until notarized distribution is stable and Tauri POC (#132) informs whether we pursue MAS via Electron or Tauri. See issue #133 for architectural approach (companion-app pattern).

---

## Dependency Graph

```
#126 (PAT Quick Fix)          ← START HERE (1-3 days)
  ↓
#125 (Distribution Infra)     ← Public releases repo (1-2 weeks)
  ↓
#127 (Microsoft Store) ──┐
#128 (macOS Notarize)  ──┤    ← Parallel (2-4 weeks)
                          ↓
                   #133 (Mac App Store) ← Long-term (6-12 months)
```
