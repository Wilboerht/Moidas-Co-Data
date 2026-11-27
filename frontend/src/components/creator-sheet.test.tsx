import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
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
    getCooperationCase: vi.fn(),
    createCooperationCase: vi.fn(),
    updateCooperationCaseStatus: vi.fn(),
    completeCooperationCase: vi.fn(),
    addCooperationNote: vi.fn(),
    updateCooperationCaseSummary: vi.fn(),
    correctCooperationCaseStatus: vi.fn(),
    uploadCooperationAttachment: vi.fn(),
    deleteCooperationAttachment: vi.fn(),
    addCooperationWork: vi.fn(),
    updateCooperationWork: vi.fn(),
    deleteCooperationWork: vi.fn(),
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
  cooperation_status: "",
  cooperation_status_label: "",
  cooperation_updated_at: "",
  cooperation_history: [],
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
        <RouterProvider
          router={createMemoryRouter([{
            path: "*",
            element: (
              <CreatorSheet
                roundId={1}
                detailKey="xhs|u1"
                statuses={[{ code: "pending", label: "待审核", color: "", order: 1 }]}
                cooperationStatuses={[]}
                onClose={onClose}
              />
            ),
          }])}
        />
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

  it("syncs auto-enrolled cooperation after saving review adoption", async () => {
    vi.mocked(api.getCreator).mockResolvedValue(detail);
    vi.mocked(api.getCreatorOwners).mockResolvedValue([]);
    vi.mocked(api.updateCreator).mockClear();
    vi.mocked(api.updateCreator).mockResolvedValue({
      ...detail,
      status: "adopted",
      status_label: "已采用",
      cooperation_status: "not_contacted",
      cooperation_status_label: "未联系",
      cooperation_history: [
        {
          id: 1,
          ts: "2026-01-01T00:00:00.000+00:00",
          from_status: "",
          to_status: "not_contacted",
          auto: true,
          batch: false,
          actor_username: "测试用户",
        },
      ],
    });
    const user = userEvent.setup();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <TooltipProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider
            router={createMemoryRouter([{
              path: "*",
              element: (
                <CreatorSheet
                  roundId={1}
                  detailKey="xhs|u1"
                  statuses={[
                    { code: "pending", label: "待审核", color: "", order: 1 },
                    { code: "adopted", label: "已采用", color: "", order: 2 },
                  ]}
                  cooperationStatuses={[
                    { code: "not_contacted", label: "未联系", color: "", order: 1 },
                  ]}
                  onClose={vi.fn()}
                />
              ),
            }])}
          />
        </QueryClientProvider>
      </TooltipProvider>,
    );

    await user.click(await screen.findByLabelText("采用状态"));
    await user.click(await screen.findByRole("option", { name: "已采用" }));
    await user.click(screen.getByRole("button", { name: "保存标注" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "保存标注" })).toBeDisabled(),
    );
    expect((await screen.findAllByText("未联系")).length).toBeGreaterThan(0);
  });


});
