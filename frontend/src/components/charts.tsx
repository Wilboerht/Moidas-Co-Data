import { Link, useNavigate } from "react-router-dom";
import {
  Bar,
  BarChart as RechartsBarChart,
  CartesianGrid,
  Cell,
  LabelList,
  XAxis,
  YAxis,
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
  color?: string;
  href?: string;
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
  const navigate = useNavigate();
  const hasLinks = items.some((item) => item.href);
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
          margin={{ top: 0, right: 130, bottom: 0, left: 0 }}
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
            width={80}
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 12 }}
          />
          <ChartTooltip
            cursor={false}
            content={
              <ChartTooltipContent
                hideIndicator={items.some((item) => item.color)}
                formatter={(value, _name, item) => {
                  const row = item?.payload as BarItem | undefined;
                  return `${fmtNumber(row?.value ?? 0)} 人 · ${Number(value ?? 0).toFixed(1)}%`;
                }}
              />
            }
          />
          <Bar
            dataKey="percent"
            fill="var(--color-percent)"
            isAnimationActive={false}
            barSize={22}
            radius={[0, 4, 4, 0]}
            onClick={(entry) => {
              const row = (entry as { payload?: BarItem }).payload;
              if (row?.href) navigate(row.href);
            }}
            className={hasLinks ? "cursor-pointer" : undefined}
          >
            {items.map((item) => (
              <Cell key={item.label} fill={item.color ?? "var(--color-percent)"} />
            ))}
            <LabelList dataKey="display" content={<BarValueLabel />} />
          </Bar>
        </RechartsBarChart>
      </ChartContainer>
      {hasLinks ? (
        <nav
          aria-label={`${ariaLabel}筛选`}
          className="sr-only focus-within:not-sr-only focus-within:mt-2 focus-within:flex focus-within:flex-wrap focus-within:gap-2"
        >
          {items.filter((item) => item.href).map((item) => (
            <Link
              key={item.label}
              to={item.href!}
              className="rounded-md px-2 py-1 text-sm underline underline-offset-4 focus-visible:ring-2 focus-visible:ring-ring"
            >
              查看{item.label}达人
            </Link>
          ))}
        </nav>
      ) : null}
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

function BarValueLabel(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  value?: unknown;
}) {
  return (
    <text
      x={(props.x ?? 0) + (props.width ?? 0) + 8}
      y={(props.y ?? 0) + (props.height ?? 0) / 2}
      textAnchor="start"
      dominantBaseline="central"
      fill="var(--muted-foreground)"
      fontSize={11}
    >
      {String(props.value ?? "")}
    </text>
  );
}
