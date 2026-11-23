import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { CreatorSheet } from "./creator-sheet";
import { TooltipProvider } from "@/components/ui/tooltip";
import { api } from "@/shared/api";
import type { CreatorDetail } from "@/shared/types";

vi.mock("@/shared/api", () => ({
  api: {
    getCreator: vi.fn(),
    getCreatorOwners: vi.fn(),
    updateCreator: vi.fn(),
    claimCreator: vi.fn(),
    releaseCreator: vi.fn(),
    assignCreator: vi.fn(),
  },
}));

vi.mock("@/shared/auth-context", () => ({
  useAuth: () => ({ user: { id: 1, role: "admin", can_collect: true } }),
}));

vi.mock("@/shared/round-context", () => ({
  useRound: () => ({ refreshRounds: vi.fn() }),
}));

const detail = {
  key: "xhs|u1",
  round_id: 1,
  platform: "xhs",
  platform_label: "小红书",
  user_id: "u1",
  nickname: "测试达人",
  avatar: "",
  profile_url: "",
  fetched_at: "2026-01-01T00:00:00+00:00",
  status: "pending",
  status_label: "待审核",
  match_direction: "",
  tags_manual: "",
  remark: "",
  owner_user_id: null,
  owner_username: "",
  claimed_at: null,
  source_keywords: [],
  source_labels: [],
  auto_tags: [],
  creator_tags: [],
  notes: [],
  desc: "",
  fans: null,
  posted: null,
  liked: null,
  interaction: null,
  sample_notes: 0,
  sample_likes: 0,
  sample_collected: 0,
  sample_comments: 0,
  sample_interactions: 0,
} as unknown as CreatorDetail;

describe("CreatorSheet", () => {
  it("asks before discarding unsaved edits", async () => {
    vi.mocked(api.getCreator).mockResolvedValue(detail);
    vi.mocked(api.getCreatorOwners).mockResolvedValue([]);
    const onClose = vi.fn();
    const user = userEvent.setup();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <TooltipProvider>
        <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <CreatorSheet
            roundId={1}
            detailKey="xhs|u1"
            statuses={[{ code: "pending", label: "待审核", color: "", order: 1 }]}
            onClose={onClose}
          />
        </MemoryRouter>
        </QueryClientProvider>
      </TooltipProvider>,
    );

    const input = await screen.findByLabelText("人工标签");
    await user.type(input, "咖啡");
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(await screen.findByText("放弃未保存的修改？")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "继续编辑" }));
    expect(input).toHaveValue("咖啡");
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "关闭" }));
    await user.click(await screen.findByRole("button", { name: "放弃并关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("confirms before switching rounds with unsaved edits", async () => {
    vi.mocked(api.getCreator).mockImplementation(async (roundId) => ({
      ...detail,
      status: roundId === 2 ? "adopted" : "pending",
      status_label: roundId === 2 ? "已采用" : "待审核",
    }));
    vi.mocked(api.getCreatorOwners).mockResolvedValue([]);
    const onCancelSwitch = vi.fn();
    const user = userEvent.setup();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = await act(async () => render(
      <TooltipProvider>
        <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <CreatorSheet
            roundId={1}
            detailKey="xhs|u1"
            statuses={[
              { code: "pending", label: "待审核", color: "", order: 1 },
              { code: "adopted", label: "已采用", color: "", order: 2 },
            ]}
            onClose={vi.fn()}
            onCancelSwitch={onCancelSwitch}
          />
        </MemoryRouter>
        </QueryClientProvider>
      </TooltipProvider>,
    ));

    const input = await screen.findByLabelText("人工标签");
    await user.type(input, "咖啡");
    await act(async () => {
      view.rerender(
      <TooltipProvider>
        <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <CreatorSheet
            roundId={2}
            detailKey="xhs|u1"
            statuses={[
              { code: "pending", label: "待审核", color: "", order: 1 },
              { code: "adopted", label: "已采用", color: "", order: 2 },
            ]}
            onClose={vi.fn()}
            onCancelSwitch={onCancelSwitch}
          />
        </MemoryRouter>
        </QueryClientProvider>
      </TooltipProvider>,
      );
    });
    expect(await screen.findByText("放弃当前修改并切换轮次？")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回原轮次" }));
    expect(onCancelSwitch).toHaveBeenCalledWith(1);
    expect(api.getCreator).not.toHaveBeenCalledWith(2, "xhs", "u1");
  });
});
