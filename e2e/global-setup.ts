import { execSync, spawn } from "child_process";
import fs from "fs";
import path from "path";

/**
 * Глобальный setup e2e-стенда:
 * 1. сидинг тестовой SQLite-БД (scripts/e2e_seed.py);
 * 2. запуск uvicorn на 127.0.0.1:8765 (MOEX отключён — мёртвый адрес);
 * 3. ожидание /v1/health.
 * PID сервера сохраняется в test-results/e2e-server.pid для globalTeardown.
 */

const PORT = 8765;
const ROOT = path.resolve(__dirname, "..");
const DB_DIR = path.join(ROOT, "test-results");
const DB_PATH = path.join(DB_DIR, "e2e.db");
const DB_URL = `sqlite+aiosqlite:///${DB_PATH.replace(/\\/g, "/")}`;
const PID_FILE = path.join(DB_DIR, "e2e-server.pid");
const HEALTH_URL = `http://127.0.0.1:${PORT}/v1/health`;

function pythonBin(): string {
  return process.platform === "win32"
    ? path.join(ROOT, ".venv", "Scripts", "python.exe")
    : "python";
}

async function waitForHealth(timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(HEALTH_URL);
      if (resp.status === 200) return;
    } catch {
      /* сервер ещё не готов */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`Тестовый сервер не поднялся за ${timeoutMs / 1000}s`);
}

export default async function globalSetup(): Promise<void> {
  fs.mkdirSync(DB_DIR, { recursive: true });
  if (fs.existsSync(DB_PATH)) fs.unlinkSync(DB_PATH);

  console.log("[e2e] Сидинг тестовой БД...");
  execSync(`"${pythonBin()}" -m scripts.e2e_seed`, {
    cwd: ROOT,
    env: { ...process.env, DATABASE_URL: DB_URL },
    stdio: "inherit",
  });

  console.log(`[e2e] Запуск uvicorn на порту ${PORT}...`);
  const server = spawn(
    pythonBin(),
    [
      "-u",
      "-m",
      "uvicorn",
      "app.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(PORT),
      "--log-level",
      "warning",
    ],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        DATABASE_URL: DB_URL,
        MOEX_BASE_URL: "http://127.0.0.1:9",
      },
      stdio: "ignore",
    }
  );
  server.on("exit", () => {
    if (fs.existsSync(PID_FILE)) fs.unlinkSync(PID_FILE);
  });
  fs.writeFileSync(PID_FILE, String(server.pid));

  await waitForHealth(60_000);
  console.log("[e2e] Стенд готов.");
}
