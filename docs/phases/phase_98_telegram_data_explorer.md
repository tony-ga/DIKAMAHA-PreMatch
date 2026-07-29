# Fase 98 — explorador de datos para Telegram

## Objetivo

Convertir Telegram en una interfaz navegable de baja fricción para predicción
pre-match, play-by-play, estadísticas de partido y perfiles de jugadores,
manteniendo la API DIKAMAHA como única puerta de datos e inferencia.

## Alcance

- mercados agrupados en `1T`, `2T` y `Total`;
- entrada de próximos partidos con tres rutas: todas las ligas, por liga y
  por fecha futura disponible;
- navegación liga→fecha→partido para plays y estadísticas;
- navegación liga→equipo→jugador para perfiles;
- búsqueda de equipos por texto o prefijo después de enviar el término;
- botones paginados y mensajes compactos;
- sistema visual uniforme con encabezados, tarjetas y tablas monoespaciadas;
- nombres del fixture en toda columna o línea orientada por equipo;
- ESPN raw-first mediante el caché versionado del conector existente;
- sin odds, stakes, ROI, Kelly ni promoción de mercados.

## Contratos

- `GET /v1/explorer/leagues`;
- `GET /v1/explorer/dates`;
- `GET /v1/explorer/fixtures`;
- `GET /v1/explorer/match/plays`;
- `GET /v1/explorer/match/statistics`;
- `GET /v1/explorer/teams`;
- `GET /v1/explorer/team/roster`;
- `GET /v1/explorer/player`.
- `GET /v1/upcoming` con filtros opcionales `leagues` y `date`;

## Gate

- todos los endpoints son read-only y sanitizados;
- el catálogo general consulta las 18 ligas en paralelo, tolera fallos
  parciales por liga y ordena globalmente por kickoff;
- cada play conserva periodo, reloj, tipo, equipo y texto;
- estadísticas de eventos reconcilian `1T + 2T = total`;
- boxscore total se identifica aparte de los conteos por periodo;
- equipos y jugadores se resuelven sólo con identidad ESPN;
- callbacks Telegram no contienen secretos ni payloads extensos;
- cada mensaje permanece debajo del límite conservador;
- predicciones, mercados, estadísticas, perfiles y estado usan tablas
  comparables en lugar de párrafos planos;
- pruebas unitarias, HTTP y smoke real aprobados;
- router y campos oficiales de predicción permanecen intactos.

## Clasificación inicial

`promising_unconfirmed` hasta completar implementación, artefactos y smoke.
