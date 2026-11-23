import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, ExternalLink, Info, LoaderCircle, UserCheck } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { fmtDate, fmtDateTime, fmtNumber, safeHttpUrl, shorten } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import type { CreatorDetail, StatusDef } from "@/shared/types";
import { Badge } from "@/components/ui/badge";
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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "cn";

export function CreatorSheet({
  roundId,
  detailKey,
  statuses,
  onClose,
  onCancelSwitch,
}: {
  roundId: number;
  detailKey: string;
  statuses: StatusDef[];
  onClose: () => void;
  onCancelSwitch?: (roundId: number) => void;
}) {
  const queryClient = useQueryClient();
  const { refreshRounds } = useRound();
  const dirtyRef = useRef(false);
  const [activeRoundId, setActiveRoundId] = useState(roundId);
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false);
  const [switchConfirmOpen, setSwitchConfirmOpen] = useState(false);
  const [platform, userId] = detailKey.split("|");
  useEffect(() => {
    if (roundId === activeRoundId) return;
    if (dirtyRef.current) {
      setSwitchConfirmOpen(true);
      return;
    }
    setActiveRoundId(roundId);
  }, [roundId, activeRoundId]);
  const detail = useQuery({
    queryKey: ["creator", activeRoundId, platform, userId],
    queryFn: () => api.getCreator(activeRoundId, platform, userId),
    enabled: Boolean(platform && userId),
  });
  const profileHref = safeHttpUrl(detail.data?.profile_url);
  const enrichmentLabel = {
    not_fetched: "未抓取",
    partial: "部分完整",
    complete: "完整",
  }[detail.data?.enrichment_status ?? "not_fetched"];
  const requestClose = () => {
    if (dirtyRef.current) {
      setCloseConfirmOpen(true);
      return;
    }
    onClose();
  };

  return (
    <Sheet open onOpenChange={(open) => { if (!open) requestClose(); }}>
      <SheetContent className="w-full gap-0 p-0 data-[side=right]:sm:max-w-lg">
        <SheetHeader className="shrink-0 border-b px-4 py-4 pr-12">
          <div className="flex items-start gap-3">
            {detail.data?.avatar ? (
              <img
                src={detail.data.avatar}
                alt=""
                width={40}
                height={40}
                referrerPolicy="no-referrer"
                className="size-10 shrink-0 rounded-full border object-cover"
              />
            ) : null}
            <div className="min-w-0 flex-1">
              <SheetTitle className="break-words">{detail.data?.nickname || "达人详情"}</SheetTitle>
              <SheetDescription className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span>
                  {detail.data?.platform_label || platform}
                  {` · ${enrichmentLabel}`}
                </span>
                {profileHref ? (
                  <a
                    href={profileHref}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-foreground underline underline-offset-4"
                  >
                    打开主页
                    <ExternalLink className="size-3" />
                  </a>
                ) : null}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>
        {detail.isLoading ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <div className="space-y-3">
              <Skeleton className="h-8 w-1/2" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          </div>
        ) : detail.error ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <p className="text-sm text-destructive">
              {detail.error instanceof Error ? detail.error.message : "加载详情失败"}
            </p>
          </div>
        ) : detail.data ? (
          <CreatorDetailForm
            key={`${activeRoundId}:${platform}:${userId}`}
            roundId={activeRoundId}
            detail={detail.data}
            statuses={statuses}
            onSaved={async (updated) => {
              await queryClient.invalidateQueries({ queryKey: ["creators"] });
              await queryClient.invalidateQueries({ queryKey: ["facets"] });
              await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
              await queryClient.invalidateQueries({ queryKey: ["activity"] });
              await refreshRounds();
              queryClient.setQueryData(["creator", activeRoundId, platform, userId], updated);
            }}
            onDirtyChange={(dirty) => { dirtyRef.current = dirty; }}
          />
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            <p className="text-sm text-muted-foreground">
              未找到该达人，链接可能已失效。
            </p>
          </div>
        )}
        <AlertDialog open={closeConfirmOpen} onOpenChange={setCloseConfirmOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle>
              <AlertDialogDescription>采用状态、匹配方向、人工标签或备注尚未保存。</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>继续编辑</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                onClick={() => {
                  setCloseConfirmOpen(false);
                  dirtyRef.current = false;
                  onClose();
                }}
              >
                放弃并关闭
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <AlertDialog open={switchConfirmOpen} onOpenChange={setSwitchConfirmOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>放弃当前修改并切换轮次？</AlertDialogTitle>
              <AlertDialogDescription>当前轮次有未保存的标注。继续编辑可返回原轮次，切换后这些修改不会保留。</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel
                onClick={() => {
                  setSwitchConfirmOpen(false);
                  onCancelSwitch?.(activeRoundId);
                }}
              >
                返回原轮次
              </AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                onClick={() => {
                  dirtyRef.current = false;
                  setSwitchConfirmOpen(false);
                  setActiveRoundId(roundId);
                }}
              >
                放弃修改并切换
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </SheetContent>
    </Sheet>
  );
}

function CreatorDetailForm({
  roundId,
  detail,
  statuses,
  onSaved,
  onDirtyChange,
}: {
  roundId: number;
  detail: CreatorDetail;
  statuses: StatusDef[];
  onSaved: (detail: CreatorDetail) => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [status, setStatus] = useState(detail.status || "pending");
  const [direction, setDirection] = useState(detail.match_direction || "");
  const [tags, setTags] = useState(detail.tags_manual || "");
  const [remark, setRemark] = useState(detail.remark || "");
  const [descExpanded, setDescExpanded] = useState(false);
  const [pendingOwner, setPendingOwner] = useState<{ userId: number | null; username: string } | null>(null);
  const { user } = useAuth();
  const canEdit = true;
  const queryClient = useQueryClient();
  const loadedKey = useRef("");

  useEffect(() => {
    const key = `${detail.platform}|${detail.user_id}`;
    if (loadedKey.current === key) return;
    loadedKey.current = key;
    setStatus(detail.status || "pending");
    setDirection(detail.match_direction || "");
    setTags(detail.tags_manual || "");
    setRemark(detail.remark || "");
    setDescExpanded(false);
  }, [detail]);

  const dirty =
    status !== (detail.status || "pending") ||
    direction !== (detail.match_direction || "") ||
    tags !== (detail.tags_manual || "") ||
    remark !== (detail.remark || "");

  useEffect(() => {
    onDirtyChange(dirty);
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, onDirtyChange]);

  const mutation = useMutation({
    mutationFn: () => {
      if (!detail) throw new Error("达人资料未加载");
      return api.updateCreator(roundId, detail.platform, detail.user_id, {
        status,
        match_direction: direction,
        tags_manual: tags,
        remark,
        expected_status: detail.status,
        expected_updated_at: detail.updated_at || undefined,
      });
    },
    onSuccess: async (updated) => {
      await onSaved(updated);
      toast.success("已保存备注");
    },
    onError: async (error) => {
      toast.error(error instanceof Error ? error.message : "保存失败");
      await queryClient.invalidateQueries({ queryKey: ["creator", roundId, detail.platform, detail.user_id] });
      await queryClient.invalidateQueries({ queryKey: ["creators"] });
    },
  });

  const claimMutation = useMutation({
    mutationFn: (action: "claim" | "release") =>
      action === "claim"
        ? api.claimCreator(roundId, detail.platform, detail.user_id)
        : api.releaseCreator(roundId, detail.platform, detail.user_id),
    onSuccess: async (updated, action) => {
      await onSaved(updated);
      toast.success(action === "claim" ? "已设为跟进人" : "已结束跟进");
    },
    onError: async (error) => {
      toast.error(error instanceof Error ? error.message : "跟进人设置失败");
      await queryClient.invalidateQueries({ queryKey: ["creator", roundId, detail.platform, detail.user_id] });
      await queryClient.invalidateQueries({ queryKey: ["creators"] });
    },
  });

  const ownersQuery = useQuery({
    queryKey: ["creator-owners"],
    queryFn: api.getCreatorOwners,
    staleTime: 300_000,
  });
  const canAssignOwner =
    user?.role === "admin" ||
    (detail.owner_user_id != null && detail.owner_user_id === user?.id);
  const ownerChoices = [...(ownersQuery.data ?? [])];
  if (
    detail.owner_user_id != null &&
    !ownerChoices.some((owner) => owner.id === detail.owner_user_id)
  ) {
    ownerChoices.unshift({
      id: detail.owner_user_id,
      username: detail.owner_username || `用户 ${detail.owner_user_id}`,
      role: "",
      can_collect: 0,
    });
  }

  const assignOwnerMutation = useMutation({
    mutationFn: (ownerUserId: number | null) =>
      api.assignCreator(roundId, detail.platform, detail.user_id, ownerUserId),
    onSuccess: async (updated, ownerUserId) => {
      await onSaved(updated);
      const nextOwner = ownerChoices.find((owner) => owner.id === ownerUserId);
      toast.success(
        ownerUserId == null
          ? "已结束跟进"
          : `已转交给 ${nextOwner?.username || "他人"}`,
      );
    },
    onError: async (error) => {
      toast.error(error instanceof Error ? error.message : "跟进人调整失败");
      await queryClient.invalidateQueries({ queryKey: ["creator", roundId, detail.platform, detail.user_id] });
      await queryClient.invalidateQueries({ queryKey: ["creators"] });
    },
  });

  const metrics = useMemo(
    () => [
      ["粉丝", fmtNumber(detail.fans)],
      ["笔记数", fmtNumber(detail.posted)],
      ["总点赞", fmtNumber(detail.liked)],
      ["互动量", fmtNumber(detail.interaction)],
    ],
    [detail.fans, detail.interaction, detail.liked, detail.posted],
  );

  const creatorTags = detail.creator_tags ?? [];
  const autoTags = detail.auto_tags ?? [];
  const sourceKeywords = detail.source_keywords ?? [];
  const sourceLabels = detail.source_labels ?? [];
  const notes = detail.notes ?? [];

  const sampleStats = [
    ["笔记", detail.sample_notes],
    ["点赞", detail.sample_likes],
    ["收藏", detail.sample_collected],
    ["评论", detail.sample_comments],
    ["互动", detail.sample_interactions],
  ] as const;
  const hasSampleData = sampleStats.some(([, value]) => value > 0);

  const sourceRound =
    detail.data_round && detail.data_round !== detail.round_name
      ? detail.data_round
      : "";
  const cooperationStatusLabel = statuses.find((item) => item.code === detail.cooperation_status)?.label ?? detail.cooperation_status;
  const concreteMetadata: [string, string][] = [
    ["主档合作状态", cooperationStatusLabel],
    ["最新轮次审核", detail.round_status_label],
    ["粉丝层级", detail.fans_tier],
    ["账号 ID", detail.account_id],
    ["小红书号", detail.red_id],
    ["当前轮次", detail.round_name],
    ["来源轮次", sourceRound],
    ["IP 属地", detail.ip_location],
    ["关注数", detail.follows != null ? fmtNumber(detail.follows) : ""],
    ["总收藏", detail.collected != null ? fmtNumber(detail.collected) : ""],
    ["认证信息", detail.verify_info],
    ["合作主平台", detail.cooperation_platform],
    ["资料更新", detail.fetched_at ? fmtDateTime(detail.fetched_at) : ""],
    ["平台标签", creatorTags.join("、")],
    ["自动标签", autoTags.join("、")],
    ["来源标签", sourceLabels.join("、")],
    ["原始标题", detail.raw_title],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]?.trim()));
  const copyValue = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  return (
    <>
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-px border bg-border sm:grid-cols-4">
        {metrics.map(([label, value]) => (
          <div key={label} className="space-y-1 bg-background p-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </div>

      {detail.desc ? (
        <div className="space-y-1">
          <p className={cn("text-sm text-muted-foreground", !descExpanded && "line-clamp-3")}>
            {detail.desc}
          </p>
          {detail.desc.length > 120 ? (
            <button
              type="button"
              onClick={() => setDescExpanded((value) => !value)}
              className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              {descExpanded ? "收起" : "展开"}
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-3 border bg-muted/20 px-3 py-2.5">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              跟进人
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label="查看跟进人说明"
                    className="inline-flex size-4 items-center justify-center rounded-full transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                  >
                    <Info className="size-3" aria-hidden="true" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="max-w-xs">
                  跟进人仅表示当前跟进人，不限制编辑；未分配时首次保存标注或状态会自动归你跟进。
                </TooltipContent>
              </Tooltip>
            </span>
            {!canAssignOwner ? (
              <span className="text-sm font-medium">
                {detail.owner_username || (detail.owner_user_id ? "未知用户" : "未分配")}
                {detail.owner_user_id === user?.id ? "（我）" : ""}
              </span>
            ) : null}
            {detail.claimed_at ? (
              <span className="text-xs text-muted-foreground">{fmtDateTime(detail.claimed_at)} 起跟进</span>
            ) : null}
          </div>
          {canAssignOwner ? (
            <Select
              value={detail.owner_user_id == null ? "none" : String(detail.owner_user_id)}
              onValueChange={(value) => {
                const nextOwnerId = value === "none" ? null : Number(value);
                if (nextOwnerId === detail.owner_user_id) return;
                const nextOwner =
                  nextOwnerId === null
                    ? null
                    : ownerChoices.find((owner) => owner.id === nextOwnerId);
                setPendingOwner({
                  userId: nextOwnerId,
                  username: nextOwner?.username || "该成员",
                });
              }}
              disabled={assignOwnerMutation.isPending || ownersQuery.isLoading}
            >
              <SelectTrigger className="h-8 w-[190px]" aria-label="调整跟进人">
                <SelectValue placeholder="未分配" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">未分配</SelectItem>
                {ownerChoices.map((owner) => (
                  <SelectItem key={owner.id} value={String(owner.id)}>
                    {owner.username}
                    {owner.id === user?.id ? "（我）" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : detail.owner_user_id == null ? (
            <Button size="sm" onClick={() => claimMutation.mutate("claim")} disabled={claimMutation.isPending}>
              <UserCheck />
              {claimMutation.isPending ? "设置中…" : "我来跟进"}
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">仅管理员可调整跟进人</span>
          )}
        </div>
        {claimMutation.isError ? (
          <p className="text-sm text-destructive">
            {claimMutation.error instanceof Error ? claimMutation.error.message : "跟进人设置失败"}
          </p>
        ) : null}
        {assignOwnerMutation.isError ? (
          <p className="text-sm text-destructive">
            {assignOwnerMutation.error instanceof Error ? assignOwnerMutation.error.message : "跟进人调整失败"}
          </p>
        ) : null}
      </div>

      {sourceKeywords.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">本轮命中关键词（{sourceKeywords.length}）</p>
          <div className="flex flex-wrap gap-1.5">
            {sourceKeywords.map((keyword) => (
              <Badge key={keyword} variant="secondary" className="font-normal">
                {keyword}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}

      {hasSampleData ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-y py-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">本轮样本</span>
          {sampleStats.map(([label, value]) => (
            <span key={label}>{label} {fmtNumber(value)}</span>
          ))}
        </div>
      ) : null}

      {detail.top_title ? (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">代表作</h3>
          <div className="flex items-start justify-between gap-3">
            {safeHttpUrl(detail.top_url) ? (
              <a
                href={safeHttpUrl(detail.top_url)}
                target="_blank"
                rel="noreferrer"
                className="min-w-0 break-words text-sm underline underline-offset-4 hover:text-primary"
              >
                {detail.top_title}
                <ExternalLink className="ml-1 inline size-3 align-[-1px]" />
              </a>
            ) : (
              <p className="min-w-0 break-words text-sm">{detail.top_title}</p>
            )}
            {detail.top_liked != null && detail.top_liked > 0 ? (
              <span className="shrink-0 text-xs text-muted-foreground">{fmtNumber(detail.top_liked)} 赞</span>
            ) : null}
          </div>
        </section>
      ) : null}

      <section className="space-y-3">
        <h3 className="text-sm font-semibold">基础资料</h3>
        {concreteMetadata.length > 0 ? (
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            {concreteMetadata.map(([label, value]) => (
              <div key={label} className="min-w-0">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                {label === "账号 ID" ? (
                  <dd className="flex min-w-0 items-center gap-1">
                    <span className="truncate text-sm" title={value}>{value}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label="复制账号 ID"
                      onClick={() => void copyValue(value)}
                    >
                      <Copy className="size-3" />
                    </Button>
                  </dd>
                ) : (
                  <dd className="truncate text-sm" title={value}>{value}</dd>
                )}
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">暂无更多资料</p>
        )}
      </section>

      <section className="space-y-3 border-t pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">人工标注</h3>
          <p className="text-xs text-muted-foreground">
            跟进人仅表示当前跟进人，不限制编辑；未分配时首次保存标注或状态会自动归你跟进。
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="creator-status">采用状态</Label>
            <Select
              value={status}
              onValueChange={(value) => {
                setStatus(value);
                if (mutation.isError) mutation.reset();
              }}
              disabled={!canEdit || mutation.isPending}
            >
              <SelectTrigger id="creator-status" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {statuses.map((item) => (
                  <SelectItem key={item.code} value={item.code}>{item.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="creator-direction">匹配方向</Label>
            <Input
              id="creator-direction"
              value={direction}
              disabled={!canEdit || mutation.isPending}
              onChange={(event) => {
                setDirection(event.target.value);
                if (mutation.isError) mutation.reset();
              }}
              autoComplete="off"
            />
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="creator-tags">人工标签</Label>
            <Input
              id="creator-tags"
              value={tags}
              disabled={!canEdit || mutation.isPending}
              onChange={(event) => {
                setTags(event.target.value);
                if (mutation.isError) mutation.reset();
              }}
              placeholder="多个标签用逗号分隔"
              autoComplete="off"
            />
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="creator-remark">备注</Label>
            <Textarea
              id="creator-remark"
              value={remark}
              disabled={!canEdit || mutation.isPending}
              onChange={(event) => {
                setRemark(event.target.value);
                if (mutation.isError) mutation.reset();
              }}
              rows={3}
            />
          </div>
        </div>
      </section>

      <details className="group border-t pt-4">
        <summary className="cursor-pointer text-sm font-semibold">
          样本笔记（{notes.length}）
        </summary>
        {notes.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">没有样本笔记。</p>
        ) : (
          <div className="mt-3 divide-y border-y">
            {notes.map((note) => {
              const title = shorten(note.title, 72) || "无标题";
              return (
                <div key={note.note_id} className="space-y-1 py-3">
                  {safeHttpUrl(note.url) ? (
                    <a
                      href={safeHttpUrl(note.url)}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-start gap-1 text-sm font-medium underline-offset-4 hover:underline"
                    >
                      {title}
                      <ExternalLink className="mt-0.5 size-3 shrink-0" />
                    </a>
                  ) : (
                    <p className="text-sm font-medium">{title}</p>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {note.type || "笔记"} · {fmtDate(note.published_at)} · 赞 {fmtNumber(note.liked)} · 藏 {fmtNumber(note.collected)} · 评 {fmtNumber(note.comments)} · 转 {fmtNumber(note.shares)}
                  </p>
                  {note.keywords.length > 0 ? (
                    <p className="text-xs text-muted-foreground">{note.keywords.join(" / ")}</p>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </details>
    </div>
    </div>
    <div className="shrink-0 border-t bg-background px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p
          className={cn(
            "min-w-0 flex-1 text-xs",
            mutation.isError
              ? "text-destructive"
              : dirty
                ? "text-amber-600"
                : "text-muted-foreground",
          )}
        >
          {mutation.isError
            ? mutation.error instanceof Error
              ? mutation.error.message
              : "保存失败"
            : dirty
              ? "有未保存的修改"
              : "当前标注已是最新"}
        </p>
        <Button
          className="shrink-0"
          onClick={() => mutation.mutate()}
          disabled={!canEdit || !dirty || mutation.isPending}
        >
          {mutation.isPending ? (
            <>
              <LoaderCircle className="animate-spin" />
              保存中…
            </>
          ) : (
            "保存标注"
          )}
        </Button>
      </div>
    </div>
    <AlertDialog open={pendingOwner !== null} onOpenChange={(open) => !open && setPendingOwner(null)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>调整跟进人？</AlertDialogTitle>
          <AlertDialogDescription>
            {pendingOwner?.userId == null
              ? `将结束 ${detail.owner_username || "当前跟进人"} 对这位达人的跟进，所有轮次生效；不限制编辑。`
              : `将这位达人的跟进人从 ${detail.owner_username || "未分配"} 转交给 ${pendingOwner?.username}，所有轮次生效；不限制编辑。`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            onClick={(event) => {
              event.preventDefault();
              if (!pendingOwner) return;
              assignOwnerMutation.mutate(pendingOwner.userId);
              setPendingOwner(null);
            }}
          >
            确认调整
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    </>
  );
}
