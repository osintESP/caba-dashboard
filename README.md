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

`runtime/bac_catalog_collector.py` consume por separado el catálogo oficial de **Buenos Aires Compras (BAC)** — un dataset OCDS real (`data.buenosaires.gob.ar/dataset/buenos-aires-compras`, resource `bac_anual.csv`, OCDS aplanado) con adjudicaciones, montos y proveedores. Corre en su propio workflow diario (`refresh-bac-data.yml`), no en el de 30 min, porque el archivo pesa ~55MB y la fuente se actualiza con una cadencia de días/semanas, no minutos. Genera `data/bac_catalog.json` con un ranking de proveedores por monto adjudicado y un bloque `audit_signals` pensado para auditoría de compras de tecnología (ver más abajo).

**Por qué CSV y no el JSON homónimo**: el mismo `package_show` también expone `bac_anual.json` (mismo dataset, formato OCDS release package sin aplanar). Se descartó porque, verificado contra el CDN real, **ese JSON sólo contiene datos de enero-junio 2022** — se re-sube periódicamente (mismo `Last-Modified` que el CSV) pero su contenido nunca avanza. El CSV sí tiene datos reales con ~2-3 meses de atraso respecto a la fecha actual. Limitación del CSV a cambio: al ser OCDS *aplanado*, sólo trae el índice `0` de cada array (`awards/0/...`, `parties/0/...`) — si un award tuviera más de un proveedor (co-adjudicación), sólo se ve el primero.

## Datos (`data/`)

| Archivo | Contenido | Clave |
|---|---|---|
| `editions.json` | Historial de ediciones del Boletín Oficial recolectadas | `numero_boletin` |
| `norms.json` | Todas las normas vistas hasta el momento (acumulativo) | `id_norma` |
| `procurements.json` | Subconjunto de `norms.json` detectado como contratación | `id_norma`, agrupable por `proceso_id` |
| `procurement_intelligence.json` | `procurements.json` enriquecido con tags temáticos y vendors mencionados | — |
| `stats.json` | Métricas agregadas que consume el panel principal del dashboard | — |
| `sync_manifest.json` | Metadata de la última sincronización pública (para el badge de estado) | — |
| `bac_catalog.json` | Adjudicaciones reales de Buenos Aires Compras (OCDS): ranking de proveedores por monto y señales de auditoría en tecnología | — |
| `bac_sync_state.json` | ETag/Last-Modified del recurso BAC en la última corrida, para no re-descargar ~55MB si no cambió | — |

**Auditoría de compras de tecnología (`bac_catalog.json.audit_signals`):** el objetivo del dashboard es facilitar auditoría, no sólo mostrar métricas. BAC no publica `numberOfTenderers`/`tenderers` (confirmado: 0 ocurrencias en todo el dataset), así que la única señal de competencia real que expone el propio publicador es el booleano `tender.competitive`. Sobre eso se calculan tres señales, todas acotadas a contrataciones clasificadas como tecnología:
- `direct_or_limited_share_pct`: % del monto adjudicado por contratación directa/limitada en vez de licitación pública.
- `non_competitive_open_tenders`: licitaciones formalmente públicas (`procurementMethod=open`) que BAC marca como sin competencia real (`competitive=false`) — en la corrida de referencia esto fue el **54% del monto adjudicado en tecnología**, la señal más fuerte encontrada hasta ahora.
- `vendor_concentration_by_organismo`: organismos donde un solo proveedor se lleva ≥60% de su gasto en tecnología (con un piso de $1M para no marcar compras únicas chicas como "concentración").

Ninguna de estas señales prueba irregularidad por sí sola — son disparadores para que un auditor priorice qué expediente revisar primero, no un veredicto.

**Actos vs. procesos:** una misma licitación puede generar varios actos publicados (llamado, circulares, prórroga), cada uno con su propio `id_norma`. `stats.procurement_acts` cuenta actos individuales; `stats.procurements` cuenta **procesos únicos** (agrupados por `proceso_id`, extraído del número de expediente citado en el nombre de la norma, ej. `14/IVC/26`). El dashboard y las métricas de categoría usan el conteo por proceso para no inflar el número de contrataciones reales.

## Automatización (GitHub Actions)

- **`refresh-official-data.yml`**: corre cada 30 min en horario hábil (11:00–16:30 UTC / 08:00–13:30 ART, lunes a viernes) más una corrida extra a las 12:55 ART. Ejecuta el pipeline del Boletín, valida que los JSON de salida sean válidos, commitea los cambios con el bot `caba-dashboard[bot]` y dispara el deploy a Pages. También se puede disparar manualmente (`workflow_dispatch`) o al tocar algo en `runtime/`.
- **`refresh-bac-data.yml`**: corre 1 vez por día a las 06:00 UTC (03:00 ART, fuera de la ventana del refresh de 30 min). Descarga y parsea `bac_anual.csv` sólo si cambió (chequeo condicional por ETag/Last-Modified), y hace commit+deploy igual que el workflow del Boletín. Ambos workflows comparten el mismo `concurrency.group` (`caba-dashboard-refresh`) para que nunca corran en paralelo y se pisen el push a `master`; además, los dos hacen `git fetch && git rebase` antes de pushear como defensa adicional (en `refresh-bac-data.yml` un conflicto de rebase se resuelve automáticamente a favor de la corrida actual, porque `bac_catalog.json` siempre es una regeneración completa, no un merge incremental).
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
python runtime/bac_catalog_collector.py     # descarga y parsea BAC (~55MB), genera data/bac_catalog.json
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

Cubren las funciones puras del pipeline (`isproc`, `category`, `proceso_id`, `merge_norm`, `belongs`, y todo `bac_catalog_collector.py`: clasificación tecnología, resolución de proveedores, señales de auditoría, detección de cambios) — son el código con más riesgo de regresión porque dependen de heurísticas de texto sobre APIs externas no documentadas oficialmente.

## Limitaciones conocidas / roadmap

- Todo el estado vive en JSON commiteados a git (patrón "git como base de datos"); funciona para el volumen actual pero es el primer punto a rediseñar si esto se migra a un runtime propio (ej. Cloud Run Jobs + Cloud SQL/Firestore en GCP).
- La clasificación por categoría/tema es rule-based (regex sobre texto libre) — precisa pero no perfecta; ver `runtime/data_model.py` (`category`) y `runtime/procurement_intelligence.py` (`RULES`) antes de confiar ciegamente en los conteos por rubro.
- Sin tests para `collector.py` más allá de `belongs()` (el resto depende de la forma real de la respuesta de la API oficial, no reproducible sin fixtures grabadas).
- **`bac_anual.csv` tiene ~2-3 meses de atraso respecto a la fecha actual** (no es en tiempo real; la ficha del dataset no garantiza una cadencia fija — "trimestral" según CKAN, "cada 15 días" según documentación histórica de BAC_OCDS). `bac_catalog.json.coverage` expone el rango real de fechas procesado en cada corrida; no asumir que son datos del día sin chequearlo ahí.
- Las señales de auditoría (`audit_signals`) son heurísticas con umbrales documentados pero arbitrarios (`CONCENTRATION_MIN_AMOUNT`, `CONCENTRATION_HIGH_PCT` en `bac_catalog_collector.py`) — son disparadores para revisión manual, no una conclusión de irregularidad.
- El cruce Boletín↔BAC (ej. matchear `proceso_id` del Boletín contra `tender/id`/`ocid` de BAC para detectar licitaciones publicadas que nunca aparecen adjudicadas, o viceversa) todavía no está implementado — es la mejora de mayor valor de auditoría pendiente. No es trivial: el Boletín identifica organismos por sigla (ej. `IVC`) en el número de proceso, BAC usa un código numérico (ej. `416`) — hace falta un mapeo sigla↔código antes de poder cruzar 1:1.
- **Se investigó y se descartó, por ahora, un radar de aperturas en tiempo real** scrapeando `buenosairescompras.gob.ar/ListarAperturaUltimos30Dias.aspx` (el sitio transaccional, no el portal de datos abiertos). Es una página ASP.NET WebForms con sesión/token anti-CSRF; se intentó reproducir con `requests` puro replicando fielmente una request real capturada del navegador (mismo HAR: headers, Referer, Origin, orden de campos, CSRF token) y siguió fallando con un redirect de sesión inválida. Investigación de soluciones de terceros confirmó que **no es viable sin un navegador real**: los únicos scrapers de terceros que funcionan hoy contra esta página usan Selenium/Puppeteer (ej. `ignaciokairuz/Boletin_Oficial_AI`, activo), y otro repo (`odia/buenosairescompras`) documenta en su código el mismo síntoma ("required as browsing directly seems to fail"). Ni siquiera un producto comercial de terceros construido específicamente para BAC (Apify `licitaciones-feed`) logró datos más frescos que el propio CSV oficial. Si se retoma, requiere agregar un navegador headless al pipeline (Chromium en el runner de GitHub Actions), no una extensión del `requests` actual.
