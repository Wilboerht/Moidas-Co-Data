import { useState, type FormEvent } from "react";
import { KeyRound, Loader2, LogOut } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/shared/auth-context";
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from "@/shared/constants";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ChangePasswordRequiredPage() {
  const { user, changePassword, logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setError(`密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await changePassword(currentPassword, newPassword);
      toast.success("密码已更新");
    } catch (err) {
      setError(err instanceof Error ? err.message : "修改失败");
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-6">
      <div className="w-full max-w-sm space-y-4">
        <div>
          <p className="text-base font-semibold text-[#1C316A]">Moidas</p>
          <p className="text-xs text-muted-foreground">账号：{user?.username}</p>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>设置新密码</CardTitle>
            <CardDescription>
              首次登录或密码被管理员重置后，需要先修改密码才能继续使用。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="required-current">当前密码</Label>
                <Input
                  id="required-current"
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                  disabled={submitting}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="required-new">新密码</Label>
                <Input
                  id="required-new"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  minLength={MIN_PASSWORD_LENGTH}
                  maxLength={MAX_PASSWORD_LENGTH}
                  onChange={(event) => setNewPassword(event.target.value)}
                  disabled={submitting}
                />
                <p className="text-xs text-muted-foreground">
                  至少 {MIN_PASSWORD_LENGTH} 个字符，最多 {MAX_PASSWORD_LENGTH} 个字符。
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="required-confirm">确认新密码</Label>
                <Input
                  id="required-confirm"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  minLength={MIN_PASSWORD_LENGTH}
                  maxLength={MAX_PASSWORD_LENGTH}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  disabled={submitting}
                />
              </div>
              {error ? <p className="text-sm text-destructive">{error}</p> : null}
              <Button
                type="submit"
                className="w-full"
                disabled={
                  submitting ||
                  !currentPassword ||
                  newPassword.length < MIN_PASSWORD_LENGTH ||
                  newPassword !== confirmPassword
                }
              >
                {submitting ? <Loader2 className="animate-spin" /> : <KeyRound />}
                更新密码
              </Button>
            </form>
          </CardContent>
        </Card>
        <Button
          variant="ghost"
          className="w-full"
          onClick={() => void logout()}
          disabled={submitting}
        >
          <LogOut />
          退出登录
        </Button>
      </div>
    </div>
  );
}
