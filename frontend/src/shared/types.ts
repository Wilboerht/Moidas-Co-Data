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
  created_by?: number | null;
  cancelled_by?: number | null;
  cancelled_at?: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at?: string | null;
  active_count?: number;
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
  archived_at?: string | null;
  archived?: boolean;
  keywords: string;
  keyword_details?: { word: string; category: string | null; pages: number }[];
  summary?: { creators: number; leads: number; notes: number; note_hits: number; cost: number };
  job?: Job | null;
}

export interface RoundImpact {
  round_id: number;
  name: string;
  archived_at?: string | null;
  creators: number;
  creators_reviewed: number;
  creators_claimed: number;
  notes: number;
  note_hits: number;
  leads: number;
  leads_converted: number;
  jobs: number;
  jobs_active: number;
  api_calls: number;
  cost_tokens: number;
  raw_files: number;
  log_files: number;
  can_purge: boolean;
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
  round_status: string;
  round_status_label: string;
  cooperation_status: string;
  cooperation_updated_at: string;
  enrichment_status: "not_fetched" | "partial" | "complete";
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
  source_labels: string[];
  matched_keywords: string;
  source: string;
  remark: string;
  owner_user_id: number | null;
  owner_username: string;
  claimed_at: string | null;
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
  updated_at: string;
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

export interface Lead {
  id: number;
  platform: string;
  platform_label: string;
  nickname: string;
  account_id: string;
  profile_url: string;
  raw_title: string;
  source: string;
  source_label: string;
  source_round_id: number | null;
  source_round_name: string;
  owner_user_id: number | null;
  owner_username: string;
  status: string;
  status_label: string;
  legacy_user_id: string;
  created_by: number | null;
  created_at: string | null;
  updated_at: string | null;
  converted_platform: string;
  converted_user_id: string;
  converted_at: string | null;
  matched_platform: string;
  matched_user_id: string;
  matched_round_id: number | null;
  matched_round_name: string;
  matched_at: string | null;
  match_confidence: string;
}

export interface LeadRound {
  lead_id: number;
  round_id: number;
  round_name: string;
  source_keywords: string;
  source_labels: string;
  raw_title: string;
  data_round: string;
  top_note_id: string;
  sample_notes: number;
  sample_likes: number;
  sample_collected: number;
  sample_comments: number;
  sample_interactions: number;
  updated_at: string;
}

export interface LeadAttempt {
  id: number;
  lead_id: number;
  provider: string;
  query: string;
  candidate_user_id: string;
  candidate_nickname: string;
  candidate_profile_url: string;
  confidence: string;
  status: string;
  error: string;
  cost: number;
  cost_native: number;
  cost_currency: string;
  round_id: number | null;
  created_by: number | null;
  created_at: string;
}

export interface LeadMatchedCreator {
  platform: string;
  user_id: string;
  nickname: string;
  avatar: string;
  fans: number | null;
  profile_url: string;
}

export interface LeadDetail extends Lead {
  raw_json: Record<string, unknown>;
  rounds: LeadRound[];
  attempts: LeadAttempt[];
  matched_creator?: LeadMatchedCreator | null;
}

export interface LeadCreateResponse extends LeadDetail {
  created: boolean;
  revived: boolean;
  match_error?: string;
}

export interface LeadIdentityHealth {
  account_index: boolean;
  profile_index: boolean;
  duplicate_account_groups: number;
  duplicate_profile_groups: number;
}

export interface LeadMatchResult {
  scanned: number;
  matched: number;
  updated: number;
  ambiguous: number;
  skipped: number;
  attempts: number;
  lead_ids: number[];
  examples: { lead_id: number; user_id: string; confidence: string }[];
}

export interface LeadOwner {
  id: number;
  username: string;
  role: string;
  can_collect: number;
}

export interface LeadFacet {
  value: string;
  count: number;
}

export interface LeadFacets {
  platforms: LeadFacet[];
  statuses: LeadFacet[];
  sources: LeadFacet[];
  owners: LeadFacet[];
  rounds: LeadFacet[];
}

export interface LeadQuery {
  round_id: number | null;
  platform: string;
  search: string;
  statuses: string[];
  sources: string[];
  owner: string;
  page: number;
  page_size: number;
  sort: string;
  order: "asc" | "desc";
}

export interface LeadListResponse {
  items: Lead[];
  total: number;
  page: number;
  page_size: number;
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
  token_cny: number;
  usd_cny: number;
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
  leads: number;
}

export interface DashboardMetrics {
  creators: number;
  enriched: number;
  pending: number;
  adopted: number;
  cost: number;
}

export interface LeadMetrics {
  leads: number;
  unverified: number;
  verifying: number;
  converted: number;
  duplicate: number;
  discarded: number;
}

export interface DashboardData {
  counts: Counts;
  round?: Round;
  recent_rounds?: Round[];
  top_creators?: Creator[];
  tier_counts?: Record<string, number>;
  status_counts?: Record<string, number>;
  metrics?: DashboardMetrics;
  lead_metrics?: LeadMetrics;
}

export interface ActivityCalendarDay {
  date: string;
  collected: number;
  processed: number;
}

export interface ActivityCalendarResponse {
  days: number;
  start: string;
  end: string;
  items: ActivityCalendarDay[];
  totals: {
    collected: number;
    processed: number;
  };
}

export interface ActionQueueItem {
  key: string;
  round_id: number;
  round_name: string;
  platform: string;
  platform_label: string;
  user_id: string;
  nickname: string;
  avatar: string;
  fans: number | null;
  fans_wan: string;
  interaction: number | null;
  tier: string;
  sample_notes: number;
  sample_interactions: number;
  status: string;
  status_label: string;
  owner_user_id: number | null;
  owner_username: string;
  match_direction: string;
  source_keywords: string[];
  updated_at: string;
}

export interface ActionQueueCounts {
  all: number;
  pending: number;
  unclaimed: number;
}

export interface ActionQueueResponse {
  counts: ActionQueueCounts;
  items: ActionQueueItem[];
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
  items: {
    platform: string;
    user_id: string;
    expected_updated_at?: string;
    expected_status?: string;
    expected_cooperation_status?: string;
    expected_cooperation_updated_at?: string;
  }[];
  status?: string | null;
  cooperation_status?: string | null;
  match_direction?: string | null;
  tags_manual?: string | null;
  remark?: string | null;
  set_owner?: boolean;
  owner_user_id?: number | null;
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

export interface LeadStatusDef {
  code: string;
  label: string;
  color: string;
  order: number;
}

export interface LeadSourceDef {
  code: string;
  label: string;
}

export interface Meta {
  statuses: StatusDef[];
  default_status: string;
  lead_statuses: LeadStatusDef[];
  lead_sources: LeadSourceDef[];
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
  labels: FacetItem[];
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
  source_mode: "any" | "all";
  labels: string[];
  owner: string;
  min_fans: string;
  max_fans: string;
  only_unenriched: boolean;
  needs_action: boolean;
  sort: string;
  order: "asc" | "desc";
}

