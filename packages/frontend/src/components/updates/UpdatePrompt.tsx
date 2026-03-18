import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';

export interface UpdatePromptProps {
  version: string;
  downloadUrl: string;
  onUpdate: () => void;
  onDismiss: () => void;
}

export function UpdatePrompt({
  version,
  downloadUrl,
  onUpdate,
  onDismiss,
}: UpdatePromptProps) {
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadPercent, setDownloadPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [fallbackUrl, setFallbackUrl] = useState<string | null>(null);

  // Set up IPC listeners for update progress
  useEffect(() => {
    if (!window.electronAPI) return;

    const cleanupDownloading = window.electronAPI.onUpdateDownloading(
      ({ percent }) => {
        setDownloadPercent(percent);
      }
    );

    const cleanupReady = window.electronAPI.onUpdateReady(() => {
      // App will quit momentarily - no action needed
    });

    const cleanupError = window.electronAPI.onUpdateError(
      ({ message, fallbackUrl: url }) => {
        setIsDownloading(false);
        setError(message);
        if (url) setFallbackUrl(url);
      }
    );

    return () => {
      cleanupDownloading();
      cleanupReady();
      cleanupError();
    };
  }, []);

  const handleUpdateNow = () => {
    if (!window.electronAPI) return;

    setIsDownloading(true);
    setDownloadPercent(0);
    setError(null);
    setFallbackUrl(null);

    window.electronAPI.startUpdate(downloadUrl, version);
    onUpdate();
  };

  const handleClose = () => {
    onDismiss();
  };

  // Render error state
  if (error) {
    return createPortal(
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        {/* Backdrop */}
        <div className="absolute inset-0 bg-black/50" aria-hidden="true" />

        {/* Modal */}
        <div
          className="relative z-10 w-full max-w-md mx-4 bg-card border border-border rounded-xl shadow-2xl animate-in fade-in zoom-in-95 duration-200"
          role="dialog"
          aria-modal="true"
          aria-labelledby="update-error-title"
        >
          <div className="p-6">
            {/* Error Icon */}
            <div className="flex items-center justify-center w-12 h-12 mx-auto mb-4 rounded-full bg-red-100 dark:bg-red-900/30">
              <svg
                className="w-6 h-6 text-red-600 dark:text-red-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
            </div>

            {/* Title */}
            <h2
              id="update-error-title"
              className="text-lg font-semibold text-foreground text-center mb-2"
            >
              Update Failed
            </h2>

            {/* Error message */}
            <p className="text-sm text-muted-foreground text-center mb-4">
              {error}
            </p>

            {/* Fallback URL */}
            {fallbackUrl && (
              <p className="text-sm text-muted-foreground text-center mb-6">
                You can download manually:{' '}
                <a
                  href={fallbackUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 dark:text-blue-400 underline hover:text-blue-500 dark:hover:text-blue-300"
                >
                  Download v{version}
                </a>
              </p>
            )}

            {/* Close button */}
            <div className="flex justify-center">
              <button
                onClick={handleClose}
                className="px-6 py-2.5 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      </div>,
      document.body
    );
  }

  // Render downloading state
  if (isDownloading) {
    return createPortal(
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        {/* Backdrop */}
        <div className="absolute inset-0 bg-black/50" aria-hidden="true" />

        {/* Modal */}
        <div
          className="relative z-10 w-full max-w-md mx-4 bg-card border border-border rounded-xl shadow-2xl animate-in fade-in zoom-in-95 duration-200"
          role="dialog"
          aria-modal="true"
          aria-labelledby="update-downloading-title"
        >
          <div className="p-6">
            {/* Download Icon */}
            <div className="flex items-center justify-center w-12 h-12 mx-auto mb-4 rounded-full bg-blue-100 dark:bg-blue-900/30">
              <svg
                className="w-6 h-6 text-blue-600 dark:text-blue-400 animate-pulse"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                />
              </svg>
            </div>

            {/* Title */}
            <h2
              id="update-downloading-title"
              className="text-lg font-semibold text-foreground text-center mb-4"
            >
              Downloading Update
            </h2>

            {/* Progress bar */}
            <div className="mb-2">
              <div className="h-3 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-600 dark:bg-blue-500 rounded-full"
                  style={{ width: `${downloadPercent}%` }}
                />
              </div>
            </div>

            {/* Percentage */}
            <p className="text-sm text-muted-foreground text-center mb-4">
              {Math.round(downloadPercent)}%
            </p>

            {/* Instructions */}
            <div className="bg-muted/50 rounded-lg p-3 space-y-1">
              <p className="text-xs text-muted-foreground text-center">
                Please do not close the app while the update is downloading.
              </p>
              <p className="text-xs text-muted-foreground text-center">
                The app will automatically restart when the update is ready.
              </p>
            </div>
          </div>
        </div>
      </div>,
      document.body
    );
  }

  // Render initial state (update available)
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" aria-hidden="true" />

      {/* Modal */}
      <div
        className="relative z-10 w-full max-w-md mx-4 bg-card border border-border rounded-xl shadow-2xl animate-in fade-in zoom-in-95 duration-200"
        role="dialog"
        aria-modal="true"
        aria-labelledby="update-available-title"
        aria-describedby="update-available-description"
      >
        <div className="p-6">
          {/* Update Icon */}
          <div className="flex items-center justify-center w-12 h-12 mx-auto mb-4 rounded-full bg-green-100 dark:bg-green-900/30">
            <svg
              className="w-6 h-6 text-green-600 dark:text-green-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
          </div>

          {/* Title */}
          <h2
            id="update-available-title"
            className="text-lg font-semibold text-foreground text-center mb-2"
          >
            Update Available
          </h2>

          {/* Description */}
          <p
            id="update-available-description"
            className="text-sm text-muted-foreground text-center mb-6"
          >
            Version {version} is available.
          </p>

          {/* Actions */}
          <div className="flex items-center justify-center gap-4">
            <button
              onClick={handleClose}
              className="px-6 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              Later
            </button>
            <button
              onClick={handleUpdateNow}
              className="px-6 py-2.5 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
            >
              Update Now
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
