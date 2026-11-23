# Co-data

A multi-platform creator data collection, cleaning, and manual review workspace.

Pipeline:

`round/category → keyword search → notes → creator profiles → dedupe & store → manual review → export`

## Features

- **Rounds & tasks** — create collection rounds by platform, category, and keyword set; background jobs with live progress, logs, cancel, forced runs, and archive/restore.
- **Creator library** — server-side pagination, filtering, sorting, detail drawer, batch review, CSV/Excel export. Creator follow-up ownership is a responsibility marker, not an edit lock. Round review status is independent of the master cooperation status: new collection rounds require fresh review without erasing historical cooperation outcomes. Profile completeness is reported as not fetched, partial, or complete.
- **Lead library** — manual/bulk lead intake, local identity matching against collected creators, ownership, conversion to creators with cost-aware fetch options.
- **Provider selection** — pick the cheapest configured provider automatically and pre-flight balance checks; transient per-item failures are recorded instead of aborting the whole round.
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
tests/            Backend unit tests
data/             SQLite database, raw responses, logs (gitignored)
```

## Commands

```powershell
uv run codata-web                          # start the panel
uv run codata balance                      # check provider balances
uv run codata round list                   # list collection rounds
uv run codata round run 1                  # run a round
uv run codata-admin leads-dedupe           # report duplicate leads (add --apply)
uv run codata-admin leads-backfill-matches # report historical matches (add --apply)
uv run codata-admin backups-prune          # report expired backups (add --apply)
uv run codata-backfill                     # report import candidates (add --apply to fetch)
uv run python -m unittest discover -s tests -v   # backend tests
npm --prefix frontend test                    # frontend interaction tests
```

## Notes

- `.env` contains real credentials and is gitignored.
- The first start after a schema upgrade automatically creates a backup in `data/` before migrating.
- Backups are retained for 90 days and capped at 10 recent files by default (`CODATA_BACKUP_RETENTION_DAYS` / `CODATA_BACKUP_RETENTION_COUNT`; 0 disables the corresponding rule). Use `codata-admin backups-prune` for a dry-run.
- New/reset passwords require at least 6 characters (max 200) and must differ from the current password.
- Provider requests do not inherit `HTTP_PROXY`/`HTTPS_PROXY` by default; set `CODATA_PROVIDER_TRUST_ENV=true` only if a proxy is required.
- Raw upstream responses are kept under `data/raw/` for re-parsing.
- Rounds should be archived rather than purged; permanent deletion is admin-only, requires confirmation, refuses rounds linked to leads, and creates a database backup.
- Only collect publicly available data and follow each provider's terms of service.
- For public deployment, use Nginx + HTTPS and see `deploy/README.md`.
