import { Fragment, useId, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { api } from "@/shared/api";
import { fmtNumber } from "@/shared/format";
import type { ActivityCalendarDay } from "@/shared/types";
import { ErrorState } from "@/components/page";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
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

const CUMULATIVE_LABELS: Record<Metric, string> = {
  collected: "累计新增",
  processed: "累计审核",
};

const ACCENTS: Record<Metric, { line: string; dot: string }> = {
  collected: { line: "text-emerald-500", dot: "bg-emerald-500" },
  processed: { line: "text-sky-500", dot: "bg-sky-500" },
};

const WEEKDAYS = ["一", "", "三", "", "五", "", "日"];
const CELL = 10;
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

type TrendWeek = {
  start: string;
  end: string;
  value: number;
};

const TREND_HEIGHT = 80;

function smoothLine(points: { x: number; y: number }[]): string {
  if (points.length === 0) return "";
  if (points.length === 1) {
    return `M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`;
  }
  const slopes: number[] = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const dx = points[index + 1].x - points[index].x;
    slopes.push(dx === 0 ? 0 : (points[index + 1].y - points[index].y) / dx);
  }
  const tangents = new Array<number>(points.length).fill(0);
  tangents[0] = slopes[0];
  tangents[points.length - 1] = slopes[slopes.length - 1];
  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = slopes[index - 1];
    const next = slopes[index];
    if (previous * next <= 0) {
      tangents[index] = 0;
      continue;
    }
    const average = (previous + next) / 2;
    const limit = 3 * Math.min(Math.abs(previous), Math.abs(next));
    tangents[index] = Math.sign(average) * Math.min(Math.abs(average), limit);
  }
  let path = `M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const dx = (points[index + 1].x - points[index].x) / 3;
    const firstX = points[index].x + dx;
    const firstY = points[index].y + tangents[index] * dx;
    const secondX = points[index + 1].x - dx;
    const secondY = points[index + 1].y - tangents[index + 1] * dx;
    path += `C${firstX.toFixed(2)},${firstY.toFixed(2)} ${secondX.toFixed(
      2,
    )},${secondY.toFixed(2)} ${points[index + 1].x.toFixed(2)},${points[
      index + 1
    ].y.toFixed(2)}`;
  }
  return path;
}

function WeekTrend({ weeks, metric }: { weeks: TrendWeek[]; metric: Metric }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const gradientId = `trend-fill-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const activeWeeks = weeks.filter((week) => week.value > 0).length;
  const maxWeek = Math.max(1, ...weeks.map((week) => week.value));
  const empty = activeWeeks === 0;
  const accent = ACCENTS[metric];
  const points = weeks.map((week, index) => ({
    ...week,
    index,
    x: ((index + 0.5) / Math.max(weeks.length, 1)) * 100,
    y: TREND_HEIGHT - 1 - (week.value / maxWeek) * (TREND_HEIGHT - 4),
  }));
  const line = smoothLine(points);
  const area =
    points.length > 0
      ? `${line} L${points[points.length - 1].x.toFixed(2)},${TREND_HEIGHT} L${points[0].x.toFixed(2)},${TREND_HEIGHT} Z`
      : "";
  const active = hovered !== null ? points[hovered] : null;

  return (
    <>
      <div className="flex items-center justify-between gap-2 text-[11px] leading-none text-muted-foreground">
        <span className="truncate">
          {active ? `${active.start.slice(5)} ~ ${active.end.slice(5)}` : "近 12 周"}
        </span>
        <span className="truncate">
          {active
            ? `${fmtNumber(active.value)} 次`
            : `活跃 ${fmtNumber(activeWeeks)} 周`}
        </span>
      </div>
      <div className="relative min-h-0 flex-1" onMouseLeave={() => setHovered(null)}>
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-border/60"
          aria-hidden="true"
        />
        <svg
          viewBox={`0 0 100 ${TREND_HEIGHT}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          className={cn("h-full w-full", accent.line, empty && "opacity-20")}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="currentColor" stopOpacity={0.2} />
              <stop offset="100%" stopColor="currentColor" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          {area ? <path d={area} fill={`url(#${gradientId})`} /> : null}
          {line ? (
            <path
              d={line}
              fill="none"
              stroke="currentColor"
              strokeOpacity={0.9}
              strokeWidth={1.25}
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
        </svg>
        {empty ? (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center text-[11px] text-muted-foreground">
            暂无{METRIC_LABELS[metric]}
          </div>
        ) : null}
        <div className="absolute inset-0 flex">
          {points.map((point) => (
            <button
              key={point.start}
              type="button"
              onMouseEnter={() => setHovered(point.index)}
              onFocus={() => setHovered(point.index)}
              onBlur={() => setHovered(null)}
              aria-label={`${point.start} ~ ${point.end}，${METRIC_LABELS[metric]} ${fmtNumber(
                point.value,
              )}`}
              className="min-w-0 flex-1 rounded-sm outline-none focus-visible:bg-muted/40 focus-visible:ring-1 focus-visible:ring-ring/50"
            />
          ))}
        </div>
        {active ? (
          <div
            className={cn(
              "pointer-events-none absolute size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full",
              accent.dot,
            )}
            style={{ left: `${active.x}%`, top: `${(active.y / TREND_HEIGHT) * 100}%` }}
          />
        ) : null}
      </div>
    </>
  );
}

export function ActivityCalendar({
  roundId = null,
  hideRoundScope = false,
}: {
  roundId?: number | null;
  hideRoundScope?: boolean;
}) {
  const [metric, setMetric] = useState<Metric>("collected");
  const tzOffset = -new Date().getTimezoneOffset();
  const query = useQuery({
    queryKey: ["activity", tzOffset, roundId],
    queryFn: () => api.getActivityCalendar(365, tzOffset, roundId),
    refetchInterval: 60_000,
  });
  const data = query.data;
  const items = data?.items ?? [];
  const activeRows = useMemo(
    () => items.filter((item) => item.collected > 0 || item.processed > 0),
    [items],
  );
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
  const activeAvg = activeDays > 0 ? total / activeDays : 0;
  const avgText = fmtNumber(
    activeAvg >= 10 ? Math.round(activeAvg) : Math.round(activeAvg * 10) / 10,
  );
  const peak = useMemo(() => {
    let value = 0;
    let date = "";
    for (const item of items) {
      const current = item[metric];
      if (current > value) {
        value = current;
        date = item.date;
      }
    }
    return { value, date };
  }, [items, metric]);
  const recentWeeks = useMemo(
    () =>
      weeks.slice(-12).map((week) => {
        const inRange = week.days.filter((day) => day.inRange);
        return {
          start: inRange[0]?.date ?? week.days[0]?.date ?? "",
          end: inRange[inRange.length - 1]?.date ?? week.days[6]?.date ?? "",
          value: inRange.reduce((sum, day) => sum + day[metric], 0),
        };
      }),
    [weeks, metric],
  );
  const gridWidth = weeks.length > 0 ? weeks.length * (CELL + GAP) - GAP : 1;
  const chartWidth = weeks.length > 0 ? WEEKDAY_WIDTH + WEEKDAY_GAP + gridWidth : undefined;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{hideRoundScope ? "活动日历" : "采集与审核日历"}</CardTitle>
        <CardDescription
          className="flex flex-wrap items-center gap-x-4 gap-y-1"
          style={
            chartWidth ? { width: `${chartWidth}px`, maxWidth: "100%" } : undefined
          }
        >
          <span>
            {hideRoundScope ? "" : `${roundId ? "当前轮次" : "全部轮次"} · `}近一年每日新增达人与审核动作；颜色越深表示当天越多。
          </span>
          {data ? (
            <div className="ml-auto flex items-center gap-1.5 text-[11px]">
              <span>少</span>
              {COLORS[metric].map((color) => (
                <span
                  key={color}
                  className={cn("size-2.5 rounded-[2px]", color)}
                  aria-hidden="true"
                />
              ))}
              <span>多</span>
            </div>
          ) : null}
        </CardDescription>
        <CardAction>
          <div
            className="inline-flex items-center rounded-md border bg-muted/30 p-0.5"
            role="group"
            aria-label="切换日历指标"
          >
            {(["collected", "processed"] as Metric[]).map((value) => {
              const button = (
                <button
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
              );
              if (value !== "processed") {
                return <Fragment key={value}>{button}</Fragment>;
              }
              return (
                <Tooltip key={value}>
                  <TooltipTrigger asChild>{button}</TooltipTrigger>
                  <TooltipContent side="bottom" className="max-w-xs">
                    采用状态、标签、跟进等修改的次数；批量审核按涉及人数累计。
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
        </CardAction>
      </CardHeader>
      <CardContent className="@container">
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
          <>
            <div className="flex min-w-0 items-stretch gap-6">
            <div
              className="flex min-w-0 overflow-x-auto overflow-y-hidden"
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
                        "rounded-[2px]",
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
            <div className="hidden max-h-[108px] min-w-0 flex-1 items-center justify-center gap-5 overflow-hidden border-l pl-5 @min-[1080px]:flex @min-[1320px]:items-stretch @min-[1320px]:justify-start">
              <div className="flex shrink-0 flex-col justify-between gap-2">
                <div className="text-[11px] leading-none text-muted-foreground">近一年</div>
                <div className="grid grid-cols-3 gap-x-6 gap-y-2 @min-[1320px]:grid-cols-2 @min-[1320px]:gap-x-10">
                  <div className="min-w-0 whitespace-nowrap">
                    <div className="text-[11px] leading-none text-muted-foreground">
                      {CUMULATIVE_LABELS[metric]}
                    </div>
                    <div className="mt-1 text-base leading-tight font-semibold tabular-nums">
                      {fmtNumber(total)}
                    </div>
                  </div>
                  <div className="min-w-0 whitespace-nowrap">
                    <div className="text-[11px] leading-none text-muted-foreground">活跃天数</div>
                    <div className="mt-1 text-base leading-tight font-semibold tabular-nums">
                      {fmtNumber(activeDays)}
                    </div>
                  </div>
                  <div className="min-w-0 whitespace-nowrap">
                    <div className="text-[11px] leading-none text-muted-foreground">活跃日平均</div>
                    <div className="mt-1 text-base leading-tight font-semibold tabular-nums">{avgText}</div>
                  </div>
                  <div className="hidden min-w-0 whitespace-nowrap @min-[1320px]:block">
                    <div className="text-[11px] leading-none text-muted-foreground">单日峰值</div>
                    <div className="mt-1 text-base leading-tight font-semibold tabular-nums">
                      {fmtNumber(peak.value)}
                      {peak.date ? (
                        <span className="ml-1 text-[10px] font-normal text-muted-foreground">
                          {peak.date.slice(5)}
                        </span>
                      ) : null}
                    </div>
                  </div>
                </div>
              </div>
              <Separator
                orientation="vertical"
                className="hidden bg-border/70 @min-[1320px]:block"
              />
              <div className="hidden min-w-0 flex-1 flex-col gap-2 @min-[1320px]:flex">
                <WeekTrend weeks={recentWeeks} metric={metric} />
              </div>
            </div>
          </div>
            {activeRows.length > 0 ? (
              <div className="sr-only">
                <table>
                  <caption>
                    {hideRoundScope ? "" : roundId ? "当前轮次" : "全部轮次"}近一年有活动的日期明细，共{" "}
                    {fmtNumber(activeRows.length)} 天
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">日期</th>
                      <th scope="col">新增达人</th>
                      <th scope="col">审核动作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeRows.map((item) => (
                      <tr key={item.date}>
                        <th scope="row">{item.date}</th>
                        <td>{fmtNumber(item.collected)}</td>
                        <td>{fmtNumber(item.processed)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
