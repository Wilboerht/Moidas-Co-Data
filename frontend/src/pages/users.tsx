import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CircleCheck, Eye, EyeOff, KeyRound, LogOut, MoreHorizontal, Shield, UserCheck, UserPlus, UserX } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/shared/api";
import { useAuth } from "@/shared/auth-context";
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, passwordLength } from "@/shared/constants";
import { fmtDateTime } from "@/shared/format";
import type { AuthUser } from "@/shared/types";
import { ErrorState, Page, PageHeader, Section } from "@/components/page";
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

function UserStatusBadge({ user }: { user: AuthUser }) {
  const status = user.disabled
    ? {
        label: "停用",
        className: "border-rose-200 bg-rose-50 font-normal text-rose-700",
        title: "账号已停用，无法登录",
      }
    : user.must_change_password
      ? {
          label: "待改密",
          className: "border-amber-200 bg-amber-50 font-normal text-amber-700",
          title: "下次登录需要修改密码",
        }
      : {
          label: "启用",
          className: "border-emerald-200 bg-emerald-50 font-normal text-emerald-700",
          title: "账号正常",
        };
  return (
    <Badge variant="outline" className={status.className} title={status.title}>
      {status.label}
    </Badge>
  );
}

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
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [role, setRole] = useState("member");
  const [canCollect, setCanCollect] = useState(false);
  const [canEditLeads, setCanEditLeads] = useState(false);
  const [canSendEmail, setCanSendEmail] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<{
    title: string;
    description: string;
    variant?: "default" | "destructive";
    action: () => void;
  } | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });

  const resetCreateForm = () => {
    setUsername("");
    setPassword("");
    setPasswordConfirm("");
    setShowPassword(false);
    setRole("member");
    setCanCollect(false);
    setCanEditLeads(false);
    setCanSendEmail(false);
    setError("");
  };

  const resetPasswordForm = () => {
    setNewPassword("");
    setNewPasswordConfirm("");
    setShowNewPassword(false);
  };

  const closeCreateDialog = () => {
    if (createMutation.isPending) return;
    setCreateOpen(false);
    resetCreateForm();
  };

  const closeResetDialog = () => {
    if (resetMutation.isPending) return;
    setResetTarget(null);
    resetPasswordForm();
  };

  const createMutation = useMutation({
    mutationFn: () =>
      api.createUser({
        username,
        password,
        role,
        can_collect: role === "admin" ? false : canCollect,
        can_edit_leads: role === "admin" ? false : canEditLeads,
        can_send_email: role === "admin" ? false : canSendEmail,
      }),
    onSuccess: async () => {
      toast.success("用户已创建，首次登录需要修改密码");
      setCreateOpen(false);
      resetCreateForm();
      await refresh();
    },
    onError: (err) => setError(err instanceof Error ? err.message : "创建失败"),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: { role?: string; disabled?: boolean; can_collect?: boolean; can_edit_leads?: boolean; can_send_email?: boolean } }) =>
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
      toast.success("密码已重置，相关会话已注销，下次登录需修改密码");
      setResetTarget(null);
      resetPasswordForm();
      await refresh();
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "重置失败"),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: number) => api.revokeUserSessions(id),
    onSuccess: async (result, id) => {
      if (id === user?.id && result.kept_current) {
        toast.success(`已注销其他 ${result.revoked} 个会话，当前会话保持登录`);
      } else {
        toast.success("已注销该用户的全部会话");
      }
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
          <Button
            onClick={() => {
              resetCreateForm();
              setCreateOpen(true);
            }}
          >
            <UserPlus />
            新建用户
          </Button>
        }
      />

      <Section
        title="全部用户"
        description={
          usersQuery.isLoading || usersQuery.error ? undefined : `共 ${users.length} 位用户。`
        }
        contentClassName="px-0"
      >
        <Table className="min-w-[880px]">
          <TableHeader>
            <TableRow>
              <TableHead>用户名</TableHead>
              <TableHead className="w-[100px] text-center">角色</TableHead>
              <TableHead className="w-[110px] text-center">采集权限</TableHead>
              <TableHead className="w-[110px] text-center">线索权限</TableHead>
              <TableHead className="w-[110px] text-center">自动发件</TableHead>
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
                  <TableCell colSpan={9}><Skeleton className="h-6 w-full" /></TableCell>
                </TableRow>
              ))
            ) : usersQuery.error ? (
              <TableRow>
                <TableCell colSpan={9} className="p-3">
                  <ErrorState
                    message={usersQuery.error instanceof Error ? usersQuery.error.message : "加载用户失败"}
                  />
                </TableCell>
              </TableRow>
            ) : users.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="h-24 text-center text-muted-foreground">暂无用户</TableCell>
              </TableRow>
            ) : (
              users.map((item) => (
                <TableRow key={item.id}>
                  <TableCell className="font-medium">
                    <div className="flex items-center gap-2">
                      <span className="truncate">{item.username}</span>
                      {item.id === user.id ? (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                          我
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-center">
                    {item.role === "admin" ? "管理员" : "成员"}
                  </TableCell>
                  <TableCell className="text-center">
                    <Badge variant="outline" className="font-normal">
                      {item.role === "admin" ? "不受限" : item.can_collect ? "允许" : "仅查看"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-center">
                    <Badge variant="outline" className="font-normal">
                      {item.role === "admin" ? "不受限" : item.can_edit_leads ? "允许" : "仅查看"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-center">
                    <Badge variant="outline" className="font-normal">
                      {item.role === "admin" ? "不受限" : item.can_send_email ? "允许" : "关闭"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-center">
                    <UserStatusBadge user={item} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {item.last_login_at ? fmtDateTime(item.last_login_at) : "从未登录"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{fmtDateTime(item.created_at)}</TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`管理 ${item.username}`}
                          disabled={updateMutation.isPending || revokeMutation.isPending}
                        >
                          <MoreHorizontal />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuLabel>{item.username}</DropdownMenuLabel>
                        <DropdownMenuItem
                          disabled={item.id === user.id}
                          onClick={() =>
                            setConfirm({
                              title: item.role === "admin" ? "设为成员？" : "设为管理员？",
                              description: `将 ${item.username} 的权限调整为${item.role === "admin" ? "成员" : "管理员"}。`,
                              action: () => updateMutation.mutate({ id: item.id, payload: { role: item.role === "admin" ? "member" : "admin" } }),
                            })
                          }
                        >
                          <Shield />
                          {item.role === "admin" ? "设为成员" : "设为管理员"}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={item.role === "admin"}
                          onClick={() =>
                            setConfirm({
                              title: item.can_collect ? "取消采集权限？" : "允许发起采集？",
                              description: item.can_collect
                                ? `${item.username} 将不能再新建采集轮次或重新运行采集，已有数据不受影响。`
                                : `${item.username} 将可以新建采集轮次和重新运行采集。`,
                              variant: item.can_collect ? "destructive" : "default",
                              action: () =>
                                updateMutation.mutate({
                                  id: item.id,
                                  payload: { can_collect: !item.can_collect },
                                }),
                            })
                          }
                        >
                          {item.can_collect ? <UserX /> : <UserCheck />}
                          {item.can_collect ? "取消采集权限" : "允许发起采集"}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={item.role === "admin"}
                          onClick={() =>
                            setConfirm({
                              title: item.can_send_email ? "关闭自动发件？" : "允许自动发件？",
                              description: item.can_send_email
                                ? `${item.username} 将不能配置发件账号或创建发件任务，已有发送记录保留。`
                                : `${item.username} 将可以配置发件账号、维护达人邮箱并创建发件任务。`,
                              variant: item.can_send_email ? "destructive" : "default",
                              action: () =>
                                updateMutation.mutate({
                                  id: item.id,
                                  payload: { can_send_email: !item.can_send_email },
                                }),
                            })
                          }
                        >
                          {item.can_send_email ? <UserX /> : <UserCheck />}
                          {item.can_send_email ? "关闭自动发件" : "允许自动发件"}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={item.role === "admin"}
                          onClick={() =>
                            setConfirm({
                              title: item.can_edit_leads ? "取消线索权限？" : "允许维护线索？",
                              description: item.can_edit_leads
                                ? `${item.username} 将不能再新增或编辑线索，已有线索不受影响。`
                                : `${item.username} 将可以新增和编辑线索。`,
                              variant: item.can_edit_leads ? "destructive" : "default",
                              action: () =>
                                updateMutation.mutate({
                                  id: item.id,
                                  payload: { can_edit_leads: !item.can_edit_leads },
                                }),
                            })
                          }
                        >
                          {item.can_edit_leads ? <UserX /> : <UserCheck />}
                          {item.can_edit_leads ? "取消线索权限" : "允许维护线索"}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={item.id === user.id}
                          title={item.id === user.id ? "请通过右上角“修改密码”修改自己的密码" : undefined}
                          onClick={() => {
                            resetPasswordForm();
                            setResetTarget(item);
                          }}
                        >
                          <KeyRound />
                          重置密码
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() =>
                            setConfirm({
                              title: item.id === user.id ? "注销其他会话？" : "注销全部会话？",
                              description:
                                item.id === user.id
                                  ? "当前会话会保持登录，其他设备上的登录会话将立即失效。"
                                  : `用户 ${item.username} 的所有登录会话将立即失效，需要重新登录。`,
                              variant: "destructive",
                              action: () => revokeMutation.mutate(item.id),
                            })
                          }
                        >
                          <LogOut />
                          {item.id === user.id ? "注销其他会话" : "注销全部会话"}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          disabled={item.id === user.id}
                          variant={item.disabled ? "default" : "destructive"}
                          onClick={() =>
                            setConfirm({
                              title: item.disabled ? "启用账号？" : "停用账号？",
                              description: item.disabled
                                ? `启用后 ${item.username} 可以重新登录。`
                                : `停用后 ${item.username} 将无法登录，历史数据不受影响。`,
                              variant: item.disabled ? "default" : "destructive",
                              action: () => updateMutation.mutate({ id: item.id, payload: { disabled: !item.disabled } }),
                            })
                          }
                        >
                          {item.disabled ? <CircleCheck /> : <Ban />}
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

      <AlertDialog open={confirm !== null} onOpenChange={(open) => !open && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm?.title}</AlertDialogTitle>
            <AlertDialogDescription>{confirm?.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={updateMutation.isPending || revokeMutation.isPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant={confirm?.variant ?? "default"}
              disabled={updateMutation.isPending || revokeMutation.isPending}
              onClick={() => {
                confirm?.action();
                setConfirm(null);
              }}
            >
              确认
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          if (!open) {
            closeCreateDialog();
            return;
          }
          setCreateOpen(true);
        }}
      >
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (
                createMutation.isPending ||
                username.trim().length < 2 ||
                passwordLength(password) < MIN_PASSWORD_LENGTH ||
                password !== passwordConfirm
              ) {
                return;
              }
              createMutation.mutate();
            }}
          >
            <DialogHeader>
              <DialogTitle>新建用户</DialogTitle>
              <DialogDescription>
                创建后把用户名和初始密码交给用户，首次登录时系统会强制要求修改密码。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="new-username">用户名</Label>
                <Input
                  id="new-username"
                  value={username}
                  maxLength={32}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="off"
                  autoFocus
                />
                <p className="text-xs text-muted-foreground">
                  2-32 个字符，支持字母、数字、下划线、点、@ 和 -。
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-user-password">初始密码</Label>
                <div className="relative">
                  <Input
                    id="new-user-password"
                    type={showPassword ? "text" : "password"}
                    value={password}
                    minLength={MIN_PASSWORD_LENGTH}
                    maxLength={MAX_PASSWORD_LENGTH}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="new-password"
                    className="pr-9"
                  />
                  <button
                    type="button"
                    className="absolute top-1/2 right-1 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label={showPassword ? "隐藏密码" : "显示密码"}
                    onClick={() => setShowPassword((visible) => !visible)}
                  >
                    {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">
                  至少 {MIN_PASSWORD_LENGTH} 个字符，最多 {MAX_PASSWORD_LENGTH} 个字符。
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-user-password-confirm">确认密码</Label>
                <Input
                  id="new-user-password-confirm"
                  type={showPassword ? "text" : "password"}
                  value={passwordConfirm}
                  minLength={MIN_PASSWORD_LENGTH}
                  maxLength={MAX_PASSWORD_LENGTH}
                  onChange={(event) => setPasswordConfirm(event.target.value)}
                  autoComplete="new-password"
                />
                {passwordConfirm && password !== passwordConfirm ? (
                  <p className="text-xs text-destructive">两次输入的密码不一致</p>
                ) : null}
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-user-role">角色</Label>
                <Select value={role} onValueChange={setRole}>
                  <SelectTrigger id="new-user-role" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">成员</SelectItem>
                    <SelectItem value="admin">管理员</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between gap-4 border-t pt-4">
                <div className="space-y-1">
                  <Label htmlFor="create-can-collect">允许发起采集</Label>
                  <p className="text-xs text-muted-foreground">
                    {role === "admin"
                      ? "管理员默认拥有采集权限，无需单独设置。"
                      : "成员开启后才能新建和重新运行采集。"}
                  </p>
                </div>
                <Switch
                  id="create-can-collect"
                  checked={role === "admin" ? true : canCollect}
                  onCheckedChange={setCanCollect}
                  disabled={role === "admin"}
                />
              </div>
              <div className="flex items-center justify-between gap-4 border-t pt-4">
                <div className="space-y-1">
                  <Label htmlFor="create-can-send-email">允许自动发件</Label>
                  <p className="text-xs text-muted-foreground">
                    {role === "admin"
                      ? "管理员默认可以使用自动发件。"
                      : "成员开启后才能配置邮箱并创建发件任务。"}
                  </p>
                </div>
                <Switch
                  id="create-can-send-email"
                  checked={role === "admin" ? true : canSendEmail}
                  onCheckedChange={setCanSendEmail}
                  disabled={role === "admin"}
                />
              </div>
              <div className="flex items-center justify-between gap-4 border-t pt-4">
                <div className="space-y-1">
                  <Label htmlFor="create-can-edit-leads">允许维护线索</Label>
                  <p className="text-xs text-muted-foreground">
                    {role === "admin"
                      ? "管理员默认可以维护线索，无需单独设置。"
                      : "成员开启后才能新增和编辑线索。"}
                  </p>
                </div>
                <Switch
                  id="create-can-edit-leads"
                  checked={role === "admin" ? true : canEditLeads}
                  onCheckedChange={setCanEditLeads}
                  disabled={role === "admin"}
                />
              </div>
              {error ? <p className="text-sm text-destructive" role="alert">{error}</p> : null}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={closeCreateDialog} disabled={createMutation.isPending}>
                取消
              </Button>
              <Button
                type="submit"
                disabled={
                  createMutation.isPending ||
                  username.trim().length < 2 ||
                  passwordLength(password) < MIN_PASSWORD_LENGTH ||
                  password !== passwordConfirm
                }
              >
                {createMutation.isPending ? "创建中…" : "创建"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={resetTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            closeResetDialog();
            return;
          }
          setResetTarget(resetTarget);
        }}
      >
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (
                resetMutation.isPending ||
                passwordLength(newPassword) < MIN_PASSWORD_LENGTH ||
                newPassword !== newPasswordConfirm
              ) {
                return;
              }
              resetMutation.mutate();
            }}
          >
            <DialogHeader>
              <DialogTitle>重置 {resetTarget?.username} 的密码</DialogTitle>
              <DialogDescription>
                重置后该用户的所有会话会立即失效，下次登录时需要修改密码。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3 py-4">
              <div className="space-y-2">
                <Label htmlFor="reset-password">新密码</Label>
                <div className="relative">
                  <Input
                    id="reset-password"
                    type={showNewPassword ? "text" : "password"}
                    value={newPassword}
                    minLength={MIN_PASSWORD_LENGTH}
                    maxLength={MAX_PASSWORD_LENGTH}
                    onChange={(event) => setNewPassword(event.target.value)}
                    autoComplete="new-password"
                    autoFocus
                    className="pr-9"
                  />
                  <button
                    type="button"
                    className="absolute top-1/2 right-1 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label={showNewPassword ? "隐藏密码" : "显示密码"}
                    onClick={() => setShowNewPassword((visible) => !visible)}
                  >
                    {showNewPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">
                  至少 {MIN_PASSWORD_LENGTH} 个字符，最多 {MAX_PASSWORD_LENGTH} 个字符。
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="reset-password-confirm">确认密码</Label>
                <Input
                  id="reset-password-confirm"
                  type={showNewPassword ? "text" : "password"}
                  value={newPasswordConfirm}
                  minLength={MIN_PASSWORD_LENGTH}
                  maxLength={MAX_PASSWORD_LENGTH}
                  onChange={(event) => setNewPasswordConfirm(event.target.value)}
                  autoComplete="new-password"
                />
                {newPasswordConfirm && newPassword !== newPasswordConfirm ? (
                  <p className="text-xs text-destructive">两次输入的密码不一致</p>
                ) : null}
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={closeResetDialog} disabled={resetMutation.isPending}>
                取消
              </Button>
              <Button
                type="submit"
                disabled={
                  resetMutation.isPending ||
                  passwordLength(newPassword) < MIN_PASSWORD_LENGTH ||
                  newPassword !== newPasswordConfirm
                }
              >
                {resetMutation.isPending ? "重置中…" : "重置密码"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Page>
  );
}
