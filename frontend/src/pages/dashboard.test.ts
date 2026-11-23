import { withRound } from "./dashboard";

describe("withRound", () => {
  it("preserves the selected round for creator and lead links", () => {
    expect(withRound("/creators", 3)).toBe("/creators?round=3");
    expect(withRound("/leads", 3)).toBe("/leads?round=3");
    expect(withRound("/creators", 3, { status: "adopted" })).toBe("/creators?status=adopted&round=3");
  });

  it("leaves unrelated paths and all-round links unchanged", () => {
    expect(withRound("/tasks", 3)).toBe("/tasks");
    expect(withRound("/leads", null)).toBe("/leads");
  });
});
