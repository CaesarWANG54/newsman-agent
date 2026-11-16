import { createHash, randomUUID } from "node:crypto";
import { realpathSync } from "node:fs";
import path from "node:path";

const instanceId = randomUUID();
export const dynamic = "force-dynamic";
export function GET() {
  const root = realpathSync(path.resolve(process.cwd(), "..")).replaceAll("\\", "/").toLowerCase().replace(/^\/mnt\/([a-z])\//, "$1:/");
  const backend = new URL(process.env.NEXT_PUBLIC_NEWSMAN_API_BASE_URL || "http://localhost:8000");
  const origin = `${backend.protocol}//${backend.hostname === "localhost" ? "127.0.0.1" : backend.hostname}:${backend.port || (backend.protocol === "https:" ? "443" : "80")}`;
  return Response.json({ product: "newsman-agent", role: "frontend", status: "ok",
    workspace_id: createHash("sha256").update(root).digest("hex").slice(0, 16), backend_origin_id: createHash("sha256").update(origin).digest("hex").slice(0,16), instance_id: instanceId, pid: process.pid });
}
