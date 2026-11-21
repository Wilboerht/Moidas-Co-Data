import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Download,
  RotateCw,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api, exportUrl } from "@/shared/api";
import { fmtNumber, shorten } from "@/shared/format";
import { useRound } from "@/shared/round-context";
import type { Creator, CreatorQuery } from "@/shared/types";
import { EmptyState, ErrorState, Page, PageHeader, Section } from "@/components/page";
import { MultiSelect } from "@/components/multi-select";
import { CreatorSheet } from "@/components/creator-sheet";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Card, CardContent, CardDescription, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";

type CreatorRow = Creator & Record<string, unknown>;

const ALL_COLUMNS = [
  ["nickname", "昵称"],
  ["fans", "粉丝数"],
  ["fans_wan", "粉丝（万）"],
  ["fans_tier", "粉丝层级"],
  ["status", "采用状态"],
  ["match_direction", "匹配方向"],
  ["top_title", "代表作"],
  ["posted", "笔记数"],
  ["liked", "总点赞"],
  ["interaction", "互动量"],
  ["remark", "备注"],
  ["tags", "标签"],
  ["account_id", "账号 ID"],
  ["profile_url", "主页"],
  ["data_round", "数据轮次"],
  ["source_keywords", "命中关键词"],
  ["source", "数据来源"],
  ["executor", "执行者"],
  ["platform_label", "平台"],
  ["cooperation_platform", "合作主平台"],
  ["raw_title", "原始标题"],
  ["sample_notes", "样本笔记"],
  ["sample_likes", "样本点赞"],
  ["sample_interactions", "样本互动"],
] as const;

const CORE_COLUMNS = new Set([
  "nickname",
  "fans",
  "fans_tier",
  "status",
  "match_direction",
  "top_title",
  "posted",
  "liked",
  "interaction",
]);

const TOGGLE_COLUMNS = ALL_COLUMNS.filter(([key]) => key !== "nickname");
const DEFAULT_VISIBILITY = Object.fromEntries(
  ALL_COLUMNS.map(([key]) => [key, CORE_COLUMNS.has(key)]),
) as Record<string, boolean>;

const SORT_OPTIONS = [
  { value: "fans", label: "粉丝数" },
  { value: "interaction", label: "互动量" },
  { value: "sample_likes", label: "样本点赞" },
  { value: "nickname", label: "昵称" },
];

const NUMERIC_COLUMNS = new Set(["fans", "posted", "liked", "interaction", "sample_notes", "sample_likes", "sample_interactions"]);
const WIDE_COLUMNS = new Set(["top_title", "raw_title", "match_direction", "remark", "tags", "source_keywords"]);

function readVisibility() {
  try {
    const raw = window.localStorage.getItem("codata_columns_v5");
    const saved = raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
    return { ...DEFAULT_VISIBILITY, ...saved, nickname: true };
  } catch {
    return DEFAULT_VISIBILITY;
  }
}

function useDebounced<T>(value: T, delay = 300) {
  const [next, setNext] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setNext(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return next;
}

function facetOptions(items: { value: string; count: number }[] | undefined) {
  return (items ?? []).map((item) => ({ value: item.value, label: `${item.value} (${item.count})` }));
}

export function CreatorsPage() {
  const { meta, rounds, refreshRounds, loading } = useRound();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const statuses = meta?.statuses ?? [];
  const platformLabels = new Map((meta?.platforms ?? []).map((item) => [item.code, item.label]));

  const [roundFilter, setRoundFilter] = useState<number | null>(() => {
    const value = params.get("round");
    const parsed = value ? Number(value) : NaN;
    return value && value !== "all" && Number.isFinite(parsed) ? parsed : null;
  });
  const [platform, setPlatform] = useState(params.get("platform") ?? "");
  const [page, setPage] = useState(() => {
    const value = Number(params.get("page") ?? 1);
    return Number.isFinite(value) && value > 0 ? Math.floor(value) : 1;
  });
  const [pageSize, setPageSize] = useState(() => {
    const value = Number(params.get("page_size") ?? 50);
    return Number.isFinite(value) ? Math.min(200, Math.max(25, Math.floor(value))) : 50;
  });
  const [search, setSearch] = useState(params.get("search") ?? "");
  const debouncedSearch = useDebounced(search);
  const [statusFilter, setStatusFilter] = useState<string[]>(params.get("status")?.split(",").filter(Boolean) ?? []);
  const [tierFilter, setTierFilter] = useState<string[]>(params.get("tier")?.split(",").filter(Boolean) ?? []);
  const [directionFilter, setDirectionFilter] = useState<string[]>(params.get("direction")?.split(",").filter(Boolean) ?? []);
  const [sourceFilter, setSourceFilter] = useState<string[]>(params.get("source")?.split(",").filter(Boolean) ?? []);
  const [minFans, setMinFans] = useState(params.get("min_fans") ?? "");
  const [maxFans, setMaxFans] = useState(params.get("max_fans") ?? "");
  const [onlyUnenriched, setOnlyUnenriched] = useState(params.get("only_unenriched") === "true");
  const [sort, setSort] = useState(params.get("sort") ?? "fans");
  const [order, setOrder] = useState<"asc" | "desc">(params.get("order") === "asc" ? "asc" : "desc");
  const [showMore, setShowMore] = useState(false);
  const [visibleColumns, setVisibleColumns] = useState<Record<string, boolean>>(readVisibility);
  const [selected, setSelected] = useState<Record<string, Creator>>({});
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchStatus, setBatchStatus] = useState("none");
  const [batchDirection, setBatchDirection] = useState("");
  const [batchRemark, setBatchRemark] = useState("");
  const detailKey = params.get("creator");
  const detailRoundParam = params.get("creator_round");
  const parsedDetailRound = detailRoundParam ? Number(detailRoundParam) : NaN;
  const detailRoundId = Number.isFinite(parsedDetailRound)
    ? parsedDetailRound
    : roundFilter ?? rounds[0]?.id ?? null;

  const facetsQuery = useQuery({
    queryKey: ["facets", roundFilter, platform],
    queryFn: () => api.getFacets(roundFilter, platform),
  });

  useEffect(() => {
    const available = facetsQuery.data?.platforms ?? [];
    if (available.length > 0 && !available.some((item) => item.value === platform)) {
      setPlatform(available.find((item) => item.value === "xhs")?.value ?? available[0].value);
    }
  }, [facetsQuery.data, platform]);

  useEffect(() => {
    window.localStorage.setItem("codata_columns_v5", JSON.stringify(visibleColumns));
  }, [visibleColumns]);

  useEffect(() => {
    if (roundFilter !== null && rounds.length > 0 && !rounds.some((item) => item.id === roundFilter)) {
      setRoundFilter(null);
    }
  }, [roundFilter, rounds]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (roundFilter !== null) next.set("round", String(roundFilter));
    if (platform) next.set("platform", platform);
    if (page > 1) next.set("page", String(page));
    if (pageSize !== 50) next.set("page_size", String(pageSize));
    if (debouncedSearch) next.set("search", debouncedSearch);
    if (statusFilter.length) next.set("status", statusFilter.join(","));
    if (tierFilter.length) next.set("tier", tierFilter.join(","));
    if (directionFilter.length) next.set("direction", directionFilter.join(","));
    if (sourceFilter.length) next.set("source", sourceFilter.join(","));
    if (minFans) next.set("min_fans", minFans);
    if (maxFans) next.set("max_fans", maxFans);
    if (onlyUnenriched) next.set("only_unenriched", "true");
    next.set("sort", sort);
    next.set("order", order);
    if (detailKey) {
      next.set("creator", detailKey);
      if (detailRoundParam) next.set("creator_round", detailRoundParam);
    }
    setParams(next, { replace: true });
  }, [
    roundFilter, platform, page, pageSize, debouncedSearch, statusFilter, tierFilter,
    directionFilter, sourceFilter, minFans, maxFans, onlyUnenriched, sort, order,
    detailKey, detailRoundParam, setParams,
  ]);

  useEffect(() => {
    setPage(1);
    setSelected({});
  }, [roundFilter, platform, debouncedSearch, statusFilter, tierFilter, directionFilter, sourceFilter, minFans, maxFans, onlyUnenriched]);

  const creatorQuery: CreatorQuery = useMemo(
    () => ({
      platform, page, page_size: pageSize, search: debouncedSearch,
      statuses: statusFilter, tiers: tierFilter, directions: directionFilter, sources: sourceFilter,
      min_fans: minFans, max_fans: maxFans, only_unenriched: onlyUnenriched, sort, order,
    }),
    [platform, page, pageSize, debouncedSearch, statusFilter, tierFilter, directionFilter, sourceFilter, minFans, maxFans, onlyUnenriched, sort, order],
  );

  const creatorsQuery = useQuery({
    queryKey: ["creators", roundFilter, creatorQuery],
    queryFn: () => api.getCreators(roundFilter, creatorQuery),
    enabled: Boolean(platform),
  });

  const batchMutation = useMutation({
    mutationFn: async () => {
      const groups = new Map<number, { platform: string; user_id: string }[]>();
      for (const item of Object.values(selected)) {
        const targetRound = item.round_id ?? roundFilter ?? rounds[0]?.id;
        if (targetRound == null) continue;
        const rows = groups.get(targetRound) ?? [];
        rows.push({ platform: item.platform, user_id: item.user_id });
        groups.set(targetRound, rows);
      }
      const results = await Promise.all(
        [...groups.entries()].map(([targetRound, items]) =>
          api.batchUpdate(targetRound, {
            items,
            status: batchStatus === "none" ? null : batchStatus,
            match_direction: batchDirection || null,
            remark: batchRemark || null,
          }),
        ),
      );
      return { changed: results.reduce((sum, item) => sum + item.changed, 0) };
    },
    onSuccess: async (result) => {
      toast.success(`已更新 ${result.changed} 位达人`);
      setSelected({});
      setBatchOpen(false);
      setBatchStatus("none");
      setBatchDirection("");
      setBatchRemark("");
      await queryClient.invalidateQueries({ queryKey: ["creators"] });
      await queryClient.invalidateQueries({ queryKey: ["facets"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      await refreshRounds();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "批量更新失败"),
  });

  const clearFilters = () => {
    setSearch("");
    setStatusFilter([]);
    setTierFilter([]);
    setDirectionFilter([]);
    setSourceFilter([]);
    setMinFans("");
    setMaxFans("");
    setOnlyUnenriched(false);
  };

  const currentRound = rounds.find((item) => item.id === roundFilter);
  const scopeLabel =
    roundFilter === null
      ? "全部轮次"
      : currentRound
        ? `${currentRound.name}${currentRound.category ? ` · ${currentRound.category}` : ""}`
        : `轮次 ${roundFilter}`;
  const total = creatorsQuery.data?.total ?? 0;
  const items = (creatorsQuery.data?.items ?? []) as CreatorRow[];
  const selectedCount = Object.values(selected).length;
  const advancedFilterCount =
    directionFilter.length + sourceFilter.length +
    (minFans ? 1 : 0) + (maxFans ? 1 : 0) + (onlyUnenriched ? 1 : 0);
  const activeFilters =
    statusFilter.length + tierFilter.length + advancedFilterCount +
    (debouncedSearch ? 1 : 0);
  const platformTabs = facetsQuery.data?.platforms ?? [];
  const visibleKeys = ALL_COLUMNS.filter(([key]) => key === "nickname" || visibleColumns[key] !== false).map(([key]) => key);
  const pageAllSelected = items.length > 0 && items.every((item) => Boolean(selected[item.key]));
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const togglePageSelection = () => {
    setSelected((current) => {
      if (pageAllSelected) {
        const next = { ...current };
        items.forEach((item) => delete next[item.key]);
        return next;
      }
      return {
        ...current,
        ...Object.fromEntries(items.map((item) => [item.key, item])),
      };
    });
  };

  const toggleRow = (item: CreatorRow) => {
    setSelected((current) => {
      const next = { ...current };
      if (next[item.key]) delete next[item.key];
      else next[item.key] = item;
      return next;
    });
  };

  const renderCell = (key: string, item: CreatorRow) => {
    switch (key) {
      case "fans": return fmtNumber(item.fans);
      case "fans_wan": return item.fans_wan || "—";
      case "fans_tier": return item.fans_tier || "—";
      case "status": return <StatusBadge status={item.status} label={item.status_label} />;
      case "match_direction": return shorten(item.match_direction, 36) || "—";
      case "top_title": return shorten(item.top_title, 48) || "—";
      case "posted": return fmtNumber(item.posted);
      case "liked": return fmtNumber(item.liked);
      case "interaction": return fmtNumber(item.interaction);
      case "remark": return shorten(item.remark, 36) || "—";
      case "tags": return shorten(item.tags, 36) || "—";
      case "account_id": return <span className="font-mono text-xs">{item.account_id || "—"}</span>;
      case "profile_url": return item.profile_url ? <a className="underline underline-offset-4" href={item.profile_url} target="_blank" rel="noreferrer">打开</a> : "—";
      case "data_round": return item.data_round || "—";
      case "source_keywords": return shorten(item.source_keywords.join("、"), 48) || "—";
      case "source": return item.source || "—";
      case "executor": return item.executor || "—";
      case "platform_label": return item.platform_label || "—";
      case "cooperation_platform": return item.cooperation_platform || "—";
      case "raw_title": return shorten(item.raw_title, 48) || "—";
      case "sample_notes": return fmtNumber(item.sample_notes);
      case "sample_likes": return fmtNumber(item.sample_likes);
      case "sample_interactions": return fmtNumber(item.sample_interactions);
      default: return "—";
    }
  };

  if (rounds.length === 0 && loading) {
    return (
      <Page>
        <PageHeader title="达人库" />
        <Skeleton className="h-72 w-full" />
      </Page>
    );
  }

  if (rounds.length === 0) {
    return (
      <Page>
        <PageHeader title="达人库" />
        <EmptyState
          title="还没有采集轮次"
          description="新建一轮采集，再进入达人筛选。"
          actions={<Button asChild><Link to="/new">新建采集</Link></Button>}
        />
      </Page>
    );
  }

  return (
    <Page>
      <PageHeader
        title="达人库"
        description={`${scopeLabel} · ${platformLabels.get(platform) ?? "全部平台"} · ${fmtNumber(total)} 位达人`}
        actions={
          <>
            <Select
              value={roundFilter === null ? "all" : String(roundFilter)}
              onValueChange={(value) => setRoundFilter(value === "all" ? null : Number(value))}
              disabled={rounds.length === 0}
            >
              <SelectTrigger className="w-[180px] max-w-full" aria-label="轮次">
                <SelectValue placeholder="全部轮次" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部轮次</SelectItem>
                {rounds.map((round) => (
                  <SelectItem key={round.id} value={String(round.id)}>
                    {round.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={platform} onValueChange={setPlatform} disabled={platformTabs.length === 0}>
              <SelectTrigger className="w-[220px] max-w-full" aria-label="平台">
                <SelectValue placeholder="选择平台" />
              </SelectTrigger>
              <SelectContent>
                {platformTabs.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {platformLabels.get(item.value) ?? item.value} · {fmtNumber(item.count)} 位
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="outline" onClick={() => void creatorsQuery.refetch()} disabled={creatorsQuery.isFetching}>
              <RotateCw className={creatorsQuery.isFetching ? "animate-spin" : undefined} />
              刷新
            </Button>
          </>
        }
      />

      {facetsQuery.error ? (
        <ErrorState
          message={facetsQuery.error instanceof Error ? facetsQuery.error.message : "加载筛选条件失败"}
        />
      ) : null}

      <Section
        title="筛选与排序"
        description="搜索结果、筛选条件和排序会保存在 URL 中。"
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => setShowMore((value) => !value)}>
              <SlidersHorizontal />
              {showMore ? "收起高级筛选" : "高级筛选"}
              {advancedFilterCount > 0 ? (
                <span className="ml-1 rounded-full bg-muted px-1.5 text-[10px] tabular-nums text-muted-foreground">
                  {advancedFilterCount}
                </span>
              ) : null}
            </Button>
            {activeFilters > 0 ? <Button variant="ghost" size="sm" onClick={clearFilters}>清除筛选</Button> : null}
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-2">
              <Label htmlFor="creator-search">搜索</Label>
              <Input
                id="creator-search"
                name="creator-search"
                autoComplete="off"
                spellCheck={false}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="昵称、账号 ID、执行者或标题"
              />
            </div>
            <div className="space-y-2">
              <Label>采用状态</Label>
              <MultiSelect
                label="采用状态"
                options={statuses.map((item) => ({ value: item.code, label: item.label }))}
                value={statusFilter}
                onChange={setStatusFilter}
                placeholder="全部状态"
                searchPlaceholder="搜索状态…"
                emptyText="暂无状态"
              />
            </div>
            <div className="space-y-2">
              <Label>粉丝层级</Label>
              <MultiSelect
                label="粉丝层级"
                options={facetOptions(facetsQuery.data?.tiers)}
                value={tierFilter}
                onChange={setTierFilter}
                placeholder="全部层级"
                searchPlaceholder="搜索层级…"
                emptyText="暂无层级"
              />
            </div>
            <div className="space-y-2">
              <Label>排序</Label>
              <div className="flex gap-2">
                <Select value={sort} onValueChange={setSort}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {SORT_OPTIONS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label={order === "desc" ? "降序" : "升序"}
                  onClick={() => setOrder((value) => (value === "desc" ? "asc" : "desc"))}
                >
                  {order === "desc" ? <ArrowDown /> : <ArrowUp />}
                </Button>
              </div>
            </div>
          </div>

          {showMore ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <div className="space-y-2">
                <Label>匹配方向</Label>
                <MultiSelect
                  label="匹配方向"
                  options={facetOptions(facetsQuery.data?.directions)}
                  value={directionFilter}
                  onChange={setDirectionFilter}
                  placeholder="全部方向"
                  searchPlaceholder="搜索方向…"
                  emptyText="暂无方向"
                />
              </div>
              <div className="space-y-2">
                <Label>数据来源</Label>
                <MultiSelect
                  label="数据来源"
                  options={facetOptions(facetsQuery.data?.sources)}
                  value={sourceFilter}
                  onChange={setSourceFilter}
                  placeholder="全部来源"
                  searchPlaceholder="搜索来源…"
                  emptyText="暂无来源"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="min-fans">最低粉丝</Label>
                <Input id="min-fans" inputMode="numeric" value={minFans} onChange={(event) => setMinFans(event.target.value)} placeholder="不限" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="max-fans">最高粉丝</Label>
                <Input id="max-fans" inputMode="numeric" value={maxFans} onChange={(event) => setMaxFans(event.target.value)} placeholder="不限" />
              </div>
              <div className="flex items-center gap-2 pt-1">
                <Switch id="only-unenriched" checked={onlyUnenriched} onCheckedChange={setOnlyUnenriched} />
                <Label htmlFor="only-unenriched">只看未补全</Label>
              </div>
            </div>
          ) : null}

          {activeFilters > 0 ? (
            <div className="flex flex-wrap items-center gap-2 border-t pt-4">
              <span className="text-xs text-muted-foreground">当前筛选</span>
              {debouncedSearch ? (
                <FilterChip label={`搜索：${debouncedSearch}`} onRemove={() => setSearch("")} />
              ) : null}
              {statusFilter.length > 0 ? (
                <FilterChip label={`状态：${summarize(statusFilter, new Map(statuses.map((item) => [item.code, item.label])))}`} onRemove={() => setStatusFilter([])} />
              ) : null}
              {tierFilter.length > 0 ? (
                <FilterChip label={`层级：${summarize(tierFilter)}`} onRemove={() => setTierFilter([])} />
              ) : null}
              {directionFilter.length > 0 ? (
                <FilterChip label={`方向：${summarize(directionFilter)}`} onRemove={() => setDirectionFilter([])} />
              ) : null}
              {sourceFilter.length > 0 ? (
                <FilterChip label={`来源：${summarize(sourceFilter)}`} onRemove={() => setSourceFilter([])} />
              ) : null}
              {minFans ? <FilterChip label={`粉丝 ≥ ${minFans}`} onRemove={() => setMinFans("")} /> : null}
              {maxFans ? <FilterChip label={`粉丝 ≤ ${maxFans}`} onRemove={() => setMaxFans("")} /> : null}
              {onlyUnenriched ? <FilterChip label="只看未补全" onRemove={() => setOnlyUnenriched(false)} /> : null}
            </div>
          ) : null}
        </div>
      </Section>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3 px-4">
          <div className="min-w-0 space-y-1">
            <CardTitle role="heading" aria-level={2}>达人列表</CardTitle>
            <CardDescription>
              {activeFilters > 0 ? `应用了 ${activeFilters} 项筛选条件` : "当前平台的全部达人"}
            </CardDescription>
          </div>
          <div className="flex w-full flex-wrap items-center justify-end gap-2 md:w-auto">
            <div className="flex items-center gap-2">
              <span className="hidden shrink-0 text-sm text-muted-foreground sm:inline">显示列</span>
              <div className="w-40 max-w-full sm:w-56">
                <MultiSelect
                  label="显示列"
                  options={TOGGLE_COLUMNS.map(([value, label]) => ({ value, label }))}
                  value={TOGGLE_COLUMNS.filter(([key]) => visibleColumns[key] !== false).map(([key]) => key)}
                  onChange={(value) =>
                    setVisibleColumns({
                      ...Object.fromEntries(ALL_COLUMNS.map(([key]) => [key, value.includes(key)])),
                      nickname: true,
                    })
                  }
                  placeholder="选择显示列"
                  searchPlaceholder="搜索列…"
                  emptyText="暂无可显示列"
                />
              </div>
            </div>
            <div className="hidden flex-wrap items-center gap-2 md:flex">
              <Button asChild variant="outline" size="sm">
                <a href={exportUrl(roundFilter, "csv", "filtered", creatorQuery)}><Download />导出 CSV</a>
              </Button>
              <Button asChild variant="outline" size="sm">
                <a href={exportUrl(roundFilter, "xlsx", "filtered", creatorQuery)}><Download />导出 Excel</a>
              </Button>
              <Button asChild variant="ghost" size="sm">
                <a href={exportUrl(roundFilter, "csv", "all")}>导出全部 CSV</a>
              </Button>
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="md:hidden">
                  <Download />导出
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem asChild>
                  <a href={exportUrl(roundFilter, "csv", "filtered", creatorQuery)}>导出当前筛选 CSV</a>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <a href={exportUrl(roundFilter, "xlsx", "filtered", creatorQuery)}>导出当前筛选 Excel</a>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <a href={exportUrl(roundFilter, "csv", "all")}>导出全部 CSV</a>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            {selectedCount > 0 ? (
              <>
                <span className="text-sm text-muted-foreground">已选 {selectedCount} 位</span>
                <Button size="sm" onClick={() => setBatchOpen(true)}>批量修改</Button>
                <Button variant="ghost" size="sm" onClick={() => setSelected({})}>取消选择</Button>
              </>
            ) : (
              <Button variant="ghost" size="sm" onClick={togglePageSelection}>全选本页</Button>
            )}
          </div>
        </div>
        <CardContent className="px-0">
        {creatorsQuery.isLoading ? (
          <div className="space-y-2 p-6">
            {Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-9 w-full" />)}
          </div>
        ) : creatorsQuery.error ? (
          <ErrorState message={creatorsQuery.error instanceof Error ? creatorsQuery.error.message : "加载达人失败"} />
        ) : (
          <div className="space-y-3">
            <Table className="min-w-[1320px]">
                <TableHeader>
                  <TableRow className="bg-muted/50">
                    <TableHead className="sticky left-0 z-20 min-w-[240px] bg-muted">
                      <div className="flex items-center gap-2">
                        <Checkbox
                          aria-label="全选本页达人"
                          checked={pageAllSelected}
                          onCheckedChange={togglePageSelection}
                        />
                        达人
                      </div>
                    </TableHead>
                    {visibleKeys.filter((key) => key !== "nickname").map((key) => (
                      <TableHead
                        key={key}
                        className={`${NUMERIC_COLUMNS.has(key) ? "text-right" : ""} ${WIDE_COLUMNS.has(key) ? "min-w-[180px]" : "whitespace-nowrap"}`}
                      >
                        {ALL_COLUMNS.find(([column]) => column === key)?.[1] ?? key}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={visibleKeys.length} className="h-28 text-center text-muted-foreground">
                        当前条件下没有达人
                      </TableCell>
                    </TableRow>
                  ) : (
                    items.map((item) => (
                      <TableRow key={item.key}>
                        <TableCell className="sticky left-0 z-10 min-w-[240px] bg-background">
                          <div className="flex items-center gap-2">
                            <Checkbox
                              aria-label={`选择 ${item.nickname || item.key}`}
                              checked={Boolean(selected[item.key])}
                              onCheckedChange={() => toggleRow(item)}
                            />
                            <button
                              type="button"
                              className="truncate text-left font-medium underline-offset-4 hover:underline"
                              onClick={() => {
                                const next = new URLSearchParams(params);
                                next.set("creator", item.key);
                                if (item.round_id != null) next.set("creator_round", String(item.round_id));
                                setParams(next, { replace: true });
                              }}
                            >
                              {item.nickname || "(未补全)"}
                            </button>
                          </div>
                        </TableCell>
                        {visibleKeys.filter((key) => key !== "nickname").map((key) => (
                          <TableCell
                            key={key}
                            className={`${NUMERIC_COLUMNS.has(key) ? "text-right tabular-nums" : ""} ${WIDE_COLUMNS.has(key) ? "max-w-[240px] truncate text-muted-foreground" : "whitespace-nowrap"}`}
                          >
                            {renderCell(key, item)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))
                  )}
                </TableBody>
            </Table>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3">
              <p className="text-sm text-muted-foreground">
                共 {fmtNumber(total)} 位 · 第 {page} / {totalPages} 页
              </p>
              <div className="flex items-center gap-2">
                <Select value={String(pageSize)} onValueChange={(value) => { setPageSize(Number(value)); setPage(1); }}>
                  <SelectTrigger className="h-8 w-[100px]"><SelectValue /></SelectTrigger>
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
          </div>
        )}
      </CardContent>
      </Card>

      <Dialog open={batchOpen} onOpenChange={setBatchOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>批量修改 {selectedCount} 位达人</DialogTitle>
            <DialogDescription>留空的字段不会被修改。</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>采用状态</Label>
              <Select value={batchStatus} onValueChange={setBatchStatus}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">不修改</SelectItem>
                  {statuses.map((item) => <SelectItem key={item.code} value={item.code}>{item.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="batch-direction">匹配方向</Label>
              <Input id="batch-direction" value={batchDirection} onChange={(event) => setBatchDirection(event.target.value)} placeholder="不修改" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="batch-remark">备注</Label>
              <Textarea id="batch-remark" value={batchRemark} onChange={(event) => setBatchRemark(event.target.value)} placeholder="不修改" rows={3} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBatchOpen(false)}>取消</Button>
            <Button onClick={() => batchMutation.mutate()} disabled={batchMutation.isPending}>
              {batchMutation.isPending ? "应用中…" : "应用修改"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {detailKey && detailRoundId !== null ? (
        <CreatorSheet
          roundId={detailRoundId}
          detailKey={detailKey}
          statuses={statuses}
          onClose={() => {
            const next = new URLSearchParams(params);
            next.delete("creator");
            next.delete("creator_round");
            setParams(next, { replace: true });
          }}
        />
      ) : null}
    </Page>
  );
}


function summarize(values: string[], labels?: Map<string, string>) {
  const first = labels?.get(values[0]) ?? values[0];
  return values.length > 1 ? `${first} +${values.length - 1}` : first;
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <Badge variant="secondary" className="max-w-full gap-1 pr-1 font-normal">
      <span className="truncate">{label}</span>
      <button
        type="button"
        className="rounded-full p-0.5 text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
        aria-label={`移除筛选：${label}`}
        onClick={onRemove}
      >
        <X className="size-3" />
      </button>
    </Badge>
  );
}
