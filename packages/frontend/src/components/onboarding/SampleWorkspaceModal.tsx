import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '@/lib/api';

interface SampleWorkspaceModalProps {
  isOpen: boolean;
  onInstall: () => Promise<void>;
  onSkip: () => void;
  // Called when the modal detects an existing install and the user
  // chooses to keep it + proceed with the tour (no second install).
  onProceedWithExisting?: () => void;
}

/**
 * Asked at the start of the tour: install a small sample workspace
 * (~250 KB download) so the rest of the tour has real content to
 * point at? Skip is fine — the tour still walks the surface, just
 * without populated examples.
 *
 * If the user has already installed the sample workspace before
 * (i.e. retaking the tour), we skip the install pitch and go
 * straight to a "you already have it — start tour?" prompt.
 */
export function SampleWorkspaceModal({ isOpen, onInstall, onSkip, onProceedWithExisting }: SampleWorkspaceModalProps) {
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [alreadyInstalled, setAlreadyInstalled] = useState<boolean | null>(null);

  // Detect existing install when modal opens
  useEffect(() => {
    if (!isOpen) {
      setAlreadyInstalled(null);
      return;
    }
    let cancelled = false;
    api.onboarding
      .sampleWorkspaceStatus()
      .then((status) => {
        if (!cancelled) setAlreadyInstalled(status.installed);
      })
      .catch(() => {
        if (!cancelled) setAlreadyInstalled(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const handleInstall = async () => {
    setError(null);
    setInstalling(true);
    try {
      await onInstall();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Install failed');
      setInstalling(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/55" aria-hidden="true" />
      <div
        className="relative z-10 w-full max-w-lg mx-4 bg-card border border-border rounded-xl shadow-2xl animate-in fade-in zoom-in-95 duration-200"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sample-title"
      >
        {/* Already-installed branch — shown when user retakes the tour
            after they previously opted in. Don't re-download / re-seed,
            just let them proceed into the tour. */}
        {alreadyInstalled === true && (
          <div className="p-7">
            <h2 id="sample-title" className="text-xl font-bold text-foreground mb-2">
              Sample workspace already installed
            </h2>
            <p className="text-sm text-muted-foreground mb-6">
              You already have the sample workspace from a previous tour.
              We'll skip the install step and jump straight back into the
              walkthrough.
            </p>
            <div className="flex items-center justify-end gap-3">
              <button
                onClick={onSkip}
                className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                Skip the tour
              </button>
              <button
                onClick={onProceedWithExisting ?? onSkip}
                className="px-5 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
              >
                Start tour
              </button>
            </div>
          </div>
        )}

        {alreadyInstalled === false && (
        <div className="p-7">
          <h2 id="sample-title" className="text-xl font-bold text-foreground mb-2">
            Want a sample workspace to follow along?
          </h2>
          <p className="text-sm text-muted-foreground mb-4">
            The tour walks the full surface of Verbatim. With a sample workspace
            installed, every step has real content for you to click into — a
            transcribed recording, a few documents, inline notes, a chat history,
            and a second project that shows isolation.
          </p>

          <div className="rounded-lg border border-border bg-muted/30 p-4 mb-4 space-y-2 text-sm">
            <div className="flex items-start gap-2">
              <svg className="w-4 h-4 text-blue-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
              <div>
                <span className="font-medium text-foreground">Sandboxed.</span>
                <span className="text-muted-foreground"> Goes into projects clearly labeled "Tour:" and tagged in the database. One-click removal, never touches your real data.</span>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <svg className="w-4 h-4 text-blue-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
              </svg>
              <div>
                <span className="font-medium text-foreground">~250 KB download.</span>
                <span className="text-muted-foreground"> Real public-domain content (Apollo 11 audio, NIST cybersecurity overview, Unsplash photo). No AI-generated material.</span>
              </div>
            </div>
            <div className="flex items-start gap-2">
              <svg className="w-4 h-4 text-blue-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              <div>
                <span className="font-medium text-foreground">Easy to remove.</span>
                <span className="text-muted-foreground"> After the tour, you'll be asked if you want to keep it. Or remove anytime via Settings → General.</span>
              </div>
            </div>
          </div>

          {error && (
            <p className="text-sm text-red-600 dark:text-red-400 mb-3">
              {error}
            </p>
          )}

          <div className="flex items-center justify-end gap-3">
            <button
              onClick={onSkip}
              disabled={installing}
              className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            >
              Skip — start empty
            </button>
            <button
              onClick={handleInstall}
              disabled={installing}
              className="px-5 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50 inline-flex items-center gap-2"
            >
              {installing ? (
                <>
                  <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Installing…
                </>
              ) : 'Install sample workspace'}
            </button>
          </div>
        </div>
        )}

        {/* Loading state while we check the install status */}
        {alreadyInstalled === null && (
          <div className="p-12 flex items-center justify-center">
            <svg className="w-6 h-6 animate-spin text-muted-foreground" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
