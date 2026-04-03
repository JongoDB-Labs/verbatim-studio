import Store from 'electron-store';

interface UpdateStoreSchema {
  autoUpdateEnabled: boolean;
  lastUpdateCheck: number;
  lastSeenVersion: string;
  githubPat: string;
}

export const updateStore = new Store<UpdateStoreSchema>({
  name: 'update-preferences',
  defaults: {
    autoUpdateEnabled: true,
    lastUpdateCheck: 0,
    lastSeenVersion: '',
    githubPat: '',
  },
});

export function getAutoUpdateEnabled(): boolean {
  return updateStore.get('autoUpdateEnabled');
}

export function setAutoUpdateEnabled(enabled: boolean): void {
  updateStore.set('autoUpdateEnabled', enabled);
}

export function getLastUpdateCheck(): number {
  return updateStore.get('lastUpdateCheck');
}

export function setLastUpdateCheck(timestamp: number): void {
  updateStore.set('lastUpdateCheck', timestamp);
}

export function getLastSeenVersion(): string {
  return updateStore.get('lastSeenVersion');
}

export function setLastSeenVersion(version: string): void {
  updateStore.set('lastSeenVersion', version);
}

export function getGithubPat(): string {
  return updateStore.get('githubPat');
}

export function setGithubPat(pat: string): void {
  updateStore.set('githubPat', pat);
}
