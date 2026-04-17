import { BrowserWindow, app } from 'electron';
import path from 'path';
import { readFileSync } from 'fs';

let splashWindow: BrowserWindow | null = null;

// Read the app icon and convert to base64 for inline embedding.
// In packaged builds the icon lives in extraResources (process.resourcesPath).
// In development it's at apps/electron/assets/icon.png relative to dist/main/.
let iconBase64 = '';
try {
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, 'icon.png')
    : path.join(__dirname, '..', '..', 'assets', 'icon.png');
  iconBase64 = readFileSync(iconPath).toString('base64');
} catch {
  // Icon not available — splash will show without it
}

function buildSplashHtml(): string {
  const logoHtml = iconBase64
    ? `<img src="data:image/png;base64,${iconBase64}" class="logo" alt="" />`
    : '';

  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: #0f172a;
  color: #e2e8f0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  -webkit-app-region: drag;
  user-select: none;
  overflow: hidden;
}
.logo {
  width: 64px;
  height: 64px;
  border-radius: 14px;
  margin-bottom: 16px;
}
.title {
  font-size: 21px;
  font-weight: 600;
  color: #f8fafc;
  letter-spacing: -0.01em;
  margin-bottom: 28px;
}
.spinner {
  width: 26px;
  height: 26px;
  border: 2.5px solid rgba(255,255,255,0.06);
  border-top-color: #4B8FDE;
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
  margin-bottom: 18px;
}
@keyframes spin { to { transform: rotate(360deg); } }
#status {
  font-size: 12px;
  color: #64748b;
  text-align: center;
  max-width: 320px;
  line-height: 1.4;
}
</style></head><body>
  ${logoHtml}
  <div class="title">Verbatim Studio</div>
  <div class="spinner"></div>
  <div id="status">Initializing\u2026</div>
</body></html>`;
}

export function createSplashWindow(): BrowserWindow {
  splashWindow = new BrowserWindow({
    width: 400,
    height: iconBase64 ? 300 : 220,
    frame: false,
    resizable: false,
    center: true,
    skipTaskbar: false,
    backgroundColor: '#0f172a',
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  const html = buildSplashHtml();
  splashWindow.loadURL(
    `data:text/html;charset=utf-8,${encodeURIComponent(html)}`
  );

  splashWindow.once('ready-to-show', () => {
    splashWindow?.show();
    // On Windows, bring the splash to the foreground reliably —
    // without this the window may appear behind others on the first click
    if (process.platform === 'win32') {
      splashWindow?.setAlwaysOnTop(true);
      splashWindow?.focus();
      setTimeout(() => splashWindow?.setAlwaysOnTop(false), 500);
    }
  });

  return splashWindow;
}

export function updateSplashStatus(text: string): void {
  if (!splashWindow || splashWindow.isDestroyed()) return;
  const safe = text.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  splashWindow.webContents
    .executeJavaScript(`document.getElementById('status').textContent='${safe}';`)
    .catch(() => {});
}

export function focusSplashWindow(): void {
  if (!splashWindow || splashWindow.isDestroyed()) return;
  if (splashWindow.isMinimized()) splashWindow.restore();
  splashWindow.focus();
}

export function closeSplashWindow(): void {
  if (!splashWindow || splashWindow.isDestroyed()) return;
  splashWindow.close();
  splashWindow = null;
}
