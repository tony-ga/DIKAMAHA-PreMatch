# Fase 99 — interfaz Discord shadow

## Objetivo

Añadir una interfaz Discord privada, navegable y de baja fricción sobre la API
DIKAMAHA existente, sin implementar lógica predictiva dentro del bot.

## Alcance

- slash commands `/dikamaha`, `/proximos`, `/playbyplay`, `/estadisticas`,
  `/jugadores`, `/estado`;
- próximos globales, por liga y por fecha;
- selección nativa de partido y predicción;
- vistas de mercados 1T, 2T y total;
- play-by-play clave/completo con paginación;
- estadísticas por `1T`, `2T` y total;
- navegación liga→equipo→jugador y búsqueda modal de equipo;
- respuestas ephemeral y allowlists por usuario/servidor;
- nombres reales de equipos y estado oficial/shadow conservado;
- token y configuración sólo mediante `.env`.

## Gate de preparación

- importación y configuración válidas sin exponer secretos;
- cliente DIKAMAHA sustituible en pruebas;
- navegación determinista sin llamadas reales a Discord;
- límites de 25 opciones por selector y 2,000 caracteres por mensaje;
- timeout, retry y errores sanitizados;
- ningún cálculo de modelo dentro del adaptador;
- router oficial y probabilidades intactos;
- smoke real requiere además que la aplicación esté invitada al guild de
  desarrollo y que pueda sincronizar comandos allí.

## Clasificación inicial

`promising_unconfirmed`.

El Gateway real conectó con identidad válida. La aplicación ya pertenece al
guild configurado y los seis comandos se sincronizaron allí. Queda pendiente
una interacción manual del usuario autorizado para cerrar el smoke.
