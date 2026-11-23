export type ChangelogRelease = {
  version: string;
  highlights: string[];
};

export const CHANGELOG: ChangelogRelease[] = [
  {
    version: "0.2.0",
    highlights: [
      "新增 Bilibili、知乎、Instagram 平台采集与字段归一化。",
      "服务商令牌支持在线校验，线索支持自动匹配与历史数据迁移。",
      "新增执行端数据清理与用户层级回填工具。",
    ],
  },
  {
    version: "0.1.0",
    highlights: [
      "首版工作台：轮次采集、关键词库、笔记/达人/线索管理与导出。",
      "多服务商择优、成本追踪、数据备份与账号权限。",
    ],
  },
];
