# Fase 6.3: empaquetado local

El servicio se empaqueta como una imagen Docker local, sin PostgreSQL, Redis,
llamadas externas ni persistencia. Hawkes permanece desactivado y no puede
usarse en una predicción oficial.

## Requisitos

- Docker Engine o Docker Desktop con integración WSL habilitada.
- Acceso local al repositorio.

No se necesita `.env`, credencial ni variable de conexión a base de datos.

## Build y ejecución

```bash
docker build --file Dockerfile --tag dikamaha-local:6.3 .
docker run --rm --publish 127.0.0.1:8000:8000 dikamaha-local:6.3
```

El único puerto expuesto es `8000`, enlazado localmente. El proceso corre como
usuario no root `app`.

## Smoke test

```bash
python scripts/smoke_test_phase_6_3.py
```

El script construye una imagen con tag determinista, arranca el contenedor y
comprueba health, OpenAPI, pre-match, live, rechazo de `704766`, rechazo de
leakage y bloqueo de Hawkes en modo oficial. Si Docker no está disponible,
registra `docker_runtime_unavailable`; no sustituye ese resultado con una
simulación.

## Configuración efectiva

Las variables internas son:

- `HAWKES_ENABLED=false`
- `OFFICIAL_PREDICTION=false`
- `EXTERNAL_CALLS_ENABLED=false`
- `PERSISTENCE_ENABLED=false`

La configuración matemática y las versiones del contrato se mantienen en el
código y artefactos de Fases 6.1 y 6.2. Este paquete no declara producción.

## Reproducibilidad

Las dependencias están fijadas en `requirements.docker.txt`. El Dockerfile usa
`python:3.12.3-slim-bookworm` para `linux/amd64`, fijada al digest OCI
`sha256:fd3817f3a855f6c2ada16ac9468e5ee93e361005bd226fd5a5ee1a504e038c84`.
La reproducibilidad bit a bit todavía requiere ejecutar y comparar el build en
CI.

Version: 1.0.0
