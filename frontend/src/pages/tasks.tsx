import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Plus, RotateCw } from "lucide-react";
import { api } from "@/shared/api";
import { fmtDateTime, fmtDuration, fmtNumber, shorten } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import type { Round } from "@/shared/types";
import { EmptyState, ErrorState, Page, PageHeader, Section } from "@/components/page";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type TaskRow = Round & { creators?: number };

function isActive(status: string | undefined | null) {
  return status === "running" || status === "pending";
}

function progressOf(item: TaskRow) {
  const job = item.job;
  if (!job) return null;
  if (job.progress_total > 0) {
    return Math.min(100, Math.round((job.progress_done / job.progress_total) * 100));
  }
  return job.status === "done" ? 100 : null;
}

export function TasksPage() {
  const { user } = useAuth();
  const { refreshRounds } = useRound();
  const canCollect = user?.role === "admin" || Boolean(user?.can_collect);
  const isAdmin = user?.role === "admin";
  const [showArchived, setShowArchived] = useState(false);
  const [params, setParams] = useSearchParams();
  const roundParam = Number(params.get("round")) || null;
  const [search, setSearch] = useState("");

  const tasksQuery = useQuery({
    queryKey: ["task-list", showArchived],
    queryFn: () => api.getRounds(isAdmin && showArchived),
    refetchInterval: (query) => {
      const active = (query.state.data ?? []).some((item) => isActive(item.job?.status));
      return active ? 2500 : false;
    },
  });

  const refresh = () => {
    void tasksQuery.refetch();
    void refreshRounds();
  };

  if (tasksQuery.isLoading) {
    return (
      <Page>
        <PageHeader title="采集轮次" />
        <Skeleton className="h-64 w-full" />
      </Page>
    );
  }

  if (tasksQuery.error) {
    return (
      <Page>
        <PageHeader title="采集轮次" />
        <ErrorState
          message={tasksQuery.error instanceof Error ? tasksQuery.error.message : "加载采集轮次失败"}
          action={
            <Button variant="outline" size="sm" onClick={refresh}>
              重试
            </Button>
          }
        />
      </Page>
    );
  }

  const tasks = (tasksQuery.data ?? []) as TaskRow[];
  const activeCount = tasks.filter((item) => isActive(item.job?.status)).length;
  const keyword = search.trim().toLowerCase();
  const filteredTasks = tasks.filter((item) => {
    if (roundParam !== null && item.id !== roundParam) return false;
    if (!keyword) return true;
    return `${item.name} ${item.keywords ?? ""}`.toLowerCase().includes(keyword);
  });

  return (
    <Page>
      <PageHeader
        title="采集轮次"
        description={
          filteredTasks.length === 0
            ? "按创建时间倒序展示所有采集轮次。"
            : `共 ${filteredTasks.length} 条采集轮次${activeCount > 0 ? ` · ${activeCount} 条进行中，每 2.5 秒自动刷新` : ""}`
        }
        actions={
          <>
            {isAdmin ? (
              <Button
                variant={showArchived ? "secondary" : "outline"}
                onClick={() => setShowArchived((value) => !value)}
              >
                {showArchived ? "隐藏已归档" : "显示已归档"}
              </Button>
            ) : null}
            <Button variant="outline" onClick={refresh} disabled={tasksQuery.isFetching}>
              <RotateCw className={tasksQuery.isFetching ? "animate-spin" : undefined} />
              刷新
            </Button>
            {canCollect ? (
              <Button asChild>
                <Link to="/new">
                  <Plus className="size-4" />
                  新建采集
                </Link>
              </Button>
            ) : null}
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索轮次名称或关键词"
          aria-label="搜索采集轮次"
          className="h-8 w-full max-w-xs"
        />
        {roundParam !== null ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete("round");
              setParams(next, { replace: true });
            }}
          >
            仅看轮次 #{roundParam} · 清除
          </Button>
        ) : null}
      </div>

      {tasks.length === 0 ? (
        <EmptyState
          title="还没有采集轮次"
          description="新建一轮采集后，每轮采集的状态、花费和结果都会记录在这里。"
          actions={
            canCollect ? (
              <Button asChild>
                <Link to="/new">新建采集</Link>
              </Button>
            ) : undefined
          }
        />
      ) : filteredTasks.length === 0 ? (
        <EmptyState
          title="当前筛选没有匹配的采集轮次"
          description="调整搜索词，或清除轮次筛选后再试。"
          actions={
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setSearch("");
                if (roundParam !== null) {
                  const next = new URLSearchParams(params);
                  next.delete("round");
                  setParams(next, { replace: true });
                }
              }}
            >
              清除筛选
            </Button>
          }
        />
      ) : (
        <Section
          title="采集轮次"
          description="点击轮次名称查看运行状态、日志和接口调用详情；花费按 token 计。"
          contentClassName="px-0"
        >
          <Table className="min-w-[950px] table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[260px]">任务</TableHead>
                <TableHead className="w-[120px]">平台 / 服务商</TableHead>
                <TableHead className="w-[100px]">状态</TableHead>
                <TableHead className="w-[150px]">进度</TableHead>
                <TableHead className="w-[80px] text-right">达人</TableHead>
                <TableHead className="w-[90px] text-right">花费</TableHead>
                <TableHead className="w-[180px]">时间</TableHead>
                <TableHead className="w-[110px] text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredTasks.map((item) => {
                const job = item.job;
                const archived = Boolean(item.archived);
                const status = archived ? "archived" : job?.status ?? item.status;
                const statusLabel = archived
                  ? "已归档"
                  : job?.status_label ?? item.status_label;
                const progress = progressOf(item);
                return (
                  <TableRow key={item.id} className="hover:bg-muted/50">
                    <TableCell className="font-medium">
                      <Link
                        to={`/tasks/${item.id}`}
                        className="block truncate underline-offset-4 hover:underline"
                      >
                        {item.name}
                      </Link>
                      <span
                        className="block truncate text-xs font-normal text-muted-foreground"
                        title={item.keywords || undefined}
                      >
                        {item.keywords ? shorten(item.keywords, 40) : "无关键词"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="block">{item.platform_label}</span>
                      <span className="block text-xs text-muted-foreground capitalize">{item.provider_label}</span>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={status} label={statusLabel} />
                    </TableCell>
                    <TableCell>
                      {progress === null || !job ? (
                        <span className="text-sm text-muted-foreground">—</span>
                      ) : (
                        <div className="space-y-1.5">
                          <div className="flex items-center gap-1.5 text-xs tabular-nums">
                            <span className="text-muted-foreground">
                              {job.progress_done} / {job.progress_total}
                            </span>
                            <span className="text-muted-foreground/60">·</span>
                            <span>{progress}%</span>
                          </div>
                          {job.status === "done" ? null : <Progress value={progress} className="h-1" />}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className={`text-right tabular-nums ${item.creators ? "" : "text-muted-foreground"}`}>
                      {fmtNumber(item.creators)}
                    </TableCell>
                    <TableCell className={`text-right tabular-nums ${item.cost_tokens ? "" : "text-muted-foreground"}`}>
                      {fmtNumber(item.cost_tokens)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      <span className="block text-xs">开始 {fmtDateTime(item.started_at ?? item.created_at)}</span>
                      {item.finished_at ? (
                        <span
                          className="block text-xs"
                          title={`结束 ${fmtDateTime(item.finished_at)}`}
                        >
                          用时 {fmtDuration(item.started_at ?? item.created_at, item.finished_at)}
                        </span>
                      ) : isActive(status) ? (
                        <span className="block text-xs">
                          已运行 {fmtDuration(item.started_at ?? item.created_at, null)}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button asChild variant="ghost" size="sm">
                        <Link to={`/tasks/${item.id}`} aria-label={`查看 ${item.name} 详情`}>
                          查看
                          <ChevronRight className="size-4" />
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Section>
      )}
    </Page>
  );
}
