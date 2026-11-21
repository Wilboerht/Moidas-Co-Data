# AGENTS.md

Frontend stack: React 19, Vite, Tailwind CSS v4, shadcn/ui (Radix), Recharts, TanStack Query.

- UI primitives live in `src/components/ui`; use them before creating new controls.
- Shared page structure is in `src/components/page.tsx`; keep pages dense and flat, avoiding decorative cards.
- Application pages live in `src/pages`.
- Data helpers and API contracts live in `src/shared`.
- Icons use `lucide-react` only; do not mix icon families.
- Charts use Recharts through `src/components/charts.tsx`.
- Run `npm run build` before delivering. There is no separate lint script.
