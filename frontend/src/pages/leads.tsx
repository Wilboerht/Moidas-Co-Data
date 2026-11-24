import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Download, Link2, LoaderCircle, Plus, RotateCcw, RotateCw, Search, UserPlus } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, downloadFile } from "@/shared/api";
import { canonicalSearch } from "@/shared/query-string";
import { fmtDateTime, fmtNumber } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import type { Lead, LeadOwner, LeadQuery } from "@/shared/types";
import { EmptyState, ErrorState, Page, PageHeader, Section } from "@/components/page";
import { LeadStatusBadge } from "@/components/lead-status-badge";
import { MultiSelect } from "@/components/multi-select";
import { LeadSheet } from "@/components/lead-sheet";
import { BulkLeadDialog } from "@/components/bulk-lead-dialog";
import { Card } from "@/components/ui/card";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const IDENTITY_CONFIDENCE_LABELS: Record<string, string> = {
  account_id: "用户 ID 一致",
  platform_handle: "平台账号一致",
  profile_url: "主页一致",
  nickname: "仅昵称一致",
  manual_mismatch: "人工确认",
  matched: "本地匹配",
  local: "本地达人",
  none: "无匹配",
  round_account_id: "轮次账号",
  auto: "自动匹配",
};

function useDebounced<T>(value: T, delay = 300) {
  const [next, setNext] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setNext(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return next;
}

const DEFAULT_STATUS_FILTER = ["unverified", "verifying"];

export function LeadsPage() {
  const { user } = useAuth();
  const canCollect = user?.role === "admin" || Boolean(user?.can_collect);
  const canEditLeads = user?.role === "admin" || Boolean(user?.can_edit_leads);
  const { meta, rounds } = useRound();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const debouncedSearch = useDebounced(search);
  const [roundFilter, setRoundFilter] = useState<number | null>(() => {
    if (!canCollect) return null;
    const value = Number(params.get("round"));
    return Number.isFinite(value) && value > 0 ? value : null;
  });
  const [platform, setPlatform] = useState(params.get("platform") ?? "");
  const [statusFilter, setStatusFilter] = useState<string[]>(() => {
    const value = params.get("status");
    if (value === "none") return [];
    return value ? value.split(",").filter(Boolean) : [...DEFAULT_STATUS_FILTER];
  });
  const [sourceFilter, setSourceFilter] = useState<string[]>(() => {
    const value = params.get("source");
    return value ? value.split(",").filter(Boolean) : [];
  });
  const [owner, setOwner] = useState(params.get("owner") ?? "");
  const [page, setPage] = useState(() => {
    const value = Number(params.get("page") ?? 1);
    return Number.isFinite(value) && value > 0 ? Math.floor(value) : 1;
  });
  const [pageSize, setPageSize] = useState(() => {
    const value = Number(params.get("page_size") ?? 50);
    return Number.isFinite(value) ? Math.min(200, Math.max(25, Math.floor(value))) : 50;
  });
  const [selectedLeadId, setSelectedLeadId] = useState<number | null>(() => {
    const value = Number(params.get("lead"));
    return Number.isFinite(value) && value > 0 ? value : null;
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const leadStatuses = meta?.lead_statuses ?? [];
  const leadSources = meta?.lead_sources ?? [];
  const platforms = meta?.platforms ?? [];
  const lastUrlRef = useRef(canonicalSearch(params));
  const syncingFromUrlRef = useRef(false);

  useEffect(() => {
    const serialized = canonicalSearch(params);
    if (serialized === lastUrlRef.current) return;
    lastUrlRef.current = serialized;
    syncingFromUrlRef.current = true;
    const nextRound = canCollect ? Number(params.get("round")) : NaN;
    setRoundFilter(Number.isFinite(nextRound) && nextRound > 0 ? nextRound : null);
    setPlatform(params.get("platform") ?? "");
    const status = params.get("status");
    setStatusFilter(status === "none" ? [] : status ? status.split(",").filter(Boolean) : [...DEFAULT_STATUS_FILTER]);
    setSourceFilter(params.get("source")?.split(",").filter(Boolean) ?? []);
    setOwner(params.get("owner") ?? "");
    const nextPage = Number(params.get("page") ?? 1);
    setPage(Number.isFinite(nextPage) && nextPage > 0 ? Math.floor(nextPage) : 1);
    const nextPageSize = Number(params.get("page_size") ?? 50);
    setPageSize(Number.isFinite(nextPageSize) ? Math.min(200, Math.max(25, Math.floor(nextPageSize))) : 50);
    setSearch(params.get("search") ?? "");
    const nextLead = Number(params.get("lead"));
    setSelectedLeadId(Number.isFinite(nextLead) && nextLead > 0 ? nextLead : null);
  }, [params, canCollect]);

  useEffect(() => {
    if (!canCollect) setRoundFilter(null);
  }, [canCollect]);

  const currentParams = new URLSearchParams();
  if (roundFilter !== null) currentParams.set("round", String(roundFilter));
  if (platform) currentParams.set("platform", platform);
  if (debouncedSearch) currentParams.set("search", debouncedSearch);
  currentParams.set("status", statusFilter.length ? statusFilter.join(",") : "none");
  if (sourceFilter.length) currentParams.set("source", sourceFilter.join(","));
  if (owner) currentParams.set("owner", owner);
  if (page > 1) currentParams.set("page", String(page));
  if (pageSize !== 50) currentParams.set("page_size", String(pageSize));
  if (selectedLeadId !== null) currentParams.set("lead", String(selectedLeadId));

  useEffect(() => {
    if (syncingFromUrlRef.current) return;
    if (canonicalSearch(currentParams) === lastUrlRef.current) return;
    setPage(1);
  }, [roundFilter, platform, debouncedSearch, statusFilter, sourceFilter, owner]);

  useEffect(() => {
    if (syncingFromUrlRef.current) {
      if (debouncedSearch === (params.get("search") ?? "")) syncingFromUrlRef.current = false;
      return;
    }
    const serialized = canonicalSearch(currentParams);
    if (serialized === canonicalSearch(params)) return;
    lastUrlRef.current = serialized;
    setParams(currentParams, { replace: true });
  }, [roundFilter, platform, debouncedSearch, statusFilter, sourceFilter, owner, page, pageSize, selectedLeadId, params, setParams]);

  const query: LeadQuery = useMemo(
    () => ({
      round_id: roundFilter,
      platform,
      search: debouncedSearch,
      statuses: statusFilter,
      sources: sourceFilter,
      source_mode: "any",
      owner,
      page,
      page_size: pageSize,
      sort: "updated_at",
      order: "desc",
    }),
    [roundFilter, platform, debouncedSearch, statusFilter, sourceFilter, owner, page, pageSize],
  );

  const leadsQuery = useQuery({
    queryKey: ["leads", query],
    queryFn: () => api.getLeads(query),
  });
  const healthQuery = useQuery({
    queryKey: ["lead-identity-health"],
    queryFn: api.getLeadIdentityHealth,
    refetchInterval: 60_000,
  });
  const ownersQuery = useQuery({
    queryKey: ["lead-owners"],
    queryFn: api.getLeadOwners,
  });
  const leadFacetsQuery = useQuery({
    queryKey: ["lead-facets", roundFilter],
    queryFn: () => api.getLeadFacets(roundFilter),
  });

  const platformOptions = leadFacetsQuery.data?.platforms.length
    ? leadFacetsQuery.data.platforms.map((item) => ({
        code: item.value,
        label: `${platforms.find((platform) => platform.code === item.value)?.label ?? item.value} (${item.count})`,
      }))
    : platforms;
  const owners: LeadOwner[] = ownersQuery.data ?? [];
  const assignableOwners = user?.role === "admin" ? owners : owners.filter((item) => item.id === user?.id);
  const total = leadsQuery.data?.total ?? 0;
  const hasQuickConvertAction =
    canCollect &&
    (leadsQuery.data?.items ?? []).some(
      (lead) =>
        lead.status !== "converted" &&
        Boolean(lead.account_id.trim() || lead.profile_url.trim()),
    );
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  useEffect(() => {
    if (leadsQuery.data && page > totalPages) setPage(totalPages);
  }, [page, totalPages, leadsQuery.data]);
  const empty = !leadsQuery.isLoading && !leadsQuery.error && (leadsQuery.data?.items ?? []).length === 0;
  const health = healthQuery.data;
  const duplicateGroups =
    (health?.duplicate_account_groups ?? 0) +
    (health?.duplicate_handle_groups ?? 0) +
    (health?.duplicate_profile_groups ?? 0);
  const indexMissing = health
    ? !health.account_index || !health.handle_index || !health.profile_index
    : false;
  const showIdentityWarning = Boolean(health && (duplicateGroups > 0 || indexMissing));

  const refresh = async () => {
    for (const key of [
      ["leads"],
      ["lead"],
      ["lead-identity-health"],
      ["lead-facets"],
      ["bootstrap"],
      ["dashboard"],
      ["creators"],
      ["facets"],
      ["action-queue"],
    ]) {
      await queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const quickConvertMutation = useMutation({
    mutationFn: (lead: Lead) =>
      api.convertLead(lead.id, {
        account_id: lead.account_id || "",
        platform_handle: lead.platform_handle || "",
        profile_url: lead.profile_url || "",
        fetch: true,
      }),
    onSuccess: async (_result, lead) => {
      toast.success(`已将「${lead.nickname || `线索 #${lead.id}`}」转为达人并补全资料`);
      await refresh();
    },
    onError: (error, lead) => {
      if (error instanceof ApiError && error.status === 409) {
        setSelectedLeadId(lead.id);
        toast.warning("账号校验不一致，请在线索详情中确认后再转换");
        return;
      }
      toast.error(error instanceof Error ? error.message : "转为达人失败");
    },
  });
  const convertingLeadId = quickConvertMutation.isPending
    ? quickConvertMutation.variables?.id
    : null;

  const quickConvertState = (lead: Lead) => {
    const visible = Boolean(
      canCollect &&
        (lead.account_id.trim() ||
          lead.platform_handle.trim() ||
          lead.profile_url.trim()) &&
        lead.status !== "converted",
    );
    if (!visible) return { visible, disabled: true, reason: "" };
    if (lead.status !== "unverified" && lead.status !== "verifying") {
      return {
        visible,
        disabled: true,
        reason: "仅待核实或核实中的线索可以转为达人",
      };
    }
    if (
      user?.role !== "admin" &&
      lead.owner_user_id != null &&
      lead.owner_user_id !== user?.id
    ) {
      return { visible, disabled: true, reason: "只能转换自己负责的线索" };
    }
    if (lead.source_round_id == null && lead.matched_round_id == null) {
      return { visible, disabled: true, reason: "线索没有关联采集轮次" };
    }
    if (!lead.account_id.trim() && lead.identity_status !== "verified") {
      return {
        visible,
        disabled: true,
        reason: "请先打开线索校验账号，确认后再转换",
      };
    }
    return { visible, disabled: false, reason: "自动选择最便宜服务商，转为达人并补全资料" };
  };

  const runExport = (format: "csv" | "xlsx") => {
    void downloadFile(leadExportUrl(query, format), `codata_leads.${format}`).catch(
      (err) => {
        toast.error(err instanceof Error ? err.message : "导出失败");
      },
    );
  };

  const statusOptions = leadStatuses.map((item) => ({ value: item.code, label: item.label }));
  const sourceOptions = leadSources.map((item) => ({ value: item.code, label: item.label }));
  const isDefaultStatus =
    statusFilter.length === DEFAULT_STATUS_FILTER.length &&
    DEFAULT_STATUS_FILTER.every((item) => statusFilter.includes(item));
  const hasCustomFilters =
    Boolean(search.trim()) ||
    roundFilter !== null ||
    Boolean(platform) ||
    Boolean(owner) ||
    sourceFilter.length > 0 ||
    !isDefaultStatus;

  const resetFilters = () => {
    setSearch("");
    setRoundFilter(null);
    setPlatform("");
    setStatusFilter([...DEFAULT_STATUS_FILTER]);
    setSourceFilter([]);
    setOwner("");
  };

  return (
    <Page>
      <PageHeader
        title="线索库"
        description={`共 ${fmtNumber(total)} 条线索 · 待核实和待补全线索都从这里进入`}
        actions={
          <>
            {canEditLeads ? (
              <>
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus /> 新增线索
                </Button>
                <Button variant="outline" onClick={() => setBulkOpen(true)}>
                  批量粘贴
                </Button>
              </>
            ) : null}
            <Button variant="outline" onClick={() => void leadsQuery.refetch()} disabled={leadsQuery.isFetching}>
              <RotateCw className={leadsQuery.isFetching ? "animate-spin" : undefined} />
              刷新
            </Button>
            <Button variant="outline" onClick={() => runExport("csv")}>
              <Download /> 导出筛选 CSV
            </Button>
            <Button variant="outline" onClick={() => runExport("xlsx")}>
              <Download /> 导出筛选 Excel
            </Button>
          </>
        }
      />

      {showIdentityWarning ? (
        <div className="border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {duplicateGroups > 0
            ? `检测到 ${duplicateGroups} 组账号或主页重复的线索，唯一索引未完全启用。`
            : "线索唯一索引未启用。"}
          请运行 <code className="rounded bg-amber-100 px-1 py-0.5 font-mono text-xs">uv run codata-admin leads-dedupe --apply</code>
          后重启面板。
        </div>
      ) : null}

      <Section
        title="筛选"
        actions={
          hasCustomFilters ? (
            <Button variant="ghost" size="sm" onClick={resetFilters}>
              <RotateCcw />
              重置筛选
            </Button>
          ) : null
        }
      >
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-[minmax(0,1.8fr)_repeat(5,minmax(0,1fr))]">
          <div className="space-y-2 md:col-span-2 lg:col-span-3 xl:col-span-1">
            <Label htmlFor="lead-search">搜索</Label>
            <div className="relative">
              <Search className="pointer-events-none absolute top-2 left-2 size-4 text-muted-foreground" />
              <Input
                id="lead-search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="pl-8"
                placeholder="昵称、原始标题、账号 ID、主页"
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="lead-platform">平台</Label>
            <Select value={platform || "all"} onValueChange={(value) => setPlatform(value === "all" ? "" : value)}>
              <SelectTrigger id="lead-platform" className="w-full">
                <SelectValue placeholder="全部平台" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部平台</SelectItem>
                {platformOptions.map((item) => (
                  <SelectItem key={item.code} value={item.code}>{item.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {canCollect ? (
            <div className="space-y-2">
              <Label htmlFor="lead-round">轮次</Label>
              <Select
                value={roundFilter === null ? "all" : String(roundFilter)}
                onValueChange={(value) => setRoundFilter(value === "all" ? null : Number(value))}
              >
                <SelectTrigger id="lead-round" className="w-full">
                  <SelectValue placeholder="全部轮次" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部轮次</SelectItem>
                  {rounds.map((round) => (
                    <SelectItem key={round.id} value={String(round.id)}>{round.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <div className="space-y-2">
            <Label>线索状态</Label>
            <MultiSelect
              label="线索状态"
              options={statusOptions}
              value={statusFilter}
              onChange={setStatusFilter}
              placeholder="全部状态"
              searchPlaceholder="搜索状态"
              emptyText="没有状态"
            />
          </div>
          <div className="space-y-2">
            <Label>线索来源</Label>
            <MultiSelect
              label="线索来源"
              options={sourceOptions}
              value={sourceFilter}
              onChange={setSourceFilter}
              placeholder="全部来源"
              searchPlaceholder="搜索来源"
              emptyText="没有来源"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="lead-owner">负责人</Label>
            <Select value={owner || "all"} onValueChange={(value) => setOwner(value === "all" ? "" : value)}>
              <SelectTrigger id="lead-owner" className="w-full">
                <SelectValue placeholder="全部负责人" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部负责人</SelectItem>
                <SelectItem value="unassigned">未分配</SelectItem>
                {owners.map((item) => (
                  <SelectItem key={item.id} value={String(item.id)}>{item.username}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </Section>

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
          <p className="text-sm text-muted-foreground">共 {fmtNumber(total)} 条</p>
          <div className="flex items-center gap-2">
            <Select value={String(pageSize)} onValueChange={(value) => { setPageSize(Number(value)); setPage(1); }}>
              <SelectTrigger className="h-8 w-[100px]" aria-label="每页条数"><SelectValue /></SelectTrigger>
              <SelectContent>
                {[25, 50, 100, 200].map((size) => <SelectItem key={size} value={String(size)}>{size} / 页</SelectItem>)}
              </SelectContent>
            </Select>
            <Button variant="outline" size="icon-sm" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} aria-label="上一页">
              <ChevronLeft />
            </Button>
            <Button variant="outline" size="icon-sm" disabled={page >= totalPages} onClick={() => setPage((value) => Math.min(totalPages, value + 1))} aria-label="下一页">
              <ChevronRight />
            </Button>
          </div>
        </div>
        {leadsQuery.isLoading ? (
          <div className="space-y-2 p-6">
            {Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-10 w-full" />)}
          </div>
        ) : leadsQuery.error ? (
          <div className="p-4">
            <ErrorState message={leadsQuery.error instanceof Error ? leadsQuery.error.message : "加载线索失败"} />
          </div>
        ) : empty ? (
          <EmptyState
            title="没有线索"
            description={
              canEditLeads
                ? "可以从导入、手动录入或客户链接开始收集线索。"
                : "管理员录入线索后会显示在这里。"
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>线索</TableHead>
                <TableHead>平台</TableHead>
                <TableHead>账号 / 主页</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>负责人</TableHead>
                <TableHead>状态</TableHead>
                {hasQuickConvertAction ? <TableHead>操作</TableHead> : null}
                <TableHead>更新时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(leadsQuery.data?.items ?? []).map((lead) => (
                <TableRow
                  key={lead.id}
                  tabIndex={0}
                  className="cursor-pointer hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => setSelectedLeadId(lead.id)}
                  onKeyDown={(event) => {
                    if (
                      (event.target as HTMLElement).closest(
                        "button, a, input, select, textarea",
                      )
                    ) {
                      return;
                    }
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedLeadId(lead.id);
                    }
                  }}
                >
                  <TableCell className="font-medium">
                    <div className="max-w-[240px] truncate">{lead.nickname || lead.raw_title || `线索 #${lead.id}`}</div>
                    {lead.raw_title && lead.nickname ? (
                      <div className="max-w-[240px] truncate text-xs text-muted-foreground">{lead.raw_title}</div>
                    ) : null}
                    {lead.matched_user_id && lead.status !== "converted" ? (
                      <div
                        className="mt-1 inline-flex max-w-[240px] items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground"
                        title={`已关联达人 ${lead.matched_user_id}${canCollect && lead.matched_round_name ? ` · ${lead.matched_round_name}` : ""}`}
                      >
                        <Link2 className="size-3 shrink-0" aria-hidden="true" />
                        <span className="truncate">
                          已关联达人{canCollect && lead.matched_round_name ? ` · ${lead.matched_round_name}` : ""}
                        </span>
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{lead.platform_label || lead.platform}</TableCell>
                  <TableCell className="max-w-[220px] truncate font-mono text-xs text-muted-foreground">
                    <div className="truncate">
                      {lead.platform_handle || lead.account_id || lead.profile_url || "—"}
                    </div>
                    <div className="truncate text-[11px] font-sans">
                      {lead.identity_status_label}
                      {lead.identity_confidence
                        ? ` · ${IDENTITY_CONFIDENCE_LABELS[lead.identity_confidence] ?? lead.identity_confidence}`
                        : ""}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {lead.source_label}{canCollect && lead.source_round_name ? ` · ${lead.source_round_name}` : ""}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{lead.owner_username || "未分配"}</TableCell>
                  <TableCell><LeadStatusBadge status={lead.status} label={lead.status_label} /></TableCell>
                  {hasQuickConvertAction ? (
                    <TableCell>
                      <LeadQuickConvertButton
                        {...quickConvertState(lead)}
                        converting={convertingLeadId === lead.id}
                        onClick={() => quickConvertMutation.mutate(lead)}
                      />
                    </TableCell>
                  ) : null}
                  <TableCell className="text-muted-foreground">{lead.updated_at ? fmtDateTime(lead.updated_at) : "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      {selectedLeadId !== null ? (
        <LeadSheet
          leadId={selectedLeadId}
          statuses={leadStatuses}
          owners={assignableOwners}
          canCollect={canCollect}
          canEditLeads={canEditLeads}
          onClose={() => setSelectedLeadId(null)}
        />
      ) : null}

      <BulkLeadDialog
        open={bulkOpen}
        onClose={() => setBulkOpen(false)}
        platforms={platforms}
        rounds={canCollect ? rounds : []}
        sources={leadSources}
        onCreated={refresh}
        defaultPlatform={platform || "xhs"}
      />

      <CreateLeadDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        platforms={platforms}
        rounds={canCollect ? rounds : []}
        owners={assignableOwners}
        sources={leadSources}
        defaultPlatform={platform || "xhs"}
        onCreated={async (leadId) => {
          setCreateOpen(false);
          setSelectedLeadId(leadId);
          await refresh();
        }}
      />
    </Page>
  );
}

function LeadQuickConvertButton({
  visible,
  disabled,
  reason,
  converting,
  onClick,
}: {
  visible: boolean;
  disabled: boolean;
  reason: string;
  converting: boolean;
  onClick: () => void;
}) {
  if (!visible) return null;
  return (
    <Button
      size="sm"
      variant="outline"
      className="h-7 px-2 text-xs"
      disabled={disabled || converting}
      title={converting ? "正在转换并补全资料" : reason}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    >
      {converting ? <LoaderCircle className="animate-spin" /> : <UserPlus />}
      {converting ? "转换中" : "转为达人"}
    </Button>
  );
}

function CreateLeadDialog({
  open,
  onClose,
  platforms,
  rounds,
  owners,
  sources,
  onCreated,
  defaultPlatform = "xhs",
}: {
  open: boolean;
  onClose: () => void;
  platforms: { code: string; label: string }[];
  rounds: { id: number; name: string }[];
  owners: LeadOwner[];
  sources: { code: string; label: string }[];
  onCreated: (leadId: number) => void;
  defaultPlatform?: string;
}) {
  const [platform, setPlatform] = useState(defaultPlatform);
  const [nickname, setNickname] = useState("");
  const [accountId, setAccountId] = useState("");
  const [platformHandle, setPlatformHandle] = useState("");
  const [profileUrl, setProfileUrl] = useState("");
  const [rawTitle, setRawTitle] = useState("");
  const [source, setSource] = useState("manual");
  const [roundId, setRoundId] = useState<string>("none");
  const [ownerId, setOwnerId] = useState<string>("none");
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPlatform(defaultPlatform);
    setNickname("");
    setAccountId("");
    setPlatformHandle("");
    setProfileUrl("");
    setRawTitle("");
    setSource("manual");
    setRoundId("none");
    setOwnerId("none");
    setCloseConfirmOpen(false);
  }, [open, defaultPlatform]);

  const canSubmit = Boolean(
    nickname.trim() || accountId.trim() || platformHandle.trim() || profileUrl.trim(),
  );
  const dirty = Boolean(
    nickname.trim() ||
    accountId.trim() ||
    platformHandle.trim() ||
    profileUrl.trim() ||
    rawTitle.trim() ||
    platform !== "xhs" ||
    source !== "manual" ||
    roundId !== "none" ||
    ownerId !== "none",
  );

  const mutation = useMutation({
    mutationFn: () =>
      api.createLead({
        platform,
        nickname,
        account_id: accountId,
        platform_handle: platformHandle,
        profile_url: profileUrl,
        raw_title: rawTitle,
        source,
        source_round_id: roundId === "none" ? null : Number(roundId),
        owner_user_id: ownerId === "none" ? null : Number(ownerId),
      }),
    onSuccess: (lead) => {
      if (lead.match_error) {
        toast.warning(`线索已保存，但自动匹配失败：${lead.match_error}`);
      } else if (lead.revived) {
        toast.success(
          lead.matched_user_id ? "旧线索已恢复，并关联到达人库" : "旧线索已恢复为待核实",
        );
      } else if (lead.created === false) {
        toast.info("线索已存在，已打开原线索");
      } else if (lead.matched_user_id) {
        toast.success("线索已创建，并关联到达人库");
      } else {
        toast.success("线索已创建");
      }
      onCreated(lead.id);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "创建线索失败"),
  });

  const requestClose = () => {
    if (mutation.isPending) return;
    if (dirty) {
      setCloseConfirmOpen(true);
      return;
    }
    onClose();
  };

  return (
    <>
      <Dialog open={open} onOpenChange={(value) => !value && requestClose()}>
        <DialogContent className="grid max-h-[85vh] max-w-[calc(100%-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-xl">
          <DialogHeader className="space-y-1.5 border-b px-5 pt-5 pr-12 pb-3">
            <DialogTitle>新增线索</DialogTitle>
            <DialogDescription>
              没有平台账号或用户 ID 也可以先存为线索，之后在详情里校验并转入达人库。
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 overflow-y-auto px-5 py-4">
            <form
              id="create-lead-form"
              className="grid gap-4 sm:grid-cols-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (canSubmit && !mutation.isPending) mutation.mutate();
              }}
            >
              <div className="space-y-2">
                <Label htmlFor="create-lead-platform">平台</Label>
                <Select value={platform} onValueChange={setPlatform} disabled={mutation.isPending}>
                  <SelectTrigger id="create-lead-platform" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {platforms.map((item) => <SelectItem key={item.code} value={item.code}>{item.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-lead-nickname">昵称</Label>
                <Input
                  id="create-lead-nickname"
                  value={nickname}
                  disabled={mutation.isPending}
                  onChange={(event) => setNickname(event.target.value)}
                  autoComplete="off"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-lead-handle">平台账号</Label>
                <Input
                  id="create-lead-handle"
                  value={platformHandle}
                  disabled={mutation.isPending}
                  onChange={(event) => setPlatformHandle(event.target.value)}
                  placeholder="小红书号 / 微博号 / 抖音号等"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-lead-account">平台用户 ID</Label>
                <Input
                  id="create-lead-account"
                  value={accountId}
                  disabled={mutation.isPending}
                  onChange={(event) => setAccountId(event.target.value)}
                  placeholder="可选，采集接口内部 ID"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-lead-url">主页 URL</Label>
                <Input
                  id="create-lead-url"
                  value={profileUrl}
                  disabled={mutation.isPending}
                  onChange={(event) => setProfileUrl(event.target.value)}
                  placeholder="https://…"
                  autoComplete="off"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-lead-source">来源</Label>
                <Select value={source} onValueChange={setSource} disabled={mutation.isPending}>
                  <SelectTrigger id="create-lead-source" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {sources.map((item) => <SelectItem key={item.code} value={item.code}>{item.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              {rounds.length > 0 ? (
                <div className="space-y-2">
                  <Label htmlFor="create-lead-round">来源轮次</Label>
                  <Select value={roundId} onValueChange={setRoundId} disabled={mutation.isPending}>
                    <SelectTrigger id="create-lead-round" className="w-full"><SelectValue placeholder="不关联" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">不关联轮次</SelectItem>
                      {rounds.map((round) => <SelectItem key={round.id} value={String(round.id)}>{round.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
              <div className="space-y-2">
                <Label htmlFor="create-lead-owner">负责人</Label>
                <Select value={ownerId} onValueChange={setOwnerId} disabled={mutation.isPending}>
                  <SelectTrigger id="create-lead-owner" className="w-full"><SelectValue placeholder="默认自己" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">默认自己</SelectItem>
                    {owners.map((item) => <SelectItem key={item.id} value={String(item.id)}>{item.username}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="create-lead-title">原始标题 / 备注</Label>
                <Input
                  id="create-lead-title"
                  value={rawTitle}
                  disabled={mutation.isPending}
                  onChange={(event) => setRawTitle(event.target.value)}
                  autoComplete="off"
                />
              </div>
            </form>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/40 px-5 py-3">
            <p className={`text-xs ${canSubmit ? "text-muted-foreground" : "text-amber-600"}`}>
              {canSubmit ? "负责人未选择时默认为自己" : "昵称、账号 ID 或主页 URL 至少填写一项"}
            </p>
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={requestClose} disabled={mutation.isPending}>
                取消
              </Button>
              <Button type="submit" form="create-lead-form" disabled={mutation.isPending || !canSubmit}>
                {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
                {mutation.isPending ? "创建中…" : "创建线索"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={closeConfirmOpen} onOpenChange={setCloseConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>放弃未保存的线索？</AlertDialogTitle>
            <AlertDialogDescription>填写的内容尚未创建，关闭后不会保存。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续编辑</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setCloseConfirmOpen(false);
                onClose();
              }}
            >
              放弃并关闭
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}


function leadExportUrl(query: LeadQuery, format: "csv" | "xlsx") {
  const params = new URLSearchParams({ format });
  if (query.round_id !== null) params.set("round_id", String(query.round_id));
  if (query.platform) params.set("platform", query.platform);
  if (query.search) params.set("search", query.search);
  if (query.statuses.length) params.set("status", query.statuses.join(","));
  if (query.sources.length) params.set("source", query.sources.join(","));
  if (query.owner) params.set("owner", query.owner);
  params.set("sort", query.sort);
  params.set("order", query.order);
  return `/api/leads/export?${canonicalSearch(params)}`;
}

