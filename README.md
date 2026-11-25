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
- **Safety** — per-provider host allowlists, redirect protection, export formula-injection escaping; tokens live only in `.env`.

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
uv run codata-backfill                     # report import candidates (add --apply to fetch)
uv run python -m unittest discover -s tests -v   # backend tests
npm --prefix frontend test                    # frontend interaction tests
```

## Notes

- `.env` contains real credentials and is gitignored.
- The first start after a schema upgrade automatically creates a backup in `data/` before migrating.
- Schema v11 removes the old review rollup from `cooperation_status` and makes cooperation follow-up the only master status. Adoption remains the per-round review result. Previously adopted creators are migrated to `未联系` so they appear in the follow-up queue. Schema v12 adds structured API error details; cooperation writes require a creator adopted in at least one round and otherwise return 409.
- Schema v14 splits lead identity into `platform_handle` (visible account) and `account_id` (platform user id), adds `identity_status` plus a `lead_identity_events` audit trail, and migrates legacy account values by platform. Conversion no longer silently overwrites a mismatched account: the API returns 409 with the requested/resolved identities unless `confirm_mismatch=true`. Use the lead sheet's “校验账号” preview or `codata-admin leads-verify --apply` to resolve handles to real user ids (provider fees apply).
- For XHS leads with only a 小红书号, verification first searches by the lead's nickname, matches the author's `red_id`, then fetches that user's profile; this can cost one search call plus one profile call. If the search does not find an exact `red_id`, the lead is left unverified and the UI asks for a profile URL or the 24-character user id. Failed provider calls are recorded with actual cost 0 plus an estimated cost in the API audit; the lead attempt shows 0 actual cost.
- Backups are retained for 90 days and capped at 10 recent files by default (`CODATA_BACKUP_RETENTION_DAYS` / `CODATA_BACKUP_RETENTION_COUNT`; 0 disables the corresponding rule). Use `codata-admin backups-prune` for a dry-run.
- New/reset passwords require at least 8 characters (max 200) and must differ from the current password.
- Provider requests do not inherit `HTTP_PROXY`/`HTTPS_PROXY` by default; set `CODATA_PROVIDER_TRUST_ENV=true` only if a proxy is required.
- Raw upstream responses are kept under `data/raw/` for re-parsing. Raw files and API calls are retained for 365 days by default; set `CODATA_RAW_RETENTION_DAYS` / `CODATA_API_CALL_RETENTION_DAYS` to 0 for indefinite retention.
- Rounds should be archived rather than purged; permanent deletion is admin-only, requires confirmation, refuses rounds linked to leads, and creates a database backup.
- Only collect publicly available data and follow each provider's terms of service.
- For public deployment, use Nginx + HTTPS and see `deploy/README.md`.
