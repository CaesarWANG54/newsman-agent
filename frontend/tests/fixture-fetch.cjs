/* eslint-disable @typescript-eslint/no-require-imports -- Isolated Node preload. */
require("../scripts/offline-network.cjs");
const { readFileSync } = require("node:fs");
globalThis.fetch = async (input) => {
  const path = new URL(typeof input === "string" ? input : input.url).pathname;
  const fixture = JSON.parse(readFileSync(process.env.NEWSMAN_FIXTURE_DATA, "utf8"));
  const key = { "/api/digests/latest": "digest", "/api/articles": "articles",
    "/api/sources/health": "sources", "/api/scheduler/status": "scheduler" }[path];
  if (!key) throw new Error("Unmocked API request in isolated browser QA");
  return new Response(JSON.stringify(fixture[key]), { status: 200,
    headers: { "Content-Type": "application/json" } });
};
