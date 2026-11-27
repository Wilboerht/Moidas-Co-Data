import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { vi } from "vitest";
import { hasQuickConvertIdentity, LeadsPage } from "./leads";
import { api } from "@/shared/api";

vi.mock("@/shared/api", () => ({
  api: {
    getLeads: vi.fn(),
    getLeadIdentityHealth: vi.fn(),
    getLeadOwners: vi.fn(),
    getLeadFacets: vi.fn(),
  },
  downloadFile: vi.fn(),
}));

vi.mock("@/shared/auth-context", () => ({
  useAuth: () => ({ user: { id: 1, role: "admin", can_collect: true } }),
}));

vi.mock("@/shared/round-context", () => ({
  useRound: () => ({
    meta: {
      platforms: [{ code: "xhs", label: "小红书" }],
      lead_statuses: [
        { code: "unverified", label: "待核实", color: "", order: 1 },
        { code: "verifying", label: "核实中", color: "", order: 2 },
      ],
      lead_sources: [],
      statuses: [],
    },
    rounds: [{ id: 1, name: "R1", platform: "xhs" }],
  }),
}));

describe("LeadsPage URL state", () => {
  it("restores real platform facets and page state after history navigation", async () => {
    vi.mocked(api.getLeads).mockResolvedValue({ items: [], total: 100, page: 1, page_size: 50 });
    vi.mocked(api.getLeadIdentityHealth).mockResolvedValue({
      account_index: true,
      profile_index: true,
      duplicate_account_groups: 0,
      duplicate_profile_groups: 0,
    });
    vi.mocked(api.getLeadOwners).mockResolvedValue([]);
    vi.mocked(api.getLeadFacets).mockResolvedValue({
      platforms: [{ value: "weibo", count: 951 }],
      statuses: [],
      sources: [],
      owners: [],
      rounds: [],
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const router = createMemoryRouter(
      [{
        path: "*",
        element: (
          <QueryClientProvider client={queryClient}>
            <LeadsPage />
          </QueryClientProvider>
        ),
      }],
      { initialEntries: ["/leads?platform=weibo&page=2&status=unverified"] },
    );
    render(<RouterProvider router={router} />);

    await screen.findByText("weibo (951)");
    await waitFor(() => {
      expect(api.getLeads).toHaveBeenLastCalledWith(
        expect.objectContaining({ platform: "weibo", page: 2 }),
      );
    });

    await act(async () => {
      await router.navigate("/leads?platform=xhs&page=1&status=unverified");
    });
    await waitFor(() => {
      expect(api.getLeads).toHaveBeenLastCalledWith(
        expect.objectContaining({ platform: "xhs", page: 1 }),
      );
    });

    await act(async () => {
      await router.navigate(-1);
    });
    await screen.findByText("weibo (951)");
    await waitFor(() => {
      expect(api.getLeads).toHaveBeenLastCalledWith(
        expect.objectContaining({ platform: "weibo", page: 2 }),
      );
    });
  });

  it("shows quick conversion for a verified handle-only lead", () => {
    expect(hasQuickConvertIdentity({
      status: "unverified",
      account_id: "",
      platform_handle: "xhs-handle",
      profile_url: "",
    })).toBe(true);
  });
});
