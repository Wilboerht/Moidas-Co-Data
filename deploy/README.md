# Co-data 公网部署（Nginx + systemd）

这是一份面向 Ubuntu/Debian 的单机部署示例。应用只监听 `127.0.0.1:8501`，由 Nginx 负责 HTTPS 和公网入口。

## 1. 准备服务器

安装 Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 22+、Nginx、certbot：

```bash
sudo apt update
sudo apt install -y python3 python3-venv nginx certbot python3-certbot-nginx
# uv 以 codata 用户安装，见下方
```

创建一个专用系统用户：

```bash
sudo useradd --system --create-home --home-dir /opt/codata --shell /usr/sbin/nologin codata
sudo mkdir -p /opt/codata
sudo chown codata:codata /opt/codata
sudo -u codata bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo chown -R root:root /opt/codata
```

把项目同步到 `/opt/codata`（`rsync`、`git clone` 或 scp 都可以）。代码和前端构建保持 root 只读，只把运行数据与 `.env` 交给服务用户：

```bash
sudo mkdir -p /opt/codata/data
sudo chown -R codata:codata /opt/codata/data
```

## 2. 安装依赖并构建前端

```bash
cd /opt/codata
sudo /opt/codata/.local/bin/uv sync --frozen
sudo npm --prefix /opt/codata/frontend ci
sudo npm --prefix /opt/codata/frontend run build
```

## 3. 配置 .env

```bash
sudo cp .env.example .env
sudo chown codata:codata .env
sudo chmod 600 .env
sudo -u codata nano .env
```

至少配置一个采集服务商：

```dotenv
DATAFLOW_HOST=https://dataflowserver.org
DATAFLOW_TOKEN=你的-token
DATAFLOW_ALLOWED_HOSTS=dataflowserver.org

# 可选：Rnote 备用小红书服务商
RNOTE_HOST=https://rnote.dev
RNOTE_API_KEY=
RNOTE_ALLOWED_HOSTS=rnote.dev

# 可选：其他小红书/抖音 API（不配置则不启用）
EVERYINFRA_API_KEY=
TIKHUB_API_KEY=
JUSTONEAPI_TOKEN=
SOCIALDATAX_API_KEY=
REDFOX_API_KEY=

CODATA_ALLOWED_HOSTS=codata.example.com
CODATA_ALLOWED_ORIGINS=https://codata.example.com
CODATA_COOKIE_SECURE=true
CODATA_ENABLE_DOCS=false
CODATA_PROVIDER_TRUST_ENV=false
```

`CODATA_ALLOWED_HOSTS` 和 `CODATA_ALLOWED_ORIGINS` 必须和实际域名完全一致，否则 Host/Origin 校验会返回 400/403。`DATAFLOW_ALLOWED_HOSTS` / `RNOTE_ALLOWED_HOSTS` 是出站服务商域名白名单，不要把文档域名当成 API host。

`CODATA_ROOT` 和 `CODATA_ENV_FILE` 不是 `.env` 项：它们在进程启动时读取，必须在 `codata.service` 的 `Environment=` 中设置（示例 unit 已经包含）。`CODATA_ROOT` 写进 `.env` 不会生效。

自动发件需要独立的 SMTP 密码加密密钥（不要放进 `data/`，否则会和数据库备份一起泄露）：

```bash
sudo install -d -m 750 -o codata -g codata /etc/codata
sudo -u codata env CODATA_EMAIL_KEY_FILE=/etc/codata/email.key \
  /opt/codata/.venv/bin/codata-admin email-key --init
sudo chown root:codata /etc/codata /etc/codata/email.key
sudo chmod 750 /etc/codata
sudo chmod 640 /etc/codata/email.key
```

`codata.service` 已经设置 `CODATA_EMAIL_KEY_FILE`。自动发件 worker 运行在 `codata-web` 进程内，请保持单实例；服务器需要能访问所选 SMTP 的 465/587 端口，日志在 `data/logs/email_worker.log`。密钥丢失后无法解密已保存的 SMTP 密码，只能删除并重新添加发件账号；备份数据库时请另外备份这把密钥。

对外发信前请在 `.env` 设置 `CODATA_PUBLIC_URL`（例如 `https://codata.example.com`），worker 会据此生成带随机令牌的退订链接和 `List-Unsubscribe` 头；留空时邮件只能提示收件人回复退订。冷却分别由 `CODATA_EMAIL_COOLDOWN_DAYS`（邮箱，默认 30）和 `CODATA_EMAIL_CREATOR_COOLDOWN_DAYS`（达人，默认 90）控制。worker 心跳超过 2 分钟会显示在任务页，便于发现进程未启动或卡死。

在线更新的令牌保存到 `data/runtime.env`，启动时覆盖基础 `.env` 中的同名配置。该文件由 `codata` 用户写入，systemd 只需开放 `data/`；基础 `.env` 和代码可以保持只读。备份时必须同时包含 `.env` 与 `data/runtime.env`。

## 4. 创建管理员

应用没有自助注册，首个管理员通过 CLI 创建：

```bash
cd /opt/codata
sudo -u codata .venv/bin/codata-admin create-admin --username admin
```

会交互式要求输入密码（至少 8 个字符）。后续用户可以在页面「用户管理」中创建，也可以使用同一 CLI 重置密码。

## 5. 启动服务

```bash
sudo cp /opt/codata/deploy/codata.service /etc/systemd/system/codata.service
sudo systemctl daemon-reload
sudo systemctl enable --now codata
sudo systemctl status codata
```

## 6. 配置 Nginx 和证书

先用 HTTP-only 配置完成 ACME 验证，再启用完整 HTTPS 配置：

```bash
sudo mkdir -p /var/www/certbot
sudo tee /etc/nginx/conf.d/codata.conf >/dev/null <<'EOF'
server {
    listen 80;
    server_name codata.example.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 404; }
}
EOF
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/certbot -d codata.example.com \
  --deploy-hook "systemctl reload nginx"
```

证书签发后换用完整配置：

```bash
sudo cp /opt/codata/deploy/nginx-codata.conf.example /etc/nginx/conf.d/codata.conf
sudo nano /etc/nginx/conf.d/codata.conf   # 改域名和证书路径
sudo nginx -t && sudo systemctl reload nginx
sudo certbot renew --dry-run
```

确认只开放 80/443：

```bash
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

不要开放 `8501`。应用的服务单元已经绑定到 `127.0.0.1`。

## 7. 账户与权限

- 所有用户共享同一批轮次、达人、关键词；个人账号用于登录、责任归属和审计。
- `admin` 可以管理用户、修改 DataFlow/Rnote token；`member` 只能使用业务功能和修改自己的密码。
- 登录后右上/侧栏用户菜单可以修改密码、退出登录。
- 用户管理页可以创建账号、切换角色、停用账号、重置密码、注销会话。
- 审计日志在 `audit_log` 表中（登录、用户管理、token 更新等）。

### 账户权限补充

- 新建用户或管理员重置密码后，用户首次登录会强制修改密码。
- 新用户默认没有发起采集权限；管理员在「用户管理」中开启“允许发起采集”。
- 新用户默认只能查看线索；需要新增或编辑线索时，管理员在「用户管理」中开启“允许维护线索”。采集权限与线索权限相互独立。
- 无采集权限的账号看不到采集任务、关键词、系统设置及轮次名称；任务、关键词和统计数据会脱敏，但轮次状态、时间等基础字段仍会返回给共享看板使用。
- 校验/转换线索需要“采集权限 + 线索权限”同时开启；只能处理自己负责或未分配的线索。
- `CODATA_MAX_ACTIVE_JOBS` 控制全局同时运行的采集任务数，默认 2。

## 8. 备份与升级

备份（使用 SQLite 在线备份，服务无需长时间停机）：

```bash
sudo install -d -m 700 /var/backups/codata
sudo /opt/codata/.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
src = Path('/opt/codata/data/codata.sqlite')
dst = Path('/var/backups/codata') / f'codata-{__import__("datetime").date.today()}.sqlite'
target = sqlite3.connect(dst)
source = sqlite3.connect(src)
try:
    source.backup(target)
finally:
    target.close(); source.close()
PY
sudo tar -czf /var/backups/codata/env-$(date +%F).tar.gz -C /opt/codata --ignore-failed-read .env data/runtime.env
sudo tar -czf /var/backups/codata/attachments-$(date +%F).tar.gz -C /opt/codata/data --ignore-failed-read cooperation_attachments
sudo chmod 600 /var/backups/codata/*
```

合作附件存放在 `data/cooperation_attachments/`，恢复数据库时请同时恢复对应日期的附件包。也可以直接运行 `sudo -u codata /opt/codata/.venv/bin/codata-admin backup --out /var/backups/codata`，它会同时备份数据库、`.env`、`data/runtime.env` 和附件，并生成带 SHA-256 校验和的 `manifest.json`。`codata-admin backup-verify --path <备份目录>` 可随时校验；`codata-admin status` 查看数据库/任务/备份/磁盘状态；`codata-admin config-check` 报告 Host/Origin/Cookie 的危险组合。

恢复时先停服务，再执行（会自动备份当前数据库、清理 WAL/SHM、校验恢复后的完整性）：

```bash
sudo systemctl stop codata
sudo -u codata /opt/codata/.venv/bin/codata-admin restore --from /var/backups/codata/codata-YYYYMMDD-HHMMSS --yes
sudo systemctl start codata
```

恢复步骤（先停服务，确认备份可打开后再替换）：

```bash
sudo systemctl stop codata
# 1. 校验备份完整性
sudo /opt/codata/.venv/bin/python -c \
  "import sqlite3; print(sqlite3.connect('/var/backups/codata/codata-YYYY-MM-DD.sqlite').execute('PRAGMA integrity_check').fetchone()[0])"
# 2. 清掉旧 WAL/SHM，避免旧日志被重放；用 root 覆盖并修正属主
sudo rm -f /opt/codata/data/codata.sqlite-wal /opt/codata/data/codata.sqlite-shm /opt/codata/data/codata.sqlite-journal
sudo cp /var/backups/codata/codata-YYYY-MM-DD.sqlite /opt/codata/data/codata.sqlite
sudo chown codata:codata /opt/codata/data/codata.sqlite
# 3. 恢复环境变量与合作附件（使用同一备份日期）
sudo tar -xzf /var/backups/codata/env-YYYY-MM-DD.tar.gz -C /opt/codata
sudo tar -xzf /var/backups/codata/attachments-YYYY-MM-DD.tar.gz -C /opt/codata/data
sudo chown -R codata:codata /opt/codata/.env /opt/codata/data/runtime.env /opt/codata/data/cooperation_attachments
sudo systemctl start codata
curl -fsS http://127.0.0.1:8501/api/health
```

数据库与附件必须来自同一备份日期，否则会出现“有记录、缺文件”。数据库、`.env`、`data/runtime.env` 与 `data/cooperation_attachments/` 建议放在同一备份批次里。

升级：

```bash
cd /opt/codata
# 先按上一节完成备份；确认没有运行中的采集任务（或先取消并等待进程退出）
sudo /opt/codata/.local/bin/uv sync --frozen
sudo npm --prefix /opt/codata/frontend ci
sudo npm --prefix /opt/codata/frontend run build
sudo systemctl restart codata
```

应用启动时如检测到 schema 版本升级，会在 `data/` 下自动生成 `codata.sqlite.bak-schema-v*` 备份后再迁移。默认保留 90 天内且最近 10 份的应用备份，启动时自动清理；可用 `CODATA_BACKUP_RETENTION_DAYS` 和 `CODATA_BACKUP_RETENTION_COUNT` 调整，0 表示对应规则不限制。先运行 `sudo -u codata /opt/codata/.venv/bin/codata-admin backups-prune` 查看待清理文件，确认后再加 `--apply`。

不要在同一份 SQLite 上启动多个 Uvicorn worker；如果后续需要多实例/高并发，再迁移到 Postgres 并把采集任务拆到独立 worker。

## 9. 后续接 SSO

用户表已经预留 `auth_source` 和 `external_id` 字段。后续接公司 SSO 时，建议保留现有 `users` 和角色/会话模型，只把登录入口替换为 OIDC 回调：
- OIDC 回调校验 `id_token` 后，根据 `external_id` 或邮箱查找/创建用户；
- 把 `auth_source` 设为 `oidc`；
- 继续复用现有的 session cookie、CSRF、角色和用户管理；
- 可以保留密码登录作为管理员应急入口，或完全关闭。
