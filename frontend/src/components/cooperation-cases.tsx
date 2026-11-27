import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, FileUp, LoaderCircle, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, downloadFile } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
import { useUnsavedGuard } from "@/shared/use-unsaved-guard";
import { fmtDateOnly, fmtDateTime, safeHttpUrl } from "@/shared/format";
import type {
  CooperationCase,
  CooperationCaseDetail,
  CooperationStatusDef,
  CooperationSummary,
  CooperationWork,
} from "@/shared/types";
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
  const [deletedCaseId, setDeletedCaseId] = useState<number | null>(null);
  const [caseDraftDirty, setCaseDraftDirty] = useState(false);
  const selectCase = (caseId: number) => {
    if (caseId === selectedCaseId) return;
    if (
      caseDraftDirty &&
      !window.confirm("当前合作记录有未保存内容，确定放弃并切换？")
    ) {
      return;
    }
    setCaseDraftDirty(false);
    setSelectedCaseId(caseId);
  };

  useEffect(() => {
    if (cases.some((item) => item.id === selectedCaseId)) return;
    const candidates = cases.filter((item) => item.id !== deletedCaseId);
    const next = candidates.find((item) => item.active) ?? candidates[0];
    setSelectedCaseId(next?.id ?? null);
  }, [cases, deletedCaseId, selectedCaseId]);

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

  const handleDeleted = async (deletedId: number) => {
    setDeletedCaseId(deletedId);
    const remaining = cases.filter((entry) => entry.id !== deletedId);
    const next = remaining.find((entry) => entry.active) ?? remaining[0];
    setSelectedCaseId(next?.id ?? null);
    queryClient.removeQueries({ queryKey: ["cooperation-case", deletedId] });
    await onChanged();
  };

  const latest = cases[0];
  const nextSeq = (latest?.seq ?? 0) + 1;
  const detail = detailQuery.data;

  return (
    <section className="space-y-3 border-y py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <h3 className="text-sm font-semibold">合作记录</h3>
          <span className="text-xs text-muted-foreground">
            累计 {summary.cooperation_count} 次
          </span>
          {cases.length === 0 && summary.cooperation_status ? (
            <CooperationStatusBadge
              status={summary.cooperation_status}
              label={
                statuses.find(
                  (item) => item.code === summary.cooperation_status,
                )?.label ?? summary.cooperation_status
              }
            />
          ) : null}
        </div>
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

      <p className="text-xs text-muted-foreground">
        合作状态跨轮次；每次合作独立记录，同一达人最多一个进行中。
      </p>

      {cases.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {creatorStatus === "adopted"
            ? "还没有合作记录，点击“开始合作”进入首次跟进。"
            : "达人被采用后才能开始合作。"}
        </p>
      ) : (
        <>
          {cases.length > 1 ? (
            <div className="flex flex-wrap gap-1.5">
              {cases.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={item.id === selectedCaseId}
                  onClick={() => selectCase(item.id)}
                  className={
                    item.id === selectedCaseId
                      ? "rounded-md border border-foreground/30 bg-muted px-2 py-1 text-xs"
                      : "rounded-md border bg-background px-2 py-1 text-xs hover:bg-muted/40"
                  }
                >
                  <span className="font-medium">第 {item.seq} 次</span>
                  <span className="mx-1 text-muted-foreground">·</span>
                  <span>{item.status_label}</span>
                  <span className="ml-1.5 text-[11px] text-muted-foreground">
                    {item.active ? "进行中" : fmtDateOnly(item.started_at)}
                  </span>
                </button>
              ))}
            </div>
          ) : null}

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
              onDeleted={handleDeleted}
              onOpenStatus={() => setStatusDialogOpen(true)}
              onOpenComplete={() => setCompleteDialogOpen(true)}
              onDirtyChange={setCaseDraftDirty}
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
  onDeleted,
  onOpenStatus,
  onOpenComplete,
  onDirtyChange,
}: {
  detail: CooperationCaseDetail;
  statuses: CooperationStatusDef[];
  onRefresh: (caseId?: number) => Promise<void>;
  onDeleted: (caseId: number) => Promise<void>;
  onOpenStatus: () => void;
  onOpenComplete: () => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const item = detail.case;
  const active = item.active;
  const { user } = useAuth();
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [editingWork, setEditingWork] = useState<CooperationWork | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [summaryEditing, setSummaryEditing] = useState(false);
  const [timelineExpanded, setTimelineExpanded] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [attachOpen, setAttachOpen] = useState(false);
  const [worksOpen, setWorksOpen] = useState(false);
  const [summary, setSummary] = useState(item.summary || "");
  const [note, setNote] = useState("");
  const [work, setWork] = useState<WorkDraft>({ ...EMPTY_WORK });
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState<string>("other");
  const [description, setDescription] = useState("");
  const workDirty = Object.values(work).some((value) => value.trim());
  const draftsDirty = Boolean(
    (summaryEditing && summary !== (item.summary || "")) ||
    (noteOpen && note.trim()) ||
    (attachOpen && (file || description.trim())) ||
    (worksOpen && workDirty),
  );

  useEffect(() => {
    onDirtyChange(draftsDirty);
  }, [draftsDirty, onDirtyChange]);
  useUnsavedGuard(draftsDirty, "合作记录有未保存内容，确定离开当前页面？");

  useEffect(() => {
    setSummary(item.summary || "");
  }, [item.id, item.summary]);

  useEffect(() => {
    setSummaryEditing(false);
    setTimelineExpanded(false);
    setNoteOpen(false);
    setAttachOpen(false);
    setWorksOpen(false);
    setDeleteOpen(false);
    setNote("");
    setWork({ ...EMPTY_WORK });
    setFile(null);
    setDescription("");
    setEditingWork(null);
    setCorrectionOpen(false);
  }, [item.id]);

  const summaryTriggerRef = useRef<HTMLButtonElement>(null);
  const noteTriggerRef = useRef<HTMLButtonElement>(null);
  const attachTriggerRef = useRef<HTMLButtonElement>(null);
  const worksTriggerRef = useRef<HTMLButtonElement>(null);
  const worksUrlRef = useRef<HTMLInputElement>(null);

  const summaryPanelId = `case-summary-panel-${item.id}`;
  const notePanelId = `case-note-panel-${item.id}`;
  const attachPanelId = `case-attach-panel-${item.id}`;
  const worksPanelId = `case-works-panel-${item.id}`;
  const timelineId = `case-timeline-${item.id}`;

  const closeSummary = () => {
    if (
      summary !== (item.summary || "") &&
      !window.confirm("放弃未保存的合作摘要？")
    ) {
      return;
    }
    setSummaryEditing(false);
    setSummary(item.summary || "");
    summaryTriggerRef.current?.focus();
  };
  const closeNote = () => {
    if (note.trim() && !window.confirm("放弃未保存的合作备注？")) return;
    setNoteOpen(false);
    setNote("");
    noteTriggerRef.current?.focus();
  };
  const closeAttach = () => {
    if (
      (file || description.trim()) &&
      !window.confirm("放弃未上传的附件和说明？")
    ) {
      return;
    }
    setAttachOpen(false);
    setFile(null);
    setDescription("");
    attachTriggerRef.current?.focus();
  };
  const closeWorks = () => {
    if (workDirty && !window.confirm("放弃未保存的作品信息？")) return;
    setWorksOpen(false);
    setWork({ ...EMPTY_WORK });
    worksTriggerRef.current?.focus();
  };
  const closeOnEscape =
    (close: () => void) => (event: KeyboardEvent<HTMLElement>) => {
      if (event.key === "Escape") close();
    };
  const openWorksFromHint = () => {
    setWorksOpen(true);
    window.requestAnimationFrame(() => worksUrlRef.current?.focus());
  };

  const summaryMutation = useMutation({
    mutationFn: () =>
      api.updateCooperationCaseSummary(
        item.id,
        summary,
        item.updated_at,
      ),
    onSuccess: async () => {
      setSummaryEditing(false);
      summaryTriggerRef.current?.focus();
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

  const deleteCaseMutation = useMutation({
    mutationFn: () => api.deleteCooperationCase(item.id),
    onSuccess: async () => {
      setDeleteOpen(false);
      toast.success(`第 ${item.seq} 次合作已删除`);
      await onDeleted(item.id);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "删除失败");
    },
  });

  const visibleEvents = timelineExpanded
    ? detail.events
    : detail.events.slice(-3);
  const hiddenEventCount = detail.events.length - visibleEvents.length;

  return (
    <div className="rounded-md border">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b bg-muted/20 px-3 py-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">第 {item.seq} 次合作</span>
            <CooperationStatusBadge
              status={item.status}
              label={item.status_label}
            />
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            开始 {fmtDateTime(item.started_at)}
            {item.completed_at ? ` · 完成 ${fmtDateTime(item.completed_at)}` : ""}
            {item.closed_at ? ` · 结束 ${fmtDateTime(item.closed_at)}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {active ? (
            <>
              <Button size="sm" variant="outline" onClick={onOpenStatus}>
                更新状态
              </Button>
              <Button size="sm" onClick={onOpenComplete}>
                完成合作
              </Button>
            </>
          ) : user?.role === "admin" ? (
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
            variant="ghost"
            onClick={() => {
              void downloadFile(
                `/api/cooperation-cases/${item.id}/export`,
                `cooperation_case_${item.id}.zip`,
              ).catch((error) => {
                toast.error(error instanceof Error ? error.message : "导出失败");
              });
            }}
          >
            <Download /> 导出
          </Button>
          {user?.role === "admin" ? (
            <Button
              size="icon-sm"
              variant="ghost"
              className="text-muted-foreground hover:text-destructive"
              aria-label={`删除第 ${item.seq} 次合作`}
              title="删除本次合作"
              onClick={() => setDeleteOpen(true)}
            >
              <Trash2 />
            </Button>
          ) : null}
        </div>
      </div>

      <div className="space-y-4 p-3">
        {item.status === "completed" && detail.works.length === 0 ? (
          <div className="flex flex-wrap items-center gap-2 rounded border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700">
            <span>该合作已完成，但还没有登记作品链接。</span>
            <button
              type="button"
              className="font-medium underline underline-offset-4"
              aria-controls={worksPanelId}
              onClick={openWorksFromHint}
            >
              去添加
            </button>
          </div>
        ) : null}

        <div className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium text-muted-foreground">合作摘要</p>
            <Button
              ref={summaryTriggerRef}
              size="xs"
              variant="ghost"
              aria-expanded={summaryEditing}
              aria-controls={summaryPanelId}
              onClick={() => {
                if (summaryEditing) closeSummary();
                else setSummaryEditing(true);
              }}
            >
              {summaryEditing ? (
                "取消"
              ) : (
                <>
                  <Pencil />
                  {item.summary ? "编辑" : "添加摘要"}
                </>
              )}
            </Button>
          </div>
          {summaryEditing ? (
            <div
              id={summaryPanelId}
              className="space-y-2"
              onKeyDown={closeOnEscape(closeSummary)}
            >
              <Textarea
                id={`case-summary-${item.id}`}
                value={summary}
                onChange={(event) => setSummary(event.target.value)}
                rows={3}
                placeholder="合作方式、报价、交付约定等"
                disabled={summaryMutation.isPending}
                autoFocus
              />
              <Button
                size="sm"
                onClick={() => summaryMutation.mutate()}
                disabled={
                  summaryMutation.isPending || summary === item.summary
                }
              >
                {summaryMutation.isPending ? (
                  <LoaderCircle className="animate-spin" />
                ) : null}
                保存
              </Button>
            </div>
          ) : (
            <p className="whitespace-pre-wrap text-sm">
              {item.summary || (
                <span className="text-muted-foreground">暂无摘要。</span>
              )}
            </p>
          )}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium text-muted-foreground">
              时间线{detail.events.length > 0 ? ` · ${detail.events.length}` : ""}
            </p>
            <Button
              ref={noteTriggerRef}
              size="xs"
              variant="ghost"
              aria-expanded={noteOpen}
              aria-controls={notePanelId}
              onClick={() => {
                if (noteOpen) closeNote();
                else setNoteOpen(true);
              }}
            >
              {noteOpen ? (
                "取消"
              ) : (
                <>
                  <Plus /> 添加记录
                </>
              )}
            </Button>
          </div>
          {noteOpen ? (
            <div
              id={notePanelId}
              className="flex items-start gap-2"
              onKeyDown={closeOnEscape(closeNote)}
            >
              <Textarea
                id={`case-note-${item.id}`}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={2}
                placeholder="补充沟通、交付、结算等说明"
                autoFocus
              />
              <Button
                size="sm"
                onClick={() => noteMutation.mutate()}
                disabled={noteMutation.isPending || !note.trim()}
              >
                {noteMutation.isPending ? (
                  <LoaderCircle className="animate-spin" />
                ) : null}
                添加
              </Button>
            </div>
          ) : null}
          {detail.events.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无记录。</p>
          ) : (
            <>
              <ol id={timelineId} className="space-y-2 border-l pl-3">
                {visibleEvents.map((event) => {
                  const showTransition =
                    event.event_type !== "created" &&
                    Boolean(event.status_from || event.status_to);
                  return (
                    <li key={event.id} className="text-xs">
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                        <span className="font-medium">
                          {EVENT_LABELS[event.event_type] ?? event.event_type}
                        </span>
                        {showTransition ? (
                          <span className="text-muted-foreground">
                            {event.from_status_label} → {event.to_status_label}
                          </span>
                        ) : null}
                        <span className="text-muted-foreground">
                          {fmtDateTime(event.created_at)}
                          {event.actor_username
                            ? ` · ${event.actor_username}`
                            : ""}
                        </span>
                      </div>
                      {event.note ? (
                        <p className="mt-0.5 whitespace-pre-wrap text-muted-foreground">
                          {event.note}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
              {detail.events.length > 3 ? (
                <Button
                  size="xs"
                  variant="ghost"
                  className="text-muted-foreground"
                  aria-expanded={timelineExpanded}
                  aria-controls={timelineId}
                  onClick={() => setTimelineExpanded((value) => !value)}
                >
                  {timelineExpanded
                    ? "收起较早记录"
                    : `展开较早的 ${hiddenEventCount} 条`}
                </Button>
              ) : null}
            </>
          )}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium text-muted-foreground">
              附件 · {detail.attachments.length}
            </p>
            <Button
              ref={attachTriggerRef}
              size="xs"
              variant="ghost"
              aria-expanded={attachOpen}
              aria-controls={attachPanelId}
              onClick={() => {
                if (attachOpen) closeAttach();
                else setAttachOpen(true);
              }}
            >
              {attachOpen ? (
                "取消"
              ) : (
                <>
                  <Plus /> 上传附件
                </>
              )}
            </Button>
          </div>
          {attachOpen ? (
            <div
              id={attachPanelId}
              className="grid gap-2 rounded border bg-background p-2 sm:grid-cols-[1fr_160px]"
              onKeyDown={closeOnEscape(closeAttach)}
            >
              <Input
                type="file"
                aria-label="选择附件文件"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="text-xs"
                autoFocus
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
          ) : null}
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
                  {user?.role === "admin" || attachment.uploaded_by === user?.id ? (
                    <Button
                      size="icon"
                      variant="ghost"
                      className="size-7"
                      aria-label="删除附件"
                      onClick={() => {
                        if (window.confirm(`确定删除附件「${attachment.original_name}」？此操作不可撤销。`)) {
                          deleteAttachmentMutation.mutate(attachment.id);
                        }
                      }}
                      disabled={deleteAttachmentMutation.isPending}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium text-muted-foreground">
              作品链接 · {detail.works.length}
            </p>
            <Button
              ref={worksTriggerRef}
              size="xs"
              variant="ghost"
              aria-expanded={worksOpen}
              aria-controls={worksPanelId}
              onClick={() => {
                if (worksOpen) closeWorks();
                else setWorksOpen(true);
              }}
            >
              {worksOpen ? (
                "取消"
              ) : (
                <>
                  <Plus /> 添加作品
                </>
              )}
            </Button>
          </div>
          {worksOpen ? (
            <div
              id={worksPanelId}
              className="grid gap-2 rounded border bg-background p-2 sm:grid-cols-2"
              onKeyDown={closeOnEscape(closeWorks)}
            >
              <Input
                ref={worksUrlRef}
                value={work.url}
                onChange={(event) => setWork({ ...work, url: event.target.value })}
                placeholder="作品链接（http/https）"
                className="text-xs sm:col-span-2"
                autoFocus
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
          ) : null}
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
                      onClick={() => {
                        if (window.confirm(`确定删除作品「${workItem.title || workItem.url}」？`)) {
                          deleteWorkMutation.mutate(workItem.id);
                        }
                      }}
                      disabled={deleteWorkMutation.isPending}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

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

      {deleteOpen ? (
        <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>删除第 {item.seq} 次合作？</AlertDialogTitle>
              <AlertDialogDescription>
                将永久删除这次合作及其 {detail.events.length} 条时间线记录、
                {detail.works.length} 条作品和 {detail.attachments.length} 个附件文件。
                删除后达人合作累计会重新计算；删除前会自动备份数据库，此操作不可撤销。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={deleteCaseMutation.isPending}>
                取消
              </AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={deleteCaseMutation.isPending}
                onClick={(event) => {
                  event.preventDefault();
                  deleteCaseMutation.mutate();
                }}
              >
                {deleteCaseMutation.isPending ? (
                  <LoaderCircle className="animate-spin" />
                ) : null}
                确认删除
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
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
