import { useEffect, useRef } from 'react';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  webSources?: Array<{ title: string; url: string }>;
}

interface ChatMessagesProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  streamingContent: string;
}

export function ChatMessages({ messages, isStreaming, streamingContent }: ChatMessagesProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  if (messages.length === 0 && !isStreaming) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 text-center">
        <div className="space-y-2">
          <div className="w-12 h-12 mx-auto rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
            <svg className="w-6 h-6 text-blue-600 dark:text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
          </div>
          <h3 className="font-medium text-gray-900 dark:text-gray-100">Hi, I'm Max!</h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 max-w-xs">
            I can help you analyze your transcripts. Attach some transcripts or ask me anything!
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      {messages.map((msg) => (
        <div
          key={msg.id}
          className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          aria-label={msg.role === 'user' ? 'You' : 'Max'}
        >
          <div
            className={`max-w-[80%] rounded-lg px-4 py-2 break-words ${
              msg.role === 'user'
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 dark:bg-gray-700 text-gray-900 dark:text-gray-100'
            }`}
          >
            <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
            {msg.role === 'assistant' && msg.webSources && msg.webSources.length > 0 && (
              <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-600">
                <p className="text-xs text-gray-400 dark:text-gray-500 mb-1">Sources:</p>
                <div className="flex flex-wrap gap-1.5">
                  {msg.webSources.map((source, i) => (
                    <a
                      key={i}
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-blue-500 hover:text-blue-600 dark:text-blue-400 dark:hover:text-blue-300 underline"
                    >
                      {source.title}
                    </a>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      ))}
      {/* Thinking indicator - shows while waiting for response */}
      {isStreaming && !streamingContent && (
        <div className="flex justify-start" aria-label="Max is thinking">
          <div className="rounded-lg px-4 py-3 bg-gray-100 dark:bg-gray-700" aria-live="polite">
            <div className="flex items-center gap-1">
              <span className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce [animation-delay:-0.3s]" />
              <span className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce [animation-delay:-0.15s]" />
              <span className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" />
            </div>
          </div>
        </div>
      )}
      {/* Streaming content - shows while receiving response */}
      {isStreaming && streamingContent && (
        <div className="flex justify-start" aria-label="Max is typing">
          <div className="max-w-[80%] rounded-lg px-4 py-2 break-words bg-gray-100 dark:bg-gray-700 text-gray-900 dark:text-gray-100" aria-live="polite">
            <p className="text-sm whitespace-pre-wrap">{streamingContent}</p>
            <span className="inline-block w-2 h-4 ml-1 bg-gray-400 animate-pulse" />
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
