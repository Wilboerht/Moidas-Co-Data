import { api, downloadFile } from "./api";
import type { CreatorQuery } from "./types";
import { vi } from "vitest";

const creatorQuery: CreatorQuery = {
  platform: "",
  page: 1,
  page_size: 50,
  search: "",
  statuses: [],
  tiers: [],
  directions: [],
  sources: [],
  source_mode: "any",
  labels: [],
  owner: "",
  cooperation: [],
  cooperation_stale_days: "",
  min_fans: "",
  max_fans: "",
  only_unenriched: false,
  needs_action: false,
  sort: "fans",
  order: "desc",
};

function mockJson(payload: unknown) {
  return vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));
}

describe("API query contracts", () => {
  it("omits platform for the all-platform creator view", async () => {
    const fetchMock = mockJson({ items: [], total: 0, page: 1, page_size: 50 });
    vi.stubGlobal("fetch", fetchMock);
    await api.getCreators(null, creatorQuery);
    const url = new URL(fetchMock.mock.calls[0][0], "http://localhost");
    expect(url.searchParams.has("platform")).toBe(false);

    await api.getCreators(null, { ...creatorQuery, platform: "xhs" });
    const scopedUrl = new URL(fetchMock.mock.calls[1][0], "http://localhost");
    expect(scopedUrl.searchParams.get("platform")).toBe("xhs");
  });

  it("sends cooperation filters and uses the global cooperation endpoint", async () => {
    const fetchMock = mockJson({ items: [], total: 0, page: 1, page_size: 50 });
    vi.stubGlobal("fetch", fetchMock);
    await api.getCreators(null, {
      ...creatorQuery,
      cooperation: ["contacted", "none"],
      cooperation_stale_days: "7",
    });
    const url = new URL(fetchMock.mock.calls[0][0], "http://localhost");
    expect(url.searchParams.get("cooperation")).toBe("contacted,none");
    expect(url.searchParams.get("cooperation_stale_days")).toBe("7");

    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          changed: true,
          previous_status: "not_contacted",
          status: "contacted",
          cooperation_status: "contacted",
          cooperation_status_label: "已联系",
          cooperation_updated_at: "2026-01-01T00:00:00.000+00:00",
          claimed: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await api.updateCooperation("xhs", "u1", { status: "contacted" });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/creators/xhs/u1/cooperation");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("PATCH");
  });

  it("loads lead facets for the selected round", async () => {
    const fetchMock = mockJson({ platforms: [], statuses: [], sources: [], owners: [], rounds: [] });
    vi.stubGlobal("fetch", fetchMock);
    await api.getLeadFacets(7);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/leads/facets?round_id=7");
  });

  it("downloads through one request and revokes the object URL", async () => {
    const blob = vi.fn().mockResolvedValue(new Blob(["content"]));
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Disposition": 'attachment; filename="creators.csv"' }),
      blob,
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue("blob:export"),
      revokeObjectURL: vi.fn(),
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.useFakeTimers();
    await downloadFile("/api/creators/export", "fallback.csv");
    await vi.runAllTimersAsync();
    vi.useRealTimers();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(blob).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:export");
  });
});
