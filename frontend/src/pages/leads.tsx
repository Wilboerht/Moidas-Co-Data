import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Download, LoaderCircle, Plus, RotateCcw, RotateCw, Search } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/shared/api";
import { fmtDateTime, fmtNumber } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import type { LeadOwner, LeadQuery } from "@/shared/types";
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
  const { meta, rounds } = useRound();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const debouncedSearch = useDebounced(search);
  const [roundFilter, setRoundFilter] = useState<number | null>(() => {
    const value = Number(params.get("round"));
    return Number.isFinite(value) && value > 0 ? value : null;
  });
  const [platform, setPlatform] = useState(params.get("platform") ?? "");
  const [statusFilter, setStatusFilter] = useState<string[]>(() => {
    const value = params.get("status");
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
  const canCollect = user?.role === "admin" || Boolean(user?.can_collect);
  const leadStatuses = meta?.lead_statuses ?? [];
  const leadSources = meta?.lead_sources ?? [];
  const platforms = meta?.platforms ?? [];

  useEffect(() => {
    setPage(1);
  }, [roundFilter, platform, debouncedSearch, statusFilter, sourceFilter, owner]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (roundFilter !== null) next.set("round", String(roundFilter));
    if (platform) next.set("platform", platform);
    if (debouncedSearch) next.set("search", debouncedSearch);
    if (statusFilter.length) next.set("status", statusFilter.join(","));
    if (sourceFilter.length) next.set("source", sourceFilter.join(","));
    if (owner) next.set("owner", owner);
    if (page > 1) next.set("page", String(page));
    if (pageSize !== 50) next.set("page_size", String(pageSize));
    if (selectedLeadId !== null) next.set("lead", String(selectedLeadId));
    setParams(next, { replace: true });
  }, [roundFilter, platform, debouncedSearch, statusFilter, sourceFilter, owner, page, pageSize, selectedLeadId, setParams]);

  const query: LeadQuery = useMemo(
    () => ({
      round_id: roundFilter,
      platform,
      search: debouncedSearch,
      statuses: statusFilter,
      sources: sourceFilter,
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
  const ownersQuery = useQuery({
    queryKey: ["lead-owners"],
    queryFn: api.getLeadOwners,
  });

  const owners: LeadOwner[] = ownersQuery.data ?? [];
  const assignableOwners = user?.role === "admin" ? owners : owners.filter((item) => item.id === user?.id);
  const total = leadsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);
  const empty = !leadsQuery.isLoading && !leadsQuery.error && (leadsQuery.data?.items ?? []).length === 0;

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["leads"] });
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
            {canCollect ? (
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
            <Button asChild variant="outline">
              <a href={leadExportUrl(query, "csv")}>
                <Download /> 导出 CSV
              </a>
            </Button>
          </>
        }
      />

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
                {platforms.map((item) => (
                  <SelectItem key={item.code} value={item.code}>{item.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
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
          <EmptyState title="没有线索" description="可以从导入、手动录入或客户链接开始收集线索。" />
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
                <TableHead>更新时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(leadsQuery.data?.items ?? []).map((lead) => (
                <TableRow
                  key={lead.id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setSelectedLeadId(lead.id)}
                >
                  <TableCell className="font-medium">
                    <div className="max-w-[240px] truncate">{lead.nickname || lead.raw_title || `线索 #${lead.id}`}</div>
                    {lead.raw_title && lead.nickname ? (
                      <div className="max-w-[240px] truncate text-xs text-muted-foreground">{lead.raw_title}</div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{lead.platform_label || lead.platform}</TableCell>
                  <TableCell className="max-w-[220px] truncate font-mono text-xs text-muted-foreground">
                    {lead.account_id || lead.profile_url || "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {lead.source_label}{lead.source_round_name ? ` · ${lead.source_round_name}` : ""}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{lead.owner_username || "未分配"}</TableCell>
                  <TableCell><LeadStatusBadge status={lead.status} label={lead.status_label} /></TableCell>
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
          onClose={() => setSelectedLeadId(null)}
        />
      ) : null}

      <BulkLeadDialog
        open={bulkOpen}
        onClose={() => setBulkOpen(false)}
        platforms={platforms}
        rounds={rounds}
        sources={leadSources}
        onCreated={refresh}
      />

      <CreateLeadDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        platforms={platforms}
        rounds={rounds}
        owners={assignableOwners}
        sources={leadSources}
        onCreated={async (leadId) => {
          setCreateOpen(false);
          setSelectedLeadId(leadId);
          await refresh();
        }}
      />
    </Page>
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
}: {
  open: boolean;
  onClose: () => void;
  platforms: { code: string; label: string }[];
  rounds: { id: number; name: string }[];
  owners: LeadOwner[];
  sources: { code: string; label: string }[];
  onCreated: (leadId: number) => void;
}) {
  const [platform, setPlatform] = useState("xhs");
  const [nickname, setNickname] = useState("");
  const [accountId, setAccountId] = useState("");
  const [profileUrl, setProfileUrl] = useState("");
  const [rawTitle, setRawTitle] = useState("");
  const [source, setSource] = useState("manual");
  const [roundId, setRoundId] = useState<string>("none");
  const [ownerId, setOwnerId] = useState<string>("none");
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPlatform("xhs");
    setNickname("");
    setAccountId("");
    setProfileUrl("");
    setRawTitle("");
    setSource("manual");
    setRoundId("none");
    setOwnerId("none");
    setCloseConfirmOpen(false);
  }, [open]);

  const canSubmit = Boolean(nickname.trim() || accountId.trim() || profileUrl.trim());
  const dirty = Boolean(
    nickname.trim() ||
    accountId.trim() ||
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
        profile_url: profileUrl,
        raw_title: rawTitle,
        source,
        source_round_id: roundId === "none" ? null : Number(roundId),
        owner_user_id: ownerId === "none" ? null : Number(ownerId),
      }),
    onSuccess: (lead) => {
      toast.success("线索已创建");
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
              没有真实账号 ID 也可以先存为线索，之后在详情里核实并转入达人库。
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
                <Label htmlFor="create-lead-account">真实账号 ID</Label>
                <Input
                  id="create-lead-account"
                  value={accountId}
                  disabled={mutation.isPending}
                  onChange={(event) => setAccountId(event.target.value)}
                  placeholder="填写后可用于转换"
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
                  placeholder="https://..."
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
  return `/api/leads/export?${params.toString()}`;
}

