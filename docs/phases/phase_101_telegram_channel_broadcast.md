# Fase 101 — difusión automática Telegram

## Objetivo

Publicar en `@viewtofuture` una agenda diaria seguida inmediatamente por sus
predicciones y un cierre reconciliado post-partido.

## Programación

- zona horaria: `America/Mexico_City`;
- resumen diario: 09:00, para el día calendario siguiente;
- tarjeta individual: inmediatamente después del resumen;
- mercados disponibles: inmediatamente después de cada tarjeta, agrupados por
  primer tiempo, segundo tiempo y partido completo;
- modo `full`: todos los fixtures disponibles del día siguiente;
- modo `lite`: los tres fixtures más próximos por kickoff;
- resultado: no antes de `kickoff + 3h`;
- ciclo operativo recomendado: cada cinco minutos.

## Servicio operativo

La ejecución permanente pertenece al repositorio:

```bash
python scripts/run_phase_101_telegram_channel_service.py
```

El entrypoint carga `.env`, reutiliza una API DIKAMAHA saludable o inicia una
instancia `operational_readonly`, y supervisa el worker continuo. No requiere
cron, tareas programadas ni automatizaciones de Codex. Al cerrarse, termina
únicamente los procesos que él mismo inició.

## Contratos

- toda predicción se persiste antes de publicar;
- el payload congelado conserva fixture, kickoff, probabilidades y hash;
- los contratos distribucionales posteriores usan snapshots append-only por
  fixture y versión, siempre congelados antes del kickoff;
- una clave idempotente única evita reenvíos tras reinicio;
- los mercados oficiales y shadow mantienen sus etiquetas;
- cada línea shadow muestra probabilidad del modelo y baseline, y permanece
  explícitamente `experimental_shadow_not_promoted`;
- los escudos ESPN son sólo presentación y tienen fallback de texto;
- el resultado exige estado final, marcador, orientación y reconciliación
  `1T + 2T = total` y `goles PBP = marcador`;
- una discrepancia queda pendiente y auditada, nunca imputada.

## Gate

- pruebas sin llamadas reales a Telegram;
- replay no genera publicaciones adicionales;
- cambio de predicción posterior no altera la congelada;
- DST y fecha objetivo resueltos con `America/Mexico_City`;
- mensajes bajo el límite conservador de Telegram;
- bot con acceso de publicación al canal;
- router, modelos y política económica intactos.

## Clasificación

`validated`.

La versión 1.0 congeló diez predicciones y confirmó un resumen real sin
duplicados. La versión 1.1 añadió el interruptor `full|lite`, entrega inmediata
de tarjetas, formato visual y escudos con fallback. Cuatro pruebas dirigidas
aprobaron esta revisión sin modificar predicciones ni settlement. La versión
1.2 retiró la automatización Codex y dejó API y publicador bajo un servicio
permanente del proyecto; 13 pruebas dirigidas y un smoke continuo aprobaron.
La versión 1.3 añadió todas las líneas de `user_market_view` debajo de cada
tarjeta. Un smoke real `lite` publicó 27 predicciones de mercado en tres
partidos, sin alterar el router.
La versión 1.4 sustituyó esa presentación fija por los escenarios variables de
Fase 102. Los fixtures congelados con contratos antiguos reciben un snapshot
`phase102_v1` append-only antes del kickoff; la predicción original permanece
intacta. Tres partidos reales publicaron seis recomendaciones variables cada
uno con claves idempotentes versionadas.
La versión 1.5 divide esos mercados en tres tarjetas visuales por fixture:
primer tiempo, segundo tiempo y total. Cada equipo presenta corners, tiros y
tarjetas en tablas monoespaciadas con líneas en columnas y Más, Menos y
referencia baseline en filas. La edición no altera los snapshots ni las PMF.
La versión 1.6 consolida las tres secciones en un dashboard por fixture si su
longitud es menor o igual a 3,900 caracteres. Cada sección conserva el nombre
del partido y su distintivo temporal; si el dashboard supera el límite, el
publicador conserva el fallback de tarjetas individuales autoidentificables.
