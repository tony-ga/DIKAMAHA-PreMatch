# Fase 55 — solicitud universal por nombres

## Resultado

Se verificó la solicitud de un partido próximo usando únicamente liga, fecha y
nombres de los equipos. El resolver consultó el scoreboard ESPN, obtuvo los IDs
canónicos y pasó la identidad al mismo motor pre-match.

- Fixture: `Puebla vs Guadalajara`.
- Liga: `mex.1`.
- Partido ESPN: `401877027`.
- HTTP: `200`.
- Snapshot: `phase54_multileague_post2025_v1_20260727`.
- Cutoff causal: aprobado.
- Datos del partido objetivo utilizados: `False`.
- Persistencia durante la request: `False`.
- Markov promovido: `False`; la salida oficial sigue siendo baseline estructural.

## Interfaz operativa

El usuario puede solicitar una predicción mediante `POST /v1/predict/fixture`
con `league_slug`, `kickoff_date`, `home_team_name` y `away_team_name`.
También puede usar IDs o `match_id` cuando los conozca. El resolver rechaza
fixtures inexistentes, ambiguos, ya iniciados o no programados.

## Artefactos

- `scripts/run_phase_55_universal_named_fixture_flow.py`
- `artifacts/phase_55_universal_named_fixture_flow_v1/final_report.md`
- `artifacts/phase_55_universal_named_fixture_flow_v1/audit.json`

