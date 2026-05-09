import { useEffect, useRef, useState } from 'react';
import type { TourStep } from './tourSteps';

interface TourTooltipProps {
  step: TourStep;
  currentStep: number;
  totalSteps: number;
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
  targetRect: DOMRect | null;
}

export function TourTooltip({
  step,
  currentStep,
  totalSteps,
  onNext,
  onBack,
  onSkip,
  targetRect,
}: TourTooltipProps) {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });

  const isFirstStep = currentStep === 0;
  const isLastStep = currentStep === totalSteps - 1;

  useEffect(() => {
    if (!targetRect || !tooltipRef.current) return;

    const tooltip = tooltipRef.current;
    const tooltipRect = tooltip.getBoundingClientRect();
    const padding = 12;

    let top = 0;
    let left = 0;

    switch (step.position) {
      case 'right':
        top = targetRect.top + targetRect.height / 2 - tooltipRect.height / 2;
        left = targetRect.right + padding;
        break;
      case 'left':
        top = targetRect.top + targetRect.height / 2 - tooltipRect.height / 2;
        left = targetRect.left - tooltipRect.width - padding;
        break;
      case 'top':
        top = targetRect.top - tooltipRect.height - padding;
        left = targetRect.left + targetRect.width / 2 - tooltipRect.width / 2;
        break;
      case 'bottom':
        top = targetRect.bottom + padding;
        left = targetRect.left + targetRect.width / 2 - tooltipRect.width / 2;
        break;
    }

    // Keep tooltip within viewport
    const viewportPadding = 16;
    top = Math.max(viewportPadding, Math.min(top, window.innerHeight - tooltipRect.height - viewportPadding));
    left = Math.max(viewportPadding, Math.min(left, window.innerWidth - tooltipRect.width - viewportPadding));

    setPosition({ top, left });
  }, [targetRect, step.position]);

  return (
    <div
      ref={tooltipRef}
      className="fixed z-[60] w-[22rem] max-w-[calc(100vw-2rem)] bg-card border border-border rounded-lg shadow-xl animate-in fade-in slide-in-from-bottom-2 duration-200"
      style={{ top: position.top, left: position.left }}
      role="dialog"
      aria-labelledby="tour-title"
      aria-describedby="tour-description"
    >
      {/* Arrow - positioned based on step.position */}
      <div
        className={`absolute w-3 h-3 bg-card border-border rotate-45 ${
          step.position === 'right'
            ? '-left-1.5 top-1/2 -translate-y-1/2 border-l border-b'
            : step.position === 'left'
            ? '-right-1.5 top-1/2 -translate-y-1/2 border-r border-t'
            : step.position === 'top'
            ? '-bottom-1.5 left-1/2 -translate-x-1/2 border-r border-b'
            : '-top-1.5 left-1/2 -translate-x-1/2 border-l border-t'
        }`}
      />

      <div className="p-4">
        {/* Section label — surfaces the tour journey shape so users
            see where they are in the breadth of the app, not just a
            count of steps. */}
        {step.category && (
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-primary/80">
            {step.category}
          </div>
        )}

        {/* Header */}
        <div className="flex items-start justify-between mb-2 gap-2">
          <h3 id="tour-title" className="text-base font-semibold text-foreground">
            {step.title}
          </h3>
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            {currentStep + 1} of {totalSteps}
          </span>
        </div>

        {/* Description */}
        <p id="tour-description" className="text-sm text-muted-foreground mb-3 leading-relaxed">
          {step.description}
        </p>

        {/* Optional external link — used for the Chrome extension step
            and any future "open this in your browser" CTA. */}
        {step.externalUrl && (
          <a
            href={step.externalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 mb-4 text-sm font-medium text-primary hover:underline"
          >
            {step.externalUrlLabel ?? 'Open link'}
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M14 5l7 7m0 0l-7 7m7-7H3" />
            </svg>
          </a>
        )}

        {/* Caveats — usually about model downloads required for AI
            features besides transcription. Surfaced as info cards so
            users don't get confused why a button is disabled. */}
        {step.caveats && step.caveats.length > 0 && (
          <div className="mb-3 space-y-2">
            {step.caveats.map((cv, idx) => (
              <div
                key={idx}
                className="rounded-md border border-blue-300/60 dark:border-blue-700/60 bg-blue-50/60 dark:bg-blue-950/30 p-2.5"
              >
                <div className="flex items-start gap-2">
                  <svg className="w-4 h-4 text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-blue-900 dark:text-blue-100">
                      {cv.label}
                    </p>
                    {cv.detail && (
                      <p className="mt-0.5 text-xs text-blue-800/80 dark:text-blue-200/70 leading-relaxed">
                        {cv.detail}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Recommended-toggle tips. Each card is a concrete "try this"
            suggestion with reasoning, surfaced inline so users learn
            the non-obvious power-user toggles right when they're
            standing in the relevant settings tab. */}
        {step.recommendations && step.recommendations.length > 0 && (
          <div className="mb-4 space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-600 dark:text-amber-400">
              Try this
            </p>
            {step.recommendations.map((rec, idx) => (
              <div
                key={idx}
                className="rounded-md border border-amber-300/60 dark:border-amber-700/60 bg-amber-50/60 dark:bg-amber-950/30 p-2.5"
              >
                <div className="flex items-start gap-2">
                  <svg className="w-4 h-4 text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.4">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
                      {rec.label}
                    </p>
                    {rec.reason && (
                      <p className="mt-0.5 text-xs text-amber-800/80 dark:text-amber-200/70 leading-relaxed">
                        {rec.reason}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center justify-between">
          <button
            onClick={onSkip}
            className="text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            Skip
          </button>
          <div className="flex items-center gap-2">
            {!isFirstStep && (
              <button
                onClick={onBack}
                className="px-3 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                Back
              </button>
            )}
            <button
              onClick={onNext}
              className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
            >
              {isLastStep ? 'Finish' : 'Next'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
