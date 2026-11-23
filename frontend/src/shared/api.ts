import type {
  ActionQueueResponse,
  ActivityCalendarResponse,
  ApiCall,
  AuthResponse,
  AuthUser,
  Balance,
  BatchUpdatePayload,
  CreateRoundPayload,
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
    try {
      const payload = await response.json();
      message = detailMessage(payload.detail) || response.statusText;
    } catch {
      // ignore non-JSON errors
    }
    throw new Error(message || "请求失败");
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
  createUser: (payload: { username: string; password: string; role: string; can_collect: boolean }) =>
    request<AuthUser>("/users", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateUser: (userId: number, payload: { role?: string; disabled?: boolean; can_collect?: boolean }) =>
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
      match_direction?: string;
      tags_manual?: string;
      remark?: string;
      expected_updated_at?: string;
      expected_status?: string;
    },
  ) =>
    request<CreatorDetail>(`/rounds/${roundId}/creators/${platform}/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  batchUpdate: (roundId: number, payload: BatchUpdatePayload) =>
    request<{ changed: number }>(`/rounds/${roundId}/creators/batch`, {
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
    payload: { account_id: string; profile_url: string; fetch: boolean; provider?: string },
  ) =>
    request<{ lead: Lead; creator: unknown; rounds: number[] }>(`/leads/${leadId}/convert`, {
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
  getActivityCalendar: (days = 365, tzOffset = -new Date().getTimezoneOffset()) =>
    request<ActivityCalendarResponse>(
      `/dashboard/activity?days=${days}&tz_offset=${tzOffset}`,
    ),
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
        body: JSON.stringify({ token, verify: false }),
      },
    ),
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
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const name = match?.[1] || fallbackName;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = name;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
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
