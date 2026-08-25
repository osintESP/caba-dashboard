# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Curador CABA Intelligence: a fully static dashboard (no backend) for auditing public procurement in the City of Buenos Aires (CABA). It correlates two independent official sources — the Boletín Oficial (norms/actos published day by day) and Buenos Aires Compras / BAC (an OCDS export with real award amounts) — with the explicit goal of surfacing audit signals (non-competitive tenders, vendor concentration, direct-contracting share), not just displaying metrics. `README.md` has the full architecture diagram, data file schemas, and known limitations — this file is about working in the codebase productively, not repeating that.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r runtime/requirements.txt
playwright install chromium           # one-time browser download, only needed for bac_aperturas_scraper.py

# pipeline steps — each reads data/*.json, merges/regenerates, writes back; safe to re-run
python runtime/collector.py                  # scrape today's live Boletín edition -> runtime/output/latest.json
python runtime/data_model.py                 # merge into data/editions.json, norms.json, procurements.json, stats.json
python runtime/procurement_intelligence.py   # -> data/procurement_intelligence.json
python runtime/bac_catalog_collector.py      # download+parse BAC's bac_anual.csv -> data/bac_catalog.json
python runtime/bac_aperturas_scraper.py      # headless-browser scrape of BAC's live tender-opening pages -> data/bac_aperturas.json
BACKFILL_START=7275 python runtime/backfill_boletin.py  # one-off historical Boletín backfill

# tests (stdlib unittest, no pytest/jest, no frontend test runner)
python -m unittest discover -s runtime/tests -v
python runtime/tests/test_data_model.py -v   # single file

# frontend — static, no build step; any static server works
python -m http.server 8000
```

There is no linter, no bundler, no frontend `package.json` — `app.js`/`index.html`/`styles.css` are hand-written and deliberately terse (single-line functions), served as-is.

## Architecture

**The pipeline is "git as database."** Every `runtime/*.py` script reads the current `data/*.json`, merges or regenerates in memory, and writes the whole file back — there's no incremental delta storage. Five GitHub Actions workflows write to `data/` and all share `concurrency.group: caba-dashboard-refresh` so they queue instead of racing each other's push to `master`:
- `refresh-official-data.yml` — every 30 min in business hours: `collector.py` → `data_model.py` → `procurement_intelligence.py`.
- `refresh-bac-data.yml` — once a day: `bac_catalog_collector.py` only, with conditional fetch (skips the ~55MB download if the source's ETag/Last-Modified hasn't changed).
- `refresh-bac-aperturas.yml` — every 30 min in business hours: `bac_aperturas_scraper.py`, a headless-browser (Playwright) scrape of BAC's live transactional site — see the gotcha below before touching it.
- `backfill-boletin.yml` — `workflow_dispatch` only, for patching historical gaps: `backfill_boletin.py` → `procurement_intelligence.py`. Budget ~30-50s per Boletín edition when picking a range (measured, not estimated).
- `pages.yml` — deploys to GitHub Pages on every push to `master`.

When a workflow's persist step hits a rebase conflict: `refresh-bac-data.yml` and `refresh-bac-aperturas.yml` auto-resolve in favor of the current run (their output is a full regeneration from a freshly fetched source, so "ours" is always at least as complete); `refresh-official-data.yml` and `backfill-boletin.yml` do **not** auto-resolve (their output is an incremental merge against the prior file, so blindly picking a side could silently drop an edition) — they fail the job for manual review instead. Keep this distinction if you add a sixth workflow that touches `data/`.

**`bac_aperturas_scraper.py` must navigate by clicking a real link, never `page.goto()` the listing URL directly.** BAC's transactional site (`buenosairescompras.gob.ar`, not the open-data portal) is ASP.NET WebForms with session-bound ViewState/postback. A plain `requests`-based scraper replicating a captured HAR (exact headers, CSRF token, Referer) still fails — the ViewState is single-use and tied to the session that rendered it, so any replay is already stale. Even a real headless browser `goto()`-ing the listing URL directly gets redirected to `Default.aspx` (confirmed) — it only works when you load the homepage first and `page.click()` the actual `<a>` for that listing, letting the browser generate the postback with a live ViewState. If this stops working, suspect the click-through sequence before suspecting Playwright itself.

**Two sources, cross-referenced only at the organismo-name level, not per-tender.** The Boletín pipeline (`collector.py` → `data_model.py`) identifies a tender by an expediente number embedded in free text (`proceso_id()`, e.g. `14/IVC/26`, keyed by organismo *sigla*). BAC (`bac_catalog_collector.py`) identifies the same kind of tender by a different scheme (e.g. `416-2702-CME26`, keyed by a numeric organismo code). These do not map 1:1, so don't attempt a direct join on process/tender id — it's a known, documented gap. What *does* work is matching organismo **display names** after normalization (both sources use human-readable names like "Ministerio de Cultura" / "MINISTERIO DE CULTURA") — see `normalizeOrgName`/`bacOrgIndex` in `app.js`, the only cross-source join that currently exists, rendered as a badge in the org ranking.

**`data_model.py` owns every Boletín business rule** — `merge_norm` (anti-regression field merge that never lets a partial re-fetch null out a previously known value, plus `clean_organismo` name normalization), `isproc`/`category` (contratación detection + rubric classification), `proceso_id` (act-to-process grouping, e.g. collapsing a llamado + its circulares + prórroga). `collector.py` and `backfill_boletin.py` both import these rather than reimplementing them — if you change classification/merge logic, change it here once, not per caller. Same principle in `bac_catalog_collector.py`: `classify_technology` is the one place technology/cybersecurity keyword rules live (deliberately requires a qualified phrase like "redes de datos", not bare "redes", after a real false-positive on "redes eléctricas" made it into a merged commit).

**`bac_catalog_collector.py` targets `bac_anual.csv`, not the more obvious `bac_anual.json`.** Same CKAN package, same nominal dataset — but the JSON resource is stale (verified against the CDN: it only ever contains Jan-Jun 2022 data despite being periodically re-uploaded with a fresh `Last-Modified`), while the flattened-OCDS CSV has real current data. `find_resource()` matches on the resource **URL**, not the CKAN-displayed `name` field (which is generic/misleading for both formats — don't "simplify" that match back to using `name`).

**Frontend does all aggregation client-side from raw JSON, no server-side pagination.** `app.js` fetches `data/*.json` whole (`cache:'no-store'`, no caching) and does filtering, `proceso_id` grouping, and the BAC organismo cross-reference in the browser (`filteredNorms`/`filteredProcurements`/`groupByProceso`/`renderOrgRanking`). Lists paginate client-side via `normsPage`/`procPage` counters ("Cargar más" buttons), not real pagination — the whole filtered set is already in memory. There's no framework and no build step: edit `app.js` directly and reload.

**Audit signal thresholds are arbitrary and documented as such, not derived**: `CONCENTRATION_MIN_AMOUNT` / `CONCENTRATION_HIGH_PCT` in `bac_catalog_collector.py` exist to reduce noise (don't flag a single small purchase as "100% vendor concentration"), not because they're statistically justified. Don't present anything computed from `audit_signals` as proof of irregularity in UI copy — it's a review trigger, and the existing copy is careful about that distinction.
