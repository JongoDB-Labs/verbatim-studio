import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import net from 'net';
import { app } from 'electron';
import { EventEmitter } from 'events';

class LiveKitManager extends EventEmitter {
  private process: ChildProcess | null = null;
  private port: number = 7880;
  private healthCheckInterval: NodeJS.Timeout | null = null;
  private isStopping = false;
  private apiKey = 'verbatim';
  private apiSecret = 'verbatim-local-dev-secret-key-min-32-chars!!';

  constructor() {
    super();
    // Prevent unhandled 'error' events from crashing the process
    this.on('error', (err: Error) => {
      console.warn('[LiveKit] Error (non-fatal):', err.message);
    });
  }

  getUrl(): string {
    return `ws://127.0.0.1:${this.port}`;
  }

  async start(): Promise<void> {
    if (this.process) {
      console.log('[LiveKit] Already running');
      return;
    }

    this.port = await this.findAvailablePort(7880);
    console.log(`[LiveKit] Using port ${this.port}`);

    const binaryPath = this.getLiveKitBinaryPath();
    console.log(`[LiveKit] Binary path: ${binaryPath}`);

    // In dev mode, check if the binary is available in PATH
    if (!app.isPackaged) {
      const { execFileSync } = require('child_process');
      try {
        execFileSync(process.platform === 'win32' ? 'where' : 'which', [binaryPath], { stdio: 'ignore' });
      } catch {
        console.log('[LiveKit] Binary not found in PATH — voice assistant disabled. Install with: brew install livekit');
        return;
      }
    } else if (!fs.existsSync(binaryPath)) {
      throw new Error(`LiveKit binary not found at ${binaryPath}`);
    }

    const configPath = this.writeConfig();
    console.log(`[LiveKit] Config written to ${configPath}`);

    this.process = spawn(
      binaryPath,
      ['--config', configPath],
      {
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: process.platform !== 'win32',
      }
    );

    this.process.stdout?.on('data', (data: Buffer) => {
      console.log(`[LiveKit] ${data.toString().trim()}`);
      this.emit('log', { level: 'info', message: data.toString() });
    });

    this.process.stderr?.on('data', (data: Buffer) => {
      console.error(`[LiveKit] ${data.toString().trim()}`);
      this.emit('log', { level: 'error', message: data.toString() });
    });

    this.process.on('exit', (code: number | null) => {
      console.log(`[LiveKit] Exited with code ${code}`);
      this.process = null;
      this.emit('exit', code);
    });

    this.process.on('error', (err: Error) => {
      console.error(`[LiveKit] Process error: ${err.message}`);
      this.emit('error', err);
    });

    try {
      await this.waitForHealth();
      this.startHealthCheck();
      console.log('[LiveKit] Started successfully');
      this.emit('ready');
    } catch (error) {
      console.error('[LiveKit] Health check failed:', error);
      this.killProcess(true);
      this.process = null;
      throw error;
    }
  }

  async stop(): Promise<void> {
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = null;
    }

    if (!this.process || this.isStopping) return;
    this.isStopping = true;

    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        if (this.process) {
          console.log('[LiveKit] Force killing');
          this.killProcess(true);
        }
        this.isStopping = false;
        resolve();
      }, 5000);

      this.process!.on('exit', () => {
        clearTimeout(timeout);
        this.isStopping = false;
        resolve();
      });

      console.log('[LiveKit] Stopping process');
      this.killProcess(false);
    });
  }

  isRunning(): boolean {
    return this.process !== null;
  }

  /** Synchronously force-kill the LiveKit process (for use in process.on('exit')) */
  forceKill(): void {
    this.killProcess(true);
  }

  /** Platform-aware process termination. */
  private killProcess(force: boolean): void {
    if (!this.process?.pid) return;

    if (process.platform === 'win32') {
      try {
        const args = ['/T', '/PID', String(this.process.pid)];
        if (force) args.unshift('/F');
        spawn('taskkill', args, { stdio: 'ignore' });
      } catch (err) {
        console.error('[LiveKit] taskkill failed:', err);
        this.process.kill();
      }
    } else {
      const signal = force ? 'SIGKILL' : 'SIGTERM';
      try {
        process.kill(-this.process.pid!, signal);
      } catch {
        this.process.kill(signal);
      }
    }
  }

  private getLiveKitBinaryPath(): string {
    if (app.isPackaged) {
      const ext = process.platform === 'win32' ? '.exe' : '';
      return path.join(process.resourcesPath, 'bin', `livekit-server${ext}`);
    } else {
      // Development: expect livekit-server in PATH
      return 'livekit-server';
    }
  }

  private writeConfig(): string {
    const configDir = app.getPath('userData');
    const configPath = path.join(configDir, 'livekit.yaml');

    const config = [
      'port: ' + this.port,
      'bind_addresses:',
      '  - 127.0.0.1',
      'rtc:',
      '  port_range_start: 7882',
      '  port_range_end: 7892',
      '  use_external_ip: false',
      '  stun_servers: []',
      '  turn_servers: []',
      '  use_ice_lite: true',
      'keys:',
      `  ${this.apiKey}: ${this.apiSecret}`,
      'logging:',
      '  level: warn',
    ].join('\n') + '\n';

    fs.writeFileSync(configPath, config, 'utf-8');
    return configPath;
  }

  private async waitForHealth(timeout = 30000): Promise<void> {
    const startTime = Date.now();
    const url = `http://127.0.0.1:${this.port}`;

    console.log(`[LiveKit] Waiting for health at ${url}`);

    let lastError = '';
    while (Date.now() - startTime < timeout) {
      try {
        const response = await fetch(url);
        // LiveKit returns something on its HTTP port when ready
        if (response.ok || response.status < 500) {
          console.log('[LiveKit] Health check passed');
          return;
        }
        lastError = `HTTP ${response.status}`;
      } catch (err) {
        lastError = err instanceof Error ? err.message : String(err);
      }
      await new Promise((r) => setTimeout(r, 500));
    }

    throw new Error(`LiveKit failed to start within ${timeout / 1000}s. Last error: ${lastError}`);
  }

  private startHealthCheck(): void {
    this.healthCheckInterval = setInterval(async () => {
      try {
        const response = await fetch(`http://127.0.0.1:${this.port}`);
        if (!response.ok && response.status >= 500) {
          this.emit('unhealthy');
        }
      } catch {
        this.emit('unhealthy');
      }
    }, 15000);
  }

  private async findAvailablePort(start: number): Promise<number> {
    let port = start;
    const maxAttempts = 20;

    for (let i = 0; i < maxAttempts; i++) {
      try {
        await this.tryPort(port);
        return port;
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code === 'EADDRINUSE') {
          port++;
          continue;
        }
        throw err;
      }
    }

    throw new Error(`No available port found in range ${start}-${start + maxAttempts - 1}`);
  }

  private tryPort(port: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const server = net.createServer();

      server.listen(port, '127.0.0.1', () => {
        server.close(() => resolve());
      });

      server.on('error', (err: NodeJS.ErrnoException) => {
        reject(err);
      });
    });
  }
}

export const livekitManager = new LiveKitManager();
