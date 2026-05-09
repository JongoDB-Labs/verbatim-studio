import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { TourTooltip } from './TourTooltip';
import { TOUR_STEPS, TOUR_STORAGE_KEYS } from './tourSteps';

interface OnboardingTourProps {
  isActive: boolean;
  onComplete: () => void;
  onSkip: () => void;
  onNavigate?: (target: string) => void;
  // When true, steps tagged requiresDemo are included. When false,
  // they're filtered out so users without seed data don't get pointed
  // at empty surfaces.
  hasSampleWorkspace?: boolean;
}

export function OnboardingTour({ isActive, onComplete, onSkip, onNavigate, hasSampleWorkspace = false }: OnboardingTourProps) {
  // Filter steps based on whether the user has the sample workspace
  // installed. Filtering happens once per tour activation so step
  // indices stay stable across renders.
  const steps = useMemo(
    () => (hasSampleWorkspace ? TOUR_STEPS : TOUR_STEPS.filter((s) => !s.requiresDemo)),
    [hasSampleWorkspace],
  );
  const [currentStep, setCurrentStep] = useState(0);
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null);
  const [targetElement, setTargetElement] = useState<HTMLElement | null>(null);

  const step = steps[currentStep];

  // Handle navigation when step changes
  useEffect(() => {
    if (!isActive || !step) return;

    // If this step requires navigation, trigger it
    if (step.navigateTo && onNavigate) {
      onNavigate(step.navigateTo);
    }

    // Optional triggerEvent — dispatches a window event the relevant
    // page component listens for (e.g. "tour-open-notes-panel" makes
    // the document viewer auto-open its notes side panel so the tour
    // can highlight a real expanded note rather than just point at a
    // closed button).
    if (step.triggerEvent) {
      const delay = setTimeout(() => {
        window.dispatchEvent(new CustomEvent(step.triggerEvent!.type, {
          detail: step.triggerEvent!.detail,
        }));
      }, 350);
      return () => clearTimeout(delay);
    }
  }, [isActive, step, onNavigate, currentStep]);

  // Find and highlight the target element
  useEffect(() => {
    if (!isActive || !step) return;

    const findTarget = () => {
      const target = document.querySelector(step.target) as HTMLElement | null;
      if (target) {
        setTargetElement(target);
        setTargetRect(target.getBoundingClientRect());

        // Add highlight class
        target.setAttribute('data-tour-active', 'true');

        // Scroll the target into view if it's outside the viewport.
        // Common case: Settings tab subsections are far down the page,
        // so just navigating to "settings#transcription" lands at the
        // top — the tour highlight points at a section the user can't
        // see without scrolling.
        const rect = target.getBoundingClientRect();
        const inView =
          rect.top >= 0 &&
          rect.bottom <= (window.innerHeight || document.documentElement.clientHeight);
        if (!inView) {
          target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }
    };

    // Initial find with a small delay to allow for navigation/rendering
    const initialTimeout = setTimeout(findTarget, 150);

    // Retry finding target if not found immediately (for navigation cases)
    const retryTimeout = setTimeout(() => {
      if (!targetElement) {
        findTarget();
      } else {
        // Already found — re-check scroll position after navigation
        // settles (settings page hash-change can shift content).
        findTarget();
      }
    }, 500);

    // Update position on scroll/resize
    const handleUpdate = () => {
      const target = document.querySelector(step.target) as HTMLElement | null;
      if (target) {
        setTargetRect(target.getBoundingClientRect());
      }
    };

    window.addEventListener('scroll', handleUpdate, true);
    window.addEventListener('resize', handleUpdate);

    return () => {
      clearTimeout(initialTimeout);
      clearTimeout(retryTimeout);
      window.removeEventListener('scroll', handleUpdate, true);
      window.removeEventListener('resize', handleUpdate);

      // Remove highlight from previous target
      if (targetElement) {
        targetElement.removeAttribute('data-tour-active');
      }
    };
  }, [isActive, step, currentStep]);

  // Clean up highlight on unmount or when tour ends
  useEffect(() => {
    return () => {
      if (targetElement) {
        targetElement.removeAttribute('data-tour-active');
      }
    };
  }, [targetElement]);

  // Reset step when tour starts
  useEffect(() => {
    if (isActive) {
      setCurrentStep(0);
    }
  }, [isActive]);

  const handleNext = useCallback(() => {
    // Remove highlight from current target
    if (targetElement) {
      targetElement.removeAttribute('data-tour-active');
    }

    if (currentStep < steps.length - 1) {
      setCurrentStep((prev) => prev + 1);
    } else {
      // Tour complete
      localStorage.setItem(TOUR_STORAGE_KEYS.completed, 'true');
      localStorage.removeItem(TOUR_STORAGE_KEYS.skipped);
      onComplete();
    }
  }, [currentStep, targetElement, onComplete]);

  const handleBack = useCallback(() => {
    // Remove highlight from current target
    if (targetElement) {
      targetElement.removeAttribute('data-tour-active');
    }

    if (currentStep > 0) {
      setCurrentStep((prev) => prev - 1);
    }
  }, [currentStep, targetElement]);

  const handleSkip = useCallback(() => {
    // Remove highlight from current target
    if (targetElement) {
      targetElement.removeAttribute('data-tour-active');
    }

    localStorage.setItem(TOUR_STORAGE_KEYS.skipped, 'true');
    onSkip();
  }, [targetElement, onSkip]);

  // Handle escape key
  useEffect(() => {
    if (!isActive) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleSkip();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isActive, handleSkip]);

  if (!isActive || !step) return null;

  return createPortal(
    <>
      {/* Backdrop overlay */}
      <div
        className="fixed inset-0 z-[45] bg-black/50 transition-opacity duration-300"
        aria-hidden="true"
      />

      {/* Highlight cutout - creates a "hole" around the target */}
      {targetRect && (
        <div
          className="fixed z-[50] rounded-lg ring-4 ring-primary shadow-[0_0_0_4px_hsl(var(--primary)/0.3)] animate-tour-pulse pointer-events-none"
          style={{
            top: targetRect.top - 4,
            left: targetRect.left - 4,
            width: targetRect.width + 8,
            height: targetRect.height + 8,
          }}
          aria-hidden="true"
        />
      )}

      {/* Tooltip */}
      <TourTooltip
        step={step}
        currentStep={currentStep}
        totalSteps={steps.length}
        onNext={handleNext}
        onBack={handleBack}
        onSkip={handleSkip}
        targetRect={targetRect}
      />
    </>,
    document.body
  );
}
