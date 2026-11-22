import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowDown, CheckCircle2, Coins, ContactRound, UserCheck, Users } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/shared/api";
import { fmtDateTime, fmtNumber } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import type { Creator, Round } from "@/shared/types";
import { EmptyState, ErrorState, Page, PageHeader } from "@/components/page";
import { HorizontalBarChart, ProcessingBar } from "@/components/charts";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { RoundStatusOrb } from "@/components/round-status-orb";
import { Button } from "@/components/ui/button";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type CreatorRow = Creator & Record<string, unknown>;
type RoundRow = Round & Record<string, unknown>;

export function DashboardPage() {
  const { user } = useAuth();
  const { roundId, meta, rounds, setRoundId, job, activeJob } = useRound();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedRoundId, setSelectedRoundId] = useState<number | null>(() => {
    const value = Number(searchParams.get("round"));
    return Number.isFinite(value) && value > 0 ? value : null;
  });
  const selectedJob = selectedRoundId !== null && selectedRoundId === roundId ? job : null;
  const watchedJob = selectedRoundId === null ? activeJob : selectedJob;
  const jobActive = watchedJob?.status === "running" || watchedJob?.status === "pending";
  const dashboard = useQuery({
    queryKey: ["dashboard", selectedRoundId],
    queryFn: () => api.getDashboard(selectedRoundId),
    refetchInterval: jobActive ? 2500 : false,
  });

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
        <Skeleton className="h-8 w-36" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-28 w-full rounded-xl" />)}
        </div>
        <Skeleton className="h-72 w-full rounded-xl" />
      </Page>
    );
  }

  if (dashboard.error) {
    return (
      <Page>
        <ErrorState message={dashboard.error instanceof Error ? dashboard.error.message : "加载失败"} />
      </Page>
    );
  }

  const data = dashboard.data;
  if (!data?.metrics || data.counts.rounds === 0) {
    return (
      <Page>
        <PageHeader title="工作台" />
        <EmptyState
          title="还没有采集轮次"
          description="新建第一轮采集后，达人数、补全进度和互动数据会出现在这里。"
          actions={<Button asChild><Link to="/new">新建采集</Link></Button>}
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
  const statusLabels = new Map((meta?.statuses ?? []).map((item) => [item.code, item.label]));
  const statusEntries = Object.entries(data.status_counts ?? {})
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  const tierEntries = Object.entries(data.tier_counts ?? {})
    .filter(([label, count]) => count > 0 && !label.includes("未补全"))
    .sort((a, b) => b[1] - a[1]);
  const tierTotal = tierEntries.reduce((sum, [, count]) => sum + count, 0);
  const processedEntries = statusEntries.filter(([code]) => code !== "pending");
  const processedTotal = processedEntries.reduce((sum, [, count]) => sum + count, 0);
  const pendingCount = metrics.pending;
  const pendingPercent = total > 0 ? (pendingCount / total) * 100 : 0;
  const processedPercent = total > 0 ? (processedTotal / total) * 100 : 0;
  const tierItems = tierEntries.map(([label, count]) => {
    const percent = tierTotal > 0 ? (count / tierTotal) * 100 : 0;
    return {
      label,
      value: count,
      percent,
      display: `${fmtNumber(count)} · ${percent.toFixed(1)}%`,
    };
  });
  const processedItems = processedEntries.map(([code, count]) => {
    const percent = processedTotal > 0 ? (count / processedTotal) * 100 : 0;
    return {
      label: statusLabels.get(code) ?? code,
      value: count,
      percent,
      display: `${fmtNumber(count)} · ${percent.toFixed(1)}%`,
    };
  });
  const recentRounds = ((data.recent_rounds ?? []) as RoundRow[]).filter(
    (item) => item.id !== round?.id,
  );
  const jobPercent =
    watchedJob && watchedJob.progress_total > 0
      ? Math.round((watchedJob.progress_done / watchedJob.progress_total) * 100)
      : null;
  const isAllRounds = selectedRoundId === null;

  const kpis = [
    { label: "达人总数", value: fmtNumber(metrics.creators), hint: undefined, icon: Users },
    { label: "已补全", value: fmtNumber(metrics.enriched), hint: `${completion}% 完整`, icon: CheckCircle2 },
    { label: "已采用", value: fmtNumber(metrics.adopted), hint: undefined, icon: UserCheck },
    { label: "线索", value: fmtNumber(leadMetrics.leads), hint: `待核实 ${fmtNumber(leadMetrics.unverified)}`, icon: ContactRound },
    { label: "总消耗", value: `${fmtNumber(metrics.cost ?? 0)} token`, hint: undefined, icon: Coins },
  ];

  return (
    <Page>
      <PageHeader
        title="工作台"
        description={
          isAllRounds
            ? `全部 ${data.counts.rounds} 个轮次 · ${fmtNumber(metrics.creators)} 位达人 · ${fmtNumber(leadMetrics.leads)} 条线索`
            : `${round?.category ? `${round?.category} · ` : ""}${keywordCount} 个关键词 · 每词 ${round?.pages ?? 1} 页 · ${fmtNumber(leadMetrics.leads)} 条线索`
        }
        actions={
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
        }
      />

      {jobActive ? (
        <div className="flex flex-wrap items-center gap-3 border bg-muted/30 px-3 py-2">
          <RoundStatusOrb status={watchedJob?.status} size={32} className="text-foreground" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">
              {watchedJob?.status_label ?? "采集中"}
              {jobPercent !== null ? ` · ${jobPercent}%` : ""}
            </p>
            <p className="truncate text-xs text-muted-foreground">{watchedJob?.stage || "任务正在后台运行"}</p>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link to={`/tasks/${watchedJob?.round_id ?? roundId}`}>查看采集任务</Link>
          </Button>
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {kpis.map((item) => (
          <Card key={item.label} size="sm">
            <CardHeader className="gap-1">
              <CardDescription>{item.label}</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{item.value}</CardTitle>
              {item.hint ? <p className="text-xs text-muted-foreground">{item.hint}</p> : null}
              <CardAction><item.icon className="size-4 text-muted-foreground" /></CardAction>
            </CardHeader>
          </Card>
        ))}
      </div>

      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>资料处理</CardTitle>
            <CardDescription>待审核与已处理达人的整体进度。</CardDescription>
            <CardAction>
              <Badge variant="secondary">已处理 {processedPercent.toFixed(1)}%</Badge>
            </CardAction>
          </CardHeader>
          <CardContent>
            <ProcessingBar
              segments={[
                {
                  key: "pending",
                  label: "待审核",
                  value: pendingCount,
                  percent: pendingPercent,
                  color: "var(--chart-pending)",
                },
                {
                  key: "processed",
                  label: "已处理",
                  value: processedTotal,
                  percent: processedPercent,
                  color: "var(--chart-2)",
                },
              ]}
            />
          </CardContent>
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>粉丝层级</CardTitle>
              <CardDescription>已补全 {fmtNumber(tierTotal)} 位达人的粉丝量级分布。</CardDescription>
            </CardHeader>
            <CardContent>
              {tierItems.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无已补全的粉丝层级数据。</p>
              ) : (
                <HorizontalBarChart items={tierItems} ariaLabel="粉丝层级分布" scaleMax={100} />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>已处理状态</CardTitle>
              <CardDescription>已处理 {fmtNumber(processedTotal)} 位达人的采用状态分布。</CardDescription>
            </CardHeader>
            <CardContent>
              {processedItems.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无已处理达人。</p>
              ) : (
                <HorizontalBarChart items={processedItems} ariaLabel="已处理状态分布" scaleMax={100} />
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>高互动达人</CardTitle>
          <CardDescription>按互动量排序，点击昵称进入达人库查看详情。</CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          <Table className="min-w-[820px] table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-12"><span className="sr-only">排名</span></TableHead>
                <TableHead className="w-[200px]">达人</TableHead>
                <TableHead className="w-[130px] text-right">粉丝数</TableHead>
                <TableHead className="w-[140px] text-right">
                  <span className="inline-flex items-center gap-1">
                    互动量
                    <ArrowDown className="size-3" aria-hidden="true" />
                  </span>
                </TableHead>
                <TableHead className="w-[120px] pl-6">状态</TableHead>
                <TableHead>方向</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data.top_creators ?? []).length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                    暂无高互动达人
                  </TableCell>
                </TableRow>
              ) : (
                (data.top_creators as CreatorRow[]).map((item, index) => (
                  <TableRow key={item.key} className="hover:bg-muted/50">
                    <TableCell className="text-muted-foreground tabular-nums">{index + 1}</TableCell>
                    <TableCell className="font-medium">
                      <Link
                        to={`/creators?creator=${encodeURIComponent(item.key)}${
                          (item.round_id ?? selectedRoundId)
                            ? `&creator_round=${item.round_id ?? selectedRoundId}`
                            : ""
                        }`}
                        className="block truncate underline-offset-4 hover:underline"
                      >
                        {item.nickname || "(未补全)"}
                      </Link>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{fmtNumber(item.fans)}</TableCell>
                    <TableCell className="text-right font-medium tabular-nums">{fmtNumber(item.interaction)}</TableCell>
                    <TableCell className="pl-6"><StatusBadge status={item.status} label={item.status_label} /></TableCell>
                    <TableCell className="truncate text-muted-foreground" title={item.match_direction}>
                      {item.match_direction || "—"}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {recentRounds.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>其他轮次</CardTitle>
            <CardDescription>查看更早的采集轮次。</CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>轮次</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">花费</TableHead>
                  <TableHead>创建时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentRounds.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="font-medium"><Link to={`/tasks/${item.id}`} className="underline-offset-4 hover:underline">{item.name}</Link></TableCell>
                    <TableCell><StatusBadge status={item.status} label={item.status_label} /></TableCell>
                    <TableCell className="text-right tabular-nums">{fmtNumber(item.cost_tokens)}</TableCell>
                    <TableCell className="text-muted-foreground">{fmtDateTime(item.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}
    </Page>
  );
}
