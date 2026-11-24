import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CheckCircle2, Coins, ContactRound, RotateCw, UserCheck, Users } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/shared/api";
import { fmtNumber } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import { EmptyState, ErrorState, Page, PageHeader } from "@/components/page";
import { HorizontalBarChart } from "@/components/charts";
import { ActionSummary } from "@/components/action-summary";
import { ActivityCalendar } from "@/components/activity-calendar";
import { Badge } from "@/components/ui/badge";
import { RoundStatusOrb } from "@/components/round-status-orb";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const STATUS_CHART_COLORS: Record<string, string> = {
  pending: "#a1a1aa",
  shortlisted: "#f59e0b",
  adopted: "#14b8a6",
  rejected: "#f43f5e",
};

const COOPERATION_CHART_COLORS: Record<string, string> = {
  not_contacted: "#a1a1aa",
  contacted: "#0ea5e9",
  negotiating: "#f59e0b",
  cooperating: "#8b5cf6",
  completed: "#10b981",
  declined: "#f43f5e",
  cancelled: "#94a3b8",
};

export function withRound(path: string, selectedRoundId: number | null, params: Record<string, string> = {}) {
  if (path !== "/creators" && path !== "/leads") return path;
  const search = new URLSearchParams(params);
  if (selectedRoundId !== null) search.set("round", String(selectedRoundId));
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export function DashboardPage() {
  const { user } = useAuth();
  const { roundId, meta, rounds, loading, setRoundId, job, activeJob } = useRound();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const canCollect = user?.role === "admin" || Boolean(user?.can_collect);
  const [selectedRoundId, setSelectedRoundId] = useState<number | null>(() => {
    if (!canCollect) return null;
    const value = Number(searchParams.get("round"));
    return Number.isFinite(value) && value > 0 ? value : null;
  });
  const selectedJob = selectedRoundId !== null && selectedRoundId === roundId ? job : null;
  const watchedJob = canCollect
    ? selectedRoundId === null
      ? activeJob
      : selectedJob
    : null;
  const jobActive = watchedJob?.status === "running" || watchedJob?.status === "pending";
  const dashboard = useQuery({
    queryKey: ["dashboard", selectedRoundId],
    queryFn: () => api.getDashboard(selectedRoundId),
    refetchInterval: jobActive ? 2500 : false,
  });

  const refreshDashboard = () => {
    void dashboard.refetch();
    void queryClient.invalidateQueries({ queryKey: ["activity"] });
  };

  useEffect(() => {
    if (!canCollect) {
      setSelectedRoundId(null);
      if (searchParams.has("round")) {
        const params = new URLSearchParams(searchParams);
        params.delete("round");
        setSearchParams(params, { replace: true });
      }
      return;
    }
    const value = Number(searchParams.get("round"));
    setSelectedRoundId(Number.isFinite(value) && value > 0 ? value : null);
  }, [searchParams, canCollect, setSearchParams]);

  useEffect(() => {
    if (!canCollect || loading || selectedRoundId === null) return;
    if (rounds.some((item) => item.id === selectedRoundId)) {
      if (roundId !== selectedRoundId) setRoundId(selectedRoundId);
      return;
    }
    setSelectedRoundId(null);
    const params = new URLSearchParams(searchParams);
    params.delete("round");
    setSearchParams(params, { replace: true });
  }, [loading, canCollect, selectedRoundId, rounds, roundId, setRoundId, searchParams, setSearchParams]);

  const handleRoundChange = (value: string) => {
    const next = value === "all" ? null : Number(value);
    setSelectedRoundId(next);
    if (next !== null) setRoundId(next);
    const params = new URLSearchParams(searchParams);
    if (next === null) params.delete("round");
    else params.set("round", String(next));
    setSearchParams(params, { replace: true });
  };

  if (dashboard.isLoading) {
    return (
      <Page>
        <PageHeader title="工作台" description="正在加载最新数据…" />
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-24 w-full rounded-xl" />)}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-72 w-full rounded-xl" />
          <Skeleton className="h-72 w-full rounded-xl" />
        </div>
      </Page>
    );
  }

  if (dashboard.error) {
    return (
      <Page>
        <PageHeader title="工作台" />
        <ErrorState message={dashboard.error instanceof Error ? dashboard.error.message : "加载失败"} />
        <Button
          variant="outline"
          onClick={refreshDashboard}
          disabled={dashboard.isFetching}
          className="w-fit"
        >
          <RotateCw className={dashboard.isFetching ? "animate-spin" : undefined} />
          重试
        </Button>
      </Page>
    );
  }

  const data = dashboard.data;
  if (!data?.metrics || rounds.length === 0) {
    return (
      <Page>
        <PageHeader title="工作台" />
        <EmptyState
          title={canCollect ? "还没有采集轮次" : "暂无数据"}
          description={
            canCollect
              ? "新建第一轮采集后，达人数、补全进度和互动数据会出现在这里。"
              : "当前还没有可展示的达人数据。"
          }
          actions={canCollect ? <Button asChild><Link to="/new">新建采集</Link></Button> : undefined}
        />
      </Page>
    );
  }

  const round = data.round;
  const metrics = data.metrics;
  const leadMetrics = data.lead_metrics ?? {
    leads: 0,
    unverified: 0,
    verifying: 0,
    converted: 0,
    duplicate: 0,
    discarded: 0,
  };
  const total = metrics.creators;
  const keywordsText = round?.keywords ?? "";
  const keywordCount =
    round?.keyword_details?.length ||
    (keywordsText ? keywordsText.split(/[、,，]/).filter(Boolean).length : 0);
  const completion = total > 0 ? Math.round((metrics.enriched / total) * 100) : 0;
  const unenriched = Math.max(0, total - metrics.enriched);
  const statusLabels = new Map((meta?.statuses ?? []).map((item) => [item.code, item.label]));
  const statusCounts = data.status_counts ?? {};
  const pendingCount = metrics.pending;
  const processedTotal = Math.max(0, total - pendingCount);
  const processedPercent = total > 0 ? (processedTotal / total) * 100 : 0;
  const adoptedRate = processedTotal > 0 ? Math.round((metrics.adopted / processedTotal) * 100) : 0;
  const costPerAdoption =
    metrics.adopted > 0 ? (metrics.cost ?? 0) / metrics.adopted : null;
  const tierEntries = Object.entries(data.tier_counts ?? {})
    .filter(([label, count]) => count > 0 && !label.includes("未补全"))
    .sort((a, b) => b[1] - a[1]);
  const tierTotal = tierEntries.reduce((sum, [, count]) => sum + count, 0);
  const orderedStatusCodes = [
    ...(meta?.statuses ?? []).map((item) => item.code).filter((code) => (statusCounts[code] ?? 0) > 0),
    ...Object.keys(statusCounts).filter(
      (code) => !(meta?.statuses ?? []).some((item) => item.code === code) && statusCounts[code] > 0,
    ),
  ];
  const statusSegments = orderedStatusCodes.map((code) => {
    const count = statusCounts[code] ?? 0;
    const percent = total > 0 ? (count / total) * 100 : 0;
    return {
      key: code,
      label: statusLabels.get(code) ?? code,
      value: count,
      percent,
      display: `${fmtNumber(count)} · ${percent.toFixed(1)}%`,
      color: STATUS_CHART_COLORS[code],
      href: withRound("/creators", selectedRoundId, { status: code }),
    };
  });
  const tierItems = tierEntries.map(([label, count]) => {
    const percent = tierTotal > 0 ? (count / tierTotal) * 100 : 0;
    return {
      label,
      value: count,
      percent,
      display: `${fmtNumber(count)} · ${percent.toFixed(1)}%`,
    };
  });
  const cooperationStatuses = meta?.cooperation_statuses ?? [];
  const cooperationCounts = data.cooperation_counts ?? {};
  const cooperationMetrics = data.cooperation_metrics;
  const cooperationEnrolled =
    cooperationMetrics?.enrolled ??
    Object.entries(cooperationCounts).reduce(
      (sum, [code, count]) => (code === "none" ? sum : sum + count),
      0,
    );
  const cooperationSegments = cooperationStatuses
    .filter((item) => (cooperationCounts[item.code] ?? 0) > 0)
    .map((item) => {
      const count = cooperationCounts[item.code] ?? 0;
      const percent = cooperationEnrolled > 0 ? (count / cooperationEnrolled) * 100 : 0;
      return {
        key: item.code,
        label: item.label,
        value: count,
        percent,
        display: `${fmtNumber(count)} · ${percent.toFixed(1)}%`,
        color: COOPERATION_CHART_COLORS[item.code],
        href: withRound("/creators", selectedRoundId, { cooperation: item.code }),
      };
    });
  const notContactedCount = cooperationCounts["not_contacted"] ?? 0;
  const cooperatingCount = cooperationCounts["cooperating"] ?? 0;
  const completedCount = cooperationCounts["completed"] ?? 0;
  const followedUpCount = Math.max(0, cooperationEnrolled - notContactedCount);
  const followUpRate =
    cooperationEnrolled > 0 ? (followedUpCount / cooperationEnrolled) * 100 : 0;
  const cooperationOpen = cooperationMetrics?.open ?? 0;
  const cooperationStale = cooperationMetrics?.stale ?? 0;
  const completedCases = cooperationMetrics?.completed_cases ?? 0;
  const repeatCreators = cooperationMetrics?.repeat_creators ?? 0;
  const jobPercent =
    watchedJob && watchedJob.progress_total > 0
      ? Math.round((watchedJob.progress_done / watchedJob.progress_total) * 100)
      : null;
  const isAllRounds = selectedRoundId === null;
  const pageDescription = !canCollect
    ? `${fmtNumber(metrics.creators)} 位达人 · ${fmtNumber(leadMetrics.leads)} 条线索`
    : isAllRounds
      ? `全部 ${rounds.length} 个未归档轮次 · ${fmtNumber(metrics.creators)} 位达人 · ${fmtNumber(leadMetrics.leads)} 条线索`
      : `${round?.platform_label ? `${round.platform_label} · ` : ""}${round?.category ? `${round.category} · ` : ""}${keywordCount} 个关键词 · 每词 ${round?.pages ?? 1} 页 · ${fmtNumber(metrics.creators)} 位达人 · ${fmtNumber(leadMetrics.leads)} 条线索`;

  const kpis = [
    {
      label: "达人总数",
      value: fmtNumber(metrics.creators),
      hint: isAllRounds ? (canCollect ? "跨轮去重后" : "已汇总去重") : "本轮候选",
      icon: Users,
      href: withRound("/creators", selectedRoundId),
    },
    {
      label: "已补全",
      value: fmtNumber(metrics.enriched),
      hint: (
        <>
          补全率 <span className="font-medium text-foreground">{completion}%</span> · 待补全{" "}
          <span className="font-medium text-foreground">{fmtNumber(unenriched)}</span>
        </>
      ),
      icon: CheckCircle2,
      href: withRound("/creators", selectedRoundId),
    },
    {
      label: "已采用",
      value: fmtNumber(metrics.adopted),
      hint: isAllRounds ? (
        processedTotal > 0 ? (
          <>
            采用率 <span className="font-medium text-foreground">{adoptedRate}%</span>
            {canCollect ? <>{" "}· 按各达人最近一条轮次统计</> : null}
          </>
        ) : (
          "尚无已审核"
        )
      ) : processedTotal > 0 ? (
        <>
          审核通过率 <span className="font-medium text-foreground">{adoptedRate}%</span>
          {notContactedCount > 0 || cooperatingCount > 0 || completedCount > 0 ? (
            <>
              {" "}· 未联系 <span className="font-medium text-foreground">{fmtNumber(notContactedCount)}</span>
              {" "}· 合作中 <span className="font-medium text-foreground">{fmtNumber(cooperatingCount)}</span>
              {completedCount > 0 ? (
                <>
                  {" "}· 完成 <span className="font-medium text-foreground">{fmtNumber(completedCount)}</span>
                </>
              ) : null}
            </>
          ) : null}
        </>
      ) : (
        "尚无已审核"
      ),
      icon: UserCheck,
      href: withRound("/creators", selectedRoundId, { status: "adopted" }),
    },
    {
      label: "线索",
      value: fmtNumber(leadMetrics.leads),
      hint: (
        <>
          待核实 <span className="font-medium text-foreground">{fmtNumber(leadMetrics.unverified)}</span> ·
          已转达人 <span className="font-medium text-foreground">{fmtNumber(leadMetrics.converted)}</span>
        </>
      ),
      icon: ContactRound,
      href: withRound("/leads", selectedRoundId),
    },
    {
      label: "总消耗",
      value: (
        <>
          {fmtNumber(metrics.cost ?? 0)}
          <span className="ml-1 text-sm font-normal text-muted-foreground">token</span>
        </>
      ),
      hint:
        costPerAdoption === null ? (
          "尚无已采用"
        ) : (
          <>
            单采用{" "}
            <span className="font-medium text-foreground">
              {fmtNumber(Math.round(costPerAdoption))} token
            </span>
          </>
        ),
      icon: Coins,
      href: canCollect ? "/tasks" : null,
    },
  ].filter((item) => canCollect || item.href !== null);

  return (
    <Page>
      <PageHeader
        title="工作台"
        description={`${pageDescription}${jobActive ? " · 采集进行中，自动刷新" : ""}`}
        actions={
          <>
            <Button variant="outline" onClick={refreshDashboard} disabled={dashboard.isFetching}>
              <RotateCw className={dashboard.isFetching ? "animate-spin" : undefined} />
              刷新
            </Button>
            {canCollect ? (
              <Select value={isAllRounds ? "all" : String(selectedRoundId)} onValueChange={handleRoundChange}>
                <SelectTrigger className="w-[220px]" aria-label="选择轮次">
                  <SelectValue placeholder="全部轮次" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部轮次</SelectItem>
                  {rounds.map((item) => (
                    <SelectItem key={item.id} value={String(item.id)}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
          </>
        }
      />

      {jobActive ? (
        <div
          className="flex flex-wrap items-center gap-3 border bg-muted/30 px-3 py-2"
          role="status"
          aria-live="polite"
        >
          <RoundStatusOrb status={watchedJob?.status} size={32} className="text-foreground" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <p className="flex flex-wrap items-baseline gap-2 text-sm font-medium">
              <span>
                {watchedJob?.status_label ?? "采集中"}
                {jobPercent !== null ? ` · ${jobPercent}%` : ""}
              </span>
              {watchedJob && watchedJob.progress_total > 0 ? (
                <span className="text-xs font-normal text-muted-foreground tabular-nums">
                  {watchedJob.progress_done} / {watchedJob.progress_total}
                </span>
              ) : null}
            </p>
            <p className="truncate text-xs text-muted-foreground">{watchedJob?.stage || "任务正在后台运行"}</p>
            {jobPercent !== null ? (
              <Progress value={jobPercent} className="h-1 max-w-md" aria-label="采集任务进度" />
            ) : null}
          </div>
          {canCollect ? (
            <Button asChild variant="outline" size="sm">
              <Link to={`/tasks/${watchedJob?.round_id ?? roundId}`}>查看采集任务</Link>
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
        {kpis.map((item) => {
          const card = (
            <Card size="sm" className="h-full transition-colors hover:bg-muted/30">
              <CardHeader className="gap-1">
                <CardDescription>{item.label}</CardDescription>
                <CardTitle className="text-3xl font-semibold tracking-tight tabular-nums">{item.value}</CardTitle>
                {item.hint ? <p className="text-xs text-muted-foreground tabular-nums">{item.hint}</p> : null}
                <CardAction>
                  <item.icon className="size-4 text-muted-foreground" aria-hidden="true" />
                </CardAction>
              </CardHeader>
            </Card>
          );
          return item.href ? (
            <Link
              key={item.label}
              to={item.href}
              className="rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {card}
            </Link>
          ) : (
            <div key={item.label}>{card}</div>
          );
        })}
        <ActionSummary roundId={selectedRoundId} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="h-full">
          <CardHeader>
            <CardTitle>{isAllRounds ? "采用分布" : "本轮审核进度"}</CardTitle>
            <CardDescription className="min-h-10">
              {isAllRounds
                ? `共 ${fmtNumber(total)} 位达人${canCollect ? "，按各达人最近一条轮次统计" : ""}，点击柱条按采用状态筛选。`
                : `共 ${fmtNumber(total)} 位达人，点击柱条按审核状态筛选。`}
            </CardDescription>
            <CardAction>
              <Badge
                variant="secondary"
                title={
                  isAllRounds
                    ? canCollect
                      ? "已处理 = 已审核（非待审核）达人 / 全部达人；按各达人最近一条轮次统计"
                      : "已处理 = 已审核（非待审核）达人 / 全部达人"
                    : "已处理 = 本轮已审核（非待审核）达人 / 本轮达人"
                }
              >
                已处理 {processedPercent.toFixed(1)}%
              </Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            {statusSegments.length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无达人数据。</p>
            ) : (
              <HorizontalBarChart
                items={statusSegments}
                ariaLabel={isAllRounds ? "采用状态分布" : "审核状态分布"}
                minCategories={5}
              />
            )}
          </CardContent>
        </Card>

        <Card className="h-full">
          <CardHeader>
            <CardTitle>粉丝层级</CardTitle>
            <CardDescription className="min-h-10">
              已补全 {fmtNumber(tierTotal)} 位达人的粉丝量级分布。
            </CardDescription>
            <CardAction>
              <Badge
                variant="secondary"
                title="已补全 = 资料完整（粉丝、笔记数、总点赞等已抓取）的达人 / 全部达人"
              >
                已补全 {completion}%
              </Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            {tierItems.length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无已补全的粉丝层级数据。</p>
            ) : (
              <HorizontalBarChart items={tierItems} ariaLabel="粉丝层级分布" minCategories={5} />
            )}
          </CardContent>
        </Card>

        <Card className="h-full">
          <CardHeader>
            <CardTitle>合作跟进</CardTitle>
            <CardDescription className="min-h-10">
              共 {fmtNumber(cooperationEnrolled)} 位已进入合作流程；进行中{" "}
              {fmtNumber(cooperationOpen)} 位，超期 {fmtNumber(cooperationStale)} 位。
              累计完成 {fmtNumber(completedCases)} 次，重复合作{" "}
              {fmtNumber(repeatCreators)} 人。
            </CardDescription>
            <CardAction>
              <Badge
                variant="secondary"
                title="按已进入合作流程的达人计算；已联系、洽谈中、合作中、合作完成、已拒绝、已取消均视为已跟进"
              >
                已跟进 {followUpRate.toFixed(1)}%
              </Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            {cooperationSegments.length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无已进入合作流程的达人。</p>
            ) : (
              <HorizontalBarChart
                items={cooperationSegments}
                ariaLabel="合作状态分布"
                minCategories={5}
              />
            )}
          </CardContent>
        </Card>
      </div>

      <ActivityCalendar roundId={selectedRoundId} hideRoundScope={!canCollect} />
    </Page>
  );
}
