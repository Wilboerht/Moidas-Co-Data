import { api } from "./api";
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

  it("loads lead facets for the selected round", async () => {
    const fetchMock = mockJson({ platforms: [], statuses: [], sources: [], owners: [], rounds: [] });
    vi.stubGlobal("fetch", fetchMock);
    await api.getLeadFacets(7);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/leads/facets?round_id=7");
  });
});
