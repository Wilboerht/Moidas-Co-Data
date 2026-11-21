import { cn } from "cn";
import { ThinkingOrb, type OrbState } from "@/components/dissolve/thinking-orb";

const STATUS_STATES: Record<string, OrbState> = {
  pending: "thinking",
  running: "searching",
  done: "done",
  failed: "error",
  error: "error",
};

export function roundOrbState(status?: string | null): OrbState {
  return status ? STATUS_STATES[status] ?? "idle" : "idle";
}

export function RoundStatusOrb({
  status,
  size = 20,
  dots,
  dotSize,
  className,
}: {
  status?: string | null;
  size?: number;
  dots?: number;
  dotSize?: number;
  className?: string;
}) {
  const state = roundOrbState(status);
  return (
    <span
      aria-hidden="true"
      className={cn("inline-flex shrink-0 items-center justify-center text-foreground", className)}
      style={{ width: size, height: size }}
    >
      <ThinkingOrb
        size={size}
        dots={dots ?? Math.round(size * 3)}
        dotSize={dotSize ?? Math.max(1.5, size * 0.045)}
        state={state}
        color={state === "error" ? "#ff3b30" : "currentColor"}
        haptics={false}
      />
    </span>
  );
}
