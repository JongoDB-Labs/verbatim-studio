import { isElectron } from '../../lib/api';

// Check if running on macOS in Electron
const isMacOS = typeof window !== 'undefined' && (window as any).electronAPI?.platform === 'darwin';

/**
 * Native-style title bar for macOS Electron apps.
 * Creates a draggable region at the top of the window that allows
 * users to move the window by dragging, just like native macOS apps.
 *
 * Only renders in Electron on macOS (where titleBarStyle: 'hiddenInset' is used).
 */
export function TitleBar() {
  // Only show in Electron on macOS
  if (!isElectron() || !isMacOS) {
    return null;
  }

  return (
    <div
      className="fixed top-0 left-0 right-0 h-9 z-[100] pointer-events-auto"
      style={{
        // Make the entire bar draggable (moves the window)
        WebkitAppRegion: 'drag',
        // Subtle background that blends with the app
        background: 'transparent',
      } as React.CSSProperties}
      aria-hidden="true"
    />
  );
}
