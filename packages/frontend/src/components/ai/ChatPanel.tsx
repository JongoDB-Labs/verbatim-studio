import { useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { useProjectStore } from '@/stores/projectStore';
import { ChatHeader } from './ChatHeader';
import { ChatMessages, type ChatMessage } from './ChatMessages';
import { ChatInput } from './ChatInput';
import { AttachmentPicker, type ChatAttachment } from './AttachmentPicker';
import { VoiceChatPanel, type VoiceTranscriptMessage } from './VoiceChatPanel';

interface ChatPanelProps {
  isOpen: boolean;
  onClose: () => void;
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  attached: ChatAttachment[];
  setAttached: React.Dispatch<React.SetStateAction<ChatAttachment[]>>;
  onNavigateToChats?: () => void;
  compressedMemory: string | null;
  setCompressedMemory: React.Dispatch<React.SetStateAction<string | null>>;
}

export function ChatPanel({
  isOpen,
  onClose,
  messages,
  setMessages,
  attached,
  setAttached,
  onNavigateToChats,
  compressedMemory,
  setCompressedMemory,
}: ChatPanelProps) {
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');
  const [streamingWebSources, setStreamingWebSources] = useState<Array<{ title: string; url: string }>>([]);
  const [toolActivities, setToolActivities] = useState<Array<{ name: string; args?: Record<string, unknown>; summary?: string; status: 'running' | 'complete' }>>([]);
  const [streamingArtifacts, setStreamingArtifacts] = useState<Array<{ type: string; url: string; filename: string }>>([]);
  const [showPicker, setShowPicker] = useState(false);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveTitle, setSaveTitle] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [generalMode, setGeneralMode] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const { selectedProjects } = useProjectStore();

  const handleSend = useCallback(async (message: string) => {
    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: message,
    };
    setMessages((prev) => [...prev, userMessage]);
    setIsStreaming(true);
    setStreamingContent('');
    setStreamingWebSources([]);
    setToolActivities([]);
    setStreamingArtifacts([]);

    try {
      const history = messages.map((m) => ({ role: m.role, content: m.content }));

      // Separate attachments by type
      const recordingIds: string[] = [];
      const documentIds: string[] = [];
      const fileTexts: string[] = [];

      for (const a of attached) {
        if (a.type === 'transcript' && a.recordingId) {
          recordingIds.push(a.recordingId);
        } else if (a.type === 'document' && a.documentId) {
          documentIds.push(a.documentId);
        } else if (a.type === 'file' && a.fileText) {
          fileTexts.push(`=== ${a.title} ===\n${a.fileText}`);
        }
      }

      let fullContent = '';
      let currentWebSources: Array<{ title: string; url: string }> | undefined;

      for await (const token of api.ai.chatMultiStream({
        message,
        recording_ids: recordingIds,
        document_ids: documentIds,
        file_context: fileTexts.length > 0 ? fileTexts.join('\n\n') : undefined,
        history,
        compressed_memory: compressedMemory,
        temperature: 0.7,
        general_mode: generalMode || undefined,
        web_search_enabled: webSearchEnabled || undefined,
      })) {
        if (token.error) {
          throw new Error(token.error);
        }
        if (token.token) {
          fullContent += token.token;
          setStreamingContent(fullContent);
        }
        if (token.compressed_memory !== undefined) {
          setCompressedMemory(token.compressed_memory);
        }
        if (token.web_sources) {
          currentWebSources = token.web_sources;
          setStreamingWebSources(token.web_sources);
        }
        if (token.tool_call) {
          setToolActivities((prev) => [
            ...prev,
            { name: token.tool_call!.name, args: token.tool_call!.args, status: 'running' as const },
          ]);
        }
        if (token.tool_result) {
          setToolActivities((prev) =>
            prev.map((a) =>
              a.name === token.tool_result!.name && a.status === 'running'
                ? { ...a, summary: token.tool_result!.summary, status: 'complete' as const }
                : a
            )
          );
        }
        if (token.artifacts) {
          setStreamingArtifacts(token.artifacts);
        }
        if (token.done) {
          const finalArtifacts = token.artifacts || (streamingArtifacts.length > 0 ? streamingArtifacts : undefined);
          const finalToolCalls = toolActivities.length > 0
            ? toolActivities.map(a => ({ name: a.name, summary: a.summary || '' }))
            : undefined;
          const assistantMessage: ChatMessage = {
            id: crypto.randomUUID(),
            role: 'assistant',
            content: fullContent,
            webSources: currentWebSources,
            artifacts: finalArtifacts,
            toolCalls: finalToolCalls,
          };
          setMessages((prev) => [...prev, assistantMessage]);
          setStreamingContent('');
        }
      }
    } catch (error) {
      console.error('Chat error:', error);
      const errorMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: 'Sorry, I encountered an error. Please try again.',
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsStreaming(false);
      setStreamingContent('');
      setStreamingWebSources([]);
      setToolActivities([]);
      setStreamingArtifacts([]);
    }
  }, [messages, attached, setMessages, generalMode, webSearchEnabled, compressedMemory, setCompressedMemory]);

  const handleAttach = useCallback((attachment: ChatAttachment) => {
    setAttached((prev) => [...prev, attachment]);
  }, [setAttached]);

  const handleDetach = useCallback((id: string) => {
    setAttached((prev) => prev.filter((a) => a.id !== id));
  }, [setAttached]);

  const handleClear = useCallback(() => {
    if (messages.length === 0 && attached.length === 0) return;

    if (confirm('Clear this conversation? This cannot be undone.')) {
      setMessages([]);
      setAttached([]);
      setStreamingContent('');
      setCompressedMemory(null);
    }
  }, [messages.length, attached.length, setMessages, setAttached, setCompressedMemory]);

  const handleSave = useCallback(async () => {
    if (messages.length === 0) {
      alert('Nothing to save. Start a conversation first.');
      return;
    }

    setShowSaveDialog(true);
    // Generate default title from first user message
    const firstUserMsg = messages.find(m => m.role === 'user');
    if (firstUserMsg) {
      const defaultTitle = firstUserMsg.content.slice(0, 50) + (firstUserMsg.content.length > 50 ? '...' : '');
      setSaveTitle(defaultTitle);
    }
  }, [messages]);

  const handleSaveConfirm = useCallback(async () => {
    if (isSaving) return;

    setIsSaving(true);
    try {
      await api.conversations.create({
        title: saveTitle || undefined,
        messages: messages.map(m => ({ role: m.role, content: m.content })),
        compressed_memory: compressedMemory,
        project_id: selectedProjects[0]?.id ?? undefined,
      });
      setShowSaveDialog(false);
      setSaveTitle('');
      alert('Conversation saved!');
    } catch (error) {
      console.error('Save error:', error);
      alert('Failed to save conversation.');
    } finally {
      setIsSaving(false);
    }
  }, [messages, saveTitle, isSaving, compressedMemory, selectedProjects]);

  const handleViewHistory = useCallback(() => {
    onClose();
    onNavigateToChats?.();
  }, [onClose, onNavigateToChats]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-white dark:bg-gray-800 flex flex-col animate-in slide-in-from-bottom-4 fade-in duration-200 sm:inset-auto sm:bottom-24 sm:right-6 sm:w-[400px] sm:h-[500px] sm:rounded-xl sm:shadow-2xl sm:border sm:border-gray-200 sm:dark:border-gray-700">
      <ChatHeader
        attached={attached}
        onDetach={handleDetach}
        onClose={onClose}
        onClear={handleClear}
        onSave={handleSave}
        onViewHistory={onNavigateToChats ? handleViewHistory : undefined}
        hasMessages={messages.length > 0}
        generalMode={generalMode}
        onToggleGeneralMode={() => setGeneralMode((prev) => !prev)}
        webSearchEnabled={webSearchEnabled}
        onToggleWebSearch={() => setWebSearchEnabled((prev) => !prev)}
        voiceActive={false}
        onToggleVoice={undefined}
      />
      {voiceMode ? (
        <VoiceChatPanel onClose={(voiceMessages?: VoiceTranscriptMessage[]) => {
          setVoiceMode(false);
          // Inject voice conversation into chat history
          if (voiceMessages && voiceMessages.length > 0) {
            const chatMessages: ChatMessage[] = voiceMessages.map((m, i) => ({
              id: `voice-${Date.now()}-${i}`,
              role: m.role,
              content: m.content,
            }));
            setMessages((prev) => [...prev, ...chatMessages]);
          }
        }} />
      ) : (
        <>
          <ChatMessages
            messages={messages}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            streamingWebSources={streamingWebSources}
            toolActivities={toolActivities}
          />
          <div className="relative">
            {showPicker && (
              <AttachmentPicker
                attached={attached}
                onAttach={handleAttach}
                onDetach={handleDetach}
                onClose={() => setShowPicker(false)}
              />
            )}
            <ChatInput
              onSend={handleSend}
              onAttachClick={() => setShowPicker(!showPicker)}
              disabled={isStreaming}
              attachedCount={attached.length}
              onMicClick={() => setVoiceMode(true)}
              voiceActive={voiceMode}
            />
          </div>
        </>
      )}

      {/* Save Dialog */}
      {showSaveDialog && (
        <div className="absolute inset-0 bg-black/50 flex items-center justify-center rounded-xl z-50">
          <div className="bg-white dark:bg-gray-800 rounded-lg p-4 w-80 shadow-xl">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">
              Save Conversation
            </h3>
            <input
              type="text"
              value={saveTitle}
              onChange={(e) => setSaveTitle(e.target.value)}
              placeholder="Enter a title..."
              className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:border-blue-500 focus:outline-none mb-3"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowSaveDialog(false)}
                className="px-3 py-1.5 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveConfirm}
                disabled={isSaving}
                className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50"
              >
                {isSaving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
