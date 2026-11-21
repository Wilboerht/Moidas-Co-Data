# Co-data

A multi-platform creator data collection, cleaning, and manual review workspace.

Pipeline:

`round/category → keyword search → notes → creator profiles → dedupe & store → manual review → export`

## Features

- **Rounds & tasks** — create collection rounds by platform, category, and keyword set; background jobs with live progress and logs.
- **Creator library** — server-side pagination, filtering, sorting, 23 fields, detail drawer, batch review, CSV/Excel export.
- **Provider fallback** — pick the cheapest configured provider automatically, pre-flight balance checks, retry with the next provider on failure.
- **Cost tracking** — per-request price config, token/USD conversion, and API call audit.
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
uv run python -m unittest discover -s tests -v   # tests
```

## Notes

- `.env` contains real credentials and is gitignored.
- Raw upstream responses are kept under `data/raw/` for re-parsing.
- Only collect publicly available data and follow each provider's terms of service.
- For public deployment, use Nginx + HTTPS and see `deploy/README.md`.
