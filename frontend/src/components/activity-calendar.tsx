import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { api } from "@/shared/api";
import { fmtNumber } from "@/shared/format";
import type { ActivityCalendarDay } from "@/shared/types";
import { ErrorState } from "@/components/page";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "cn";

type Metric = "collected" | "processed";

const METRIC_LABELS: Record<Metric, string> = {
  collected: "新增达人",
  processed: "审核动作",
};

const COLORS: Record<Metric, string[]> = {
  collected: [
    "bg-muted",
    "bg-emerald-200",
    "bg-emerald-300",
    "bg-emerald-500",
    "bg-emerald-700",
  ],
  processed: [
    "bg-muted",
    "bg-sky-200",
    "bg-sky-300",
    "bg-sky-500",
    "bg-sky-700",
  ],
};

const WEEKDAYS = ["一", "", "三", "", "五", "", "日"];
const CELL = 14;
const GAP = 3;
const WEEKDAY_WIDTH = 18;
const WEEKDAY_GAP = 8;

type Cell = {
  date: string;
  collected: number;
  processed: number;
  inRange: boolean;
  level: number;
};

type Week = {
  month?: string;
  days: Cell[];
};

function parseDay(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, (month || 1) - 1, day || 1);
}

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

function addDays(date: Date, amount: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + amount);
  return next;
}

function startOfWeek(date: Date): Date {
  const next = new Date(date);
  next.setDate(next.getDate() - ((next.getDay() + 6) % 7));
  return next;
}

function formatDay(value: string): string {
  const date = parseDay(value);
  const weekday = ["日", "一", "二", "三", "四", "五", "六"][date.getDay()];
  return `${date.getMonth() + 1}/${date.getDate()} 周${weekday}`;
}

function levelOf(value: number, thresholds: number[]): number {
  if (value <= 0) return 0;
  if (value <= thresholds[0]) return 1;
  if (value <= thresholds[1]) return 2;
  if (value <= thresholds[2]) return 3;
  return 4;
}

function thresholdsFor(values: number[]): number[] {
  if (values.length === 0) return [1, 2, 3, 4];
  const sorted = [...values].sort((a, b) => a - b);
  const at = (percent: number) =>
    sorted[Math.min(sorted.length - 1, Math.floor(percent * sorted.length))];
  return [at(0.25), at(0.5), at(0.75), at(0.9)];
}

function buildWeeks(items: ActivityCalendarDay[], startKey: string, endKey: string): Week[] {
  const start = parseDay(startKey);
  const end = parseDay(endKey);
  const gridStart = startOfWeek(start);
  const gridEnd = addDays(startOfWeek(end), 6);
  const byDate = new Map(items.map((item) => [item.date, item]));
  const weeks: Week[] = [];
  let cursor = gridStart;
  while (cursor <= gridEnd) {
    const days: Cell[] = [];
    for (let index = 0; index < 7; index += 1) {
      const date = addDays(cursor, index);
      const key = dayKey(date);
      const inRange = date >= start && date <= end;
      const item = byDate.get(key);
      days.push({
        date: key,
        collected: item?.collected ?? 0,
        processed: item?.processed ?? 0,
        inRange,
        level: 0,
      });
    }
    weeks.push({ days });
    cursor = addDays(cursor, 7);
  }
  const monthRanges = new Map<string, { first: number; last: number }>();
  weeks.forEach((week, index) => {
    const first = week.days.find((day) => day.inRange);
    if (!first) return;
    const date = parseDay(first.date);
    const key = `${date.getFullYear()}-${date.getMonth()}`;
    const existing = monthRanges.get(key);
    if (existing) {
      existing.last = index;
    } else {
      monthRanges.set(key, { first: index, last: index });
    }
  });
  for (const [key, range] of monthRanges) {
    if (range.last - range.first < 1) continue;
    const [, month] = key.split("-").map(Number);
    weeks[range.first].month = `${month + 1}月`;
  }
  return weeks;
}

export function ActivityCalendar() {
  const [metric, setMetric] = useState<Metric>("collected");
  const tzOffset = -new Date().getTimezoneOffset();
  const query = useQuery({
    queryKey: ["activity", tzOffset],
    queryFn: () => api.getActivityCalendar(365, tzOffset),
    refetchInterval: 60_000,
  });
  const data = query.data;
  const items = data?.items ?? [];
  const values = useMemo(
    () => items.map((item) => item[metric]).filter((value) => value > 0),
    [items, metric],
  );
  const thresholds = useMemo(() => thresholdsFor(values), [values]);
  const weeks = useMemo(
    () => (data ? buildWeeks(items, data.start, data.end) : []),
    [data, items],
  );
  const cells = useMemo(
    () =>
      weeks.flatMap((week) =>
        week.days.map((day) => ({
          ...day,
          level: day.inRange ? levelOf(day[metric], thresholds) : -1,
        })),
      ),
    [weeks, metric, thresholds],
  );
  const total = data?.totals[metric] ?? 0;
  const activeDays = values.length;
  const maxDay = values.reduce((max, value) => Math.max(max, value), 0);
  const recentDays = useMemo(
    () =>
      items
        .filter((item) => item[metric] > 0)
        .sort((a, b) => b.date.localeCompare(a.date))
        .slice(0, 4),
    [items, metric],
  );
  const gridWidth = weeks.length > 0 ? weeks.length * (CELL + GAP) - GAP : 1;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <CardTitle>采集与处理日历</CardTitle>
          <div
            className="inline-flex items-center rounded-md border bg-muted/30 p-0.5"
            role="group"
            aria-label="切换日历指标"
          >
            {(["collected", "processed"] as Metric[]).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={metric === value}
                onClick={() => setMetric(value)}
                className={cn(
                  "rounded px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
                  metric === value
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {METRIC_LABELS[value]}
              </button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {query.error ? (
          <div className="space-y-2">
            <ErrorState
              message={
                query.error instanceof Error ? query.error.message : "加载活跃日历失败"
              }
            />
            <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
              <RotateCw className={query.isFetching ? "animate-spin" : undefined} />
              重试
            </Button>
          </div>
        ) : query.isLoading || !data ? (
          <Skeleton className="h-44 w-full" />
        ) : (
          <div className="space-y-3">
            <div className="flex flex-col gap-5 xl:flex-row xl:items-stretch">
              <div className="flex min-w-0 flex-1 flex-col gap-3">
                <div
                  className="flex flex-wrap items-center justify-between gap-2"
                  style={{
                    maxWidth: `${gridWidth + WEEKDAY_WIDTH + WEEKDAY_GAP}px`,
                  }}
                >
                  <CardDescription>
                    全部轮次近一年每日新增达人与审核动作；颜色越深表示当天越多。
                  </CardDescription>
                  <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    <span>少</span>
                    {COLORS[metric].map((color) => (
                      <span
                        key={color}
                        className={cn("size-3 rounded-[3px]", color)}
                        aria-hidden="true"
                      />
                    ))}
                    <span>多</span>
                  </div>
                </div>
                <div
                  className="flex min-w-0 overflow-x-auto"
                  style={{ gap: `${WEEKDAY_GAP}px` }}
                >
              <div
                className="mt-5 grid shrink-0 grid-rows-7 text-[10px] leading-none text-muted-foreground"
                style={{ width: `${WEEKDAY_WIDTH}px`, gap: `${GAP}px` }}
                aria-hidden="true"
              >
                {WEEKDAYS.map((label, index) => (
                  <span
                    key={index}
                    className="flex items-center justify-end pr-1"
                    style={{ height: `${CELL}px` }}
                  >
                    {label}
                  </span>
                ))}
              </div>
              <div className="shrink-0">
                <div
                  className="relative mb-1 h-4"
                  style={{ width: `${Math.max(gridWidth, 1)}px` }}
                  aria-hidden="true"
                >
                  {weeks.map((week, index) =>
                    week.month ? (
                      <span
                        key={`${week.month}-${index}`}
                        className="absolute top-0 text-[10px] leading-none text-muted-foreground"
                        style={{ left: `${index * (CELL + GAP)}px` }}
                      >
                        {week.month}
                      </span>
                    ) : null,
                  )}
                </div>
                <div
                  className="grid grid-flow-col grid-rows-7"
                  style={{ gap: `${GAP}px` }}
                  role="img"
                  aria-label={`近一年${METRIC_LABELS[metric]}日历，共 ${fmtNumber(
                    total,
                  )}，活跃 ${fmtNumber(activeDays)} 天，单日最高 ${fmtNumber(maxDay)}`}
                >
                  {cells.map((cell) => (
                    <div
                      key={cell.date}
                      title={
                        cell.inRange
                          ? `${cell.date} · 新增达人 ${fmtNumber(cell.collected)} · 审核动作 ${fmtNumber(cell.processed)}`
                          : undefined
                      }
                      aria-label={
                        cell.inRange
                          ? `${cell.date}，新增达人 ${fmtNumber(cell.collected)}，审核动作 ${fmtNumber(cell.processed)}`
                          : undefined
                      }
                      className={cn(
                        "rounded-[3px]",
                        !cell.inRange
                          ? "bg-transparent"
                          : COLORS[metric][cell.level],
                      )}
                      style={{ width: `${CELL}px`, height: `${CELL}px` }}
                    />
                  ))}
                </div>
              </div>
            </div>
              </div>
            <aside className="flex shrink-0 flex-col self-stretch rounded-lg border bg-muted/20 p-3 xl:w-[260px]">
              <div className="border-b pb-1.5">
                <p className="text-xs font-semibold text-foreground">最近活跃日期</p>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  按{METRIC_LABELS[metric]}统计
                </p>
              </div>
              {recentDays.length ? (
                <ul className="mt-1">
                  {recentDays.map((day) => {
                    const level = levelOf(day[metric], thresholds);
                    return (
                      <li
                        key={day.date}
                        className="flex items-center justify-between gap-3 py-1 text-xs"
                      >
                        <span className="flex min-w-0 items-center gap-2 text-muted-foreground">
                          <span
                            className={cn(
                              "size-2 shrink-0 rounded-[2px]",
                              COLORS[metric][level],
                            )}
                            aria-hidden="true"
                          />
                          <span className="truncate">{formatDay(day.date)}</span>
                        </span>
                        <span className="shrink-0 font-medium tabular-nums text-foreground">
                          {fmtNumber(day[metric])}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="mt-3 text-xs text-muted-foreground">暂无活跃记录</p>
              )}
            </aside>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
