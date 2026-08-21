# Curador CABA Intelligence

Observatorio ejecutivo del Boletín Oficial de la Ciudad Autónoma de Buenos Aires: normas, contrataciones públicas y un panel de "inteligencia" tecnológica/ciberseguridad derivado de esas contrataciones.

Es un dashboard **100% estático** (sin backend propio): un pipeline en Python recolecta y clasifica los datos, los publica como JSON dentro del mismo repo, y un frontend en HTML/JS los consume vía `fetch`. Todo el ciclo — scraping, clasificación, commit y deploy — corre solo, en GitHub Actions.

Dashboard en vivo: publicado vía GitHub Pages a partir de la rama `master` (ver `.github/workflows/pages.yml`).

## Arquitectura

```
Boletín Oficial CABA (API pública)
        │
        ▼
runtime/collector.py            → runtime/output/latest.json (no versionado)
        │  scrapea el boletín del día, dedup por id_norma
        ▼
runtime/data_model.py           → data/editions.json, data/norms.json,
        │  merge incremental contra el histórico,          data/procurements.json,
        │  detecta contrataciones y las clasifica            data/stats.json
        ▼
runtime/procurement_intelligence.py → data/procurement_intelligence.json
        │  etiqueta por tema (ciberseguridad, cloud, etc.) y vendors mencionados
        ▼
index.html + app.js + styles.css
        fetch() de los JSON de data/, todo el filtrado/render es client-side
```

`runtime/bac_catalog_collector.py` existe en el repo pero **no está activo** en ningún workflow (fue removido, ver historial de commits "Remove BAC..."); queda como base para retomar el enriquecimiento contra el catálogo oficial de Buenos Aires Compras si se decide reactivarlo.

## Datos (`data/`)

| Archivo | Contenido | Clave |
|---|---|---|
| `editions.json` | Historial de ediciones del Boletín Oficial recolectadas | `numero_boletin` |
| `norms.json` | Todas las normas vistas hasta el momento (acumulativo) | `id_norma` |
| `procurements.json` | Subconjunto de `norms.json` detectado como contratación | `id_norma`, agrupable por `proceso_id` |
| `procurement_intelligence.json` | `procurements.json` enriquecido con tags temáticos y vendors mencionados | — |
| `stats.json` | Métricas agregadas que consume el panel principal del dashboard | — |
| `sync_manifest.json` | Metadata de la última sincronización pública (para el badge de estado) | — |

**Actos vs. procesos:** una misma licitación puede generar varios actos publicados (llamado, circulares, prórroga), cada uno con su propio `id_norma`. `stats.procurement_acts` cuenta actos individuales; `stats.procurements` cuenta **procesos únicos** (agrupados por `proceso_id`, extraído del número de expediente citado en el nombre de la norma, ej. `14/IVC/26`). El dashboard y las métricas de categoría usan el conteo por proceso para no inflar el número de contrataciones reales.

## Automatización (GitHub Actions)

- **`refresh-official-data.yml`**: corre cada 30 min en horario hábil (11:00–16:30 UTC / 08:00–13:30 ART, lunes a viernes) más una corrida extra a las 12:55 ART. Ejecuta el pipeline completo, valida que los JSON de salida sean válidos, commitea los cambios con el bot `caba-dashboard[bot]` y dispara el deploy a Pages. También se puede disparar manualmente (`workflow_dispatch`) o al tocar algo en `runtime/`.
- **`pages.yml`**: deploya el contenido del repo a GitHub Pages en cada push a `master`.

## Desarrollo local

Requisitos: Python 3.12+, sin dependencias de frontend (no hay build step).

```bash
# pipeline de datos
python -m venv .venv && source .venv/bin/activate
pip install -r runtime/requirements.txt

python runtime/collector.py               # pega contra la API oficial, genera runtime/output/latest.json
python runtime/data_model.py              # actualiza data/editions.json, norms.json, procurements.json, stats.json
python runtime/procurement_intelligence.py  # genera data/procurement_intelligence.json
```

```bash
# frontend — cualquier servidor estático sirve, por ejemplo:
python -m http.server 8000
# abrir http://localhost:8000
```

Variable de entorno soportada por el collector: `CABA_BO_BASE` (default `http://api-restboletinoficial.buenosaires.gob.ar`), útil para apuntar a un mock/entorno alternativo en tests manuales.

## Tests

```bash
python -m unittest discover -s runtime/tests -v
```

Cubren las funciones puras del pipeline (`isproc`, `category`, `proceso_id`, `merge_norm`, `belongs`) — son el código con más riesgo de regresión porque dependen de heurísticas de texto sobre una API externa no documentada oficialmente.

## Limitaciones conocidas / roadmap

- Todo el estado vive en JSON commiteados a git (patrón "git como base de datos"); funciona para el volumen actual pero es el primer punto a rediseñar si esto se migra a un runtime propio (ej. Cloud Run Jobs + Cloud SQL/Firestore en GCP).
- La clasificación por categoría/tema es rule-based (regex sobre texto libre) — precisa pero no perfecta; ver `runtime/data_model.py` (`category`) y `runtime/procurement_intelligence.py` (`RULES`) antes de confiar ciegamente en los conteos por rubro.
- Sin tests para `collector.py` más allá de `belongs()` (el resto depende de la forma real de la respuesta de la API oficial, no reproducible sin fixtures grabadas).
