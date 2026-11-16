/* eslint-disable @typescript-eslint/no-require-imports -- In-memory TypeScript test loader. */
require("../scripts/offline-network.cjs");
const { readFileSync } = require("node:fs");
const ts = require("typescript");
const assert = require("node:assert/strict");
const { test } = require("node:test");

require.extensions[".ts"] = (module, filename) => {
  const result = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  });
  module._compile(result.outputText, filename);
};
const { getApiBaseUrl, fetchJson } = require("../lib/api.ts");

test("server rendering uses the container address; browser calls use the public address", async () => {
  const original = {
    internal: process.env.NEWSMAN_API_INTERNAL_URL,
    public: process.env.NEXT_PUBLIC_NEWSMAN_API_BASE_URL,
    window: globalThis.window,
    fetch: globalThis.fetch,
  };
  try {
    process.env.NEWSMAN_API_INTERNAL_URL = "http://api:8000";
    process.env.NEXT_PUBLIC_NEWSMAN_API_BASE_URL = "http://localhost:8300";
    delete globalThis.window;
    let requested;
    globalThis.fetch = async url => {
      requested = url;
      return { ok: true, json: async () => ({ ok: true }) };
    };
    await fetchJson("/health");
    assert.equal(requested, "http://api:8000/health");
    globalThis.window = {};
    await fetchJson("/health");
    assert.equal(requested, "http://localhost:8300/health");
    delete globalThis.window;
    delete process.env.NEWSMAN_API_INTERNAL_URL;
    assert.equal(getApiBaseUrl(), "http://localhost:8300");
  } finally {
    for (const [key, value] of [
      ["NEWSMAN_API_INTERNAL_URL", original.internal],
      ["NEXT_PUBLIC_NEWSMAN_API_BASE_URL", original.public],
    ]) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    if (original.window === undefined) delete globalThis.window;
    else globalThis.window = original.window;
    globalThis.fetch = original.fetch;
  }
});
