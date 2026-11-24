import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, FileUp, LoaderCircle, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, downloadFile } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
import { fmtDateTime, safeHttpUrl } from "@/shared/format";
import type {
  CooperationCase,
  CooperationCaseDetail,
  CooperationStatusDef,
  CooperationSummary,
  CooperationWork,
} from "@/shared/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { CooperationStatusBadge } from "@/components/cooperation-status-badge";

const ATTACHMENT_CATEGORIES = [
  { value: "contract", label: "合同/协议" },
  { value: "quote", label: "报价/沟通" },
  { value: "screenshot", label: "截图" },
  { value: "settlement", label: "结算凭证" },
  { value: "other", label: "其他" },
] as const;

const EVENT_LABELS: Record<string, string> = {
  created: "创建合作",
  status_changed: "更新状态",
  status_corrected: "更正状态",
  migrated: "迁移记录",
  note_added: "添加记录",
  summary_updated: "更新摘要",
  attachment_added: "上传附件",
  attachment_removed: "删除附件",
  work_added: "添加作品",
  work_updated: "更新作品",
  work_removed: "删除作品",
};

type WorkDraft = {
  platform: string;
  url: string;
  title: string;
  published_at: string;
  note: string;
};

const EMPTY_WORK: WorkDraft = {
  platform: "",
  url: "",
  title: "",
  published_at: "",
  note: "",
};

function formatSize(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

function caseStatusLabel(
  statuses: CooperationStatusDef[],
  code: string,
  fallback?: string,
): string {
  return statuses.find((item) => item.code === code)?.label ?? fallback ?? code;
}

export function CooperationRecords({
  platform,
  userId,
  creatorStatus,
  statuses,
  cases,
  summary,
  onChanged,
}: {
  platform: string;
  userId: string;
  creatorStatus: string;
  statuses: CooperationStatusDef[];
  cases: CooperationCase[];
  summary: CooperationSummary;
  onChanged: () => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const activeCase = useMemo(() => cases.find((item) => item.active), [cases]);
  const [selectedCaseId, setSelectedCaseId] = useState<number | null>(
    activeCase?.id ?? cases[0]?.id ?? null,
  );
  const [statusDialogOpen, setStatusDialogOpen] = useState(false);
  const [completeDialogOpen, setCompleteDialogOpen] = useState(false);

  useEffect(() => {
    if (!cases.some((item) => item.id === selectedCaseId)) {
      setSelectedCaseId(activeCase?.id ?? cases[0]?.id ?? null);
    }
  }, [activeCase, cases, selectedCaseId]);

  const detailQuery = useQuery({
    queryKey: ["cooperation-case", selectedCaseId],
    queryFn: () => api.getCooperationCase(selectedCaseId as number),
    enabled: selectedCaseId !== null,
  });

  const refresh = async (caseId?: number) => {
    if (caseId) setSelectedCaseId(caseId);
    await onChanged();
    if (selectedCaseId || caseId) {
      await queryClient.invalidateQueries({
        queryKey: ["cooperation-case", caseId ?? selectedCaseId],
      });
    }
  };

  const createMutation = useMutation({
    mutationFn: () =>
      api.createCooperationCase({
        platform,
        user_id: userId,
      }),
    onSuccess: async (created) => {
      toast.success("已开始新的合作记录");
      await refresh(created.case.id);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "开启合作失败");
    },
  });

  const latest = cases[0];
  const nextSeq = (latest?.seq ?? 0) + 1;
  const detail = detailQuery.data;

  return (
    <section className="space-y-3 border-y py-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <h3 className="text-sm font-semibold">合作记录</h3>
          <p className="text-xs text-muted-foreground">
            每次合作独立记录状态、备注、附件和作品；同一达人最多一个进行中合作。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">累计 {summary.cooperation_count} 次</Badge>
          {summary.cooperation_status ? (
            <CooperationStatusBadge
              status={summary.cooperation_status}
              label={caseStatusLabel(
                statuses,
                summary.cooperation_status,
                summary.cooperation_status,
              )}
            />
          ) : null}
          {!activeCase ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => createMutation.mutate()}
              disabled={
                createMutation.isPending ||
                (cases.length === 0 && creatorStatus !== "adopted")
              }
              title={
                cases.length === 0 && creatorStatus !== "adopted"
                  ? "达人被采用后才能开始合作"
                  : undefined
              }
            >
              {createMutation.isPending ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <Plus />
              )}
              {cases.length === 0 ? "开始合作" : `开启第 ${nextSeq} 次合作`}
            </Button>
          ) : null}
        </div>
      </div>

      {cases.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {creatorStatus === "adopted"
            ? "还没有合作记录，点击“开始合作”进入首次跟进。"
            : "达人被采用后才能开始合作。"}
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {cases.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedCaseId(item.id)}
                className={
                  item.id === selectedCaseId
                    ? "rounded-md border border-foreground/30 bg-muted px-2.5 py-1.5 text-left"
                    : "rounded-md border bg-background px-2.5 py-1.5 text-left hover:bg-muted/40"
                }
              >
                <span className="flex items-center gap-2 text-xs">
                  <span className="font-medium">第 {item.seq} 次</span>
                  <CooperationStatusBadge
                    status={item.status}
                    label={item.status_label}
                  />
                </span>
                <span className="mt-1 block text-[11px] text-muted-foreground">
                  {item.active ? "进行中" : "已结束"}
                  {` · 作品 ${item.work_count} · 附件 ${item.attachment_count}`}
                </span>
              </button>
            ))}
          </div>

          {detailQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="animate-spin" /> 加载合作记录
            </div>
          ) : detailQuery.error ? (
            <p className="text-sm text-destructive">
              {detailQuery.error instanceof Error
                ? detailQuery.error.message
                : "加载合作记录失败"}
            </p>
          ) : detail ? (
            <CaseDetail
              detail={detail}
              statuses={statuses}
              onRefresh={refresh}
              onOpenStatus={() => setStatusDialogOpen(true)}
              onOpenComplete={() => setCompleteDialogOpen(true)}
            />
          ) : null}
        </>
      )}

      {statusDialogOpen && detail && detail.case.active ? (
        <StatusDialog
          detail={detail}
          statuses={statuses}
          open={statusDialogOpen}
          onOpenChange={setStatusDialogOpen}
          onDone={() => refresh(detail.case.id)}
        />
      ) : null}
      {completeDialogOpen && detail && detail.case.active ? (
        <CompletionDialog
          detail={detail}
          open={completeDialogOpen}
          onOpenChange={setCompleteDialogOpen}
          onDone={() => refresh(detail.case.id)}
        />
      ) : null}
    </section>
  );
}

function CaseDetail({
  detail,
  statuses,
  onRefresh,
  onOpenStatus,
  onOpenComplete,
}: {
  detail: CooperationCaseDetail;
  statuses: CooperationStatusDef[];
  onRefresh: (caseId?: number) => Promise<void>;
  onOpenStatus: () => void;
  onOpenComplete: () => void;
}) {
  const item = detail.case;
  const active = item.active;
  const { user } = useAuth();
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [editingWork, setEditingWork] = useState<CooperationWork | null>(null);
  const [summary, setSummary] = useState(item.summary || "");
  const [note, setNote] = useState("");
  const [work, setWork] = useState<WorkDraft>({ ...EMPTY_WORK });
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState<string>("other");
  const [description, setDescription] = useState("");

  useEffect(() => {
    setSummary(item.summary || "");
  }, [item.id, item.summary]);

  const summaryMutation = useMutation({
    mutationFn: () =>
      api.updateCooperationCaseSummary(
        item.id,
        summary,
        item.updated_at,
      ),
    onSuccess: async () => {
      toast.success("合作摘要已保存");
      await onRefresh(item.id);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "保存失败");
    },
  });

  const noteMutation = useMutation({
    mutationFn: () => api.addCooperationNote(item.id, note),
    onSuccess: async () => {
      setNote("");
      toast.success("已添加合作记录");
      await onRefresh(item.id);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "添加失败");
    },
  });

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("请选择附件");
      return api.uploadCooperationAttachment(
        item.id,
        file,
        category,
        description,
      );
    },
    onSuccess: async () => {
      setFile(null);
      setDescription("");
      toast.success("附件已上传");
      await onRefresh(item.id);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "上传失败");
    },
  });

  const deleteAttachmentMutation = useMutation({
    mutationFn: (attachmentId: number) =>
      api.deleteCooperationAttachment(attachmentId),
    onSuccess: async () => {
      toast.success("附件已删除");
      await onRefresh(item.id);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "删除失败");
    },
  });

  const addWorkMutation = useMutation({
    mutationFn: () => api.addCooperationWork(item.id, work),
    onSuccess: async () => {
      setWork({ ...EMPTY_WORK });
      toast.success("作品已添加");
      await onRefresh(item.id);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "添加作品失败");
    },
  });

  const deleteWorkMutation = useMutation({
    mutationFn: (workId: number) => api.deleteCooperationWork(workId),
    onSuccess: async () => {
      toast.success("作品已删除");
      await onRefresh(item.id);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "删除作品失败");
    },
  });

  return (
    <div className="space-y-4 rounded-md border bg-muted/15 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">第 {item.seq} 次合作</span>
            <CooperationStatusBadge
              status={item.status}
              label={item.status_label}
            />
            <Badge variant="outline">作品 {detail.works.length}</Badge>
            <Badge variant="outline">附件 {detail.attachments.length}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            开始 {fmtDateTime(item.started_at)}
            {item.completed_at ? ` · 完成 ${fmtDateTime(item.completed_at)}` : ""}
            {item.closed_at ? ` · 结束 ${fmtDateTime(item.closed_at)}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {active ? (
            <>
              <Button size="sm" variant="outline" onClick={onOpenStatus}>
                更新状态
              </Button>
              <Button size="sm" onClick={onOpenComplete}>
                完成合作
              </Button>
            </>
          ) : (
            <Badge variant="secondary">已结束，可继续补充证据</Badge>
          )}
          {!active && user?.role === "admin" ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setCorrectionOpen(true)}
            >
              更正状态
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              void downloadFile(
                `/api/cooperation-cases/${item.id}/export`,
                `cooperation_case_${item.id}.zip`,
              ).catch((error) => {
                toast.error(error instanceof Error ? error.message : "导出失败");
              });
            }}
          >
            <Download /> 导出档案
          </Button>
        </div>
      </div>

      {!active && detail.works.length === 0 ? (
        <p className="text-xs text-amber-600">
          该合作已完成，但还没有登记作品链接；建议补充以完善证据链。
        </p>
      ) : null}

      <div className="space-y-2">
        <Label htmlFor={`case-summary-${item.id}`}>合作摘要</Label>
        <Textarea
          id={`case-summary-${item.id}`}
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
          rows={3}
          placeholder="合作方式、报价、交付约定等"
          disabled={summaryMutation.isPending}
        />
        <Button
          size="sm"
          variant="outline"
          onClick={() => summaryMutation.mutate()}
          disabled={summaryMutation.isPending || summary === item.summary}
        >
          {summaryMutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
          保存摘要
        </Button>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">时间线</p>
        {detail.events.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无记录。</p>
        ) : (
          <ol className="space-y-2 border-l pl-3">
            {detail.events.map((event) => (
              <li key={event.id} className="text-xs">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="font-medium">
                    {EVENT_LABELS[event.event_type] ?? event.event_type}
                  </span>
                  {event.status_from || event.status_to ? (
                    <span className="text-muted-foreground">
                      {event.from_status_label} → {event.to_status_label}
                    </span>
                  ) : null}
                  <span className="text-muted-foreground">
                    {fmtDateTime(event.created_at)}
                    {event.actor_username ? ` · ${event.actor_username}` : ""}
                  </span>
                </div>
                {event.note ? (
                  <p className="mt-0.5 whitespace-pre-wrap text-sm text-muted-foreground">
                    {event.note}
                  </p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor={`case-note-${item.id}`}>追加记录</Label>
        <div className="flex items-start gap-2">
          <Textarea
            id={`case-note-${item.id}`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            rows={2}
            placeholder="补充沟通、交付、结算等说明"
          />
          <Button
            size="sm"
            onClick={() => noteMutation.mutate()}
            disabled={noteMutation.isPending || !note.trim()}
          >
            {noteMutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            添加
          </Button>
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">附件</p>
        {detail.attachments.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无附件。</p>
        ) : (
          <ul className="space-y-1.5">
            {detail.attachments.map((attachment) => (
              <li
                key={attachment.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded border bg-background px-2 py-1.5 text-xs"
              >
                <div className="min-w-0">
                  <a
                    href={attachment.download_url}
                    className="inline-flex max-w-full items-center gap-1 font-medium underline underline-offset-4"
                  >
                    <span className="truncate">{attachment.original_name}</span>
                    <ExternalLink className="size-3 shrink-0" />
                  </a>
                  <span className="ml-1 text-muted-foreground">
                    {attachment.category_label} · {formatSize(attachment.size_bytes)}
                    {attachment.description ? ` · ${attachment.description}` : ""}
                  </span>
                  <p className="mt-0.5 text-muted-foreground">
                    {fmtDateTime(attachment.created_at)}
                    {attachment.uploaded_by_username
                      ? ` · ${attachment.uploaded_by_username}`
                      : ""}
                  </p>
                </div>
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-7"
                  aria-label="删除附件"
                  onClick={() =>
                    deleteAttachmentMutation.mutate(attachment.id)
                  }
                  disabled={deleteAttachmentMutation.isPending}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="grid gap-2 rounded border bg-background p-2 sm:grid-cols-[1fr_160px]">
          <Input
            type="file"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="text-xs"
          />
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ATTACHMENT_CATEGORIES.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="附件说明（可选）"
            className="text-xs sm:col-span-2"
          />
          <Button
            size="sm"
            variant="outline"
            className="w-fit"
            onClick={() => uploadMutation.mutate()}
            disabled={uploadMutation.isPending || !file}
          >
            {uploadMutation.isPending ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <FileUp />
            )}
            上传附件
          </Button>
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">作品链接</p>
        {detail.works.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂未登记作品。</p>
        ) : (
          <ul className="space-y-1.5">
            {detail.works.map((workItem) => (
              <li
                key={workItem.id}
                className="flex flex-wrap items-start justify-between gap-2 rounded border bg-background px-2 py-1.5 text-xs"
              >
                <div className="min-w-0">
                  {safeHttpUrl(workItem.url) ? (
                    <a
                      href={safeHttpUrl(workItem.url)}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex max-w-full items-center gap-1 font-medium underline underline-offset-4"
                    >
                      <span className="truncate">
                        {workItem.title || workItem.url}
                      </span>
                      <ExternalLink className="size-3 shrink-0" />
                    </a>
                  ) : (
                    <span>{workItem.title || workItem.url}</span>
                  )}
                  <p className="mt-0.5 text-muted-foreground">
                    {workItem.platform || "未填平台"}
                    {workItem.published_at ? ` · ${workItem.published_at}` : ""}
                    {workItem.note ? ` · ${workItem.note}` : ""}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-7"
                    aria-label="编辑作品"
                    onClick={() => setEditingWork(workItem)}
                  >
                    <Pencil className="size-3.5" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-7"
                    aria-label="删除作品"
                    onClick={() => deleteWorkMutation.mutate(workItem.id)}
                    disabled={deleteWorkMutation.isPending}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="grid gap-2 rounded border bg-background p-2 sm:grid-cols-2">
          <Input
            value={work.url}
            onChange={(event) => setWork({ ...work, url: event.target.value })}
            placeholder="作品链接（http/https）"
            className="text-xs sm:col-span-2"
          />
          <Input
            value={work.title}
            onChange={(event) => setWork({ ...work, title: event.target.value })}
            placeholder="作品标题（可选）"
            className="text-xs"
          />
          <Input
            value={work.platform}
            onChange={(event) =>
              setWork({ ...work, platform: event.target.value })
            }
            placeholder="平台（可选）"
            className="text-xs"
          />
          <Input
            value={work.published_at}
            onChange={(event) =>
              setWork({ ...work, published_at: event.target.value })
            }
            placeholder="发布时间（可选）"
            className="text-xs"
          />
          <Input
            value={work.note}
            onChange={(event) => setWork({ ...work, note: event.target.value })}
            placeholder="备注（可选）"
            className="text-xs"
          />
          <Button
            size="sm"
            variant="outline"
            className="w-fit"
            onClick={() => addWorkMutation.mutate()}
            disabled={addWorkMutation.isPending || !work.url.trim()}
          >
            {addWorkMutation.isPending ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <Plus />
            )}
            添加作品
          </Button>
        </div>
      </div>

      {!active ? (
        <p className="text-[11px] text-muted-foreground">
          已完成或已取消的合作仍可补充备注、附件和作品；状态如有误，请联系管理员更正。
        </p>
      ) : null}

      {correctionOpen ? (
        <CorrectionDialog
          detail={detail}
          statuses={statuses}
          open={correctionOpen}
          onOpenChange={setCorrectionOpen}
          onDone={() => onRefresh(item.id)}
        />
      ) : null}

      {editingWork ? (
        <WorkEditDialog
          work={editingWork}
          open
          onOpenChange={(open) => {
            if (!open) setEditingWork(null);
          }}
          onDone={async () => {
            setEditingWork(null);
            await onRefresh(item.id);
          }}
        />
      ) : null}
    </div>
  );
}

function WorkEditDialog({
  work,
  open,
  onOpenChange,
  onDone,
}: {
  work: CooperationWork;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<WorkDraft>({
    platform: work.platform || "",
    url: work.url || "",
    title: work.title || "",
    published_at: work.published_at || "",
    note: work.note || "",
  });
  const mutation = useMutation({
    mutationFn: () => api.updateCooperationWork(work.id, draft),
    onSuccess: async () => {
      toast.success("作品已更新");
      onOpenChange(false);
      await onDone();
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "更新作品失败");
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑作品</DialogTitle>
          <DialogDescription>
            修改会写入该合作的时间线，原有记录不会被覆盖删除。
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor="cooperation-work-edit-url">作品链接</Label>
            <Input
              id="cooperation-work-edit-url"
              value={draft.url}
              onChange={(event) =>
                setDraft({ ...draft, url: event.target.value })
              }
              placeholder="http/https"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="cooperation-work-edit-title">作品标题</Label>
            <Input
              id="cooperation-work-edit-title"
              value={draft.title}
              onChange={(event) =>
                setDraft({ ...draft, title: event.target.value })
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="cooperation-work-edit-platform">平台</Label>
            <Input
              id="cooperation-work-edit-platform"
              value={draft.platform}
              onChange={(event) =>
                setDraft({ ...draft, platform: event.target.value })
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="cooperation-work-edit-published">发布时间</Label>
            <Input
              id="cooperation-work-edit-published"
              value={draft.published_at}
              onChange={(event) =>
                setDraft({ ...draft, published_at: event.target.value })
              }
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="cooperation-work-edit-note">备注</Label>
            <Input
              id="cooperation-work-edit-note"
              value={draft.note}
              onChange={(event) =>
                setDraft({ ...draft, note: event.target.value })
              }
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || !draft.url.trim()}
          >
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            保存修改
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CorrectionDialog({
  detail,
  statuses,
  open,
  onOpenChange,
  onDone,
}: {
  detail: CooperationCaseDetail;
  statuses: CooperationStatusDef[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => Promise<void>;
}) {
  const [status, setStatus] = useState(detail.case.status);
  const [note, setNote] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      api.correctCooperationCaseStatus(detail.case.id, {
        status,
        note: note.trim(),
      }),
    onSuccess: async () => {
      toast.success("合作状态已更正");
      onOpenChange(false);
      await onDone();
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "更正失败");
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>更正合作状态</DialogTitle>
          <DialogDescription>
            终态合作不会自动重建；更正会写入事件记录，必须填写原因。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="cooperation-correction-status">目标状态</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger id="cooperation-correction-status" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {statuses.map((item) => (
                  <SelectItem key={item.code} value={item.code}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="cooperation-correction-note">更正原因</Label>
            <Textarea
              id="cooperation-correction-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || !note.trim() || status === detail.case.status}
          >
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            确认更正
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function StatusDialog({
  detail,
  statuses,
  open,
  onOpenChange,
  onDone,
}: {
  detail: CooperationCaseDetail;
  statuses: CooperationStatusDef[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => Promise<void>;
}) {
  const [status, setStatus] = useState(detail.case.status);
  const [note, setNote] = useState("");
  const [files, setFiles] = useState<File[]>([]);

  const mutation = useMutation({
    mutationFn: async () => {
      const result = await api.updateCooperationCaseStatus(
        detail.case.id,
        {
          status,
          note: note.trim() || undefined,
          expected_updated_at: detail.case.updated_at,
        },
      );
      let uploadError = "";
      for (const file of files) {
        try {
          await api.uploadCooperationAttachment(
            detail.case.id,
            file,
            "other",
            "状态更新附件",
          );
        } catch (error) {
          uploadError = error instanceof Error ? error.message : "附件上传失败";
        }
      }
      return { result, uploadError };
    },
    onSuccess: async ({ uploadError }) => {
      if (uploadError) {
        toast.warning(`合作状态已更新，但部分附件上传失败：${uploadError}`);
      } else {
        toast.success("合作状态已更新");
      }
      onOpenChange(false);
      await onDone();
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "更新失败");
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>更新合作状态</DialogTitle>
          <DialogDescription>
            状态变更会写入这次合作的时间线；可以同时补充备注和附件。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="cooperation-status-select">新状态</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger id="cooperation-status-select" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {statuses
                  .filter((item) => item.code !== "completed")
                  .map((item) => (
                  <SelectItem key={item.code} value={item.code}>
                    {item.label}
                  </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="cooperation-status-note">备注</Label>
            <Textarea
              id="cooperation-status-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={4}
              placeholder="沟通结论、下一步约定等"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cooperation-status-files">附件</Label>
            <Input
              id="cooperation-status-files"
              type="file"
              multiple
              onChange={(event) =>
                setFiles(Array.from(event.target.files ?? []))
              }
            />
            <p className="text-xs text-muted-foreground">
              附件会在状态更新后按“其他”分类上传。
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CompletionDialog({
  detail,
  open,
  onOpenChange,
  onDone,
}: {
  detail: CooperationCaseDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [works, setWorks] = useState<WorkDraft[]>([]);
  const [files, setFiles] = useState<File[]>([]);

  const mutation = useMutation({
    mutationFn: async () => {
      const result = await api.completeCooperationCase(detail.case.id, {
        note: note.trim() || undefined,
        expected_updated_at: detail.case.updated_at,
        works: works
          .filter((item) => item.url.trim())
          .map((item) => ({
            platform: item.platform,
            url: item.url,
            title: item.title,
            published_at: item.published_at,
            note: item.note,
          })),
      });
      let uploadError = "";
      for (const file of files) {
        try {
          await api.uploadCooperationAttachment(
            detail.case.id,
            file,
            "other",
            "完成合作附件",
          );
        } catch (error) {
          uploadError = error instanceof Error ? error.message : "附件上传失败";
        }
      }
      return { result, uploadError };
    },
    onSuccess: async ({ uploadError }) => {
      if (uploadError) {
        toast.warning(`合作已完成，但部分附件上传失败：${uploadError}`);
      } else {
        toast.success("合作已完成");
      }
      onOpenChange(false);
      await onDone();
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "完成失败");
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>确认合作完成</DialogTitle>
          <DialogDescription>
            这是人工确认操作。作品链接和附件可以之后再补，但建议完成时填写。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-2">
            <Label htmlFor="cooperation-completion-note">结案备注</Label>
            <Textarea
              id="cooperation-completion-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              placeholder="合作结果、交付情况、结算说明等"
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>作品链接</Label>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  setWorks((current) => [...current, { ...EMPTY_WORK }])
                }
              >
                <Plus /> 添加作品
              </Button>
            </div>
            {detail.works.length > 0 ? (
              <p className="text-xs text-muted-foreground">
                已登记 {detail.works.length} 条作品，完成后仍会保留；下面可继续新增。
              </p>
            ) : null}
            {works.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                还没有填写作品链接，完成时会提示资料未完整。
              </p>
            ) : (
              <div className="space-y-2">
                {works.map((item, index) => (
                  <div
                    key={index}
                    className="grid gap-2 rounded border bg-muted/20 p-2 sm:grid-cols-2"
                  >
                    <Input
                      value={item.url}
                      onChange={(event) =>
                        setWorks((current) =>
                          current.map((row, rowIndex) =>
                            rowIndex === index
                              ? { ...row, url: event.target.value }
                              : row,
                          ),
                        )
                      }
                      placeholder="作品链接"
                      className="sm:col-span-2"
                    />
                    <Input
                      value={item.title}
                      onChange={(event) =>
                        setWorks((current) =>
                          current.map((row, rowIndex) =>
                            rowIndex === index
                              ? { ...row, title: event.target.value }
                              : row,
                          ),
                        )
                      }
                      placeholder="作品标题"
                    />
                    <Input
                      value={item.platform}
                      onChange={(event) =>
                        setWorks((current) =>
                          current.map((row, rowIndex) =>
                            rowIndex === index
                              ? { ...row, platform: event.target.value }
                              : row,
                          ),
                        )
                      }
                      placeholder="平台"
                    />
                    <Input
                      value={item.published_at}
                      onChange={(event) =>
                        setWorks((current) =>
                          current.map((row, rowIndex) =>
                            rowIndex === index
                              ? { ...row, published_at: event.target.value }
                              : row,
                          ),
                        )
                      }
                      placeholder="发布时间"
                    />
                    <Input
                      value={item.note}
                      onChange={(event) =>
                        setWorks((current) =>
                          current.map((row, rowIndex) =>
                            rowIndex === index
                              ? { ...row, note: event.target.value }
                              : row,
                          ),
                        )
                      }
                      placeholder="备注"
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      className="w-fit"
                      onClick={() =>
                        setWorks((current) =>
                          current.filter((_, rowIndex) => rowIndex !== index),
                        )
                      }
                    >
                      删除
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="cooperation-completion-files">附件</Label>
            <Input
              id="cooperation-completion-files"
              type="file"
              multiple
              onChange={(event) =>
                setFiles(Array.from(event.target.files ?? []))
              }
            />
          </div>

          <label className="flex items-start gap-2 text-sm">
            <Checkbox
              checked={confirmed}
              onCheckedChange={(value) => setConfirmed(Boolean(value))}
            />
            <span>我确认本次合作已经完成。</span>
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || !confirmed}
          >
            {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
            完成合作
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
