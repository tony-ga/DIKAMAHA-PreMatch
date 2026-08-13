# Rollback manual — migración 014

La tabla `catalog_cache` es aditiva y no participa de ningún contrato causal.
Sólo guarda copias derivadas de barridos ESPN que pueden reconstruirse
repitiendo el barrido, así que aquí el rollback es la excepción del proyecto:
no destruye evidencia.

Aun así, no se incluye un script automático. Para revertir:

1. desactivar el warmer con `DIKAMAHA_CATALOG_WARMER_ENABLED=false` (no
   requiere redespliegue del código, sólo reinicio del servicio);
2. comprobar que el servicio sigue respondiendo `/v1/upcoming` y `/v1/live`
   con la caché en memoria, que es autosuficiente;
3. ejecutar manualmente `DROP TABLE catalog_cache` sólo con autorización.

Alternativa no destructiva y preferible: `TRUNCATE catalog_cache`. Vacía la
caché sin tocar el esquema y el único efecto es que el siguiente barrido se
calcula en frío.

**Consecuencia de revertir:** el catálogo vuelve a perderse en cada despliegue
o reinicio del contenedor, y el primer usuario tras cada uno paga de nuevo los
~30 s del barrido completo.
