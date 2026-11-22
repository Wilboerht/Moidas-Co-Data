import { Badge } from "@/components/ui/badge";
import { cn } from "cn";

const LEAD_STATUS_TONES: Record<string, string> = {
  unverified: "border-border bg-muted text-muted-foreground",
  verifying: "border-sky-200 bg-sky-50 text-sky-700",
  converted: "border-emerald-200 bg-emerald-50 text-emerald-700",
  duplicate: "border-amber-200 bg-amber-50 text-amber-700",
  discarded: "border-rose-200 bg-rose-50 text-rose-700",
};

export function LeadStatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("font-normal", LEAD_STATUS_TONES[status] ?? LEAD_STATUS_TONES.unverified)}
    >
      {label ?? status}
    </Badge>
  );
}
