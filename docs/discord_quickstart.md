# Discord DIKAMAHA — preparación

## Crear la aplicación

1. Crear una aplicación y un bot en Discord Developer Portal.
2. Copiar `.env.discord.example` a las variables correspondientes de `.env`.
3. Configurar `DISCORD_APPLICATION_ID`, `DISCORD_BOT_TOKEN` y, para pruebas
   rápidas, `DISCORD_GUILD_ID`.
4. Añadir los IDs autorizados en `DISCORD_ALLOWED_USER_IDS` y
   `DISCORD_ALLOWED_GUILD_IDS`.
5. Invitar la aplicación con los scopes `bot` y `applications.commands`.

No se requiere activar Message Content Intent. La interfaz usa slash commands
y componentes nativos.

## Instalar y ejecutar

```bash
python -m venv .venv-discord
.venv-discord/bin/pip install -r requirements.discord.txt
.venv-discord/bin/python scripts/run_phase_99_discord_bot.py
```

La API DIKAMAHA debe estar disponible en `DIKAMAHA_BOT_API_URL`.

## Flujo inicial

- `/dikamaha`: menú global;
- `/proximos`: próximos globales, por liga o fecha;
- `/playbyplay`: liga→fecha→partido→eventos clave/todos;
- `/estadisticas`: liga→fecha→partido→1T/2T/total;
- `/jugadores`: liga→equipo/búsqueda→plantilla→jugador;
- `/estado`: disponibilidad de la API;
- selección de partido: predicción, estadísticas o play-by-play.

Las respuestas son privadas para el usuario. Los mercados secundarios
conservan su etiqueta experimental y el bot no ejecuta apuestas.
