import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, LoaderCircle, MoreHorizontal } from "lucide-react";
import { toast } from "sonner";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
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

type ConfirmAction = "convert" | "duplicate" | "discarded";

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
  const query = useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => api.getLead(leadId),
  });
  const lead = query.data;
  const [accountId, setAccountId] = useState("");
  const [profileUrl, setProfileUrl] = useState("");
  const [nickname, setNickname] = useState("");

  useEffect(() => {
    if (!lead) return;
    setAccountId(lead.account_id || "");
    setProfileUrl(lead.profile_url || "");
    setNickname(lead.nickname || "");
  }, [lead?.id, lead?.account_id, lead?.profile_url, lead?.nickname]);

  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["lead", leadId] });
    await queryClient.invalidateQueries({ queryKey: ["leads"] });
    await queryClient.invalidateQueries({ queryKey: ["lead-facets"] });
    await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    await queryClient.invalidateQueries({ queryKey: ["creators"] });
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateLead(leadId, {
        nickname: nickname.trim(),
        account_id: accountId.trim(),
        profile_url: profileUrl.trim(),
      }),
    onSuccess: async () => {
      toast.success("线索资料已保存");
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
          nickname: nickname.trim(),
          account_id: accountId.trim(),
          profile_url: profileUrl.trim(),
        });
      }
      return api.convertLead(leadId, {
        account_id: accountId.trim(),
        profile_url: profileUrl.trim(),
        fetch: true,
      });
    },
    onSuccess: async () => {
      toast.success("线索已转为达人");
      await refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "转换失败"),
  });

  const locked = lead?.status === "converted";
  const dirty =
    Boolean(lead) &&
    (nickname !== (lead?.nickname || "") ||
      accountId !== (lead?.account_id || "") ||
      profileUrl !== (lead?.profile_url || ""));
  const canIdentify = Boolean(accountId.trim() || profileUrl.trim());
  const hasRounds = Boolean(lead && (lead.rounds.length > 0 || lead.source_round_id != null));
  const busy =
    saveMutation.isPending ||
    statusMutation.isPending ||
    ownerMutation.isPending ||
    convertMutation.isPending;

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
  const convertedRoundId = lead?.rounds[0]?.round_id ?? lead?.source_round_id ?? null;
  const creatorHref =
    locked && convertedUserId && convertedRoundId != null
      ? `/creators?creator=${encodeURIComponent(
          `${lead.converted_platform || lead.platform}|${convertedUserId}`,
        )}&creator_round=${convertedRoundId}`
      : null;

  const profileHref = assumeHttpUrl(profileUrl);

  const footerHint = convertMutation.isPending
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
                : "资料已保存";

  const confirmCopy =
    confirmAction === "convert"
      ? {
          title: "转为达人？",
          description: `将按${
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
                    <Label>平台</Label>
                    <div className="flex h-8 items-center text-sm">
                      {lead.platform_label || lead.platform}
                    </div>
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
                    disabled={!canIdentify || !hasRounds || busy || !lead}
                    title={
                      !canIdentify
                        ? "填写真实账号 ID 或主页 URL 后可转为达人"
                        : !hasRounds
                          ? "该线索没有关联轮次，无法转为达人"
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
