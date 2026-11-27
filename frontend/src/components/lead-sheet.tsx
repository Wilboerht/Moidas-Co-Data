import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, Link2, LoaderCircle, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
import { useUnsavedGuard } from "@/shared/use-unsaved-guard";
import { useRound } from "@/shared/round-context";
import { assumeHttpUrl, fmtDateTime } from "@/shared/format";
import type { LeadOwner, LeadStatusDef, LeadVerifyResult } from "@/shared/types";
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

type IdentityMismatch = {
  code: string;
  message: string;
  lead_id: number;
  platform: string;
  requested_user_id: string;
  requested_handle: string;
  requested_profile_url: string;
  resolved_user_id: string;
  resolved_handle: string;
  resolved_nickname: string;
  resolved_profile_url: string;
  confidence: string;
  status: string;
  note: string;
  provider: string;
};

const IDENTITY_STATUS_LABELS: Record<string, string> = {
  unverified: "未校验",
  verified: "已校验",
  mismatch: "待确认",
  not_found: "未找到",
};

const IDENTITY_CONFIDENCE_LABELS: Record<string, string> = {
  account_id: "用户 ID 一致",
  platform_handle: "平台账号一致",
  profile_url: "主页一致",
  nickname: "仅昵称一致",
  manual_mismatch: "人工确认",
  matched: "本地匹配",
  local: "本地达人",
  none: "无匹配",
  round_account_id: "轮次账号",
  auto: "自动匹配",
};

const MATCH_CONFIDENCE_LABELS: Record<string, string> = {
  account_id: "账号 ID",
  round_account_id: "轮次账号",
  auto: "自动匹配",
  legacy_user_id: "历史导入 ID",
  nickname: "昵称疑似",
};

const ATTEMPT_STATUS_LABELS: Record<string, string> = {
  matched: "已匹配",
  suggested: "疑似匹配",
  no_match: "未匹配",
  error: "匹配失败",
};

const IDENTITY_EVENT_LABELS: Record<string, string> = {
  converted: "转为达人",
  mismatch_confirmed: "确认采用抓取账号",
  mismatch_blocked: "账号不一致已阻止",
  verify_verified: "校验通过",
  verify_mismatch: "校验不一致",
  verify_not_found: "未找到账号",
  verify_error: "校验失败",
  migrated: "身份迁移",
};

export function LeadSheet({
  leadId,
  statuses,
  owners,
  canCollect,
  canEditLeads,
  onClose,
}: {
  leadId: number;
  statuses: LeadStatusDef[];
  owners: LeadOwner[];
  canCollect: boolean;
  canEditLeads: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { meta, rounds, settings } = useRound();
  const query = useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => api.getLead(leadId),
  });
  const lead = query.data;
  const [accountId, setAccountId] = useState("");
  const [platformHandle, setPlatformHandle] = useState("");
  const [profileUrl, setProfileUrl] = useState("");
  const [nickname, setNickname] = useState("");
  const [platform, setPlatform] = useState("xhs");
  const [provider, setProvider] = useState("auto");
  const [sourceRoundId, setSourceRoundId] = useState("none");
  const [fetchProfile, setFetchProfile] = useState(true);
  const [verifyResult, setVerifyResult] = useState<LeadVerifyResult | null>(null);
  const [mismatchResult, setMismatchResult] = useState<IdentityMismatch | null>(null);

  useEffect(() => {
    if (!lead) return;
    setAccountId(lead.account_id || "");
    setPlatformHandle(lead.platform_handle || "");
    setProfileUrl(lead.profile_url || "");
    setNickname(lead.nickname || "");
    setPlatform(lead.platform || "xhs");
    setSourceRoundId(lead.source_round_id ? String(lead.source_round_id) : "none");
    setVerifyResult(null);
  }, [
    lead?.id,
    lead?.account_id,
    lead?.platform_handle,
    lead?.profile_url,
    lead?.nickname,
    lead?.platform,
    lead?.source_round_id,
  ]);

  useEffect(() => {
    if (!lead) return;
    setFetchProfile(!lead.matched_creator);
  }, [lead?.id, lead?.matched_creator?.user_id]);
  const platforms = meta?.platforms ?? [];
  const platformConfig = settings?.platforms.find((item) => item.code === platform);
  const providerOptions = (platformConfig?.providers ?? []).filter(
    (item) => item.enabled,
  );
  const automaticProvider = providerOptions
    .filter((item) => item.ready)
    .reduce<(typeof providerOptions)[number] | undefined>((best, item) => {
      if (!best) return item;
      return item.user_cost_tokens < best.user_cost_tokens ? item : best;
    }, undefined);
  const effectiveProviderId =
    provider === "auto" ? automaticProvider?.id : provider;
  const selectedProvider = providerOptions.find(
    (item) => item.id === effectiveProviderId,
  );
  const providerReady = Boolean(selectedProvider?.ready);
  const providerCostTokens = selectedProvider?.user_cost_tokens ?? 0;
  const needsHandleResolution =
    platform === "xhs" &&
    !accountId.trim() &&
    !profileUrl.trim() &&
    Boolean(platformHandle.trim());
  const providerTotalCostTokens =
    providerCostTokens +
    (needsHandleResolution ? (selectedProvider?.search_cost_tokens ?? 0) : 0);
  const providerCostCny = selectedProvider
    ? (
        Number(selectedProvider.user_cost_cny) +
        (needsHandleResolution ? Number(selectedProvider.search_cost_cny) : 0)
      ).toFixed(2)
    : "—";
  const needsUserId =
    !accountId.trim() &&
    !profileUrl.trim() &&
    Boolean(platformHandle.trim()) &&
    platform !== "xhs" &&
    platform !== "instagram" &&
    platform !== "zhihu";
  const providerLabel =
    provider === "auto"
      ? `自动（${selectedProvider?.label ?? "未配置"}）`
      : selectedProvider?.label ?? provider;

  useEffect(() => {
    if (!settings || provider === "auto") return;
    if (!providerOptions.some((item) => item.id === provider)) {
      setProvider("auto");
    }
  }, [provider, providerOptions, settings]);

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
        platform_handle: platformHandle.trim(),
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
    mutationFn: async (confirmMismatch: boolean) => {
      if (dirty) {
        await api.updateLead(leadId, {
          platform,
          nickname: nickname.trim(),
          account_id: accountId.trim(),
          platform_handle: platformHandle.trim(),
          profile_url: profileUrl.trim(),
          source_round_id: sourceRoundId === "none" ? null : Number(sourceRoundId),
        });
      }
      return api.convertLead(leadId, {
        account_id: accountId.trim(),
        platform_handle: platformHandle.trim(),
        profile_url: profileUrl.trim(),
        fetch: fetchProfile,
        provider,
        confirm_mismatch: confirmMismatch,
      });
    },
    onSuccess: async () => {
      toast.success("线索已转为达人（待采用）");
      setMismatchResult(null);
      setVerifyResult(null);
      await refresh();
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.detail &&
        typeof error.detail === "object" &&
        (error.detail as { code?: string }).code === "lead_identity_mismatch"
      ) {
        setMismatchResult(error.detail as IdentityMismatch);
        setConfirmAction(null);
        return;
      }
      toast.error(error instanceof Error ? error.message : "转换失败");
    },
  });

  const verifyMutation = useMutation({
    mutationFn: async (options: { apply?: boolean; confirmMismatch?: boolean } = {}) => {
      if (dirty) {
        await api.updateLead(leadId, {
          platform,
          nickname: nickname.trim(),
          account_id: accountId.trim(),
          platform_handle: platformHandle.trim(),
          profile_url: profileUrl.trim(),
          source_round_id: sourceRoundId === "none" ? null : Number(sourceRoundId),
        });
      }
      return api.verifyLead(leadId, {
        provider,
        account_id: accountId.trim() || undefined,
        platform_handle: platformHandle.trim() || undefined,
        profile_url: profileUrl.trim() || undefined,
        apply: options.apply,
        confirm_mismatch: options.confirmMismatch,
      });
    },
    onSuccess: async (result) => {
      setVerifyResult(result);
      if (result.applied) {
        toast.success("账号校验结果已保存");
        await refresh();
      } else if (result.status === "verified") {
        toast.success("校验通过，可保存结果或直接转换");
      } else if (result.status === "mismatch") {
        toast.warning(result.note || "抓取结果与线索不一致，请确认");
      } else {
        toast.error(result.note || "未能校验账号");
      }
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "校验失败"),
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
      platformHandle !== (lead?.platform_handle || "") ||
      profileUrl !== (lead?.profile_url || "") ||
      sourceRoundId !== (lead?.source_round_id ? String(lead.source_round_id) : "none"));
  useUnsavedGuard(dirty, "有未保存的线索修改，确定离开当前页面？");

  const canIdentify = Boolean(
    accountId.trim() || platformHandle.trim() || profileUrl.trim(),
  );
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
    verifyMutation.isPending ||
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
  const editable = Boolean(lead && lead.status !== "converted" && canEditLeads && ownedByMe);
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
  const matchedConfidence = (() => {
    const value = lead?.match_confidence;
    const label = MATCH_CONFIDENCE_LABELS[value || ""] || value || "—";
    return !canCollect && value === "round_account_id" ? "自动匹配" : label;
  })();

  const profileHref = assumeHttpUrl(profileUrl);

  const footerHint = !lead
    ? "正在加载线索…"
    : convertMutation.isPending
      ? "正在抓取并转换，请稍候…"
      : locked
        ? "该线索已转为达人，资料只读。"
        : !canEditLeads
          ? "当前为只读模式"
          : !ownedByMe
            ? `只读：线索由 ${lead?.owner_username || "其他成员"} 负责`
            : dirty
            ? "有未保存的修改"
            : canCollect && !canIdentify
              ? "填写平台账号、用户 ID 或主页 URL 后可转为达人"
              : canCollect && !hasRounds
                ? "该线索没有关联轮次，无法转为达人"
                : canCollect && fetchProfile && !providerReady
                  ? "当前平台没有可用的采集服务商，请先在设置中配置"
                  : matched
                    ? canCollect
                      ? "已关联达人，转换默认复用达人库资料"
                      : "已关联达人"
                    : "资料已保存";

  const confirmCopy =
    confirmAction === "convert"
      ? {
          title: "转为达人？",
          description: lead?.matched_creator
            ? fetchProfile
              ? `将按已关联账号刷新达人资料后再转换，服务商：${providerLabel}，预计 ${providerTotalCostTokens} token（约 ${providerCostCny} 元）。转换后线索资料将不可再编辑。`
              : "达人资料已在达人库中，转换不会产生接口调用费用。转换后线索资料将不可再编辑。"
            : `将按${
                accountId.trim()
                  ? `用户 ID「${accountId.trim()}」`
                  : platformHandle.trim()
                    ? `平台账号「${platformHandle.trim()}」`
                    : "主页 URL"
              }抓取并校验账号，服务商：${providerLabel}，预计 ${providerTotalCostTokens} token（约 ${providerCostCny} 元）。如抓取结果与账号不一致，会再次请你确认。`,
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
              <div className="space-y-2">
                <p className="text-sm text-destructive">
                  {query.error instanceof Error ? query.error.message : "线索加载失败"}
                </p>
                <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
                  重试
                </Button>
              </div>
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
                    <Label htmlFor="lead-handle">平台账号</Label>
                    <Input
                      id="lead-handle"
                      value={platformHandle}
                      disabled={!editable}
                      onChange={(event) => setPlatformHandle(event.target.value)}
                      placeholder="小红书号 / 微博号 / 抖音号等"
                      autoComplete="off"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="lead-account">平台用户 ID</Label>
                    <Input
                      id="lead-account"
                      value={accountId}
                      disabled={!editable}
                      onChange={(event) => setAccountId(event.target.value)}
                      placeholder="采集接口使用的内部 ID，可校验后自动获取"
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
                        placeholder="https://…"
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
                  {canCollect ? (
                    <div className="space-y-2 sm:col-span-2">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Label htmlFor="lead-provider">采集服务商</Label>
                        <span className="text-xs text-muted-foreground">
                          {selectedProvider
                            ? `${providerTotalCostTokens} token / 约 ${providerCostCny} 元${needsHandleResolution ? " 起（含搜索解析）" : ""}`
                            : "当前平台没有可用服务商"}
                        </span>
                      </div>
                      <Select
                        value={provider}
                        onValueChange={setProvider}
                        disabled={!editable || busy || providerOptions.length === 0}
                      >
                        <SelectTrigger id="lead-provider" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="auto">
                            自动选择最便宜（{automaticProvider?.label ?? "未配置"}）
                          </SelectItem>
                          {providerOptions.map((item) => (
                            <SelectItem
                              key={item.id}
                              value={item.id}
                              disabled={!item.ready}
                            >
                              {item.label}
                              {item.ready ? "" : "（未配置）"} ·{" "}
                              {item.user_cost_tokens} token/次
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {!providerReady ? (
                        <p className="text-xs text-destructive">
                          当前没有可用服务商，请先在设置中配置；关闭“抓取最新资料”可使用本地达人资料。
                        </p>
                      ) : null}
                      {needsHandleResolution ? (
                        <p className="text-xs text-amber-600">
                          当前只有小红书号：校验会先按昵称搜索解析用户 ID，额外产生一次搜索费用（约{" "}
                          {selectedProvider?.search_cost_tokens ?? 0} token）。如果已有主页 URL
                          或 24 位用户 ID，直接填写可跳过搜索。
                        </p>
                      ) : null}
                      {needsUserId ? (
                        <p className="text-xs text-amber-600">
                          该平台账号不能直接作为接口用户 ID，请填写主页 URL 或平台用户 ID 后再校验。
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                  {canCollect ? (
                    <div className="space-y-2 border px-3 py-2 sm:col-span-2">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                            账号校验
                            <Badge variant="secondary" className="font-normal">
                              {lead.identity_status_label || IDENTITY_STATUS_LABELS[lead.identity_status] || lead.identity_status}
                            </Badge>
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {lead.identity_checked_at
                              ? `最近校验 ${fmtDateTime(lead.identity_checked_at)}`
                              : "尚未校验；校验通过后会自动保存，不一致时需要确认。"}
                            {lead.identity_note ? ` · ${lead.identity_note}` : ""}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            校验只确认身份；直接“转为达人”会在一次抓取中完成校验和补全。
                          </p>
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => verifyMutation.mutate({ apply: true })}
                          disabled={!canIdentify || !providerReady || busy || !editable}
                          title={
                            !providerReady
                              ? "请先选择可用的采集服务商"
                              : undefined
                          }
                        >
                          {verifyMutation.isPending ? (
                            <LoaderCircle className="animate-spin" />
                          ) : null}
                          校验账号
                        </Button>
                      </div>
                      {verifyResult ? (
                        <div className="space-y-1.5 border-t pt-2 text-xs">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge
                              variant={verifyResult.status === "verified" ? "secondary" : "outline"}
                              className="font-normal"
                            >
                              {IDENTITY_STATUS_LABELS[verifyResult.status] || verifyResult.status}
                            </Badge>
                            <span className="text-muted-foreground">
                              {IDENTITY_CONFIDENCE_LABELS[verifyResult.confidence] || verifyResult.confidence || "—"}
                              {verifyResult.provider ? ` · ${verifyResult.provider}` : ""}
                              {verifyResult.resolution === "search" ? " · 搜索结果解析" : ""}
                              {verifyResult.cost
                                ? ` · ${verifyResult.cost} token`
                                : verifyResult.estimated_cost
                                  ? ` · 未计费（预估 ${verifyResult.estimated_cost} token）`
                                  : ""}
                              {verifyResult.attempts > 1 ? ` · ${verifyResult.attempts} 次尝试` : ""}
                            </span>
                          </div>
                          <p className="break-words">
                            请求：{verifyResult.requested_user_id || verifyResult.requested_handle || verifyResult.requested_profile_url || "—"}
                          </p>
                          <p className="break-words">
                            结果：{verifyResult.resolved_nickname || "—"}
                            {verifyResult.resolved_user_id ? ` · ${verifyResult.resolved_user_id}` : ""}
                            {verifyResult.resolved_handle ? ` · ${verifyResult.resolved_handle}` : ""}
                          </p>
                          {verifyResult.note ? (
                            <p className="text-muted-foreground">{verifyResult.note}</p>
                          ) : null}
                          <div className="flex flex-wrap items-center gap-2">
                            {verifyResult.resolved_profile_url ? (
                              <a
                                href={assumeHttpUrl(verifyResult.resolved_profile_url) || verifyResult.resolved_profile_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-primary underline-offset-4 hover:underline"
                              >
                                打开抓取到的主页
                              </a>
                            ) : null}
                            {verifyResult.applied ? (
                              <span className="text-muted-foreground">已保存</span>
                            ) : verifyResult.status === "verified" || verifyResult.status === "mismatch" ? (
                              <Button
                                type="button"
                                size="sm"
                                variant={verifyResult.status === "mismatch" ? "destructive" : "outline"}
                                disabled={busy}
                                onClick={() =>
                                  verifyMutation.mutate({
                                    apply: true,
                                    confirmMismatch: verifyResult.status === "mismatch",
                                  })
                                }
                              >
                                {verifyMutation.isPending ? (
                                  <LoaderCircle className="animate-spin" />
                                ) : null}
                                {verifyResult.status === "mismatch" ? "确认采用并保存" : "采用并保存"}
                              </Button>
                            ) : null}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  {canCollect ? (
                    <div className="flex items-center justify-between gap-4 border px-3 py-2 sm:col-span-2">
                      <div>
                        <Label htmlFor="lead-fetch-profile">转换时抓取最新资料</Label>
                        <p className="text-xs text-muted-foreground">
                          {lead.matched_creator
                            ? "本地已有关联达人资料，可关闭以避免重复计费。"
                            : "本地没有关联达人资料，转换时必须抓取。"}
                        </p>
                      </div>
                      <Switch
                        id="lead-fetch-profile"
                        checked={fetchProfile}
                        onCheckedChange={setFetchProfile}
                        disabled={!editable || !lead.matched_creator}
                      />
                    </div>
                  ) : null}
                  {canCollect ? (
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
                  ) : null}
                  <div className="space-y-2">
                    <Label htmlFor="lead-owner">负责人</Label>
                    <Select
                      value={lead.owner_user_id == null ? "none" : String(lead.owner_user_id)}
                      onValueChange={(value) => ownerMutation.mutate(value === "none" ? null : Number(value))}
                      disabled={!editable || ownerMutation.isPending || dirty}
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
                      disabled={!editable || statusMutation.isPending || dirty}
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
                          {canCollect ? (
                            <>
                              匹配轮次{" "}
                              {lead.matched_round_name ||
                                (matchedRoundId != null ? `轮次 ${matchedRoundId}` : "—")}{" "}
                              · 方式 {matchedConfidence}
                            </>
                          ) : (
                            <>方式 {matchedConfidence}</>
                          )}
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
                            disabled={matchMutation.isPending || dirty}
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
                  {canCollect ? (
                    <div className="min-w-0">
                      <dt className="text-xs text-muted-foreground">来源轮次</dt>
                      <dd className="truncate">{lead.source_round_name || lead.source_round_id || "—"}</dd>
                    </div>
                  ) : null}
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
                  {canCollect && lead.rounds.length > 1 ? (
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
                    <h3 className="text-sm font-semibold">匹配与校验尝试</h3>
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
                                {attempt.provider === "round"
                                  ? canCollect
                                    ? "轮次匹配"
                                    : "匹配"
                                  : attempt.provider || "—"}{" "}
                                · {ATTEMPT_STATUS_LABELS[attempt.status] || attempt.status}
                                {attempt.confidence
                                  ? ` · ${
                                      canCollect
                                        ? MATCH_CONFIDENCE_LABELS[attempt.confidence] || attempt.confidence
                                        : attempt.confidence === "round_account_id"
                                          ? "自动匹配"
                                          : MATCH_CONFIDENCE_LABELS[attempt.confidence] || attempt.confidence
                                    }`
                                  : ""}
                              </span>
                              {attempt.status === "suggested" &&
                              attempt.candidate_user_id &&
                              editable &&
                              !lead.account_id ? (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={busy || dirty}
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
                {lead.identity_events.length > 0 ? (
                  <section className="space-y-2 border-t pt-4">
                    <h3 className="text-sm font-semibold">账号变更记录</h3>
                    <div className="divide-y border-y text-sm">
                      {lead.identity_events.slice(0, 6).map((event) => (
                        <div key={event.id} className="space-y-0.5 py-2">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="flex flex-wrap items-center gap-2">
                              <span>{IDENTITY_EVENT_LABELS[event.event_type] || event.event_type}</span>
                              <Badge variant="secondary" className="font-normal">
                                {IDENTITY_STATUS_LABELS[event.status] || event.status}
                              </Badge>
                            </span>
                            <span className="text-xs text-muted-foreground">
                              {fmtDateTime(event.created_at)}
                            </span>
                          </div>
                          <p className="text-xs text-muted-foreground">
                            {event.old_user_id || event.old_handle || "—"} →{" "}
                            {event.new_user_id || event.new_handle || "—"}
                            {event.provider ? ` · ${event.provider}` : ""}
                            {event.confidence
                              ? ` · ${
                                  IDENTITY_CONFIDENCE_LABELS[event.confidence] ||
                                  event.confidence
                                }`
                              : ""}
                          </p>
                          {event.note ? (
                            <p className="text-xs text-muted-foreground">{event.note}</p>
                          ) : null}
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
                !canEditLeads ? null : (
                  <span className="text-xs text-muted-foreground">
                    只读：线索由 {lead?.owner_username || "其他成员"} 负责
                  </span>
                )
              ) : (
                <>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="更多操作"
                        title="更多操作"
                        disabled={busy || dirty}
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
                  {canCollect ? (
                    <Button
                      onClick={() => setConfirmAction("convert")}
                      disabled={
                        !canIdentify ||
                        !hasRounds ||
                        !convertibleStatus ||
                        (fetchProfile && !providerReady) ||
                        busy ||
                        !lead
                      }
                      title={
                        !canIdentify
                          ? "填写平台账号、用户 ID 或主页 URL 后可转为达人"
                          : !hasRounds
                            ? "该线索没有关联轮次，无法转为达人"
                            : !convertibleStatus
                              ? "仅待核实/核实中的线索可以转换"
                              : fetchProfile && !providerReady
                                ? "当前没有可用的采集服务商，请先选择或配置"
                                : undefined
                      }
                    >
                      {convertMutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
                      {convertMutation.isPending ? "转换中…" : "转为达人"}
                    </Button>
                  ) : null}
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
                if (action === "convert") convertMutation.mutate(false);
                else if (action) statusMutation.mutate(action);
              }}
            >
              {confirmCopy.action}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={mismatchResult !== null}
        onOpenChange={(open) => !open && setMismatchResult(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>账号不一致，是否继续？</AlertDialogTitle>
            <AlertDialogDescription>
              {mismatchResult ? (
                <span className="block space-y-1">
                  <span className="block">
                    线索账号：
                    {mismatchResult.requested_user_id ||
                      mismatchResult.requested_handle ||
                      mismatchResult.requested_profile_url ||
                      "—"}
                  </span>
                  <span className="block">
                    抓取结果：
                    {mismatchResult.resolved_nickname || "—"}
                    {mismatchResult.resolved_user_id
                      ? ` · ${mismatchResult.resolved_user_id}`
                      : ""}
                    {mismatchResult.resolved_handle
                      ? ` · ${mismatchResult.resolved_handle}`
                      : ""}
                  </span>
                  <span className="block">
                    置信度：
                    {IDENTITY_CONFIDENCE_LABELS[mismatchResult.confidence] ||
                      mismatchResult.confidence ||
                      "—"}
                    {mismatchResult.note ? ` · ${mismatchResult.note}` : ""}
                  </span>
                  <span className="block">
                    确认是同一人后将使用抓取到的账号转换；否则请取消并修正账号。
                  </span>
                </span>
              ) : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setMismatchResult(null);
                convertMutation.mutate(true);
              }}
            >
              使用核实账号并转换
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={closeConfirmOpen} onOpenChange={setCloseConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle>
            <AlertDialogDescription>
              昵称、平台账号、平台用户 ID 和主页 URL 的修改尚未保存。
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
