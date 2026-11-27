import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Mail, Pencil, Plus, RefreshCw, Send, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
import { fmtDateTime, fmtNumber } from "@/shared/format";
import { useUnsavedGuard } from "@/shared/use-unsaved-guard";
import type {
  EmailAccount,
  EmailAccountInput,
  EmailTarget,
} from "@/shared/types";
import { EmptyState, ErrorState, Page, PageHeader, Section } from "@/components/page";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";

const SMTP_PRESETS = [
  { value: "qq", label: "QQ 邮箱", host: "smtp.qq.com", port: 465, security: "ssl" },
  { value: "163", label: "163 邮箱", host: "smtp.163.com", port: 465, security: "ssl" },
  { value: "gmail", label: "Gmail", host: "smtp.gmail.com", port: 465, security: "ssl" },
  {
    value: "outlook",
    label: "Outlook / Microsoft 365",
    host: "smtp.office365.com",
    port: 587,
    security: "starttls",
  },
  { value: "custom", label: "自定义", host: "", port: 587, security: "starttls" },
];

const CAMPAIGN_STATUS_LABELS: Record<string, { label: string; className: string }> = {
  draft: { label: "草稿", className: "border-slate-200 bg-slate-50 text-slate-600" },
  scheduled: { label: "已计划", className: "border-sky-200 bg-sky-50 text-sky-700" },
  sending: { label: "发送中", className: "border-violet-200 bg-violet-50 text-violet-700" },
  paused: { label: "已暂停", className: "border-amber-200 bg-amber-50 text-amber-700" },
  completed: { label: "已完成", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  cancelled: { label: "已取消", className: "border-rose-200 bg-rose-50 text-rose-700" },
};

const RECIPIENT_STATUS_LABELS: Record<string, string> = {
  pending: "待发送",
  sending: "发送中",
  sent: "已发送",
  failed: "失败",
  skipped: "跳过",
};

const WEEKDAYS = [
  { value: 1, label: "一" },
  { value: 2, label: "二" },
  { value: 3, label: "三" },
  { value: 4, label: "四" },
  { value: 5, label: "五" },
  { value: 6, label: "六" },
  { value: 7, label: "日" },
];

const BASE_PLATFORM_OPTIONS = [
  { value: "xhs", label: "小红书" },
  { value: "douyin", label: "抖音" },
  { value: "bilibili", label: "B 站" },
  { value: "zhihu", label: "知乎" },
  { value: "instagram", label: "Instagram" },
];

const TEMPLATE_VARIABLES = new Set([
  "nickname",
  "platform",
  "user_id",
  "fans",
  "profile_url",
]);

const EMPTY_ACCOUNT: EmailAccountInput = {
  scope: "personal",
  label: "",
  from_name: "",
  from_email: "",
  reply_to: "",
  host: "",
  port: 587,
  security: "starttls",
  username: "",
  password: "",
  daily_limit: 20,
  min_interval_seconds: 60,
};

function statusBadge(status: string) {
  const item = CAMPAIGN_STATUS_LABELS[status] ?? {
    label: status,
    className: "border-border bg-muted text-muted-foreground",
  };
  return (
    <Badge variant="outline" className={item.className}>
      {item.label}
    </Badge>
  );
}

function renderPreview(text: string, target: EmailTarget | null) {
  const context: Record<string, string> = target
    ? {
        nickname: target.nickname || "",
        platform: target.platform,
        user_id: target.user_id,
        fans: target.fans != null ? fmtNumber(target.fans) : "",
        profile_url: target.profile_url || "",
      }
    : {
        nickname: "示例达人",
        platform: "xhs",
        user_id: "example-user",
        fans: "12,345",
        profile_url: "https://example.com/profile",
      };
  return text.replace(/\{\{\s*(\w+)\s*\}\}/g, (match, key: string) =>
    key in context ? context[key] : match,
  );
}


function SuppressionSection() {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [reason, setReason] = useState("");
  const query = useQuery({
    queryKey: ["email-suppressions"],
    queryFn: api.getEmailSuppressions,
  });
  const addMutation = useMutation({
    mutationFn: () =>
      api.addEmailSuppression({ email: email.trim(), reason: reason.trim() }),
    onSuccess: async () => {
      toast.success("已加入退订名单");
      setEmail("");
      setReason("");
      await queryClient.invalidateQueries({ queryKey: ["email-suppressions"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "添加失败"),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteEmailSuppression(id),
    onSuccess: async () => {
      toast.success("已移出退订名单");
      await queryClient.invalidateQueries({ queryKey: ["email-suppressions"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "删除失败"),
  });
  return (
    <Section
      title="退订名单"
      description="这些邮箱不会出现在目标池中；发送前也会再次校验。"
      contentClassName="px-0"
    >
      <div className="flex flex-wrap items-end gap-2 border-b px-4 pb-3">
        <div className="w-full max-w-xs space-y-1">
          <Label htmlFor="suppression-email">邮箱</Label>
          <Input
            id="suppression-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@example.com"
            className="h-8"
          />
        </div>
        <div className="w-full max-w-xs space-y-1">
          <Label htmlFor="suppression-reason">原因（可选）</Label>
          <Input
            id="suppression-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            className="h-8"
          />
        </div>
        <Button
          size="sm"
          onClick={() => addMutation.mutate()}
          disabled={addMutation.isPending || !email.trim()}
        >
          {addMutation.isPending ? <LoaderCircle className="animate-spin" /> : <Plus />}
          添加
        </Button>
      </div>
      {query.isLoading ? (
        <Skeleton className="m-4 h-16 w-full" />
      ) : query.error ? (
        <div className="p-4">
          <ErrorState
            message={
              query.error instanceof Error ? query.error.message : "加载退订名单失败"
            }
          />
        </div>
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">暂无退订邮箱。</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>邮箱</TableHead>
              <TableHead>原因</TableHead>
              <TableHead>时间</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(query.data?.items ?? []).map((item) => (
              <TableRow key={item.id}>
                <TableCell className="font-mono text-xs">{item.email}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {item.reason || "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {fmtDateTime(item.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label="移出退订名单"
                    onClick={() => deleteMutation.mutate(item.id)}
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Section>
  );
}

export function EmailPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<"campaigns" | "new" | "accounts">("campaigns");
  const [selectedCampaignId, setSelectedCampaignId] = useState<number | null>(null);
  const [detailPage, setDetailPage] = useState(1);
  const [accountDialogOpen, setAccountDialogOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<EmailAccount | null>(null);
  const [accountForm, setAccountForm] = useState<EmailAccountInput>({ ...EMPTY_ACCOUNT });
  const [accountPreset, setAccountPreset] = useState("custom");
  const [testRecipient, setTestRecipient] = useState("");

  const accountsQuery = useQuery({
    queryKey: ["email-accounts"],
    queryFn: api.getEmailAccounts,
  });
  const campaignsQuery = useQuery({
    queryKey: ["email-campaigns"],
    queryFn: api.listEmailCampaigns,
    refetchInterval: 5000,
  });
  const detailQuery = useQuery({
    queryKey: ["email-campaign", selectedCampaignId, detailPage],
    queryFn: () =>
      api.getEmailCampaign(selectedCampaignId as number, {
        page: detailPage,
        pageSize: 50,
      }),
    enabled: selectedCampaignId !== null,
    refetchInterval: 5000,
  });
  const workerQuery = useQuery({
    queryKey: ["email-worker-status"],
    queryFn: api.getEmailWorkerStatus,
    refetchInterval: 10000,
    enabled: tab === "campaigns",
  });
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
    enabled: tab === "new",
  });
  const ownersQuery = useQuery({
    queryKey: ["creator-owners"],
    queryFn: api.getCreatorOwners,
    enabled: tab === "new",
  });
  const roundsQuery = useQuery({
    queryKey: ["rounds", false],
    queryFn: () => api.getRounds(false),
    enabled: tab === "new" && Boolean(user?.can_collect),
  });

  const accounts = accountsQuery.data?.items ?? [];
  const campaigns = campaignsQuery.data?.items ?? [];
  const runCampaignAction = async <T,>(
    action: () => Promise<T>,
    successMessage: (result: T) => string,
  ): Promise<T | null> => {
    try {
      const result = await action();
      toast.success(successMessage(result));
      await queryClient.invalidateQueries({ queryKey: ["email-campaigns"] });
      await detailQuery.refetch();
      return result;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "操作失败，请稍后重试");
      return null;
    }
  };

  const accountMutation = useMutation({
    mutationFn: () => {
      const payload = { ...accountForm };
      if (editingAccount && !payload.password) delete payload.password;
      return editingAccount
        ? api.updateEmailAccount(editingAccount.id, payload)
        : api.createEmailAccount(payload);
    },
    onSuccess: async () => {
      toast.success(editingAccount ? "发件账号已更新" : "发件账号已创建");
      setAccountDialogOpen(false);
      setEditingAccount(null);
      await queryClient.invalidateQueries({ queryKey: ["email-accounts"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "保存失败"),
  });
  const testMutation = useMutation({
    mutationFn: ({ accountId, sendTestTo = "" }: { accountId: number; sendTestTo?: string }) =>
      api.testEmailAccount(accountId, sendTestTo),
    onSuccess: async (result) => {
      toast.success(result.sent_test ? "SMTP 测试邮件已发送" : "SMTP 连接与认证成功");
      await queryClient.invalidateQueries({ queryKey: ["email-accounts"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "SMTP 测试失败"),
  });
  const deleteAccountMutation = useMutation({
    mutationFn: (accountId: number) => api.deleteEmailAccount(accountId),
    onSuccess: async () => {
      toast.success("发件账号已删除");
      await queryClient.invalidateQueries({ queryKey: ["email-accounts"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "删除失败"),
  });
  const resumeAccountMutation = useMutation({
    mutationFn: (accountId: number) => api.resumePausedEmailCampaigns(accountId),
    onSuccess: async (result) => {
      toast.success(`已恢复 ${result.resumed} 个暂停任务`);
      await queryClient.invalidateQueries({ queryKey: ["email-accounts"] });
      await queryClient.invalidateQueries({ queryKey: ["email-campaigns"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "恢复失败"),
  });

  const openCreateAccount = () => {
    setEditingAccount(null);
    setAccountPreset("custom");
    setTestRecipient("");
    setAccountForm({ ...EMPTY_ACCOUNT, scope: "personal" });
    setAccountDialogOpen(true);
  };
  const openEditAccount = (account: EmailAccount) => {
    setEditingAccount(account);
    setAccountPreset("custom");
    setTestRecipient(account.from_email);
    setAccountForm({
      scope: account.scope,
      label: account.label,
      from_name: account.from_name,
      from_email: account.from_email,
      reply_to: account.reply_to,
      host: account.host,
      port: account.port,
      security: account.security,
      username: account.username,
      password: "",
      daily_limit: account.daily_limit,
      min_interval_seconds: account.min_interval_seconds,
      enabled: account.enabled,
    });
    setAccountDialogOpen(true);
  };

  const campaignMutation = useMutation({
    mutationFn: async (payload: {
      accountId: number;
      name: string;
      subject: string;
      body: string;
      recipients: { platform: string; user_id: string; email: string; nickname?: string }[];
      schedule: {
        scheduled_start: string | null;
        timezone: string;
        window_start: string;
        window_end: string;
        weekdays: number[];
        interval_seconds: number;
        jitter_seconds: number;
        daily_limit: number;
      };
      autoMark: boolean;
    }) =>
      api.createEmailCampaign({
        name: payload.name,
        account_id: payload.accountId,
        subject: payload.subject,
        body: payload.body,
        scheduled_start: payload.schedule.scheduled_start,
        schedule: true,
        timezone: payload.schedule.timezone,
        window_start: payload.schedule.window_start,
        window_end: payload.schedule.window_end,
        weekdays: payload.schedule.weekdays,
        interval_seconds: payload.schedule.interval_seconds,
        jitter_seconds: payload.schedule.jitter_seconds,
        daily_limit: payload.schedule.daily_limit,
        auto_mark_contacted: payload.autoMark,
        recipients: payload.recipients,
      }),
    onSuccess: async (campaign) => {
      toast.success(
        campaign.skipped?.length
          ? `已创建并计划；跳过 ${campaign.skipped.length} 个收件人`
          : "已创建并计划发送",
      );
      setTab("campaigns");
      setDetailPage(1);
      setSelectedCampaignId(campaign.id);
      resetWizard();
      await queryClient.invalidateQueries({ queryKey: ["email-campaigns"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "创建发件任务失败"),
  });

  // wizard state
  const [wizardAccountId, setWizardAccountId] = useState("");
  const [wizardName, setWizardName] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [targetSearch, setTargetSearch] = useState("");
  const [targetPlatform, setTargetPlatform] = useState("xhs");
  const [targetOwner, setTargetOwner] = useState("all");
  const [targetRound, setTargetRound] = useState("all");
  const [targetHasEmail, setTargetHasEmail] = useState("all");
  const [targetPage, setTargetPage] = useState(1);
  const [selectedTargets, setSelectedTargets] = useState<Record<string, EmailTarget>>({});
  const [emailOverrides, setEmailOverrides] = useState<Record<string, string>>({});
  const [scheduledStart, setScheduledStart] = useState("");
  const [timezone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
  );
  const [windowStart, setWindowStart] = useState("09:00");
  const [windowEnd, setWindowEnd] = useState("18:00");
  const [weekdays, setWeekdays] = useState<number[]>([1, 2, 3, 4, 5]);
  const [intervalSeconds, setIntervalSeconds] = useState(60);
  const [jitterSeconds, setJitterSeconds] = useState(30);
  const [dailyLimit, setDailyLimit] = useState(20);
  const [autoMark, setAutoMark] = useState(true);

  const targetsQuery = useQuery({
    queryKey: [
      "email-targets",
      targetSearch,
      targetPlatform,
      targetOwner,
      targetRound,
      targetHasEmail,
      targetPage,
    ],
    queryFn: () =>
      api.getEmailTargets({
        search: targetSearch,
        platform: targetPlatform,
        owner: targetOwner,
        roundId: targetRound === "all" ? null : Number(targetRound),
        hasEmail:
          targetHasEmail === "all" ? null : targetHasEmail === "with",
        page: targetPage,
        pageSize: 50,
      }),
    enabled: tab === "new",
  });

  const targets = targetsQuery.data?.items ?? [];
  const configuredPlatforms = settingsQuery.data?.platforms ?? [];
  const platformOptions = [
    ...BASE_PLATFORM_OPTIONS.map((item) => ({
      ...item,
      label: configuredPlatforms.find((platform) => platform.code === item.value)?.label ?? item.label,
    })),
    ...configuredPlatforms
      .filter((item) => !BASE_PLATFORM_OPTIONS.some((base) => base.value === item.code))
      .map((item) => ({ value: item.code, label: item.label })),
  ];
  const owners = ownersQuery.data ?? [];
  const rounds = roundsQuery.data ?? [];
  const selectedKeys = Object.keys(selectedTargets);
  const previewTarget = selectedKeys.length
    ? selectedTargets[selectedKeys[0]]
    : null;
  const unknownVariables = useMemo(() => {
    const keys = Array.from(
      `${subject}\n${body}`.matchAll(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g),
      (match) => match[1],
    );
    return Array.from(new Set(keys)).filter((key) => !TEMPLATE_VARIABLES.has(key));
  }, [subject, body]);
  const wizardDirty = Boolean(
    wizardAccountId ||
      wizardName ||
      subject ||
      body ||
      selectedKeys.length ||
      Object.keys(emailOverrides).length ||
      scheduledStart ||
      windowStart !== "09:00" ||
      windowEnd !== "18:00" ||
      weekdays.join(",") !== "1,2,3,4,5" ||
      intervalSeconds !== 60 ||
      jitterSeconds !== 30 ||
      dailyLimit !== 20 ||
      !autoMark,
  );
  useUnsavedGuard(tab === "new" && wizardDirty && !campaignMutation.isPending);
  const changeTab = (next: "campaigns" | "new" | "accounts") => {
    if (
      tab === "new" &&
      next !== "new" &&
      wizardDirty &&
      !window.confirm("新建发件尚未提交，确定放弃当前内容吗？")
    ) {
      return;
    }
    setTab(next);
  };

  const resetWizard = () => {
    setWizardAccountId("");
    setWizardName("");
    setSubject("");
    setBody("");
    setSelectedTargets({});
    setEmailOverrides({});
    setScheduledStart("");
    setWeekdays([1, 2, 3, 4, 5]);
    setIntervalSeconds(60);
    setJitterSeconds(30);
    setDailyLimit(20);
    setAutoMark(true);
    setTargetPage(1);
    setTargetOwner("all");
    setTargetRound("all");
    setTargetHasEmail("all");
    setTargetSearch("");
  };

  const toggleTarget = (target: EmailTarget, checked: boolean) => {
    const key = `${target.platform}|${target.user_id}`;
    setSelectedTargets((current) => {
      const next = { ...current };
      if (checked) next[key] = target;
      else delete next[key];
      return next;
    });
  };

  const submitCampaign = () => {
    const accountId = Number(wizardAccountId);
    if (!accountId) {
      toast.error("请选择发件账号");
      return;
    }
    if (!wizardName.trim() || !subject.trim() || !body.trim()) {
      toast.error("请填写任务名称、主题和正文");
      return;
    }
    if (!selectedKeys.length) {
      toast.error("请选择至少一个达人");
      return;
    }
    if (
      selectedKeys.some((key) => Boolean(selectedTargets[key]?.cooldown_until))
    ) {
      toast.error("已选达人中有冷却中的账号，请取消勾选后再提交");
      return;
    }
    if (unknownVariables.length) {
      toast.error(`模板包含未知变量：${unknownVariables.join("、")}`);
      return;
    }
    if (!weekdays.length || windowStart >= windowEnd) {
      toast.error("请至少选择一个发送日，并确保开始时段早于结束时段");
      return;
    }
    const recipients = [];
    for (const key of selectedKeys) {
      const target = selectedTargets[key];
      const email = (emailOverrides[key] ?? target.email ?? "").trim();
      if (!email) {
        toast.error(`请补充 ${target.nickname || target.user_id} 的邮箱`);
        return;
      }
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
        toast.error(`${target.nickname || target.user_id} 的邮箱格式不正确`);
        return;
      }
      recipients.push({
        platform: target.platform,
        user_id: target.user_id,
        email,
        nickname: target.nickname,
      });
    }
    const account = accounts.find((item) => item.id === accountId);
    const dayLabels = WEEKDAYS.filter((day) => weekdays.includes(day.value))
      .map((day) => `周${day.label}`)
      .join("、");
    const confirmed = window.confirm(
      [
        "请最终确认自动发件设置：",
        `发件账号：${account?.from_email || accountId}`,
        `收件人：${recipients.length} 位`,
        `开始：${scheduledStart || "立即排队"}（${timezone}）`,
        `发送日：${dayLabels}；时段：${windowStart}–${windowEnd}`,
        `频率：间隔 ${intervalSeconds} 秒 + 0–${jitterSeconds} 秒抖动；每日最多 ${dailyLimit} 封`,
        `发送成功后自动标记已联系：${autoMark ? "是" : "否"}`,
        "",
        "确认创建并开始按计划发送吗？",
      ].join("\n"),
    );
    if (!confirmed) return;
    campaignMutation.mutate({
      accountId,
      name: wizardName.trim(),
      subject: subject.trim(),
      body,
      recipients,
      schedule: {
        scheduled_start: scheduledStart ? scheduledStart : null,
        timezone,
        window_start: windowStart,
        window_end: windowEnd,
        weekdays,
        interval_seconds: intervalSeconds,
        jitter_seconds: jitterSeconds,
        daily_limit: dailyLimit,
      },
      autoMark,
    });
  };

  const title = "自动发件";
  return (
    <Page>
      <PageHeader
        title={title}
        description="配置发件账号，从已采用未联系的达人中创建发件任务并按时段发送。"
        actions={
          <>
            <Button
              variant={tab === "campaigns" ? "secondary" : "outline"}
              onClick={() => changeTab("campaigns")}
            >
              <Mail /> 发件任务
            </Button>
            <Button
              variant={tab === "new" ? "secondary" : "outline"}
              onClick={() => changeTab("new")}
            >
              <Plus /> 新建发件
            </Button>
            <Button
              variant={tab === "accounts" ? "secondary" : "outline"}
              onClick={() => changeTab("accounts")}
            >
              发件账号
            </Button>
          </>
        }
      />

      {tab === "accounts" ? (
        <div className="space-y-6">
        <Section
          title="发件账号"
          description="个人账号仅自己可见；共享账号由管理员维护，所有发件人可用。"
          actions={
            <Button size="sm" onClick={openCreateAccount}>
              <Plus /> 添加账号
            </Button>
          }
          contentClassName="px-0"
        >
          {accountsQuery.isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 3 }).map((_, index) => (
                <Skeleton key={index} className="h-10 w-full" />
              ))}
            </div>
          ) : accountsQuery.error ? (
            <div className="p-4">
              <ErrorState
                message={
                  accountsQuery.error instanceof Error
                    ? accountsQuery.error.message
                    : "加载发件账号失败"
                }
              />
            </div>
          ) : accounts.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="还没有发件账号"
                description="先添加个人或公司 SMTP 账号，再创建发件任务。"
                actions={
                  <Button size="sm" onClick={openCreateAccount}>
                    添加账号
                  </Button>
                }
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称 / 发件人</TableHead>
                  <TableHead>服务器</TableHead>
                  <TableHead>范围</TableHead>
                  <TableHead className="text-right">每日上限 / 间隔</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.map((account) => (
                  <TableRow key={account.id}>
                    <TableCell>
                      <div className="font-medium">
                        {account.label || account.from_email}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {account.from_name ? `${account.from_name} · ` : ""}
                        {account.from_email}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {account.host}:{account.port} · {account.security}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="font-normal">
                        {account.scope === "shared" ? "共享" : "个人"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {account.daily_limit} / {account.min_interval_seconds}s
                    </TableCell>
                    <TableCell>
                      {account.auth_error ? (
                        <span className="text-xs text-rose-600" title={account.auth_error}>
                          认证失败
                        </span>
                      ) : account.last_test_error ? (
                        <span className="text-xs text-rose-600" title={account.last_test_error}>
                          测试失败
                        </span>
                      ) : account.last_test_at ? (
                        <span className="text-xs text-emerald-600">可用</span>
                      ) : (
                        <span className="text-xs text-muted-foreground">未测试</span>
                      )}
                      {account.paused_campaigns > 0 ? (
                        <div className="mt-1 text-xs text-amber-600">
                          {account.paused_campaigns} 个任务暂停
                        </div>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => testMutation.mutate({ accountId: account.id })}
                          disabled={testMutation.isPending}
                        >
                          测试连接
                        </Button>
                        {account.can_manage && account.paused_campaigns > 0 ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => resumeAccountMutation.mutate(account.id)}
                            disabled={
                              resumeAccountMutation.isPending || !account.enabled
                            }
                          >
                            恢复任务
                          </Button>
                        ) : null}
                        {account.can_manage ? (
                          <>
                            <Button
                              size="icon-sm"
                              variant="ghost"
                              aria-label="编辑发件账号"
                              onClick={() => openEditAccount(account)}
                            >
                              <Pencil />
                            </Button>
                            <Button
                              size="icon-sm"
                              variant="ghost"
                              aria-label="删除发件账号"
                              onClick={() => {
                                if (confirm(`删除发件账号「${account.from_email}」？`)) {
                                  deleteAccountMutation.mutate(account.id);
                                }
                              }}
                              disabled={deleteAccountMutation.isPending}
                            >
                              <Trash2 />
                            </Button>
                          </>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
        {isAdmin ? <SuppressionSection /> : null}
        </div>
      ) : tab === "campaigns" ? (
        <Section
          title="发件任务"
          description="按时间窗口逐个发送；同一达人最多一个进行中的合作。"
          actions={
            <Button size="sm" onClick={() => changeTab("new")}>
              <Plus /> 新建发件
            </Button>
          }
          contentClassName="px-0"
        >
          {workerQuery.data ? (
            <div className="mx-4 mt-3 space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {workerQuery.data.stale ? (
                <div className="font-medium">
                  {workerQuery.data.last_tick_at
                    ? `发件 worker 心跳超时（最近 ${fmtDateTime(workerQuery.data.last_tick_at)}）`
                    : "发件 worker 尚未启动"}
                </div>
              ) : null}
              {workerQuery.data.last_error ? (
                <div>最近错误：{workerQuery.data.last_error}</div>
              ) : null}
              {!workerQuery.data.public_url_configured ? (
                <div>
                  未配置 CODATA_PUBLIC_URL：邮件只能使用“回复退订”，不会附带一键退订链接和
                  List-Unsubscribe 头。
                </div>
              ) : null}
              {!workerQuery.data.stale &&
              !workerQuery.data.last_error &&
              workerQuery.data.public_url_configured ? (
                <div className="font-medium text-emerald-700">
                  发件 worker 正常 · 待发 {workerQuery.data.pending_recipients} · 发送中{" "}
                  {workerQuery.data.sending_recipients} · 失败{" "}
                  {workerQuery.data.failed_recipients}
                </div>
              ) : null}
            </div>
          ) : null}
          {campaignsQuery.isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-10 w-full" />
              ))}
            </div>
          ) : campaignsQuery.error ? (
            <div className="p-4">
              <ErrorState
                message={
                  campaignsQuery.error instanceof Error
                    ? campaignsQuery.error.message
                    : "加载发件任务失败"
                }
              />
            </div>
          ) : campaigns.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="还没有发件任务"
                description="从已采用未联系的达人中创建第一批外联邮件。"
                actions={
                  <Button size="sm" onClick={() => changeTab("new")}>
                    新建发件
                  </Button>
                }
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>任务</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>发件账号</TableHead>
                  <TableHead className="text-right">收件人</TableHead>
                  <TableHead>计划开始</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {campaigns.map((campaign) => (
                  <TableRow key={campaign.id}>
                    <TableCell>
                      <div className="font-medium">{campaign.name}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {campaign.subject}
                      </div>
                    </TableCell>
                    <TableCell>{statusBadge(campaign.status)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {campaign.account_from_email || campaign.account_label || "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {fmtNumber(campaign.recipients)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {campaign.scheduled_start
                        ? fmtDateTime(campaign.scheduled_start)
                        : "立即"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setDetailPage(1);
                          setSelectedCampaignId(campaign.id);
                        }}
                      >
                        查看
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-6">
            {settingsQuery.data && !settingsQuery.data.email_public_url ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                未配置 CODATA_PUBLIC_URL：邮件只能使用“回复退订”，无法附带一键退订链接。
                对外发信前建议配置为可公开访问的地址。
              </div>
            ) : null}
            <Section title="1. 基本信息">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="campaign-name">任务名称</Label>
                  <Input
                    id="campaign-name"
                    value={wizardName}
                    onChange={(event) => setWizardName(event.target.value)}
                    placeholder="例如：护肤达人首轮外联"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="campaign-account">发件账号</Label>
                  <Select
                    value={wizardAccountId}
                    onValueChange={setWizardAccountId}
                  >
                    <SelectTrigger id="campaign-account" className="w-full">
                      <SelectValue placeholder="选择发件账号" />
                    </SelectTrigger>
                    <SelectContent>
                      {accounts.map((account) => (
                        <SelectItem key={account.id} value={String(account.id)}>
                          {account.label || account.from_email}
                          {account.scope === "shared" ? "（共享）" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="campaign-subject">主题</Label>
                  <Input
                    id="campaign-subject"
                    value={subject}
                    onChange={(event) => setSubject(event.target.value)}
                    placeholder="支持 {{nickname}} 等变量"
                  />
                </div>
                <div className="space-y-2 sm:col-span-2">
                  <Label htmlFor="campaign-body">正文（纯文本）</Label>
                  <Textarea
                    id="campaign-body"
                    value={body}
                    onChange={(event) => setBody(event.target.value)}
                    rows={8}
                    placeholder="支持 {{nickname}}、{{platform}}、{{user_id}}、{{fans}}、{{profile_url}}"
                  />
                </div>
              </div>
            </Section>

            <Section title={`2. 选择达人（已选 ${selectedKeys.length}）`}>
              <div className="mb-3 flex flex-wrap items-end gap-2">
                <div className="w-full max-w-xs space-y-2">
                  <Label htmlFor="target-search">搜索</Label>
                  <Input
                    id="target-search"
                    value={targetSearch}
                    onChange={(event) => {
                      setTargetSearch(event.target.value);
                      setTargetPage(1);
                    }}
                    placeholder="昵称、账号或邮箱"
                  />
                </div>
                <div className="w-32 space-y-2">
                  <Label>平台</Label>
                  <Select
                    value={targetPlatform}
                    onValueChange={(value) => {
                      setTargetPlatform(value);
                      setTargetPage(1);
                    }}
                  >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {platformOptions.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="w-32 space-y-2">
                  <Label>负责人</Label>
                  <Select
                    value={targetOwner}
                    onValueChange={(value) => {
                      setTargetOwner(value);
                      setTargetPage(1);
                    }}
                  >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">全部</SelectItem>
                      <SelectItem value="unassigned">未分配</SelectItem>
                      {owners.map((owner) => (
                        <SelectItem key={owner.id} value={String(owner.id)}>
                          {owner.username}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {user?.can_collect ? (
                  <div className="w-40 space-y-2">
                    <Label>轮次</Label>
                    <Select
                      value={targetRound}
                      onValueChange={(value) => {
                        setTargetRound(value);
                        setTargetPage(1);
                      }}
                    >
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">全部</SelectItem>
                        {rounds.map((round) => (
                          <SelectItem key={round.id} value={String(round.id)}>
                            {round.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                ) : null}
                <div className="w-36 space-y-2">
                  <Label>邮箱</Label>
                  <Select
                    value={targetHasEmail}
                    onValueChange={(value) => {
                      setTargetHasEmail(value);
                      setTargetPage(1);
                    }}
                  >
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">全部</SelectItem>
                      <SelectItem value="with">仅有邮箱</SelectItem>
                      <SelectItem value="without">仅缺邮箱</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <Button
                  variant="outline"
                  onClick={() => void targetsQuery.refetch()}
                  disabled={targetsQuery.isFetching}
                >
                  <RefreshCw className={targetsQuery.isFetching ? "animate-spin" : ""} />
                  刷新
                </Button>
              </div>
              {targetsQuery.isLoading ? (
                <Skeleton className="h-40 w-full" />
              ) : targetsQuery.error ? (
                <ErrorState
                  message={
                    targetsQuery.error instanceof Error
                      ? targetsQuery.error.message
                      : "加载达人失败"
                  }
                />
              ) : targets.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  没有符合条件的“已采用未联系”达人。
                </p>
              ) : (
                <>
                  <div className="divide-y border-y">
                    {targets.map((target) => {
                      const key = `${target.platform}|${target.user_id}`;
                      const cooldown = Boolean(target.cooldown_until);
                      return (
                        <div
                          key={key}
                          className="flex flex-wrap items-center gap-3 py-2 text-sm"
                        >
                          <Checkbox
                            checked={Boolean(selectedTargets[key]) && !cooldown}
                            disabled={cooldown}
                            onCheckedChange={(value) =>
                              toggleTarget(target, value === true)
                            }
                            aria-label={`选择 ${target.nickname || target.user_id}`}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="truncate font-medium">
                              {target.nickname || target.user_id}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              {target.platform} · {target.user_id} · 粉丝{" "}
                              {target.fans != null ? fmtNumber(target.fans) : "—"}
                              {target.owner_username
                                ? ` · 跟进人 ${target.owner_username}`
                                : ""}
                            </div>
                          </div>
                          {cooldown ? (
                            <Badge
                              variant="outline"
                              className="border-amber-200 bg-amber-50 font-normal text-amber-700"
                            >
                              冷却中 ·{" "}
                              {target.cooldown_reason === "creator" ? "达人" : "邮箱"}至{" "}
                              {fmtDateTime(target.cooldown_until as string)}
                            </Badge>
                          ) : target.email ? (
                            <span className="text-xs text-muted-foreground">
                              {target.email}
                            </span>
                          ) : (
                            <Input
                              type="email"
                              value={emailOverrides[key] ?? ""}
                              onChange={(event) =>
                                setEmailOverrides((current) => ({
                                  ...current,
                                  [key]: event.target.value,
                                }))
                              }
                              placeholder="补充邮箱"
                              className="h-8 w-52 text-xs"
                              aria-label={`${target.nickname || target.user_id} 的邮箱`}
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground">
                    <span>
                      共 {fmtNumber(targetsQuery.data?.total ?? 0)} 位
                    </span>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={targetPage <= 1}
                        onClick={() => setTargetPage((value) => Math.max(1, value - 1))}
                      >
                        上一页
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={
                          (targetsQuery.data?.total ?? 0) <=
                          targetPage * (targetsQuery.data?.page_size ?? 50)
                        }
                        onClick={() => setTargetPage((value) => value + 1)}
                      >
                        下一页
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </Section>
          </div>

          <div className="space-y-6">
            <Section title="3. 发送计划">
              <div className="space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="campaign-start">计划开始（留空立即排队）</Label>
                  <Input
                    id="campaign-start"
                    type="datetime-local"
                    value={scheduledStart}
                    onChange={(event) => setScheduledStart(event.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">时区：{timezone}</p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label htmlFor="window-start">开始时段</Label>
                    <Input
                      id="window-start"
                      type="time"
                      value={windowStart}
                      onChange={(event) => setWindowStart(event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="window-end">结束时段</Label>
                    <Input
                      id="window-end"
                      type="time"
                      value={windowEnd}
                      onChange={(event) => setWindowEnd(event.target.value)}
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>发送日</Label>
                  <div className="flex flex-wrap gap-2">
                    {WEEKDAYS.map((day) => (
                      <label key={day.value} className="flex items-center gap-1 text-xs">
                        <Checkbox
                          checked={weekdays.includes(day.value)}
                          onCheckedChange={(checked) =>
                            setWeekdays((current) =>
                              checked
                                ? [...new Set([...current, day.value])].sort()
                                : current.filter((item) => item !== day.value),
                            )
                          }
                        />
                        {day.label}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <div className="space-y-2">
                    <Label htmlFor="interval">间隔秒</Label>
                    <Input
                      id="interval"
                      type="number"
                      min={1}
                      max={3600}
                      value={intervalSeconds}
                      onChange={(event) =>
                        setIntervalSeconds(Math.max(1, Number(event.target.value) || 1))
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="jitter">抖动秒</Label>
                    <Input
                      id="jitter"
                      type="number"
                      min={0}
                      max={600}
                      value={jitterSeconds}
                      onChange={(event) =>
                        setJitterSeconds(Math.max(0, Number(event.target.value) || 0))
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="daily">日上限</Label>
                    <Input
                      id="daily"
                      type="number"
                      min={1}
                      max={500}
                      value={dailyLimit}
                      onChange={(event) =>
                        setDailyLimit(Math.max(1, Number(event.target.value) || 1))
                      }
                    />
                  </div>
                </div>
                <div className="flex items-center justify-between gap-3 border-t pt-3">
                  <div className="space-y-0.5">
                    <Label htmlFor="auto-mark">发送成功后标记已联系</Label>
                    <p className="text-xs text-muted-foreground">
                      仅当合作状态仍为“未联系”时更新。
                    </p>
                  </div>
                  <Switch
                    id="auto-mark"
                    checked={autoMark}
                    onCheckedChange={setAutoMark}
                  />
                </div>
              </div>
            </Section>

            <Section title="4. 预览与确认">
              <div className="space-y-2 text-sm">
                <p className="font-medium">{renderPreview(subject || "（未填写主题）", previewTarget)}</p>
                <p className="whitespace-pre-wrap text-muted-foreground">
                  {renderPreview(body || "（未填写正文）", previewTarget)}
                </p>
              </div>
              {unknownVariables.length ? (
                <p className="mt-3 text-xs text-rose-600" role="alert">
                  未知模板变量：{unknownVariables.join("、")}。请修正后再计划发送。
                </p>
              ) : null}
              <p className="mt-3 text-xs text-muted-foreground">
                实际发送时还会附加退订说明；是否包含一键退订链接取决于 CODATA_PUBLIC_URL 配置。
              </p>
              <Button
                className="mt-4 w-full"
                onClick={submitCampaign}
                disabled={
                  campaignMutation.isPending ||
                  selectedKeys.length === 0 ||
                  unknownVariables.length > 0 ||
                  !subject.trim() ||
                  !body.trim()
                }
              >
                {campaignMutation.isPending ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <Send />
                )}
                创建并计划发送（{selectedKeys.length} 位）
              </Button>
            </Section>
          </div>
        </div>
      )}

      <Dialog open={accountDialogOpen} onOpenChange={setAccountDialogOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingAccount ? "编辑发件账号" : "添加发件账号"}</DialogTitle>
            <DialogDescription>
              密码使用独立密钥加密保存；个人账号只有自己可见，共享账号由管理员维护。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            {isAdmin && !editingAccount ? (
              <div className="space-y-2">
                <Label htmlFor="account-scope">账号范围</Label>
                <Select
                  value={accountForm.scope}
                  onValueChange={(value) =>
                    setAccountForm((current) => ({ ...current, scope: value }))
                  }
                >
                  <SelectTrigger id="account-scope"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="personal">个人</SelectItem>
                    <SelectItem value="shared">共享（全员可用）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            ) : null}
            <div className="space-y-2">
              <Label htmlFor="account-preset">服务商预设</Label>
              <Select
                value={accountPreset}
                onValueChange={(value) => {
                  setAccountPreset(value);
                  const preset = SMTP_PRESETS.find((item) => item.value === value);
                  if (preset && preset.value !== "custom") {
                    setAccountForm((current) => ({
                      ...current,
                      host: preset.host,
                      port: preset.port,
                      security: preset.security,
                    }));
                  }
                }}
              >
                <SelectTrigger id="account-preset"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {SMTP_PRESETS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-label">名称（可选）</Label>
              <Input
                id="account-label"
                value={accountForm.label ?? ""}
                onChange={(event) =>
                  setAccountForm((current) => ({ ...current, label: event.target.value }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-from-name">发件人名称</Label>
              <Input
                id="account-from-name"
                value={accountForm.from_name ?? ""}
                onChange={(event) =>
                  setAccountForm((current) => ({ ...current, from_name: event.target.value }))
                }
              />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="account-from-email">发件邮箱</Label>
              <Input
                id="account-from-email"
                type="email"
                value={accountForm.from_email}
                onChange={(event) =>
                  setAccountForm((current) => ({ ...current, from_email: event.target.value }))
                }
                placeholder="ops@example.com"
              />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="account-reply-to">回复邮箱（可选）</Label>
              <Input
                id="account-reply-to"
                type="email"
                value={accountForm.reply_to ?? ""}
                onChange={(event) =>
                  setAccountForm((current) => ({ ...current, reply_to: event.target.value }))
                }
                placeholder="replies@example.com"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-host">SMTP 主机</Label>
              <Input
                id="account-host"
                value={accountForm.host}
                onChange={(event) =>
                  setAccountForm((current) => ({ ...current, host: event.target.value }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-port">端口</Label>
              <Input
                id="account-port"
                type="number"
                value={accountForm.port}
                onChange={(event) =>
                  setAccountForm((current) => ({
                    ...current,
                    port: Number(event.target.value) || 0,
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-security">加密方式</Label>
              <Select
                value={accountForm.security}
                onValueChange={(value) =>
                  setAccountForm((current) => ({ ...current, security: value }))
                }
              >
                <SelectTrigger id="account-security"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ssl">SSL（465）</SelectItem>
                  <SelectItem value="starttls">STARTTLS（587）</SelectItem>
                  <SelectItem value="none">明文（不推荐）</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-username">SMTP 用户名</Label>
              <Input
                id="account-username"
                value={accountForm.username}
                onChange={(event) =>
                  setAccountForm((current) => ({ ...current, username: event.target.value }))
                }
              />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="account-password">
                {editingAccount ? "新密码或授权码（留空不修改）" : "密码 / 授权码"}
              </Label>
              <Input
                id="account-password"
                type="password"
                value={accountForm.password ?? ""}
                onChange={(event) =>
                  setAccountForm((current) => ({ ...current, password: event.target.value }))
                }
                autoComplete="new-password"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-daily">每日上限</Label>
              <Input
                id="account-daily"
                type="number"
                min={1}
                max={500}
                value={accountForm.daily_limit ?? 20}
                onChange={(event) =>
                  setAccountForm((current) => ({
                    ...current,
                    daily_limit: Math.max(1, Number(event.target.value) || 1),
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="account-interval">最小发送间隔（秒）</Label>
              <Input
                id="account-interval"
                type="number"
                min={1}
                max={3600}
                value={accountForm.min_interval_seconds ?? 60}
                onChange={(event) =>
                  setAccountForm((current) => ({
                    ...current,
                    min_interval_seconds: Math.max(
                      1,
                      Number(event.target.value) || 1,
                    ),
                  }))
                }
              />
              <p className="text-xs text-muted-foreground">
                多个任务共用账号时按此间隔排队，取任务间隔与账号间隔的较大值。
              </p>
            </div>
            <div className="flex items-center justify-between gap-3 border-t pt-3 sm:col-span-2">
              <div>
                <Label htmlFor="account-enabled">启用发件账号</Label>
                <p className="text-xs text-muted-foreground">停用后，相关活动会在下一轮暂停。</p>
              </div>
              <Switch
                id="account-enabled"
                checked={accountForm.enabled ?? true}
                onCheckedChange={(enabled) =>
                  setAccountForm((current) => ({ ...current, enabled }))
                }
              />
            </div>
            {editingAccount ? (
              <div className="space-y-2 border-t pt-3 sm:col-span-2">
                <Label htmlFor="account-test-recipient">真实测试收件人</Label>
                <div className="flex gap-2">
                  <Input
                    id="account-test-recipient"
                    type="email"
                    value={testRecipient}
                    onChange={(event) => setTestRecipient(event.target.value)}
                    placeholder="name@example.com"
                  />
                  <Button
                    variant="outline"
                    onClick={() =>
                      testMutation.mutate({
                        accountId: editingAccount.id,
                        sendTestTo: testRecipient.trim(),
                      })
                    }
                    disabled={testMutation.isPending || !testRecipient.trim()}
                  >
                    {testMutation.isPending ? <LoaderCircle className="animate-spin" /> : <Send />}
                    发送测试
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  会真实发送一封测试邮件，并使用已保存的账号配置；如有修改请先保存。普通用户只能发送到当前发件邮箱。
                </p>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAccountDialogOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => accountMutation.mutate()}
              disabled={
                accountMutation.isPending ||
                !accountForm.from_email ||
                !accountForm.host ||
                !accountForm.username ||
                (!editingAccount && !accountForm.password)
              }
            >
              {accountMutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={selectedCampaignId !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedCampaignId(null);
            setDetailPage(1);
          }
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{detailQuery.data?.campaign.name || "发件任务"}</DialogTitle>
            <DialogDescription>
              {detailQuery.data
                ? `${detailQuery.data.campaign.account_from_email || "—"} · 计划 ${
                    detailQuery.data.campaign.scheduled_start
                      ? fmtDateTime(detailQuery.data.campaign.scheduled_start)
                      : "立即"
                  }`
                : "加载中"}
            </DialogDescription>
          </DialogHeader>
          {detailQuery.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : detailQuery.error ? (
            <ErrorState
              message={
                detailQuery.error instanceof Error
                  ? detailQuery.error.message
                  : "加载发件任务失败"
              }
            />
          ) : detailQuery.data ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {statusBadge(detailQuery.data.campaign.status)}
                <span className="text-muted-foreground">
                  收件人 {detailQuery.data.campaign.recipients} · 已发{" "}
                  {detailQuery.data.campaign.sent} · 失败{" "}
                  {detailQuery.data.campaign.failed} · 跳过{" "}
                  {detailQuery.data.campaign.skipped_count}
                </span>
              </div>
              <p className="text-sm font-medium">
                {detailQuery.data.campaign.subject}
              </p>
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                {detailQuery.data.campaign.body}
              </p>
              <div className="max-h-64 divide-y overflow-y-auto border-y">
                {detailQuery.data.recipients.map((recipient) => (
                  <div
                    key={recipient.id}
                    className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-xs"
                  >
                    <span>
                      {recipient.creator_nickname || recipient.creator_user_id} ·{" "}
                      {recipient.email}
                    </span>
                    <span className="text-muted-foreground">
                      {RECIPIENT_STATUS_LABELS[recipient.status] ?? recipient.status} ·
                      尝试 {recipient.attempts}
                      {recipient.manual_retries
                        ? ` · 人工重试 ${recipient.manual_retries}`
                        : ""}
                      {recipient.error_message ? ` · ${recipient.error_message}` : ""}
                    </span>
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  共 {fmtNumber(detailQuery.data.total)} 位 · 第{" "}
                  {detailQuery.data.page} 页
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={detailQuery.data.page <= 1}
                    onClick={() => setDetailPage((value) => Math.max(1, value - 1))}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={
                      detailQuery.data.total <=
                      detailQuery.data.page * detailQuery.data.page_size
                    }
                    onClick={() => setDetailPage((value) => value + 1)}
                  >
                    下一页
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setSelectedCampaignId(null)}
                >
                  关闭
                </Button>
                {detailQuery.data.campaign.status === "scheduled" ||
                detailQuery.data.campaign.status === "sending" ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      void runCampaignAction(
                        () => api.pauseEmailCampaign(detailQuery.data!.campaign.id),
                        () => "发件任务已暂停；正在发送的一封可能仍会完成",
                      )
                    }
                  >
                    暂停
                  </Button>
                ) : null}
                {detailQuery.data.campaign.status === "paused" ? (
                  <Button
                    variant="outline"
                    onClick={() =>
                      void runCampaignAction(
                        () => api.resumeEmailCampaign(detailQuery.data!.campaign.id),
                        () => "发件任务已恢复",
                      )
                    }
                  >
                    恢复
                  </Button>
                ) : null}
                {detailQuery.data.campaign.failed > 0 &&
                detailQuery.data.campaign.status !== "cancelled" ? (
                  <Button
                    variant="outline"
                    onClick={() => {
                      if (
                        !window.confirm(
                          "仅重试确认失败的邮件。若失败原因为“状态未知”，邮件可能已送达，重试可能造成重复外联。确定继续吗？",
                        )
                      ) {
                        return;
                      }
                      void runCampaignAction(
                        () => api.retryFailedEmailRecipients(detailQuery.data!.campaign.id),
                        (result) =>
                          result.retried
                            ? `已将 ${result.retried} 封失败邮件重新排队`
                            : "没有可重试的邮件（可能已达人工重试上限）",
                      );
                    }}
                  >
                    重试失败
                  </Button>
                ) : null}
                {detailQuery.data.campaign.status === "draft" ||
                detailQuery.data.campaign.status === "scheduled" ||
                detailQuery.data.campaign.status === "sending" ||
                detailQuery.data.campaign.status === "paused" ? (
                  <Button
                    variant="outline"
                    onClick={() => {
                      if (!window.confirm("确定取消这个发件任务？取消后不能恢复。")) return;
                      void runCampaignAction(
                        () => api.cancelEmailCampaign(detailQuery.data!.campaign.id),
                        () => "发件任务已取消",
                      );
                    }}
                  >
                    取消任务
                  </Button>
                ) : null}
                {detailQuery.data.campaign.status === "draft" ? (
                  <Button
                    variant="destructive"
                    onClick={async () => {
                      if (!window.confirm("删除这个草稿？")) return;
                      const result = await runCampaignAction(
                        () => api.deleteEmailCampaign(detailQuery.data!.campaign.id),
                        () => "草稿已删除",
                      );
                      if (result) setSelectedCampaignId(null);
                    }}
                  >
                    <Trash2 /> 删除草稿
                  </Button>
                ) : null}
              </DialogFooter>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </Page>
  );
}
