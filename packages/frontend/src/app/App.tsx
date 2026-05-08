import React, { useCallback, useEffect, useRef, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DataSyncProvider } from '@/hooks/useDataSync';
import { api, getApiUrl, isElectron, onAuthRequired, type ApiInfo, type HealthStatus, type GlobalSearchResult, type SystemInfo, type PluginManifest } from '@/lib/api';
import { LoginPage } from '@/pages/auth/LoginPage';
import { usePluginManifest, PluginManifestContext } from '@/hooks/usePluginManifest';
import { RecordingsPage } from '@/pages/recordings/RecordingsPage';
import { ProjectsPage } from '@/pages/projects/ProjectsPage';
import { ProjectHomePage } from '@/pages/projects/ProjectHomePage';
import { ProjectAnalyticsPage } from '@/pages/projects/ProjectAnalyticsPage';
import { TranscriptPage } from '@/pages/transcript/TranscriptPage';
import { SearchPage } from '@/pages/search/SearchPage';
import { DocumentsPage } from '@/pages/documents/DocumentsPage';
import { DocumentViewerPage } from '@/pages/documents/DocumentViewerPage';
import { FileBrowserPage } from '@/pages/browser/FileBrowserPage';
import { LiveTranscriptionPage } from '@/pages/live/LiveTranscriptionPage';
import { SearchBox } from '@/components/search/SearchBox';
import { Dashboard } from '@/components/dashboard/Dashboard';
import { SettingsPage } from '@/pages/settings/SettingsPage';
import { Sidebar } from '@/components/layout/Sidebar';
import { TitleBar } from '@/components/layout/TitleBar';
import { APP_VERSION } from '@/version'; // static fallback
import { ChatFAB } from '@/components/ai/ChatFAB';
import { ChatPanel } from '@/components/ai/ChatPanel';
import type { ChatMessage } from '@/components/ai/ChatMessages';
import type { ChatAttachment } from '@/components/ai/AttachmentPicker';
import { ChatsPage } from '@/pages/chats/ChatsPage';
import { ArchivePage } from '@/pages/archive/ArchivePage';
import { OnboardingTour, WelcomeModal, SampleWorkspaceModal, TourToast, TOUR_STORAGE_KEYS } from '@/components/onboarding';
import { UpdatePrompt, WhatsNewDialog } from '@/components/updates';
import { useProjectStore } from '@/stores/projectStore';

// Check if running on macOS in Electron (for title bar padding)
const isMacOS = typeof window !== 'undefined' && (window as any).electronAPI?.platform === 'darwin';
const needsTitleBarPadding = isElectron() && isMacOS;

type NavigationState =
  | { type: 'dashboard' }
  | { type: 'recordings' }
  | { type: 'projects' }
  | { type: 'project-detail'; projectId: string }
  | { type: 'project-analytics'; projectId: string }
  | { type: 'live' }
  | { type: 'search' }
  | { type: 'settings' }
  | { type: 'transcript'; recordingId: string; initialSeekTime?: number }
  | { type: 'documents' }
  | { type: 'document-viewer'; documentId: string }
  | { type: 'browser'; folderId?: string | null }
  | { type: 'chats' }
  | { type: 'archive' }
  | { type: 'plugin'; pluginRoute: string };

// Map navigation state to URL path
function navigationToPath(nav: NavigationState): string {
  switch (nav.type) {
    case 'dashboard': return '/';
    case 'recordings': return '/recordings';
    case 'projects': return '/projects';
    case 'project-detail': return `/projects/${nav.projectId}`;
    case 'project-analytics': return `/projects/${nav.projectId}/analytics`;
    case 'live': return '/live';
    case 'search': return '/search';
    case 'settings': return '/settings';
    case 'transcript': return `/recordings/${nav.recordingId}`;
    case 'documents': return '/documents';
    case 'document-viewer': return `/documents/${nav.documentId}`;
    case 'browser': return nav.folderId ? `/browser/${nav.folderId}` : '/browser';
    case 'chats': return '/chats';
    case 'archive': return '/archive';
    case 'plugin': return `/plugins${nav.pluginRoute}`;
  }
}

// Parse URL path to navigation state
function pathToNavigation(path: string): NavigationState {
  // Remove trailing slash
  const cleanPath = path.replace(/\/$/, '') || '/';

  if (cleanPath === '/' || cleanPath === '') return { type: 'dashboard' };
  if (cleanPath === '/recordings') return { type: 'recordings' };
  if (cleanPath === '/projects') return { type: 'projects' };
  if (cleanPath === '/live') return { type: 'live' };
  if (cleanPath === '/search') return { type: 'search' };
  if (cleanPath === '/settings') return { type: 'settings' };
  if (cleanPath === '/chats') return { type: 'chats' };
  if (cleanPath === '/archive') return { type: 'archive' };

  // /recordings/:id -> transcript
  const recordingMatch = cleanPath.match(/^\/recordings\/([^/]+)$/);
  if (recordingMatch) return { type: 'transcript', recordingId: recordingMatch[1] };

  // /projects/:id/analytics -> project analytics
  const analyticsMatch = cleanPath.match(/^\/projects\/([^/]+)\/analytics$/);
  if (analyticsMatch) return { type: 'project-analytics', projectId: analyticsMatch[1] };

  // /projects/:id -> project detail
  const projectMatch = cleanPath.match(/^\/projects\/([^/]+)$/);
  if (projectMatch) return { type: 'project-detail', projectId: projectMatch[1] };

  if (cleanPath === '/documents') return { type: 'documents' };

  const documentMatch = cleanPath.match(/^\/documents\/([^/]+)$/);
  if (documentMatch) return { type: 'document-viewer', documentId: documentMatch[1] };

  if (cleanPath === '/browser') return { type: 'browser' };

  const browserMatch = cleanPath.match(/^\/browser\/([^/]+)$/);
  if (browserMatch) return { type: 'browser', folderId: browserMatch[1] };

  // /plugins/... -> plugin page
  const pluginMatch = cleanPath.match(/^\/plugins(\/.*)?$/);
  if (pluginMatch) return { type: 'plugin', pluginRoute: pluginMatch[1] || '/' };

  // Default to dashboard for unknown paths
  return { type: 'dashboard' };
}

type Theme = 'light' | 'dark' | 'system';

function getInitialTheme(): Theme {
  if (typeof window === 'undefined') return 'system';
  const stored = localStorage.getItem('theme');
  if (stored === 'light' || stored === 'dark' || stored === 'system') {
    return stored;
  }
  return 'system';
}

function getEffectiveDarkMode(theme: Theme): boolean {
  if (theme === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  return theme === 'dark';
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,        // Data considered fresh for 30s
      gcTime: 5 * 60_000,       // Cache garbage collected after 5 min
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

// Plugin module cache: avoids recreating React.lazy components on every render
const pluginModuleCache = new Map<string, React.LazyExoticComponent<React.ComponentType<{ route: string }>>>();

function getPluginModule(moduleUrl: string): React.LazyExoticComponent<React.ComponentType<{ route: string }>> {
  if (!pluginModuleCache.has(moduleUrl)) {
    pluginModuleCache.set(moduleUrl, React.lazy(() => import(/* @vite-ignore */ moduleUrl)));
  }
  return pluginModuleCache.get(moduleUrl)!;
}

function getPluginRouteConfig(route: string, manifest: PluginManifest): { renderMode: 'iframe' | 'module'; moduleUrl?: string } {
  for (const r of manifest.routes) {
    if (typeof r === 'string') {
      if (r === route || route.startsWith(r + '/')) {
        return { renderMode: 'iframe' };
      }
    } else {
      if (r.path === route || route.startsWith(r.path + '/')) {
        return { renderMode: r.renderMode, moduleUrl: r.moduleUrl };
      }
    }
  }
  return { renderMode: 'iframe' };
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppContent />
    </QueryClientProvider>
  );
}

function AppContent() {
  const [apiInfo, setApiInfo] = useState<ApiInfo | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isConnecting, setIsConnecting] = useState(true);
  const [needsAuth, setNeedsAuth] = useState(false);
  const [navigation, setNavigation] = useState<NavigationState>(() =>
    pathToNavigation(window.location.pathname)
  );
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  // Chat assistant state
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatAttachments, setChatAttachments] = useState<ChatAttachment[]>([]);
  const [chatCompressedMemory, setChatCompressedMemory] = useState<string | null>(null);

  // Sidebar collapsed state (persisted to localStorage)
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return localStorage.getItem('sidebar-collapsed') === 'true';
  });

  // Selected projects (for clearing chat on switch)
  const selectedProjects = useProjectStore(state => state.selectedProjects);
  const selectedProjectKey = selectedProjects.map(p => p.id).sort().join(',');
  const isInitialMountRef = useRef(true);

  useEffect(() => {
    // Skip first run — don't clear chat when the component mounts
    // (selectedProjects may already be hydrated from localStorage)
    if (isInitialMountRef.current) {
      isInitialMountRef.current = false;
      return;
    }
    // Clear chat state to prevent context bleed between projects
    setChatMessages([]);
    setChatAttachments([]);
    setChatCompressedMemory(null);
    setIsChatOpen(false);
  }, [selectedProjectKey]);

  // Onboarding tour state
  const [showWelcomeModal, setShowWelcomeModal] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    const completed = localStorage.getItem(TOUR_STORAGE_KEYS.completed);
    const skipped = localStorage.getItem(TOUR_STORAGE_KEYS.skipped);
    return !completed && !skipped;
  });
  const [isTourActive, setIsTourActive] = useState(false);
  const [showTourToast, setShowTourToast] = useState(false);

  // Update dialog state
  const [updateInfo, setUpdateInfo] = useState<{ version: string; downloadUrl: string } | null>(null);
  const [whatsNewReleases, setWhatsNewReleases] = useState<Array<{ version: string; notes: string }> | null>(null);

  const pluginManifest = usePluginManifest();

  // Persist sidebar collapsed state
  useEffect(() => {
    localStorage.setItem('sidebar-collapsed', String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  // Listen for update events from Electron
  useEffect(() => {
    if (!window.electronAPI) return;

    const cleanupAvailable = window.electronAPI.onUpdateAvailable((data) => {
      setUpdateInfo(data);
    });

    const cleanupWhatsNew = window.electronAPI.onShowWhatsNew((data) => {
      setWhatsNewReleases(data.releases);
    });

    return () => {
      cleanupAvailable();
      cleanupWhatsNew();
    };
  }, []);

  const handleViewTranscript = useCallback((recordingId: string) => {
    setNavigation({ type: 'transcript', recordingId });
  }, []);

  const handleNavigateToDashboard = useCallback(() => {
    setNavigation({ type: 'dashboard' });
  }, []);

  const handleNavigateToRecordings = useCallback(() => {
    setNavigation({ type: 'recordings' });
  }, []);

  const handleNavigateToSettings = useCallback(() => {
    setNavigation({ type: 'settings' });
  }, []);

  const handleNavigateToSearch = useCallback(() => {
    setNavigation({ type: 'search' });
  }, []);

  const handleNavigateToLive = useCallback(() => {
    setNavigation({ type: 'live' });
  }, []);

  const handleBackToRecordings = useCallback(() => {
    setNavigation({ type: 'recordings' });
  }, []);

  const handleNavigateToProjects = useCallback(() => {
    setNavigation({ type: 'projects' });
  }, []);

  const handleNavigateToProjectDetail = useCallback((projectId: string) => {
    setNavigation({ type: 'project-detail', projectId });
  }, []);



  const handleNavigateToDocuments = useCallback(() => {
    setNavigation({ type: 'documents' });
  }, []);

  const handleViewDocument = useCallback((documentId: string) => {
    setNavigation({ type: 'document-viewer', documentId });
  }, []);

  const handleNavigateToBrowser = useCallback((folderId?: string | null) => {
    setNavigation({ type: 'browser', folderId });
  }, []);

  const handleNavigateToChats = useCallback(() => {
    setNavigation({ type: 'chats' });
  }, []);

  const handleNavigateToArchive = useCallback(() => {
    setNavigation({ type: 'archive' });
  }, []);

  // Map navigation types to sidebar tabs
  const currentTab = (() => {
    switch (navigation.type) {
      case 'transcript':
        return 'recordings';
      case 'project-detail':
      case 'project-analytics':
        return 'projects';
      case 'documents':
      case 'document-viewer':
        return 'documents';
      case 'browser':
        return 'browser';
      case 'chats':
        return 'chats';
      case 'archive':
        return 'archive';
      case 'plugin':
        return navigation.pluginRoute.replace(/^\//, '') as string;
      default:
        return navigation.type as 'dashboard' | 'recordings' | 'projects' | 'live' | 'search' | 'settings' | 'documents' | 'browser' | 'chats' | 'archive';
    }
  })();

  const handleSearchResult = useCallback(async (result: GlobalSearchResult) => {
    if (result.type === 'conversation' && result.conversation_id) {
      // Load the conversation and open chat panel
      try {
        const detail = await api.conversations.get(result.conversation_id);
        const messages: ChatMessage[] = detail.messages.map(m => ({
          id: m.id,
          role: m.role,
          content: m.content,
        }));
        setChatMessages(messages);
        setChatAttachments([]);
        setChatCompressedMemory(detail.compressed_memory);
        setIsChatOpen(true);
      } catch {
        console.error('Failed to load conversation');
      }
    } else if (result.type === 'document' && result.document_id) {
      // Navigate to document viewer
      setNavigation({
        type: 'document-viewer',
        documentId: result.document_id,
      });
    } else if (result.recording_id) {
      // Navigate to the recording's transcript, seeking to the segment time if available
      setNavigation({
        type: 'transcript',
        recordingId: result.recording_id,
        initialSeekTime: result.start_time ?? undefined,
      });
    }
  }, []);

  // Sync theme to document and localStorage
  useEffect(() => {
    const effectiveDark = getEffectiveDarkMode(theme);

    if (effectiveDark) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }

    localStorage.setItem('theme', theme);
  }, [theme]);

  // Listen for system theme changes when using 'system' mode
  useEffect(() => {
    if (theme !== 'system') return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = () => {
      if (mediaQuery.matches) {
        document.documentElement.classList.add('dark');
      } else {
        document.documentElement.classList.remove('dark');
      }
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, [theme]);

  const cycleTheme = useCallback(() => {
    setTheme(current => {
      if (current === 'light') return 'dark';
      if (current === 'dark') return 'system';
      return 'light';
    });
  }, []);

  // Sync navigation state to URL
  useEffect(() => {
    const targetPath = navigationToPath(navigation);
    if (window.location.pathname !== targetPath) {
      window.history.replaceState(null, '', targetPath);
    }
  }, [navigation]);

  // Handle browser back/forward navigation
  useEffect(() => {
    const handlePopState = () => {
      setNavigation(pathToNavigation(window.location.pathname));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Register auth callback — when any API call returns 401, show login
  useEffect(() => {
    onAuthRequired(() => setNeedsAuth(true));
  }, []);

  const checkBackend = useCallback(async () => {
    try {
      const [info, healthStatus, sysInfo] = await Promise.all([
        api.info(),
        api.health.ready(),
        api.system.info(),
      ]);
      setApiInfo(info);
      setHealth(healthStatus);
      setSystemInfo(sysInfo);
      setError(null);
      setIsConnecting(false);
    } catch (err) {
      setIsConnecting(false);
      // Don't overwrite needsAuth with a connection error
      setNeedsAuth((current) => {
        if (!current) {
          setError(err instanceof Error ? err.message : 'Failed to connect to backend');
        }
        return current;
      });
    }
  }, []);

  useEffect(() => {
    checkBackend();
    const interval = setInterval(checkBackend, 15000);
    return () => clearInterval(interval);
  }, [checkBackend]);

  // Auto-attach current transcript when opening chat from transcript page
  const handleOpenChat = useCallback(() => {
    if (navigation.type === 'transcript' && !isChatOpen) {
      // Check if this transcript is already attached
      const attachmentId = `transcript-${navigation.recordingId}`;
      const alreadyAttached = chatAttachments.some((a) => a.id === attachmentId);
      if (!alreadyAttached) {
        // Fetch recording title and attach
        api.recordings.get(navigation.recordingId).then((recording) => {
          setChatAttachments((prev) => [
            ...prev,
            {
              id: attachmentId,
              type: 'transcript',
              title: recording.title,
              recordingId: recording.id,
            },
          ]);
        }).catch(() => {});
      }
    }
    setIsChatOpen(true);
  }, [navigation, isChatOpen, chatAttachments]);

  // Load saved conversation into chat
  const handleLoadConversation = useCallback((messages: ChatMessage[], compressedMemory?: string | null) => {
    setChatMessages(messages);
    setChatAttachments([]);
    setChatCompressedMemory(compressedMemory ?? null);
  }, []);

  // Tour handlers
  // Modal sequence on first launch: WelcomeModal → SampleWorkspaceModal
  // → tour overlay. Skipping welcome ends the sequence; skipping the
  // sample workspace just starts the tour without seeded content.
  const [showSampleModal, setShowSampleModal] = useState(false);

  const handleStartTour = useCallback(() => {
    setShowWelcomeModal(false);
    // Show the sample-workspace prompt before starting the tour.
    setShowSampleModal(true);
  }, []);

  const handleInstallSampleWorkspace = useCallback(async () => {
    const result = await api.onboarding.installSampleWorkspace();
    setShowSampleModal(false);
    setIsTourActive(true);
    // Navigate to the seeded primary project so step 1 of the tour
    // points at populated content.
    if (result.primary_project_id) {
      // Project navigation goes through the project store — mirror
      // what happens when the user picks a project in the dropdown.
      try {
        const { useProjectStore } = await import('@/stores/projectStore');
        useProjectStore.getState().setSelectedProjects([{
          id: result.primary_project_id,
          name: 'Tour: Sample Workspace',
        }]);
      } catch {
        // Best-effort — store contract may shift; tour still works
        // without auto-scoping.
      }
    }
  }, []);

  const handleSkipSampleWorkspace = useCallback(() => {
    setShowSampleModal(false);
    setIsTourActive(true);
  }, []);

  const handleTourNavigate = useCallback((target: string) => {
    if (target === 'settings') {
      setNavigation({ type: 'settings' });
      return;
    }
    if (target.startsWith('settings#')) {
      const tab = target.split('#')[1];
      setNavigation({ type: 'settings' });
      // SettingsPage listens for hashchange — short delay so the page
      // mounts before we set the hash.
      setTimeout(() => {
        window.location.hash = tab;
      }, 50);
      return;
    }
    // Top-level pages — let the tour walk through actual surfaces of
    // the app rather than just sidebar tooltips. The full set of nav
    // keys is mirrored from Sidebar's NAV_ITEMS.
    if (target === 'dashboard') {
      setNavigation({ type: 'dashboard' });
      return;
    }
    if (target === 'projects') {
      setNavigation({ type: 'projects' });
      return;
    }
    if (target === 'recordings') {
      setNavigation({ type: 'recordings' });
      return;
    }
    if (target === 'live') {
      setNavigation({ type: 'live' });
      return;
    }
    if (target === 'documents') {
      setNavigation({ type: 'documents' });
      return;
    }
    if (target === 'chats') {
      setNavigation({ type: 'chats' });
      return;
    }
    if (target === 'search') {
      setNavigation({ type: 'search' });
      return;
    }
    if (target === 'browser') {
      setNavigation({ type: 'browser' });
      return;
    }
    if (target === 'archive') {
      setNavigation({ type: 'archive' });
      return;
    }
  }, []);

  const handleTourComplete = useCallback(() => {
    setIsTourActive(false);
    setShowTourToast(true);
  }, []);

  const handleTourSkip = useCallback(() => {
    setIsTourActive(false);
    setShowWelcomeModal(false);
  }, []);

  const handleWelcomeSkip = useCallback(() => {
    localStorage.setItem(TOUR_STORAGE_KEYS.skipped, 'true');
    setShowWelcomeModal(false);
  }, []);

  // Show loading state while connecting
  if (isConnecting) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-4">
          <svg
            className="h-12 w-12 animate-spin text-primary mx-auto"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
          <p className="text-muted-foreground">Connecting to backend...</p>
        </div>
      </div>
    );
  }

  // Show login page when enterprise auth is required
  if (needsAuth) {
    return (
      <LoginPage
        onLoginSuccess={() => {
          setNeedsAuth(false);
          setError(null);
          checkBackend();
        }}
        appName={apiInfo?.name ?? 'Verbatim Studio'}
        mode={apiInfo?.mode}
      />
    );
  }

  // Show error state if connection failed
  if (error && !apiInfo) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-4 max-w-md p-6">
          <div className="rounded-full bg-destructive/10 p-4 w-16 h-16 mx-auto flex items-center justify-center">
            <svg
              className="h-8 w-8 text-destructive"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
          </div>
          <h1 className="text-xl font-semibold text-foreground">Connection Error</h1>
          <p className="text-muted-foreground">{error}</p>
          <p className="text-sm text-muted-foreground">
            Make sure the backend is running on port 52780
          </p>
        </div>
      </div>
    );
  }

  return (
    <DataSyncProvider>
      <PluginManifestContext.Provider value={pluginManifest}>
      <div className="min-h-screen bg-background flex">
          {/* macOS Title Bar (draggable region for window movement) */}
          <TitleBar />

          {/* Sidebar */}
          <Sidebar
            currentTab={currentTab}
            onNavigateToProject={handleNavigateToProjectDetail}
            onNavigate={(tab) => {
              if (tab === 'dashboard') handleNavigateToDashboard();
              else if (tab === 'recordings') handleNavigateToRecordings();
              else if (tab === 'projects') handleNavigateToProjects();
              else if (tab === 'live') handleNavigateToLive();
              else if (tab === 'search') handleNavigateToSearch();
              else if (tab === 'documents') handleNavigateToDocuments();
              else if (tab === 'chats') handleNavigateToChats();
              else if (tab === 'browser') handleNavigateToBrowser();
              else if (tab === 'archive') handleNavigateToArchive();
              else if (tab === 'settings') handleNavigateToSettings();
              else {
                // Plugin nav item
                const matchedRoute = pluginManifest.routes.find((r) =>
                  typeof r === 'string' ? r.includes(tab) : r.path.includes(tab)
                );
                const route = matchedRoute
                  ? (typeof matchedRoute === 'string' ? matchedRoute : matchedRoute.path)
                  : `/${tab}`;
                setNavigation({ type: 'plugin', pluginRoute: route });
              }
            }}
            theme={theme}
            onCycleTheme={cycleTheme}
            version={systemInfo?.app_version || APP_VERSION}
            health={health}
            collapsed={sidebarCollapsed}
            onCollapsedChange={setSidebarCollapsed}
            isTourActive={isTourActive}
            pluginNavItems={pluginManifest.nav_items}
          />

          {/* Content Area - offset by sidebar width on desktop */}
          <div className={`flex-1 flex flex-col h-screen overflow-hidden transition-[margin] duration-300 ${sidebarCollapsed ? 'md:ml-16' : 'md:ml-60'} ${needsTitleBarPadding ? 'pt-9' : ''}`}>
            {/* Content header with search */}
            <header className="h-14 border-b border-border bg-card flex items-center px-4 md:px-6 gap-4 shrink-0">
              <div className="w-8 md:hidden" />
              <div className="flex-1 max-w-md">
                <SearchBox onResultClick={handleSearchResult} />
              </div>
            </header>

            {/* Main Content */}
            <main className="flex-1 overflow-y-auto">
              <div className="container mx-auto px-4 py-8">
                {navigation.type === 'dashboard' && (
                  <Dashboard
                    onNavigateToRecordings={handleNavigateToRecordings}
                    onNavigateToProjects={handleNavigateToProjects}
                    onNavigateToDocuments={handleNavigateToDocuments}
                    onViewRecording={handleViewTranscript}
                    onStartTour={handleStartTour}
                  />
                )}
                {navigation.type === 'recordings' && (
                  <RecordingsPage onViewTranscript={handleViewTranscript} />
                )}
                {navigation.type === 'projects' && (
                  <ProjectsPage onNavigateToProject={handleNavigateToProjectDetail} />
                )}
                {navigation.type === 'project-detail' && (
                  <ProjectHomePage
                    projectId={navigation.projectId}
                    onNavigateToRecordings={handleNavigateToRecordings}
                    onNavigateToDocuments={handleNavigateToDocuments}
                    onViewTranscript={handleViewTranscript}
                    onViewDocument={handleViewDocument}
                  />
                )}
                {navigation.type === 'project-analytics' && (
                  <ProjectAnalyticsPage
                    projectId={navigation.projectId}
                    onBack={() => handleNavigateToProjectDetail(navigation.projectId)}
                  />
                )}
                {navigation.type === 'live' && (
                  <LiveTranscriptionPage
                    onNavigateToRecordings={handleNavigateToRecordings}
                    onViewRecording={handleViewTranscript}
                  />
                )}
                {navigation.type === 'search' && (
                  <SearchPage onResultClick={handleSearchResult} />
                )}
                {navigation.type === 'documents' && (
                  <DocumentsPage onViewDocument={handleViewDocument} />
                )}
                {navigation.type === 'document-viewer' && (
                  <DocumentViewerPage
                    documentId={navigation.documentId}
                    onBack={handleNavigateToDocuments}
                  />
                )}
                {navigation.type === 'browser' && (
                  <FileBrowserPage
                    initialFolderId={navigation.folderId}
                    onViewRecording={(id) => setNavigation({ type: 'transcript', recordingId: id })}
                    onViewDocument={(id) => setNavigation({ type: 'document-viewer', documentId: id })}
                  />
                )}
                {navigation.type === 'archive' && (
                  <ArchivePage />
                )}
                {navigation.type === 'chats' && (
                  <ChatsPage
                    onLoadConversation={handleLoadConversation}
                    onOpenChat={handleOpenChat}
                  />
                )}
                {navigation.type === 'transcript' && (
                  <TranscriptPage
                    recordingId={navigation.recordingId}
                    onBack={handleBackToRecordings}
                    initialSeekTime={navigation.initialSeekTime}
                  />
                )}
                {navigation.type === 'settings' && (
                  <SettingsPage
                    theme={theme}
                    onThemeChange={setTheme}
                    pluginSettingsTabs={pluginManifest.settings_tabs}
                    onNavigateToDocuments={handleNavigateToDocuments}
                  />
                )}
                {navigation.type === 'plugin' && (() => {
                  const routeConfig = getPluginRouteConfig(navigation.pluginRoute, pluginManifest);
                  if (routeConfig.renderMode === 'module' && routeConfig.moduleUrl) {
                    const PluginModule = getPluginModule(routeConfig.moduleUrl);
                    return (
                      <div className="w-full h-full">
                        <React.Suspense fallback={<div className="flex items-center justify-center h-full"><div className="animate-spin h-8 w-8 border-2 border-blue-500 border-t-transparent rounded-full" /></div>}>
                          <PluginModule route={navigation.pluginRoute} />
                        </React.Suspense>
                      </div>
                    );
                  }
                  return (
                    <div className="w-full h-[calc(100vh-8rem)]">
                      <iframe
                        src={getApiUrl(`/plugins${navigation.pluginRoute}`)}
                        className="w-full h-full border-0"
                        title={`Plugin: ${navigation.pluginRoute}`}
                      />
                    </div>
                  );
                })()}
              </div>
            </main>
          </div>

          {/* Chat Assistant */}
          <ChatFAB onClick={handleOpenChat} isOpen={isChatOpen} />
          <ChatPanel
            isOpen={isChatOpen}
            onClose={() => setIsChatOpen(false)}
            messages={chatMessages}
            setMessages={setChatMessages}
            attached={chatAttachments}
            setAttached={setChatAttachments}
            onNavigateToChats={handleNavigateToChats}
            compressedMemory={chatCompressedMemory}
            setCompressedMemory={setChatCompressedMemory}
          />

          {/* Onboarding Tour */}
          <WelcomeModal
            isOpen={showWelcomeModal && !isConnecting && !error}
            onStartTour={handleStartTour}
            onSkip={handleWelcomeSkip}
          />
          <SampleWorkspaceModal
            isOpen={showSampleModal && !isConnecting && !error}
            onInstall={handleInstallSampleWorkspace}
            onSkip={handleSkipSampleWorkspace}
          />
          <OnboardingTour
            isActive={isTourActive}
            onComplete={handleTourComplete}
            onSkip={handleTourSkip}
            onNavigate={handleTourNavigate}
          />
          <TourToast
            isVisible={showTourToast}
            onDismiss={() => setShowTourToast(false)}
          />

          {/* Update Prompt */}
          {updateInfo && (
            <UpdatePrompt
              version={updateInfo.version}
              downloadUrl={updateInfo.downloadUrl}
              onUpdate={() => {/* Download started, prompt handles rest */}}
              onDismiss={() => setUpdateInfo(null)}
            />
          )}

          {/* What's New Dialog */}
          {whatsNewReleases && whatsNewReleases.length > 0 && (
            <WhatsNewDialog
              releases={whatsNewReleases}
              onDismiss={() => setWhatsNewReleases(null)}
            />
          )}
        </div>
      </PluginManifestContext.Provider>
    </DataSyncProvider>
  );
}
