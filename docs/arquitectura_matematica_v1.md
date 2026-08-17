# Arquitectura matemática: auditoría, composición y candidatos

**Fecha:** 2026-08-16
**Estado:** auditoría cerrada; propuestas registradas en `DEC-196` a `DEC-199`.

Auditoría externa de los modelos y, sobre todo, de **las conexiones entre ellos**,
contrastada contra el corpus `rag-matematicas` -libros de matemáticas, estadística y
física- con libro y página por afirmación. Complementa la Fase 113
(`docs/specs/model_integrity_v1.md`), que verificó fórmula, causalidad, validez numérica y
artefactos por revisión de código; esta pasada verifica contra fuentes externas y añade
una pregunta que aquella no hacía: si dos piezas correctas pueden encadenarse.

Las reglas de composición resultantes están congeladas en
`docs/specs/model_composition_v1.md`. Este documento es el relato: qué se encontró, qué se
descartó y qué queda como candidato.

## Qué se auditó

Lectura directa de la implementación, no de la documentación de fase:
`dixon_coles_v1.py`, `kalman_v2.py`, `official_goal_chain.py`, `team_count_markets.py`,
`market_calibration.py`, `dual_markov_simulator.py`, `hawkes_v1.py`.

Cada afirmación se formuló **sin mencionar fútbol** -el corpus no lo conoce- y se verificó
de forma atómica. Diez resultados salieron `SUPPORTED` con cita; ninguno
`CONTRADICTED`. Los que salieron `UNSUPPORTED` son resultados clásicos de literatura
especializada que este corpus no indexa -Dixon & Coles 1997, Hawkes 1971, Wilson 1927,
Bradley-Terry-: no son errores, y registrarlos como tales sería una conclusión falsa.

Confirmados sin hallazgos: `combined_dispersion` -incluido el término de covarianza
`2ρσ_Hσ_A`-, la parametrización NB2, el shrinkage Beta-Binomial, el uso conjunto de Brier
y log-loss como *proper scoring rules*, y la unidad IID del bootstrap.

## Hallazgos

### El paso de predicción de Kalman no existe (`DEC-197`)

`KalmanV2Filter._update_batch` implementa correctamente la actualización -ganancia por
pseudo-inversa, forma de Joseph, proyección suma-cero-. No hay en el módulo ningún paso
que sume la covarianza de ruido de proceso `Q` entre observaciones. `KalmanV2Config`
declara `process_noise_attack`, `process_noise_defense` y `process_noise_home_advantage`,
y los tres se usan sólo para validar que son finitos y no negativos.

La consecuencia es medible, no opinable: con `F=I` implícito y `Q=0` la covarianza sólo
puede decrecer, la ganancia decae con el número de partidos y el filtro converge a la
estimación de un parámetro casi estático. Un equipo con 200 partidos en el histórico causal
pondera las últimas jornadas casi igual que las de hace dos temporadas. La pieza no rastrea
un estado temporal, que es como la describen `CLAUDE.md` y los documentos de fase.

`kalman_v1.py` sí sumaba ruido de proceso; la versión activa lo perdió. La Fase 113 no lo
detectó porque su alcance nunca incluyó la ecuación de predicción.

Fijado como propiedad ejecutable en
`tests/test_model_composition_v1.py::test_kalman_v2_covariance_never_grows_between_updates`
y en la prueba de decaimiento de ganancia que la acompaña. Mientras esas pruebas pasen, el
hallazgo sigue vigente; corregirlo las hace fallar, que es el aviso para volver a
`DEC-197`.

### Markov y Hawkes no componen (`DEC-198`)

`DEC-092` congela que Markov redistribuye las lambdas Dixon-Coles/Kalman entre 18 ventanas
**sin alterar su masa**. `hawkes_v1.predict_snapshot` calcula
`lambda_hawkes = lambda_markov + excitación` con `excitación ≥ 0`, que es la definición
correcta de un proceso autoexcitado. Cada pieza es correcta por separado; encadenadas no
pueden serlo, porque sumar un término positivo a una masa conservada la deja de conservar.

Hoy no hay contradicción activa -Hawkes está fuera del router-, pero la incompatibilidad
no es visible leyendo ninguno de los dos módulos: sólo aparece al mirar la composición. Es
el mejor argumento de por qué esta auditoría existía.

### Hallazgo positivo y asimetría observada

`_update_batch` usa la **forma de Joseph** `(I-KH)Σ(I-KH)ᵀ + KRKᵀ` en vez de la forma
simplificada que el propio libro de referencia presenta como canónica. Es más robusta
numéricamente y conviene que quede documentada como decisión deliberada, para que nadie la
"simplifique" pensando que es redundante.

Dixon-Coles usa penalización ridge blanda para la identificabilidad de ataque/defensa;
Kalman usa proyección suma-cero dura sobre el mismo espacio de parámetros. Dos mecanismos
distintos encadenados, no documentados como decisión conjunta.

## Candidatos

Criterio, en este orden: **¿ataca un defecto ya medido?** → **¿su fundamento verifica?** →
**¿cuánto código de producción cambia?** Un modelo más sofisticado que no ataca ningún
defecto medido no es un candidato: el proyecto ya tiene evidencia negativa cara de eso
(familia Markov v4, `DEC-100`).

| Candidato | Ataca | Fuente | Costo |
| --- | --- | --- | --- |
| Escalado de temperatura para 1X2 | `DEC-162`, sobreconfianza medida | Murphy §14.2.2.5 | mínimo — **implementado, `DEC-199`** |
| Contracción jerárquica `w_j = σ_j²/(σ_j²+τ²)` | `shrinkage` fijo igual para toda liga | Murphy p.146 | bajo, generaliza código existente |
| Comparador externo con cuotas | no hay vara de medir externa | col10708 ec. 1.25 | bajo, sólo evaluación |
| Offset `log(exposición)` | supuesto tácito de 90 minutos | McCullagh & Nelder p.438 | medio |
| Ornstein-Uhlenbeck como `Q` | `DEC-197` | Karatzas & Shreve cap. 5 | **alto: cambia producción** |
| Stacking del blend | 0.8/0.2 fijo desde Fase 42 | Murphy p.639 | medio, tras el anterior |
| Matrices aleatorias (Marchenko-Pastur) | ruido en correlaciones | *Oxford Handbook of RMT* | sólo si ρ escala a matriz |
| Máxima entropía para priors | `safe_default` fijado a mano | MacKay cap. 22 | conceptual |
| Proceso semi-markoviano | formaliza "duration-aware" de v4 | Ross cap. 4.8/8.6 | sólo si se reabre v4 |
| Cox para lesiones | `injuries` sin modelo de riesgo | ISLR2 cap. 11 | medio |

Descartados con razón declarada: mecánica cuántica, teoría de campos, Ising y
renormalización -herramientas para sistemas con muchos grados de libertad interactuando en
red, mientras cada partido se predice de forma aislada-; y Poisson bivariada por shock
compartido y cópulas, porque el ajuste de momentos actual ya corrigió el sesgo medido.

## Datos de ESPN aún sin usar

Del reporte de la API pública, campos que hoy no alimentan ningún modelo y tienen una pieza
matemática verificada que los consume:

| Campo | Modelo | Verificación |
| --- | --- | --- |
| `officials` (árbitro/VAR) | contracción jerárquica, dimensión árbitro | Murphy p.146 |
| `injuries` | riesgos proporcionales de Cox | ISLR2 cap. 11 |
| tiempo añadido y paros de `plays` | renovación → exposición como offset | Ross cap. 3; McCullagh p.438 |
| `odds` | `P = O/(1+O)`, sólo comparador de calibración | col10708 ec. 1.25 |
| `probabilities` en vivo | martingala, diagnóstico del feed | Ross cap. 6 |
| paradas de portero | contracción jerárquica, dimensión portero | Murphy p.146 |
| posesión y % de pases | GLM binomial logit, **sin** pre-transformar | Murphy p.445 |
| rachas en `standings` | teoría de récords, `R_n/log n → 1` | Ross; PTE5 |
| formación táctica | minimax de von Neumann, exploratorio | Dasgupta et al. |

Todo campo nuevo pasa antes por la auditoría de cobertura por liga de la capa 0
(`model_composition v1`). Es la misma comprobación que destapó los ceros de córners; clima
y xG son exactamente los casos donde ESPN admite cobertura desigual.

Sin dato fuente: el efecto Magnus explica físicamente la curva de un tiro con efecto, pero
la API no expone ninguna medida de rotación del balón.

## Autocorrecciones

Tres veces el corpus rechazó una afirmación de esta misma auditoría y, al investigar por
qué en vez de reformularla, apareció un error real. Se conservan visibles porque una
auditoría que sólo muestra sus aciertos no es auditable.

1. **Transformación arcoseno para proporciones.** Se recomendó pre-transformar posesión y
   % de pases. La cita era correcta pero la recomendación estaba mal dirigida: DIKAMAHA ya
   trabaja con GLMs, y R4 establece que un GLM con enlace y varianza correctos hace
   innecesaria la transformación previa. Lo correcto es enlace logit directo.
2. **"Correlación espuria" entre posesiones.** Si local y visitante suman 100%,
   `visitante = 100 − local` es una identidad con correlación exactamente −1 por álgebra,
   no un artefacto composicional. La consecuencia real es más simple: usarlas ambas da una
   matriz de diseño de rango deficiente.
3. **Bradley-Terry** salió `UNSUPPORTED` por nombre y se conservó marcado como no
   verificado, en vez de fabricar una cita.

## Qué queda en el repositorio

- `docs/specs/model_composition_v1.md` — ocho reglas y seis capas, con fuente.
- `tests/test_model_composition_v1.py` — los dos hallazgos como propiedades ejecutables.
- `src/temperature_calibration.py` + `tests/test_temperature_calibration.py` — el primer
  candidato de la lista, implementado y sin conectar.
- `DEC-196` a `DEC-199` — las cuatro decisiones, todas en estado `propuesta`.

La skill `dikamaha-math-supervision` conserva el material de referencia: citas completas,
inventario de afirmaciones ya verificadas -para no repetir trabajo-, la lista de
resultados clásicos que el corpus no indexa, y la técnica de formulación que hace que una
verificación sirva.

## Qué no se hizo

Ningún modelo se modificó, ninguna probabilidad servida cambió y ninguna decisión se
congeló. `DEC-197` deja explícitamente la elección abierta entre corregir Kalman -lo que
cambia producción en cuatro servicios y obliga a re-certificar Fase 42- o renombrar la
pieza para que deje de afirmar lo que no hace. Esa elección no es técnica y no
correspondía a la auditoría.
