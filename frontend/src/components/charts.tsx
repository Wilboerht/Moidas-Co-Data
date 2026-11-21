import {
  Bar,
  BarChart as RechartsBarChart,
  CartesianGrid,
  LabelList,
  XAxis,
  YAxis,
  useChartWidth,
} from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { fmtNumber } from "@/shared/format";

type BarItem = {
  label: string;
  value: number;
  percent: number;
  display: string;
};

export function HorizontalBarChart({
  items,
  ariaLabel,
  scaleMax: requestedScaleMax,
}: {
  items: BarItem[];
  ariaLabel: string;
  scaleMax?: number;
}) {
  const maxPercent = Math.max(0, ...items.map((item) => item.percent));
  const scaleMax = requestedScaleMax ?? Math.max(10, Math.ceil(maxPercent / 10) * 10);
  const ticks = Array.from({ length: Math.floor(scaleMax / 10) + 1 }, (_, index) => index * 10);
  const config = {
    percent: { label: "占比", color: "var(--chart-2)" },
  } satisfies ChartConfig;

  return (
    <>
      <ChartContainer
        config={config}
        className="aspect-auto h-[220px] w-full"
        aria-label={ariaLabel}
      >
      <RechartsBarChart
        data={items}
        layout="vertical"
        margin={{ top: 0, right: 88, bottom: 0, left: 0 }}
        barCategoryGap={16}
      >
        <CartesianGrid horizontal={false} stroke="var(--border)" />
        <XAxis
          type="number"
          domain={[0, scaleMax]}
          ticks={ticks}
          tickFormatter={(value: number) => `${value}%`}
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 11 }}
        />
        <YAxis
          type="category"
          dataKey="label"
          width={112}
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 12 }}
        />
        <ChartTooltip
          cursor={false}
          content={<ChartTooltipContent hideLabel />}
        />
        <Bar
          dataKey="percent"
          fill="var(--color-percent)"
          isAnimationActive={false}
          barSize={22}
          radius={[0, 4, 4, 0]}
        >
          <LabelList dataKey="display" content={<AlignedValueLabel />} />
        </Bar>
      </RechartsBarChart>
      </ChartContainer>
      <table className="sr-only">
      <caption>{ariaLabel}</caption>
      <thead>
        <tr><th>类别</th><th>人数</th><th>占比</th></tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.label}>
            <th scope="row">{item.label}</th>
            <td>{fmtNumber(item.value)}</td>
            <td>{item.percent.toFixed(1)}%</td>
          </tr>
        ))}
      </tbody>
      </table>
    </>
  );
}

function AlignedValueLabel(props: {
  x?: number;
  y?: number;
  height?: number;
  value?: unknown;
}) {
  const chartWidth = useChartWidth();

  if (chartWidth == null) {
    return null;
  }

  return (
    <text
      x={chartWidth - 16}
      y={(props.y ?? 0) + (props.height ?? 0) / 2}
      textAnchor="end"
      dominantBaseline="central"
      fill="var(--muted-foreground)"
      fontSize={11}
    >
      {String(props.value ?? "")}
    </text>
  );
}

type StackedSegment = {
  key: string;
  label: string;
  value: number;
  percent: number;
  color?: string;
};

export function ProcessingBar({ segments }: { segments: StackedSegment[] }) {
  const data = [
    Object.fromEntries(segments.map((segment) => [segment.key, segment.percent])),
  ];
  const config = Object.fromEntries(
    segments.map((segment, index) => [
      segment.key,
      { label: segment.label, color: segment.color ?? `var(--chart-${index + 1})` },
    ]),
  ) satisfies ChartConfig;

  return (
    <div className="space-y-3">
      <ChartContainer
        config={config}
        className="aspect-auto h-6 w-full"
        aria-label="资料处理进度"
      >
        <RechartsBarChart
          data={data}
          layout="vertical"
          margin={{ top: 0, right: 0, bottom: 0, left: 0 }}
          barCategoryGap={0}
        >
          <XAxis type="number" domain={[0, 100]} hide />
          <YAxis type="category" hide />
          <ChartTooltip
            cursor={false}
            content={<ChartTooltipContent hideLabel />}
          />
          {segments.map((segment, index) => (
            <Bar
              key={segment.key}
              dataKey={segment.key}
              stackId="progress"
              fill={`var(--color-${segment.key})`}
              isAnimationActive={false}
              radius={
                index === 0
                  ? [4, 0, 0, 4]
                  : index === segments.length - 1
                    ? [0, 4, 4, 0]
                    : [0, 0, 0, 0]
              }
            />
          ))}
        </RechartsBarChart>
      </ChartContainer>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {segments.map((segment) => (
          <div key={segment.key} className="flex items-center gap-2 text-xs text-muted-foreground">
            <span
              className="size-2.5 border"
              style={{
                backgroundColor: segment.color ?? `var(--chart-${segments.indexOf(segment) + 1})`,
              }}
            />
            <span>
              {segment.label} {fmtNumber(segment.value)} · {segment.percent.toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
      <table className="sr-only">
        <caption>资料处理进度</caption>
        <thead>
          <tr><th>状态</th><th>人数</th><th>占比</th></tr>
        </thead>
        <tbody>
          {segments.map((segment) => (
            <tr key={segment.key}>
              <th scope="row">{segment.label}</th>
              <td>{fmtNumber(segment.value)}</td>
              <td>{segment.percent.toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
