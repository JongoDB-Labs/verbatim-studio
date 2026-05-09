import { useState } from 'react';
import { api, type Document } from '@/lib/api';
import { cn } from '@/lib/utils';
import { DocumentTypeIcon } from './DocumentTypeIcon';

interface DocumentThumbnailProps {
  document: Document;
  // When false, always show the icon. When true, attempt to fetch a real
  // thumbnail and fall back to the icon on error or if the type isn't
  // renderable (e.g. text/markdown).
  showPreview: boolean;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const SIZE_CLASSES: Record<NonNullable<DocumentThumbnailProps['size']>, string> = {
  sm: 'w-10 h-10',
  md: 'w-16 h-20',
  lg: 'w-24 h-32',
};

// Backend returns a thumbnail for these MIME families. Anything else gets
// the icon fallback without a wasted network request.
function isPreviewable(mime: string | null | undefined): boolean {
  if (!mime) return false;
  if (mime.startsWith('image/')) return true;
  if (mime === 'application/pdf') return true;
  if (mime.includes('wordprocessingml')) return true;
  if (mime.includes('spreadsheetml')) return true;
  if (mime.includes('presentationml')) return true;
  return false;
}

export function DocumentThumbnail({
  document,
  showPreview,
  size = 'md',
  className,
}: DocumentThumbnailProps) {
  const [errored, setErrored] = useState(false);
  const sizeClass = SIZE_CLASSES[size];
  const canPreview = showPreview && !errored && isPreviewable(document.mime_type);

  if (!canPreview) {
    return (
      <div
        className={cn(
          sizeClass,
          'flex-shrink-0 flex items-center justify-center rounded-md bg-muted text-muted-foreground',
          className,
        )}
      >
        <DocumentTypeIcon mimeType={document.mime_type} size={size === 'sm' ? 'sm' : 'md'} />
      </div>
    );
  }

  return (
    <div
      className={cn(
        sizeClass,
        'flex-shrink-0 overflow-hidden rounded-md bg-muted border border-border',
        className,
      )}
    >
      <img
        src={api.documents.getThumbnailUrl(document.id)}
        alt=""
        loading="lazy"
        decoding="async"
        onError={() => setErrored(true)}
        className="w-full h-full object-cover"
      />
    </div>
  );
}
