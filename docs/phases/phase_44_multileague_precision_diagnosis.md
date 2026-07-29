# Fase 44 — diagnóstico de precisión multi-liga

Se identificó que 1X2, Over 2.5 y BTTS se estaban calculando por Monte Carlo
con 300 trayectorias aunque Markov conserva la intensidad total por partido.
Se sustituyeron esos tres mercados por la distribución Poisson analítica de
`lambda_base`.

La corrección recuperó exactamente el baseline estructural en los mercados de
partido completo. Las probabilidades temporales no se modificaron.

Clasificación: `precision_defect_identified_temporal_signal_pending`.
