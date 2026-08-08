# Fase 109 — Bot premium Telegram en Railway

## Objetivo

Desplegar el bot como servicio Railway independiente del canal, con acceso
privado o público explícito y la misma presentación de predicciones y mercados
que la difusión automática.

## Alcance

- long polling en un solo proceso;
- API DIKAMAHA remota por HTTPS y `X-Dikamaha-Key`;
- acceso `private|public`; el modo privado exige `TELEGRAM_ALLOWED_USER_IDS`;
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

1. Configuración sin token o API URL HTTPS falla al arrancar; `private` sin
   allowlist también falla y un modo desconocido es rechazado.
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
- 30 pruebas dirigidas aprobadas;
- regresión: 457 aprobadas y 8 integraciones opcionales omitidas.

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

### Revisión v1.3 — tolerancia de `/help`

La ayuda completa se mantiene disponible sin autorización y ahora cuenta con
un fallback seguro: si Telegram rechaza el HTML de cualquier ventana, el
transporte reintenta una vez como texto plano sin teclado. El rechazo registra
sólo método, estado y descripción sanitizada, nunca token ni cuerpo sensible.
La suite integral quedó en `459 passed, 8 skipped`, con pruebas específicas
para `/help` y el fallback de transporte.

### Revisión v1.4 — ayuda orientada al usuario

`/start` y `/help` ya no muestran variables de entorno, tokens, allowlists ni
detalles de configuración interna. Sólo exponen comandos, navegación,
mercados disponibles y el aviso analítico correspondiente.

### Revisión v1.2 — interruptor público

`TELEGRAM_ACCESS_MODE` admite:

- `private`: valor por defecto; sólo IDs de la allowlist;
- `public`: cualquier usuario en chat privado.

El modo público conserva rate limit individual, una réplica de long polling,
API key, HTTPS y rechazo completo de grupos. Cambiar el modo es reversible y
no requiere reconstruir modelos. Aprobaron 30 pruebas dirigidas y 457 pruebas
integrales; 8 integraciones opcionales fueron omitidas.

### Revisión v1.5 — regresión Cambridge United–Barnet

El flujo `Todos los próximos` resolvió correctamente el fixture `401880614`,
pero la API desplegada devolvía `shadow_unavailable` por verificación de
artefactos no portable. El bot quedaba sin dashboard por periodo aunque la
predicción oficial existía.

Con la corrección de Fase 113 v1.1, la imagen mínima devuelve 8 filas de
mercado y 21 grupos distribucionales. La presentación compartida genera una
tarjeta de 345 caracteres y un dashboard de 2,941 caracteres con primer
tiempo, segundo tiempo y partido completo. Estado:
`validated_for_deployment`; no modifica probabilidades ni modelos.

### Revisión v1.6 — modelos live visibles

El menú principal incorpora `Partidos en vivo` y `Modelos en operación`.
Telegram sigue siendo un cliente ligero: lista fixtures activos mediante
`GET /v1/live` y solicita la inferencia con
`POST /v1/predict/live/fixture`; nunca llama ESPN ni carga artefactos.

La tarjeta live separa Markov Live, Hawkes residual y combinado, muestra
marcador/reloj, 1X2, over 2.5, BTTS y próximo evento, y rotula todo el bloque
como `shadow`. Hawkes se describe como complemento; fuera de allowlist usa
fallback Markov exacto. `/en_vivo` y `/modelos` ofrecen las mismas rutas por
comando.

La política Hawkes queda empaquetada sólo en la imagen API. La imagen del bot
continúa ejecutando como `app` y sin modelos, snapshots ni artefactos. Las dos
imágenes construyeron y sus smoke tests aprobaron; regresión integral:
529 aprobadas y 8 integraciones opcionales omitidas.
