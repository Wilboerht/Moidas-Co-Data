import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, Link2, LoaderCircle, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import { assumeHttpUrl, fmtDateTime } from "@/shared/format";
import type { LeadOwner, LeadStatusDef } from "@/shared/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LeadStatusBadge } from "@/components/lead-status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

type ConfirmAction = "convert" | "duplicate" | "discarded";

const MATCH_CONFIDENCE_LABELS: Record<string, string> = {
  account_id: "账号 ID",
  round_account_id: "轮次账号",
  legacy_user_id: "历史导入 ID",
  nickname: "昵称疑似",
};

const ATTEMPT_STATUS_LABELS: Record<string, string> = {
  matched: "已匹配",
  suggested: "疑似匹配",
  no_match: "未匹配",
  error: "匹配失败",
};

export function LeadSheet({
  leadId,
  statuses,
  owners,
  canCollect,
  onClose,
}: {
  leadId: number;
  statuses: LeadStatusDef[];
  owners: LeadOwner[];
  canCollect: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { meta, rounds } = useRound();
  const query = useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => api.getLead(leadId),
  });
  const lead = query.data;
  const [accountId, setAccountId] = useState("");
  const [profileUrl, setProfileUrl] = useState("");
  const [nickname, setNickname] = useState("");
  const [platform, setPlatform] = useState("xhs");
  const [sourceRoundId, setSourceRoundId] = useState("none");
  const [fetchProfile, setFetchProfile] = useState(true);

  useEffect(() => {
    if (!lead) return;
    setAccountId(lead.account_id || "");
    setProfileUrl(lead.profile_url || "");
    setNickname(lead.nickname || "");
    setPlatform(lead.platform || "xhs");
    setSourceRoundId(lead.source_round_id ? String(lead.source_round_id) : "none");
  }, [
    lead?.id,
    lead?.account_id,
    lead?.profile_url,
    lead?.nickname,
    lead?.platform,
    lead?.source_round_id,
  ]);

  useEffect(() => {
    if (!lead) return;
    setFetchProfile(!lead.matched_user_id);
  }, [lead?.id, lead?.matched_user_id]);
  const platforms = meta?.platforms ?? [];

  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false);

  const refresh = async () => {
    for (const key of [
      ["lead", leadId],
      ["leads"],
      ["lead-identity-health"],
      ["lead-facets"],
      ["bootstrap"],
      ["dashboard"],
      ["creators"],
      ["facets"],
      ["action-queue"],
    ]) {
      await queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateLead(leadId, {
        platform,
        nickname: nickname.trim(),
        account_id: accountId.trim(),
        profile_url: profileUrl.trim(),
        source_round_id: sourceRoundId === "none" ? null : Number(sourceRoundId),
      }),
    onSuccess: async (updated) => {
      if (updated.match_error) {
        toast.warning(`线索资料已保存，但自动匹配失败：${updated.match_error}`);
      } else {
        toast.success("线索资料已保存");
      }
      await refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "保存失败"),
  });

  const statusMutation = useMutation({
    mutationFn: (status: string) => api.updateLead(leadId, { status }),
    onSuccess: async (_updated, status) => {
      const label = statuses.find((item) => item.code === status)?.label ?? status;
      toast.success(`线索状态已更新为「${label}」`);
      await refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "状态更新失败"),
  });

  const ownerMutation = useMutation({
    mutationFn: (ownerUserId: number | null) => api.assignLead(leadId, ownerUserId),
    onSuccess: async () => {
      toast.success("负责人已更新");
      await refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "负责人更新失败"),
  });

  const convertMutation = useMutation({
    mutationFn: async () => {
      if (dirty) {
        await api.updateLead(leadId, {
          platform,
          nickname: nickname.trim(),
          account_id: accountId.trim(),
          profile_url: profileUrl.trim(),
          source_round_id: sourceRoundId === "none" ? null : Number(sourceRoundId),
        });
      }
      return api.convertLead(leadId, {
        account_id: accountId.trim(),
        profile_url: profileUrl.trim(),
        fetch: fetchProfile,
      });
    },
    onSuccess: async () => {
      toast.success("线索已转为达人");
      await refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "转换失败"),
  });

  const matchMutation = useMutation({
    mutationFn: () => api.matchLead(leadId),
    onSuccess: async (result) => {
      if (result.match.matched > 0) {
        toast.success("已重新匹配到达人");
      } else {
        toast.info("暂未匹配到达人");
      }
      await refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "匹配失败"),
  });

  const confirmSuggestionMutation = useMutation({
    mutationFn: (suggestion: { user_id: string; profile_url: string }) =>
      api.updateLead(leadId, {
        account_id: suggestion.user_id,
        ...(suggestion.profile_url
          ? { profile_url: suggestion.profile_url }
          : {}),
      }),
    onSuccess: async (updated) => {
      if (updated.match_error) {
        toast.warning(`已写入账号，但自动匹配失败：${updated.match_error}`);
      } else {
        toast.success("已确认关联，达人资料已同步");
      }
      await refresh();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "确认关联失败"),
  });

  const locked = lead?.status === "converted";
  const dirty =
    Boolean(lead) &&
    (platform !== (lead?.platform || "xhs") ||
      nickname !== (lead?.nickname || "") ||
      accountId !== (lead?.account_id || "") ||
      profileUrl !== (lead?.profile_url || "") ||
      sourceRoundId !== (lead?.source_round_id ? String(lead.source_round_id) : "none"));
  const canIdentify = Boolean(accountId.trim() || profileUrl.trim());
  const hasRounds = Boolean(
    lead &&
      (lead.rounds.length > 0 ||
        lead.source_round_id != null ||
        lead.matched_round_id != null ||
        sourceRoundId !== "none"),
  );
  const convertibleStatus = lead?.status === "unverified" || lead?.status === "verifying";
  const busy =
    saveMutation.isPending ||
    statusMutation.isPending ||
    ownerMutation.isPending ||
    convertMutation.isPending ||
    matchMutation.isPending ||
    confirmSuggestionMutation.isPending;

  const requestClose = () => {
    if (convertMutation.isPending) return;
    if (dirty) {
      setCloseConfirmOpen(true);
      return;
    }
    onClose();
  };

  const { user } = useAuth();
  const ownedByMe = Boolean(
    lead && (lead.owner_user_id == null || lead.owner_user_id === user?.id || user?.role === "admin"),
  );
  const editable = Boolean(lead && lead.status !== "converted" && canCollect && ownedByMe);
  const convertedUserId = lead?.converted_user_id || lead?.account_id || "";
  const convertedRoundId =
    lead?.rounds[0]?.round_id ?? lead?.source_round_id ?? lead?.matched_round_id ?? null;
  const creatorHref =
    locked && convertedUserId && convertedRoundId != null
      ? `/creators?creator=${encodeURIComponent(
          `${lead.converted_platform || lead.platform}|${convertedUserId}`,
        )}&creator_round=${convertedRoundId}`
      : null;

  const matched = Boolean(lead?.matched_user_id);
  const matchedRoundId = lead?.matched_round_id ?? null;
  const matchedCreatorHref =
    matched && matchedRoundId != null
      ? `/creators?creator=${encodeURIComponent(
          `${lead?.matched_platform || lead?.platform || ""}|${lead?.matched_user_id || ""}`,
        )}&creator_round=${matchedRoundId}`
      : null;
  const matchedConfidence =
    MATCH_CONFIDENCE_LABELS[lead?.match_confidence || ""] || lead?.match_confidence || "—";

  const profileHref = assumeHttpUrl(profileUrl);

  const footerHint = !lead
    ? "正在加载线索…"
    : convertMutation.isPending
      ? "正在抓取并转换，请稍候…"
      : locked
        ? "该线索已转为达人，资料只读。"
        : !canCollect
          ? "只读：当前账号没有采集权限"
          : !ownedByMe
            ? `只读：线索由 ${lead?.owner_username || "其他成员"} 负责`
            : dirty
            ? "有未保存的修改"
            : !canIdentify
              ? "填写真实账号 ID 或主页 URL 后可转为达人"
              : !hasRounds
                ? "该线索没有关联轮次，无法转为达人"
                : matched
                  ? "已关联达人，转换默认复用达人库资料"
                  : "资料已保存";

  const confirmCopy =
    confirmAction === "convert"
      ? {
          title: "转为达人？",
          description: matched
            ? fetchProfile
              ? "将按已关联账号刷新达人资料后再转换，可能产生一次接口调用费用。转换后线索资料将不可再编辑。"
              : "达人资料已在达人库中，转换不会产生接口调用费用。转换后线索资料将不可再编辑。"
            : `将按${
                accountId.trim() ? `账号 ID「${accountId.trim()}」` : "主页 URL"
              }抓取并补全达人资料，可能产生一次接口调用费用。转换后线索资料将不可再编辑。`,
          action: "确认转换",
          variant: "default" as const,
        }
      : confirmAction === "duplicate"
        ? {
            title: "标记为重复线索？",
            description: "该线索将被标记为重复，可在线索状态中改回。",
            action: "标记重复",
            variant: "default" as const,
          }
        : {
            title: "废弃该线索？",
            description: "该线索将被标记为废弃，可在线索状态中改回，数据不会被删除。",
            action: "废弃",
            variant: "destructive" as const,
          };

  return (
    <>
      <Dialog open onOpenChange={(open) => !open && requestClose()}>
        <DialogContent className="grid max-h-[85vh] max-w-[calc(100%-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-2xl">
          <DialogHeader className="space-y-1.5 border-b px-5 pt-5 pr-12 pb-3">
            <DialogTitle className="flex flex-wrap items-center gap-2">
              <span className="break-words">{lead?.nickname || `线索 #${leadId}`}</span>
              {lead ? <LeadStatusBadge status={lead.status} label={lead.status_label} /> : null}
            </DialogTitle>
            <DialogDescription>
              {lead ? `${lead.platform_label || lead.platform} · 线索 #${lead.id}` : "核实真实平台账号后可转为达人"}
            </DialogDescription>
          </DialogHeader>

          <div className="min-h-0 overflow-y-auto px-5 py-4">
            {query.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-32 w-full" />
              </div>
            ) : query.error || !lead ? (
              <p className="text-sm text-destructive">
                {query.error instanceof Error ? query.error.message : "线索加载失败"}
              </p>
            ) : (
              <div className="space-y-5">
                <form
                  id="lead-form"
                  className="grid gap-4 sm:grid-cols-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (dirty && !saveMutation.isPending) saveMutation.mutate();
                  }}
                >
                  <div className="space-y-2">
                    <Label htmlFor="lead-nickname">昵称</Label>
                    <Input
                      id="lead-nickname"
                      value={nickname}
                      disabled={!editable}
                      onChange={(event) => setNickname(event.target.value)}
                      autoComplete="off"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="lead-platform">平台</Label>
                    <Select value={platform} onValueChange={setPlatform} disabled={!editable}>
                      <SelectTrigger id="lead-platform" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {platforms.map((item) => (
                          <SelectItem key={item.code} value={item.code}>
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="lead-account">真实账号 ID</Label>
                    <Input
                      id="lead-account"
                      value={accountId}
                      disabled={!editable}
                      onChange={(event) => setAccountId(event.target.value)}
                      placeholder="填写后可用于转换"
                      autoComplete="off"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="lead-url">主页 URL</Label>
                    <div className="relative">
                      <Input
                        id="lead-url"
                        value={profileUrl}
                        disabled={!editable}
                        onChange={(event) => setProfileUrl(event.target.value)}
                        placeholder="https://..."
                        className="pr-9"
                        autoComplete="off"
                      />
                      {profileHref && editable ? (
                        <a
                          href={profileHref}
                          target="_blank"
                          rel="noreferrer"
                          className="absolute top-1/2 right-1 inline-flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                          aria-label="打开主页"
                          title="打开主页"
                        >
                          <ExternalLink className="size-3.5" />
                        </a>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex items-center justify-between gap-4 border px-3 py-2 sm:col-span-2">
                    <div>
                      <Label htmlFor="lead-fetch-profile">转换时抓取最新资料</Label>
                      <p className="text-xs text-muted-foreground">
                        本地已有资料可关闭，避免重复计费。
                      </p>
                    </div>
                    <Switch
                      id="lead-fetch-profile"
                      checked={fetchProfile}
                      onCheckedChange={setFetchProfile}
                      disabled={!editable}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="lead-source-round">来源轮次</Label>
                    <Select
                      value={sourceRoundId}
                      onValueChange={setSourceRoundId}
                      disabled={!editable}
                    >
                      <SelectTrigger id="lead-source-round" className="w-full">
                        <SelectValue placeholder="不关联" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">不关联</SelectItem>
                        {rounds.map((round) => (
                          <SelectItem key={round.id} value={String(round.id)}>
                            {round.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="lead-owner">负责人</Label>
                    <Select
                      value={lead.owner_user_id == null ? "none" : String(lead.owner_user_id)}
                      onValueChange={(value) => ownerMutation.mutate(value === "none" ? null : Number(value))}
                      disabled={!editable || ownerMutation.isPending}
                    >
                      <SelectTrigger id="lead-owner" className="w-full">
                        <SelectValue placeholder="未分配" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">未分配</SelectItem>
                        {owners.map((owner) => (
                          <SelectItem key={owner.id} value={String(owner.id)}>
                            {owner.username}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="lead-status">线索状态</Label>
                    <Select
                      value={lead.status}
                      onValueChange={(value) => statusMutation.mutate(value)}
                      disabled={!editable || statusMutation.isPending}
                    >
                      <SelectTrigger id="lead-status" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {statuses.map((item) => (
                          <SelectItem
                            key={item.code}
                            value={item.code}
                            disabled={item.code === "converted" && lead.status !== "converted"}
                          >
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </form>

                {!locked && matched ? (
                  <section className="space-y-2 border-t pt-4">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0 space-y-1">
                        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
                          <Link2 className="size-4 text-teal-600" aria-hidden="true" />
                          已关联达人
                        </h3>
                        <p className="truncate text-sm">
                          {lead.matched_creator?.nickname || lead.nickname || "—"}
                          <span className="ml-2 font-mono text-xs text-muted-foreground">
                            {lead.matched_user_id}
                          </span>
                        </p>
                        <p className="text-xs text-muted-foreground">
                          匹配轮次{" "}
                          {lead.matched_round_name ||
                            (matchedRoundId != null ? `轮次 ${matchedRoundId}` : "—")}{" "}
                          · 方式 {matchedConfidence}
                          {lead.matched_at ? ` · ${fmtDateTime(lead.matched_at)}` : ""}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {matchedCreatorHref ? (
                          <Button asChild variant="outline" size="sm">
                            <Link to={matchedCreatorHref}>查看达人</Link>
                          </Button>
                        ) : null}
                        {editable ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => matchMutation.mutate()}
                            disabled={matchMutation.isPending}
                          >
                            {matchMutation.isPending ? (
                              <LoaderCircle className="animate-spin" />
                            ) : null}
                            重新匹配
                          </Button>
                        ) : null}
                      </div>
                    </div>
                  </section>
                ) : null}

                <dl className="grid gap-x-6 gap-y-3 border-t pt-4 text-sm sm:grid-cols-2">
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">来源</dt>
                    <dd className="truncate">{lead.source_label || lead.source}</dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">来源轮次</dt>
                    <dd className="truncate">{lead.source_round_name || lead.source_round_id || "—"}</dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">创建时间</dt>
                    <dd className="truncate">{lead.created_at ? fmtDateTime(lead.created_at) : "—"}</dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-muted-foreground">更新时间</dt>
                    <dd className="truncate">{lead.updated_at ? fmtDateTime(lead.updated_at) : "—"}</dd>
                  </div>
                  {lead.raw_title && lead.raw_title !== lead.nickname ? (
                    <div className="min-w-0 sm:col-span-2">
                      <dt className="text-xs text-muted-foreground">原始标题</dt>
                      <dd className="break-words">{lead.raw_title}</dd>
                    </div>
                  ) : null}
                  {lead.rounds.length > 1 ? (
                    <div className="min-w-0 sm:col-span-2">
                      <dt className="text-xs text-muted-foreground">全部关联轮次</dt>
                      <dd className="mt-1 flex flex-wrap gap-1.5">
                        {lead.rounds.map((round) => (
                          <Badge key={round.round_id} variant="secondary" className="font-normal">
                            {round.round_name || `轮次 ${round.round_id}`}
                          </Badge>
                        ))}
                      </dd>
                    </div>
                  ) : null}
                </dl>
                {lead.attempts.length > 0 ? (
                  <section className="space-y-2 border-t pt-4">
                    <h3 className="text-sm font-semibold">核实记录</h3>
                    <div className="divide-y border-y text-sm">
                      {lead.attempts.slice(0, 6).map((attempt) => (
                        <div key={attempt.id} className="space-y-0.5 py-2">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="flex min-w-0 flex-wrap items-center gap-2">
                              <span className="truncate">
                                {attempt.candidate_nickname ||
                                  attempt.candidate_user_id ||
                                  attempt.query ||
                                  "—"}
                              </span>
                              {attempt.candidate_nickname && attempt.candidate_user_id ? (
                                <span className="font-mono text-xs text-muted-foreground">
                                  {attempt.candidate_user_id}
                                </span>
                              ) : null}
                            </span>
                            <span className="flex items-center gap-2">
                              <span className="text-xs text-muted-foreground">
                                {attempt.provider === "round" ? "轮次匹配" : attempt.provider || "—"} ·{" "}
                                {ATTEMPT_STATUS_LABELS[attempt.status] || attempt.status}
                                {attempt.confidence
                                  ? ` · ${MATCH_CONFIDENCE_LABELS[attempt.confidence] || attempt.confidence}`
                                  : ""}
                              </span>
                              {attempt.status === "suggested" &&
                              attempt.candidate_user_id &&
                              editable &&
                              !lead.account_id ? (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={busy}
                                  onClick={() =>
                                    confirmSuggestionMutation.mutate({
                                      user_id: attempt.candidate_user_id,
                                      profile_url: attempt.candidate_profile_url,
                                    })
                                  }
                                >
                                  {confirmSuggestionMutation.isPending ? (
                                    <LoaderCircle className="animate-spin" />
                                  ) : null}
                                  确认关联
                                </Button>
                              ) : null}
                            </span>
                          </div>
                          {attempt.error ? (
                            <p className="text-xs text-muted-foreground">{attempt.error}</p>
                          ) : null}
                          <p className="text-xs text-muted-foreground">
                            {fmtDateTime(attempt.created_at)}
                          </p>
                        </div>
                      ))}
                    </div>
                  </section>
                ) : null}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/40 px-5 py-3">
            <p className={`text-xs ${dirty ? "text-amber-600" : "text-muted-foreground"}`}>
              {footerHint}
            </p>
            <div className="flex items-center gap-2">
              {locked ? (
                creatorHref ? (
                  <Button asChild>
                    <Link to={creatorHref}>查看达人</Link>
                  </Button>
                ) : null
              ) : !lead ? (
                <span className="text-xs text-muted-foreground">加载中…</span>
              ) : !editable ? (
                <span className="text-xs text-muted-foreground">
                  {!canCollect
                    ? "只读：当前账号没有采集权限"
                    : `只读：线索由 ${lead?.owner_username || "其他成员"} 负责`}
                </span>
              ) : (
                <>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="更多操作"
                        title="更多操作"
                        disabled={busy}
                      >
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        disabled={matchMutation.isPending || !hasRounds}
                        onSelect={() => matchMutation.mutate()}
                      >
                        重新匹配达人
                      </DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => setConfirmAction("duplicate")}>
                        标记重复
                      </DropdownMenuItem>
                      <DropdownMenuItem variant="destructive" onSelect={() => setConfirmAction("discarded")}>
                        废弃
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Button
                    type="submit"
                    form="lead-form"
                    variant="outline"
                    disabled={!dirty || saveMutation.isPending || convertMutation.isPending}
                  >
                    {saveMutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
                    保存资料
                  </Button>
                  <Button
                    onClick={() => setConfirmAction("convert")}
                    disabled={
                      !canIdentify ||
                      !hasRounds ||
                      !convertibleStatus ||
                      busy ||
                      !lead
                    }
                    title={
                      !canIdentify
                        ? "填写真实账号 ID 或主页 URL 后可转为达人"
                        : !hasRounds
                          ? "该线索没有关联轮次，无法转为达人"
                          : !convertibleStatus
                            ? "仅待核实/核实中的线索可以转换"
                            : undefined
                    }
                  >
                    {convertMutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
                    {convertMutation.isPending ? "转换中…" : "转为达人"}
                  </Button>
                </>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmAction !== null} onOpenChange={(open) => !open && setConfirmAction(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmCopy.title}</AlertDialogTitle>
            <AlertDialogDescription>{confirmCopy.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant={confirmCopy.variant}
              disabled={busy}
              onClick={(event) => {
                event.preventDefault();
                const action = confirmAction;
                setConfirmAction(null);
                if (action === "convert") convertMutation.mutate();
                else if (action) statusMutation.mutate(action);
              }}
            >
              {confirmCopy.action}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={closeConfirmOpen} onOpenChange={setCloseConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle>
            <AlertDialogDescription>
              昵称、真实账号 ID 和主页 URL 的修改尚未保存。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续编辑</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setCloseConfirmOpen(false);
                onClose();
              }}
            >
              放弃并关闭
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
