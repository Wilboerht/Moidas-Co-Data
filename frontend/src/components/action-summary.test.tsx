import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { ActionSummary } from "./action-summary";
import { api } from "@/shared/api";

vi.mock("@/shared/api", () => ({
  api: { getActionQueue: vi.fn() },
}));

describe("ActionSummary", () => {
  it("uses the shared invalidation key and preserves round filters", async () => {
    vi.mocked(api.getActionQueue).mockResolvedValue({
      counts: { all: 4, pending: 3, unclaimed: 1 },
      items: [],
    });
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ActionSummary roundId={3} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const link = await screen.findByRole("link", { name: "待处理 4 条，进入达人库处理" });
    expect(link).toHaveAttribute(
      "href",
      "/creators?needs_action=true&sort=ratio&order=desc&round=3",
    );
    await waitFor(() => {
      expect(queryClient.getQueryData(["action-queue", 3])).toBeDefined();
    });
  });
});
