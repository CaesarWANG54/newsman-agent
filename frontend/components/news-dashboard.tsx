"use client";

import { Activity, CalendarClock, ExternalLink, FileText, FileJson, Download, Play, RefreshCw, Rss, Bookmark, Ban, Copy, RotateCcw, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { fetchJson, type DashboardData, type Digest, type DigestItem, type SourceHealth } from "../lib/api";
import { statusLabel } from "../lib/status";
import { InsightPanel } from "./insight-panel";

type Props = {
  data: DashboardData;
};

export function digestArtifact(digest: Digest, format: "json" | "md") {
  return {
    filename: `${digest.digest_date}-v${digest.revision ?? 0}.${format}`,
    content: format === "json" ? JSON.stringify(digest, null, 2) + "\n" : digest.markdown,
    mime: format === "json" ? "application/json;charset=utf-8" : "text/markdown;charset=utf-8",
  };
}

function downloadDigest(digest: Digest, format: "json" | "md") {
  const artifact = digestArtifact(digest, format);
  const url = URL.createObjectURL(new Blob([artifact.content], {type: artifact.mime}));
  const link = document.createElement("a");
  link.href = url;
  link.download = artifact.filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function formatScore(score: number) {
  return Math.round(score * 100);
}

function sourceLabel(item: DigestItem) {
  return item.source ? `${item.source}` : "Unknown source";
}

function isSynthetic(item: DigestItem) {
  return Boolean(item.insight || item.source === "NewsMan Synthesis" || item.analysis || item.evidence?.length || item.conservative_assumption);
}

function healthLabel(source: SourceHealth) {
  if (source.health_status === "healthy" && (!source.last_attempt_at || !source.last_success_at || !source.last_body_success_at)) return "缺少观测，状态未知";
  return statusLabel(source.health_status);
}

function formatDateTime(value?: string | null) {
  if (!value) {
    return "No digest yet";
  }
  return value.replace("T", " ").slice(0, 16);
}

function chinese(value?: string) {
  if (!value || (value.match(/[\u4e00-\u9fff]/g) || []).length < 2) return "";
  if (/\b(?:is|are|was|were|has|have|will|released|releases|launches|built|builds|supports|announced|allows|lets|raises|can)\b/i.test(value)) return "";
  const runs = value.match(/(?:[A-Za-z][\w'-]*[ ,]+){4}[A-Za-z][\w'-]*/g) || [];
  return runs.some((run) => /\b(?:the|a|an|and|for|with|to|that)\b/i.test(run)) ? "" : value;
}

function english(value?: string) {
  return value && /[A-Za-z]/.test(value) && !/[\u4e00-\u9fff]/.test(value) ? value : "";
}

export function bilingualSummary(item: DigestItem) {
  if (isSynthetic(item) && !item.insight) return { summaryEn: "Legacy analysis has no reviewed evidence contract.", summaryZh: "旧版分析缺少可复核证据契约，原观点未重新核实。", whyEn: "", whyZh: "" };
  const legacy = !item.summary_mode || item.summary_mode === "legacy_unknown";
  return {
    summaryEn: english(item.summary_en) || (legacy ? english(item.summary) : ""),
    summaryZh: chinese(item.summary_zh) || (legacy ? chinese(item.summary) : ""),
    whyEn: english(item.why_it_matters_en) || (legacy ? english(item.why_it_matters) : ""),
    whyZh: chinese(item.why_it_matters_zh) || (legacy ? chinese(item.why_it_matters) : "")
  };
}

export function SummaryPanel({ item }: { item: DigestItem }) {
  const summary = bilingualSummary(item);
  return (
    <div className="summaryPanel">
      <div className="languageBlock" lang="en">
        <span>EN</span>
        <p>{summary.summaryEn || "Summary / translation pending."}</p>
        {summary.whyEn ? <em>{summary.whyEn}</em> : null}
      </div>
      <div className="languageBlock" lang="zh-CN">
        <span>中文</span>
        <p>{summary.summaryZh || "摘要待生成或翻译。"}</p>
        {summary.whyZh ? <em>{summary.whyZh}</em> : null}
      </div>
      {item.source_excerpt && !summary.summaryEn && !summary.summaryZh ? (
        <div className="languageBlock">
          <span>Source excerpt / 原文摘录 ({item.excerpt_language || "unknown"})</span>
          <p>{item.source_excerpt}</p>
        </div>
      ) : null}
      <div className="languageBlock summaryStatus">
        <span>{item.summary_mode === "model" ? "Model summary / 模型摘要" :
          item.summary_mode === "source_excerpt" ? "Source excerpt / 原文摘录" :
          item.summary_mode === "evidence_synthesis" ? "Evidence-based draft / 证据分析草稿" :
          item.summary_mode === "rule_synthesis" ? "Rule synthesis / 规则合成" :
          "Legacy record / 旧记录，生成方式未知"}</span>
        <p>翻译：{statusLabel(item.translation_status)} · 质量：{statusLabel(item.quality_status)}</p>
        <p>正文：{statusLabel(item.body_status)} · 一致性：{statusLabel(item.consistency_status || "not_checked")}</p>
        <p>来源事实未核验 / Not verified</p>
        {item.quality_issues?.length ? <p>存在 {item.quality_issues.length} 项质量疑点，需对照原文复核。</p> : null}
      </div>
    </div>
  );
}

export function NewsDashboard({ data }: Props) {
  const [openSummaries, setOpenSummaries] = useState<Record<string, boolean>>({});
  const [openEvidence, setOpenEvidence] = useState<Record<string, boolean>>({});
  const [runState, setRunState] = useState(data.schedulerStatus?.run_status || "idle");
  const [runDetail, setRunDetail] = useState<{ stage?: string; retry_at?: string; error_type?: string }>({});
  const [notice, setNotice] = useState("");
  const [digest, setDigest] = useState(data.digest);
  const [versions, setVersions] = useState<Digest[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [savedOnly, setSavedOnly] = useState(false);
  const [votes, setVotes] = useState(data.preferences || {});
  const [pendingVote, setPendingVote] = useState("");
  const [readingBusy, setReadingBusy] = useState(false);
  const readSequence = useRef(0);
  const [sourceActions, setSourceActions] = useState<Record<string, string>>({});
  const [enabledOverrides, setEnabledOverrides] = useState<Record<string, boolean>>({});
  const scheduler = data.schedulerStatus;
  const allDigestItems = digest?.sections.flatMap((section) => section.items) ?? [];
  const articlesAvailable = data.availability?.articles !== false;
  const sourcesAvailable = data.availability?.sourceHealth !== false;
  const articleCount = data.articles.length;
  const sourceCount = data.sourceHealth.filter((source) => source.enabled).length;
  const search = query.trim().toLowerCase();
  const matchesReadingFilters = (item: DigestItem) =>
    (!search || `${item.title} ${item.source || ""} ${item.summary_en || ""} ${item.summary_zh || ""}`.toLowerCase().includes(search)) &&
    (!savedOnly || votes[item.id] === "saved");
  const visibleSections = digest?.sections.filter(section => !category || section.key === category)
    .map(section => ({...section, items: section.items.filter(matchesReadingFilters)})) ?? [];
  const visibleItemCount = visibleSections.reduce((count, section) => count + section.items.length, 0);
  const freshSourceCount = data.sourceHealth.filter(
    (source) => source.enabled && source.health_status === "healthy" && source.last_attempt_at && source.last_body_success_at && source.last_success_at,
  ).length;

  const targetDay = scheduler?.target_date;
  useEffect(() => {
    if (!data.digest) return;
    const controller = new AbortController();
    const sequence = readSequence.current;
    fetchJson<Digest[]>(`/api/digests/${data.digest.digest_date}/versions`, {signal: controller.signal})
      .then(values => { if (sequence === readSequence.current) setVersions(values); })
      .catch(() => { if (!controller.signal.aborted && sequence === readSequence.current) setNotice("版本列表暂不可用，当前快照仍可阅读。"); });
    return () => controller.abort();
  }, [data.digest]);
  useEffect(() => {
    if (!targetDay || !["queued", "running", "busy"].includes(runState)) return;
    const controller = new AbortController();
    const timer = setInterval(async () => {
      try {
        const state = await fetchJson<{status: string; stage?: string; retry_at?: string; error_type?: string}>(`/api/runs/${targetDay}`, { signal: controller.signal });
        if (state.status !== "not_started") setRunState(state.status);
        if (state.status === "succeeded") setNotice("运行已完成，请刷新以读取新快照；当前阅读版本保持不变。");
        setRunDetail(state);
      } catch { if (!controller.signal.aborted) setNotice("运行状态暂不可用，提交结果不能据此确认。"); }
    }, 2500);
    return () => { controller.abort(); clearInterval(timer); };
  }, [targetDay, runState]);

  async function startRun() {
    setNotice("");
    setRunState("running");
    try {
      const mode = ["partial", "pending_summary"].includes(runState) ? "retry" : "ensure";
      const result = await fetchJson<{task_id?: string}>(`/api/runs/daily?target_date=${targetDay}&mode=${mode}${mode === "retry" ? `&request_key=${crypto.randomUUID()}` : ""}`, {method: "POST"});
      if (!result.task_id) throw new Error("No queue receipt");
      setRunState("queued");
    } catch { setRunState("failed"); setNotice("提交未获确认，请检查后台依赖与运行状态。不会显示虚假的已排队。"); }
  }

  async function openDigest(day: string, revision?: string) {
    const request = ++readSequence.current;
    setReadingBusy(true);
    setNotice("");
    try {
      const [snapshot, choices] = await Promise.allSettled([
        fetchJson<Digest>(`/api/digests/${day}${revision ? `?revision=${revision}` : ""}`),
        fetchJson<Digest[]>(`/api/digests/${day}/versions`),
      ]);
      if (request !== readSequence.current) return;
      if (snapshot.status === "rejected") throw snapshot.reason;
      setDigest(snapshot.value);
      setVersions(choices.status === "fulfilled" ? choices.value : []);
      setOpenSummaries({}); setOpenEvidence({}); setCategory("");
      if (choices.status === "rejected") setNotice("版本列表暂不可用，已打开所选日期的快照；当前内容仍可阅读和下载。");
    } catch { if (request === readSequence.current) setNotice("历史读取失败，仍保留先前日报，请重试。"); }
    finally { if (request === readSequence.current) setReadingBusy(false); }
  }

  async function feedback(itemId: string, vote: string) {
    setPendingVote(itemId); setNotice("");
    try {
      const remove = votes[itemId] === vote;
      await fetchJson(`/api/preferences/${itemId}${remove ? "" : `?vote=${vote}`}`, {method: remove ? "DELETE" : "PUT"});
      setVotes(current => { const next = {...current}; if (remove) delete next[itemId]; else next[itemId] = vote; return next; });
    } catch { setNotice("偏好保存失败，当前偏好未改变。"); }
    finally { setPendingVote(""); }
  }

  async function resetVotes() {
    setPendingVote("all");
    try { await fetchJson("/api/preferences", {method: "DELETE"}); setVotes({}); setNotice("阅读偏好已清除，原始新闻不变。"); }
    catch { setNotice("清除失败，偏好仍保留。"); }
    finally { setPendingVote(""); }
  }

  function toggleSummary(itemId: string) {
    setOpenSummaries((current) => ({ ...current, [itemId]: !current[itemId] }));
  }

  async function updateSource(source: SourceHealth, action: "probe" | "toggle") {
    if (action === "toggle") setEnabledOverrides((current) => ({ ...current, [source.id]: !source.enabled }));
    setSourceActions((current) => ({ ...current, [source.id]: "busy" }));
    try {
      const api = process.env.NEXT_PUBLIC_NEWSMAN_API_BASE_URL ?? "http://localhost:8000";
      const response = await fetch(`${api}/api/sources/${source.id}${action === "probe" ? "/probe" : ""}`, {
        method: action === "probe" ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: action === "probe" ? undefined : JSON.stringify({ enabled: !source.enabled })
      });
      if (!response.ok) throw new Error("来源操作失败，请查看当前核验与重试状态。");
      window.location.reload();
    } catch {
      setEnabledOverrides((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      setSourceActions((current) => ({ ...current, [source.id]: "操作未完成，请重试或检查 API。" }));
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden="true">
            {/* Existing user-provided product asset, also used as the app icon. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/icon.png" width={36} height={36} alt="" />
          </div>
          <div>
            <h1>NewsMan Agent</h1>
            <span>{digest?.digest_date ?? "Daily AI brief"}</span>
          </div>
        </div>
        <div className="actions">
          <button className="iconButton" type="button" title="Refresh" onClick={() => window.location.reload()}>
            <RefreshCw size={17} />
          </button>
          <button className="textButton" type="button" onClick={startRun} disabled={!targetDay || ["running", "queued", "busy"].includes(runState)}>
            <Play size={16} />
            {["partial", "pending_summary", "failed"].includes(runState) ? "补跑" : "更新日报"}
          </button>
        </div>
      </header>

      <section className="layout">
        <div className="band">
          <div className="signalGrid">
            <div className="metric">
              <span>Digest Items</span>
              <strong>{digest ? allDigestItems.length : "—"}</strong>
              <em>{data.isFallback ? "部分接口不可用" : digest?.digest_date === scheduler?.target_date ? "目标日期快照" : "历史快照"}</em>
            </div>
            <div className="metric">
              <span>Queue</span>
              <strong>{articlesAvailable ? articleCount : "—"}</strong>
              <em>{articlesAvailable ? "ranked articles" : "新闻队列状态未知"}</em>
            </div>
            <div className="metric">
              <span>Auto Update</span>
              <strong>{scheduler?.enabled ? scheduler.daily_time : scheduler ? "已暂停" : "Unknown"}</strong>
              <em>{scheduler?.timezone || "状态未知"}</em>
            </div>
            <div className="metric">
              <span>Fresh Sources</span>
              <strong>{sourcesAvailable ? freshSourceCount : "—"}</strong>
              <em>{sourcesAvailable ? `${sourceCount} enabled` : "来源状态未知"}</em>
            </div>
          </div>
        </div>

        <section className="panel">
          {data.errors?.length ? <p className="empty" role="status">{data.errors.join(" · ")}</p> : null}
          <div className="readingTools">
            <label>日报日期<select aria-label="日报日期" value={digest?.digest_date || ""} disabled={readingBusy} onChange={e => openDigest(e.target.value)}>
              {!digest ? <option value="">无日报</option> : null}
              {Array.from(new Set([...(digest ? [digest.digest_date] : []), ...(data.history || []).map(d => d.digest_date)])).sort().reverse().map(day => <option key={day}>{day}</option>)}
            </select></label>
            <label>版本<select aria-label="日报版本" value={digest?.revision ?? 0} disabled={readingBusy || !versions.length} onChange={e => digest && openDigest(digest.digest_date, e.target.value)}>
              {(versions.length ? versions : digest ? [digest] : []).map(v => <option key={v.revision ?? 0} value={v.revision ?? 0}>v{v.revision ?? 0} · {statusLabel(v.publication_status || "legacy_read_only")}</option>)}
            </select></label>
            <label className="searchLabel"><Search size={14} /><input aria-label="搜索当前日报" placeholder="搜索当前日报" value={query} onChange={e => setQuery(e.target.value)} /></label>
            <label>栏目<select aria-label="栏目" value={category} onChange={e => setCategory(e.target.value)}><option value="">全部</option>{digest?.sections.filter(s => s.items.length).map(s => <option key={s.key} value={s.key}>{s.title}</option>)}</select></label>
            <label><input type="checkbox" checked={savedOnly} onChange={e => setSavedOnly(e.target.checked)} />仅收藏</label>
            <button className="iconButton" title="清除阅读偏好" aria-label="清除阅读偏好" onClick={resetVotes} disabled={Boolean(pendingVote) || !data.preferences}><RotateCcw size={15} /></button>
            <button className="iconButton" title="下载 Markdown" aria-label="下载 Markdown" disabled={!digest || readingBusy} onClick={() => digest && downloadDigest(digest, "md")}><Download size={15} /></button>
            <button className="iconButton" title="下载 JSON" aria-label="下载 JSON" disabled={!digest || readingBusy} onClick={() => digest && downloadDigest(digest, "json")}><FileJson size={15} /></button>
          </div>
          <p className="runStatus" role="status">目标：{targetDay || "未知"} · 运行：{statusLabel(runState)}{runDetail.stage ? ` · ${statusLabel(runDetail.stage)}` : ""}{runDetail.retry_at ? ` · 可重试：${formatDateTime(runDetail.retry_at)}` : ""}</p>
          {notice ? <p className="empty" role="status">{notice}</p> : null}
          <div className="panelHeader">
            <h2>{digest?.title ?? "Latest Digest"}</h2>
            <span>{sourcesAvailable ? `${sourceCount} enabled sources / 已启用` : "来源状态未知"}</span>
          </div>
          {digest?.sections.length ? (
            <div className="digest">
              {visibleSections.map((section) => (
                <div className="section" key={section.key}>
                  <h3>{section.title}</h3>
                  {section.message ? <p className="empty">{section.message}</p> : null}
                  {section.window ? <p className="empty">{section.window.start} → {section.window.cutoff} ({section.window.timezone})</p> : null}
                  {section.items.map((item) => {
                    const isSummaryOpen = Boolean(openSummaries[item.id]);
                    return (
                      <article className={isSynthetic(item) ? "article articleInsight" : "article"} key={item.id}>
                        <div className="articleTitle">
                          <div>
                            <h4>{item.title}</h4>
                            <span>{sourceLabel(item)}</span>
                            {isSynthetic(item) ? <span>合成观点 · 以各证据时间为准 · 推断未核验</span> : <><span>发布：{item.published_at || "未知"} · 抓取：{item.fetched_at || "未知"}</span><span>{statusLabel(item.time_status || "legacy_unknown")} · {statusLabel(item.summary_mode || "legacy_unknown")} · 来源事实未核验</span></>}
                          </div>
                          {!isSynthetic(item) ? <span className="score">{formatScore(item.score)}</span> : null}
                        </div>
                        {!isSynthetic(item) && /^[0-9a-f-]{36}$/i.test(item.id) ? <div className="feedbackRow" aria-label="阅读反馈">
                          {([{vote: "saved", title: "收藏", Icon: Bookmark}, {vote: "irrelevant", title: "不相关", Icon: Ban}, {vote: "duplicate", title: "重复", Icon: Copy}]).map(({vote, title, Icon}) => <button key={vote} className="iconButton" title={title} aria-label={title} aria-pressed={votes[item.id] === vote} disabled={Boolean(pendingVote) || !data.preferences} onClick={() => feedback(item.id, vote)}><Icon size={15} /></button>)}
                          {votes[item.id] ? <span>{({saved: "已收藏", irrelevant: "已标为不相关", duplicate: "已标为重复"} as Record<string,string>)[votes[item.id]] || "已反馈"}</span> : null}
                        </div> : null}
                        {!isSynthetic(item) ? <details className="ranking"><summary>重要性依据 · {formatScore(item.score)}</summary>{item.ranking ? <><p>{Object.entries({base: "基础", source_quality: "来源质量", recency: "时效", signals: "内容信号", company_context: "主体背景", body_penalty: "正文调整"}).map(([key,label]) => `${label} ${Math.round((item.ranking?.components[key] || 0)*100)}`).join(" · ")}</p><p>新颖性仅以发布时间近似，不代表已证明首次事件。影响对象：{item.ranking.affected_audience === "researchers" ? "研究者" : "AI 从业者"}。</p><p>{item.ranking.uncertainty.map(statusLabel).join(" · ")} · {item.source_count && item.source_count > 1 ? "多来源仍不等同独立核实" : "来源有限"}</p></> : <p>旧版评分依据未保存，不能追溯解释。</p>}</details> : null}
                        <div className="choiceRow">
                          <button
                            aria-expanded={isSummaryOpen}
                            className="choiceButton"
                            type="button"
                            onClick={() => toggleSummary(item.id)}
                          >
                            <FileText size={15} />
                            AI Summary / AI 总结
                          </button>
                          {isSynthetic(item) ? <button className="choiceButton" type="button" aria-expanded={Boolean(openEvidence[item.id])} onClick={() => setOpenEvidence(current => ({...current, [item.id]: !current[item.id]}))}><ExternalLink size={15} />Evidence / 证据</button> : <a className="choiceButton choiceLink" href={item.url} target="_blank" rel="noreferrer">
                            <ExternalLink size={15} />
                            Original / 原文
                          </a>}
                        </div>
                        {isSummaryOpen ? (
                          <SummaryPanel item={item} />
                        ) : null}
                        {item.source_count != null ? <p className="why">{item.source_count} 个来源组 · {item.document_count} 篇报道{item.coverage_status === "limited_sources" ? " · 来源有限" : ""}</p> : null}
                        {item.evidence_review_required ? <p className="assumption">来源标题的数字或否定表述有差异，需核对原文。</p> : null}
                        {(item.event_sources?.length ?? 0) > 1 ? (
                          <div className="evidence">
                            {item.event_sources!.map((source) => (
                              <a key={`${source.article_id}-${source.url}`} href={source.url} target="_blank" rel="noreferrer">
                                <span>{source.source}{source.is_reprint ? " · 转载/归因" : source.same_content ? " · 相同正文" : ""}</span>{source.title}
                              </a>
                            ))}
                          </div>
                        ) : null}
                        {isSynthetic(item) ? <InsightPanel item={item} showClaims={isSummaryOpen} showEvidence={Boolean(openEvidence[item.id])} revealEvidence={anchor => { setOpenEvidence(current => ({...current, [item.id]: true})); requestAnimationFrame(() => { const target = document.getElementById(anchor); target?.scrollIntoView({block: "center"}); target?.focus({preventScroll: true}); }); }} /> : null}
                        <div className="tags">
                          {!isSynthetic(item) && item.confidence ? <span className="tag">confidence {item.confidence}</span> : null}
                          {(!isSynthetic(item) ? item.sectors : [])?.map((sector) => (
                            <span className="tag" key={sector}>
                              {sector}
                            </span>
                          ))}
                          {item.tags.map((tag) => (
                            <span className="tag" key={tag}>
                              {tag}
                            </span>
                          ))}
                        </div>
                      </article>
                    );
                  })}
                </div>
              ))}
              {allDigestItems.length > 0 && visibleItemCount === 0 ? <p className="empty" role="status">当前日报没有匹配条目。</p> : null}
            </div>
          ) : (
            <p className="empty">No digest yet.</p>
          )}
        </section>

        <aside className="sidebar">
          <section className="panel">
            <div className="panelHeader">
              <h2>Auto Update</h2>
              <span>
                <CalendarClock size={13} /> {scheduler?.enabled ? "configured" : scheduler ? "paused" : "unknown"}
              </span>
            </div>
            <div className="automationCard">
              <div>
                <span>Daily run</span>
                <strong>{scheduler?.enabled ? scheduler.daily_time : scheduler ? "Paused" : "Unknown"}</strong>
              </div>
              <div>
                <span>Timezone</span>
                <strong>{scheduler?.timezone || "Unknown"}</strong>
              </div>
              <div>
                <span>Latest digest</span>
                <strong>{scheduler?.latest_digest_date ?? "Unknown"}</strong>
              </div>
              <em>Last generated: {formatDateTime(scheduler?.latest_digest_created_at)}</em>
              <em>配置不代表已执行；定时进程：{statusLabel(data.runtime?.scheduler)}。</em>
              <div className="runtimeStatus" aria-label="后台依赖状态">{(["database", "redis", "worker"] as const).map(key => <span key={key}>{({database: "数据库", redis: "队列", worker: "工作进程"})[key]}：{statusLabel(data.runtime?.[key])}</span>)}</div>
            </div>
          </section>

          <section className="panel">
            <div className="panelHeader">
              <h2>Source Health</h2>
              <span>
                <Rss size={13} /> {sourcesAvailable ? data.sourceHealth.length : "未知"}
              </span>
            </div>
            <div className="sourceList">
              {!data.sourceHealth.length ? <p className="empty">{sourcesAvailable ? "暂无已配置来源。" : "来源状态暂不可用，请刷新重试。"}</p> : null}
              {data.sourceHealth.map((source) => (
                <div className="sourceItem" key={source.id}>
                  <div>
                    <a href={source.url} target="_blank" rel="noreferrer">
                      {source.name}
                    </a>
                    <span>{statusLabel(source.category_hint)}</span>
                    <span>{statusLabel(source.origin_kind)} · 正文：{statusLabel(source.body_status)}</span>
                    {source.body_results && Object.keys(source.body_results).length ? <span>{Object.entries(source.body_results).map(([status, count]) => `${statusLabel(status)} × ${count}`).join(" / ")} · HTTP {source.body_http_statuses?.map((code) => code ?? "未知").join(", ")}</span> : null}
                    <span>最近检查：{source.last_attempt_at || "未检查"}</span>
                    <span>最近正文成功：{source.last_body_success_at || "未知"}</span>
                    <span>HTTP {source.http_status ?? "未知"} · {source.latency_ms == null ? "延迟未知" : `${Math.round(source.latency_ms)} ms`}</span>
                    {source.next_retry_at ? <span>重试：{source.next_retry_at}</span> : null}
                    {!source.enabled && source.observed_status ? <span>观测：{statusLabel(source.observed_status)}</span> : null}
                    <div className="sourceControls">
                      <button type="button" className="iconButton" title="检查来源 URL、feed 与正文" aria-label={`检查 ${source.name}`}
                        disabled={sourceActions[source.id] === "busy"} onClick={() => updateSource(source, "probe")}><RefreshCw size={14} /></button>
                      <label><input type="checkbox" checked={enabledOverrides[source.id] ?? source.enabled}
                        disabled={sourceActions[source.id] === "busy" || (!source.enabled && !source.activation_allowed)}
                        onChange={() => updateSource(source, "toggle")} />启用</label>
                    </div>
                    {sourceActions[source.id] && sourceActions[source.id] !== "busy" ? <span role="status">{sourceActions[source.id]}</span> : null}
                  </div>
                  <div className="sourceMeta">
                    <strong>{formatScore(source.credibility)}</strong>
                    <em className={`health health-${source.health_status}`}>{healthLabel(source)}</em>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panelHeader">
              <h2>Article Queue</h2>
              <span>
                <Activity size={13} /> {articlesAvailable ? "已载入" : "不可用"}
              </span>
            </div>
            <div className="queue">
              {!data.articles.length ? <p className="empty">{articlesAvailable ? "暂无已载入新闻。" : "新闻队列暂不可用，请刷新重试。"}</p> : null}
              {data.articles.map((article) => (
                <div className="queueItem" key={article.id}>
                  <a href={article.canonical_url} target="_blank" rel="noreferrer">
                    {article.title}
                  </a>
                  <div className="queueMeta">
                    <span className="dot" aria-hidden="true" />
                    <span>{article.category}</span>
                    <span>{formatScore(article.importance_score)}</span>
                    <ExternalLink size={12} />
                  </div>
                </div>
              ))}
            </div>
          </section>
        </aside>
      </section>
    </main>
  );
}
