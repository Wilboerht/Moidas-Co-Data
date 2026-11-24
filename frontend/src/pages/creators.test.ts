import { batchCooperationTargets, editedValue } from "./creators";

describe("batch edit tri-state values", () => {
  it("keeps, sets, and clears fields without conflating empty input", () => {
    expect(editedValue("keep", "existing")).toBeNull();
    expect(editedValue("set", "new value")).toBe("new value");
    expect(editedValue("clear", "existing")).toBe("");
    expect(editedValue("clear", "")).toBe("");
  });

  it("uses the post-review adoption state for cooperation updates", () => {
    const items = [{ status: "pending" }, { status: "adopted" }, { status: "rejected" }];
    expect(batchCooperationTargets(items, 1, "adopted")).toHaveLength(3);
    expect(batchCooperationTargets(items, 1, "rejected")).toHaveLength(0);
    expect(batchCooperationTargets(items, null, "none")).toEqual([{ status: "adopted" }]);
  });
});
