# Plan maestro — Markov pre-match v4

## Estado del documento

- Fecha: 2026-07-27.
- Decisión asociada: `DEC-077`, congelada para ejecución.
- Punto de partida: Fase 71 cerrada sin promoción; `alpha=0`.
- Requisito no negociable: toda inferencia Markov ocurre antes del kickoff.
- Alcance: reconstruir Markov; no optimizar ROI ni activar Hawkes.

## Diagnóstico que guía el plan

El problema no se resuelve ajustando otra vez el pooling o el peso de fusión.
Las variantes existentes fallaron por cuatro causas acumulativas:

1. los estados manuales mezclan ritmo, control y peligro en una sola etiqueta;
2. las ventanas de 15 minutos esconden persistencia y cambios de régimen;
3. el contexto de equipo entra débilmente en `state_0` y en las transiciones;
4. `first_half_goal` elimina dirección y deja al baseline capturar casi toda
   la señal de volumen de gol.

La API ESPN sí permite ampliar el contexto pre-match: calendario, plantillas,
lesiones/sanciones, alineaciones/formaciones, tabla, historial, atletas,
árbitro, sede y cuotas. No obstante, que un endpoint contenga hoy un dato no
prueba que estuviera publicado antes de un kickoff histórico. Por ello, el
plan separa datos reconstruibles de datos que exigen snapshot prospectivo.

## Resultado objetivo

`markov_pre_match v4` será un modelo Markov latente y direccional con duración:

```text
historial causal
  -> perfiles secuenciales de equipos
  -> contexto congelado antes del kickoff
  -> distribución de estado inicial
  -> transiciones y duración condicionadas
  -> simulación conjunta local/visitante
  -> probabilidades por intervalos de 15 minutos
```

Dixon-Coles y Kalman fijan la capacidad estructural pre-match. Markov modela
cómo esa capacidad se distribuye entre equipo, tiempo y régimen. Los estados se
aprenden sobre microventanas de 5 minutos y se agregan a intervalos de 15
minutos para los mercados. Las granularidades de 10 y 15 minutos se conservan
como sensibilidad de desarrollo, nunca como búsqueda sobre el holdout.

## Contrato causal de datos

### Permitidos para entrenamiento retrospectivo

- resultados y eventos de partidos completamente anteriores;
- calendario, descanso, congestión y viaje derivables antes del kickoff;
- tabla y forma reconstruidas sólo con resultados anteriores;
- estadísticas de jugador/equipo acumuladas hasta el cutoff;
- sede histórica y características inmutables con procedencia demostrable;
- Dixon-Coles/Kalman calculados walk-forward.

### Permitidos sólo con snapshot timestamped

- lesiones y suspensiones;
- plantilla disponible y convocados;
- alineación o formación proyectada/confirmada;
- árbitro asignado;
- cuotas `open`;
- cualquier campo cuya fecha de publicación no pueda reconstruirse.

Se capturarán, cuando existan, cortes `T-168h`, `T-72h`, `T-24h`, `T-6h` y
`T-90m`. Una predicción sólo podrá usar el último snapshot anterior a su
`cutoff_ts`.

### Prohibidos para el partido objetivo

- plays, situación, posesión o probabilidades live;
- marcador o estadísticas del partido;
- cuotas `current`, `close` o live;
- alineaciones recuperadas después del kickoff;
- datos derivados de la ventana que se intenta predecir.

## Contratos nuevos

### `raw_responses`

Toda respuesta se persiste antes de parsearse con:

- proveedor, endpoint, parámetros normalizados y hash de request;
- `fetched_at`, `available_at` cuando la fuente lo publique y `cutoff_ts`;
- liga, temporada, equipo, atleta y evento cuando correspondan;
- código HTTP, hash del payload, versión de parser y JSON crudo;
- indicador `historical_reconstruction` o `prospective_snapshot`.

La tabla existente `raw_api_responses` requiere migración o un adaptador
versionado: hoy está ligada a `match_id` y no puede representar correctamente
respuestas de equipo, atleta, tabla o temporada.

### `prematch_context_snapshot v2`

Una fila por partido y cutoff, con procedencia por campo, máscara de
disponibilidad, antigüedad del dato y cero imputación silenciosa.

### `markov_sequence_features v1`

Perfiles rolling de estilo y persistencia por equipo:

- intensidad direccional de tiros, tiros a puerta, corners y entradas al área;
- disciplina, interrupciones y cambios tras gol o tarjeta;
- duración y recurrencia de regímenes;
- asimetría local/visitante y respuesta a rivales de distinta fuerza;
- incertidumbre y soporte efectivo de cada feature.

## Targets y comparadores

### Targets primarios

La selección se congelará antes de abrir el holdout:

1. gol local por intervalo `0–15`, `16–30`, `31–45+`;
2. gol visitante en los mismos intervalos;
3. primer equipo en marcar y bucket temporal;
4. vector conjunto de resultado goleador de primera mitad.

`first_half_goal` seguirá reportándose, pero será un mercado derivado. Esto
evita evaluar Markov sólo en un target que destruye la dirección de la cadena.

### Comparadores obligatorios

- Dixon-Coles;
- Dixon-Coles + Kalman;
- Dixon-Coles + Kalman + curva temporal por liga/equipo;
- modelo tabular discriminativo con exactamente los mismos datos pre-match;
- cuotas `open`, únicamente donde exista snapshot causal suficiente;
- Markov v1/v2/v3 como diagnóstico, no como referencia de promoción.

Si el modelo tabular con los mismos datos iguala o supera a v4, la información
puede ser útil, pero la estructura Markov no queda justificada.

## Plan por fases

### Fase 72 — contrato causal y expansión ESPN

**Objetivo:** implementar un acceso uniforme a todos los endpoints pre-match
relevantes y persistir primero el payload crudo.

**Entregables:**

- clientes abstractos para fixture, equipo, atleta y competición;
- métodos ESPN para roster, injuries, schedule, standings, athletes, officials,
  venue y odds;
- migración/adaptador de `raw_responses`;
- caché, retry, rate limit, hashes y pruebas de contrato.

**Éxito:**

- 100% de llamadas parseadas tienen una fila cruda previa;
- replay idéntico por hash;
- ninguna credencial o payload sensible en logs;
- cero escritura fuera del esquema autorizado.

### Fase 73 — snapshots pre-match multicutoff

**Objetivo:** capturar disponibilidad real antes del kickoff.

**Entregables:** colector programable, manifiesto por cutoff, máscaras de
cobertura y auditoría de puntualidad.

**Éxito:**

- `fetched_at < kickoff` en 100% de features usadas;
- cobertura y latencia publicadas por endpoint, liga y cutoff;
- campos tardíos se omiten, nunca se rellenan retrospectivamente;
- al menos dos snapshots pre-kickoff por partido prospectivo cuando la fuente
  responda.

### Fase 74 — corpus causal y calidad secuencial

**Objetivo:** reconstruir el corpus sin duplicados, errores de identidad o
ventanas contaminadas.

**Entregables:** microventanas 5/10/15, reconciliación de marcador, catálogo de
equipos/ligas y partición temporal anidada.

**Éxito:**

- cero solapamiento de `match_id` entre ajuste, selección y confirmación;
- cero eventos del objetivo en sus features;
- 100% de marcadores reconciliados en partidos admitidos;
- ≥95% de partidos con secuencia completa en cada liga admitida;
- exclusiones y motivos versionados, sin imputación entre ligas.

### Fase 75 — baseline temporal fuerte y targets direccionales

**Objetivo:** elevar el comparador antes de atribuir valor a Markov.

**Entregables:** baselines analíticos, modelo tabular same-data, definición
formal de targets y scoring multiclase/conjunto.

**Éxito:**

- probabilidades válidas y calibradas;
- reproducción de métricas históricas dentro de tolerancia `1e-6`;
- targets derivados sólo post-match y separados del paquete de inferencia;
- baseline seleccionado únicamente en validación.

### Fase 76 — descubrimiento de estados latentes

**Objetivo:** aprender regímenes con semántica predictiva futura, no etiquetas
contemporáneas hechas a mano.

**Entregables:** modelo Markov latente con duración, emisiones multivariadas,
alineación de etiquetas entre folds y tarjetas semánticas de estado.

**Éxito:**

- entre 4 y 8 estados seleccionados sólo en desarrollo;
- ningún estado ordinario con ocupación global <5%;
- similitud de estados alineados `NMI >= 0.70` entre folds/semillas;
- spread absoluto de riesgo de gol siguiente `>=0.05`;
- orden de riesgo estable en al menos 75% de ligas con soporte;
- likelihood secuencial OOS superior al modelo sin duración.

### Fase 77 — estado inicial pre-match

**Objetivo:** predecir `P(S0 | contexto)` sin observar el partido objetivo.

**Entregables:** modelo jerárquico de estado inicial, niveles de disponibilidad
`core`, `contextual` y `lineup_confirmed`, y calibración.

**Éxito:**

- mejora relativa de log-loss `>=1%` frente al prior liga-ventana;
- Brier y ECE no empeoran;
- ganancia presente en desarrollo y validación temporal;
- el modo `core` produce salida para cualquier fixture con historia mínima.

### Fase 78 — transición, duración y respuesta al contexto

**Objetivo:** estimar transiciones específicas sin caer en uniform/global.

**Entregables:** transiciones jerárquicas condicionadas por matchup, ventana,
localía y marcador simulado; distribución de duración y backoff explícito.

**Éxito:**

- mejora relativa de log-loss de transición `>=1%` frente a liga-ventana;
- al menos 50% de masa predictiva proviene de niveles más específicos que
  global/uniform en ligas admitidas;
- calibración de duración dentro de 10% de error relativo;
- estabilidad temporal y ausencia de degradación material por liga.

### Fase 79 — simulador pre-match coherente

**Objetivo:** producir trayectorias y mercados sin consumir información live.

**Entregables:** simulación reproducible, conservación de intensidad
Dixon-Coles/Kalman, agregación 5→15 minutos y provenance por predicción.

**Éxito:**

- misma semilla produce hashes idénticos;
- error de conservación de masa `<1e-6`;
- probabilidades suman uno dentro de `1e-9`;
- cero lecturas del objetivo posteriores al cutoff;
- salida Markov siempre disponible en modo `core`, sin copiar el baseline.

### Fase 80 — ablación y walk-forward anidado

**Objetivo:** demostrar qué componente aporta valor y evitar selección por
azar.

**Entregables:** ablaciones de contexto, duración, dirección y granularidad;
bootstrap por partido; corrección Holm por múltiples targets.

**Gate contundente:**

- mejora de log-loss `>=0.005` absoluta o `>=1%` relativa, la mayor de ambas,
  frente al mejor comparador;
- IC bootstrap 95% de la mejora estrictamente por encima de cero;
- mejora de Brier `>=0.002`;
- ECE no empeora más de `0.005`;
- resultado no explicado por una sola liga, temporada o cutoff;
- ≥70% de ligas con soporte tienen delta no negativo;
- ninguna liga con `n>=100` empeora log-loss más de `0.01`;
- Markov supera al modelo tabular same-data en al menos el score conjunto
  direccional o la estructura Markov se rechaza.

### Fase 81 — confirmación prospectiva independiente

**Objetivo:** repetir el resultado en partidos cuyas features fueron congeladas
antes del kickoff y nunca participaron en selección.

**Entregables:** ledger sellado, predicciones firmadas, resultados posteriores
y reporte ciego.

**Éxito:**

- mínimo 500 partidos terminados de al menos 10 ligas;
- mínimo 100 positivos para cada target primario promovido;
- mismo signo de mejora que Fase 80;
- IC bootstrap 95% estrictamente positivo;
- cero cambios de modelo, features o umbrales después de sellar la cohorte.

### Fase 82 — integración oficial

**Objetivo:** servir Markov v4 en el flujo universal pre-match.

**Entregables:** router por mercado, contrato de respuesta, observabilidad,
rollback y shadow previo a activación.

**Éxito:**

- paridad offline/online dentro de `1e-9`;
- p95 de latencia dentro del presupuesto vigente;
- 100% de respuestas incluyen cutoff, versión, cobertura y provenance;
- rollback al baseline probado;
- sólo se activan mercados que aprobaron Fases 80 y 81.

### Fase 83 — valor de mercado

Se reabre `betting_value_validation` sólo después de Fase 82. ROI, Kelly y
drawdown no pueden usarse para rescatar una probabilidad que no superó el gate
estadístico.

## Reglas de decisión

### Promover

Markov entra al router sólo si supera todos los gates de causalidad,
incrementalidad, estabilidad y confirmación prospectiva.

### Revisar

Se permite una única revisión por hipótesis cuando la causalidad es correcta y
el fallo queda localizado en un componente medible. El holdout usado pasa al
catálogo de reutilización y nunca vuelve a ser confirmatorio.

### Detener

La línea Markov se cierra como no justificada si ocurre cualquiera:

- los estados no son estables ni separan riesgo futuro;
- `state_0` o las transiciones no superan sus priors;
- el modelo tabular same-data supera consistentemente a Markov;
- la mejora desaparece por liga o en la cohorte prospectiva;
- el valor sólo aparece después de buscar pesos sobre el holdout.

Cerrar Markov bajo estas condiciones no implica perder los datos nuevos: éstos
pueden enriquecer el baseline, pero no se presentarán como valor de una cadena
Markov.

## Orden de ejecución

```text
72 -> 73
72 -> 74 -> 75 -> 76 -> 77 -> 78 -> 79 -> 80
73 -----------------------------------------> 81 -> 82 -> 83
```

Las Fases 72–80 aprovechan el corpus histórico causal. La Fase 73 empieza desde
el primer día porque la evidencia prospectiva no puede reconstruirse después.

## Primer incremento ejecutable

1. ejecutar el contrato de Fase 72;
2. ampliar el ORM de respuestas crudas y el conector ESPN;
3. iniciar snapshots multicutoff de fixtures próximos;
4. materializar microventanas y baselines direccionales;
5. abrir Fase 76 sólo tras cerrar los gates de datos.
