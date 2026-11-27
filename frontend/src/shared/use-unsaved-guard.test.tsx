import { useState } from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Link, RouterProvider } from "react-router-dom";
import { useUnsavedGuard } from "./use-unsaved-guard";

function DraftPage() {
  const [value, setValue] = useState("");
  useUnsavedGuard(Boolean(value), "离开测试页面？");
  return (
    <div>
      <input aria-label="草稿" value={value} onChange={(event) => setValue(event.target.value)} />
      <Link to="/next">离开</Link>
    </div>
  );
}

function makeRouter(initialEntries: [string, string] = ["/draft", "/next"]) {
  return createMemoryRouter(
    [
      { path: "/draft", element: <DraftPage /> },
      { path: "/next", element: <p>已离开</p> },
    ],
    { initialEntries, initialIndex: 0 },
  );
}

describe("useUnsavedGuard", () => {
  it("blocks link navigation until the user confirms", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const router = makeRouter();
    render(<RouterProvider router={router} />);

    await user.type(screen.getByLabelText("草稿"), "未保存");
    await user.click(screen.getByRole("link", { name: "离开" }));
    expect(confirm).toHaveBeenCalledWith("离开测试页面？");
    expect(screen.getByLabelText("草稿")).toHaveValue("未保存");

    await user.click(screen.getByRole("link", { name: "离开" }));
    expect(await screen.findByText("已离开")).toBeInTheDocument();
  });

  it("blocks browser history navigation", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const router = createMemoryRouter(
      [
        { path: "/next", element: <p>上一页</p> },
        { path: "/draft", element: <DraftPage /> },
      ],
      { initialEntries: ["/next", "/draft"], initialIndex: 1 },
    );
    render(<RouterProvider router={router} />);
    await user.type(screen.getByLabelText("草稿"), "未保存");

    await act(async () => {
      await router.navigate(-1);
    });

    expect(router.state.location.pathname).toBe("/draft");
    expect(screen.getByLabelText("草稿")).toHaveValue("未保存");
  });
});
