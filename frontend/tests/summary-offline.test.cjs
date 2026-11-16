/* eslint-disable @typescript-eslint/no-require-imports -- In-memory TS loader uses CommonJS. */
require("../scripts/offline-network.cjs");
const { readFileSync } = require("node:fs");
const ts = require("typescript");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

// Load repo TS/TSX with the installed compiler; no new runner dependency or output files.
for (const extension of [".ts", ".tsx"]) {
  require.extensions[extension] = (module, filename) => {
    const result = ts.transpileModule(readFileSync(filename, "utf8"), {
      compilerOptions: {
        jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020, esModuleInterop: true,
      },
      fileName: filename,
    });
    module._compile(result.outputText, filename);
  };
}
const { SummaryPanel, NewsDashboard, bilingualSummary } = require("../components/news-dashboard.tsx");
const { getDashboardData } = require("../lib/api.ts");
const item = {
  id: "offline", title: "Fixture", url: "https://example.com/story", score: 0.5, tags: [],
  summary_en: "OpenAI releases Codex.", summary_zh: "OpenAI 发布 Codex。",
  summary_mode: "model", quality_status: "ready", translation_status: "complete",
  verification_status: "not_verified",
};

test("expanded summary keeps EN above ZH, state and not-verified label", () => {
  const html = renderToStaticMarkup(React.createElement(SummaryPanel, { item }));
  assert.ok(html.indexOf('lang="en"') < html.indexOf('lang="zh-CN"'));
  assert.ok(html.includes(item.summary_en) && html.includes(item.summary_zh));
  assert.match(html, /Model summary/);
  assert.match(html, /Not verified/);
});

test("legacy Chinese is never labeled English; missing translation remains pending", () => {
  const legacy = { ...item, summary_en: undefined, summary_zh: undefined,
    summary: "已开源。", summary_mode: undefined };
  assert.equal(bilingualSummary(legacy).summaryEn, "");
  assert.equal(bilingualSummary(legacy).summaryZh, "已开源。");
  const html = renderToStaticMarkup(React.createElement(SummaryPanel, { item: legacy }));
  assert.match(html, /translation pending/);
  assert.match(html, /Legacy record/);
  const bad = { ...item, summary_zh: "消息：OpenAI releases a new model for all developers." };
  assert.equal(bilingualSummary(bad).summaryZh, "");
  assert.equal(bilingualSummary({ ...item, summary_zh: "Google DeepMind Gemini Flash Lite 已发布。" }).summaryZh,
    "Google DeepMind Gemini Flash Lite 已发布。");
  assert.equal(bilingualSummary({ ...item, summary_zh: "消息：OpenAI builds models。" }).summaryZh, "");
  assert.equal(bilingualSummary({ ...item, summary_zh: "", summary: "旧的中文模板。",
    summary_mode: "source_excerpt" }).summaryZh, "");
});

test("excerpt is labeled and untranslated Chinese is not manufactured", () => {
  const excerpt = { ...item, summary_zh: "", summary_mode: "source_excerpt", quality_status: "degraded" };
  const html = renderToStaticMarkup(React.createElement(SummaryPanel, { item: excerpt }));
  assert.match(html, /Source excerpt/);
  assert.match(html, /摘要待生成或翻译/);
  assert.match(html, /降级/);
});

test("reading card retains independent summary button and source link", () => {
  const data = {
    articles: [], sourceHealth: [], isFallback: false,
    digest: { title: "Offline", digest_date: "2026-10-25", sections: [{ key: "test", title: "Test", items: [item] }] },
  };
  const html = renderToStaticMarkup(React.createElement(NewsDashboard, { data }));
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /AI Summary/);
  assert.ok(html.includes('href="https://example.com/story" target="_blank"'));
  assert.match(html, /Original/);
  assert.match(html, /Unknown/);
});

test("partial API failure preserves real digest and reports scheduler unknown", async () => {
  const savedFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url) => {
      if (url.endsWith("/api/scheduler/status")) throw new Error("offline");
      return { ok: true, json: async () => url.endsWith("/api/digests/latest") ?
        { digest_date: "2026-10-24", sections: [] } : [] };
    };
    const data = await getDashboardData();
    assert.equal(data.digest.digest_date, "2026-10-24");
    assert.equal(data.schedulerStatus, undefined);
    assert.equal(data.errors.length, 1);
  } finally {
    globalThis.fetch = savedFetch;
  }
});

test("total API failure never fabricates today's digest or enabled schedule", async () => {
  const data = await getDashboardData();
  assert.equal(data.digest, undefined);
  assert.equal(data.schedulerStatus, undefined);
  assert.deepEqual(data.articles, []);
  assert.equal(data.errors.length, 7);
});
