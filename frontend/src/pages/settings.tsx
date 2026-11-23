import { useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Copy, Eye, EyeOff, KeyRound, LoaderCircle, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import logo from "@/assets/logo.svg";
import { api } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
import { CHANGELOG } from "@/shared/changelog";
import { fmtNumber, safeHttpUrl } from "@/shared/format";
import { useRound } from "@/shared/round-context";
import { APP_VERSION, BUILD_TIME } from "@/shared/version";
import type { Balance, PlatformProviderPrice, ProviderSettings } from "@/shared/types";
import { ErrorState, Page, PageHeader, Section } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const SETTINGS_TABS = ["connection", "storage", "about"] as const;
type SettingsTab = (typeof SETTINGS_TABS)[number];

const UPDATED_AT = new Date(BUILD_TIME).toLocaleString("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const DATA_POLICY = [
  { label: "数据存储", value: "SQLite 数据库保存在本机 data 目录，启用 WAL 与安全写入。" },
  {
    label: "自动备份",
    value:
      "数据库结构升级前自动备份；默认保留 90 天、最多 10 份，可用 CODATA_BACKUP_RETENTION_DAYS / CODATA_BACKUP_RETENTION_COUNT 调整（0 关闭）。",
  },
  { label: "原始响应", value: "服务商原始响应保存在 data/raw，便于复核与重新解析。" },
  { label: "运行日志", value: "采集与运行日志保存在 data/logs。" },
  { label: "访问凭据", value: "访问令牌仅写入 .env，界面只显示配置状态。" },
  { label: "网络边界", value: "面板默认只监听本机；公网访问需 Nginx/HTTPS 与账号登录。" },
  { label: "采集边界", value: "仅采集公开数据，并遵守各服务商的使用条款。" },
];

function formatCny(value: string) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return value;
  return amount.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

export function SettingsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { counts, settings: bootstrapSettings } = useRound();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const availableTabs: readonly SettingsTab[] = isAdmin
    ? SETTINGS_TABS
    : SETTINGS_TABS.filter((item) => item !== "storage");
  const tab: SettingsTab = availableTabs.includes(requestedTab as SettingsTab)
    ? (requestedTab as SettingsTab)
    : "connection";
  const setTab = (next: string) => {
    const params = new URLSearchParams(searchParams);
    if (next === "connection") params.delete("tab");
    else params.set("tab", next);
    setSearchParams(params, { replace: true });
  };
  const queryClient = useQueryClient();
  const [balance, setBalance] = useState<Balance | null>(null);
  const [balanceError, setBalanceError] = useState("");
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [rnoteBalance, setRnoteBalance] = useState<Balance | null>(null);
  const [rnoteBalanceError, setRnoteBalanceError] = useState("");
  const [rnoteBalanceLoading, setRnoteBalanceLoading] = useState(false);
  const [extraBalances, setExtraBalances] = useState<Record<string, { data: Balance | null; error: string; loading: boolean }>>({});
  const [tokenOpen, setTokenOpen] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [rnoteTokenOpen, setRnoteTokenOpen] = useState(false);
  const [rnoteTokenInput, setRnoteTokenInput] = useState("");
  const [showRnoteToken, setShowRnoteToken] = useState(false);
  const [providerTokenOpen, setProviderTokenOpen] = useState(false);
  const [providerTokenTarget, setProviderTokenTarget] = useState<ProviderSettings | null>(null);
  const [providerTokenInput, setProviderTokenInput] = useState("");
  const [showProviderToken, setShowProviderToken] = useState(false);
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
    initialData: bootstrapSettings ?? undefined,
  });
  const tokenMutation = useMutation({
    mutationFn: (token: string) => api.updateDataflowToken(token),
    onSuccess: async (nextSettings) => {
      const remaining = nextSettings.balance?.remaining;
      queryClient.setQueryData(["settings"], nextSettings);
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      setTokenOpen(false);
      setTokenInput("");
      setShowToken(false);
      tokenMutation.reset();
      setBalance(nextSettings.balance);
      setBalanceError("");
      toast.success(
        remaining != null
          ? `令牌已验证，当前剩余 ${fmtNumber(remaining)} token`
          : "令牌已验证并更新，后续采集将使用新令牌",
      );
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "访问令牌保存失败");
    },
  });
  const rnoteTokenMutation = useMutation({
    mutationFn: (token: string) => api.updateRnoteToken(token),
    onSuccess: async (nextSettings) => {
      queryClient.setQueryData(["settings"], nextSettings);
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      setRnoteTokenOpen(false);
      setRnoteTokenInput("");
      setShowRnoteToken(false);
      rnoteTokenMutation.reset();
      toast.success("Rnote 令牌已验证并更新");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Rnote 访问令牌保存失败");
    },
  });
  const providerTokenMutation = useMutation({
    mutationFn: ({ providerId, token }: { providerId: string; token: string }) =>
      api.updateProviderToken(providerId, token),
    onSuccess: async (nextSettings) => {
      queryClient.setQueryData(["settings"], nextSettings);
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      setProviderTokenOpen(false);
      setProviderTokenTarget(null);
      setProviderTokenInput("");
      setShowProviderToken(false);
      providerTokenMutation.reset();
      toast.success("服务商访问令牌已保存");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "服务商访问令牌保存失败");
    },
  });
  const openProviderTokenDialog = (provider: ProviderSettings) => {
    providerTokenMutation.reset();
    setProviderTokenTarget(provider);
    setProviderTokenInput("");
    setShowProviderToken(false);
    setProviderTokenOpen(true);
  };

  const openTokenDialog = () => {
    tokenMutation.reset();
    setTokenInput("");
    setShowToken(false);
    setTokenOpen(true);
  };
  const openRnoteTokenDialog = () => {
    rnoteTokenMutation.reset();
    setRnoteTokenInput("");
    setShowRnoteToken(false);
    setRnoteTokenOpen(true);
  };

  const copy = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label}已复制`);
    } catch {
      toast.error("复制失败，请手动选择文本");
    }
  };

  const queryBalance = async () => {
    setBalanceLoading(true);
    setBalanceError("");
    try {
      const result = await api.getBalance();
      setBalance(result);
    } catch (error) {
      setBalanceError(error instanceof Error ? error.message : "查询失败");
    } finally {
      setBalanceLoading(false);
    }
  };

  const queryRnoteBalance = async () => {
    setRnoteBalanceLoading(true);
    setRnoteBalanceError("");
    try {
      const result = await api.getBalance("rnote");
      setRnoteBalance(result);
    } catch (error) {
      setRnoteBalanceError(error instanceof Error ? error.message : "查询失败");
    } finally {
      setRnoteBalanceLoading(false);
    }
  };

  const queryExtraBalance = async (providerId: string) => {
    setExtraBalances((current) => ({
      ...current,
      [providerId]: { data: current[providerId]?.data ?? null, error: "", loading: true },
    }));
    try {
      const result = await api.getBalance(providerId);
      setExtraBalances((current) => ({
        ...current,
        [providerId]: { data: result, error: "", loading: false },
      }));
    } catch (error) {
      setExtraBalances((current) => ({
        ...current,
        [providerId]: {
          data: current[providerId]?.data ?? null,
          error: error instanceof Error ? error.message : "查询失败",
          loading: false,
        },
      }));
    }
  };

  if (settingsQuery.isLoading) {
    return (
      <Page>
        <PageHeader title="设置" />
        <Skeleton className="h-40 w-full" />
      </Page>
    );
  }

  if (settingsQuery.error) {
    return (
      <Page>
        <ErrorState message={settingsQuery.error instanceof Error ? settingsQuery.error.message : "加载设置失败"} />
      </Page>
    );
  }

  const settings = settingsQuery.data;
  if (!settings) return null;

  const platformPrices = new Map<string, { code: string; label: string; price: PlatformProviderPrice }[]>();
  for (const platform of settings.platforms) {
    for (const option of platform.providers) {
      const entries = platformPrices.get(option.id) ?? [];
      entries.push({ code: platform.code, label: platform.label, price: option });
      platformPrices.set(option.id, entries);
    }
  }
  const priceFor = (providerId: string, code: string) =>
    platformPrices.get(providerId)?.find((entry) => entry.code === code)?.price;
  const outputDir = settings.output_dir.replace(/[\\/]+$/, "");
  const pathSep = outputDir.includes("\\") ? "\\" : "/";
  const databasePath = `${outputDir}${pathSep}codata.sqlite`;
  const rawPath = `${outputDir}${pathSep}raw`;
  const logsPath = `${outputDir}${pathSep}logs`;
  const metrics = [
    { label: "轮次", value: counts?.rounds ?? 0 },
    { label: "关键词", value: counts?.keywords ?? 0 },
    { label: "笔记", value: counts?.notes ?? 0 },
    { label: "达人", value: counts?.creators ?? 0 },
    { label: "线索", value: counts?.leads ?? 0 },
    { label: "达人-轮次", value: counts?.creator_rounds ?? 0 },
  ];

  return (
    <Page className="flex min-h-full flex-col">
      <PageHeader
        title="设置"
        description="访问令牌可在此直接更换并立即生效；其他配置修改项目根目录的 .env 后重启服务生效。"
      />

      {!settings.credentials_ok ? (
        <div className="border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          接口尚未配置：请至少填写 DataFlow 或 Rnote 的访问令牌，采集功能才可用。
        </div>
      ) : null}

      <Card className="py-0">
        <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-3 xl:grid-cols-6">
          {metrics.map((item) => (
            <div key={item.label} className="min-w-0 bg-card px-4 py-3">
              <p className="text-xs text-muted-foreground">{item.label}</p>
              <p className="mt-1 text-xl leading-none font-semibold tabular-nums">{fmtNumber(item.value)}</p>
            </div>
          ))}
        </div>
      </Card>

      <Tabs value={tab} onValueChange={setTab} className="flex flex-1 flex-col">
        <TabsList>
          <TabsTrigger value="connection">连接与用量</TabsTrigger>
          {isAdmin ? <TabsTrigger value="storage">数据与缓存</TabsTrigger> : null}
          <TabsTrigger value="about">关于</TabsTrigger>
        </TabsList>

        <TabsContent value="connection" className="space-y-8 pt-6">
          <Section
            title="采集服务商"
            description="集中管理所有采集接口、令牌和计价；令牌保存后立即生效。"
            contentClassName="px-0"
          >
            <Table className="min-w-[1080px] table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[240px]">服务商</TableHead>
                  <TableHead className="w-[170px]">访问令牌</TableHead>
                  {settings.platforms.map((platform) => (
                    <TableHead key={platform.code} className="w-[110px] px-2 text-center">
                      {platform.label}
                    </TableHead>
                  ))}
                  <TableHead className="w-[120px] text-right">余额</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {settings.providers.map((provider) => {
                  const openToken = provider.id === "dataflow"
                    ? openTokenDialog
                    : provider.id === "rnote"
                      ? openRnoteTokenDialog
                      : () => openProviderTokenDialog(provider);
                  return (
                    <TableRow key={provider.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{provider.label}</span>
                          {provider.id === "dataflow" ? (
                            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">主</span>
                          ) : null}
                        </div>
                        {provider.host ? (
                          <a
                            href={safeHttpUrl(provider.host) || undefined}
                            target="_blank"
                            rel="noreferrer"
                            title={provider.host}
                            className="mt-0.5 block max-w-[210px] truncate font-mono text-[11px] text-muted-foreground underline-offset-4 hover:underline"
                          >
                            {provider.host.replace(/^https?:\/\//, "")}
                          </a>
                        ) : (
                          <span className="text-xs text-muted-foreground">未配置地址</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-start gap-2">
                          <Badge
                            variant="outline"
                            className={
                              provider.token_configured
                                ? "border-emerald-200 bg-emerald-50 font-normal text-emerald-700"
                                : "font-normal text-muted-foreground"
                            }
                          >
                            {provider.token_configured ? "已配置" : "未配置"}
                          </Badge>
                          {isAdmin ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              className="pr-0"
                              onClick={openToken}
                            >
                              <KeyRound />
                              {provider.token_configured ? "更换" : "设置"}
                            </Button>
                          ) : null}
                        </div>
                      </TableCell>
                      {settings.platforms.map((platform) => {
                        const price = priceFor(provider.id, platform.code);
                        return (
                          <TableCell key={platform.code} className="px-2 text-center">
                            {price ? (
                              <div className="space-y-0.5">
                                <div className="text-xs whitespace-nowrap tabular-nums">
                                  {price.search_cost_native} {price.search_currency}
                                </div>
                                <div
                                  className="text-[10px] whitespace-nowrap tabular-nums text-muted-foreground"
                                  title={`≈ ${fmtNumber(price.search_cost_tokens)} token`}
                                >
                                  ≈¥{formatCny(price.search_cost_cny)}
                                </div>
                              </div>
                            ) : (
                              <span className="text-xs text-muted-foreground">—</span>
                            )}
                          </TableCell>
                        );
                      })}
                      <TableCell className="text-right">
                        {!provider.token_configured ? (
                          <span className="text-xs text-muted-foreground">—</span>
                        ) : provider.id === "dataflow" ? (
                          <div className="flex items-center justify-end gap-2">
                            {balanceError ? (
                              <span className="max-w-40 truncate text-xs text-destructive" title={balanceError}>
                                查询失败
                              </span>
                            ) : (
                              <span className="text-xs tabular-nums">
                                {balanceLoading
                                  ? "查询中…"
                                  : balance?.remaining == null
                                    ? balance?.text || "未查询"
                                    : `${fmtNumber(balance.remaining)} token`}
                              </span>
                            )}
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-sm"
                              className="-my-[3.5px]"
                              title="查询 DataFlow 余额"
                              aria-label="查询 DataFlow 余额"
                              onClick={() => void queryBalance()}
                              disabled={balanceLoading}
                            >
                              <RefreshCw className={balanceLoading ? "animate-spin" : undefined} />
                            </Button>
                          </div>
                        ) : provider.id === "rnote" ? (
                          <div className="flex items-center justify-end gap-2">
                            {rnoteBalanceError ? (
                              <span className="max-w-40 truncate text-xs text-destructive" title={rnoteBalanceError}>
                                查询失败
                              </span>
                            ) : (
                              <span className="text-xs tabular-nums">
                                {rnoteBalanceLoading
                                  ? "查询中…"
                                  : rnoteBalance?.remaining == null
                                    ? rnoteBalance?.text || "未查询"
                                    : rnoteBalance.text}
                              </span>
                            )}
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-sm"
                              className="-my-[3.5px]"
                              title="查询 Rnote 余额"
                              aria-label="查询 Rnote 余额"
                              onClick={() => void queryRnoteBalance()}
                              disabled={rnoteBalanceLoading}
                            >
                              <RefreshCw className={rnoteBalanceLoading ? "animate-spin" : undefined} />
                            </Button>
                          </div>
                        ) : provider.id === "justoneapi" ? (
                          <span className="text-xs text-muted-foreground">不支持</span>
                        ) : (
                          <div className="flex items-center justify-end gap-2">
                            {extraBalances[provider.id]?.error ? (
                              <span
                                className="max-w-40 truncate text-xs text-destructive"
                                title={extraBalances[provider.id]?.error}
                              >
                                查询失败
                              </span>
                            ) : (
                              <span className="text-xs tabular-nums">
                                {extraBalances[provider.id]?.loading
                                  ? "查询中…"
                                  : extraBalances[provider.id]?.data?.text || "未查询"}
                              </span>
                            )}
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-sm"
                              className="-my-[3.5px]"
                              title={`查询 ${provider.label} 余额`}
                              aria-label={`查询 ${provider.label} 余额`}
                              onClick={() => void queryExtraBalance(provider.id)}
                              disabled={extraBalances[provider.id]?.loading}
                            >
                              <RefreshCw className={extraBalances[provider.id]?.loading ? "animate-spin" : undefined} />
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            <div className="flex items-center justify-between border-t px-4 py-3 text-sm">
              <span className="text-muted-foreground">达人缓存（全局）</span>
              <span>{settings.cache_days} 天</span>
            </div>
            <div className="flex items-center justify-between border-t px-4 py-3 text-sm">
              <span className="text-muted-foreground">计价换算</span>
              <span className="tabular-nums">
                1 token = ¥{settings.token_cny} · 1 USD = ¥{settings.usd_cny}
              </span>
            </div>
            <p className="border-t px-4 py-3 text-xs text-muted-foreground">
              DataFlow、Rnote、EveryInfra、TikHub、SocialDataX、RedFoxHub 可查询余额；JustOneAPI 暂未找到公开余额接口。价格按当前配置和汇率折算，实际扣费以服务商账单为准。
            </p>
          </Section>
        </TabsContent>

        {isAdmin ? <TabsContent value="storage" className="space-y-8 pt-6">
          <Section title="数据与路径" description="所有采集数据默认保存在项目 data 目录。" contentClassName="px-0">
            <Table className="min-w-[640px] table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[180px]">项目</TableHead>
                  <TableHead>路径</TableHead>
                  <TableHead className="w-[80px] text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <CopyRow label="数据库文件" value={databasePath} onCopy={copy} />
                <CopyRow label="原始响应" value={rawPath} onCopy={copy} />
                <CopyRow label="运行日志" value={logsPath} onCopy={copy} />
                <CopyRow label="输出目录" value={settings.output_dir} onCopy={copy} />
              </TableBody>
            </Table>
            <p className="border-t px-4 py-3 text-xs text-muted-foreground">
              SQLite 使用 WAL 与安全写入，无需手动维护。建议定期备份 codata.sqlite；原始响应与运行日志可按需清理，迁移或换机时复制整个 data 目录即可。
            </p>
          </Section>
        </TabsContent> : null}

        <TabsContent value="about" className="flex flex-1 flex-col pt-6">
          <div className="flex flex-1 flex-col gap-4">
            <div className="relative">
              <Section className="ring-0" title="Moidas Co-data" description="多平台信息数据处理工作台">
                <div className="max-w-5xl space-y-3 text-sm text-muted-foreground">
                  <p>
                    这是一个本地运行的研究工作台，把关键词搜索、作者去重、资料补全和人工标注放在同一流程里。
                  </p>
                  <p>
                    数据默认保存在本机 data 目录；只有采集请求会访问 .env 中配置的 DataFlow 或 Rnote 地址。面板默认只监听本机，公网部署请使用 Nginx/HTTPS 和账号登录。
                  </p>
                  <div className="flex flex-nowrap items-center gap-1 overflow-x-auto pt-1 text-xs">
                    {[
                      "React 19",
                      "TypeScript",
                      "Vite",
                      "Tailwind CSS 4",
                      "shadcn/ui",
                      "Recharts",
                      "TanStack Query",
                      "FastAPI",
                      "SQLite（WAL + FTS5）",
                    ].map((item) => (
                      <span
                        key={item}
                        className="shrink-0 whitespace-nowrap border border-foreground/15 bg-muted/20 px-2 py-1 font-mono text-xs leading-none text-muted-foreground"
                      >
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
                <img
                  src={logo}
                  alt=""
                  aria-hidden="true"
                  width={481}
                  height={424}
                  className="pointer-events-none absolute inset-y-0 right-0 hidden h-full w-auto opacity-10 select-none md:block"
                />
              </Section>
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 rounded-xl border border-foreground/10 [mask-image:linear-gradient(to_right,black_50%,transparent)]"
              />
            </div>
            <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <span className="font-mono">v{APP_VERSION}</span>
              <span aria-hidden="true">·</span>
              <span>更新于 {UPDATED_AT}</span>
              <span aria-hidden="true">·</span>
              <span>by Information Technology Department</span>
            </div>
            <div className="grid items-start gap-4 lg:grid-cols-2">
              <AboutInfoCard title="更新日志" description="记录产品能力的迭代">
                <ol className="space-y-4">
                  {CHANGELOG.map((release) => (
                    <li key={release.version} className="border-b pb-4 last:border-0 last:pb-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-medium">v{release.version}</span>
                        {release.version === APP_VERSION ? (
                          <Badge
                            variant="outline"
                            className="border-emerald-200 bg-emerald-50 text-[10px] font-normal text-emerald-700"
                          >
                            当前
                          </Badge>
                        ) : null}
                      </div>
                      <ul className="mt-2 space-y-1.5">
                        {release.highlights.map((item) => (
                          <li key={item} className="flex gap-2 text-sm text-muted-foreground">
                            <span
                              aria-hidden="true"
                              className="mt-[7px] size-1 shrink-0 rounded-full bg-foreground/25"
                            />
                            <span>{item}</span>
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ol>
              </AboutInfoCard>
              <AboutInfoCard title="数据与安全" description="数据仅保存在本机，令牌不会在界面回显">
                <dl className="space-y-3">
                  {DATA_POLICY.map((item) => (
                    <div key={item.label} className="grid grid-cols-[76px_1fr] gap-3">
                      <dt className="text-xs leading-5 text-muted-foreground">{item.label}</dt>
                      <dd className="text-sm leading-5">{item.value}</dd>
                    </div>
                  ))}
                </dl>
              </AboutInfoCard>
            </div>
          </div>
        </TabsContent>
      </Tabs>

      <Dialog
        open={tokenOpen}
        onOpenChange={(open) => {
          setTokenOpen(open);
          if (!open) {
            setTokenInput("");
            setShowToken(false);
            tokenMutation.reset();
          }
        }}
      >
        <DialogContent className="sm:max-w-[440px]">
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              const token = tokenInput.trim();
              if (token) tokenMutation.mutate(token);
            }}
          >
            <DialogHeader>
              <DialogTitle>更换 Dataflow 访问令牌</DialogTitle>
              <DialogDescription>
                验证通过后才会写入 .env，并立即用于后续采集。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="dataflow-token">新访问令牌</Label>
              <div className="relative">
                <Input
                  id="dataflow-token"
                  type={showToken ? "text" : "password"}
                  className="pr-10"
                  autoComplete="new-password"
                  spellCheck={false}
                  maxLength={4000}
                  value={tokenInput}
                  onChange={(event) => {
                    setTokenInput(event.target.value);
                    if (tokenMutation.isError) tokenMutation.reset();
                  }}
                  placeholder="粘贴新的访问令牌"
                  autoFocus
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="absolute top-1 right-1 text-muted-foreground"
                  aria-label={showToken ? "隐藏令牌" : "显示令牌"}
                  title={showToken ? "隐藏令牌" : "显示令牌"}
                  onClick={() => setShowToken((value) => !value)}
                >
                  {showToken ? <EyeOff /> : <Eye />}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                会调用一次余额接口验证令牌，不计费；运行中的任务不受影响。
              </p>
              {tokenMutation.isError ? (
                <p className="text-sm text-destructive" role="alert">
                  {tokenMutation.error instanceof Error ? tokenMutation.error.message : "保存失败"}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setTokenOpen(false)}
                disabled={tokenMutation.isPending}
              >
                取消
              </Button>
              <Button type="submit" disabled={!tokenInput.trim() || tokenMutation.isPending}>
                {tokenMutation.isPending ? (
                  <>
                    <LoaderCircle className="animate-spin" />
                    验证中…
                  </>
                ) : (
                  "保存并验证"
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={rnoteTokenOpen}
        onOpenChange={(open) => {
          setRnoteTokenOpen(open);
          if (!open) {
            setRnoteTokenInput("");
            setShowRnoteToken(false);
            rnoteTokenMutation.reset();
          }
        }}
      >
        <DialogContent className="sm:max-w-[440px]">
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              const token = rnoteTokenInput.trim();
              if (token) rnoteTokenMutation.mutate(token);
            }}
          >
            <DialogHeader>
              <DialogTitle>更换 Rnote 访问令牌</DialogTitle>
              <DialogDescription>
                验证通过后才会写入 .env，并立即用于后续采集。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="rnote-token">新访问令牌</Label>
              <div className="relative">
                <Input
                  id="rnote-token"
                  type={showRnoteToken ? "text" : "password"}
                  className="pr-10"
                  autoComplete="new-password"
                  spellCheck={false}
                  maxLength={4000}
                  value={rnoteTokenInput}
                  onChange={(event) => {
                    setRnoteTokenInput(event.target.value);
                    if (rnoteTokenMutation.isError) rnoteTokenMutation.reset();
                  }}
                  placeholder="粘贴 Rnote API Key"
                  autoFocus
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="absolute top-1 right-1 text-muted-foreground"
                  aria-label={showRnoteToken ? "隐藏令牌" : "显示令牌"}
                  title={showRnoteToken ? "隐藏令牌" : "显示令牌"}
                  onClick={() => setShowRnoteToken((value) => !value)}
                >
                  {showRnoteToken ? <EyeOff /> : <Eye />}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                会调用一次余额接口验证令牌，不计费；运行中的任务不受影响。
              </p>
              {rnoteTokenMutation.isError ? (
                <p className="text-sm text-destructive" role="alert">
                  {rnoteTokenMutation.error instanceof Error ? rnoteTokenMutation.error.message : "保存失败"}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setRnoteTokenOpen(false)}
                disabled={rnoteTokenMutation.isPending}
              >
                取消
              </Button>
              <Button type="submit" disabled={!rnoteTokenInput.trim() || rnoteTokenMutation.isPending}>
                {rnoteTokenMutation.isPending ? (
                  <>
                    <LoaderCircle className="animate-spin" />
                    验证中…
                  </>
                ) : (
                  "保存并验证"
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={providerTokenOpen}
        onOpenChange={(open) => {
          setProviderTokenOpen(open);
          if (!open) {
            setProviderTokenTarget(null);
            setProviderTokenInput("");
            setShowProviderToken(false);
            providerTokenMutation.reset();
          }
        }}
      >
        <DialogContent className="sm:max-w-[440px]">
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              const token = providerTokenInput.trim();
              if (token && providerTokenTarget) {
                providerTokenMutation.mutate({ providerId: providerTokenTarget.id, token });
              }
            }}
          >
            <DialogHeader>
              <DialogTitle>设置 {providerTokenTarget?.label ?? "服务商"} 访问令牌</DialogTitle>
              <DialogDescription>
                令牌保存到 .env；新服务商暂不做在线余额验证，保存后可在新建采集里使用。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="provider-token">访问令牌</Label>
              <div className="relative">
                <Input
                  id="provider-token"
                  type={showProviderToken ? "text" : "password"}
                  className="pr-10"
                  autoComplete="new-password"
                  spellCheck={false}
                  maxLength={4000}
                  value={providerTokenInput}
                  onChange={(event) => {
                    setProviderTokenInput(event.target.value);
                    if (providerTokenMutation.isError) providerTokenMutation.reset();
                  }}
                  placeholder="粘贴 API Key"
                  autoFocus
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="absolute top-1 right-1 text-muted-foreground"
                  aria-label={showProviderToken ? "隐藏令牌" : "显示令牌"}
                  title={showProviderToken ? "隐藏令牌" : "显示令牌"}
                  onClick={() => setShowProviderToken((value) => !value)}
                >
                  {showProviderToken ? <EyeOff /> : <Eye />}
                </Button>
              </div>
              {providerTokenMutation.isError ? (
                <p className="text-sm text-destructive" role="alert">
                  {providerTokenMutation.error instanceof Error ? providerTokenMutation.error.message : "保存失败"}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setProviderTokenOpen(false)}
                disabled={providerTokenMutation.isPending}
              >
                取消
              </Button>
              <Button type="submit" disabled={!providerTokenInput.trim() || providerTokenMutation.isPending}>
                {providerTokenMutation.isPending ? "保存中…" : "保存"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Page>
  );
}

function AboutInfoCard({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <Dialog>
      <Card className="gap-0 overflow-hidden py-0">
        <DialogTrigger asChild>
          <button
            type="button"
            className="flex w-full cursor-pointer items-start justify-between gap-4 px-4 py-4 text-left outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset"
          >
            <div className="space-y-1">
              <h2 className="font-heading text-base leading-snug font-medium">{title}</h2>
              <p className="text-sm text-muted-foreground">{description}</p>
            </div>
            <ChevronRight
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-muted-foreground"
            />
          </button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader className="pr-8">
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto">{children}</div>
        </DialogContent>
      </Card>
    </Dialog>
  );
}

function SettingRow({
  label,
  value,
  href,
  mono = false,
}: {
  label: string;
  value: string;
  href?: string;
  mono?: boolean;
}) {
  return (
    <TableRow>
      <TableCell className="text-muted-foreground">{label}</TableCell>
      <TableCell
        className={mono ? "truncate text-right font-mono text-xs" : "text-right"}
        title={value}
      >
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="rounded-sm underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            {value}
          </a>
        ) : (
          value
        )}
      </TableCell>
    </TableRow>
  );
}

function CopyRow({
  label,
  value,
  onCopy,
}: {
  label: string;
  value: string;
  onCopy: (value: string, label: string) => void;
}) {
  return (
    <TableRow>
      <TableCell className="text-muted-foreground">{label}</TableCell>
      <TableCell className="truncate font-mono text-xs" title={value}>{value}</TableCell>
      <TableCell className="text-right">
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={`复制${label}`}
          onClick={() => onCopy(value, label)}
        >
          <Copy />
        </Button>
      </TableCell>
    </TableRow>
  );
}
