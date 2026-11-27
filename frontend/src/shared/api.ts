import type {
  ActionQueueResponse,
  ActivityCalendarResponse,
  ApiCall,
  AuthResponse,
  AuthUser,
  Balance,
  BatchUpdatePayload,
  CreateRoundPayload,
  CooperationCaseDetail,
  CooperationSummary,
  CreatorEmail,
  EmailAccount,
  EmailAccountInput,
  EmailCampaign,
  EmailCampaignDetail,
  EmailRecipient,
  EmailSuppression,
  EmailTarget,
  EmailWorkerStatus,
  CooperationWorkPayload,
  CreatorDetail,
  CreatorListResponse,
  CreatorQuery,
  Counts,
  DashboardData,
  Facets,
  Job,
  Keyword,
  Lead,
  LeadCreateResponse,
  LeadDetail,
  LeadFacets,
  LeadIdentityHealth,
  LeadListResponse,
  LeadMatchResult,
  LeadOwner,
  LeadQuery,
  LeadVerifyResult,
  Meta,
  Round,
  RoundImpact,
  Settings,
} from "./types";

const BASE = "/api";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token;
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const record = item as { msg?: unknown; loc?: unknown };
          const loc = Array.isArray(record.loc) ? record.loc.join(".") : "";
          return loc ? `${loc}: ${String(record.msg)}` : String(record.msg);
        }
        return String(item);
      })
      .join("；");
  }
  return detail == null ? "" : JSON.stringify(detail);
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (csrfToken && UNSAFE_METHODS.has(method)) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login") {
      window.dispatchEvent(new Event("codata:unauthorized"));
    }
    let message = response.statusText;
    let detail: unknown;
    try {
      const payload = await response.json();
      detail = payload.detail;
      message = detailMessage(payload.detail) || response.statusText;
    } catch {
      // ignore non-JSON errors
    }
    throw new ApiError(message || "请求失败", response.status, detail);
  }
  return response.json() as Promise<T>;
}

async function uploadRequest<T>(path: string, body: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers,
    body,
  });
  if (!response.ok) {
    if (response.status === 401) {
      window.dispatchEvent(new Event("codata:unauthorized"));
    }
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = detailMessage(payload.detail) || response.statusText;
    } catch {
      // ignore non-JSON errors
    }
    throw new ApiError(message || "上传失败", response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  me: () => request<AuthResponse>("/auth/me"),
  login: (username: string, password: string) =>
    request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ ok: boolean }>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),
  getUsers: () => request<AuthUser[]>("/users"),
  createUser: (payload: { username: string; password: string; role: string; can_collect: boolean; can_edit_leads: boolean; can_send_email: boolean }) =>
    request<AuthUser>("/users", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateUser: (userId: number, payload: { role?: string; disabled?: boolean; can_collect?: boolean; can_edit_leads?: boolean; can_send_email?: boolean }) =>
    request<AuthUser>(`/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  resetUserPassword: (userId: number, newPassword: string) =>
    request<{ ok: boolean }>(`/users/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify({ new_password: newPassword }),
    }),
  revokeUserSessions: (userId: number) =>
    request<{ ok: boolean; revoked: number; kept_current: boolean }>(
      `/users/${userId}/revoke-sessions`,
      { method: "POST" },
    ),
  getRounds: (includeArchived = false) =>
    request<Round[]>(`/rounds${includeArchived ? "?include_archived=true" : ""}`),
  getRound: (roundId: number) => request<Round>(`/rounds/${roundId}`),
  getRoundJobs: (roundId: number) =>
    request<{ items: Job[] }>(`/rounds/${roundId}/jobs`),
  archiveRound: (roundId: number) =>
    request<Round>(`/rounds/${roundId}/archive`, { method: "POST" }),
  restoreRound: (roundId: number) =>
    request<Round>(`/rounds/${roundId}/restore`, { method: "POST" }),
  getRoundImpact: (roundId: number) => request<RoundImpact>(`/rounds/${roundId}/impact`),
  deleteRound: (roundId: number, confirmName: string) =>
    request<{ id: number; backup: string; impact: RoundImpact }>(`/rounds/${roundId}`, {
      method: "DELETE",
      body: JSON.stringify({ confirm_name: confirmName }),
    }),
  createRound: (payload: CreateRoundPayload) =>
    request<{ round: Round; job_id: number | null }>("/rounds", {
      method: "POST",
      body: JSON.stringify({ ...payload, start: true }),
    }),
  runRound: (roundId: number, refreshUsers = false, force = false, provider?: string) =>
    request<{ job_id: number; job: Job | null }>(`/rounds/${roundId}/run`, {
      method: "POST",
      body: JSON.stringify({ refresh_users: refreshUsers, force, provider }),
    }),
  cancelJob: (jobId: number) =>
    request<Job | null>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  getLatestJob: () => request<Job | null>("/jobs/latest"),
  getMeta: () => request<Meta>("/meta"),
  getCreators: (roundId: number | null, query: CreatorQuery) => {
    const params = new URLSearchParams();
    if (query.platform) params.set("platform", query.platform);
    params.set("page", String(query.page));
    params.set("page_size", String(query.page_size));
    params.set("sort", query.sort);
    params.set("order", query.order);
    if (query.search) params.set("search", query.search);
    if (query.statuses.length) params.set("status", query.statuses.join(","));
    if (query.tiers.length) params.set("tier", query.tiers.join(","));
    if (query.directions.length) params.set("direction", query.directions.join(","));
    if (query.sources.length) params.set("source", query.sources.join(","));
    if (query.sources.length && query.source_mode === "all") params.set("source_mode", "all");
    if (query.labels.length) params.set("label", query.labels.join(","));
    if (query.owner) params.set("owner", query.owner);
    if (query.cooperation.length) params.set("cooperation", query.cooperation.join(","));
    if (query.cooperation_stale_days) {
      params.set("cooperation_stale_days", query.cooperation_stale_days);
    }
    if (query.min_fans) params.set("min_fans", query.min_fans);
    if (query.max_fans) params.set("max_fans", query.max_fans);
    if (query.only_unenriched) params.set("only_unenriched", "true");
    if (query.needs_action) params.set("needs_action", "true");
    const path = roundId === null ? "/creators" : `/rounds/${roundId}/creators`;
    return request<CreatorListResponse>(`${path}?${params.toString()}`);
  },
  getFacets: (roundId: number | null, platform: string) => {
    const params = new URLSearchParams();
    if (roundId !== null) params.set("round_id", String(roundId));
    if (platform) params.set("platform", platform);
    const query = params.toString();
    return request<Facets>(roundId === null ? `/creators/facets${query ? `?${query}` : ""}` : `/rounds/${roundId}/facets${platform ? `?platform=${encodeURIComponent(platform)}` : ""}`);
  },
  getCreator: (roundId: number, platform: string, userId: string) =>
    request<CreatorDetail>(`/rounds/${roundId}/creators/${platform}/${encodeURIComponent(userId)}`),
  updateCreator: (
    roundId: number,
    platform: string,
    userId: string,
    payload: {
      status?: string;
      cooperation_status?: string;
      match_direction?: string;
      tags_manual?: string;
      remark?: string;
      expected_updated_at?: string;
      expected_status?: string;
      expected_cooperation_status?: string;
      expected_cooperation_updated_at?: string;
    },
  ) =>
    request<CreatorDetail>(`/rounds/${roundId}/creators/${platform}/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  updateCooperation: (
    platform: string,
    userId: string,
    payload: {
      status: string;
      expected_status?: string;
      expected_updated_at?: string;
    },
  ) =>
    request<{
      changed: boolean;
      previous_status: string;
      status: string;
      cooperation_status: string;
      cooperation_status_label: string;
      cooperation_updated_at: string;
      claimed: boolean;
    }>(`/creators/${platform}/${encodeURIComponent(userId)}/cooperation`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  batchUpdateCooperation: (payload: {
    status: string;
    items: {
      platform: string;
      user_id: string;
      expected_status?: string;
      expected_updated_at?: string;
    }[];
  }) =>
    request<{
      changed: number;
      conflicts: { platform: string; user_id: string; reason: string }[];
      claimed: number;
    }>("/creators/cooperation/batch", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getCooperationCase: (caseId: number) =>
    request<CooperationCaseDetail>(`/cooperation-cases/${caseId}`),
  deleteCooperationCase: (caseId: number) =>
    request<{
      deleted: boolean;
      case_id: number;
      platform: string;
      user_id: string;
      seq: number;
      status: string;
      summary: CooperationSummary;
      impact: {
        events: number;
        works: number;
        active_works: number;
        attachments: number;
        active_attachments: number;
      };
      backup: string;
    }>(`/cooperation-cases/${caseId}`, {
      method: "DELETE",
      body: JSON.stringify({ confirm: true }),
    }),
  createCooperationCase: (payload: {
    platform: string;
    user_id: string;
    status?: string;
    note?: string;
  }) =>
    request<CooperationCaseDetail>("/cooperation-cases", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateCooperationCaseStatus: (
    caseId: number,
    payload: {
      status: string;
      note?: string;
      expected_updated_at?: string;
    },
  ) =>
    request<CooperationCaseDetail>(`/cooperation-cases/${caseId}/status`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  completeCooperationCase: (
    caseId: number,
    payload: {
      note?: string;
      expected_updated_at?: string;
      works: CooperationWorkPayload[];
    },
  ) =>
    request<CooperationCaseDetail>(`/cooperation-cases/${caseId}/complete`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  addCooperationNote: (caseId: number, note: string) =>
    request<CooperationCaseDetail>(`/cooperation-cases/${caseId}/notes`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),
  updateCooperationCaseSummary: (
    caseId: number,
    summary: string,
    expectedUpdatedAt?: string,
  ) =>
    request<CooperationCaseDetail>(`/cooperation-cases/${caseId}`, {
      method: "PATCH",
      body: JSON.stringify({
        summary,
        expected_updated_at: expectedUpdatedAt,
      }),
    }),
  correctCooperationCaseStatus: (
    caseId: number,
    payload: { status: string; note: string },
  ) =>
    request<CooperationCaseDetail>(`/cooperation-cases/${caseId}/correct`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  uploadCooperationAttachment: (
    caseId: number,
    file: File,
    category: string,
    description: string,
  ) => {
    const form = new FormData();
    form.append("file", file);
    form.append("category", category);
    form.append("description", description);
    return uploadRequest<CooperationCaseDetail>(
      `/cooperation-cases/${caseId}/attachments`,
      form,
    );
  },
  deleteCooperationAttachment: (attachmentId: number) =>
    request<CooperationCaseDetail>(
      `/cooperation-attachments/${attachmentId}`,
      { method: "DELETE" },
    ),
  addCooperationWork: (caseId: number, payload: CooperationWorkPayload) =>
    request<CooperationCaseDetail>(`/cooperation-cases/${caseId}/works`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateCooperationWork: (workId: number, payload: CooperationWorkPayload) =>
    request<CooperationCaseDetail>(`/cooperation-works/${workId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteCooperationWork: (workId: number) =>
    request<CooperationCaseDetail>(`/cooperation-works/${workId}`, {
      method: "DELETE",
    }),
  batchUpdate: (roundId: number, payload: BatchUpdatePayload) =>
    request<{
      changed: number;
      conflicts: { platform: string; user_id: string; reason: string }[];
    }>(`/rounds/${roundId}/creators/batch`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  claimCreator: (roundId: number, platform: string, userId: string) =>
    request<CreatorDetail>(`/rounds/${roundId}/creators/${platform}/${encodeURIComponent(userId)}/claim`, {
      method: "POST",
    }),
  assignCreator: (roundId: number, platform: string, userId: string, ownerUserId: number | null) =>
    request<CreatorDetail>(`/rounds/${roundId}/creators/${platform}/${encodeURIComponent(userId)}/assign`, {
      method: "POST",
      body: JSON.stringify({ owner_user_id: ownerUserId }),
    }),
  getCreatorOwners: () => request<LeadOwner[]>("/creators/owners"),
  releaseCreator: (roundId: number, platform: string, userId: string) =>
    request<CreatorDetail>(`/rounds/${roundId}/creators/${platform}/${encodeURIComponent(userId)}/release`, {
      method: "POST",
    }),
  claimCreators: (roundId: number, items: { platform: string; user_id: string }[]) =>
    request<{ claimed: number; already_mine: number; skipped: number; missing: number }>(
      `/rounds/${roundId}/creators/claim`,
      {
        method: "POST",
        body: JSON.stringify({ items }),
      },
    ),
  getLeads: (query: LeadQuery) => {
    const params = new URLSearchParams();
    if (query.round_id !== null) params.set("round_id", String(query.round_id));
    if (query.platform) params.set("platform", query.platform);
    if (query.search) params.set("search", query.search);
    if (query.statuses.length) params.set("status", query.statuses.join(","));
    if (query.sources.length) params.set("source", query.sources.join(","));
    if (query.owner) params.set("owner", query.owner);
    params.set("sort", query.sort);
    params.set("order", query.order);
    params.set("page", String(query.page));
    params.set("page_size", String(query.page_size));
    return request<LeadListResponse>(`/leads?${params.toString()}`);
  },
  getLead: (leadId: number) => request<LeadDetail>(`/leads/${leadId}`),
  createLead: (payload: {
    platform: string;
    nickname: string;
    account_id: string;
    platform_handle: string;
    profile_url: string;
    raw_title: string;
    source: string;
    source_round_id: number | null;
    owner_user_id: number | null;
    raw_json?: Record<string, unknown>;
  }) =>
    request<LeadCreateResponse>("/leads", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createLeadsBulk: (items: Array<{
    platform: string;
    nickname: string;
    account_id: string;
    platform_handle: string;
    profile_url: string;
    raw_title: string;
    source: string;
    source_round_id: number | null;
    owner_user_id: number | null;
  }>) =>
    request<{
      created: number;
      revived: number;
      existing: number;
      ids: number[];
      revived_ids: number[];
      existing_ids: number[];
      match_errors: number;
      errors: { index: number; error: string }[];
    }>("/leads/bulk", {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
  updateLead: (leadId: number, payload: Partial<Lead>) =>
    request<LeadDetail & { match_error?: string }>(`/leads/${leadId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  assignLead: (leadId: number, ownerUserId: number | null) =>
    request<LeadDetail>(`/leads/${leadId}/assign`, {
      method: "POST",
      body: JSON.stringify({ owner_user_id: ownerUserId }),
    }),
  matchLead: (leadId: number) =>
    request<LeadDetail & { match: LeadMatchResult }>(`/leads/${leadId}/match`, {
      method: "POST",
    }),
  convertLead: (
    leadId: number,
    payload: {
      account_id: string;
      platform_handle: string;
      profile_url: string;
      fetch: boolean;
      provider?: string;
      confirm_mismatch?: boolean;
    },
  ) =>
    request<{ lead: Lead; creator: unknown; rounds: number[] }>(`/leads/${leadId}/convert`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  verifyLead: (
    leadId: number,
    payload: {
      provider?: string;
      account_id?: string;
      platform_handle?: string;
      profile_url?: string;
      apply?: boolean;
      confirm_mismatch?: boolean;
    },
  ) =>
    request<LeadVerifyResult>(`/leads/${leadId}/verify`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getLeadFacets: (roundId: number | null) =>
    request<LeadFacets>(`/leads/facets${roundId === null ? "" : `?round_id=${roundId}`}`),
  getLeadIdentityHealth: () => request<LeadIdentityHealth>("/leads/identity-health"),
  getLeadOwners: () => request<LeadOwner[]>("/leads/owners"),
  getJob: (roundId: number) => request<Job | null>(`/rounds/${roundId}/job`),
  getActiveJob: () => request<Job | null>(`/jobs/active`),
  getCalls: (roundId: number) => request<ApiCall[]>(`/rounds/${roundId}/calls`),
  getLog: (roundId: number, lines = 300) =>
    request<{ path: string; content: string }>(`/rounds/${roundId}/log?lines=${lines}`),
  getKeywords: () => request<Keyword[]>("/keywords"),
  createKeyword: (word: string) =>
    request<{ id: number }>("/keywords", {
      method: "POST",
      body: JSON.stringify({ word, enabled: true }),
    }),
  updateKeyword: (id: number, payload: { enabled?: boolean }) =>
    request<{ ok: boolean }>(`/keywords/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteKeyword: (id: number) =>
    request<{ ok: boolean }>(`/keywords/${id}`, { method: "DELETE" }),
  getDashboard: (roundId: number | null) =>
    request<DashboardData>(`/dashboard${roundId == null ? "" : `?round_id=${roundId}`}`),
  getActivityCalendar: (
    days = 365,
    tzOffset = -new Date().getTimezoneOffset(),
    roundId: number | null = null,
  ) => {
    const params = new URLSearchParams({ days: String(days), tz_offset: String(tzOffset) });
    if (roundId !== null) params.set("round_id", String(roundId));
    return request<ActivityCalendarResponse>(`/dashboard/activity?${params.toString()}`);
  },
  getCounts: () => request<Counts>("/counts"),
  getActionQueue: (params: {
    roundId: number | null;
    scope: "all" | "pending" | "unclaimed";
    sort: "potential" | "ratio" | "updated";
    includeOthers?: boolean;
    limit?: number;
  }) => {
    const search = new URLSearchParams({
      scope: params.scope,
      sort: params.sort,
      limit: String(params.limit ?? 6),
    });
    if (params.includeOthers) search.set("include_others", "true");
    if (params.roundId !== null) search.set("round_id", String(params.roundId));
    return request<ActionQueueResponse>(`/dashboard/queue?${search.toString()}`);
  },
  getSettings: () => request<Settings>("/settings"),
  updateDataflowToken: (token: string) =>
    request<Settings & { balance: Balance | null }>("/settings/dataflow-token", {
      method: "PUT",
      body: JSON.stringify({ token, verify: true }),
    }),
  updateRnoteToken: (token: string) =>
    request<Settings & { balance: Balance | null }>("/settings/rnote-token", {
      method: "PUT",
      body: JSON.stringify({ token, verify: true }),
    }),
  updateProviderToken: (providerId: string, token: string) =>
    request<Settings & { balance: Balance | null; verified?: boolean }>(
      `/settings/providers/${encodeURIComponent(providerId)}/token`,
      {
        method: "PUT",
        body: JSON.stringify({ token, verify: true }),
      },
    ),
  getEmailAccounts: () => request<{ items: EmailAccount[] }>("/email/accounts"),
  createEmailAccount: (payload: EmailAccountInput) =>
    request<EmailAccount>("/email/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateEmailAccount: (accountId: number, payload: Partial<EmailAccountInput>) =>
    request<EmailAccount>(`/email/accounts/${accountId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteEmailAccount: (accountId: number) =>
    request<{ deleted: boolean }>(`/email/accounts/${accountId}`, {
      method: "DELETE",
    }),
  testEmailAccount: (accountId: number, sendTestTo = "") =>
    request<{ ok: boolean; sent_test: boolean }>(
      `/email/accounts/${accountId}/test`,
      { method: "POST", body: JSON.stringify({ send_test_to: sendTestTo }) },
    ),
  resumePausedEmailCampaigns: (accountId: number) =>
    request<{ resumed: number }>(
      `/email/accounts/${accountId}/resume-paused`,
      { method: "POST" },
    ),
  getEmailWorkerStatus: () =>
    request<EmailWorkerStatus>("/email/worker-status"),
  getCreatorEmails: (platform: string, userId: string) =>
    request<{ items: CreatorEmail[] }>(
      `/creators/${encodeURIComponent(platform)}/${encodeURIComponent(userId)}/emails`,
    ),
  addCreatorEmail: (
    platform: string,
    userId: string,
    payload: { email: string; label?: string; is_primary?: boolean },
  ) =>
    request<CreatorEmail>(
      `/creators/${encodeURIComponent(platform)}/${encodeURIComponent(userId)}/emails`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  deleteCreatorEmail: (emailId: number) =>
    request<{ deleted: boolean }>(`/email/creator-emails/${emailId}`, {
      method: "DELETE",
    }),
  setPrimaryCreatorEmail: (emailId: number) =>
    request<CreatorEmail>(`/email/creator-emails/${emailId}/primary`, {
      method: "POST",
    }),
  getEmailTargets: (query: {
    search?: string;
    platform?: string;
    roundId?: number | null;
    owner?: string;
    hasEmail?: boolean | null;
    page?: number;
    pageSize?: number;
  }) => {
    const params = new URLSearchParams();
    if (query.search) params.set("search", query.search);
    if (query.platform) params.set("platform", query.platform);
    if (query.roundId) params.set("round_id", String(query.roundId));
    if (query.owner && query.owner !== "all") params.set("owner", query.owner);
    if (query.hasEmail !== null && query.hasEmail !== undefined) {
      params.set("has_email", String(query.hasEmail));
    }
    params.set("page", String(query.page ?? 1));
    params.set("page_size", String(query.pageSize ?? 50));
    return request<{
      items: EmailTarget[];
      total: number;
      page: number;
      page_size: number;
    }>(`/email/targets?${params.toString()}`);
  },
  previewEmailCampaign: (payload: {
    subject: string;
    body: string;
    platform?: string;
    user_id?: string;
  }) =>
    request<{ subject: string; body: string; context: Record<string, string> }>(
      "/email/campaigns/preview",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  listEmailCampaigns: () =>
    request<{ items: EmailCampaign[] }>("/email/campaigns"),
  createEmailCampaign: (payload: {
    name: string;
    account_id: number;
    subject: string;
    body: string;
    scheduled_start?: string | null;
    schedule?: boolean;
    timezone: string;
    window_start: string;
    window_end: string;
    weekdays: number[];
    interval_seconds: number;
    jitter_seconds: number;
    daily_limit: number;
    auto_mark_contacted: boolean;
    recipients: { platform: string; user_id: string; email: string; nickname?: string }[];
  }) =>
    request<EmailCampaign>("/email/campaigns", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getEmailCampaign: (campaignId: number, params?: { status?: string; page?: number; pageSize?: number }) => {
    const search = new URLSearchParams();
    if (params?.status) search.set("status", params.status);
    search.set("page", String(params?.page ?? 1));
    search.set("page_size", String(params?.pageSize ?? 100));
    return request<EmailCampaignDetail>(
      `/email/campaigns/${campaignId}?${search.toString()}`,
    );
  },
  scheduleEmailCampaign: (
    campaignId: number,
    payload: {
      scheduled_start?: string | null;
      timezone: string;
      window_start: string;
      window_end: string;
      weekdays: number[];
      interval_seconds: number;
      jitter_seconds: number;
      daily_limit: number;
    },
  ) =>
    request<EmailCampaign>(`/email/campaigns/${campaignId}/schedule`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  pauseEmailCampaign: (campaignId: number) =>
    request<EmailCampaign>(`/email/campaigns/${campaignId}/pause`, {
      method: "POST",
    }),
  resumeEmailCampaign: (campaignId: number) =>
    request<EmailCampaign>(`/email/campaigns/${campaignId}/resume`, {
      method: "POST",
    }),
  retryFailedEmailRecipients: (campaignId: number) =>
    request<EmailCampaign & { retried: number }>(
      `/email/campaigns/${campaignId}/retry-failed`,
      { method: "POST" },
    ),
  cancelEmailCampaign: (campaignId: number) =>
    request<EmailCampaign>(`/email/campaigns/${campaignId}/cancel`, {
      method: "POST",
    }),
  deleteEmailCampaign: (campaignId: number) =>
    request<{ deleted: boolean }>(`/email/campaigns/${campaignId}`, {
      method: "DELETE",
    }),
  getEmailSuppressions: () =>
    request<{ items: EmailSuppression[] }>("/email/suppressions"),
  addEmailSuppression: (payload: { email: string; reason?: string }) =>
    request<EmailSuppression>("/email/suppressions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteEmailSuppression: (suppressionId: number) =>
    request<{ deleted: boolean }>(`/email/suppressions/${suppressionId}`, {
      method: "DELETE",
    }),
  getBalance: (provider = "dataflow") =>
    request<Balance>(`/balance?provider=${encodeURIComponent(provider)}`),
};

export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) {
    if (response.status === 401) {
      window.dispatchEvent(new Event("codata:unauthorized"));
    }
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = detailMessage(payload.detail) || response.statusText;
    } catch {
      // ignore non-JSON errors
    }
    throw new Error(message || "下载失败");
  }
  const disposition = response.headers.get("content-disposition") || "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const name = match?.[1] || fallbackName;
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = name;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export function exportUrl(
  roundId: number | null,
  format: "csv" | "xlsx",
  scope: "filtered" | "all",
  query?: CreatorQuery,
): string {
  const params = new URLSearchParams();
  params.set("format", format);
  params.set("scope", scope);
  if (roundId !== null) params.set("round_id", String(roundId));
  if (scope === "filtered" && query) {
    params.set("platform", query.platform);
    if (query.search) params.set("search", query.search);
    if (query.statuses.length) params.set("status", query.statuses.join(","));
    if (query.tiers.length) params.set("tier", query.tiers.join(","));
    if (query.directions.length) params.set("direction", query.directions.join(","));
    if (query.sources.length) params.set("source", query.sources.join(","));
    if (query.sources.length && query.source_mode === "all") params.set("source_mode", "all");
    if (query.labels.length) params.set("label", query.labels.join(","));
    if (query.owner) params.set("owner", query.owner);
    if (query.cooperation.length) params.set("cooperation", query.cooperation.join(","));
    if (query.cooperation_stale_days) {
      params.set("cooperation_stale_days", query.cooperation_stale_days);
    }
    if (query.min_fans) params.set("min_fans", query.min_fans);
    if (query.max_fans) params.set("max_fans", query.max_fans);
    if (query.only_unenriched) params.set("only_unenriched", "true");
    if (query.needs_action) params.set("needs_action", "true");
    params.set("sort", query.sort);
    params.set("order", query.order);
  }
  return roundId === null
    ? `${BASE}/creators/export?${params.toString()}`
    : `${BASE}/rounds/${roundId}/export?${params.toString()}`;
}
