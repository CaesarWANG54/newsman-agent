/* eslint-disable @typescript-eslint/no-require-imports -- Isolated SSR preload. */
require("../scripts/offline-network.cjs");
const {readFileSync} = require("node:fs");
globalThis.fetch = async input => {
  const url = new URL(typeof input === "string" ? input : input.url);
  const data = JSON.parse(readFileSync(process.env.NEWSMAN_FIXTURE_DATA, "utf8"));
  const key = {"/api/digests/latest":"digest", "/api/digests":"history", "/api/articles":"articles",
    "/api/sources/health":"sources", "/api/scheduler/status":"scheduler", "/api/preferences":"preferences",
    "/api/runtime/status":"runtime"}[url.pathname];
  if (!key) throw new Error("Unexpected SSR API in offline QA");
  return Response.json(data[key], {status: data.unavailable?.includes(key) ? 503 : 200});
};
