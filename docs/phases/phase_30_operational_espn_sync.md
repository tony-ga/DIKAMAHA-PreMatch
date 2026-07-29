# Fase 30 — sincronización operativa ESPN

## Objetivo

Mantener una ventana móvil de partidos ESPN en `prospective_staging_v2` para
que la próxima cohorte pueda superar el gate de independencia sin tocar el
router oficial.

## Protecciones

- La fuente es únicamente ESPN y el proveedor queda registrado.
- La escritura requiere `--write-staging` y sólo utiliza `prospective_staging_v2`.
- Los IDs de calibración, confirmación y router se excluyen antes de normalizar.
- No se ejecutan evaluación, calibración, bootstrap, entrenamiento ni promoción.
- Se usa concurrencia 1, backoff del conector y refresco de partidos incompletos.

## Ejecución operativa

Búsqueda adaptativa por cobertura:

```bash
python scripts/run_adaptive_espn_search.py --fallback-years 2025,2024 --minimum-candidates 30
```

El buscador consulta primero la ventana reciente y amplía a temporadas
completas si no alcanza el mínimo de candidatos fuente. Con `--write-staging`
sólo persiste registros permitidos en `prospective_staging_v2`; los partidos
históricos o reutilizados se conservan como diagnóstico y se excluyen.

Dry-run:

```bash
python scripts/run_operational_espn_sync.py
```

Persistencia explícita en staging:

```bash
python scripts/run_operational_espn_sync.py --write-staging
```

La ventana predeterminada cubre los últimos 7 días UTC. Para una recuperación
controlada puede indicarse `--start-date YYYYMMDD --end-date YYYYMMDD`.

## Resultado de activación

El conector quedó conectado al ejecutor operativo y se probó sobre un rango
sin partidos nuevos. La ejecución no modificó el router ni produjo evaluación;
los 245 partidos históricos detectados por ESPN permanecen fuera de la nueva
cohorte porque ya pertenecen a calibración o confirmación.

## Siguiente paso

Ejecutar la sincronización diaria con `--write-staging`. Cuando existan
partidos nuevos, el gate de Fase 29 deberá aprobarlos antes de generar
predicciones o calcular métricas.
