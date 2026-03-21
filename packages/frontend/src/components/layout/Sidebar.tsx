import { useCallback, useEffect, useState } from 'react';
import { isElectron } from '../../lib/api';
import { DownloadIndicator } from '../downloads/DownloadIndicator';
import { TaskIndicator } from '../tasks/TaskIndicator';
import { ProjectSelector } from './ProjectSelector';

// Import icon logo to get correct bundled path (works with both http and file:// protocols)
import logoIcon from '/logo-icon.png';

// Check if running on macOS in Electron (for traffic light padding)
const isMacOS = typeof navigator !== 'undefined' && navigator.platform.toLowerCase().includes('mac');

type Theme = 'light' | 'dark' | 'system';

type NavKey = 'dashboard' | 'recordings' | 'projects' | 'live' | 'search' | 'documents' | 'chats' | 'browser' | 'archive' | 'settings' | (string & {});

interface SidebarProps {
  currentTab: NavKey;
  onNavigate: (tab: NavKey) => void;
  onNavigateToProject?: (projectId: string) => void;
  theme: Theme;
  onCycleTheme: () => void;
  version: string;
  health: { status: string } | null;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  isTourActive?: boolean;
  pluginNavItems?: Array<{ key: string; label: string; icon: string; position: string }>;
}

// Main nav items (top section)
// Order: dashboard, projects, recordings, live, documents, chats, search, files
const NAV_ITEMS = [
  {
    key: 'dashboard' as const,
    label: 'Dashboard',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
      </svg>
    ),
  },
  {
    key: 'projects' as const,
    label: 'Projects',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
      </svg>
    ),
  },
  {
    key: 'recordings' as const,
    label: 'Recordings',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
      </svg>
    ),
  },
  {
    key: 'live' as const,
    label: 'Live',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5.636 18.364a9 9 0 010-12.728m12.728 0a9 9 0 010 12.728m-9.9-2.829a5 5 0 010-7.07m7.072 0a5 5 0 010 7.07M13 12a1 1 0 11-2 0 1 1 0 012 0z" />
      </svg>
    ),
  },
  {
    key: 'documents' as const,
    label: 'Documents',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
  },
  {
    key: 'chats' as const,
    label: 'Chats',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    key: 'search' as const,
    label: 'Search',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
      </svg>
    ),
  },
  {
    key: 'browser' as const,
    label: 'Files',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
      </svg>
    ),
  },
  {
    key: 'archive' as const,
    label: 'Archive',
    icon: (
      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
      </svg>
    ),
  },
];

// Settings icon for bottom section
const SETTINGS_ICON = (
  <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
    <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
    <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
  </svg>
);

export function Sidebar({ currentTab, onNavigate, theme, onCycleTheme, version, health, collapsed, onCollapsedChange, isTourActive, pluginNavItems }: SidebarProps) {
  const [mobileOpen, setMobileOpen] = useState(false);

  // Force mobile sidebar open when tour is active (so tooltips can reference sidebar items)
  useEffect(() => {
    if (isTourActive) {
      setMobileOpen(true);
    }
  }, [isTourActive]);

  // Close mobile drawer on Escape (but not during tour)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isTourActive) setMobileOpen(false);
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isTourActive]);

  const handleNavigate = useCallback((tab: NavKey) => {
    onNavigate(tab);
    setMobileOpen(false);
  }, [onNavigate]);

  return (
    <>
      {/* Mobile hamburger button */}
      <button
        onClick={() => setMobileOpen(true)}
        className="fixed top-2 left-3 z-40 md:hidden min-w-touch min-h-touch flex items-center justify-center rounded-lg bg-card border border-border shadow-sm"
        aria-label="Open navigation"
      >
        <svg className="w-5 h-5 text-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {/* Mobile backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/50 md:hidden transition-opacity duration-300 ${
          mobileOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={() => setMobileOpen(false)}
      />

      {/* Sidebar */}
      <aside
        className={[
          'fixed top-0 left-0 z-50 h-full bg-card border-r border-border',
          'flex flex-col transition-all duration-300 ease-in-out',
          // Mobile: slide in/out, always w-60
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
          'w-60',
          // Desktop: fixed positioning (stays in place), responsive width
          'md:translate-x-0',
          collapsed ? 'md:w-16' : 'md:w-60',
        ].join(' ')}
      >
        {/* macOS title bar spacer (only in Electron on macOS) - matches TitleBar height */}
        {isElectron() && isMacOS && (
          <div className="h-9 shrink-0" aria-hidden="true" />
        )}

        {/* Top: Brand + collapse toggle */}
        <div className={`flex items-center h-14 border-b border-border shrink-0 ${collapsed ? 'md:justify-center md:px-2' : 'gap-2 px-3'}`}>
          {collapsed ? (
            // Collapsed: show icon that expands sidebar on click
            <button
              onClick={() => onCollapsedChange(false)}
              className="w-10 h-10 flex items-center justify-center shrink-0 cursor-pointer hover:opacity-80 transition-opacity"
              aria-label="Expand sidebar"
            >
              <img
                src={logoIcon}
                alt="Verbatim Studio"
                className="h-8 w-auto object-contain"
              />
            </button>
          ) : (
            // Expanded: show text wordmark with icon + tagline
            <div className="flex flex-col min-w-0">
              <div className="flex items-center gap-2.5">
                <img
                  src={logoIcon}
                  alt=""
                  className="h-9 w-9 shrink-0"
                />
                <span className="text-[15px] font-semibold leading-tight tracking-tight text-foreground">
                  Verbatim<span className="font-light text-muted-foreground">{' '}Studio</span>
                </span>
              </div>
              <span className="text-[10px] text-muted-foreground tracking-wide -mt-0.5">
                Transcription you can trust
              </span>
            </div>
          )}
          {/* Desktop collapse toggle (hidden when collapsed — V logo expands instead) */}
          <button
            onClick={() => onCollapsedChange(true)}
            className={`hidden ${collapsed ? '' : 'md:flex'} ml-auto min-w-touch min-h-touch items-center justify-center rounded-md hover:bg-muted transition-colors`}
            aria-label="Collapse sidebar"
          >
            <svg
              className="w-4 h-4 text-muted-foreground"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          </button>
          {/* Mobile close button */}
          <button
            onClick={() => setMobileOpen(false)}
            className="md:hidden ml-auto min-w-touch min-h-touch flex items-center justify-center rounded-md hover:bg-muted transition-colors"
            aria-label="Close navigation"
          >
            <svg className="w-4 h-4 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Middle: Navigation items */}
        <nav className={`flex-1 px-3 py-4 space-y-1 ${collapsed ? 'md:overflow-visible overflow-y-auto' : 'overflow-y-auto'}`}>
          {/* Project Selector */}
          <div className="px-0 mb-2">
            <ProjectSelector collapsed={collapsed} />
          </div>

          {NAV_ITEMS.map((item) => {
            const isActive = currentTab === item.key;
            return (
              <button
                key={item.key}
                data-tour={item.key}
                onClick={() => handleNavigate(item.key)}
                className={[
                  'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                  'relative group',
                  isActive
                    ? 'bg-muted text-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
                ].join(' ')}
              >
                <span className="shrink-0">{item.icon}</span>
                <span className={`overflow-hidden whitespace-nowrap transition-all duration-300 ${collapsed ? 'md:w-0 md:opacity-0' : 'md:w-auto md:opacity-100'}`}>
                  {item.label}
                </span>
                {/* Tooltip when collapsed (desktop only) */}
                {collapsed && (
                  <span className="hidden md:block absolute left-full ml-2 px-2 py-1 rounded-md bg-gray-900 text-gray-100 dark:bg-gray-100 dark:text-gray-900 text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity duration-100 z-50">
                    {item.label}
                  </span>
                )}
              </button>
            );
          })}
          {/* Plugin nav items (main position) */}
          {pluginNavItems?.filter((p) => p.position === 'main').map((item) => (
            <button
              key={item.key}
              onClick={() => handleNavigate(item.key as NavKey)}
              className={[
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                'relative group',
                currentTab === item.key
                  ? 'bg-muted text-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              ].join(' ')}
            >
              <span className="shrink-0">
                <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 002.25-2.25V6a2.25 2.25 0 00-2.25-2.25H6A2.25 2.25 0 003.75 6v2.25A2.25 2.25 0 006 10.5zm0 9.75h2.25A2.25 2.25 0 0010.5 18v-2.25a2.25 2.25 0 00-2.25-2.25H6a2.25 2.25 0 00-2.25 2.25V18A2.25 2.25 0 006 20.25zm9.75-9.75H18a2.25 2.25 0 002.25-2.25V6A2.25 2.25 0 0018 3.75h-2.25A2.25 2.25 0 0013.5 6v2.25a2.25 2.25 0 002.25 2.25z" />
                </svg>
              </span>
              <span className={`overflow-hidden whitespace-nowrap transition-all duration-300 ${collapsed ? 'md:w-0 md:opacity-0' : 'md:w-auto md:opacity-100'}`}>
                {item.label}
              </span>
              {collapsed && (
                <span className="hidden md:block absolute left-full ml-2 px-2 py-1 rounded-md bg-gray-900 text-gray-100 dark:bg-gray-100 dark:text-gray-900 text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity duration-100 z-50">
                  {item.label}
                </span>
              )}
            </button>
          ))}
        </nav>

        {/* Bottom: Downloads, Tasks, Settings, Theme, version, health */}
        <div className="px-3 py-4 border-t border-border space-y-1 shrink-0">
          {/* Download indicator */}
          <DownloadIndicator collapsed={collapsed} />

          {/* Task indicator (AI summarization, etc.) */}
          <TaskIndicator collapsed={collapsed} />

          {/* Plugin nav items (bottom position) */}
          {pluginNavItems?.filter((p) => p.position === 'bottom').map((item) => (
            <button
              key={item.key}
              onClick={() => handleNavigate(item.key as NavKey)}
              className={[
                'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                'relative group',
                currentTab === item.key
                  ? 'bg-muted text-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              ].join(' ')}
            >
              <span className="shrink-0">
                <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 002.25-2.25V6a2.25 2.25 0 00-2.25-2.25H6A2.25 2.25 0 003.75 6v2.25A2.25 2.25 0 006 10.5zm0 9.75h2.25A2.25 2.25 0 0010.5 18v-2.25a2.25 2.25 0 00-2.25-2.25H6a2.25 2.25 0 00-2.25 2.25V18A2.25 2.25 0 006 20.25zm9.75-9.75H18a2.25 2.25 0 002.25-2.25V6A2.25 2.25 0 0018 3.75h-2.25A2.25 2.25 0 0013.5 6v2.25a2.25 2.25 0 002.25 2.25z" />
                </svg>
              </span>
              <span className={`overflow-hidden whitespace-nowrap transition-all duration-300 ${collapsed ? 'md:w-0 md:opacity-0' : 'md:w-auto md:opacity-100'}`}>
                {item.label}
              </span>
              {collapsed && (
                <span className="hidden md:block absolute left-full ml-2 px-2 py-1 rounded-md bg-gray-900 text-gray-100 dark:bg-gray-100 dark:text-gray-900 text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity duration-100 z-50">
                  {item.label}
                </span>
              )}
            </button>
          ))}

          {/* Settings */}
          <button
            data-tour="settings"
            onClick={() => handleNavigate('settings')}
            className={[
              'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
              'relative group',
              currentTab === 'settings'
                ? 'bg-muted text-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
            ].join(' ')}
          >
            <span className="shrink-0">{SETTINGS_ICON}</span>
            <span className={`overflow-hidden whitespace-nowrap transition-all duration-300 ${collapsed ? 'md:w-0 md:opacity-0' : 'md:w-auto md:opacity-100'}`}>
              Settings
            </span>
            {collapsed && (
              <span className="hidden md:block absolute left-full ml-2 px-2 py-1 rounded-md bg-gray-900 text-gray-100 dark:bg-gray-100 dark:text-gray-900 text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity duration-100 z-50">
                Settings
              </span>
            )}
          </button>

          {/* Theme toggle */}
          <button
            onClick={onCycleTheme}
            className={[
              'w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
              'text-muted-foreground hover:text-foreground hover:bg-muted/50',
              'relative group',
            ].join(' ')}
            title={`Theme: ${theme}`}
          >
            <span className="shrink-0">
              {theme === 'light' ? (
                <svg className="w-5 h-5 text-amber-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
                </svg>
              ) : theme === 'dark' ? (
                <svg className="w-5 h-5 text-blue-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
                </svg>
              ) : (
                <svg className="w-5 h-5 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
              )}
            </span>
            <span className={`overflow-hidden whitespace-nowrap transition-all duration-300 capitalize ${collapsed ? 'md:w-0 md:opacity-0' : 'md:w-auto md:opacity-100'}`}>
              {theme}
            </span>
            {collapsed && (
              <span className="hidden md:block absolute left-full ml-2 px-2 py-1 rounded-md bg-gray-900 text-gray-100 dark:bg-gray-100 dark:text-gray-900 text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity duration-100 z-50">
                Theme: {theme}
              </span>
            )}
          </button>

          {/* Version + Health */}
          <div className={`flex items-center gap-2 px-3 ${collapsed ? 'md:justify-center' : ''}`}>
            {health && (
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${
                  health.status === 'ready'
                    ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
                    : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200'
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${health.status === 'ready' ? 'bg-green-500' : 'bg-yellow-500'}`} />
                <span className={`overflow-hidden whitespace-nowrap transition-all duration-300 ${collapsed ? 'md:w-0 md:hidden' : ''}`}>
                  {health.status === 'ready' ? 'Connected' : 'Connecting'}
                </span>
              </span>
            )}
            <span className={`text-xs text-muted-foreground font-mono overflow-hidden whitespace-nowrap transition-all duration-300 ${collapsed ? 'md:w-0 md:opacity-0 md:hidden' : ''}`}>
              {version}
            </span>
          </div>
        </div>
      </aside>
    </>
  );
}
