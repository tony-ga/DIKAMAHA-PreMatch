# Fase 70 — candidato state_labeling v2

Se probó una taxonomía enriquecida con goles propios/concedidos, tiros, tiros a
puerta, corners, presión y disciplina. Los umbrales de amenaza y exposición se
aprendieron sólo con 5,880 partidos de desarrollo.

Resultado sobre 9,801 partidos:

- distribución v1: equilibrio `75,375`, presión `22,310`, repliegue `1,446`,
  desorganización `18,481`;
- distribución v2: equilibrio `43,646`, presión `27,346`, repliegue `8,587`,
  desorganización `38,033`;
- spread de goles en la siguiente ventana v1: `0.132934`;
- spread v2: `0.085693`.

La versión enriquecida produce más soporte para estados raros, pero separa peor
el riesgo de gol siguiente. No reemplaza `state_labeling_v1` y no se conecta al
router.

Artefactos: `artifacts/phase_70_state_labeling_v2_candidate_v1/`.

