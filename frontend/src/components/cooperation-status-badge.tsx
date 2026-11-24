import { Badge } from "@/components/ui/badge";
import { cn } from "cn";

const COOPERATION_TONES: Record<string, string> = {
  not_contacted: "border-border bg-muted text-muted-foreground",
  contacted: "border-sky-200 bg-sky-50 text-sky-700",
  negotiating: "border-amber-200 bg-amber-50 text-amber-700",
  cooperating: "border-violet-200 bg-violet-50 text-violet-700",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  declined: "border-rose-200 bg-rose-50 text-rose-700",
  cancelled: "border-slate-200 bg-slate-50 text-slate-600",
};

export function CooperationStatusBadge({
  status,
  label,
  emptyLabel = "未进入",
}: {
  status: string;
  label?: string;
  emptyLabel?: string;
}) {
  if (!status) {
    return (
      <Badge variant="outline" className="font-normal text-muted-foreground">
        {emptyLabel}
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className={cn("font-normal", COOPERATION_TONES[status] ?? COOPERATION_TONES.not_contacted)}
    >
      {label || status}
    </Badge>
  );
}
