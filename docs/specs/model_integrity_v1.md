# Contrato de integridad de modelos v1

Estado: congelado para implementación en Fase 113.

## Corrección Dixon-Coles

Para `x=home_goals`, `y=away_goals`, intensidades `lambda_home` y
`lambda_away`, y dependencia `rho`, el factor de baja anotación es:

- `tau(0,0) = 1 - lambda_home * lambda_away * rho`;
- `tau(0,1) = 1 + lambda_home * rho`;
- `tau(1,0) = 1 + lambda_away * rho`;
- `tau(1,1) = 1 - rho`;
- `tau(x,y) = 1` en los demás marcadores.

El mismo contrato aplica a fitting, scoring e inferencia. Un factor no finito o
no positivo invalida el candidato.

## Causalidad temporal

La unidad IID es el partido, pero las actualizaciones históricas se realizan
por lotes de `(league_slug, kickoff_ts)`. Todas las predicciones de un lote se
calculan contra el estado anterior al kickoff y sólo después se incorporan sus
resultados. El orden de `match_id` dentro del lote no puede cambiar ninguna
predicción.

Los cortes fit, selección y evaluación se realizan entre kickoffs completos.
Ningún kickoff puede aparecer a ambos lados de una frontera. La historia de una
predicción oficial debe cumplir estrictamente `match_date < cutoff_ts`.

## Validez numérica y artefactos

- lambdas y conteos esperados: finitos y no negativos, o positivos cuando el
  modelo lo requiera;
- probabilidades: finitas, dentro de `[0,1]` y normalizadas por contrato;
- PMF: finita, no negativa y con masa uno dentro de tolerancia documentada;
- métricas: el log-score de goles usa la probabilidad del marcador observado;
- optimización no convergente: candidato no servible y fallback explícito;
- artefactos: todos los archivos declarados en `hashes.json` se verifican antes
  de deserializar modelos;
- promoción: `match_id` único, mercados exactos, targets binarios, réplicas
  válidas y probabilidades finitas.

## Evidencia económica

Una cuota derivada como inversa de la probabilidad del propio modelo no es una
cuota de mercado y no permite estimar ROI, edge, Kelly ni rentabilidad. Esas
afirmaciones requieren cuotas observadas, con fuente y timestamp comparables.
