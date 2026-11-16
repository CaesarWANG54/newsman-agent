import type { DigestItem } from "../lib/api";

const roles: Record<string, string> = { demand: "需求", customer: "目标客户", workflow: "现有流程", solution: "待验证方案", alternative: "替代方案", risk: "风险", validation: "低成本验证", event: "事件", transmission: "传导条件", sector: "相关行业", counterevidence: "反向证据", scenario: "保守情景", indicator: "观察指标" };
const kinds: Record<string, string> = { fact: "人工审阅来源记录", source_report: "来源报道", inference: "推断", hypothesis: "假设", validation: "验证建议" };

export function evidenceHref(value: string) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}

export function insightReferences(item: DigestItem) {
  return (item.insight?.evidence_context?.sources || []).flatMap((source, sourceIndex) =>
    source.facts.map((fact, factIndex) => ({ source, fact, label: `S${sourceIndex + 1}.${factIndex + 1}`, anchor: `${item.id}-s${sourceIndex + 1}-f${factIndex + 1}` })));
}

export function InsightPanel({ item, showClaims, showEvidence, revealEvidence }: { item: DigestItem; showClaims: boolean; showEvidence: boolean; revealEvidence?: (anchor: string) => void }) {
  const insight = item.insight;
  const references = insightReferences(item);
  const byId = new Map(references.map(reference => [reference.fact.id, reference]));
  const renderReferences = (ids: string[]) => ids.map(id => {
    const reference = byId.get(id);
    return reference ? <a className="citationLink" key={id} href={`#${reference.anchor}`} title={`${reference.source.source} · ${reference.source.title}`} onClick={event => { if (revealEvidence) { event.preventDefault(); revealEvidence(reference.anchor); } }}>{reference.label}</a> : <span key={id}>引用缺失</span>;
  });
  return <div className="insightDetail">
    <p className="assumption">{!insight ? "历史合成记录缺少证据契约，旧观点未重新核实。" : insight.status === "no_conclusion" ? "本期无结论 · 不生成推荐" : "条件分析 · 非推荐 · 尚需人工审阅"}</p>
    {insight ? <>
      <p className="why">截至：{insight.as_of || "未知"} · 适用至：{insight.valid_until || "未知"}</p>
      <p className="why">{insight.confidence_basis.reviewed_origins} 个来源组 · 置信度未校准；来源报道与推断均未独立核实。{insight.confidence_basis.machine_source_count ? `机器提取 ${insight.confidence_basis.machine_source_count} 份，人工审阅 ${insight.confidence_basis.human_review_count || 0} 份。` : ""}</p>
      {showClaims ? <div className="insightClaims">{insight.claims.map(claim => <div className="insightClaim" key={claim.id}>
        <strong>{roles[claim.role] || "分析判断"} · {kinds[claim.kind] || "未核实"}</strong>
        <p lang="en">{claim.text_en}</p><p lang="zh-CN">{claim.text_zh}</p>
        <small className="citationRow">依据：{renderReferences(claim.fact_ids)}{claim.counter_fact_ids.length ? <>反证：{renderReferences(claim.counter_fact_ids)}</> : null}</small>
      </div>)}</div> : null}
      {showClaims && insight.companies.length ? <div className="assumption">已记录业务关联（不等于受益证明）：{insight.companies.map(c => <p key={`${c.entity}-${c.symbol}`}>{c.entity}{c.symbol ? ` (${c.symbol})` : ""} {renderReferences(c.fact_ids)}</p>)}</div> : null}
    </> : null}
    {showEvidence ? <div className="evidence insightEvidence" aria-label="洞察证据列表">
      {!item.evidence?.length ? <p>暂无可用证据，不存在唯一原文。</p> : <p>合成分析没有唯一原文，以下链接分别打开证据来源。</p>}
      {(insight?.evidence_context?.sources || item.evidence || []).map((source, sourceIndex) => <div key={`${source.url}-${source.source}`}>
        {evidenceHref(source.url) ? <a href={evidenceHref(source.url)} target="_blank" rel="noreferrer"><span>S{sourceIndex + 1} · {source.source}</span>{source.title}</a> : <p>来源链接无效，未提供跳转。</p>}
        {"facts" in source ? <><p>发布：<time dateTime={source.published_at}>{source.published_at}</time> · {source.method === "machine_source_extraction" ? "机器提取 · 未独立核实" : "人工审阅记录 · 未独立核实"}</p>{source.body_scope === "observed_extracted_text_completeness_unknown" ? <p>仅引用已提取正文；全文完整度未知。</p> : null}{source.facts.map(fact => { const reference = byId.get(fact.id); return <blockquote tabIndex={-1} id={reference?.anchor} key={fact.id}><strong>{reference?.label || "引用缺失"}</strong><p>{fact.quote}</p></blockquote>; })}</> : null}
      </div>)}
      {insight ? <details className="insightAudit"><summary>高级审计详情</summary><p>完整来源标识、正文定位、材料版本与生成记录。</p><pre>{JSON.stringify(insight, null, 2)}</pre></details> : null}
    </div> : null}
  </div>;
}
