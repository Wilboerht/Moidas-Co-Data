import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { fmtDateTime, fmtNumber } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import type { ApiCall, Round } from "@/shared/types";
import { EmptyState, ErrorState, Page, PageHeader, Section } from "@/components/page";
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
import { StatusBadge } from "@/components/status-badge";
import { RoundStatusOrb } from "@/components/round-status-orb";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PROVIDER_LABELS: Record<string, string> = {
  dataflow: "DataFlow",
  rnote: "Rnote",
  everyinfra: "EveryInfra",
  tikhub: "TikHub",
  justoneapi: "JustOneAPI",
  socialdatax: "SocialDataX",
  redfox: "RedFoxHub",
};

export function TasksPage() {
  const { user } = useAuth();
  const { roundId, rounds, setRoundId, refreshRounds, loading } = useRound();
  const canCollect = user?.role === "admin" || Boolean(user?.can_collect);
  const queryClient = useQueryClient();
  const [forceRefresh, setForceRefresh] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [starting, setStarting] = useState(false);

  const taskQuery = useQuery({
    queryKey: ["task", roundId],
    queryFn: async () => {
      if (roundId === null) return null;
      const [round, job, calls] = await Promise.all([
        api.getRound(roundId),
        api.getJob(roundId),
        api.getCalls(roundId),
      ]);
      return { round, job, calls };
    },
    enabled: roundId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.job?.status;
      return status === "running" || status === "pending" ? 2500 : false;
    },
  });

  const logQuery = useQuery({
    queryKey: ["task-log", roundId],
    queryFn: () => (roundId === null ? null : api.getLog(roundId)),
    enabled: roundId !== null,
    refetchInterval: () => {
      const status = taskQuery.data?.job?.status;
      return status === "running" || status === "pending" ? 2500 : false;
    },
  });

  useEffect(() => {
    if (roundId === null && rounds.length > 0) {
      setRoundId(rounds[0].id);
    }
  }, [roundId, rounds, setRoundId]);

  const start = async () => {
    if (roundId === null) return;
    if (!canCollect) {
      toast.error("?????????????");
      return;
    }
    const current = taskQuery.data?.job;
    if (current && (current.status === "running" || current.status === "pending")) {
      setConfirmOpen(false);
      toast.error("任务正在运行，请等待当前任务结束");
      return;
    }
    if (taskQuery.isFetching) {
      toast.error("任务状态更新中，请稍后再试");
      return;
    }
    setStarting(true);
    try {
      await api.runRound(roundId, forceRefresh, false);
      setConfirmOpen(false);
      toast.success(forceRefresh ? "已开始强制刷新采集" : "已开始采集");
      await queryClient.invalidateQueries({ queryKey: ["task", roundId] });
      await queryClient.invalidateQueries({ queryKey: ["task-log", roundId] });
      await refreshRounds();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动失败");
    } finally {
      setStarting(false);
    }
  };

  if (roundId === null && loading) {
    return (
      <Page>
        <PageHeader title="采集任务" />
        <Skeleton className="h-40 w-full" />
      </Page>
    );
  }

  if (roundId === null) {
    return (
      <Page>
        <PageHeader title="采集任务" />
        <EmptyState
          title="还没有采集轮次"
          description="新建一轮采集后，可以在这里查看进度、日志和接口调用。"
          actions={<Button asChild><Link to="/new">新建采集</Link></Button>}
        />
      </Page>
    );
  }

  const round = taskQuery.data?.round as Round | undefined;
  const job = taskQuery.data?.job ?? null;
  const calls = taskQuery.data?.calls ?? [];
  const running = job?.status === "running" || job?.status === "pending";
  const taskBusy = running || taskQuery.isLoading || taskQuery.isFetching || !canCollect;
  const progress = job && job.progress_total > 0
    ? Math.min(100, (job.progress_done / job.progress_total) * 100)
    : job?.status === "done"
      ? 100
      : 0;

  return (
    <Page>
      <PageHeader
        title="采集任务"
        description={`${round?.name ?? `轮次 ${roundId}`} · ${round?.platform_label ?? "小红书"} / ${round?.provider_label ?? "自动"} · 每 2.5 秒自动刷新`}
        actions={
          <>
            <Button variant="outline" onClick={() => { void taskQuery.refetch(); void logQuery.refetch(); void refreshRounds(); }}>
              刷新
            </Button>
            <Button asChild><Link to="/creators">进入达人库</Link></Button>
          </>
        }
      />

      {taskQuery.error ? (
        <ErrorState message={taskQuery.error instanceof Error ? taskQuery.error.message : "加载任务失败"} />
      ) : null}
      {job?.error ? (
        <div className="border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          任务失败：{job.error}
        </div>
      ) : null}

      <div className="grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0 space-y-8">
          <Section
            title="运行状态"
            description={job ? job.stage || "任务运行信息" : undefined}
            actions={
              <StatusBadge
                status={job?.status ?? "pending"}
                label={job?.status_label ?? "未运行"}
              />
            }
          >
            {taskQuery.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : job ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="flex min-w-0 items-center gap-4">
                    <RoundStatusOrb status={job.status} size={44} className="text-foreground" />
                    <div className="min-w-0 space-y-1">
                      <p className="text-sm font-medium">任务进度</p>
                      <p className="text-xs text-muted-foreground">
                        已处理 {job.progress_done} / {job.progress_total}
                      </p>
                    </div>
                  </div>
                  <p className="text-3xl font-semibold tabular-nums">{Math.round(progress)}%</p>
                </div>
                <Progress value={progress} />
                <div className="grid gap-px border bg-border sm:grid-cols-3">
                  <div className="space-y-1 bg-background p-3">
                    <p className="text-xs text-muted-foreground">花费</p>
                    <p className="font-medium tabular-nums">{fmtNumber(job.cost)} token</p>
                  </div>
                  <div className="space-y-1 bg-background p-3">
                    <p className="text-xs text-muted-foreground">开始时间</p>
                    <p>{fmtDateTime(job.started_at)}</p>
                  </div>
                  <div className="space-y-1 bg-background p-3">
                    <p className="text-xs text-muted-foreground">结束时间</p>
                    <p>{fmtDateTime(job.finished_at)}</p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-4 py-3">
                <RoundStatusOrb status={null} size={44} className="text-muted-foreground" />
                <div className="min-w-0 space-y-1">
                  <p className="font-medium">该轮次还没有运行记录</p>
                  <p className="max-w-2xl text-sm text-muted-foreground">
                    这是导入轮次或任务尚未启动。使用「重新采集」开始运行。
                  </p>
                </div>
              </div>
            )}
          </Section>

          <Section
            title="运行日志"
            description={logQuery.data?.path || "任务开始后会生成日志"}
            actions={
              <Button variant="outline" size="sm" onClick={() => void logQuery.refetch()}>
                刷新日志
              </Button>
            }
          >
            <pre className="max-h-[420px] overflow-auto border bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap">
              {logQuery.data?.content || "暂无日志"}
            </pre>
          </Section>

          <Section title="最近接口调用" description="最近 30 次请求。" contentClassName="px-0">
              <Table className="min-w-[720px] table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[180px]">时间</TableHead>
                    <TableHead className="w-[100px]">类型</TableHead>
                    <TableHead className="w-[90px]">服务商</TableHead>
                    <TableHead>路径</TableHead>
                    <TableHead className="w-[90px] text-right">状态码</TableHead>
                    <TableHead className="w-[120px] text-right">消耗 / 原价</TableHead>
                    <TableHead className="w-[90px] text-right">耗时</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {calls.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                        暂无接口记录
                      </TableCell>
                    </TableRow>
                  ) : (
                    (calls as (ApiCall & Record<string, unknown>)[]).map((call) => (
                      <TableRow key={call.id}>
                        <TableCell className="text-muted-foreground">{fmtDateTime(call.ts)}</TableCell>
                        <TableCell>{call.kind || "—"}</TableCell>
                        <TableCell>{call.provider === "rnote" ? "Rnote" : call.provider === "dataflow" ? "DataFlow" : "—"}</TableCell>
                        <TableCell className="max-w-[260px] truncate font-mono text-xs">{call.path || "—"}</TableCell>
                        <TableCell className="text-right tabular-nums">{call.code ?? "—"}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {fmtNumber(call.cost)}
                          <span className="ml-1 text-xs text-muted-foreground">
                            / {call.cost_native} {call.cost_currency === "token" ? "token" : call.cost_currency}
                          </span>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{call.duration_ms === null ? "—" : `${call.duration_ms} ms`}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
          </Section>

          <Section title="全部轮次" description="点击轮次名称切换。" contentClassName="px-0">
              <Table className="min-w-[760px] table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead>轮次</TableHead>
                    <TableHead className="w-[120px]">垂类</TableHead>
                    <TableHead className="w-[130px]">状态</TableHead>
                    <TableHead className="w-[100px] text-right">花费</TableHead>
                    <TableHead className="w-[180px]">创建时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rounds.map((item) => (
                    <TableRow
                      key={item.id}
                      aria-current={item.id === roundId ? "true" : undefined}
                      className={item.id === roundId ? "bg-muted/50" : undefined}
                    >
                      <TableCell className="font-medium">
                        {item.id === roundId ? (
                          <span className="inline-flex items-center gap-2">
                            {item.name}
                            <span className="text-xs font-normal text-muted-foreground">当前</span>
                          </span>
                        ) : (
                          <button
                            type="button"
                            className="text-left underline-offset-4 hover:underline"
                            onClick={() => setRoundId(item.id)}
                          >
                            {item.name}
                          </button>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{item.category || "—"}</TableCell>
                      <TableCell><StatusBadge status={item.status} label={item.status_label} /></TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNumber(item.cost_tokens)}</TableCell>
                      <TableCell className="text-muted-foreground">{fmtDateTime(item.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
          </Section>
        </div>

        <aside className="h-fit space-y-6 lg:sticky lg:top-20">
          <Section title="任务操作">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="force-refresh" className="text-sm font-medium">强制刷新达人资料</Label>
                <Switch id="force-refresh" checked={forceRefresh} onCheckedChange={setForceRefresh} />
              </div>
              <Button
                variant="outline"
                className="w-full"
                disabled={taskBusy}
                onClick={() => setConfirmOpen(true)}
              >
                重新采集
              </Button>
              {running ? <p className="text-xs text-muted-foreground">任务正在运行，请等待当前任务结束。</p> : null}
            </div>
          </Section>

          <Section title="当前轮次">
            <dl className="space-y-2 text-sm">
              <div className="flex items-center justify-between gap-3">
                <dt className="shrink-0 text-muted-foreground">平台 / 服务商</dt>
                <dd className="min-w-0 truncate text-right">
                  {round?.platform_label ?? "小红书"} / {round?.provider_label ?? "自动"}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="shrink-0 text-muted-foreground">垂类</dt>
                <dd className="min-w-0 truncate text-right">{round?.category || "—"}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="shrink-0 text-muted-foreground">关键词</dt>
                <dd className="min-w-0 truncate text-right" title={round?.keywords || undefined}>
                  {round?.keywords || "—"}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">创建时间</dt>
                <dd>{fmtDateTime(round?.created_at)}</dd>
              </div>
            </dl>
          </Section>
        </aside>
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>重新运行采集？</AlertDialogTitle>
            <AlertDialogDescription>
              会再次调用接口读取数据。已缓存的内容默认不会重复扣费。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={starting}>取消</AlertDialogCancel>
            <AlertDialogAction disabled={starting} onClick={(event) => { event.preventDefault(); void start(); }}>
              {starting ? "启动中…" : "开始重新采集"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Page>
  );
}
