import { Loader2 } from "lucide-react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/app-shell";
import { DashboardPage } from "@/pages/dashboard";
import { CreatorsPage } from "@/pages/creators";
import { NewRoundPage } from "@/pages/new-round";
import { TasksPage } from "@/pages/tasks";
import { KeywordsPage } from "@/pages/keywords";
import { SettingsPage } from "@/pages/settings";
import { UsersPage } from "@/pages/users";
import { LoginPage } from "@/pages/login";
import { ChangePasswordRequiredPage } from "@/pages/change-password-required";
import { useAuth } from "@/shared/auth-context";
import { RoundProvider } from "@/shared/round-context";

export function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

  if (user.must_change_password) {
    return <ChangePasswordRequiredPage />;
  }

  return (
    <RoundProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="/creators" element={<CreatorsPage />} />
          <Route path="/new" element={<NewRoundPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/keywords" element={<KeywordsPage />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route
            path="*"
            element={
              <div className="flex min-h-[40vh] items-center justify-center text-sm text-muted-foreground">
                页面不存在
              </div>
            }
          />
        </Route>
      </Routes>
    </RoundProvider>
  );
}
