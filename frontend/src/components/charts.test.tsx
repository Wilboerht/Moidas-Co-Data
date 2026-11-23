import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { HorizontalBarChart } from "./charts";

describe("HorizontalBarChart", () => {
  it("provides keyboard-accessible links for chart filtering", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 800,
      bottom: 220,
      width: 800,
      height: 220,
      toJSON: () => ({}),
    });
    render(
      <MemoryRouter>
        <HorizontalBarChart
          ariaLabel="审核状态分布"
          items={[{
            label: "已采用",
            value: 2,
            percent: 50,
            display: "2 · 50.0%",
            href: "/creators?status=adopted",
          }]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "查看已采用达人" })).toHaveAttribute(
      "href",
      "/creators?status=adopted",
    );
    expect(screen.getByRole("table", { name: "审核状态分布" })).toBeInTheDocument();
  });
});
