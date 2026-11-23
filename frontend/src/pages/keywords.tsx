import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Lock, Search, Trash2, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { fmtDateOnly, fmtNumber } from "@/shared/format";
import type { Keyword } from "@/shared/types";
import { EmptyState, ErrorState, Page, PageHeader, Section } from "@/components/page";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function KeywordsPage() {
  const queryClient = useQueryClient();
  const keywordsQuery = useQuery({ queryKey: ["keywords"], queryFn: api.getKeywords });
  const [word, setWord] = useState("");
  const [search, setSearch] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Keyword | null>(null);
  const [recentId, setRecentId] = useState<number | null>(null);
  const wordInput = useRef<HTMLInputElement>(null);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["keywords"] });
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
    await queryClient.invalidateQueries({ queryKey: ["facets"] });
  };

  const create = useMutation({
    mutationFn: () => api.createKeyword(word.trim()),
    onSuccess: (result) => {
      setWord("");
      setRecentId(result.id);
      wordInput.current?.focus();
      void invalidate();
      toast.success("已添加关键词");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "添加失败"),
  });

  useEffect(() => {
    if (recentId === null) return;
    const timer = window.setTimeout(() => setRecentId(null), 2000);
    return () => window.clearTimeout(timer);
  }, [recentId]);

  const remove = useMutation({
    mutationFn: (target: Keyword) => api.deleteKeyword(target.id),
    onSuccess: async (_, target) => {
      setDeleteTarget(null);
      await invalidate();
      toast.success(`已删除「${target.word}」`);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "删除失败"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, value }: { id: number; value: boolean }) =>
      api.updateKeyword(id, { enabled: value }),
    onMutate: async ({ id, value }) => {
      await queryClient.cancelQueries({ queryKey: ["keywords"] });
      const previous = queryClient.getQueryData<Keyword[]>(["keywords"]);
      queryClient.setQueryData<Keyword[]>(["keywords"], (current) =>
        current?.map((item) => (item.id === id ? { ...item, enabled: value ? 1 : 0 } : item)),
      );
      return { previous };
    },
    onError: (error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(["keywords"], context.previous);
      toast.error(error instanceof Error ? error.message : "更新失败");
    },
    onSuccess: (_data, variables) => {
      toast.success(variables.value ? "关键词已启用" : "关键词已停用");
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["keywords"] });
    },
  });

  const keywords = keywordsQuery.data ?? [];
  const enabledCount = keywords.filter((item) => item.enabled).length;
  const filteredKeywords = useMemo(() => {
    const query = search.trim().toLowerCase();
    return query ? keywords.filter((item) => item.word.toLowerCase().includes(query)) : keywords;
  }, [keywords, search]);
  const enableAll = useMutation({
    mutationFn: () =>
      Promise.all(keywords.filter((item) => !item.enabled).map((item) => api.updateKeyword(item.id, { enabled: true }))),
    onSuccess: async () => {
      await invalidate();
      toast.success("已启用全部关键词");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "启用失败"),
  });
  const normalizedWord = word.trim().toLowerCase();
  const duplicateKeyword = normalizedWord
    ? keywords.find((item) => item.word.trim().toLowerCase() === normalizedWord)
    : undefined;
  const canAdd =
    Boolean(word.trim()) && !duplicateKeyword && !create.isPending && !keywordsQuery.isLoading;

  return (
    <Page>
      <PageHeader
        title="关键词"
        description="管理采集用搜索词。业务垂类在创建轮次时设置，单个关键词不再绑定垂类。"
      />

      <Section title="添加关键词" description="新词会加入关键词库，并在新建采集时可选。">
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (canAdd) create.mutate();
          }}
        >
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
            <div className="space-y-2">
              <Label htmlFor="keyword-word">关键词</Label>
              <Input
                ref={wordInput}
                id="keyword-word"
                name="keyword-word"
                autoComplete="off"
                spellCheck={false}
                maxLength={50}
                value={word}
                aria-invalid={duplicateKeyword ? true : undefined}
                aria-describedby="keyword-help"
                onChange={(event) => {
                  setWord(event.target.value);
                  if (create.isError) create.reset();
                }}
                placeholder="例如：咖啡探店"
              />
            </div>
            <Button type="submit" disabled={!canAdd}>
              {create.isPending ? "添加中…" : "添加"}
            </Button>
          </div>
          <div id="keyword-help" className="space-y-1 text-xs">
            {duplicateKeyword ? (
              <p className="text-destructive" role="status">
                已存在该关键词{duplicateKeyword.enabled ? "" : "（当前为停用状态）"}，请在下方列表启用。
              </p>
            ) : (
              <p className="text-muted-foreground">
                关键词只用于采集搜索，不决定达人方向；业务垂类请在新建采集时填写。
              </p>
            )}
            {create.isError ? (
              <p className="text-destructive" role="alert">
                {create.error instanceof Error ? create.error.message : "添加失败"}
              </p>
            ) : null}
          </div>
        </form>
      </Section>

      <Section
        title="关键词列表"
        description={
          keywordsQuery.isLoading
            ? "被轮次引用的关键词只能停用，不能删除。"
            : `被轮次引用的关键词只能停用，不能删除。共 ${keywords.length} 个，启用 ${enabledCount} 个。`
        }
        contentClassName="px-0"
        actions={
          keywords.length > 0 ? (
            <div className="relative">
              <Search className="pointer-events-none absolute top-[9px] left-2 size-3.5 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索关键词"
                aria-label="搜索关键词"
                className="h-8 w-48 pl-7 text-sm"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
          ) : null
        }
      >
        {keywordsQuery.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : keywordsQuery.error ? (
          <ErrorState message={keywordsQuery.error instanceof Error ? keywordsQuery.error.message : "加载失败"} />
        ) : keywords.length === 0 ? (
          <EmptyState title="还没有关键词" description="先添加一个关键词，再去新建采集。" />
        ) : (
          <>
            {enabledCount === 0 ? (
              <div className="mx-4 mb-3 flex flex-wrap items-center gap-2 border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1">当前没有启用的关键词，新建采集时将没有可选词库。</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="border-amber-300 bg-transparent text-amber-900 hover:bg-amber-100 hover:text-amber-900"
                  disabled={enableAll.isPending}
                  onClick={() => enableAll.mutate()}
                >
                  {enableAll.isPending ? (
                    <>
                      <LoaderCircle className="animate-spin" />
                      启用中…
                    </>
                  ) : (
                    "全部启用"
                  )}
                </Button>
              </div>
            ) : null}
            <Table className="min-w-[720px] table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead>关键词</TableHead>
                  <TableHead className="w-[120px]">启用</TableHead>
                  <TableHead className="w-[96px] text-right">使用轮次</TableHead>
                  <TableHead className="w-[120px]">创建时间</TableHead>
                  <TableHead className="w-[104px] text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredKeywords.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="h-20 text-center text-muted-foreground">
                      没有匹配「{search.trim()}」的关键词
                    </TableCell>
                  </TableRow>
                ) : filteredKeywords.map((item) => (
                  <TableRow key={item.id} className={item.id === recentId ? "bg-muted/50" : undefined}>
                    <TableCell className="font-medium">{item.word}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Switch
                          aria-label={`${item.word}启用状态`}
                          checked={Boolean(item.enabled)}
                          disabled={toggle.isPending && toggle.variables?.id === item.id}
                          onCheckedChange={(checked) => toggle.mutate({ id: item.id, value: checked })}
                        />
                        <span className={item.enabled ? "text-xs text-foreground" : "text-xs text-muted-foreground"}>
                          {item.enabled ? "已启用" : "已停用"}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className={`text-right tabular-nums ${item.rounds ? "" : "text-muted-foreground"}`}>
                      {fmtNumber(item.rounds)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{fmtDateOnly(item.created_at)}</TableCell>
                    <TableCell className="text-right">
                      {item.rounds > 0 ? (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span
                              tabIndex={0}
                              aria-label={`${item.word}被 ${item.rounds} 个轮次引用，只能停用，不能删除`}
                              className="inline-flex cursor-not-allowed items-center justify-end gap-1.5 text-xs text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                            >
                              <Lock className="size-3.5" />
                              已引用
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="left" align="center">
                            被 {item.rounds} 个轮次引用，只能停用
                          </TooltipContent>
                        </Tooltip>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                          aria-label={`删除关键词 ${item.word}`}
                          onClick={() => setDeleteTarget(item)}
                        >
                          <Trash2 />
                          删除
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </>
        )}
      </Section>

      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除关键词「{deleteTarget?.word ?? ""}」？</AlertDialogTitle>
            <AlertDialogDescription>
              未被轮次引用的关键词可以删除，删除后可以重新添加。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={remove.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (deleteTarget) remove.mutate(deleteTarget);
              }}
            >
              {remove.isPending ? "删除中…" : "删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Page>
  );
}
