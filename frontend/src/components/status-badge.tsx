import { Badge } from "@/components/ui/badge";
import { cn } from "cn";

const STATUS_TONES: Record<string, string> = {
  shortlisted: "border-amber-200 bg-amber-50 text-amber-700",
  adopted: "border-teal-200 bg-teal-50 text-teal-700",
  rejected: "border-rose-200 bg-rose-50 text-rose-700",
  pending: "border-border bg-muted text-muted-foreground",
};

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("font-normal", STATUS_TONES[status] ?? STATUS_TONES.pending)}
    >
      {label ?? status}
    </Badge>
  );
}
