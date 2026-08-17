# Especificación `model_composition v1`

Estado: propuesto en `DEC-196`.

Contrato de **composición** entre modelos. `model_integrity v1` gobierna si una fórmula
aislada es correcta; esta especificación gobierna **dónde va cada pieza en la cadena** y
qué no puede encadenarse con qué. La distinción no es retórica: la auditoría de 2026-08-16
encontró dos piezas individualmente correctas que no pueden serlo a la vez encadenadas
(ver `Conservación de masa` más abajo).

Cada regla está verificada contra el corpus `rag-matematicas` con libro y página. Una
revisión que aplique una regla debe citarla; una que la viole debe justificar por qué.

## Reglas

### R1 — Predicción antes de actualización

En cada ciclo de un filtro de Kalman, el paso de predicción -que suma la covarianza de
ruido de proceso `Q` al estado- se ejecuta **antes** del paso de actualización de
observación. Fuente: Murphy, *Probabilistic Machine Learning*, p.399-407, ec. 8.22-8.24.

Un filtro sin este paso no rastrea un estado variable en el tiempo: con `F=I` y `Q=0` la
covarianza sólo puede decrecer, la ganancia decae con el número de observaciones y la
recursión converge a la estimación de un parámetro estático. Una pieza en ese estado no
puede documentarse como "estado temporal" sin declararlo.

### R2 — Los pesos de mezcla se aprenden fuera del bloque de ajuste

Los pesos que combinan dos o más modelos base se estiman sobre datos **distintos** de los
usados para ajustar esos modelos. En el mismo bloque, los pesos sobreajustan y colapsan
sobre el modelo con mejor ajuste dentro de muestra. Fuente: Murphy, *PML*, p.639, §18.2.2.

La misma sección distingue esto de `Bayes model averaging`, donde los pesos suman uno y
colapsan al modelo MAP con datos suficientes. Un peso fijado a mano una sola vez no es
ninguno de los dos y debe declararse como constante congelada, no como mezcla aprendida.

### R3 — Exposición como offset, no como divisor

Para modelar una tasa por unidad de exposición en un GLM de conteo, la exposición entra
como `log(exposición)` en el predictor lineal con coeficiente fijado en uno. Fuente:
McCullagh & Nelder, *Generalized Linear Models*, p.438.

Dividir el conteo por su exposición y tratar el cociente como respuesta continua destruye
la estructura de varianza del conteo, que es exactamente lo que la familia Poisson/binomial
negativa modela.

### R4 — Con GLM correcto no se pre-transforma la respuesta

Cuando la función de enlace y la función de varianza corresponden a la distribución de la
respuesta, no se aplica transformación estabilizadora de varianza previa: el GLM ya modela
explícitamente la relación media-varianza. Fuente: Murphy *PML* p.445; McCullagh & Nelder
p.38.

Una proporción se modela con enlace logit y varianza binomial, no transformándola con
arcoseno antes de una regresión lineal. Las transformaciones estabilizadoras son la
solución previa a la existencia de los GLM.

### R5 — La contracción se deriva de la muestra, no se fija a mano

En un modelo jerárquico, la media posterior de un grupo es `w_j·μ + (1-w_j)·θ̂_j` con
`w_j = σ_j²/(σ_j²+τ²)`: la contracción hacia la media global crece cuanto menor es la
precisión del grupo. Fuente: Murphy *PML* p.146, ec. 3.256-3.257.

Un `shrinkage` constante aplica la misma contracción a un grupo con 40 observaciones y a
uno con 4,000. Es admisible como decisión congelada, pero debe declararse como tal y no
como estimación jerárquica.

### R6 — La recalibración va al final y no altera el argmax

La recalibración posterior -Platt, escalado de temperatura, binning- se aplica **después**
del modelo base, sobre probabilidades ya formadas, con datos de validación separados de los
de ajuste, y no cambia cuál resultado es el más probable. Fuente: Murphy *PML* p.614-615,
§14.2.2.5.

De aquí se sigue una consecuencia que es fácil violar: **la corrección `τ` de Dixon-Coles
no es una recalibración**. Corrige la forma de la distribución conjunta en marcadores
bajos, es parte del modelo generativo y se aplica sobre la matriz de marcadores, nunca
sobre un resumen agregado como 1X2 ya derivado de ella.

### R7 — La unidad IID es el partido completo

El bootstrap no paramétrico exige unidades independientes e idénticamente distribuidas.
Fuente: van der Vaart, *Asymptotic Statistics*, p.345. En DIKAMAHA la unidad es el partido
completo, nunca la ventana, la línea ni el equipo-partido. Coincide con `DEC-006` y con
`evaluation_protocol v1`; se repite aquí porque es una regla de composición: determina qué
puede agregarse antes de remuestrear.

### R8 — El filtrado de ruido precede al uso

Si una matriz de correlación estimada se filtra para separar estructura de ruido de
muestreo, el filtrado se aplica **antes** de derivar cualquier probabilidad con ella.
Limpiar una matriz ya usada no corrige retroactivamente las probabilidades emitidas.

Referencia del criterio de separación: ley de Marchenko-Pastur, *Oxford Handbook of Random
Matrix Theory*, p.968/973/977. La regla aplica con cualquier criterio de filtrado; la
referencia fija uno verificado.

## Capas

Cada frontera entre capas corresponde a una regla. El orden no es convención: es la
condición para que la regla se cumpla.

| Capa | Contenido | Regla en la frontera de salida |
| --- | --- | --- |
| 0. Ingesta causal | Clasificación de campos y auditoría de cobertura por liga | contrato pre-match de `DEC-001` |
| 1. Estado latente | Dixon-Coles, Kalman | R1 dentro; R2 al salir |
| 2. Generativo | Matriz Poisson + `τ`, conteos NB2 + covarianza | R3, R4, R8 dentro |
| 3. Agrupamiento | Contracción por liga, árbitro, portero | R5 |
| 4. Calibración | Recalibración posterior por mercado | R6, siempre última |
| 5. Evaluación | Proper scoring rules, bootstrap, comparadores | R7 |

Un campo nuevo de la capa 0 no alimenta ninguna capa superior antes de pasar la auditoría
de cobertura por liga. Es la misma comprobación que destapó los ceros de córners: un campo
con huecos sistemáticos por competición contamina el ajuste de las ligas sanas, no sólo el
de las afectadas.

## Conservación de masa

Cuando una intensidad se redistribuye entre sub-ventanas conservando su masa total
-`DEC-092`-, ninguna etapa posterior puede añadirle un término no negativo sin romper esa
conservación. Es álgebra, no una cuestión de calidad de implementación.

Esto afecta a la composición Markov → Hawkes: `hawkes_v1.predict_snapshot` calcula
`lambda_hawkes = lambda_markov + excitación` con `excitación ≥ 0`, que es la definición
correcta de un proceso autoexcitado. Hawkes está fuera del router, así que hoy no hay
contradicción activa. Cualquier reconexión debe resolverla antes, y las dos salidas
posibles no son equivalentes:

- **renormalizar tras la excitación**, conservando las proporciones de Markov y aceptando
  que la masa total crece — sustituye la invariante de `DEC-092` por otra;
- **usar un proceso autoexcitado compensado**, donde la excitación se descuenta de una
  reserva — es un estimador distinto del implementado.

Ver `DEC-198`.

## Gate

Una revisión de composición aprueba cuando:

1. cada pieza nueva declara en qué capa entra;
2. cada frontera cruzada cita la regla que la gobierna;
3. ninguna violación queda sin justificación explícita en `decision_log.md`;
4. las invariantes de conservación declaradas por decisiones congeladas siguen cumpliéndose
   aguas abajo de cualquier etapa añadida.

## Verificación externa

Las ocho reglas están respaldadas por el corpus `rag-matematicas`. La skill
`dikamaha-math-supervision` conserva las citas completas, el inventario de afirmaciones ya
verificadas y la lista de resultados clásicos que el corpus no indexa -donde `UNSUPPORTED`
significa "no indexado" y no "incorrecto"-.
