/* eslint-disable @typescript-eslint/no-require-imports -- Offline TS loader. */
require("../scripts/offline-network.cjs");
const {readFileSync} = require("node:fs");
const ts = require("typescript");
for (const ext of [".ts", ".tsx"]) require.extensions[ext] = (module, filename) => module._compile(ts.transpileModule(readFileSync(filename,"utf8"), {compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,esModuleInterop:true},fileName:filename}).outputText, filename);
const React = require("react");
const {renderToStaticMarkup} = require("react-dom/server");
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {InsightPanel,evidenceHref,insightReferences} = require("../components/insight-panel.tsx");
const {NewsDashboard,bilingualSummary,digestArtifact} = require("../components/news-dashboard.tsx");

test("legacy synthesis cannot inherit old recommendation or claim a unique original", () => {
  const item={id:"old-insight", title:"Old", url:"https://example.com/lead",source:"NewsMan Synthesis",tags:[],score:.9,summary:"预计收益翻倍。",analysis:{outlook:"固定12-24个月增长"},confidence:"medium"};
  assert.doesNotMatch(bilingualSummary(item).summaryZh,/翻倍/);
  const html=renderToStaticMarkup(React.createElement(NewsDashboard,{data:{articles:[],sourceHealth:[],isFallback:false,digest:{title:"History",digest_date:"2026-10-24",sections:[{key:"investment-lens",title:"Investment",items:[item]}]}}}));
  assert.match(html,/Evidence \/ 证据/);
  assert.doesNotMatch(html,/Original \/ 原文|固定12-24|预计收益|confidence medium/);
  assert.match(html,/旧观点未重新核实/);
});
test("evidence links reject unsafe schemes and embedded credentials", () => {
  for(const url of ["javascript:alert(1)","https://user:password@example.com","not-url"]) assert.equal(evidenceHref(url),undefined);
  assert.equal(evidenceHref("https://example.com/evidence"),"https://example.com/evidence");
});
test("no-conclusion is explicit and does not fabricate evidence", () => {
  const item={insight:{status:"no_conclusion",as_of:"2026-10-25",valid_until:null,confidence_basis:{reviewed_origins:0},claims:[],companies:[],evidence_context:null}};
  const html=renderToStaticMarkup(React.createElement(InsightPanel,{item,showClaims:true,showEvidence:true}));
  assert.match(html,/本期无结论/); assert.match(html,/暂无可用证据/); assert.doesNotMatch(html,/href=/);
});

test("machine reports never render as human review or complete full text", () => {
  const item={insight:{status:"review_required",as_of:"2026-10-25",valid_until:null,
    confidence_basis:{reviewed_origins:2,machine_source_count:2,human_review_count:0},companies:[],
    claims:[{id:"demand",role:"demand",kind:"source_report",text_en:"A source reports demand.",text_zh:"来源报道存在需求。",fact_ids:["a:demand"],counter_fact_ids:[]}],
    evidence_context:{sources:[{url:"https://source.invalid/report",source:"Source",title:"Report",published_at:"2026-10-25",extracted_at:"2026-10-25",method:"machine_source_extraction",body_scope:"observed_extracted_text_completeness_unknown",facts:[]}]}}};
  const html=renderToStaticMarkup(React.createElement(InsightPanel,{item,showClaims:true,showEvidence:true}));
  assert.match(html,/来源报道/);
  assert.match(html,/机器提取 · 未独立核实/);
  assert.match(html,/人工审阅 0 份/);
  assert.match(html,/全文完整度未知/);
  assert.doesNotMatch(html,/人工审阅记录：/);
  assert.ok(html.indexOf('lang="en"') < html.indexOf('lang="zh-CN"'));
});

test("short citations map to actual source locators; raw IDs stay inside closed audit", () => {
  const fact={id:"7cc77980-8324-4ddf-8822-52335f1eaa99:sector",quote:"Observed software pilot.",start:0,end:24};
  const source={source:"Source A",url:"https://source.invalid/pilot",title:"Pilot",published_at:"2026-10-25",method:"machine_source_extraction",body_sha256:"abc123",facts:[fact]};
  const item={id:"investment-one",insight:{status:"review_required",confidence_basis:{reviewed_origins:1},claims:[{id:"sector",role:"sector",kind:"source_report",text_en:"Observed pilot.",text_zh:"已报道的试点。",fact_ids:[fact.id],counter_fact_ids:[]}],companies:[],evidence_context:{sources:[source]}}};
  const refs=insightReferences(item);
  assert.equal(refs[0].label,"S1.1"); assert.equal(refs[0].fact,fact); assert.equal(refs[0].source,source);
  const html=renderToStaticMarkup(React.createElement(InsightPanel,{item,showClaims:true,showEvidence:true}));
  const normal=html.split('<details class="insightAudit">')[0];
  assert.match(normal,/href="#investment-one-s1-f1"/);
  assert.match(normal,/id="investment-one-s1-f1"/);
  assert.doesNotMatch(normal,/7cc77980|abc123|source_id/);
  assert.match(html,/高级审计详情/); assert.match(html,/7cc77980/); assert.match(html,/abc123/);
});

test("download serializes the selected immutable version, not preferences or a fabricated latest", () => {
  const digest={id:"revision-fixture",digest_date:"2026-10-24",revision:2,status:"partial",publication_status:"published",markdown:"# Historical QA\n",sections:[{key:"investment-lens",items:[{insight:{publishable:false,evidence_context:{sources:[{body_sha256:"fixture-hash",facts:[{quote:"Observed text.",start:0,end:14}]}]}}}]}]};
  const json=digestArtifact(digest,"json"), markdown=digestArtifact(digest,"md");
  assert.equal(json.filename,"2026-10-24-v2.json");
  assert.deepEqual(JSON.parse(json.content),digest);
  assert.equal(markdown.filename,"2026-10-24-v2.md");
  assert.equal(markdown.content,digest.markdown);
  assert.doesNotMatch(json.content,/preferences/);
});
