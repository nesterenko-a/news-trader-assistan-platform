import { execSync } from "child_process";
import fs from "fs";
import path from "path";

/**
 * Глобальный teardown: останавливает uvicorn, поднятый в globalSetup
 * (на Windows — дерево процессов через taskkill /T /F).
 */

const ROOT = path.resolve(__dirname, "..", "..");
const PID_FILE = path.join(ROOT, "test-results", "e2e-server.pid");

export default async function globalTeardown(): Promise<void> {
  if (!fs.existsSync(PID_FILE)) return;
  const pid = Number(fs.readFileSync(PID_FILE, "utf8").trim());
  if (Number.isFinite(pid) && pid > 0) {
    try {
      if (process.platform === "win32") {
        execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
      } else {
        process.kill(pid, "SIGTERM");
      }
    } catch {
      /* процесс уже завершён */
    }
  }
  fs.unlinkSync(PID_FILE);
}
