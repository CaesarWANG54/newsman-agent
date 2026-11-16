export type Insight = {
  version: string;
  kind: "startup" | "investment";
  status: "no_conclusion" | "review_required";
  reason: string;
  publishable: false;
  as_of: string | null;
  valid_until: string | null;
  generation: string;
  confidence_basis: { reviewed_origins: number; machine_source_count?: number; human_review_count?: number; limitations: string[] };
  claims: { id: string; role: string; kind: string; text_en: string; text_zh: string; fact_ids: string[]; counter_fact_ids: string[] }[];
  companies: { entity: string; symbol?: string | null; fact_ids: string[] }[];
  evidence_context: { sources: { article_id: string; source_id: string; source: string; url: string; title: string; published_at: string; reviewed_at?: string | null; extracted_at?: string | null; method?: string; body_scope?: string; facts: { id: string; quote: string; start: number; end: number; valid_from: string; valid_until: string }[] }[] } | null;
};

export type DigestItem = {
  id: string;
  title: string;
  url: string;
  source?: string;
  summary?: string;
  summary_en?: string;
  summary_zh?: string;
  why_it_matters?: string;
  why_it_matters_en?: string;
  why_it_matters_zh?: string;
  summary_mode?: string;
  quality_status?: string;
  translation_status?: string;
  body_status?: string;
  verification_status?: string;
  consistency_status?: string;
  quality_issues?: string[];
  source_excerpt?: string;
  excerpt_language?: string;
  published_at?: string | null;
  fetched_at?: string | null;
  event_at?: string | null;
  time_status?: string;
  cluster_id?: string | null;
  cluster_method?: string | null;
  event_sources?: { article_id: string; source: string; url: string; title: string; is_reprint?: boolean; same_content?: boolean }[];
  source_count?: number | null;
  document_count?: number | null;
  coverage_status?: string;
  evidence_review_required?: boolean;
  score: number;
  ranking?: { version: string; components: Record<string, number>; affected_audience: string; uncertainty: string[] } | null;
  tags: string[];
  analysis?: {
    background?: string;
    outlook?: string;
    risks?: string;
  };
  evidence?: {
    title: string;
    url: string;
    source: string;
    score?: number;
  }[];
  confidence?: string;
  sectors?: string[];
  watchlist?: string[];
  conservative_assumption?: string;
  insight?: Insight | null;
};

export type DigestSection = {
  key: string;
  title: string;
  items: DigestItem[];
  status?: string;
  message?: string;
  window?: { target_date: string; timezone: string; start: string; cutoff: string; observed_at: string };
};

export type Digest = {
  id: string;
  digest_date: string;
  title: string;
  brief: string;
  sections: DigestSection[];
  markdown: string;
  created_at: string;
  revision?: number;
  status?: string;
  publication_status?: string;
};

export type Article = {
  id: string;
  url: string;
  canonical_url: string;
  title: string;
  published_at?: string;
  fetched_at: string;
  category: string;
  tags: string[];
  cluster_key?: string;
  importance_score: number;
  summary?: string;
  why_it_matters?: string;
};

export type SourceHealth = {
  id: string;
  name: string;
  url: string;
  feed_url?: string | null;
  source_type: string;
  category_hint: string;
  credibility: number;
  enabled: boolean;
  article_count: number;
  latest_article_at?: string | null;
  latest_article_title?: string | null;
  health_status: "fresh" | "stale" | "quiet" | "empty" | "disabled" | string;
  observed_status?: string;
  freshness_status?: string;
  last_attempt_at?: string | null;
  last_success_at?: string | null;
  last_body_success_at?: string | null;
  next_retry_at?: string | null;
  http_status?: number | null;
  latency_ms?: number | null;
  body_status?: string;
  origin_kind?: string;
  url_verification?: string;
  activation_allowed?: boolean;
  body_results?: Record<string, number>;
  body_http_statuses?: (number | null)[];
};

export type SchedulerStatus = {
  enabled: boolean;
  task_name: string;
  timezone: string;
  daily_time: string;
  daily_update_hour: number;
  daily_update_minute: number;
  latest_digest_date?: string | null;
  latest_digest_created_at?: string | null;
  backend_scheduler: string;
  target_date?: string;
  run_status?: string;
};

export type RuntimeStatus = { database: string; redis: string; worker: string; scheduler: string; issues?: string[]; instance_id?: string };

export type DashboardData = {
  digest?: Digest;
  articles: Article[];
  sourceHealth: SourceHealth[];
  schedulerStatus?: SchedulerStatus;
  errors?: string[];
  history?: Pick<Digest, "digest_date" | "title" | "revision" | "publication_status">[];
  preferences?: Record<string, string>;
  runtime?: RuntimeStatus;
  availability?: { articles: boolean; sourceHealth: boolean };
  isFallback: boolean;
};

export function getApiBaseUrl(): string {
  const browserUrl = process.env.NEXT_PUBLIC_NEWSMAN_API_BASE_URL ?? "http://localhost:8000";
  return typeof window === "undefined"
    ? process.env.NEWSMAN_API_INTERNAL_URL ?? browserUrl
    : browserUrl;
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    cache: "no-store",
    signal: AbortSignal.timeout(5000),
    ...init
  });
  if (!response.ok) throw new Error(`NewsMan API error ${response.status}`);
  return response.json() as Promise<T>;
}

export async function getDashboardData(): Promise<DashboardData> {
  const [digest, articles, sourceHealth, scheduler, history, preferences, runtime] = await Promise.allSettled([
    fetchJson<Digest>("/api/digests/latest"),
    fetchJson<Article[]>("/api/articles?limit=30"),
    fetchJson<SourceHealth[]>("/api/sources/health"),
    fetchJson<SchedulerStatus>("/api/scheduler/status"),
    fetchJson<Digest[]>("/api/digests?limit=365"),
    fetchJson<Record<string, string>>("/api/preferences"),
    fetchJson<RuntimeStatus>("/api/runtime/status")
  ]);
  const errors: string[] = [];
  if (digest.status === "rejected") errors.push("Digest unavailable / 日报暂不可用");
  if (articles.status === "rejected") errors.push("Article queue unavailable / 新闻队列暂不可用");
  if (sourceHealth.status === "rejected") errors.push("Source health unavailable / 来源状态暂不可用");
  if (scheduler.status === "rejected") errors.push("Schedule status unknown / 调度状态未知");
  if (history.status === "rejected") errors.push("历史日报暂不可用");
  if (preferences.status === "rejected") errors.push("阅读偏好暂不可用");
  if (runtime.status === "rejected") errors.push("后台依赖状态未知");
  return {
    digest: digest.status === "fulfilled" ? digest.value : undefined,
    articles: articles.status === "fulfilled" ? articles.value : [],
    sourceHealth: sourceHealth.status === "fulfilled" ? sourceHealth.value : [],
    schedulerStatus: scheduler.status === "fulfilled" ? scheduler.value : undefined,
    history: history.status === "fulfilled" ? history.value.map(({digest_date, title, revision, publication_status}) => ({digest_date, title, revision, publication_status})) : [],
    preferences: preferences.status === "fulfilled" ? preferences.value : undefined,
    runtime: runtime.status === "fulfilled" ? runtime.value : undefined,
    availability: { articles: articles.status === "fulfilled", sourceHealth: sourceHealth.status === "fulfilled" },
    isFallback: errors.length > 0,
    errors
  };
}
