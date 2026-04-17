import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import { app } from 'electron';
import { EventEmitter } from 'events';
import { ensurePortAvailable } from './utils';

interface BackendConfig {
  preferredPort?: number;
  pythonPath?: string;
  backendPath?: string;
}

class BackendManager extends EventEmitter {
  private process: ChildProcess | null = null;
  private config: BackendConfig;
  private healthCheckInterval: NodeJS.Timeout | null = null;
  private isStopping = false;
  private _port: number | null = null;
  private _externalBackend = false;

  constructor(config: BackendConfig = {}) {
    super();
    this.config = config;
  }

  get port(): number | null {
    return this._port;
  }

  getApiUrl(): string | null {
    if (this._port === null) return null;
    return `http://127.0.0.1:${this._port}`;
  }

  async start(): Promise<void> {
    if (this.process) {
      console.log('[Backend] Already running');
      return;
    }

    const port = this.config.preferredPort ?? 52780;

    try {
      this._port = await ensurePortAvailable(port);
    } catch (err) {
      // In dev mode, a backend may already be running via dev.sh — reuse it
      if (!app.isPackaged) {
        try {
          const res = await fetch(`http://127.0.0.1:${port}/health`);
          if (res.ok) {
            console.log(`[Backend] Dev backend already running on port ${port}, reusing`);
            this._port = port;
            this._externalBackend = true;
            return;
          }
        } catch { /* not healthy, rethrow original */ }
      }
      throw err;
    }

    console.log(`[Backend] Using port ${this._port}`);

    const pythonPath = this.config.pythonPath || this.getPythonPath();
    const backendPath = this.config.backendPath || this.getBackendPath();

    console.log(`[Backend] Starting: ${pythonPath} at ${backendPath}`);
    console.log(`[Backend] app.isPackaged: ${app.isPackaged}`);
    console.log(`[Backend] process.resourcesPath: ${process.resourcesPath}`);

    // Check if Python exists
    console.log(`[Backend] Python exists: ${fs.existsSync(pythonPath)}`);
    console.log(`[Backend] Backend dir exists: ${fs.existsSync(backendPath)}`);

    // Build PATH based on platform.
    // Resources may be in user data dir (migrated) or bundle (fresh install).
    const userDataDir = app.getPath('userData');
    let extendedPath: string;
    if (process.platform === 'win32') {
      // Windows: prepend FFmpeg (and optionally CUDA DLLs) to PATH.
      // Prefer bundled paths (Program Files — trusted by Application Control)
      // over migrated copies in AppData which may be blocked by AppLocker/WDAC.
      //
      // CUDA DLLs are optional — they enable GPU-accelerated transcription but
      // Smart App Control (SAC) may block unsigned DLLs. The app works fine
      // in CPU mode without them. When the user installs GPU acceleration via
      // Settings, PyTorch brings its own signed CUDA runtime.
      const bundledCudaPath = path.join(process.resourcesPath, 'cuda');
      const userCudaPath = path.join(userDataDir, 'cuda');
      const cudaPath = fs.existsSync(bundledCudaPath) ? bundledCudaPath
        : fs.existsSync(userCudaPath) ? userCudaPath : null;

      const bundledFfmpegPath = path.join(process.resourcesPath, 'ffmpeg');
      const userFfmpegPath = path.join(userDataDir, 'ffmpeg');
      const ffmpegPath = fs.existsSync(bundledFfmpegPath) ? bundledFfmpegPath : userFfmpegPath;

      const pathParts = [ffmpegPath, process.env.PATH || ''];
      if (cudaPath) {
        // Prepend CUDA path — may be blocked by Smart App Control on some systems
        // but won't break the app (GPU features just fall back to CPU)
        pathParts.unshift(cudaPath);
      }
      extendedPath = pathParts.join(';');
    } else {
      // macOS/Linux: add user data FFmpeg + common binary paths
      const userFfmpegPath = path.join(userDataDir, 'ffmpeg');
      const bundledFfmpegPath = path.join(process.resourcesPath, 'ffmpeg');
      const ffmpegPath = fs.existsSync(userFfmpegPath) ? userFfmpegPath : bundledFfmpegPath;

      const additionalPaths = [
        ffmpegPath,
        '/opt/homebrew/bin',
        '/opt/homebrew/sbin',
        '/usr/local/bin',
        '/usr/local/sbin',
        '/opt/local/bin',
        '/opt/local/sbin',
      ];
      const currentPath = process.env.PATH || '/usr/bin:/bin';
      extendedPath = [...additionalPaths, currentPath].join(':');
    }

    // Use user data directory for database to persist across updates
    const databasePath = path.join(userDataDir, 'verbatim.db');

    const env: NodeJS.ProcessEnv = {
      ...process.env,
      PATH: extendedPath,
      VERBATIM_ELECTRON: '1',
      VERBATIM_PORT: String(this._port),
      VERBATIM_DATA_DIR: userDataDir,
      VERBATIM_DATABASE_URL: `sqlite+aiosqlite:///${databasePath}`,
      VERBATIM_FRONTEND_DIR: path.join(process.resourcesPath, 'frontend'),
    };

    this.process = spawn(
      pythonPath,
      ['-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', String(this._port)],
      {
        cwd: backendPath,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: process.platform !== 'win32',
      }
    );

    this.process.stdout?.on('data', (data: Buffer) => {
      console.log(`[Backend] ${data.toString().trim()}`);
      this.emit('log', { level: 'info', message: data.toString() });
    });

    this.process.stderr?.on('data', (data: Buffer) => {
      console.error(`[Backend] ${data.toString().trim()}`);
      this.emit('log', { level: 'error', message: data.toString() });
    });

    this.process.on('exit', (code: number | null) => {
      console.log(`[Backend] Exited with code ${code}`);
      this.process = null;
      this._port = null;
      this.emit('exit', code);
    });

    this.process.on('error', (err: Error) => {
      console.error(`[Backend] Process error: ${err.message}`);
      this.emit('error', err);
    });

    try {
      await this.waitForHealth();
      this.startHealthCheck();
      console.log('[Backend] Started successfully');
    } catch (error) {
      console.error('[Backend] Health check failed:', error);
      this.killProcess(true);
      this.process = null;
      this._port = null;
      throw error;
    }
  }

  async stop(): Promise<void> {
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = null;
    }

    if (this._externalBackend) {
      this._port = null;
      this._externalBackend = false;
      return;
    }

    if (!this.process || this.isStopping) return;
    this.isStopping = true;

    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        if (this.process) {
          console.log('[Backend] Force killing');
          this.killProcess(true);
        }
        this.isStopping = false;
        this._port = null;
        resolve();
      }, 5000);

      this.process!.on('exit', () => {
        clearTimeout(timeout);
        this.isStopping = false;
        this._port = null;
        resolve();
      });

      console.log('[Backend] Stopping process');
      this.killProcess(false);
    });
  }

  isRunning(): boolean {
    return this.process !== null || this._externalBackend;
  }

  /** Platform-aware process termination. */
  private killProcess(force: boolean): void {
    if (!this.process?.pid) return;

    if (process.platform === 'win32') {
      // Windows: use taskkill to kill the process tree
      try {
        const args = ['/T', '/PID', String(this.process.pid)];
        if (force) args.unshift('/F');
        spawn('taskkill', args, { stdio: 'ignore' });
      } catch (err) {
        console.error('[Backend] taskkill failed:', err);
        this.process.kill();
      }
    } else {
      // Kill the process group so uvicorn workers are also terminated
      const signal = force ? 'SIGKILL' : 'SIGTERM';
      try {
        process.kill(-this.process.pid!, signal);
      } catch {
        this.process.kill(signal);
      }
    }
  }

  /** Synchronously force-kill the backend process (for use in process.on('exit')) */
  forceKill(): void {
    this.killProcess(true);
  }

  private getPythonPath(): string {
    if (app.isPackaged) {
      const userDataDir = app.getPath('userData');
      const resourcesPath = process.resourcesPath;

      if (process.platform === 'win32') {
        // Prefer bundled Python (Program Files — trusted by Application Control).
        // The migrated copy in AppData may be blocked by AppLocker/WDAC policies
        // that prevent DLL execution from user-writable directories.
        const bundledPython = path.join(resourcesPath, 'python', 'python.exe');
        if (fs.existsSync(bundledPython)) {
          return bundledPython;
        }
        // Fall back to migrated copy (stripped update — no Python in bundle)
        return path.join(userDataDir, 'python', 'python.exe');
      } else {
        const userPython = path.join(userDataDir, 'python', 'bin', 'python3');
        if (fs.existsSync(userPython)) {
          return userPython;
        }
        return path.join(resourcesPath, 'python', 'bin', 'python3');
      }
    } else {
      // Development: Use venv Python
      const backendPath = path.join(__dirname, '../../../../packages/backend');
      if (process.platform === 'win32') {
        return path.join(backendPath, '.venv', 'Scripts', 'python.exe');
      } else {
        return path.join(backendPath, '.venv', 'bin', 'python');
      }
    }
  }

  private getBackendPath(): string {
    if (app.isPackaged) {
      return path.join(process.resourcesPath, 'backend');
    } else {
      return path.join(__dirname, '../../../../packages/backend');
    }
  }

  private async waitForHealth(timeout = 180000): Promise<void> {
    const startTime = Date.now();
    const url = `http://127.0.0.1:${this._port}/health`;

    console.log(`[Backend] Waiting for health at ${url}`);

    let lastError = '';
    while (Date.now() - startTime < timeout) {
      try {
        const response = await fetch(url);
        if (response.ok) {
          console.log('[Backend] Health check passed');
          return;
        }
        lastError = `HTTP ${response.status}`;
      } catch (err) {
        lastError = err instanceof Error ? err.message : String(err);
      }
      await new Promise((r) => setTimeout(r, 500));
    }

    throw new Error(`Backend failed to start within ${timeout/1000}s. Last error: ${lastError}`);
  }

  private startHealthCheck(): void {
    this.healthCheckInterval = setInterval(async () => {
      try {
        const response = await fetch(`http://127.0.0.1:${this._port}/health`);
        if (!response.ok) {
          this.emit('unhealthy');
        }
      } catch {
        this.emit('unhealthy');
      }
    }, 10000);
  }
}

export const backendManager = new BackendManager();
