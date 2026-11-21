import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
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
  counts: Counts | null;
  meta: Meta | null;
  settings: Settings | null;
  loading: boolean;
  error: string;
  refreshRounds: () => Promise<void>;
};

const RoundContext = createContext<RoundContextValue | null>(null);

async function loadBootstrap() {
  const [rounds, dashboard, settings, meta] = await Promise.all([
    api.getRounds(),
    api.getDashboard(null),
    api.getSettings(),
    api.getMeta(),
  ]);
  return { rounds, counts: dashboard.counts, settings, meta };
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
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
    await queryClient.invalidateQueries({ queryKey: ["job"] });
    await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  }, [queryClient]);

  const value = useMemo<RoundContextValue>(
    () => ({
      rounds,
      round: rounds.find((item) => item.id === roundId) ?? null,
      roundId,
      setRoundId,
      job: jobQuery.data ?? null,
      counts: query.data?.counts ?? null,
      meta: query.data?.meta ?? null,
      settings: query.data?.settings ?? null,
      loading: query.isLoading,
      error: query.error instanceof Error ? query.error.message : "",
      refreshRounds,
    }),
    [jobQuery.data, query.data, query.error, query.isLoading, refreshRounds, roundId, rounds, setRoundId],
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
