import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowLeft, Info, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { fmtDateTime, fmtDuration, fmtNumber } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import type { ApiCall, Round, RoundImpact } from "@/shared/types";
import { ErrorState, Page, PageHeader, Section } from "@/components/page";
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
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatusBadge } from "@/components/status-badge";
import { RoundStatusOrb } from "@/components/round-status-orb";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function InfoHint({ children }: { children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label="查看说明"
          className="inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <Info className="size-3" aria-hidden="true" />
        </button>
      </TooltipTrigger>
      <TooltipContent side="left" className="max-w-xs">
        {children}
      </TooltipContent>
    </Tooltip>
  );
}

const PROVIDER_LABELS: Record<string, string> = {
  dataflow: "DataFlow",
  rnote: "Rnote",
  everyinfra: "EveryInfra",
  tikhub: "TikHub",
  justoneapi: "JustOneAPI",
  socialdatax: "SocialDataX",
  redfox: "RedFoxHub",
};

function BackLink() {
  return (
    <Link
      to="/tasks"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
    >
      <ArrowLeft className="size-4" aria-hidden="true" />
      返回采集记录
    </Link>
  );
}

export function TaskDetailPage() {
  const params = useParams();
  const parsedId = Number(params.roundId);
  const roundId = Number.isInteger(parsedId) && parsedId > 0 ? parsedId : null;
  const { user } = useAuth();
  const { refreshRounds, settings } = useRound();
  const navigate = useNavigate();
  const isAdmin = user?.role === "admin";
  const canCollect = isAdmin || Boolean(user?.can_collect);
  const queryClient = useQueryClient();
  const [forceRefresh, setForceRefresh] = useState(false);
  const [forceRun, setForceRun] = useState(false);
  const [providerOverride, setProviderOverride] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [impact, setImpact] = useState<RoundImpact | null>(null);
  const [confirmName, setConfirmName] = useState("");
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    setForceRefresh(false);
    setForceRun(false);
    setProviderOverride("");
    setConfirmOpen(false);
    setCancelOpen(false);
    setDeleteOpen(false);
    setImpact(null);
    setConfirmName("");
  }, [roundId]);

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

  const start = async () => {
    if (roundId === null) return;
    if (!canCollect) {
      toast.error("当前账号没有发起采集的权限，请联系管理员开通。");
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
      const roundPlatform = taskQuery.data?.round.platform ?? "xhs";
      const providerOptions =
        settings?.platforms.find((item) => item.code === roundPlatform)?.providers ?? [];
      const providerValue =
        providerOverride || taskQuery.data?.round.provider || "auto";
      const provider =
        providerValue === "auto" || providerOptions.some((item) => item.id === providerValue)
          ? providerValue
          : undefined;
      await api.runRound(roundId, forceRefresh, forceRun, provider);
      setConfirmOpen(false);
      toast.success(forceRefresh ? "已开始强制刷新采集" : "已开始采集");
      await queryClient.invalidateQueries({ queryKey: ["task", roundId] });
      await queryClient.invalidateQueries({ queryKey: ["task-log", roundId] });
      await queryClient.invalidateQueries({ queryKey: ["task-list"] });
      await refreshRounds();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动失败");
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (roundId === null || !job) return;
    setCancelling(true);
    try {
      const cancelled = await api.cancelJob(job.id);
      setCancelOpen(false);
      if (cancelled?.process_terminated === false) {
        toast.warning("任务状态已取消，但进程退出尚未确认，请稍后刷新检查");
      } else {
        toast.success("任务已取消");
      }
      await queryClient.invalidateQueries({ queryKey: ["task", roundId] });
      await queryClient.invalidateQueries({ queryKey: ["task-log", roundId] });
      await queryClient.invalidateQueries({ queryKey: ["task-list"] });
      await refreshRounds();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "取消失败");
    } finally {
      setCancelling(false);
    }
  };

  const setArchived = async (archived: boolean) => {
    if (roundId === null) return;
    setArchiveBusy(true);
    try {
      if (archived) await api.archiveRound(roundId);
      else await api.restoreRound(roundId);
      toast.success(archived ? "已归档轮次" : "已恢复轮次");
      await queryClient.invalidateQueries({ queryKey: ["task", roundId] });
      await queryClient.invalidateQueries({ queryKey: ["task-list"] });
      await refreshRounds();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "操作失败");
    } finally {
      setArchiveBusy(false);
    }
  };

  const openDelete = async () => {
    if (roundId === null) return;
    try {
      const nextImpact = await api.getRoundImpact(roundId);
      setImpact(nextImpact);
      setConfirmName("");
      setDeleteOpen(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载影响范围失败");
    }
  };

  const purge = async () => {
    if (roundId === null || !impact) return;
    if (confirmName.trim() !== impact.name) {
      toast.error("请输入完整的轮次名称");
      return;
    }
    setDeleting(true);
    try {
      await api.deleteRound(roundId, confirmName.trim());
      setDeleteOpen(false);
      toast.success("轮次已永久删除");
      await refreshRounds();
      navigate("/tasks");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  };

  if (roundId === null) {
    return (
      <Page>
        <BackLink />
        <PageHeader title="任务详情" />
        <ErrorState message="任务不存在，请从采集记录中重新选择。" />
      </Page>
    );
  }

  if (taskQuery.error) {
    return (
      <Page>
        <BackLink />
        <PageHeader
          title={`任务 #${roundId}`}
          actions={
            <Button variant="outline" onClick={() => void taskQuery.refetch()}>重试</Button>
          }
        />
        <ErrorState message={taskQuery.error instanceof Error ? taskQuery.error.message : "加载任务失败"} />
      </Page>
    );
  }

  const round = taskQuery.data?.round as Round | undefined;
  const job = taskQuery.data?.job ?? null;
  const calls = taskQuery.data?.calls ?? [];
  const roundPlatform = round?.platform ?? "xhs";
  const providerOptions =
    settings?.platforms.find((item) => item.code === roundPlatform)?.providers ?? [];
  const providerValue = providerOverride || round?.provider || "auto";
  const rerunSearchCalls = (round?.keyword_details?.length ?? 0) * (round?.pages ?? 1);
  const rerunUserCalls = round?.max_creators ?? rerunSearchCalls * 10;
  const estimatedRerunTokens = (() => {
    const candidates = providerOptions.filter((item) => item.ready && item.enabled);
    const selected = providerValue === "auto"
      ? candidates.reduce<(typeof providerOptions)[number] | undefined>((best, item) => {
          const total = rerunSearchCalls * item.search_cost_tokens + rerunUserCalls * item.user_cost_tokens;
          const bestTotal = best
            ? rerunSearchCalls * best.search_cost_tokens + rerunUserCalls * best.user_cost_tokens
            : Number.POSITIVE_INFINITY;
          return total < bestTotal ? item : best;
        }, undefined)
      : candidates.find((item) => item.id === providerValue);
    return selected
      ? rerunSearchCalls * selected.search_cost_tokens + rerunUserCalls * selected.user_cost_tokens
      : 0;
  })();
  const canCancel = isAdmin || (job?.created_by != null && job.created_by === user?.id);
  const running = job?.status === "running" || job?.status === "pending";
  const taskBusy = running || taskQuery.isLoading || taskQuery.isFetching || !canCollect;
  const progress = job && job.progress_total > 0
    ? Math.min(100, (job.progress_done / job.progress_total) * 100)
    : job?.status === "done"
      ? 100
      : 0;
  const durationText = job ? fmtDuration(job.started_at, job.finished_at) : "—";
  const heroValue =
    job && job.status !== "done" && job.progress_total > 0 ? `${Math.round(progress)}%` : null;
  const summary = round?.summary;

  return (
    <Page>
      <BackLink />

      <PageHeader
        title={round?.name ?? `轮次 ${roundId}`}
        description={`${round?.platform_label ?? "小红书"} / ${round?.provider_label ?? "自动"} · 创建于 ${fmtDateTime(round?.created_at)}${
          round?.archived ? " · 已归档" : ""
        }${running ? " · 运行中每 2.5 秒自动刷新" : ""}`}
        actions={
          <>
            <Button variant="outline" onClick={() => { void taskQuery.refetch(); void logQuery.refetch(); }}>
              刷新
            </Button>
            <Button asChild>
              <Link to={`/creators?round=${roundId}`}>进入达人库</Link>
            </Button>
          </>
        }
      />

      {job?.error && job.status !== "cancelled" ? (
        <div className="border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          任务失败：{job.error}
        </div>
      ) : null}
      {job?.status === "cancelled" ? (
        <div className="border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          任务已取消，已产生的数据和费用会保留。
        </div>
      ) : null}

      <div className="grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-8">
          <Section
            title="运行状态"
            description={
              job
                ? `${
                    job.status === "running" || job.status === "pending"
                      ? job.stage || "任务运行信息"
                      : "最近一次运行"
                  } · 任务 #${job.id}`
                : undefined
            }
            actions={
              <StatusBadge
                status={job?.status ?? round?.status ?? "pending"}
                label={job?.status_label ?? round?.status_label ?? "未运行"}
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
                        已处理 {job.progress_done} / {job.progress_total} 项
                      </p>
                    </div>
                  </div>
                  {heroValue ? (
                    <div className="text-right">
                      <p className="text-xs text-muted-foreground">进度</p>
                      <p className="text-3xl font-semibold tabular-nums">{heroValue}</p>
                    </div>
                  ) : null}
                </div>
                {job.status !== "done" && job.progress_total > 0 ? (
                  <Progress
                    value={progress}
                    className={
                      job.status === "failed"
                        ? "[&>[data-slot=progress-indicator]]:bg-destructive"
                        : undefined
                    }
                  />
                ) : null}
                <div className="grid grid-cols-2 gap-px border bg-border 2xl:grid-cols-4">
                  <div className="space-y-1 bg-background p-3">
                    <p className="text-xs text-muted-foreground">花费</p>
                    <p className="font-medium tabular-nums">{fmtNumber(job.cost)} token</p>
                  </div>
                  <div className="space-y-1 bg-background p-3">
                    <p className="text-xs text-muted-foreground">{job.finished_at || !job.started_at ? "耗时" : "已运行"}</p>
                    <p className="font-medium tabular-nums">{durationText}</p>
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
                    这是导入轮次或任务尚未启动。使用右侧的「开始采集」运行。
                  </p>
                </div>
              </div>
            )}
          </Section>

          <Section
            title="运行日志"
            description={
              logQuery.data?.path && isAdmin
                ? `最近一次运行 · ${logQuery.data.path}`
                : "任务开始后生成日志"
            }
            actions={
              <Button variant="outline" size="sm" onClick={() => void logQuery.refetch()}>
                刷新日志
              </Button>
            }
          >
            <pre
              aria-live="polite"
              className="max-h-[420px] overflow-auto border bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap"
            >
              {logQuery.isLoading
                ? "加载中…"
                : logQuery.error
                  ? `日志加载失败：${logQuery.error instanceof Error ? logQuery.error.message : "未知错误"}`
                  : logQuery.data?.content || "暂无日志"}
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
                      <TableCell>{call.provider ? PROVIDER_LABELS[call.provider] ?? call.provider : "—"}</TableCell>
                      <TableCell className="max-w-[260px] font-mono text-xs">
                        <div className="truncate" title={call.path || undefined}>{call.path || "—"}</div>
                        {call.error ? <p className="mt-1 whitespace-normal text-destructive">{call.error}</p> : null}
                      </TableCell>
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
        </div>

        <aside className="h-fit space-y-6 lg:sticky lg:top-20">
          <Section title="任务操作">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div className="flex min-w-0 items-center gap-1">
                  <Label htmlFor="force-refresh" className="text-sm font-medium">强制刷新达人资料</Label>
                  <InfoHint>
                    忽略已缓存的达人资料，重新请求服务商补全本轮所有达人；会增加接口调用次数和费用。
                  </InfoHint>
                </div>
                <Switch id="force-refresh" checked={forceRefresh} onCheckedChange={setForceRefresh} />
              </div>
              <div className="flex items-center justify-between gap-4">
                <div className="flex min-w-0 items-center gap-1">
                  <Label htmlFor="force-run" className="text-sm font-medium">跳过余额检查</Label>
                  <InfoHint>
                    即使服务商余额不足或余额查询失败，也强制开始采集；可能产生超出预算的费用。
                  </InfoHint>
                </div>
                <Switch id="force-run" checked={forceRun} onCheckedChange={setForceRun} />
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-1">
                  <Label htmlFor="run-provider">本次服务商</Label>
                  <InfoHint>
                    仅影响本次重新采集；自动模式会按当前配置选择价格更优的可用服务商。
                  </InfoHint>
                </div>
                <Select value={providerValue} onValueChange={setProviderOverride}>
                  <SelectTrigger id="run-provider" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">自动（最便宜可用）</SelectItem>
                    {providerOptions.map((option) => (
                      <SelectItem
                        key={option.id}
                        value={option.id}
                        disabled={!option.enabled || !option.ready}
                      >
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="block w-full">
                    <Button
                      variant="outline"
                      className="w-full"
                      disabled={taskBusy || Boolean(round?.archived)}
                      onClick={() => setConfirmOpen(true)}
                    >
                      {job ? "重新采集" : "开始采集"}
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-xs">
                  使用当前关键词、页数和上限发起采集；已缓存达人资料默认不重复补全。
                </TooltipContent>
              </Tooltip>
              {running ? (
                <Button
                  variant="destructive"
                  className="w-full"
                  disabled={!canCancel || cancelling}
                  onClick={() => setCancelOpen(true)}
                >
                  {cancelling ? "取消中…" : "取消任务"}
                </Button>
              ) : null}
              {running ? <p className="text-xs text-muted-foreground">任务正在运行，请等待当前任务结束。</p> : null}
              {round?.archived ? <p className="text-xs text-muted-foreground">轮次已归档，恢复后才能重新采集。</p> : null}
              {!canCollect ? <p className="text-xs text-muted-foreground">当前账号没有发起采集的权限。</p> : null}
            </div>
          </Section>

          {isAdmin ? (
            <Section title="轮次管理">
              <div className="space-y-3">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="block w-full">
                      <Button
                        variant="outline"
                        className="w-full"
                        disabled={archiveBusy || running}
                        onClick={() => void setArchived(!round?.archived)}
                      >
                        {round?.archived ? <RotateCcw /> : <Archive />}
                        {round?.archived ? "恢复轮次" : "归档轮次"}
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="left" className="max-w-xs">
                    {round?.archived
                      ? "恢复后轮次会重新出现在默认列表中，数据和线索关联保留。"
                      : "归档后从默认列表隐藏，保留全部数据和线索关联，可随时恢复。"}
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="block w-full">
                      <Button
                        variant="ghost"
                        className="w-full text-destructive hover:bg-destructive/10 hover:text-destructive"
                        disabled={running}
                        onClick={() => void openDelete()}
                      >
                        <Trash2 />
                        永久删除
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="left" className="max-w-xs">
                    彻底删除轮次及关联的达人、笔记、任务和接口记录，需要输入轮次名称确认并自动备份数据库；有关联线索时不能永久删除。
                  </TooltipContent>
                </Tooltip>
                <p className="text-xs text-muted-foreground">
                  归档保留全部数据和线索关联；关联线索的轮次不能永久删除。
                </p>
              </div>
            </Section>
          ) : null}

          <Section title="采集配置">
            <dl className="space-y-3 text-sm">
              <div className="space-y-1.5">
                <dt className="text-muted-foreground">关键词</dt>
                <dd>
                  {round?.keyword_details?.length ? (
                    <span className="flex flex-wrap gap-1">
                      {round.keyword_details.map((keyword) => (
                        <Badge key={keyword.word} variant="secondary" title={keyword.category ?? undefined}>
                          {keyword.word}
                        </Badge>
                      ))}
                    </span>
                  ) : (
                    <span>{round?.keywords || "—"}</span>
                  )}
                </dd>
              </div>
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
                <dt className="shrink-0 text-muted-foreground">每词页数</dt>
                <dd className="tabular-nums">{round?.pages ?? "—"}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="shrink-0 text-muted-foreground">候选达人</dt>
                <dd className="tabular-nums">{fmtNumber(summary?.creators)}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="shrink-0 text-muted-foreground">关联线索</dt>
                <dd className="tabular-nums">{fmtNumber(summary?.leads)}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="shrink-0 text-muted-foreground">笔记数量</dt>
                <dd className="tabular-nums">{fmtNumber(summary?.notes)}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="shrink-0 text-muted-foreground">创建时间</dt>
                <dd>{fmtDateTime(round?.created_at)}</dd>
              </div>
            </dl>
          </Section>
        </aside>
      </div>

      <AlertDialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>取消当前任务？</AlertDialogTitle>
            <AlertDialogDescription>
              将终止当前采集进程，已产生的数据和费用会保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={cancelling}>继续运行</AlertDialogCancel>
            <AlertDialogAction
              disabled={cancelling}
              onClick={(event) => { event.preventDefault(); void cancel(); }}
            >
              {cancelling ? "取消中…" : "确认取消"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{job ? "重新运行采集？" : "开始采集？"}</AlertDialogTitle>
            <AlertDialogDescription>
              会再次调用接口读取数据。已缓存的内容默认不会重复扣费；按当前配置最多约 {fmtNumber(estimatedRerunTokens)} token。余额检查是预检，不是并发任务间的硬限额。
              {forceRun ? " 本次已选择跳过余额检查。" : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={starting}>取消</AlertDialogCancel>
            <AlertDialogAction disabled={starting} onClick={(event) => { event.preventDefault(); void start(); }}>
              {starting ? "启动中…" : job ? "开始重新采集" : "开始采集"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>永久删除轮次？</DialogTitle>
            <DialogDescription>
              此操作会删除轮次、达人-轮次关系、笔记关联、任务和接口记录，并清理孤立数据。删除前会自动备份数据库。
            </DialogDescription>
          </DialogHeader>
          {impact ? (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2 border p-3">
                <span>达人-轮次：{fmtNumber(impact.creators)}</span>
                <span>已审核：{fmtNumber(impact.creators_reviewed)}</span>
                <span>笔记：{fmtNumber(impact.notes)}</span>
                <span>关联线索：{fmtNumber(impact.leads)}</span>
                <span>已转线索：{fmtNumber(impact.leads_converted)}</span>
                <span>接口调用：{fmtNumber(impact.api_calls)}</span>
                <span>原始文件：{fmtNumber(impact.raw_files)}</span>
                <span>日志文件：{fmtNumber(impact.log_files)}</span>
              </div>
              {!impact.can_purge ? (
                <p className="border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {impact.jobs_active > 0
                    ? "该轮次仍有运行中的任务，请先取消。"
                    : "该轮次仍关联线索，不能永久删除；请使用归档。"}
                </p>
              ) : (
                <>
                  <p className="text-muted-foreground">
                    请输入轮次名称 <strong>{impact.name}</strong> 以确认：
                  </p>
                  <Input
                    value={confirmName}
                    onChange={(event) => setConfirmName(event.target.value)}
                    placeholder={impact.name}
                    autoComplete="off"
                  />
                </>
              )}
            </div>
          ) : (
            <Skeleton className="h-24 w-full" />
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)} disabled={deleting}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={() => void purge()}
              disabled={
                deleting ||
                !impact?.can_purge ||
                confirmName.trim() !== impact?.name
              }
            >
              {deleting ? "删除中…" : "确认永久删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Page>
  );
}
