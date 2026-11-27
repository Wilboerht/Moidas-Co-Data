import { lazy, Suspense, type ComponentType } from "react";
import { Loader2 } from "lucide-react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/app-shell";
import { LoginPage } from "@/pages/login";
import { ChangePasswordRequiredPage } from "@/pages/change-password-required";
import { useAuth } from "@/shared/auth-context";
import { RoundProvider } from "@/shared/round-context";

const DashboardPage = lazy(() =>
  import("@/pages/dashboard").then((module) => ({ default: module.DashboardPage })),
);
const CreatorsPage = lazy(() =>
  import("@/pages/creators").then((module) => ({ default: module.CreatorsPage })),
);
const EmailPage = lazy(() =>
  import("@/pages/email").then((module) => ({ default: module.EmailPage })),
);
const LeadsPage = lazy(() =>
  import("@/pages/leads").then((module) => ({ default: module.LeadsPage })),
);
const NewRoundPage = lazy(() =>
  import("@/pages/new-round").then((module) => ({ default: module.NewRoundPage })),
);
const TasksPage = lazy(() =>
  import("@/pages/tasks").then((module) => ({ default: module.TasksPage })),
);
const TaskDetailPage = lazy(() =>
  import("@/pages/task-detail").then((module) => ({ default: module.TaskDetailPage })),
);
const KeywordsPage = lazy(() =>
  import("@/pages/keywords").then((module) => ({ default: module.KeywordsPage })),
);
const SettingsPage = lazy(() =>
  import("@/pages/settings").then((module) => ({ default: module.SettingsPage })),
);
const UsersPage = lazy(() =>
  import("@/pages/users").then((module) => ({ default: module.UsersPage })),
);

function PageFallback() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center" aria-busy="true">
      <Loader2 className="size-5 animate-spin text-muted-foreground" />
    </div>
  );
}

function page(Component: ComponentType) {
  return (
    <Suspense fallback={<PageFallback />}>
      <Component />
    </Suspense>
  );
}

export function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center" aria-busy="true">
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

  const canCollect = user.role === "admin" || user.can_collect;
  const canSendEmail = user.role === "admin" || user.can_send_email;
  const isAdmin = user.role === "admin";

  return (
    <RoundProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={page(DashboardPage)} />
          <Route path="/creators" element={page(CreatorsPage)} />
          <Route path="/leads" element={page(LeadsPage)} />
          <Route path="/email" element={canSendEmail ? page(EmailPage) : <Navigate to="/" replace />} />
          <Route path="/new" element={canCollect ? page(NewRoundPage) : <Navigate to="/" replace />} />
          <Route path="/tasks" element={canCollect ? page(TasksPage) : <Navigate to="/" replace />} />
          <Route path="/tasks/:roundId" element={canCollect ? page(TaskDetailPage) : <Navigate to="/" replace />} />
          <Route path="/keywords" element={canCollect ? page(KeywordsPage) : <Navigate to="/" replace />} />
          <Route path="/users" element={isAdmin ? page(UsersPage) : <Navigate to="/" replace />} />
          <Route path="/settings" element={isAdmin ? page(SettingsPage) : <Navigate to="/" replace />} />
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
