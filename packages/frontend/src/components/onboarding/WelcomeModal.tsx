import { createPortal } from 'react-dom';

interface WelcomeModalProps {
  isOpen: boolean;
  onStartTour: () => void;
  onSkip: () => void;
}

export function WelcomeModal({ isOpen, onStartTour, onSkip }: WelcomeModalProps) {
  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" aria-hidden="true" />

      {/* Modal */}
      <div
        className="relative z-10 w-full max-w-md mx-4 bg-card border border-border rounded-xl shadow-2xl animate-in fade-in zoom-in-95 duration-200"
        role="dialog"
        aria-modal="true"
        aria-labelledby="welcome-title"
        aria-describedby="welcome-description"
      >
        <div className="p-8 text-center">
          {/* Wave emoji */}
          <div className="text-5xl mb-4">
            <span role="img" aria-label="Wave">👋</span>
          </div>

          {/* Title */}
          <h2 id="welcome-title" className="text-2xl font-bold text-foreground mb-3">
            Welcome to Verbatim Studio
          </h2>

          {/* Description */}
          <p id="welcome-description" className="text-muted-foreground mb-3">
            An offline-first workspace for transcription, documents, and intelligent search — with a local AI assistant that knows your work.
          </p>
          <p className="text-muted-foreground mb-1">
            <span className="inline-flex items-center gap-1.5">
              <svg className="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
              <span className="font-medium">Private by design.</span>
            </span>
          </p>
          <p className="text-muted-foreground mb-6">
            Your data, your device, your rules.
          </p>
          <p className="text-muted-foreground mb-8">
            The tour walks the full surface in about 3 minutes. Skip and the toast in the corner will let you start it whenever you want.
          </p>

          {/* Actions */}
          <div className="flex items-center justify-center gap-4">
            <button
              onClick={onStartTour}
              className="px-6 py-2.5 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
            >
              Start Tour
            </button>
            <button
              onClick={onSkip}
              className="px-6 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              Maybe Later
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}
