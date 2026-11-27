import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { fmtNumber } from "@/shared/format";
import { useAuth } from "@/shared/auth-context";
import { useUnsavedGuard } from "@/shared/use-unsaved-guard";
import { useRound } from "@/shared/round-context";
import type { Keyword } from "@/shared/types";
import { ErrorState, Page, PageHeader, Section } from "@/components/page";
import { MultiSelect } from "@/components/multi-select";
import { NumberTicker } from "@/components/dissolve/number-ticker";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

const DEFAULT_YUAN_PER_TOKEN = 0.001;

function wordsOf(value: string) {
  return value
    .split(/[,，、;；\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function NewRoundPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { roundId, rounds, settings, refreshRounds, setRoundId } = useRound();
  const canCollect = user?.role === "admin" || Boolean(user?.can_collect);
  const keywordsQuery = useQuery({ queryKey: ["keywords"], queryFn: api.getKeywords });
  const [selected, setSelected] = useState<string[]>([]);
  const [newWords, setNewWords] = useState("");
  const [vertical, setVertical] = useState("");
  const [pages, setPages] = useState(1);
  const [maxCreators, setMaxCreators] = useState<number | null>(20);
  const [platform, setPlatform] = useState("xhs");
  const [provider, setProvider] = useState("auto");
  const [roundName, setRoundName] = useState("");
  const [refreshUsers, setRefreshUsers] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const hasUnsavedChanges = Boolean(
    selected.length || newWords.trim() || vertical.trim() || roundName.trim() || refreshUsers ||
    pages !== 1 || maxCreators !== 20 || platform !== "xhs" || provider !== "auto",
  );

  useUnsavedGuard(hasUnsavedChanges && !creating, "采集参数尚未提交，确定离开当前页面？");

  useEffect(() => {
    if (roundId === null && rounds.length > 0) {
      setRoundId(rounds[0].id);
    }
  }, [roundId, rounds, setRoundId]);

  const keywords = keywordsQuery.data ?? [];
  const enabledKeywords = keywords.filter((keyword) => Boolean(keyword.enabled));
  const words = useMemo(
    () => Array.from(new Set([...selected, ...wordsOf(newWords)])),
    [selected, newWords],
  );
  const yuanPerToken = settings?.token_cny ?? DEFAULT_YUAN_PER_TOKEN;
  const platformConfig = settings?.platforms.find((item) => item.code === platform);
  const providerOptions = platformConfig?.providers ?? [];
  const searchCalls = words.length * pages;
  const userCalls = maxCreators === null ? searchCalls * 10 : maxCreators;
  const automaticProvider = providerOptions
    .filter((item) => item.ready && item.enabled)
    .reduce<(typeof providerOptions)[number] | undefined>((best, item) => {
      if (!best) return item;
      const total = searchCalls * item.search_cost_tokens + userCalls * item.user_cost_tokens;
      const bestTotal = searchCalls * best.search_cost_tokens + userCalls * best.user_cost_tokens;
      return total < bestTotal ? item : best;
    }, undefined);
  const effectiveProviderId = provider === "auto" ? automaticProvider?.id : provider;
  const selectedProvider = providerOptions.find((item) => item.id === effectiveProviderId);
  const cost = selectedProvider?.search_cost_tokens ?? 0;
  const userCost = selectedProvider?.user_cost_tokens ?? cost;
  const totalCalls = words.length === 0 ? 0 : searchCalls + userCalls;
  const tokens = words.length === 0 ? 0 : searchCalls * cost + userCalls * userCost;
  const estimatedYuan = tokens * yuanPerToken;
  const yuanText = (Math.round(estimatedYuan * 100) / 100).toFixed(2);
  const credentialsOk = Boolean(selectedProvider?.ready);
  const validationMessage =
    words.length === 0
      ? "请至少选择或输入一个关键词"
      : words.length > 50
        ? "关键词最多 50 个"
        : words.some((word) => word.length > 50)
          ? "单个关键词最多 50 个字符"
          : !vertical.trim()
            ? "请填写垂类"
            : vertical.trim().length > 40
              ? "垂类最多 40 个字符"
              : roundName.trim().length > 100
                ? "轮次名称最多 100 个字符"
                : pages < 1 || pages > 20
                  ? "页码需在 1 到 20 之间"
                  : !credentialsOk
                    ? "当前平台没有可用的采集服务商，请先在设置中配置"
                    : "";
  const canCreate = !validationMessage && !creating;
  const providerLabel =
    provider === "auto"
      ? `自动（当前最优：${selectedProvider?.label ?? "未配置"}）`
      : selectedProvider?.label ?? provider;

  const removeWord = (word: string) => {
    setSelected((current) => current.filter((item) => item !== word));
    const currentNewWords = wordsOf(newWords);
    const nextWords = currentNewWords.filter((item) => item !== word);
    if (nextWords.length !== currentNewWords.length) {
      setNewWords(nextWords.join("，"));
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!canCreate) {
      setError(validationMessage || "请检查表单");
      return;
    }
    setConfirmOpen(true);
  };

  const createRound = async () => {
    if (!canCreate) {
      setError(validationMessage || "请检查表单");
      return;
    }
    setConfirmOpen(false);
    setCreating(true);
    setError("");
    try {
      const result = await api.createRound({
        keywords: words,
        category: vertical.trim() || null,
        pages,
        max_creators: maxCreators,
        name: roundName.trim() || null,
        platform,
        provider,
        refresh_users: refreshUsers,
      });
      await refreshRounds();
      await queryClient.invalidateQueries({ queryKey: ["keywords"] });
      setRoundId(result.round.id);
      toast.success("采集轮次已创建");
      navigate(`/tasks/${result.round.id}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "创建失败";
      setError(message);
      toast.error(message);
    } finally {
      setCreating(false);
    }
  };

  if (!canCollect) {
    return (
      <Page>
        <PageHeader title="新建采集" />
        <div className="border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
          当前账号没有发起采集的权限，请联系管理员开通。
        </div>
      </Page>
    );
  }

  return (
    <Page className="pb-20 lg:pb-0">
      <PageHeader
        title="新建采集"
        description="填写轮次垂类、选择关键词和采集范围，确认预估消耗后创建本轮任务。"
      />

      {error ? (
        <div role="alert" className="border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : null}
      {!credentialsOk ? (
        <div className="border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          接口尚未配置。请在设置页配置 DataFlow 或 Rnote 访问令牌。
        </div>
      ) : null}

      <form onSubmit={handleSubmit} className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-8">
          <Section title="关键词">
            {keywordsQuery.isLoading ? (
              <Skeleton className="h-10 w-full" />
            ) : keywordsQuery.error ? (
              <ErrorState message={keywordsQuery.error instanceof Error ? keywordsQuery.error.message : "加载关键词失败"} />
            ) : (
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label>已有词库</Label>
                  <MultiSelect
                    label="已有词库"
                    options={enabledKeywords.map((keyword: Keyword) => ({
                      value: keyword.word,
                      label: keyword.word,
                    }))}
                    value={selected}
                    onChange={setSelected}
                    placeholder="选择关键词"
                    searchPlaceholder="搜索关键词…"
                    emptyText="暂无已启用关键词"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="new-words">新增关键词（可选）</Label>
                  <Input
                    id="new-words"
                    name="new-keywords"
                    autoComplete="off"
                    spellCheck={false}
                    value={newWords}
                    onChange={(event) => setNewWords(event.target.value)}
                    placeholder="例如：咖啡探店，手冲咖啡"
                  />
                </div>
                {words.length > 0 ? (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs text-muted-foreground">已选 {words.length} 个关键词</p>
                      <button
                        type="button"
                        className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                        onClick={() => {
                          setSelected([]);
                          setNewWords("");
                        }}
                      >
                        清空
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {words.map((word) => (
                        <Badge key={word} variant="secondary" className="max-w-full gap-1 pr-1 font-normal">
                          <span className="truncate">{word}</span>
                          <button
                            type="button"
                            className="rounded-full p-0.5 text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                            aria-label={`移除关键词：${word}`}
                            onClick={() => removeWord(word)}
                          >
                            <X className="size-3" />
                          </button>
                        </Badge>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            )}
          </Section>

          <Section title="平台与服务商" description="每轮只采集一个平台；服务商可按价格或稳定性切换。">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>平台</Label>
                <Select
                  value={platform}
                  onValueChange={(value) => {
                    setPlatform(value);
                    setProvider("auto");
                  }}
                >
                  <SelectTrigger className="w-full" aria-label="平台">
                    <SelectValue placeholder="选择平台" />
                  </SelectTrigger>
                  <SelectContent>
                    {(settings?.platforms ?? [{ code: "xhs", label: "小红书", default_provider: "dataflow", providers: [] }]).map((item) => (
                      <SelectItem key={item.code} value={item.code}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>采集服务商</Label>
                <Select value={provider} onValueChange={setProvider}>
                  <SelectTrigger className="w-full" aria-label="采集服务商">
                    <SelectValue placeholder="选择服务商" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">自动（最便宜可用）</SelectItem>
                    {providerOptions.map((option) => (
                      <SelectItem key={option.id} value={option.id} disabled={!option.enabled || !option.ready}>
                        {option.label} · {option.search_cost_native} {option.search_currency} ≈ {option.search_cost_tokens} token
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {providerLabel} · {cost} token/次 · ¥{(cost * yuanPerToken).toFixed(3)}
                </p>
              </div>
            </div>
            {!credentialsOk ? (
              <p className="text-xs text-amber-700">
                当前服务商未配置访问令牌，请先到设置页完成配置。
              </p>
            ) : null}
          </Section>

          <Section title="采集设置">
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="pages">每个关键词页数</Label>
                  <Input
                    id="pages"
                    name="pages"
                    type="number"
                    min={1}
                    max={20}
                    value={pages}
                    onChange={(event) => setPages(Math.min(20, Math.max(1, Number(event.target.value) || 1)))}
                  />
                  <p className="text-xs text-muted-foreground">{cost} token/页</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="max-creators">最多补全达人数</Label>
                  <Input
                    id="max-creators"
                    name="max-creators"
                    type="number"
                    min={1}
                    max={500}
                    value={maxCreators ?? ""}
                    onChange={(event) => {
                      const raw = event.target.value;
                      if (!raw) {
                        setMaxCreators(null);
                        return;
                      }
                      const parsed = Number(raw);
                      setMaxCreators(
                        Number.isFinite(parsed) && parsed > 0
                          ? Math.min(500, Math.floor(parsed))
                          : null,
                      );
                    }}
                  />
                  <p className="text-xs text-muted-foreground">留空表示不限制。</p>
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="round-vertical">
                    垂类 <span className="text-destructive" aria-hidden="true">*</span>
                    <span className="sr-only">必填</span>
                  </Label>
                  <Input
                    id="round-vertical"
                    name="round-vertical"
                    autoComplete="off"
                    spellCheck={false}
                    maxLength={40}
                    required
                    value={vertical}
                    onChange={(event) => setVertical(event.target.value)}
                    placeholder="例如：护肤、咖啡"
                  />
                  <p className="text-xs text-muted-foreground">用于轮次归类、结果统计和后续复用。</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="round-name">轮次名称</Label>
                  <Input
                    id="round-name"
                    name="round-name"
                    autoComplete="off"
                    maxLength={100}
                    value={roundName}
                    onChange={(event) => setRoundName(event.target.value)}
                    placeholder="留空自动生成"
                  />
                </div>
              </div>
              <div className="flex items-center justify-between gap-4 border-t pt-4">
                <Label htmlFor="refresh-users">强制刷新达人资料</Label>
                <Switch
                  id="refresh-users"
                  checked={refreshUsers}
                  onCheckedChange={setRefreshUsers}
                />
              </div>
            </div>
          </Section>
        </div>

        <Card className="h-fit gap-4 lg:sticky lg:top-24">
          <CardContent className="space-y-4">
            <div className="space-y-1">
              <h2 className="text-sm font-semibold">本轮预算</h2>
              <p className="text-xs text-muted-foreground">按上限估算</p>
            </div>
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">预估 token</p>
              <div className="flex items-baseline gap-2">
                <NumberTicker
                  value={tokens}
                  locale="zh-CN"
                  fontSize={30}
                  duration={700}
                  stagger={24}
                  textClassName="font-semibold text-foreground"
                />
                <span className="text-sm text-muted-foreground">token</span>
              </div>
              {words.length > 0 ? (
                <p className="text-xs text-muted-foreground">
                  约 {totalCalls} 次调用 · ¥{yuanText}
                </p>
              ) : null}
            </div>
            <dl className="space-y-2 border-t pt-3 text-sm">
              <div className="flex justify-between gap-2"><dt className="text-muted-foreground">垂类</dt><dd className="max-w-[70%] truncate">{vertical.trim() || "未填写"}</dd></div>
              <div className="flex justify-between gap-2"><dt className="text-muted-foreground">关键词</dt><dd className="tabular-nums">{words.length}</dd></div>
              <div className="flex justify-between gap-2"><dt className="text-muted-foreground">搜索调用</dt><dd className="tabular-nums">{words.length === 0 ? 0 : searchCalls}</dd></div>
              <div className="flex justify-between gap-2"><dt className="text-muted-foreground">补全调用</dt><dd className="tabular-nums">{words.length === 0 ? 0 : userCalls}</dd></div>
              <div className="flex justify-between gap-2"><dt className="text-muted-foreground">页数 / 词</dt><dd className="tabular-nums">{pages}</dd></div>
              <div className="flex justify-between gap-2"><dt className="text-muted-foreground">缓存策略</dt><dd>{refreshUsers ? "强制刷新" : "优先使用缓存"}</dd></div>
            </dl>
            {!canCreate && validationMessage ? (
              <p className="text-xs text-destructive">{validationMessage}</p>
            ) : null}
            <Button type="submit" className="hidden w-full lg:flex" disabled={!canCreate}>
              {creating ? "创建中…" : "创建并开始采集"}
            </Button>
          </CardContent>
        </Card>

        <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-background px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] lg:hidden">
          <div className="mx-auto flex max-w-[1500px] items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted-foreground">预估上限</p>
              <div className="flex min-w-0 items-baseline gap-1.5">
                <NumberTicker
                  value={tokens}
                  locale="zh-CN"
                  fontSize={14}
                  duration={500}
                  stagger={14}
                  textClassName="font-semibold text-foreground"
                />
                <span className="truncate text-sm text-muted-foreground">
                  token · ¥{yuanText}
                </span>
              </div>
              {!canCreate && validationMessage ? (
                <p className="truncate text-xs text-destructive">{validationMessage}</p>
              ) : null}
            </div>
            <Button type="submit" disabled={!canCreate}>
              {creating ? "创建中…" : "创建采集"}
            </Button>
          </div>
        </div>
      </form>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认开始本轮采集？</AlertDialogTitle>
            <AlertDialogDescription>
              创建后会立即启动任务，并按实际接口调用扣费。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2 text-sm">
            <div className="flex gap-3">
              <span className="w-20 shrink-0 text-muted-foreground">垂类</span>
              <span className="min-w-0 flex-1 break-words">{vertical.trim()}</span>
            </div>
            <div className="flex gap-3">
              <span className="w-20 shrink-0 text-muted-foreground">关键词</span>
              <span className="min-w-0 flex-1 break-words">{words.join("、")}</span>
            </div>
            <div className="flex gap-3">
              <span className="w-20 shrink-0 text-muted-foreground">平台 / 服务商</span>
              <span>{platformConfig?.label ?? platform} · {providerLabel}</span>
            </div>
            <div className="flex gap-3">
              <span className="w-20 shrink-0 text-muted-foreground">采集范围</span>
              <span>{pages} 页 / 词 · 最多补全 {maxCreators === null ? "不限" : `${maxCreators} 位`}</span>
            </div>
            <div className="flex gap-3">
              <span className="w-20 shrink-0 text-muted-foreground">轮次名称</span>
              <span className="min-w-0 flex-1 break-words">{roundName.trim() || "自动生成"}</span>
            </div>
            <div className="flex gap-3">
              <span className="w-20 shrink-0 text-muted-foreground">缓存策略</span>
              <span>{refreshUsers ? "强制刷新" : "优先使用缓存"}</span>
            </div>
            <div className="flex gap-3 border-t pt-2">
              <span className="w-20 shrink-0 text-muted-foreground">预估消耗</span>
              <span className="font-medium tabular-nums">
                {fmtNumber(tokens)} token · ¥{yuanText}
              </span>
            </div>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={creating}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={creating}
              onClick={(event) => {
                event.preventDefault();
                void createRound();
              }}
            >
              {creating ? "创建中…" : "创建并开始采集"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Page>
  );
}
