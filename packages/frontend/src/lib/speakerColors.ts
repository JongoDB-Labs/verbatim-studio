/**
 * Stable speaker → color mapping for live + saved transcripts.
 *
 * Eight curated palette entries chosen to be distinguishable in light and
 * dark mode, and accessible against typical chat-bubble backgrounds. We
 * hash the speaker label so the same name always gets the same color.
 */

export interface SpeakerPalette {
  text: string;
  bg: string;
  ring: string;
  accent: string;
  border: string;
}

const PALETTE: readonly SpeakerPalette[] = [
  {
    text: 'text-purple-700 dark:text-purple-300',
    bg: 'bg-purple-100 dark:bg-purple-900/30',
    ring: 'ring-purple-300 dark:ring-purple-700',
    accent: 'bg-purple-500',
    border: 'border-l-purple-400',
  },
  {
    text: 'text-emerald-700 dark:text-emerald-300',
    bg: 'bg-emerald-100 dark:bg-emerald-900/30',
    ring: 'ring-emerald-300 dark:ring-emerald-700',
    accent: 'bg-emerald-500',
    border: 'border-l-emerald-400',
  },
  {
    text: 'text-sky-700 dark:text-sky-300',
    bg: 'bg-sky-100 dark:bg-sky-900/30',
    ring: 'ring-sky-300 dark:ring-sky-700',
    accent: 'bg-sky-500',
    border: 'border-l-sky-400',
  },
  {
    text: 'text-amber-700 dark:text-amber-300',
    bg: 'bg-amber-100 dark:bg-amber-900/30',
    ring: 'ring-amber-300 dark:ring-amber-700',
    accent: 'bg-amber-500',
    border: 'border-l-amber-400',
  },
  {
    text: 'text-pink-700 dark:text-pink-300',
    bg: 'bg-pink-100 dark:bg-pink-900/30',
    ring: 'ring-pink-300 dark:ring-pink-700',
    accent: 'bg-pink-500',
    border: 'border-l-pink-400',
  },
  {
    text: 'text-teal-700 dark:text-teal-300',
    bg: 'bg-teal-100 dark:bg-teal-900/30',
    ring: 'ring-teal-300 dark:ring-teal-700',
    accent: 'bg-teal-500',
    border: 'border-l-teal-400',
  },
  {
    text: 'text-indigo-700 dark:text-indigo-300',
    bg: 'bg-indigo-100 dark:bg-indigo-900/30',
    ring: 'ring-indigo-300 dark:ring-indigo-700',
    accent: 'bg-indigo-500',
    border: 'border-l-indigo-400',
  },
  {
    text: 'text-rose-700 dark:text-rose-300',
    bg: 'bg-rose-100 dark:bg-rose-900/30',
    ring: 'ring-rose-300 dark:ring-rose-700',
    accent: 'bg-rose-500',
    border: 'border-l-rose-400',
  },
];

const NEUTRAL: SpeakerPalette = {
  text: 'text-gray-600 dark:text-gray-400',
  bg: 'bg-gray-100 dark:bg-gray-700',
  ring: 'ring-gray-200 dark:ring-gray-600',
  accent: 'bg-gray-400',
  border: 'border-l-gray-300 dark:border-l-gray-600',
};

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/**
 * Pick a stable palette for a speaker label.
 *
 * "Speaker 1" / "Speaker 2" labels assigned in chunk-arrival order get
 * palette[0], palette[1], etc. Other labels hash into the palette so
 * named speakers stay color-stable across reloads.
 */
export function getSpeakerPalette(label: string | null | undefined): SpeakerPalette {
  if (!label) return NEUTRAL;
  const numericMatch = label.trim().match(/^Speaker\s+(\d+)$/i);
  if (numericMatch) {
    const idx = (parseInt(numericMatch[1], 10) - 1) % PALETTE.length;
    return PALETTE[idx >= 0 ? idx : 0];
  }
  return PALETTE[hashString(label) % PALETTE.length];
}

export const SPEAKER_PALETTE_LENGTH = PALETTE.length;
