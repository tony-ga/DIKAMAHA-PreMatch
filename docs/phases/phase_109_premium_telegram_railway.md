# Fase 109 — Bot premium Telegram en Railway

## Objetivo

Desplegar el bot privado como servicio Railway independiente del canal, con
acceso premium por allowlist y la misma presentación de predicciones y mercados
que la difusión automática.

## Alcance

- long polling en un solo proceso;
- API DIKAMAHA remota por HTTPS y `X-Dikamaha-Key`;
- allowlist obligatoria mediante `TELEGRAM_ALLOWED_USER_IDS`;
- `/whoami` disponible antes de autorizar;
- próximos por liga y fecha, predicciones, contexto, PBP, estadísticas,
  equipos, búsqueda y perfiles de jugador;
- tarjeta principal y dashboard de mercados compartidos con Fase 101;
- imagen Docker separada sin snapshots, modelos ni base de datos;
- logs sanitizados, retry, rate limit y cierre limpio.

## Exclusiones

- cobros, renovaciones y webhooks de pago;
- administración automática de membresías;
- cuotas, ROI, Kelly o ejecución de apuestas;
- cambios en modelos, datos o router.

## Gate

1. Configuración sin token, API URL HTTPS o allowlist falla al arrancar.
2. Un usuario no autorizado no puede consultar datos ni predicciones.
3. La selección de un fixture devuelve la tarjeta y dashboard de Fase 101.
4. Los mensajes permanecen bajo 3,900 caracteres.
5. El contenedor ejecuta como usuario no privilegiado.
6. El bot alcanza readiness remoto y procesa un update falso exactamente una
   vez.
7. Suite dirigida y regresión integral aprobadas.
8. Todas las ventanas cumplen el contrato móvil: prosa ≤72 columnas visibles,
   tablas ≤40, botones ≤32 y mensajes ≤3,900 caracteres.

## Resultado

`validated_for_deployment`.

- paridad textual con el presentador Fase 101 aprobada;
- configuración incompleta rechazada antes del polling;
- imagen Docker: `57,852,478` bytes, usuario `app`;
- imagen sin modelos, snapshots ni evidencia histórica;
- 11 pruebas dirigidas aprobadas;
- regresión: 447 aprobadas y 8 integraciones opcionales omitidas.

### Revisión v1.1 — legibilidad móvil

La auditoría amplió el gate a cada familia visible del bot y del avisador:
menús, tarjetas, mercados por periodo, resumen diario, resultados, contexto,
play-by-play, estadísticas, plantillas, perfiles, errores y estado.

- tablas comparativas: `46 → 38` columnas;
- campos de nombres dinámicos acotados con elipsis;
- contexto y disponibilidad separados por equipo;
- botones dinámicos limitados a 32 columnas;
- eventos compactados dentro de tarjetas autoidentificables;
- 24 pruebas visuales dirigidas aprobadas;
- regresión integral: 450 aprobadas y 8 omitidas.

La prueba contra Telegram y la API reales se ejecutará al crear el segundo
servicio Railway con sus secretos. No cambia el router ni el estado de
promoción de ningún mercado.
