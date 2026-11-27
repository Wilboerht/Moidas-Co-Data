import { parseLeadText } from "./bulk-lead-dialog";

const platforms = [{ code: "xhs", label: "小红书" }];

describe("bulk lead parsing", () => {
  it("supports escaped quotes and quoted multiline fields", () => {
    const result = parseLeadText(
      '平台,昵称,账号ID,主页URL,原始标题\n' +
        'xhs,"达人 ""A""",u1,https://www.xiaohongshu.com/user/profile/u1,"第一行\n第二行"',
      platforms,
      "xhs",
    );
    expect(result.invalid).toEqual([]);
    expect(result.items).toHaveLength(1);
    expect(result.items[0].nickname).toBe('达人 "A"');
    expect(result.items[0].platform_handle).toBe("u1");
    expect(result.items[0].account_id).toBe("");
    expect(result.items[0].raw_title).toBe("第一行\n第二行");
  });

  it("maps a user id column separately from the platform account", () => {
    const result = parseLeadText(
      "平台,昵称,账号,用户ID\nxhs,达人,handle1,user1",
      platforms,
      "xhs",
    );
    expect(result.invalid).toEqual([]);
    expect(result.items[0].platform_handle).toBe("handle1");
    expect(result.items[0].account_id).toBe("user1");
  });

  it("supports a single platform-account header", () => {
    const result = parseLeadText("账号\nhandle1", platforms, "xhs");
    expect(result.invalid).toEqual([]);
    expect(result.items[0].platform_handle).toBe("handle1");
    expect(result.items[0].nickname).toBe("");
  });

  it("rejects an unknown platform instead of silently using the default", () => {
    const result = parseLeadText(
      "平台,昵称,账号ID\nunknown,达人,u1",
      platforms,
      "xhs",
    );
    expect(result.items).toEqual([]);
    expect(result.invalid[0].reason).toContain("未知平台");
  });
});
