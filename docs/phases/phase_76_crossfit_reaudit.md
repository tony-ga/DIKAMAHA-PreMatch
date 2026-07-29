# Fase 76R — estados de cola con validación temporal anidada

## Estado

`ready_for_next_phase`

## Cambio semántico

v3 dividía el score continuo en cuartiles uniformes. Esto garantizaba balance,
pero comprimía los extremos predictivos y hacía caer el spread entre dominios.
76R conserva el riesgo causal regularizado y aprende en cada train límites de
cola `10/50/90`, produciendo cuatro estados:

1. riesgo bajo;
2. régimen ordinario bajo;
3. régimen ordinario alto;
4. riesgo alto.

Los límites son parámetros del train, no reglas basadas en outcomes OOS.

## Evaluación anidada

Cada fold externo contiene una selección interna 80/20. El bloque externo no
participa en hiperparámetros. La estabilidad compara dos mitades temporales de
tamaño equivalente.

| Fold externo | Spread | Ocupación mínima | NMI | Ligas estables | Duración |
| --- | ---: | ---: | ---: | ---: | ---: |
| selection OOS | 0.056876 | 10.34% | 0.847527 | 100% | +0.140545 |
| confirmation OOS | 0.056224 | 10.89% | 0.796878 | 96% | +0.145935 |

Ambos bloques superan todos los gates de Fase 76.

## Cobertura

- 9,465 partidos causales;
- 39 ligas admitidas;
- 340,740 filas direccionales de cinco minutos;
- cero eventos futuros en features;
- cero selección con el bloque externo;
- router sin cambios.

## Decisión

`predictive_latent_state_v4_tail_crossfit` reemplazó v3 y sirvió como régimen
base para la posterior representación dual de DEC-090. El lock v3 se conserva
como evidencia negativa, pero
deja de ser ruta de aprobación. Fase 77 queda autorizada para construir
`P(S0 | contexto pre-match)`. Esto no equivale a promoción al router: siguen
siendo obligatorias las Fases 77–81.

## Verificación

- replay completo de 76R reproducido;
- suite con integraciones PostgreSQL: `338 passed`;
- compilación de módulos modificados: correcta.
