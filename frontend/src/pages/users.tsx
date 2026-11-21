import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, MoreHorizontal, ShieldCheck, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
import { fmtDateTime } from "@/shared/format";
import type { AuthUser } from "@/shared/types";
import { Page, PageHeader, Section } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function UsersPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const usersQuery = useQuery({
    queryKey: ["users"],
    queryFn: api.getUsers,
    enabled: user?.role === "admin",
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [resetTarget, setResetTarget] = useState<AuthUser | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("member");
  const [canCollect, setCanCollect] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });

  const createMutation = useMutation({
    mutationFn: () => api.createUser({ username, password, role, can_collect: canCollect }),
    onSuccess: async () => {
      toast.success("用户已创建");
      setCreateOpen(false);
      setUsername("");
      setPassword("");
      setRole("member");
      setCanCollect(false);
      setError("");
      await refresh();
    },
    onError: (err) => setError(err instanceof Error ? err.message : "创建失败"),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: { role?: string; disabled?: boolean; can_collect?: boolean } }) =>
      api.updateUser(id, payload),
    onSuccess: async () => {
      toast.success("用户已更新");
      await refresh();
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "更新失败"),
  });

  const resetMutation = useMutation({
    mutationFn: () => api.resetUserPassword(resetTarget!.id, newPassword),
    onSuccess: async () => {
      toast.success("密码已重置，相关会话已注销");
      setResetTarget(null);
      setNewPassword("");
      await refresh();
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "重置失败"),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: number) => api.revokeUserSessions(id),
    onSuccess: async () => {
      toast.success("已注销该用户的全部会话");
      await refresh();
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "操作失败"),
  });

  if (user?.role !== "admin") {
    return (
      <Page>
        <PageHeader title="用户管理" />
        <p className="text-sm text-muted-foreground">需要管理员权限。</p>
      </Page>
    );
  }

  const users = usersQuery.data ?? [];

  return (
    <Page>
      <PageHeader
        title="用户管理"
        description="账号由管理员创建，所有用户共享同一批采集数据。"
        actions={
          <Button onClick={() => { setError(""); setCreateOpen(true); }}>
            <UserPlus />
            新建用户
          </Button>
        }
      />

      {usersQuery.error ? (
        <div className="border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {usersQuery.error instanceof Error ? usersQuery.error.message : "加载用户失败"}
        </div>
      ) : null}

      <Section title="全部用户" contentClassName="px-0">
        <Table className="min-w-[760px]">
          <TableHeader>
            <TableRow>
              <TableHead>用户名</TableHead>
              <TableHead className="w-[100px] text-center">角色</TableHead>
              <TableHead className="w-[110px] text-center">采集权限</TableHead>
              <TableHead className="w-[110px] text-center">状态</TableHead>
              <TableHead className="w-[180px]">最近登录</TableHead>
              <TableHead className="w-[180px]">创建时间</TableHead>
              <TableHead className="w-[60px] text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {usersQuery.isLoading ? (
              Array.from({ length: 4 }).map((_, index) => (
                <TableRow key={index}>
                  <TableCell colSpan={7}><Skeleton className="h-6 w-full" /></TableCell>
                </TableRow>
              ))
            ) : users.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">暂无用户</TableCell>
              </TableRow>
            ) : (
              users.map((item) => (
                <TableRow key={item.id}>
                  <TableCell className="font-medium">{item.username}</TableCell>
                  <TableCell className="text-center">
                    <Badge variant="outline" className="gap-1 font-normal">
                      {item.role === "admin" ? <ShieldCheck className="size-3" /> : null}
                      {item.role === "admin" ? "管理员" : "成员"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-center">
                    <Badge variant="outline" className="font-normal">
                      {item.role === "admin" ? "管理员" : item.can_collect ? "允许" : "仅查看"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-center">
                    <div className="flex flex-wrap justify-center gap-1">
                      <Badge variant="outline" className={item.disabled ? "text-muted-foreground" : ""}>
                        {item.disabled ? "停用" : "启用"}
                      </Badge>
                      {item.must_change_password ? (
                        <Badge variant="outline" className="text-amber-700">待改密</Badge>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{fmtDateTime(item.last_login_at)}</TableCell>
                  <TableCell className="text-muted-foreground">{fmtDateTime(item.created_at)}</TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon-sm" aria-label={`管理 ${item.username}`}>
                          <MoreHorizontal />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuLabel>{item.username}</DropdownMenuLabel>
                        <DropdownMenuItem
                          disabled={item.id === user.id}
                          onClick={() => updateMutation.mutate({ id: item.id, payload: { role: item.role === "admin" ? "member" : "admin" } })}
                        >
                          {item.role === "admin" ? "设为成员" : "设为管理员"}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={item.role === "admin"}
                          onClick={() => updateMutation.mutate({ id: item.id, payload: { can_collect: !item.can_collect } })}
                        >
                          {item.can_collect ? "取消采集权限" : "允许发起采集"}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={item.id === user.id}
                          onClick={() => setResetTarget(item)}
                        >
                          <KeyRound />
                          重置密码
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={item.id === user.id}
                          onClick={() => revokeMutation.mutate(item.id)}
                        >
                          注销全部会话
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          disabled={item.id === user.id}
                          onClick={() => updateMutation.mutate({ id: item.id, payload: { disabled: !item.disabled } })}
                        >
                          {item.disabled ? "启用账号" : "停用账号"}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Section>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <form onSubmit={(event) => { event.preventDefault(); createMutation.mutate(); }}>
            <DialogHeader>
              <DialogTitle>新建用户</DialogTitle>
              <DialogDescription>创建后把用户名和初始密码交给用户，首次登录后建议修改密码。</DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="new-username">用户名</Label>
                <Input id="new-username" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-user-password">初始密码</Label>
                <Input id="new-user-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" />
                <p className="text-xs text-muted-foreground">至少 10 个字符。</p>
              </div>
              <div className="space-y-2">
                <Label>角色</Label>
                <Select value={role} onValueChange={setRole}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">成员</SelectItem>
                    <SelectItem value="admin">管理员</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between gap-4 border-t pt-4">
                <div className="space-y-1">
                  <Label htmlFor="create-can-collect">允许发起采集</Label>
                  <p className="text-xs text-muted-foreground">成员开启后才能新建和重新运行采集。</p>
                </div>
                <Switch
                  id="create-can-collect"
                  checked={canCollect}
                  onCheckedChange={setCanCollect}
                />
              </div>
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={createMutation.isPending}>取消</Button>
              <Button type="submit" disabled={createMutation.isPending || !username.trim() || !password}>创建</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={resetTarget !== null} onOpenChange={(open) => { if (!open) { setResetTarget(null); setNewPassword(""); } }}>
        <DialogContent>
          <form onSubmit={(event) => { event.preventDefault(); resetMutation.mutate(); }}>
            <DialogHeader>
              <DialogTitle>重置 {resetTarget?.username} 的密码</DialogTitle>
              <DialogDescription>重置后该用户的所有会话会立即失效。</DialogDescription>
            </DialogHeader>
            <div className="space-y-2 py-4">
              <Label htmlFor="reset-password">新密码</Label>
              <Input id="reset-password" type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" />
              <p className="text-xs text-muted-foreground">至少 10 个字符。</p>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setResetTarget(null)} disabled={resetMutation.isPending}>取消</Button>
              <Button type="submit" disabled={resetMutation.isPending || !newPassword}>重置密码</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Page>
  );
}
