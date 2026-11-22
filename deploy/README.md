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
sudo chown codata:codata /opt/codata/.env
sudo chmod 600 /opt/codata/.env
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
sudo -u codata cp .env.example .env
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

CODATA_ROOT=/opt/codata
CODATA_ALLOWED_HOSTS=codata.example.com
CODATA_ALLOWED_ORIGINS=https://codata.example.com
CODATA_COOKIE_SECURE=true
CODATA_ENABLE_DOCS=false
```

`CODATA_ALLOWED_HOSTS` 和 `CODATA_ALLOWED_ORIGINS` 必须和实际域名完全一致，否则 Host/Origin 校验会返回 400/403。`DATAFLOW_ALLOWED_HOSTS` / `RNOTE_ALLOWED_HOSTS` 是出站服务商域名白名单，不要把文档域名当成 API host。

## 4. 创建管理员

应用没有自助注册，首个管理员通过 CLI 创建：

```bash
cd /opt/codata
sudo -u codata .venv/bin/codata-admin create-admin --username admin
```

会交互式要求输入密码（至少 10 个字符）。后续用户可以在页面「用户管理」中创建，也可以使用同一 CLI 重置密码。

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
sudo tar -czf /var/backups/codata/env-$(date +%F).tar.gz -C /opt/codata .env
sudo chmod 600 /var/backups/codata/*
```

恢复步骤（先停服务，确认备份可打开后再替换）：

```bash
sudo systemctl stop codata
sudo -u codata cp /var/backups/codata/codata-YYYY-MM-DD.sqlite /opt/codata/data/codata.sqlite
sudo systemctl start codata
curl -fsS http://127.0.0.1:8501/api/health
```

升级：

```bash
cd /opt/codata
# 先按上一节完成备份
sudo /opt/codata/.local/bin/uv sync --frozen
sudo npm --prefix /opt/codata/frontend ci
sudo npm --prefix /opt/codata/frontend run build
sudo systemctl restart codata
```

应用启动时如检测到 schema 版本升级，会在 `data/` 下自动生成 `codata.sqlite.bak-schema-v*` 备份后再迁移。不要在同一份 SQLite 上启动多个 Uvicorn worker；如果后续需要多实例/高并发，再迁移到 Postgres 并把采集任务拆到独立 worker。

## 9. 后续接 SSO

用户表已经预留 `auth_source` 和 `external_id` 字段。后续接公司 SSO 时，建议保留现有 `users` 和角色/会话模型，只把登录入口替换为 OIDC 回调：
- OIDC 回调校验 `id_token` 后，根据 `external_id` 或邮箱查找/创建用户；
- 把 `auth_source` 设为 `oidc`；
- 继续复用现有的 session cookie、CSRF、角色和用户管理；
- 可以保留密码登录作为管理员应急入口，或完全关闭。
