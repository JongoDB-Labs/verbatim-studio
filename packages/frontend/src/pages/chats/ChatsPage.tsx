import { useCallback } from 'react';
import { api, type ConversationDetail } from '@/lib/api';
import { useConversations, useDeleteConversation } from '@/hooks';
import { formatDate } from '@/lib/utils';
import type { ChatMessage } from '@/components/ai/ChatMessages';
import { useProjectStore } from '@/stores/projectStore';

interface ChatsPageProps {
  onLoadConversation: (messages: ChatMessage[], compressedMemory?: string | null) => void;
  onOpenChat: () => void;
}

export function ChatsPage({ onLoadConversation, onOpenChat }: ChatsPageProps) {
  const { activeProject } = useProjectStore();
  const { data: conversationsData, isLoading: loading, error: fetchError, refetch } = useConversations();
  const conversations = conversationsData?.items ?? [];
  const error = fetchError?.message;

  const deleteConversation = useDeleteConversation();

  const handleContinue = useCallback(async (conversationId: string) => {
    try {
      const detail: ConversationDetail = await api.conversations.get(conversationId);
      const messages: ChatMessage[] = detail.messages.map(m => ({
        id: m.id,
        role: m.role,
        content: m.content,
      }));
      onLoadConversation(messages, detail.compressed_memory);
      onOpenChat();
    } catch {
      alert('Failed to load conversation');
    }
  }, [onLoadConversation, onOpenChat]);

  const handleDelete = useCallback(async (conversationId: string) => {
    if (!confirm('Delete this conversation? This cannot be undone.')) return;
    deleteConversation.mutate(conversationId);
  }, [deleteConversation]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <p className="text-red-500">{error}</p>
        <button
          onClick={() => refetch()}
          className="mt-4 px-4 py-2 text-sm font-medium text-blue-600 hover:text-blue-800"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Saved Chats</h1>
            {activeProject && (
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
                {activeProject.color && (
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: activeProject.color }} />
                )}
                {activeProject.name}
              </span>
            )}
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Your saved conversations with Max
          </p>
        </div>
        <button
          onClick={onOpenChat}
          className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          New Chat
        </button>
      </div>

      {conversations.length === 0 ? (
        <div className="text-center py-16 bg-gray-50 dark:bg-gray-800/50 rounded-xl border border-gray-200 dark:border-gray-700">
          <svg className="w-16 h-16 mx-auto text-gray-300 dark:text-gray-600 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-2">
            No saved chats yet
          </h3>
          <p className="text-gray-500 dark:text-gray-400 mb-6 max-w-sm mx-auto">
            Start a conversation with Max and save it to access it later.
          </p>
          <button
            onClick={onOpenChat}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            Start a conversation
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {conversations.map((conversation) => (
            <div
              key={conversation.id}
              className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 hover:border-gray-300 dark:hover:border-gray-600 transition-colors"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <h3 className="font-medium text-gray-900 dark:text-gray-100 truncate">
                    {conversation.title || 'Untitled Conversation'}
                  </h3>
                  {conversation.last_message_preview && (
                    <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">
                      {conversation.last_message_preview}
                    </p>
                  )}
                  <div className="flex items-center gap-3 mt-2 text-xs text-gray-400 dark:text-gray-500">
                    <span>{formatDate(conversation.created_at)}</span>
                    <span className="flex items-center gap-1">
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                      </svg>
                      {conversation.message_count} messages
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleContinue(conversation.id)}
                    className="px-3 py-1.5 text-sm font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-md transition-colors"
                  >
                    Continue
                  </button>
                  <button
                    onClick={() => handleDelete(conversation.id)}
                    disabled={deleteConversation.isPending && deleteConversation.variables === conversation.id}
                    className="p-1.5 text-gray-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md transition-colors disabled:opacity-50"
                    aria-label="Delete conversation"
                  >
                    {deleteConversation.isPending && deleteConversation.variables === conversation.id ? (
                      <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                    ) : (
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
