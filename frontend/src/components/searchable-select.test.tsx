import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { SearchableSelect } from "./searchable-select";

const options = [
  { value: "all", label: "全部轮次", pinned: true },
  { value: "1", label: "R20261122-01", description: "小红书 · 护肤", keywords: ["xhs"] },
  { value: "2", label: "R20261123-02", description: "抖音 · 美妆" },
];

function renderSelect(onChange = vi.fn(), value = "all") {
  render(
    <SearchableSelect
      label="轮次"
      options={options}
      value={value}
      onChange={onChange}
      placeholder="全部轮次"
    />,
  );
  return onChange;
}

describe("SearchableSelect", () => {
  it("filters by description and keywords, then selects by click", async () => {
    const user = userEvent.setup();
    const onChange = renderSelect();

    await user.click(screen.getByRole("button", { name: "轮次：全部轮次" }));
    const search = screen.getByRole("combobox", { name: "搜索轮次" });

    await user.type(search, "美妆");
    expect(screen.getByRole("option", { name: /R20261123-02/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /R20261122-01/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /全部轮次/ })).toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "xhs");
    expect(screen.getByRole("option", { name: /R20261122-01/ })).toBeInTheDocument();

    await user.click(screen.getByRole("option", { name: /R20261122-01/ }));
    expect(onChange).toHaveBeenCalledWith("1");
  });

  it("supports arrow keys, enter and aria-activedescendant", async () => {
    const user = userEvent.setup();
    const onChange = renderSelect();

    await user.click(screen.getByRole("button", { name: "轮次：全部轮次" }));
    const search = screen.getByRole("combobox", { name: "搜索轮次" });
    await user.type(search, "R2026");

    const activeId = search.getAttribute("aria-activedescendant");
    expect(activeId).toBeTruthy();
    expect(document.getElementById(activeId!)).toHaveTextContent("R20261122-01");

    await user.keyboard("{ArrowDown}{ArrowUp}{Enter}");
    expect(onChange).toHaveBeenCalledWith("1");
  });

  it("keeps pinned options visible and shows an empty hint without matches", async () => {
    const user = userEvent.setup();
    const onChange = renderSelect(vi.fn(), "1");

    const trigger = screen.getByRole("button", { name: "轮次：R20261122-01" });
    expect(trigger).toHaveAttribute("title", "R20261122-01 · 小红书 · 护肤");

    await user.click(trigger);
    const search = screen.getByRole("combobox", { name: "搜索轮次" });
    await user.type(search, "zzz");

    expect(screen.getByText("没有匹配项")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /全部轮次/ })).toBeInTheDocument();
    expect(search).not.toHaveAttribute("aria-activedescendant");

    await user.keyboard("{Enter}");
    expect(onChange).not.toHaveBeenCalled();

    await user.click(screen.getByRole("option", { name: /全部轮次/ }));
    expect(onChange).toHaveBeenCalledWith("all");
  });
});
