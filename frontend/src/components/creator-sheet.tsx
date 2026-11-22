import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, LoaderCircle, UserCheck, UserMinus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { fmtDate, fmtDateTime, fmtNumber, safeHttpUrl, shorten } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import type { CreatorDetail, StatusDef } from "@/shared/types";
import { Badge } from "@/components/ui/badge";
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

export function CreatorSheet({
  roundId,
  detailKey,
  statuses,
  onClose,
}: {
  roundId: number;
  detailKey: string;
  statuses: StatusDef[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { refreshRounds } = useRound();
  const [platform, userId] = detailKey.split("|");
  const detail = useQuery({
    queryKey: ["creator", roundId, platform, userId],
    queryFn: () => api.getCreator(roundId, platform, userId),
    enabled: Boolean(platform && userId),
  });
  const profileHref = safeHttpUrl(detail.data?.profile_url);

  return (
    <Sheet open onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent className="w-full gap-0 p-0 data-[side=right]:sm:max-w-lg">
        <SheetHeader className="shrink-0 border-b px-4 py-4 pr-12">
          <div className="flex items-start gap-3">
            {detail.data?.avatar ? (
              <img
                src={detail.data.avatar}
                alt=""
                className="size-10 shrink-0 rounded-full border object-cover"
              />
            ) : null}
            <div className="min-w-0 flex-1">
              <SheetTitle className="break-words">{detail.data?.nickname || "达人详情"}</SheetTitle>
              <SheetDescription className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span>
                  {detail.data?.platform_label || platform}
                  {detail.data?.fetched_at ? " · 资料已补全" : " · 资料未补全"}
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
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {detail.isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-8 w-1/2" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          ) : detail.error ? (
            <p className="text-sm text-destructive">
              {detail.error instanceof Error ? detail.error.message : "加载详情失败"}
            </p>
          ) : detail.data ? (
            <CreatorDetailForm
              roundId={roundId}
              detail={detail.data}
              statuses={statuses}
              onSaved={async (updated) => {
                await queryClient.invalidateQueries({ queryKey: ["creators"] });
                await queryClient.invalidateQueries({ queryKey: ["facets"] });
                await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
                await refreshRounds();
                queryClient.setQueryData(["creator", roundId, platform, userId], updated);
              }}
            />
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function CreatorDetailForm({
  roundId,
  detail,
  statuses,
  onSaved,
}: {
  roundId: number;
  detail: CreatorDetail;
  statuses: StatusDef[];
  onSaved: (detail: CreatorDetail) => Promise<void>;
}) {
  const [status, setStatus] = useState(detail.status || "pending");
  const [direction, setDirection] = useState(detail.match_direction || "");
  const [tags, setTags] = useState(detail.tags_manual || "");
  const [remark, setRemark] = useState(detail.remark || "");
  const { user } = useAuth();
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
  }, [detail]);

  const dirty =
    status !== (detail.status || "pending") ||
    direction !== (detail.match_direction || "") ||
    tags !== (detail.tags_manual || "") ||
    remark !== (detail.remark || "");

  const mutation = useMutation({
    mutationFn: () =>
      api.updateCreator(roundId, detail.platform, detail.user_id, {
        status,
        match_direction: direction,
        tags_manual: tags,
        remark,
      }),
    onSuccess: async (updated) => {
      await onSaved(updated);
      toast.success("已保存备注");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "保存失败"),
  });

  const claimMutation = useMutation({
    mutationFn: (action: "claim" | "release") =>
      action === "claim"
        ? api.claimCreator(roundId, detail.platform, detail.user_id)
        : api.releaseCreator(roundId, detail.platform, detail.user_id),
    onSuccess: async (updated, action) => {
      await onSaved(updated);
      toast.success(action === "claim" ? "已认领该达人" : "已释放该达人");
    },
    onError: async (error) => {
      toast.error(error instanceof Error ? error.message : "认领操作失败");
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

  const sampleStats = [
    ["笔记", detail.sample_notes],
    ["点赞", detail.sample_likes],
    ["收藏", detail.sample_collected],
    ["评论", detail.sample_comments],
    ["互动", detail.sample_interactions],
  ] as const;
  const hasSampleData = sampleStats.some(([, value]) => value > 0);

  const concreteMetadata: [string, string][] = [
    ["粉丝层级", detail.fans_tier],
    ["账号 ID", detail.account_id],
    ["小红书号", detail.red_id],
    ["当前轮次", detail.round_name],
    ["数据轮次", detail.data_round],
    ["IP 属地", detail.ip_location],
    ["关注数", detail.follows != null ? fmtNumber(detail.follows) : ""],
    ["总收藏", detail.collected != null ? fmtNumber(detail.collected) : ""],
    ["认证信息", detail.verify_info],
    ["合作主平台", detail.cooperation_platform],
    ["资料更新", detail.fetched_at ? fmtDateTime(detail.fetched_at) : ""],
    ["平台标签", detail.creator_tags.join("、")],
    ["自动标签", detail.auto_tags.join("、")],
    ["原始标题", detail.raw_title],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]?.trim()));

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-px border bg-border sm:grid-cols-4">
        {metrics.map(([label, value]) => (
          <div key={label} className="space-y-1 bg-background p-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </div>

      {detail.desc ? <p className="text-sm text-muted-foreground">{detail.desc}</p> : null}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-3 border bg-muted/20 px-3 py-2.5">
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-xs text-muted-foreground">经手人</span>
            <span className="text-sm font-medium">
              {detail.owner_username || (detail.owner_user_id ? "未知用户" : "未认领")}
              {detail.owner_user_id === user?.id ? "（我）" : ""}
            </span>
            {detail.claimed_at ? (
              <span className="text-xs text-muted-foreground">{fmtDateTime(detail.claimed_at)} 认领</span>
            ) : null}
          </div>
          {detail.owner_user_id == null ? (
            <Button size="sm" onClick={() => claimMutation.mutate("claim")} disabled={claimMutation.isPending}>
              <UserCheck />
              {claimMutation.isPending ? "认领中…" : "认领"}
            </Button>
          ) : detail.owner_user_id === user?.id || user?.role === "admin" ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => claimMutation.mutate("release")}
              disabled={claimMutation.isPending}
            >
              <UserMinus />
              释放
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">
              已被 {detail.owner_username || "其他成员"} 认领
            </span>
          )}
        </div>
        {claimMutation.isError ? (
          <p className="text-sm text-destructive">
            {claimMutation.error instanceof Error ? claimMutation.error.message : "认领操作失败"}
          </p>
        ) : null}
      </div>

      {detail.source_keywords.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">命中关键词</p>
          <div className="flex flex-wrap gap-1.5">
            {detail.source_keywords.map((keyword) => (
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
                <dd className="truncate text-sm" title={value}>{value}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">暂无更多资料</p>
        )}
      </section>

      <section className="space-y-3 border-t pt-4">
        <h3 className="text-sm font-semibold">人工标注</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="creator-status">采用状态</Label>
            <Select
              value={status}
              onValueChange={(value) => {
                setStatus(value);
                if (mutation.isError) mutation.reset();
              }}
              disabled={mutation.isPending}
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
              disabled={mutation.isPending}
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
              disabled={mutation.isPending}
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
              disabled={mutation.isPending}
              onChange={(event) => {
                setRemark(event.target.value);
                if (mutation.isError) mutation.reset();
              }}
              rows={3}
            />
          </div>
        </div>
        {mutation.isError ? (
          <p className="text-sm text-destructive">
            {mutation.error instanceof Error ? mutation.error.message : "保存失败"}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className={`text-xs ${dirty ? "text-amber-600" : "text-muted-foreground"}`}>
            {dirty ? "有未保存的修改" : "当前标注已是最新"}
          </p>
          <Button onClick={() => mutation.mutate()} disabled={!dirty || mutation.isPending}>
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
      </section>

      <section className="space-y-3 border-t pt-4">
        <h3 className="text-sm font-semibold">样本笔记</h3>
        {detail.notes.length === 0 ? (
          <p className="text-sm text-muted-foreground">没有样本笔记。</p>
        ) : (
          <div className="divide-y border-y">
            {detail.notes.map((note) => {
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
      </section>
    </div>
  );
}
