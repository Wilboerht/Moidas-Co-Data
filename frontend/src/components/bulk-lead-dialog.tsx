import { useMemo, useRef, useState } from "react";
import { LoaderCircle, Upload } from "lucide-react";
import { toast } from "sonner";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/shared/api";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

type ParsedLead = {
  line: number;
  raw: string;
  platform: string;
  nickname: string;
  account_id: string;
  profile_url: string;
  raw_title: string;
};

type InvalidLine = { line: number; raw: string; reason: string };

type BulkResult = {
  created: number;
  failures: { line: number; raw: string; error: string }[];
};

const HEADER_TOKENS = new Set([
  "平台", "platform",
  "昵称", "nickname",
  "账号", "账号id", "account", "account_id",
  "主页", "主页url", "profile", "profile_url", "链接", "url",
  "原始标题", "标题", "raw_title", "title",
]);

function splitRow(line: string) {
  if (line.includes("\t")) {
    return line.split("\t").map((cell) => cell.trim());
  }
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (const char of line) {
    if (char === '"') {
      quoted = !quoted;
      continue;
    }
    if (char === "," && !quoted) {
      cells.push(cell);
      cell = "";
      continue;
    }
    cell += char;
  }
  cells.push(cell);
  return cells.map((item) => item.trim());
}

function parseLeadText(
  text: string,
  platforms: { code: string; label: string }[],
  defaultPlatform: string,
) {
  const aliases = new Map<string, string>();
  for (const item of platforms) {
    aliases.set(item.code.toLowerCase(), item.code);
    aliases.set(item.label.toLowerCase(), item.code);
  }
  const items: ParsedLead[] = [];
  const invalid: InvalidLine[] = [];
  let headers = 0;

  text.split(/\r?\n/).forEach((raw, index) => {
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith("#")) return;
    const line = index + 1;
    const cells = splitRow(trimmed);
    const urlIndex = cells.findIndex((cell) => /^https?:\/\//i.test(cell));
    const isHeader = urlIndex < 0 && cells.some((cell) => HEADER_TOKENS.has(cell.toLowerCase()));
    if (isHeader) {
      headers += 1;
      return;
    }
    const rest = urlIndex >= 0 ? cells.filter((_, i) => i !== urlIndex) : cells;
    let platform = defaultPlatform;
    let fields = rest;
    const alias = urlIndex === 0 ? undefined : aliases.get((rest[0] || "").toLowerCase());
    if (alias) {
      platform = alias;
      fields = rest.slice(1);
    }
    const [nickname = "", accountId = "", rawTitle = ""] = fields;
    const profileUrl = urlIndex >= 0 ? cells[urlIndex] : "";
    if (!nickname.trim() && !accountId.trim() && !profileUrl.trim()) {
      invalid.push({ line, raw: trimmed, reason: "缺少昵称、账号 ID 或主页 URL" });
      return;
    }
    items.push({
      line,
      raw: trimmed,
      platform,
      nickname: nickname.trim(),
      account_id: accountId.trim(),
      profile_url: profileUrl.trim(),
      raw_title: rawTitle.trim(),
    });
  });

  return { items, invalid, headers };
}

export function BulkLeadDialog({
  open,
  onClose,
  platforms,
  rounds,
  sources,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  platforms: { code: string; label: string }[];
  rounds: { id: number; name: string }[];
  sources: { code: string; label: string }[];
  onCreated: () => void | Promise<void>;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [platform, setPlatform] = useState("xhs");
  const [source, setSource] = useState("manual");
  const [roundId, setRoundId] = useState("none");
  const [fileName, setFileName] = useState("");
  const [result, setResult] = useState<BulkResult | null>(null);
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false);

  const parsed = useMemo(() => parseLeadText(text, platforms, platform), [text, platforms, platform]);

  const resetDraft = () => {
    setText("");
    setResult(null);
    setFileName("");
    if (fileRef.current) fileRef.current.value = "";
  };

  const mutation = useMutation({
    mutationFn: () => {
      if (!parsed.items.length) throw new Error("没有可导入的行");
      return api.createLeadsBulk(
        parsed.items.map((item) => ({
          platform: item.platform,
          nickname: item.nickname,
          account_id: item.account_id,
          profile_url: item.profile_url,
          raw_title: item.raw_title,
          source,
          source_round_id: roundId === "none" ? null : Number(roundId),
          owner_user_id: null,
        })),
      );
    },
    onSuccess: async (data) => {
      if (data.errors.length > 0) {
        const failures = data.errors.map((error) => {
          const item = parsed.items[error.index - 1];
          return { line: item?.line ?? error.index, raw: item?.raw ?? "", error: error.error };
        });
        setResult({ created: data.created, failures });
        setText(failures.map((failure) => failure.raw).join("\n"));
        toast.warning(`已创建 ${data.created} 条，${failures.length} 条失败`);
        await onCreated();
        return;
      }
      toast.success(`已创建 ${data.created} 条线索`);
      resetDraft();
      await onCreated();
      onClose();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "批量创建失败"),
  });

  const requestClose = () => {
    if (mutation.isPending) return;
    if (text.trim()) {
      setCloseConfirmOpen(true);
      return;
    }
    onClose();
  };

  const footerHint = mutation.isPending
    ? "正在导入，请稍候…"
    : parsed.items.length > 0
      ? `识别 ${parsed.items.length} 条${parsed.invalid.length ? ` · ${parsed.invalid.length} 行有问题` : ""}${parsed.headers ? ` · 跳过表头 ${parsed.headers} 行` : ""}`
      : text.trim()
        ? "没有识别到可导入的行"
        : "每行一条，Ctrl+Enter 开始导入";

  return (
    <>
      <Dialog open={open} onOpenChange={(value) => !value && requestClose()}>
        <DialogContent className="grid max-h-[85vh] max-w-[calc(100%-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-2xl">
          <DialogHeader className="space-y-1.5 border-b px-5 pt-5 pr-12 pb-3">
            <DialogTitle>批量粘贴线索</DialogTitle>
            <DialogDescription>
              每行一条：纯主页 URL，或{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                平台,昵称,账号ID,主页URL,原始标题
              </code>
              。支持从 CSV / TXT 导入，表头行会自动跳过。
            </DialogDescription>
          </DialogHeader>

          <div className="min-h-0 space-y-4 overflow-y-auto px-5 py-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="bulk-platform">默认平台</Label>
                <Select value={platform} onValueChange={setPlatform} disabled={mutation.isPending}>
                  <SelectTrigger id="bulk-platform" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {platforms.map((item) => <SelectItem key={item.code} value={item.code}>{item.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="bulk-source">来源</Label>
                <Select value={source} onValueChange={setSource} disabled={mutation.isPending}>
                  <SelectTrigger id="bulk-source" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {sources.map((item) => <SelectItem key={item.code} value={item.code}>{item.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="bulk-round">来源轮次</Label>
                <Select value={roundId} onValueChange={setRoundId} disabled={mutation.isPending}>
                  <SelectTrigger id="bulk-round" className="w-full"><SelectValue placeholder="不关联" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">不关联</SelectItem>
                    {rounds.map((item) => <SelectItem key={item.id} value={String(item.id)}>{item.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {result && result.failures.length > 0 ? (
              <div className="space-y-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                <p className="font-medium">
                  已创建 {result.created} 条，{result.failures.length} 条失败
                </p>
                <ul className="space-y-0.5 text-xs">
                  {result.failures.slice(0, 5).map((failure) => (
                    <li key={`${failure.line}-${failure.error}`}>
                      第 {failure.line} 行：{failure.error}
                    </li>
                  ))}
                  {result.failures.length > 5 ? <li>还有 {result.failures.length - 5} 条未显示…</li> : null}
                </ul>
                <p className="text-xs text-amber-700/80">失败行已保留在粘贴框中，修正后可直接重试。</p>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={fileRef}
                id="lead-bulk-file"
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                className="sr-only"
                disabled={mutation.isPending}
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  setFileName(file.name);
                  setText(await file.text());
                  setResult(null);
                }}
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => fileRef.current?.click()}
                disabled={mutation.isPending}
              >
                <Upload />
                选择文件
              </Button>
              <span className="min-w-0 truncate text-xs text-muted-foreground">
                {fileName || "支持 .csv / .txt，或直接在下方粘贴"}
              </span>
            </div>

            <div className="space-y-2">
              <Label htmlFor="lead-bulk-text">粘贴内容</Label>
              <Textarea
                id="lead-bulk-text"
                value={text}
                rows={9}
                disabled={mutation.isPending}
                className="min-h-40 font-mono text-xs"
                onChange={(event) => {
                  setText(event.target.value);
                  if (result) setResult(null);
                }}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                    event.preventDefault();
                    if (parsed.items.length > 0 && !mutation.isPending) mutation.mutate();
                  }
                }}
                placeholder={"https://www.xiaohongshu.com/user/profile/xxxx\nxhs,昵称,账号ID,https://...,原始标题"}
              />
              {parsed.invalid.length > 0 ? (
                <p className="text-xs text-amber-600">
                  第 {parsed.invalid.slice(0, 3).map((item) => item.line).join("、")} 行
                  {parsed.invalid.length > 3 ? " 等" : ""}缺少昵称、账号 ID 或主页 URL，将被跳过。
                </p>
              ) : null}
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/40 px-5 py-3">
            <p className="text-xs text-muted-foreground">{footerHint}</p>
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={requestClose} disabled={mutation.isPending}>
                取消
              </Button>
              <Button
                onClick={() => mutation.mutate()}
                disabled={mutation.isPending || parsed.items.length === 0}
              >
                {mutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
                {mutation.isPending ? "导入中…" : "开始导入"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={closeConfirmOpen} onOpenChange={setCloseConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>关闭批量导入？</AlertDialogTitle>
            <AlertDialogDescription>粘贴框中还有未导入的内容，关闭后不会保存。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续编辑</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setCloseConfirmOpen(false);
                resetDraft();
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
