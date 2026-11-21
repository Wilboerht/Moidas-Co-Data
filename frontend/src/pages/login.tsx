import { useState, type FormEvent } from "react";
import { CircleAlert, Eye, EyeOff, Loader2 } from "lucide-react";
import { useAuth } from "@/shared/auth-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import moidasLogo from "@/assets/moidas-logo.svg";

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password) return;
    setSubmitting(true);
    setError("");
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-theme flex min-h-screen flex-col bg-background text-foreground">
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex justify-center">
            <img src={moidasLogo} alt="Moidas" className="h-10 w-auto" />
          </div>

          <Card className="py-0 shadow-sm">
            <CardContent className="p-6">
              <form onSubmit={submit} className="space-y-5">
                <div className="space-y-2">
                  <Label htmlFor="login-username" className="text-sm">
                    用户名
                  </Label>
                  <Input
                    id="login-username"
                    className="h-10 rounded-lg px-3"
                    autoComplete="username"
                    placeholder="请输入用户名"
                    autoFocus
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    disabled={submitting}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="login-password" className="text-sm">
                    密码
                  </Label>
                  <div className="relative">
                    <Input
                      id="login-password"
                      className="h-10 rounded-lg px-3 pr-10"
                      type={showPassword ? "text" : "password"}
                      autoComplete="current-password"
                      placeholder="请输入密码"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      disabled={submitting}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="absolute top-1/2 right-1.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                      onClick={() => setShowPassword((visible) => !visible)}
                      aria-label={showPassword ? "隐藏密码" : "显示密码"}
                      disabled={submitting}
                    >
                      {showPassword ? <EyeOff /> : <Eye />}
                    </Button>
                  </div>
                </div>
                {error ? (
                  <div
                    role="alert"
                    className="flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                  >
                    <CircleAlert className="mt-0.5 size-4 shrink-0" />
                    <p>{error}</p>
                  </div>
                ) : null}
                <Button
                  type="submit"
                  className="h-10 w-full"
                  disabled={submitting || !username.trim() || !password}
                >
                  {submitting ? <Loader2 className="animate-spin" /> : null}
                  {submitting ? "登录中…" : "登录"}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>
      </div>

      <footer className="pb-6 text-center text-xs text-muted-foreground">
        © {new Date().getFullYear()} Moidas. All rights reserved.
      </footer>
    </div>
  );
}
