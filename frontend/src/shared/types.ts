export interface Job {
  id: number;
  round_id: number | null;
  status: string;
  status_label: string;
  stage: string | null;
  progress_done: number;
  progress_total: number;
  cost: number;
  error: string | null;
  pid: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Round {
  id: number;
  name: string;
  category: string | null;
  status: string;
  status_label: string;
  pages: number;
  max_creators: number | null;
  platform: string;
  platform_label: string;
  provider: string;
  provider_label: string;
  started_at: string | null;
  finished_at: string | null;
  cost_tokens: number;
  error: string | null;
  created_at: string;
  keywords: string;
  keyword_details?: { word: string; category: string | null; pages: number }[];
  summary?: { creators: number; notes: number; note_hits: number; cost: number };
  job?: Job | null;
}

export interface Creator {
  key: string;
  round_id: number | null;
  platform: string;
  platform_label: string;
  user_id: string;
  account_id: string;
  nickname: string;
  avatar: string;
  fans: number | null;
  follows: number | null;
  collected: number | null;
  fans_wan: string;
  fans_tier: string;
  round_name: string;
  data_round: string;
  status: string;
  status_label: string;
  match_direction: string;
  posted: number | null;
  liked: number | null;
  interaction: number | null;
  tags: string;
  auto_tags: string[];
  tags_manual: string;
  profile_url: string;
  red_id: string;
  top_title: string;
  top_url: string;
  top_liked: number | null;
  source_keywords: string[];
  source: string;
  remark: string;
  executor: string;
  cooperation_platform: string;
  raw_title: string;
  sample_notes: number;
  sample_likes: number;
  sample_collected: number;
  sample_comments: number;
  sample_interactions: number;
  ip_location: string;
  verify_info: string;
  fetched_at: string;
}

export interface Note {
  note_id: string;
  title: string;
  type: string;
  published_at: number | null;
  liked: number;
  collected: number;
  comments: number;
  shares: number;
  url: string;
  keywords: string[];
}

export interface CreatorDetail extends Creator {
  desc: string;
  creator_tags: string[];
  raw_json: Record<string, unknown>;
  notes: Note[];
}

export interface Keyword {
  id: number;
  word: string;
  enabled: number;
  rounds: number;
  created_at: string;
}

export interface ProviderSettings {
  id: string;
  label: string;
  adapter: string;
  host: string;
  hostname: string;
  token_configured: boolean;
  ready: boolean;
}

export interface PlatformProviderPrice {
  id: string;
  label: string;
  ready: boolean;
  enabled: boolean;
  search_cost_native: string;
  search_currency: string;
  search_cost_tokens: number;
  search_cost_cny: string;
  user_cost_native: string;
  user_currency: string;
  user_cost_tokens: number;
  user_cost_cny: string;
}

export interface PlatformSettings {
  code: string;
  label: string;
  default_provider: string | null;
  providers: PlatformProviderPrice[];
}

export interface Settings {
  host: string;
  token_configured: boolean;
  rnote_host: string;
  rnote_token_configured: boolean;
  output_dir: string;
  cache_days: number;
  xhs_cost: number;
  douyin_cost: number;
  credentials_ok: boolean;
  providers: ProviderSettings[];
  platforms: PlatformSettings[];
  default_provider: string | null;
}

export interface Balance {
  remaining: number | null;
  text: string;
  remaining_native?: number | null;
  currency?: string;
  tokens?: number | null;
}

export interface AuthUser {
  id: number;
  username: string;
  role: "admin" | "member";
  disabled: boolean;
  can_collect: boolean;
  must_change_password: boolean;
  auth_source: string;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}

export interface AuthResponse {
  user: AuthUser | null;
  csrf_token: string;
}

export interface Counts {
  rounds: number;
  keywords: number;
  notes: number;
  creators: number;
  creator_rounds: number;
}

export interface DashboardMetrics {
  creators: number;
  enriched: number;
  pending: number;
  adopted: number;
  cost: number;
}

export interface DashboardData {
  counts: Counts;
  recent_rounds: Round[];
  round?: Round;
  tier_counts?: Record<string, number>;
  status_counts?: Record<string, number>;
  top_creators?: Creator[];
  metrics?: DashboardMetrics;
}

export interface ApiCall {
  id: number;
  round_id: number | null;
  platform: string | null;
  provider: string | null;
  kind: string | null;
  path: string | null;
  params: string | null;
  code: number | null;
  cost: number;
  cost_native: number;
  cost_currency: string;
  duration_ms: number | null;
  raw_file: string | null;
  ts: string;
}

export interface CreateRoundPayload {
  keywords: string[];
  category?: string | null;
  pages: number;
  max_creators: number | null;
  name?: string | null;
  platform: string;
  provider: string;
  refresh_users: boolean;
}

export interface BatchUpdatePayload {
  items: { platform: string; user_id: string }[];
  status?: string | null;
  match_direction?: string | null;
  tags_manual?: string | null;
  remark?: string | null;
}

export interface StatusDef {
  code: string;
  label: string;
  color: string;
  order: number;
}

export interface PlatformDef {
  code: string;
  label: string;
}

export interface Meta {
  statuses: StatusDef[];
  default_status: string;
  platforms: PlatformDef[];
}

export interface CreatorListResponse {
  items: Creator[];
  total: number;
  page: number;
  page_size: number;
}

export interface FacetItem {
  value: string;
  count: number;
}

export interface Facets {
  platforms: FacetItem[];
  statuses: FacetItem[];
  tiers: FacetItem[];
  directions: FacetItem[];
  sources: FacetItem[];
}

export interface CreatorQuery {
  platform: string;
  page: number;
  page_size: number;
  search: string;
  statuses: string[];
  tiers: string[];
  directions: string[];
  sources: string[];
  min_fans: string;
  max_fans: string;
  only_unenriched: boolean;
  sort: string;
  order: "asc" | "desc";
}

