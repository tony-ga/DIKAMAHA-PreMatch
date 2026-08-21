# Rollback manual — migración 016 (store de parlays, Fase 136)

La migración es **aditiva**: crea cuatro tablas nuevas y tres índices, y no
modifica ni borra nada existente. Reaplicarla no tiene efecto.

## No permitido automáticamente

- `DROP TABLE` de cualquiera de las cuatro tablas.
- Borrado de filas congeladas o liquidadas.

Las filas de `parlay_leg_freezes` y `parlay_freezes` son la evidencia
prospectiva de DEC-222: borrarlas destruye la única muestra fuera de muestra que
puede confirmar o refutar el gate de Fase 135, y no se puede reconstruir después
porque exige haberse materializado antes del kickoff.

## Pasos seguros

1. Detener el runner `scripts/run_phase_136_parlay_prospective.py`.
2. Ejecutar `016_verify_parlay_store.sql` y revisar el resultado.
3. Si hace falta desactivar la fase, basta con no ejecutar el runner: ninguna
   ruta servida lee estas tablas.
4. Un revert estructural sólo procede con confirmación explícita y un script
   dedicado revisado aparte.
