/**
 * Spawning the `me` CLI. No shell: the binary path comes from settings and
 * arguments are passed as an array. Output is never logged.
 */
import { spawn, type ChildProcess } from "child_process";
import * as os from "os";
import * as path from "path";
import { LineBuffer, type ProcessOutcome } from "./parse";

export interface MeConfig {
  mePath: string;
  pathPrefix: string;
}

export interface RunOptions {
  /** Called for every complete stdout line, partial lines are held until completed. */
  onStdoutLine?: (line: string) => void;
  /** Written to the child's stdin, which is then closed. */
  stdin?: string;
}

export interface RunResult extends ProcessOutcome {
  /** True when stop() was called before the process exited. */
  stopped: boolean;
}

const MAX_CAPTURE = 4 * 1024 * 1024;
const MAX_TAIL = 64 * 1024;

/** process.env with the configured PATH prefix in front of PATH. */
export function buildEnv(pathPrefix: string, base: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const parts = [...pathPrefix.split(path.delimiter), ...(base.PATH ?? "").split(path.delimiter)]
    .map((p) => p.trim())
    .filter(Boolean);
  return { ...base, PATH: [...new Set(parts)].join(path.delimiter) };
}

/** One running `me` process. Await `done`; call `stop()` to cancel. */
export class MeRun {
  readonly done: Promise<RunResult>;
  private child: ChildProcess | null = null;
  private exited = false;
  private stopRequested = false;
  private timers: ReturnType<typeof setTimeout>[] = [];

  constructor(config: MeConfig, args: string[], options: RunOptions = {}) {
    this.done = new Promise<RunResult>((resolve) => {
      let stdout = "";
      let stderr = "";
      let spawnError: string | undefined;
      let settled = false;
      const lines = new LineBuffer();
      const collectAll = !options.onStdoutLine;

      const finish = (code: number | null, signal: NodeJS.Signals | null) => {
        if (settled) return;
        settled = true;
        this.exited = true;
        this.timers.forEach(clearTimeout);
        if (options.onStdoutLine) lines.flush().forEach((l) => this.emit(options.onStdoutLine, l));
        resolve({ code, signal, stdout, stderr, spawnError, stopped: this.stopRequested });
      };

      const mePath = config.mePath.trim();
      if (!mePath) {
        spawnError = "Set the absolute path to `me` in the me harness settings.";
        finish(null, null);
        return;
      }

      let child: ChildProcess;
      try {
        child = spawn(mePath, args, {
          cwd: os.homedir(),
          env: buildEnv(config.pathPrefix),
          stdio: ["pipe", "pipe", "pipe"],
          // Own process group, so Stop reaches `me` and anything it runs in-group.
          detached: process.platform !== "win32",
          windowsHide: true,
        });
      } catch (e) {
        spawnError = `Could not start ${mePath}: ${(e as Error).message}`;
        finish(null, null);
        return;
      }
      this.child = child;

      child.on("error", (err: NodeJS.ErrnoException) => {
        // With a pid the process did start (e.g. a failed kill); the close event reports the outcome.
        if (child.pid !== undefined) return;
        const hint = err.code === "ENOENT" ? " (check the path in settings)" : err.code === "EACCES" ? " (not executable)" : "";
        spawnError = `Could not run ${mePath}: ${err.message}${hint}`;
        finish(null, null);
      });

      child.stdout?.setEncoding("utf8");
      child.stdout?.on("data", (chunk: string) => {
        if (collectAll) {
          if (stdout.length < MAX_CAPTURE) stdout += chunk;
        } else {
          stdout = (stdout + chunk).slice(-MAX_TAIL);
          for (const line of lines.push(chunk)) this.emit(options.onStdoutLine, line);
        }
      });

      child.stderr?.setEncoding("utf8");
      child.stderr?.on("data", (chunk: string) => {
        stderr = (stderr + chunk).slice(-MAX_TAIL);
      });

      child.stdin?.on("error", () => {
        /* EPIPE when the child exits before reading stdin; the exit code tells the story */
      });
      if (options.stdin !== undefined) child.stdin?.end(options.stdin, "utf8");
      else child.stdin?.end();

      child.on("close", (code, signal) => finish(code, signal));
      // A grandchild that inherited our pipes can delay "close" indefinitely; don't wait forever.
      child.on("exit", (code, signal) => {
        this.timers.push(setTimeout(() => finish(code, signal), 2000));
      });
    });
  }

  private emit(handler: ((line: string) => void) | undefined, line: string): void {
    if (!handler) return;
    try {
      handler(line);
    } catch (e) {
      // Report the bug without echoing the line (it may quote note content).
      console.error("me harness: failed to handle a stream line:", (e as Error).message);
    }
  }

  get running(): boolean {
    return this.child !== null && !this.exited;
  }

  /** Interrupt politely, then escalate: SIGINT, SIGTERM after 1.5 s, SIGKILL after 4 s. */
  stop(): void {
    if (!this.child || this.exited || this.stopRequested) return;
    this.stopRequested = true;
    this.signal("SIGINT");
    this.timers.push(setTimeout(() => this.signal("SIGTERM"), 1500));
    this.timers.push(setTimeout(() => this.signal("SIGKILL"), 4000));
  }

  private signal(sig: NodeJS.Signals): void {
    const child = this.child;
    if (!child || this.exited) return;
    try {
      if (child.pid !== undefined && process.platform !== "win32") process.kill(-child.pid, sig);
      else child.kill(sig);
    } catch {
      try {
        child.kill(sig);
      } catch {
        /* already gone */
      }
    }
  }
}

export function runMe(config: MeConfig, args: string[], options?: RunOptions): MeRun {
  return new MeRun(config, args, options);
}

/** True when the run exited cleanly. */
export function succeeded(r: RunResult): boolean {
  return !r.spawnError && !r.stopped && r.code === 0;
}
