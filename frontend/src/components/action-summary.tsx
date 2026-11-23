import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { api } from "@/shared/api";
import { fmtNumber } from "@/shared/format";
import {
  Card,
  CardAction,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function ActionSummary({ roundId }: { roundId: number | null }) {
  const query = useQuery({
    queryKey: ["action-queue", roundId],
    queryFn: () => api.getActionQueue({ roundId, scope: "all", sort: "ratio", limit: 1 }),
    refetchInterval: 60_000,
  });
  const counts = query.data?.counts;
  const search = new URLSearchParams({
    needs_action: "true",
    sort: "sample_average",
    order: "desc",
  });
  if (roundId !== null) search.set("round", String(roundId));
  const href = `/creators?${search.toString()}`;

  return (
    <Link
      to={href}
      aria-label={counts ? `待处理 ${counts.all} 条，进入达人库处理` : "进入达人库处理待办"}
      className="rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <Card
        size="sm"
        className="h-full bg-primary/[0.02] ring-primary/20 transition-colors hover:bg-primary/[0.05]"
      >
        <CardHeader className="gap-1">
          <CardDescription>待处理</CardDescription>
          <CardTitle className="text-3xl font-semibold tracking-tight tabular-nums">
            {counts ? fmtNumber(counts.all) : "—"}
          </CardTitle>
          <p
            className="text-xs text-muted-foreground"
            title="待处理 = 待审核 + 未分配的备选达人"
          >
            {counts ? (
              <span className="font-medium text-primary">去处理</span>
            ) : (
              "进入达人库处理"
            )}
          </p>
          <CardAction>
            <ArrowUpRight className="size-4 text-primary" aria-hidden="true" />
          </CardAction>
        </CardHeader>
      </Card>
    </Link>
  );
}
