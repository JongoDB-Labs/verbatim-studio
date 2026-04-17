import { useState, useEffect } from 'react';
import { api, CalendarEvent } from '@/lib/api';

export function UpcomingEventsCard() {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [needsAuth, setNeedsAuth] = useState(false);

  useEffect(() => {
    loadEvents();
  }, []);

  const loadEvents = async () => {
    setLoading(true);
    try {
      const data = await api.calendar.events(5);
      setEvents(data);
      setError(null);
      setNeedsAuth(false);
    } catch (err: any) {
      if (err?.message?.includes('401') || err?.message?.includes('422') || err?.message?.includes('credentials')) {
        setNeedsAuth(true);
      } else {
        setError('Could not load calendar');
      }
    } finally {
      setLoading(false);
    }
  };

  // Don't render anything if auth isn't configured (keep dashboard clean)
  if (needsAuth || error) return null;
  if (!loading && events.length === 0) return null;

  const formatTime = (timeStr: string | null) => {
    if (!timeStr) return '';
    try {
      const d = new Date(timeStr);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  const formatDate = (timeStr: string | null) => {
    if (!timeStr) return '';
    try {
      const d = new Date(timeStr);
      const today = new Date();
      const tomorrow = new Date(today);
      tomorrow.setDate(tomorrow.getDate() + 1);

      if (d.toDateString() === today.toDateString()) return 'Today';
      if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
      return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    } catch {
      return '';
    }
  };

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
      <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-2">
        <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
        <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">Upcoming Meetings</h3>
      </div>
      <div className="divide-y divide-gray-100 dark:divide-gray-700">
        {loading ? (
          <div className="px-5 py-4 text-sm text-gray-500 dark:text-gray-400">Loading...</div>
        ) : (
          events.map((event) => (
            <div key={event.id} className="px-5 py-3 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{event.title}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  {formatDate(event.start_time)} {formatTime(event.start_time)}
                  {event.end_time && ` – ${formatTime(event.end_time)}`}
                </p>
                {event.attendees && event.attendees.length > 0 && (
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5 truncate">
                    {event.attendees.slice(0, 3).join(', ')}
                    {event.attendees.length > 3 && ` +${event.attendees.length - 3}`}
                  </p>
                )}
              </div>
              {event.has_video_link && event.meeting_url && (
                <a
                  href={event.meeting_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex-shrink-0 inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-md bg-green-50 text-green-700 hover:bg-green-100 dark:bg-green-900/20 dark:text-green-300 dark:hover:bg-green-900/30"
                >
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                  </svg>
                  Join
                </a>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
