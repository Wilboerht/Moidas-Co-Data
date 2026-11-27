# Co-data

A multi-platform creator data collection, cleaning, and manual review workspace.

完整用户使用手册见 [docs/使用手册.md](docs/使用手册.md)。

Pipeline:

`round/category → keyword search → notes → creator profiles → dedupe & store → manual review → export`

## Features

- **Rounds & tasks** — create collection rounds by platform, category, and keyword set; background jobs with live progress, logs, cancel, forced runs, and archive/restore.
- **Creator library** — server-side pagination, filtering, sorting, detail drawer, batch review, CSV/Excel export. Creator follow-up ownership is a responsibility marker, not an edit lock. Adoption is the per-round screening result; the all-round view uses each creator's latest round. Cooperation follow-up is a separate per-creator master pipeline (未联系 / 已联系 / 洽谈中 / 合作中 / 合作完成 / 已拒绝 / 已取消). Cooperation is a master field shared across rounds: once a creator enters the pipeline their status remains visible in later rounds even if that round is not adopted, while creators that never entered cooperation show no status. Only creators adopted in at least one round can start or update cooperation. Adopting a creator enrolls them as 未联系 once, and later rounds do not reset cooperation. Cooperation statuses support filters, batch updates, stale follow-up detection, and a change history. Profile completeness is reported as not fetched, partial, or complete.
- **Lead library** — manual/bulk lead intake, local identity matching against collected creators, ownership, conversion to creators with cost-aware fetch options. Leads keep the visible platform account (`platform_handle`, e.g. 小红书号) separate from the internal `user_id`, track an identity status (`unverified / verified / mismatch / not_found`), and require confirmation before converting when a provider resolves a different account. A verification preview endpoint and `codata-admin leads-verify` resolve accounts without converting. The lead sheet lets you pick the provider per verification/conversion (default auto = cheapest ready provider, with the per-call price shown).
- **Provider selection** — pick the cheapest configured provider automatically and pre-flight balance checks; transient per-item failures are recorded and tolerated up to a threshold instead of aborting on the first error.
- **Cost tracking** — per-request price config, token/USD conversion, live job costs, and API call audit.
- **Admin panel** — user management, credential rotation, keyword library, and system settings.
- **Safety** — per-provider host allowlists, redirect protection, export formula-injection escaping; secrets live only in `.env` / `CODATA_ENV_FILE`, never in code or the database.
- **Automatic email outreach** — personal or shared SMTP accounts with encrypted passwords, an adopted-but-not-contacted target pool, atomic create-and-schedule transactions, plain-text campaigns with ISO weekdays, time windows, intervals, jitter and daily caps, active-campaign dedupe plus email/creator cooldowns, stable Message-IDs and no automatic retry for unknown delivery outcomes, pause/resume/manual retry, auto-mark contacted, worker health and rotating logs, secure one-click unsubscribe links + `List-Unsubscribe` headers, and an admin-managed suppression list.

## Tech Stack

| Layer | Stack |
|---|---|
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4, shadcn/ui, Recharts |
| Backend | FastAPI, Uvicorn, SQLite (WAL + FTS5) |
| Runtime | Python 3.11+, uv, Node.js 22+ |

## Getting Started

```powershell
uv sync
Copy-Item .env.example .env    # then fill in at least one provider API key

cd frontend
npm install
npm run build
cd ..

uv run codata-admin create-admin --username admin
uv run codata-web
```

Open <http://127.0.0.1:8501>. On Windows you can also run `start_panel.bat`.

Supported providers include DataFlow, Rnote, EveryInfra, TikHub, JustOneAPI, SocialDataX, and RedFoxHub. Provider hosts, prices, and selection policy live in `config/platforms.json`; secrets belong in `.env`.

## Project Structure

```text
src/codata/       FastAPI app, collection pipeline, providers, storage
frontend/         React panel (Vite)
config/           Platform and provider configuration
deploy/           systemd unit and Nginx example
docs/             使用手册
tests/            Backend unit tests
data/             SQLite database, raw responses, logs (gitignored)
```

## Commands

```powershell
uv run codata-web                          # start the panel
uv run codata balance                      # check DataFlow balance
uv run codata round list                   # list collection rounds
uv run codata round run 1                  # run a round
uv run codata-admin leads-dedupe           # report duplicate leads (add --apply)
uv run codata-admin leads-backfill-matches # report historical matches (add --apply)
uv run codata-admin leads-verify          # report leads needing verification (add --apply to call providers)
uv run codata-admin backups-prune          # report expired backups (add --apply)
uv run codata-admin config-check           # print effective config and safety warnings
uv run codata-admin status                 # database, jobs, backups, attachments, disk
uv run codata-admin backup --out D:\backups # back up database, env files and attachments
uv run codata-admin email-key --init        # create the SMTP password encryption key (once)
uv run codata-admin backup-verify --path D:\backups\codata-YYYY... # verify checksums and integrity
uv run codata-admin restore --from D:\backups\codata-YYYY... --yes # restore (stop the panel first)
uv run codata-backfill                     # report import candidates (add --apply to fetch)
uv run python -m unittest discover -s tests -v   # backend tests
npm --prefix frontend test                    # frontend interaction tests
```

## Notes

- `.env` contains real credentials and is gitignored.
- The first start after a schema upgrade automatically creates a backup in `data/` before migrating.
- Schema v11 removes the old review rollup from `cooperation_status` and makes cooperation follow-up the only master status. Adoption remains the per-round review result. Previously adopted creators are migrated to `未联系` so they appear in the follow-up queue. Schema v12 adds structured API error details; cooperation writes require a creator adopted in at least one round and otherwise return 409.
- Schema v14 splits lead identity into `platform_handle` (visible account) and `account_id` (platform user id), adds `identity_status` plus a `lead_identity_events` audit trail, and migrates legacy account values by platform. Conversion no longer silently overwrites a mismatched account: the API returns 409 with the requested/resolved identities unless `confirm_mismatch=true`. Use the lead sheet's “校验账号” preview or `codata-admin leads-verify --apply` to resolve handles to real user ids (provider fees apply).
- Schema v15 adds `creators.cooperation_reset_at`: after an admin deletes all cooperation records of a creator, later review saves (including re-adoption) do not auto-create a new 未联系 record; cooperation restarts only when explicitly initiated (开始合作 / setting a cooperation status).
- Schema v16 records the latest successful run per round (`rounds.latest_run_id`, `note_hits.last_run_id`). Rerunning a round refreshes statistics, exports, and dashboard data to the newest run; reviewed or claimed creators stay in the round even when they are no longer found, while unreviewed collected creators that disappear from the latest run are removed. Legacy rounds keep using all historical hits until their next successful run. If any search page fails, raw responses and costs are retained but the previous creator snapshot remains active.
- Schema v17 adds `creator_rounds.created_at`, recording when a creator first joined each round so round-scoped activity calendars do not depend on the creator's global profile creation time.
- Schemas v18–v21 add email sender permissions, SMTP account authentication state, recipient sent dates and manual retry counters, per-account minimum intervals, worker heartbeat/tick diagnostics, and hashed one-click unsubscribe tokens. Existing databases are backed up before migration; back up `CODATA_EMAIL_KEY_FILE` separately because it is intentionally outside `data/`.
- For XHS leads with only a 小红书号, verification first searches by the lead's nickname, matches the author's `red_id`, then fetches that user's profile; this can cost one search call plus one profile call. If the search does not find an exact `red_id`, the lead is left unverified and the UI asks for a profile URL or the 24-character user id. Failed provider calls are recorded with actual cost 0 plus an estimated cost in the API audit; the lead attempt shows 0 actual cost.
- Backups are retained for 90 days and capped at 10 recent files by default (`CODATA_BACKUP_RETENTION_DAYS` / `CODATA_BACKUP_RETENTION_COUNT`; 0 disables the corresponding rule). Use `codata-admin backups-prune` for a dry-run.
- New/reset passwords require at least 8 characters (max 200) and must differ from the current password.
- Provider requests do not inherit `HTTP_PROXY`/`HTTPS_PROXY` by default; set `CODATA_PROVIDER_TRUST_ENV=true` only if a proxy is required.
- Provider prices are read from `config/platforms.json` at runtime for the web panel and CLI estimates; built-in 45/90 token defaults are only fallbacks when the platform config is unavailable.
- Excel imports apply cooperation status as a new case when there is no open case and report skipped/conflicting rows as warnings; `codata-migrate-leads` moves cooperation cases to the resolved account and keeps the legacy creator when both sides have open cases.
- Raw upstream responses are kept under `data/raw/` for re-parsing. Raw files and API calls are retained for 365 days by default; set `CODATA_RAW_RETENTION_DAYS` / `CODATA_API_CALL_RETENTION_DAYS` to 0 for indefinite retention.
- Rounds should be archived rather than purged; permanent deletion is admin-only, requires confirmation, refuses rounds linked to leads, and creates a database backup.
- Only collect publicly available data and follow each provider's terms of service.
- For public deployment, use Nginx + HTTPS and see `deploy/README.md`.
- `start_lan.bat` serves plain HTTP on the LAN for trusted networks only; it injects the current private IPv4s into the Host/Origin allowlists and adds a LAN-scoped firewall rule. Use HTTPS or a private tunnel for anything beyond a trusted office LAN.
