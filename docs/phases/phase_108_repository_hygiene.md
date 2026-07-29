# Fase 108 — Higiene del repositorio y paquete de producción

**Fecha:** 2026-07-29  
**Estado:** validada

## Objetivo

Separar el código y los artefactos mínimos de ejecución de los datos locales,
cachés y evidencia científica histórica, sin romper trazabilidad ni el runtime
de Railway.

## Política

- Git conserva código, pruebas, documentación, contratos y modelos activos.
- Los artefactos históricos siguen disponibles localmente, pero se excluyen de
  Git; no son basura ni dependencias del servicio.
- Bases SQLite, respuestas raw, logs y cachés son estado local o persistente.
- Entornos virtuales, bytecode y cachés de API son regenerables y se eliminan.
- El snapshot activo se distribuye como gzip y se valida contra el hash SHA-256
  del JSON descomprimido.
- Docker copia archivos runtime por lista explícita, no directorios completos.

## Criterios de éxito

1. Ningún archivo requerido por Railway supera 100 MB.
2. El snapshot comprimido conserva hash y conteo del manifiesto.
3. El contenedor produce una predicción real.
4. La suite dirigida y la regresión integral permanecen verdes.
5. El inventario final documenta archivos y bytes eliminados.

## Exclusiones deliberadas

No se eliminan resultados de fases, cohortes, modelos congelados ni snapshots
históricos con valor de auditoría. Se mantienen fuera del repositorio remoto.

## Resultado

- retirados al menos `8,289,279,942` bytes (`7.72 GiB`) de objetivos medidos,
  además de 1,035 directorios de bytecode;
- workspace final: `4,797,370,041` bytes;
- contexto Docker: `5.04 MB`;
- imagen Docker: `181,455,750` bytes;
- snapshot activo: `122,014,971 → 3,098,211` bytes;
- hash lógico conservado:
  `26d9143b13a8a353db9203c457461e4973cc1bd9784b4fd943bdf3c3ed7aef3c`;
- 442 pruebas aprobadas y 8 integraciones opcionales omitidas;
- smoke Docker con `selective_dc_kalman_official` y suma 1X2 igual a `1.0`.

Los objetivos eliminados se enviaron a la papelera del sistema. La evidencia
histórica retenida ocupa `4,610,477,019` bytes y queda excluida de Git.
