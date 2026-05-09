import { useState, useEffect, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ExcelJS from 'exceljs';
import { MessageSquare } from 'lucide-react';
import { api, type Document, type OCRStatusResponse } from '@/lib/api';
import { NotesPanel, PDFViewer, DOCXViewer } from '@/components/documents';
import { ProjectAssignmentControl } from '@/components/shared/ProjectAssignmentControl';

interface DocumentViewerPageProps {
  documentId: string;
  onBack: () => void;
}

// MIME types for Office documents
const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';

interface SheetData {
  name: string;
  headers: string[];
  rows: string[][];
}

interface SlideData {
  index: number;
  texts: string[];
}

export function DocumentViewerPage({ documentId, onBack }: DocumentViewerPageProps) {
  const [document, setDocument] = useState<Document | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [officeLoading, setOfficeLoading] = useState(false);
  const [ocrRunning, setOcrRunning] = useState(false);
  const [extractRunning, setExtractRunning] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [sheets, setSheets] = useState<SheetData[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const [slides, setSlides] = useState<SlideData[]>([]);
  const [activeSlide, setActiveSlide] = useState(0);
  const [ocrContent, setOcrContent] = useState<string | null>(null);
  const [showNotes, setShowNotes] = useState(false);
  const [currentPdfPage, setCurrentPdfPage] = useState(1);
  const [pendingAnchor, setPendingAnchor] = useState<{ type: 'selection'; data: { text: string; page: number } } | null>(null);
  const [highlightText, setHighlightText] = useState<string | null>(null);
  const [ocrStatus, setOcrStatus] = useState<OCRStatusResponse | null>(null);

  // Fetch OCR status on mount and when OCR model is downloaded
  useEffect(() => {
    const fetchOcrStatus = () => api.ocr.status().then(setOcrStatus).catch(() => setOcrStatus(null));
    fetchOcrStatus();
    window.addEventListener('ocr-status-changed', fetchOcrStatus);
    return () => window.removeEventListener('ocr-status-changed', fetchOcrStatus);
  }, []);

  // Tour can dispatch "tour-open-notes-panel" to auto-open the side
  // panel so the tour step highlights a real expanded note instead
  // of pointing at a closed toggle button.
  useEffect(() => {
    const handler = () => setShowNotes(true);
    window.addEventListener('tour-open-notes-panel', handler);
    return () => window.removeEventListener('tour-open-notes-panel', handler);
  }, []);

  // Handle text selection in PDF to create a note
  const handleSelectionNote = useCallback((selection: { text: string; page: number }) => {
    setPendingAnchor({ type: 'selection', data: selection });
    setShowNotes(true);
  }, []);

  // Handle navigation to anchor from notes panel
  const handleNavigateToAnchor = useCallback((anchorType: string, anchorData: Record<string, unknown>) => {
    if (anchorType === 'page' && typeof anchorData.page === 'number') {
      setCurrentPdfPage(anchorData.page);
    } else if (anchorType === 'selection' && typeof anchorData.text === 'string') {
      // Navigate to page and highlight text
      if (typeof anchorData.page === 'number') {
        setCurrentPdfPage(anchorData.page);
      }
      setHighlightText(anchorData.text);
    }
  }, []);

  const handleRunOcr = async () => {
    if (!document || ocrRunning) return;
    setOcrRunning(true);
    try {
      await api.documents.runOcr(documentId);
      // Refresh document to show new status
      const updatedDoc = await api.documents.get(documentId);
      setDocument(updatedDoc);
    } catch (err) {
      console.error('Failed to start OCR:', err);
    } finally {
      setOcrRunning(false);
    }
  };

  const handleExtractVocab = async () => {
    if (!document || extractRunning) return;
    setExtractRunning(true);
    try {
      const result = await api.dictionary.extractFromDocument(
        documentId,
        document.project_id ?? undefined,
      );
      const dedupedFromBundle = result.skipped_already_bundled;
      const dedupedFromUser = result.skipped_already_user;
      const summary = [
        `Added ${result.accepted} new term${result.accepted === 1 ? '' : 's'} to your vocabulary.`,
        `Considered ${result.candidates_proposed} candidate${result.candidates_proposed === 1 ? '' : 's'}.`,
        dedupedFromBundle > 0 && `${dedupedFromBundle} already in bundled corpus.`,
        dedupedFromUser > 0 && `${dedupedFromUser} already in your vocabulary.`,
        result.skipped_common_english > 0 && `${result.skipped_common_english} skipped as common English.`,
        result.errors.length > 0 && `${result.errors.length} error${result.errors.length === 1 ? '' : 's'} (see console).`,
      ]
        .filter(Boolean)
        .join('\n');
      if (result.errors.length > 0) {
        console.error('Vocabulary extraction errors:', result.errors);
      }
      alert(summary);
    } catch (err) {
      console.error('Failed to extract vocabulary:', err);
      alert(
        err instanceof Error
          ? `Vocabulary extraction failed: ${err.message}`
          : 'Vocabulary extraction failed.',
      );
    } finally {
      setExtractRunning(false);
    }
  };

  const handleCancelProcessing = async () => {
    if (!document || cancelling) return;
    setCancelling(true);
    try {
      await api.documents.cancelProcessing(documentId);
      // Refresh document to show new status
      const updatedDoc = await api.documents.get(documentId);
      setDocument(updatedDoc);
    } catch (err) {
      console.error('Failed to cancel processing:', err);
    } finally {
      setCancelling(false);
    }
  };

  // Check if document supports OCR (type and not already processed with OCR)
  const documentSupportsOcr = document &&
    document.status === 'completed' &&
    (document.mime_type.startsWith('image/') || document.mime_type === 'application/pdf') &&
    !document.metadata?.ocr_engine;

  // Check if document has extracted text (any OCR model or PDF extractor)
  const hasExtractedText = !!document?.metadata?.ocr_engine;

  // Fetch OCR content when document has OCR results
  useEffect(() => {
    async function fetchOcrContent() {
      if (!document || !hasExtractedText) {
        setOcrContent(null);
        return;
      }
      try {
        const contentRes = await api.documents.getContent(documentId);
        setOcrContent(contentRes.content);
      } catch {
        setOcrContent(null);
      }
    }
    fetchOcrContent();
  }, [document, hasExtractedText, documentId]);

  // Parse OCR content into pages (format: "## Page N\n\ntext...")
  const ocrPages = useMemo(() => {
    if (!ocrContent) return {};
    const pages: Record<number, string> = {};
    // Split by page headers
    const parts = ocrContent.split(/(?=## Page \d+)/);
    for (const part of parts) {
      const match = part.match(/^## Page (\d+)\n\n?([\s\S]*)/);
      if (match) {
        const pageNum = parseInt(match[1], 10);
        const pageText = match[2].trim();
        pages[pageNum] = pageText;
      }
    }
    return pages;
  }, [ocrContent]);

  // Get OCR text for current page
  const currentPageOcrText = ocrPages[currentPdfPage] || null;

  // Poll for status updates when document is processing
  useEffect(() => {
    if (!document || (document.status !== 'pending' && document.status !== 'processing')) {
      return;
    }

    const pollStatus = async () => {
      try {
        const updatedDoc = await api.documents.get(documentId);
        setDocument(updatedDoc);
      } catch (err) {
        console.error('Failed to poll document status:', err);
      }
    };

    const interval = setInterval(pollStatus, 2000);
    return () => clearInterval(interval);
  }, [document?.status, documentId]);

  useEffect(() => {
    async function load() {
      try {
        const doc = await api.documents.get(documentId);
        setDocument(doc);

        // Only fetch text content for documents that need markdown rendering
        const isPdf = doc.mime_type === 'application/pdf';
        const isImage = doc.mime_type.startsWith('image/');
        const isOffice = [DOCX_MIME, XLSX_MIME, PPTX_MIME].includes(doc.mime_type);

        if (doc.status === 'completed' && !isPdf && !isImage && !isOffice) {
          try {
            const contentRes = await api.documents.getContent(documentId);
            setContent(contentRes.content);
          } catch {
            setContent(null);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load document');
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [documentId]);

  // Render XLSX files using exceljs
  useEffect(() => {
    async function renderXlsx() {
      if (!document || document.mime_type !== XLSX_MIME) {
        return;
      }

      setOfficeLoading(true);
      try {
        const response = await fetch(api.documents.getFileUrl(documentId));
        const arrayBuffer = await response.arrayBuffer();

        const workbook = new ExcelJS.Workbook();
        await workbook.xlsx.load(arrayBuffer);

        const sheetData: SheetData[] = [];
        workbook.eachSheet((worksheet) => {
          const headers: string[] = [];
          const rows: string[][] = [];

          worksheet.eachRow((row, rowNumber) => {
            const values = row.values as (string | number | boolean | null | undefined)[];
            // ExcelJS row.values is 1-indexed, first element is empty
            const cells = values.slice(1).map(v => {
              if (v === null || v === undefined) return '';
              if (typeof v === 'object' && 'text' in v) return String((v as { text: string }).text);
              if (typeof v === 'object' && 'result' in v) return String((v as { result: unknown }).result);
              return String(v);
            });

            if (rowNumber === 1) {
              headers.push(...cells);
            } else {
              rows.push(cells);
            }
          });

          if (headers.length > 0 || rows.length > 0) {
            sheetData.push({ name: worksheet.name, headers, rows });
          }
        });

        setSheets(sheetData);
        setActiveSheet(0);
      } catch (err) {
        console.error('Failed to render XLSX:', err);
        try {
          const contentRes = await api.documents.getContent(documentId);
          setContent(contentRes.content);
        } catch {
          setContent(null);
        }
      } finally {
        setOfficeLoading(false);
      }
    }

    if (document?.status === 'completed' && document.mime_type === XLSX_MIME) {
      renderXlsx();
    }
  }, [document, documentId]);

  // Render PPTX files by extracting slide content
  useEffect(() => {
    async function renderPptx() {
      if (!document || document.mime_type !== PPTX_MIME) {
        return;
      }

      setOfficeLoading(true);
      try {
        const response = await fetch(api.documents.getFileUrl(documentId));
        const arrayBuffer = await response.arrayBuffer();

        // PPTX is a ZIP file, use JSZip to extract
        const JSZip = (await import('jszip')).default;
        const zip = await JSZip.loadAsync(arrayBuffer);

        const slideData: SlideData[] = [];
        const slideFiles = Object.keys(zip.files)
          .filter(f => f.match(/ppt\/slides\/slide\d+\.xml$/))
          .sort((a, b) => {
            const numA = parseInt(a.match(/slide(\d+)/)?.[1] || '0');
            const numB = parseInt(b.match(/slide(\d+)/)?.[1] || '0');
            return numA - numB;
          });

        for (let i = 0; i < slideFiles.length; i++) {
          const slideXml = await zip.file(slideFiles[i])?.async('text');
          if (slideXml) {
            // Extract text content from XML using regex (simple approach)
            const texts: string[] = [];
            const textMatches = slideXml.matchAll(/<a:t>([^<]*)<\/a:t>/g);
            for (const match of textMatches) {
              const text = match[1].trim();
              if (text) {
                texts.push(text);
              }
            }
            slideData.push({ index: i + 1, texts });
          }
        }

        setSlides(slideData);
        setActiveSlide(0);
      } catch (err) {
        console.error('Failed to render PPTX:', err);
        try {
          const contentRes = await api.documents.getContent(documentId);
          setContent(contentRes.content);
        } catch {
          setContent(null);
        }
      } finally {
        setOfficeLoading(false);
      }
    }

    if (document?.status === 'completed' && document.mime_type === PPTX_MIME) {
      renderPptx();
    }
  }, [document, documentId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <svg className="w-8 h-8 animate-spin text-blue-500" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    );
  }

  if (error || !document) {
    return (
      <div className="text-center py-12">
        <p className="text-red-500">{error || 'Document not found'}</p>
        <button onClick={onBack} className="mt-4 text-blue-500 hover:underline">
          Go back
        </button>
      </div>
    );
  }

  const isPdf = document.mime_type === 'application/pdf';
  const isImage = document.mime_type.startsWith('image/');
  const isDocx = document.mime_type === DOCX_MIME;
  const isXlsx = document.mime_type === XLSX_MIME;
  const isPptx = document.mime_type === PPTX_MIME;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={onBack}
          className="p-2 rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <div className="flex-1">
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">
            {document.title}
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {document.filename} • {document.status}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ProjectAssignmentControl
            currentProjectId={document.project_id}
            onAssign={async (projectId) => {
              await api.documents.update(document.id, { project_id: projectId });
              const fresh = await api.documents.get(document.id);
              setDocument(fresh);
            }}
          />
          {documentSupportsOcr && (
            <button
              data-tour="run-ocr"
              onClick={handleRunOcr}
              disabled={ocrRunning || !ocrStatus?.available}
              title={!ocrStatus?.available ? 'Download OCR model in Settings → AI to enable OCR' : undefined}
              className={`inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                ocrStatus?.available
                  ? 'text-white bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400'
                  : 'text-gray-500 bg-gray-200 dark:bg-gray-700 dark:text-gray-400 cursor-not-allowed'
              }`}
            >
              {ocrRunning ? (
                <>
                  <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Running OCR...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  Run OCR
                </>
              )}
            </button>
          )}
          <a
            href={api.documents.getFileUrl(documentId)}
            download={document.filename}
            className="inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Download
          </a>
          {/* Extract vocabulary — runs Granite over the doc, dedupes,
              adds new acronyms / proper nouns to the user's vocabulary */}
          <button
            data-tour="extract-vocab"
            onClick={handleExtractVocab}
            disabled={extractRunning || document.status !== 'completed'}
            title="Run AI vocabulary extraction over this document. New acronyms / proper nouns get added to your custom vocabulary so future transcriptions recognize them."
            className="inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {extractRunning ? (
              <>
                <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Extracting…
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
                </svg>
                Extract vocabulary
              </>
            )}
          </button>
          {/* Notes toggle button */}
          <button
            data-tour="notes-toggle"
            onClick={() => setShowNotes(!showNotes)}
            className={`inline-flex items-center gap-2 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
              showNotes
                ? 'text-white bg-blue-600 hover:bg-blue-700'
                : 'text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'
            }`}
          >
            <MessageSquare className="w-4 h-4" />
            Notes
          </button>
        </div>
      </div>

      {/* Content with optional notes panel */}
      <div className="flex gap-4">
        {/* Main document area */}
        <div className={`flex-1 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 overflow-hidden ${showNotes ? 'w-2/3' : 'w-full'}`}>
        {document.status === 'processing' && (
          <div className="p-8 text-center">
            <svg className="w-8 h-8 animate-spin text-blue-500 mx-auto" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <p className="mt-4 text-gray-500 dark:text-gray-400">Processing document...</p>
            <button
              onClick={handleCancelProcessing}
              disabled={cancelling}
              className="mt-4 px-4 py-2 text-sm font-medium text-red-600 hover:text-red-700 border border-red-300 hover:border-red-400 rounded-lg transition-colors disabled:opacity-50"
            >
              {cancelling ? 'Cancelling...' : 'Cancel Processing'}
            </button>
          </div>
        )}

        {document.status === 'failed' && (
          <div className="p-8 text-center">
            <p className="text-red-500">Processing failed: {document.error_message}</p>
            <button
              onClick={() => api.documents.reprocess(documentId)}
              className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90"
            >
              Retry Processing
            </button>
          </div>
        )}

        {document.status === 'cancelled' && (
          <div className="p-8 text-center">
            <p className="text-amber-500">Processing was cancelled</p>
            <button
              onClick={() => api.documents.reprocess(documentId)}
              className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90"
            >
              Retry Processing
            </button>
          </div>
        )}

        {document.status === 'completed' && (
          <>
            {isPdf && (
              <div className="overflow-auto h-[calc(100vh-260px)]">
                <div className="h-[calc(100vh-280px)]">
                  <PDFViewer
                    url={api.documents.getFileUrl(documentId, true)}
                    onSelectionNote={handleSelectionNote}
                    currentPage={currentPdfPage}
                    onPageChange={setCurrentPdfPage}
                    highlightText={highlightText}
                    onHighlightCleared={() => setHighlightText(null)}
                  />
                </div>
                {/* OCR Results - synced with current page */}
                {ocrContent && (
                  <div className="border-t border-gray-200 dark:border-gray-700 p-4">
                    <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1 flex items-center gap-2">
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      Extracted Text (Page {currentPdfPage})
                    </h3>
                    {!!(document.metadata?.ocr_model_label || document.metadata?.ocr_duration_s) && (
                      <p className="text-xs text-gray-400 dark:text-gray-500 mb-3 ml-6">
                        {!!document.metadata.ocr_model_label && <span>{String(document.metadata.ocr_model_label)}</span>}
                        {!!(document.metadata.ocr_model_label && document.metadata.ocr_duration_s) && <span> · </span>}
                        {!!document.metadata.ocr_duration_s && <span>{Number(document.metadata.ocr_duration_s)}s</span>}
                      </p>
                    )}
                    <div className="bg-gray-50 dark:bg-gray-900 rounded-lg p-4">
                      {currentPageOcrText ? (
                        <pre className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap font-mono">
                          {currentPageOcrText}
                        </pre>
                      ) : (
                        <p className="text-sm text-gray-500 dark:text-gray-400 italic">
                          No extracted text for this page
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {isImage && (
              <div className="p-4 flex flex-col items-center">
                <img
                  src={api.documents.getFileUrl(documentId, true)}
                  alt={document.title}
                  className="max-w-full max-h-[60vh] object-contain"
                />
                {/* OCR Results */}
                {ocrContent && (
                  <div className="w-full mt-6 border-t border-gray-200 dark:border-gray-700 pt-6">
                    <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1 flex items-center gap-2">
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      Extracted Text (OCR)
                    </h3>
                    {!!(document.metadata?.ocr_model_label || document.metadata?.ocr_duration_s) && (
                      <p className="text-xs text-gray-400 dark:text-gray-500 mb-3 ml-6">
                        {!!document.metadata.ocr_model_label && <span>{String(document.metadata.ocr_model_label)}</span>}
                        {!!(document.metadata.ocr_model_label && document.metadata.ocr_duration_s) && <span> · </span>}
                        {!!document.metadata.ocr_duration_s && <span>{Number(document.metadata.ocr_duration_s)}s</span>}
                      </p>
                    )}
                    <div className="bg-gray-50 dark:bg-gray-900 rounded-lg p-4 max-h-[40vh] overflow-auto">
                      <pre className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap font-mono">
                        {ocrContent}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            )}

            {isDocx && (
              <div className="h-[calc(100vh-260px)]">
                <DOCXViewer
                  documentId={documentId}
                  onSelectionNote={handleSelectionNote}
                  highlightText={highlightText}
                  onHighlightCleared={() => setHighlightText(null)}
                />
              </div>
            )}

            {isXlsx && (
              <div className="relative">
                {officeLoading && (
                  <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-gray-800 z-10">
                    <svg className="w-8 h-8 animate-spin text-blue-500" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  </div>
                )}
                {sheets.length > 0 && (
                  <div className="h-[calc(100vh-260px)] flex flex-col">
                    {/* Sheet tabs */}
                    {sheets.length > 1 && (
                      <div className="flex border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
                        {sheets.map((sheet, idx) => (
                          <button
                            key={sheet.name}
                            onClick={() => setActiveSheet(idx)}
                            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                              activeSheet === idx
                                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                                : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                            }`}
                          >
                            {sheet.name}
                          </button>
                        ))}
                      </div>
                    )}
                    {/* Sheet content */}
                    <div className="flex-1 overflow-auto">
                      <table className="w-full text-sm">
                        <thead className="bg-gray-100 dark:bg-gray-700 sticky top-0">
                          <tr>
                            {sheets[activeSheet]?.headers.map((h, i) => (
                              <th
                                key={i}
                                className="px-3 py-2 text-left font-semibold text-gray-700 dark:text-gray-300 border-b border-r border-gray-200 dark:border-gray-600"
                              >
                                {h}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {sheets[activeSheet]?.rows.map((row, rowIdx) => (
                            <tr key={rowIdx} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                              {row.map((cell, cellIdx) => (
                                <td
                                  key={cellIdx}
                                  className="px-3 py-2 border-b border-r border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100"
                                >
                                  {cell}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )}

            {isPptx && (
              <div className="relative">
                {officeLoading && (
                  <div className="absolute inset-0 flex items-center justify-center bg-white dark:bg-gray-800 z-10">
                    <svg className="w-8 h-8 animate-spin text-blue-500" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  </div>
                )}
                {slides.length > 0 && (
                  <div className="h-[calc(100vh-260px)] flex">
                    {/* Slide thumbnails */}
                    <div className="w-48 border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 overflow-y-auto flex-shrink-0">
                      {slides.map((slide, idx) => (
                        <button
                          key={idx}
                          onClick={() => setActiveSlide(idx)}
                          className={`w-full p-3 text-left border-b border-gray-200 dark:border-gray-700 transition-colors ${
                            activeSlide === idx
                              ? 'bg-blue-100 dark:bg-blue-900/30'
                              : 'hover:bg-gray-100 dark:hover:bg-gray-800'
                          }`}
                        >
                          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                            Slide {slide.index}
                          </div>
                          <div className="text-sm text-gray-700 dark:text-gray-300 line-clamp-2">
                            {slide.texts[0] || '(No text)'}
                          </div>
                        </button>
                      ))}
                    </div>
                    {/* Slide content */}
                    <div className="flex-1 p-8 overflow-auto">
                      <div className="max-w-3xl mx-auto bg-white dark:bg-gray-800 rounded-lg shadow-lg p-8 min-h-[400px]">
                        {slides[activeSlide]?.texts.map((text, idx) => (
                          <p
                            key={idx}
                            className={`mb-4 ${
                              idx === 0
                                ? 'text-2xl font-bold text-gray-900 dark:text-gray-100'
                                : 'text-lg text-gray-700 dark:text-gray-300'
                            }`}
                          >
                            {text}
                          </p>
                        ))}
                        {slides[activeSlide]?.texts.length === 0 && (
                          <p className="text-gray-400 dark:text-gray-500 italic">
                            This slide has no text content
                          </p>
                        )}
                      </div>
                      {/* Navigation */}
                      <div className="flex justify-center gap-4 mt-6">
                        <button
                          onClick={() => setActiveSlide(Math.max(0, activeSlide - 1))}
                          disabled={activeSlide === 0}
                          className="px-4 py-2 rounded-lg bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 disabled:opacity-50"
                        >
                          Previous
                        </button>
                        <span className="px-4 py-2 text-gray-600 dark:text-gray-400">
                          {activeSlide + 1} / {slides.length}
                        </span>
                        <button
                          onClick={() => setActiveSlide(Math.min(slides.length - 1, activeSlide + 1))}
                          disabled={activeSlide === slides.length - 1}
                          className="px-4 py-2 rounded-lg bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 disabled:opacity-50"
                        >
                          Next
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {!isPdf && !isImage && !isDocx && !isXlsx && !isPptx && content && (
              <div className="p-6 prose dark:prose-invert max-w-none overflow-auto max-h-[calc(100vh-260px)]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
              </div>
            )}
          </>
        )}

        {document.status === 'pending' && (
          <div className="p-8 text-center">
            <p className="text-gray-500 dark:text-gray-400">Document queued for processing...</p>
          </div>
        )}
        </div>

        {/* Notes Panel Sidebar */}
        {showNotes && (
          <div className="w-80 flex-shrink-0 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 overflow-hidden h-[calc(100vh-260px)]">
            <NotesPanel
              documentId={documentId}
              currentPage={currentPdfPage}
              onNavigateToAnchor={handleNavigateToAnchor}
              onClose={() => setShowNotes(false)}
              pendingAnchor={pendingAnchor}
              onPendingAnchorUsed={() => setPendingAnchor(null)}
            />
          </div>
        )}
      </div>

    </div>
  );
}
