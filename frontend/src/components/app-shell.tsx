import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { toast } from "sonner";
import {
  LayoutDashboard,
  ContactRound,
  ListChecks,
  Settings,
  Tags,
  UserCog,
  Users,
} from "lucide-react";
import logo from "@/assets/logo.svg";
import { AccountMenu } from "@/components/account-menu";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { ThinkingOrb } from "@/components/dissolve/thinking-orb";
import { roundOrbState } from "@/components/round-status-orb";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuth } from "@/shared/auth-context";
import { useRound } from "@/shared/round-context";
import { fmtNumber } from "@/shared/format";
import type { Job } from "@/shared/types";
import { APP_VERSION } from "@/shared/version";

const NAV_ITEMS = [
  { href: "/", label: "工作台", icon: LayoutDashboard },
  { href: "/creators", label: "达人库", icon: Users },
  { href: "/leads", label: "线索库", icon: ContactRound },
  { href: "/tasks", label: "采集任务", icon: ListChecks },
  { href: "/keywords", label: "关键词", icon: Tags },
];

const SYSTEM_ITEMS = [
  { href: "/settings", label: "设置", icon: Settings },
];

function readSidebarOpen() {
  if (typeof document === "undefined") return true;
  const match = document.cookie.match(/(?:^|;\s*)sidebar_state=(true|false)/);
  return match ? match[1] === "true" : true;
}

const TITLES: Record<string, string> = {
  "/users": "用户管理",
  "/": "工作台",
  "/creators": "达人库",
  "/leads": "线索库",
  "/new": "新建采集",
  "/tasks": "采集任务",
  "/keywords": "关键词",
  "/settings": "设置",
};

function titleForPath(pathname: string) {
  if (pathname.startsWith("/tasks/")) return "任务详情";
  return TITLES[pathname] ?? "工作台";
}

function SidebarToggle() {
  const { state, isMobile } = useSidebar();
  const label = isMobile ? "打开侧栏" : state === "collapsed" ? "展开侧栏" : "收起侧栏";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <SidebarTrigger size="icon" className="-ml-1" aria-label={label} />
      </TooltipTrigger>
      <TooltipContent side="bottom" align="start" sideOffset={6}>
        {isMobile ? label : `${label} · Ctrl+B`}
      </TooltipContent>
    </Tooltip>
  );
}

function AppSidebar() {
  const location = useLocation();
  const { isMobile, setOpenMobile } = useSidebar();
  const { user } = useAuth();
  const { loading, error } = useRound();
  const runtimeStatus = error ? "系统异常" : loading ? "连接中" : "运行正常";
  const runtimeTone = error ? "bg-destructive" : loading ? "bg-amber-500" : "bg-emerald-500";
  const canCollect = user?.role === "admin" || Boolean(user?.can_collect);
  const navItems = (
    user?.role === "admin"
      ? [...NAV_ITEMS, { href: "/users", label: "用户", icon: UserCog }]
      : NAV_ITEMS
  ).filter((item) => canCollect || (item.href !== "/tasks" && item.href !== "/keywords"));
  const systemItems = user?.role === "admin" ? SYSTEM_ITEMS : [];

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader className="h-16 flex-row items-center gap-2">
        <div className="min-w-0 flex-1">
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" asChild tooltip="Moidas">
                <Link to="/" aria-label="Moidas 首页">
                  <img src={logo} alt="" aria-hidden="true" width={481} height={424} className="h-7 w-auto shrink-0" />
                  <div className="grid flex-1 text-left text-sm leading-tight group-data-[collapsible=icon]:hidden">
                    <span className="truncate font-semibold text-[#1C316A]">Moidas</span>
                    <span className="truncate text-[11px] text-muted-foreground">多平台信息数据处理工作台</span>
                  </div>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>工作区</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => {
                const active =
                  item.href === "/"
                    ? location.pathname === "/"
                    : location.pathname.startsWith(item.href);
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton asChild isActive={active} tooltip={item.label}>
                      <NavLink
                        to={item.href}
                        onClick={() => {
                          if (isMobile) setOpenMobile(false);
                        }}
                      >
                        <item.icon />
                        <span>{item.label}</span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        {systemItems.length > 0 ? (
          <SidebarGroup>
            <SidebarGroupLabel>系统</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {systemItems.map((item) => {
                  const active = location.pathname.startsWith(item.href);
                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton asChild isActive={active} tooltip={item.label}>
                        <NavLink
                          to={item.href}
                          onClick={() => {
                            if (isMobile) setOpenMobile(false);
                          }}
                        >
                          <item.icon />
                          <span>{item.label}</span>
                        </NavLink>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ) : null}
      </SidebarContent>

      <SidebarFooter>
        <AccountMenu />
        <div className="space-y-2 border p-2 group-data-[collapsible=icon]:hidden">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">系统</span>
            <span className="inline-flex items-center gap-1.5 text-xs">
              <span className={`size-1.5 rounded-full ${runtimeTone}`} aria-hidden="true" />
              {runtimeStatus}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span className="font-mono">v{APP_VERSION}</span>
            <span>在线</span>
          </div>
        </div>
        <div className="hidden justify-center group-data-[collapsible=icon]:flex">
          <Tooltip>
            <TooltipTrigger asChild>
              {user?.role === "admin" ? (
                <Link
                  to="/settings"
                  aria-label={`系统${runtimeStatus}，打开设置`}
                  className="flex size-8 items-center justify-center rounded-md outline-hidden hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-sidebar-ring"
                >
                  <span className={`size-2 rounded-full ${runtimeTone}`} aria-hidden="true" />
                </Link>
              ) : (
                <span
                  aria-label={`系统${runtimeStatus}`}
                  className="flex size-8 items-center justify-center"
                >
                  <span className={`size-2 rounded-full ${runtimeTone}`} aria-hidden="true" />
                </span>
              )}
            </TooltipTrigger>
            <TooltipContent side="right" align="center">
              系统{runtimeStatus} · v{APP_VERSION}
            </TooltipContent>
          </Tooltip>
        </div>
      </SidebarFooter>
    </Sidebar>
  );
}

export function AppShell() {
  const location = useLocation();
  const [sidebarDefaultOpen] = useState(readSidebarOpen);
  const { user } = useAuth();
  const { error, job, activeJob, latestJob, rounds } = useRound();
  const canCollect = user?.role === "admin" || Boolean(user?.can_collect);
  const statusJob =
    [activeJob, job].find(
      (item) => item && (item.status === "running" || item.status === "pending"),
    ) ?? null;
  const status = statusJob?.status ?? null;
  const statusLabel = statusJob?.status_label ?? status;
  const statusPercent =
    statusJob && statusJob.progress_total > 0
      ? Math.round((statusJob.progress_done / statusJob.progress_total) * 100)
      : null;
  const statusRound = statusJob
    ? rounds.find((item) => item.id === statusJob.round_id) ?? null
    : null;
  const activeCount = statusJob?.active_count ?? (statusJob ? 1 : 0);
  const statusText = statusJob
    ? `${statusLabel ?? "采集中"}${statusPercent !== null ? ` · ${statusPercent}%` : ""}${
        activeCount > 1 ? ` · ${activeCount} 个任务` : ""
      }`
    : "空闲";
  const statusDescription = statusJob
    ? `采集状态：${statusText}${statusJob.stage ? ` · ${statusJob.stage}` : ""}${
        statusRound ? ` · ${statusRound.name}` : ""
      }。${activeCount > 1 ? "点击查看全部任务。" : "点击查看任务详情。"}`
    : "当前没有运行中的采集任务。点击查看采集记录。";
  const statusHref =
    statusJob?.round_id && activeCount <= 1 ? `/tasks/${statusJob.round_id}` : "/tasks";
  const orbState = roundOrbState(status);
  const pageTitle = titleForPath(location.pathname);
  const isTaskDetail = location.pathname.startsWith("/tasks/");
  const previousJobRef = useRef<Job | null>(null);
  const notifiedJobRef = useRef("");

  useEffect(() => {
    document.title = `${pageTitle} · Moidas Co-data`;
  }, [pageTitle]);

  useEffect(() => {
    if (!canCollect) return;
    const previous = previousJobRef.current;
    previousJobRef.current = latestJob;
    if (!previous || !latestJob || previous.id !== latestJob.id) return;
    if (previous.status !== "running" && previous.status !== "pending") return;
    if (latestJob.status !== "done" && latestJob.status !== "failed") return;
    const key = `${latestJob.id}:${latestJob.status}`;
    if (notifiedJobRef.current === key) return;
    notifiedJobRef.current = key;
    const roundName = rounds.find((item) => item.id === latestJob.round_id)?.name;
    const suffix = roundName ? ` · ${roundName}` : "";
    if (latestJob.status === "done") {
      toast.success(`采集完成${suffix}`, {
        description: `处理 ${latestJob.progress_done} / ${latestJob.progress_total} · 花费 ${fmtNumber(latestJob.cost)} token`,
        duration: 6000,
      });
      return;
    }
    if (latestJob.stage === "已取消" || latestJob.error === "任务已取消") return;
    toast.error(`采集失败${suffix}`, {
      description: latestJob.error || "请进入采集记录查看详情",
      duration: 8000,
    });
  }, [latestJob, rounds, canCollect]);

  return (
    <SidebarProvider defaultOpen={sidebarDefaultOpen}>
      <a
        href="#main-content"
        className="fixed start-4 top-4 z-[100] -translate-y-24 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground shadow-md transition-transform focus:translate-y-0"
      >
        跳到主内容
      </a>
      <AppSidebar />
      <SidebarInset id="main-content" tabIndex={-1} className="min-w-0 scroll-mt-20">
        <header className="sticky top-0 z-20 flex h-16 shrink-0 items-center gap-2 md:rounded-t-xl border-b bg-background px-4 md:px-6 lg:px-8">
          <SidebarToggle />
          <Separator orientation="vertical" className="mx-1 h-4 md:hidden" />
          <Breadcrumb className="hidden md:block">
            <BreadcrumbList>
              <BreadcrumbItem>
                {location.pathname === "/" ? (
                  <span>工作区</span>
                ) : (
                  <BreadcrumbLink asChild>
                    <Link to="/">工作区</Link>
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
              <BreadcrumbSeparator />
              {isTaskDetail ? (
                <>
                  <BreadcrumbItem>
                    <BreadcrumbLink asChild>
                      <Link to="/tasks">采集任务</Link>
                    </BreadcrumbLink>
                  </BreadcrumbItem>
                  <BreadcrumbSeparator />
                  <BreadcrumbItem>
                    <BreadcrumbPage>{pageTitle}</BreadcrumbPage>
                  </BreadcrumbItem>
                </>
              ) : (
                <BreadcrumbItem>
                  <BreadcrumbPage>{pageTitle}</BreadcrumbPage>
                </BreadcrumbItem>
              )}
            </BreadcrumbList>
          </Breadcrumb>
          <span className="text-sm font-medium text-[#1C316A] md:hidden">Moidas</span>
          <div className="ml-auto flex items-center gap-3">
            {canCollect ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Link
                    to={statusHref}
                    aria-label={statusDescription}
                    className={`group flex h-9 items-center gap-2 text-sm font-medium outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 ${
                      statusJob
                        ? "text-foreground hover:text-primary"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <span className="flex size-8 items-center justify-center" aria-hidden="true">
                      <ThinkingOrb
                        size={32}
                        dots={80}
                        dotSize={2}
                        state={orbState}
                        color={orbState === "error" ? "#ff3b30" : "currentColor"}
                        haptics={false}
                      />
                    </span>
                    <span className="hidden max-w-[180px] truncate sm:inline">{statusText}</span>
                  </Link>
                </TooltipTrigger>
                <TooltipContent side="bottom" align="end" sideOffset={6}>
                  {statusDescription}
                </TooltipContent>
              </Tooltip>
            ) : null}
            {canCollect ? (
              <>
                <span aria-hidden="true" className="h-4 w-px shrink-0 self-center bg-border" />
                <Link
                  to="/new"
                  className="group flex h-9 items-center text-sm font-medium text-foreground outline-none transition-colors hover:text-primary focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <span className="underline-offset-4 group-hover:underline">新建采集</span>
                </Link>
              </>
            ) : null}
          </div>
        </header>
        <div className="flex-1 space-y-4 p-4 md:p-6 lg:p-8">
          {error ? (
            <div className="border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              无法连接本地服务：{error}
            </div>
          ) : null}
          <Outlet />
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
