# Curador CABA — Histórico editorial

Este directorio contiene la capa persistente del **Curador de Noticias — CABA**. Su objetivo es conservar hechos, temas, seguimientos, cierres mensuales y resúmenes anuales de manera auditable y reutilizable.

## Principios

- Un **topic** representa un tema de largo plazo.
- Un **event** representa un acontecimiento concreto dentro de un topic.
- Un **daily record** conserva qué detectó el Curador en una fecha determinada.
- Las actualizaciones nunca sobrescriben la historia: se agregan como entradas en `updates`.
- No se crea historia retroactiva sin evidencia verificable.
- Los estados editoriales (`NUEVO`, `SEGUIMIENTO`, `ACTUALIZACIÓN`, `OFICIAL`) son distintos de los estados de verificación.
- La clasificación de continuidad debe considerar entidades, objeto, acción, ubicación y ventana temporal; no solo similitud textual.

## Estructura

```text
curador-history/
├── README.md
├── config.json
├── state.json
├── schema/
│   ├── topic.schema.json
│   ├── event.schema.json
│   └── daily.schema.json
└── data/
    ├── topics.json
    ├── events.json
    ├── daily/index.json
    ├── monthly/index.json
    └── annual/index.json
```

Los archivos diarios se crean como `data/daily/YYYY-MM-DD.json`. Los cierres mensuales se guardan como `data/monthly/YYYY-MM.json` y los anuales como `data/annual/YYYY.json`.

## Flujo operativo

1. Leer `config.json`, `state.json`, `topics.json` y `events.json`.
2. Ejecutar el Curador diario.
3. Determinar si cada hecho es nuevo o una actualización.
4. Crear/actualizar topics y events.
5. Guardar el registro diario.
6. Actualizar índices y `state.json`.
7. En el primer día hábil de un nuevo mes, si falta el cierre del mes anterior, generarlo a partir del histórico verificable disponible.
8. En el primer día hábil de un nuevo año, si falta el cierre anual anterior, generarlo a partir de los cierres mensuales y eventos persistidos.

## Integridad

La persistencia histórica es independiente del dashboard público y no debe alterar `data/` ni la lógica de publicación del dashboard existente.
