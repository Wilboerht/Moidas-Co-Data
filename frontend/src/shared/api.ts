import type {
  ApiCall,
  AuthResponse,
  AuthUser,
  Balance,
  BatchUpdatePayload,
  CreateRoundPayload,
  CreatorDetail,
  CreatorListResponse,
  CreatorQuery,
  DashboardData,
  Facets,
  Job,
  Keyword,
  Meta,
  Round,
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
    request<{ ok: boolean }>(`/users/${userId}/revoke-sessions`, {
      method: "POST",
    }),
  getRounds: () => request<Round[]>("/rounds"),
  getRound: (roundId: number) => request<Round>(`/rounds/${roundId}`),
  createRound: (payload: CreateRoundPayload) =>
    request<{ round: Round; job_id: number | null }>("/rounds", {
      method: "POST",
      body: JSON.stringify({ ...payload, start: true }),
    }),
  runRound: (roundId: number, refreshUsers = false, force = false) =>
    request<{ job_id: number; job: Job | null }>(`/rounds/${roundId}/run`, {
      method: "POST",
      body: JSON.stringify({ refresh_users: refreshUsers, force }),
    }),
  getMeta: () => request<Meta>("/meta"),
  getCreators: (roundId: number | null, query: CreatorQuery) => {
    const params = new URLSearchParams();
    params.set("platform", query.platform);
    params.set("page", String(query.page));
    params.set("page_size", String(query.page_size));
    params.set("sort", query.sort);
    params.set("order", query.order);
    if (query.search) params.set("search", query.search);
    if (query.statuses.length) params.set("status", query.statuses.join(","));
    if (query.tiers.length) params.set("tier", query.tiers.join(","));
    if (query.directions.length) params.set("direction", query.directions.join(","));
    if (query.sources.length) params.set("source", query.sources.join(","));
    if (query.min_fans) params.set("min_fans", query.min_fans);
    if (query.max_fans) params.set("max_fans", query.max_fans);
    if (query.only_unenriched) params.set("only_unenriched", "true");
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
  getJob: (roundId: number) => request<Job | null>(`/rounds/${roundId}/job`),
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
    if (query.min_fans) params.set("min_fans", query.min_fans);
    if (query.max_fans) params.set("max_fans", query.max_fans);
    if (query.only_unenriched) params.set("only_unenriched", "true");
  }
  return roundId === null
    ? `${BASE}/creators/export?${params.toString()}`
    : `${BASE}/rounds/${roundId}/export?${params.toString()}`;
}
