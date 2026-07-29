# Fase 20 — reentrenamiento con histórico completo previo a confirmación

## Resultado

Se utilizaron 899 partidos válidos antes del bloque de confirmación: temporadas
2023-24 y 2024-25, el tramo agosto-octubre de 2025 y los 44 partidos de
octubre-noviembre de 2025. La confirmación contiene 241 partidos posteriores.

La regeneración excluyó una copia canónica del partido del 26/10/2025 que
también estaba presente en la cohorte de calibración bajo otro `match_id`.

- `first_half_goal`: Markov `0.626560`, baseline `0.629820`;
- `second_half_goal`: Markov `0.451818`, baseline `0.411345`;
- el IC de mejora de primera mitad cruza cero;
- ningún target obtiene mejora confirmatoria estricta;
- mercados promovidos: `False`.

Conclusión: la corrección de identidad no convierte Markov en ganador
universal. La arquitectura mantiene selección por target y ningún mercado se
promueve.

# Version: 1.0.0
# Created: 2026-07-26
