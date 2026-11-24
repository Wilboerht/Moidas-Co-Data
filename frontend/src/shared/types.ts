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
  process_terminated?: boolean;
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
  cooperation_status_label: string;
  cooperation_updated_at: string;
  cooperation_count: number;
  cooperation_last_completed_at: string;
  cooperation_case_id: number | null;
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

export interface CooperationHistoryEntry {
  id: number;
  case_id?: number;
  ts: string;
  from_status: string;
  to_status: string;
  event_type?: string;
  note?: string;
  auto: boolean;
  batch: boolean;
  actor_username: string;
}

export interface CooperationCase {
  id: number;
  platform: string;
  user_id: string;
  seq: number;
  status: string;
  status_label: string;
  summary: string;
  started_at: string;
  status_updated_at: string;
  completed_at: string | null;
  closed_at: string | null;
  created_by: number | null;
  created_at: string;
  updated_at: string;
  event_count: number;
  attachment_count: number;
  work_count: number;
  active: boolean;
  summary_info?: CooperationSummary;
  creator?: {
    platform: string;
    user_id: string;
    nickname: string;
    avatar: string;
    follow_up_user_id: number | null;
  };
}

export interface CooperationEvent {
  id: number;
  case_id: number;
  event_type: string;
  status_from: string | null;
  status_to: string | null;
  from_status_label: string;
  to_status_label: string;
  note: string | null;
  metadata: Record<string, unknown>;
  actor_user_id: number | null;
  actor_username: string;
  created_at: string;
}

export interface CooperationAttachment {
  id: number;
  case_id: number;
  event_id: number | null;
  original_name: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  category: string;
  category_label: string;
  description: string;
  uploaded_by: number | null;
  uploaded_by_username: string;
  created_at: string;
  download_url: string;
}

export interface CooperationWork {
  id: number;
  case_id: number;
  platform: string;
  url: string;
  title: string;
  published_at: string;
  note: string;
  created_by: number | null;
  created_by_username: string;
  created_at: string;
  updated_at: string;
}

export interface CooperationSummary {
  cooperation_status: string;
  cooperation_updated_at: string;
  cooperation_count: number;
  cooperation_last_completed_at: string;
  cooperation_case_id: number | null;
}

export interface CooperationCaseDetail {
  case: CooperationCase;
  summary: CooperationSummary;
  events: CooperationEvent[];
  attachments: CooperationAttachment[];
  works: CooperationWork[];
  change?: Record<string, unknown>;
  created?: Record<string, unknown>;
  uploaded_attachment_id?: number;
}

export interface CooperationCaseList {
  items: CooperationCase[];
}

export interface CooperationWorkPayload {
  platform?: string;
  url: string;
  title?: string;
  published_at?: string;
  note?: string;
}

export interface CreatorDetail extends Creator {
  desc: string;
  creator_tags: string[];
  raw_json: Record<string, unknown>;
  cooperation_cases: CooperationCase[];
  cooperation_summary: CooperationSummary;
  cooperation_history: CooperationHistoryEntry[];
  notes: Note[];
}

export interface Lead {
  id: number;
  platform: string;
  platform_label: string;
  nickname: string;
  account_id: string;
  platform_handle: string;
  identity_status: string;
  identity_status_label: string;
  identity_confidence: string;
  identity_provider: string;
  identity_checked_at: string | null;
  identity_note: string;
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

export interface LeadIdentityEvent {
  id: number;
  lead_id: number;
  event_type: string;
  old_user_id: string;
  new_user_id: string;
  old_handle: string;
  new_handle: string;
  provider: string;
  confidence: string;
  status: string;
  note: string;
  actor_id: number | null;
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

export interface LeadVerifyResult {
  lead_id: number;
  platform: string;
  requested_user_id: string;
  requested_handle: string;
  requested_profile_url: string;
  resolved_user_id: string;
  resolved_handle: string;
  resolved_nickname: string;
  resolved_profile_url: string;
  avatar: string;
  fans: number | null;
  confidence: string;
  status: string;
  note: string;
  error: string;
  provider: string;
  cost: number;
  cost_native: number;
  cost_currency: string;
  estimated_cost: number;
  attempts: number;
  search_cost: number;
  resolution: string;
  checked_at: string;
  applied: boolean;
  lead?: Lead;
}

export interface LeadDetail extends Lead {
  raw_json: Record<string, unknown>;
  rounds: LeadRound[];
  attempts: LeadAttempt[];
  identity_events: LeadIdentityEvent[];
  matched_creator?: LeadMatchedCreator | null;
}

export interface LeadCreateResponse extends LeadDetail {
  created: boolean;
  revived: boolean;
  match_error?: string;
}

export interface LeadIdentityHealth {
  account_index: boolean;
  handle_index: boolean;
  profile_index: boolean;
  duplicate_account_groups: number;
  duplicate_handle_groups: number;
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
  can_edit_leads: number;
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
  can_edit_leads: boolean;
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

export interface DashboardCooperationMetrics {
  enrolled: number;
  open: number;
  stale: number;
  cooperating: number;
  completed: number;
  declined: number;
  cancelled: number;
  total_cases: number;
  completed_cases: number;
  repeat_creators: number;
}

export interface DashboardData {
  counts: Counts;
  round?: Round;
  recent_rounds?: Round[];
  top_creators?: Creator[];
  tier_counts?: Record<string, number>;
  status_counts?: Record<string, number>;
  metrics?: DashboardMetrics;
  cooperation_counts?: Record<string, number>;
  cooperation_metrics?: DashboardCooperationMetrics;
  cooperation_stale_days?: number;
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
  error?: string | null;
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

export interface CooperationStatusDef {
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

export interface CooperationAttachmentCategoryDef {
  code: string;
  label: string;
}

export interface Meta {
  statuses: StatusDef[];
  default_status: string;
  cooperation_statuses: CooperationStatusDef[];
  cooperation_attachment_categories: CooperationAttachmentCategoryDef[];
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
  cooperation_statuses: FacetItem[];
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
  cooperation: string[];
  cooperation_stale_days: string;
  min_fans: string;
  max_fans: string;
  only_unenriched: boolean;
  needs_action: boolean;
  sort: string;
  order: "asc" | "desc";
}

