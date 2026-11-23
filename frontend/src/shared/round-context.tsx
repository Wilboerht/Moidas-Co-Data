import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../shared/api";
import type { Counts, Job, Meta, Round, Settings } from "../shared/types";

type RoundContextValue = {
  rounds: Round[];
  round: Round | null;
  roundId: number | null;
  setRoundId: (id: number) => void;
  job: Job | null;
  activeJob: Job | null;
  latestJob: Job | null;
  counts: Counts | null;
  meta: Meta | null;
  settings: Settings | null;
  loading: boolean;
  error: string;
  refreshRounds: () => Promise<void>;
};

const RoundContext = createContext<RoundContextValue | null>(null);

async function loadCounts(): Promise<Counts> {
  try {
    return await api.getCounts();
  } catch {
    return (await api.getDashboard(null)).counts;
  }
}

async function loadBootstrap() {
  const [rounds, counts, settings, meta] = await Promise.all([
    api.getRounds(),
    loadCounts(),
    api.getSettings(),
    api.getMeta(),
  ]);
  return { rounds, counts, settings, meta };
}

export function RoundProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["bootstrap"], queryFn: loadBootstrap });
  const [roundId, setRoundIdState] = useState<number | null>(() => {
    const saved = window.localStorage.getItem("codata_round");
    const parsed = saved ? Number(saved) : NaN;
    return Number.isFinite(parsed) ? parsed : null;
  });

  const rounds = query.data?.rounds ?? [];
  const jobQuery = useQuery({
    queryKey: ["job", roundId],
    queryFn: () => api.getJob(roundId!),
    enabled: roundId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "running" || status === "pending" ? 2500 : 30000;
    },
  });
  const latestJobQuery = useQuery({
    queryKey: ["latest-job"],
    queryFn: api.getLatestJob,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "running" || status === "pending" ? 2500 : 15000;
    },
  });
  const activeJobQuery = useQuery({
    queryKey: ["active-job"],
    queryFn: api.getActiveJob,
    refetchInterval: (query) => (query.state.data ? 2500 : 15000),
  });
  const latestJob = latestJobQuery.data ?? null;
  const activeJob = activeJobQuery.data ?? null;
  const previousJobRef = useRef<{ id: number; status: string } | null>(null);
  const previousActiveIdRef = useRef<number | null>(null);

  useEffect(() => {
    const job = latestJobQuery.data;
    if (!job) return;
    const previous = previousJobRef.current;
    const wasActive =
      previous?.id === job.id &&
      (previous.status === "running" || previous.status === "pending");
    const isActive = job.status === "running" || job.status === "pending";
    if (wasActive && !isActive) {
      for (const key of [
        ["leads"],
        ["lead"],
        ["dashboard"],
        ["activity"],
        ["creators"],
        ["facets"],
        ["action-queue"],
        ["bootstrap"],
        ["task-list"],
        ["job"],
        ["lead-identity-health"],
        ["lead-facets"],
      ]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    }
    previousJobRef.current = { id: job.id, status: job.status };
  }, [latestJobQuery.data, queryClient]);

  useEffect(() => {
    const current = activeJobQuery.data?.id ?? null;
    const previous = previousActiveIdRef.current;
    if (previous !== current && (previous !== null || current !== null)) {
      for (const key of [
        ["leads"],
        ["lead"],
        ["dashboard"],
        ["activity"],
        ["creators"],
        ["facets"],
        ["action-queue"],
        ["bootstrap"],
        ["task-list"],
        ["job"],
        ["lead-identity-health"],
        ["lead-facets"],
      ]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    }
    previousActiveIdRef.current = current;
  }, [activeJobQuery.data, queryClient]);

  useEffect(() => {
    if (!query.data || rounds.length === 0) {
      return;
    }
    if (roundId !== null && rounds.some((item) => item.id === roundId)) {
      return;
    }
    setRoundIdState(rounds[0].id);
  }, [query.data, roundId, rounds]);

  useEffect(() => {
    if (roundId !== null) {
      window.localStorage.setItem("codata_round", String(roundId));
    }
  }, [roundId]);

  const setRoundId = useCallback((id: number) => {
    setRoundIdState(id);
    window.localStorage.setItem("codata_round", String(id));
  }, []);

  const refreshRounds = useCallback(async () => {
    for (const key of [
      ["bootstrap"],
      ["job"],
      ["latest-job"],
      ["active-job"],
      ["dashboard"],
      ["task-list"],
      ["creators"],
      ["facets"],
      ["action-queue"],
      ["lead-identity-health"],
      ["lead-facets"],
    ]) {
      await queryClient.invalidateQueries({ queryKey: key });
    }
  }, [queryClient]);

  const value = useMemo<RoundContextValue>(
    () => ({
      rounds,
      round: rounds.find((item) => item.id === roundId) ?? null,
      roundId,
      setRoundId,
      job: jobQuery.data ?? null,
      activeJob,
      latestJob,
      counts: query.data?.counts ?? null,
      meta: query.data?.meta ?? null,
      settings: query.data?.settings ?? null,
      loading: query.isLoading,
      error: query.error instanceof Error ? query.error.message : "",
      refreshRounds,
    }),
    [jobQuery.data, activeJob, latestJob, query.data, query.error, query.isLoading, refreshRounds, roundId, rounds, setRoundId],
  );

  return <RoundContext.Provider value={value}>{children}</RoundContext.Provider>;
}

export function useRound() {
  const value = useContext(RoundContext);
  if (!value) {
    throw new Error("useRound must be used inside RoundProvider");
  }
  return value;
}
