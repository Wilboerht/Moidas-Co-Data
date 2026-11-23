import { useState, type FormEvent } from "react";
import { ChevronUp, KeyRound, LogOut, UserRound } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/shared/auth-context";
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from "@/shared/constants";
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

export function AccountMenu() {
  const { user, logout, changePassword } = useAuth();
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!user) return null;

  const submitPassword = async (event: FormEvent) => {
    event.preventDefault();
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await changePassword(currentPassword, newPassword);
      toast.success("密码已修改");
      setPasswordOpen(false);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSubmitting(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "修改失败");
      setSubmitting(false);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left outline-none hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-sidebar-ring group-data-[collapsible=icon]:mx-auto group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:w-8 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:p-0" aria-label={`账号 ${user.username}`}
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full border bg-background">
              <UserRound className="size-3.5" />
            </span>
            <span className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
              <span className="block truncate text-sm font-medium">{user.username}</span>
              <span className="block text-xs text-muted-foreground">
                {user.role === "admin" ? "管理员" : "成员"}
              </span>
            </span>
            <ChevronUp className="size-4 text-muted-foreground group-data-[collapsible=icon]:hidden" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="w-56">
          <DropdownMenuLabel className="space-y-0.5">
            <p className="text-sm font-medium">{user.username}</p>
            <p className="text-xs font-normal text-muted-foreground">
              {user.role === "admin" ? "管理员" : "成员"}
            </p>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => { setPasswordOpen(true); setError(""); }}>
            <KeyRound />
            修改密码
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void logout()}>
            <LogOut />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={passwordOpen} onOpenChange={(open) => { setPasswordOpen(open); if (!open) { setError(""); setCurrentPassword(""); setNewPassword(""); setConfirmPassword(""); } }}>
        <DialogContent>
          <form onSubmit={submitPassword}>
            <DialogHeader>
              <DialogTitle>修改密码</DialogTitle>
              <DialogDescription>修改后其他设备的会话会失效。</DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="current-password">当前密码</Label>
                <Input id="current-password" type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-password">新密码</Label>
                <Input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  minLength={MIN_PASSWORD_LENGTH}
                  maxLength={MAX_PASSWORD_LENGTH}
                  onChange={(event) => setNewPassword(event.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  至少 {MIN_PASSWORD_LENGTH} 个字符，最多 {MAX_PASSWORD_LENGTH} 个字符。
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirm-password">确认新密码</Label>
                <Input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  minLength={MIN_PASSWORD_LENGTH}
                  maxLength={MAX_PASSWORD_LENGTH}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                />
              </div>
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setPasswordOpen(false)} disabled={submitting}>取消</Button>
              <Button
                type="submit"
                disabled={
                  submitting ||
                  !currentPassword ||
                  newPassword.length < MIN_PASSWORD_LENGTH ||
                  newPassword !== confirmPassword
                }
              >
                保存
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
