import { useState, useRef, useEffect } from 'react';

const LANGUAGES = [
  'Spanish', 'French', 'German', 'Italian', 'Portuguese', 'Dutch',
  'Russian', 'Chinese', 'Japanese', 'Korean', 'Arabic', 'Hindi',
  'Polish', 'Turkish', 'Vietnamese', 'Thai',
];

interface TranslateButtonProps {
  transcriptId: string;
  onTranslate: (language: string) => void;
}

export function TranslateButton({ transcriptId: _transcriptId, onTranslate }: TranslateButtonProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isOpen]);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
        title="Translate transcript"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 5h12M9 3v2m1.048 9.5A18.022 18.022 0 016.412 9m6.088 9h7M11 21l5-10 5 10M12.751 5C11.783 10.77 8.07 15.61 3 18.129" />
        </svg>
        <span className="hidden sm:inline">Translate</span>
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1 w-48 py-1 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg z-50 max-h-64 overflow-y-auto">
          {LANGUAGES.map((lang) => (
            <button
              key={lang}
              onClick={() => {
                onTranslate(lang);
                setIsOpen(false);
              }}
              className="w-full text-left px-3 py-1.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            >
              {lang}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
