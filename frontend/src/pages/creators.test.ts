import { editedValue } from "./creators";

describe("batch edit tri-state values", () => {
  it("keeps, sets, and clears fields without conflating empty input", () => {
    expect(editedValue("keep", "existing")).toBeNull();
    expect(editedValue("set", "new value")).toBe("new value");
    expect(editedValue("clear", "existing")).toBe("");
    expect(editedValue("clear", "")).toBe("");
  });
});
