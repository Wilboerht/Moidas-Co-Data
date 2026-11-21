# Co-data

多平台信息数据采集、整理和人工筛选工作台。

核心链路：

`轮次垂类 → 关键词搜索 → 笔记作者 → 达人资料 → 去重入库 → 人工标注 → 导出`

## 技术栈

- 前端：React 19、TypeScript、Vite、Tailwind CSS 4、shadcn/ui、Recharts、TanStack Query
- 后端：FastAPI、Uvicorn、SQLite（WAL + FTS5 trigram）
- 任务：面板创建轮次后，由 FastAPI 启动后台采集子进程
- 依赖：Python 3.11+、uv、Node.js 22+

## 快速启动

```powershell
cd C:\Users\hongk\Desktop\Co-data

uv sync
Copy-Item .env.example .env
notepad .env

cd frontend
npm install
npm run build
cd ..

uv run codata-admin create-admin --username admin
uv run codata-web
```

打开 <http://127.0.0.1:8501>。Windows 可直接双击根目录的 `start_panel.bat`。

`.env` 至少配置一个采集服务商：DataFlow（`DATAFLOW_HOST` + `DATAFLOW_TOKEN`）、Rnote（`RNOTE_API_KEY`），或新增的 `EVERYINFRA_API_KEY`、`TIKHUB_API_KEY`、`JUSTONEAPI_TOKEN`、`SOCIALDATAX_API_KEY`、`REDFOX_API_KEY`。访问令牌仅管理员可以在「设置」中更换，保存后写回 `.env` 并立即生效；新增服务商暂不做在线余额验证。默认只监听本机；公网部署请使用 Nginx/HTTPS，账号登录和部署步骤见 `deploy/README.md`。

小红书默认选择当前最便宜且可用的服务商：DataFlow 45 token/次（约 ¥0.045），Rnote $0.01/次（约 ¥0.072，约 72 token）。价格、接口路径和默认策略在 `config/platforms.json`；修改后重启生效。Rnote 只支持小红书，抖音搜索仍需单独的服务商。

## 页面

| 页面 | 功能 |
|---|---|
| 工作台 | KPI、处理进度、粉丝层级、已处理状态、高互动达人、其他轮次 |
| 达人库 | 平台 Tab、服务端分页/筛选/排序、23 个字段、固定昵称列、列设置、详情抽屉、批量标注、CSV/Excel 导出 |
| 新建采集 | 选择平台与服务商，填写轮次垂类，选择词库或输入新词，设置页数与补全上限，实时估算 token |
| 采集任务 | 自动刷新进度、日志、重新采集、接口调用审计、轮次列表 |
| 关键词 | 新增、启停、删除未被引用的搜索词；垂类在轮次上设置 |
| 设置 | DataFlow/Rnote 连接与用量、令牌更换、数据路径、余额查询、系统说明 |

达人库的筛选、排序、页码、平台和详情会写入 URL；列显示设置保存在 localStorage（`codata_columns_v5`）。

## 服务商与费用

平台能力、服务商 host、接口路径、单价和默认选择策略在 `config/platforms.json`，令牌只放在 `.env`。

- **DataFlow**：小红书 45 token/次，约 ¥0.045；抖音仅有作品/评论接口。
- **Rnote**：小红书 `$0.01/次`，按配置汇率折算约 72 token / ¥0.072；仅成功请求扣费。
- **EveryInfra**：小红书/抖音搜索与用户；公开价约小红书 $5.56/千次、抖音搜索 $8.33/千次。
- **TikHub**：小红书/抖音搜索与用户；搜索接口 `$0.01/次`，其他接口按端点计价。
- **JustOneAPI**：小红书/抖音搜索、用户；价格以控制台和免费试用额度为准。
- **SocialDataX**：小红书/抖音搜索、用户；约 ¥0.10/次，接口文档较完整。
- **RedFoxHub**：小红书/抖音搜索、用户；¥0.02–0.06/次，按接口和用量阶梯。
- 新建轮次默认 `自动`，按 `config/platforms.json` 中折算后的价格选择最便宜且已配置的服务商。
- 开跑前如果首选服务商余额/连通性预检失败，会自动尝试下一个可用服务商；任务中途不混用服务商，失败后可用另一个 provider 重跑。
- Rnote 搜索翻页会自动携带 `search_id` / `search_session_id`，避免重复页。
- 每个 provider 有独立 host allowlist，禁止跨域重定向，防止 token 泄漏。

## 前端结构

```text
frontend/src/
  components/
    ui/                  shadcn/ui 组件
    app-shell.tsx        Sidebar + 顶栏 + 路由出口
    page.tsx             Page / PageHeader / Section / EmptyState
    charts.tsx           Recharts 统计图
    creator-sheet.tsx    达人详情抽屉
    multi-select.tsx     多选筛选器
  pages/                 六个业务页面
  shared/                API、类型、格式化、轮次上下文
  lib/                   shadcn 工具
  index.css              Tailwind 与 shadcn 主题
```

图标统一使用 `lucide-react`。

## 数据与导出

达人库支持 23 个字段，包括粉丝数、粉丝层级、采用状态、匹配方向、命中关键词、代表作、互动量、备注、标签、账号 ID、来源、执行者、样本笔记等。

导出支持：

- 当前筛选的 CSV / Excel
- 全部平台的 CSV
- CSV 使用 UTF-8 BOM，Excel 使用 write-only 临时文件
- 文本单元格做公式注入转义

## 常用命令

```powershell
uv run codata balance
uv run codata stats

uv run codata round list
uv run codata round create --keyword "咖啡" --keyword "手冲咖啡" --category "咖啡" --pages 1 --max-creators 20
uv run codata round run 1
uv run codata round run 1 --refresh-users --force

uv run codata import-excel "path/to/达人池.xlsx" --round-name "护肤达人池-导入" --keyword "护肤"
uv run codata import-raw data/raw/xxx_xhs_search.json data/raw/xxx_xhs_user.json --keyword "咖啡" --category "咖啡"
```

## 开发与测试

```powershell
# 后端测试
uv run python -m unittest discover -s tests -v

# 前端类型检查 + 构建
cd frontend
npm run build
```

后端测试不调用真实 API。当前测试覆盖字段归一化、粉丝层级、聚合去重、人工标注保留、达人缓存、任务状态、Excel/JSON 导入、FTS 搜索、分页/筛选/facets、CSV/Excel 导出、轮次删除等。

## 数据目录

```text
data/
  codata.sqlite   # SQLite 数据库
  raw/            # 原始 JSON 响应
  logs/           # 后台任务日志
```

重要表：`rounds`、`keywords`、`notes`、`creators`、`creator_rounds`、`jobs`、`api_calls`。

## 注意事项

- `.env` 含真实 Token，已在 `.gitignore` 中忽略。
- 上游响应字段可能变化；原始 JSON 会保留在 `data/raw/`。
- 请遵守 DataFlow、Rnote 和平台的使用条款，仅采集公开数据。
- `config/platforms.json` 修改后重启服务生效；`.env` 和令牌不会写入该文件。
- 费用换算由 `CODATA_TOKEN_CNY`、`CODATA_USD_CNY` 和 `config/platforms.json` 的 `billing` 控制。

## 接口余额

- DataFlow `/user/callremain/count` 返回剩余 token；字段名 `remaining_calls` 是历史命名，不是调用次数。10 token = 0.01 元。
- Rnote `/api/v1/account/balance` 返回美元余额；设置页按配置汇率折算为 token 参考值，实际扣费以 Rnote 账单为准。
