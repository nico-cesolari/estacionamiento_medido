# Automatización de pagos — Juzgado de Faltas (SIGI, Villa María)

Este proyecto hace **una sola cosa**: actualizar las multas vencidas a
**pagadas**, corriendo el proceso de pagos entre el SIGI (municipalidad) y
el SEMyT.

Paso a paso, cada corrida:

1. Inicia (o reutiliza) sesión en SEMyT y en SIGI.
2. Descarga, en paralelo, el TXT de pagos (SIGI) y los dos Excel de actas
   vencidas usados para el cruce (SEMyT).
3. Cruza esos pagos contra las actas vencidas y contra el maestro de
   causas de SIGEMI, y arma el TXT final.
4. Sube ese TXT al SEMyT, lo que marca esas multas como pagadas.

No hace nada más: no compara archivos sueltos, no revisa consistencia con
SIGEMI a mano, no exporta actas por separado. Eso se sacó a propósito para
que quede una sola responsabilidad clara.

## Estructura del proyecto

```
backend/            -> SOLO código (nada que se genere en tiempo de ejecución)
  main.py              Punto de entrada
  configs/             Configuración (.env, columnas, rutas)
  models/              Estructuras de datos (contexto de ejecución, credenciales)
  orquestador/         Panel, cola de procesos, programador del automático
  pages/               Selectores de Playwright (un archivo por pantalla)
  pasos/               Cada paso del workflow, chico y con una sola tarea
  services/            Lógica de negocio reutilizable (login, actas, pagos, excel)
  utils/               Utilidades genéricas (fechas, reintentos, carpetas por día)
  workflows/           Arma la secuencia de pasos de "pagos"

datos/               -> SOLO datos (nada de código; se recrea solo)
  sesiones/            Sesión guardada de Playwright (storage_state)
  descargas/           Archivos intermedios de cada corrida
  logs/                Logs organizados por día (AAAA-MM-DD/)
  maestro/             total_causas_sigemi.txt (de solo lectura, se actualiza a mano)

tests/               -> Tests unitarios de la lógica de cruce (sin Playwright)
```

## Cómo correrlo

### Primera vez
1. Instalar Python 3.11+.
2. Copiar `backend/.env.ejemplo` como `backend/.env` y completar credenciales.
3. Poner el maestro de causas en `datos/maestro/total_causas_sigemi.txt`.
4. Doble clic en `init.command` (instala dependencias y abre el panel).

### Uso normal
- **Panel manual** (`init.command` o `python3 -m backend.main`): opción
  `1) Actualizar pagos` para disparar una corrida a mano.
- **Servicio automático** (macOS, vía `launchd`): `instalar_servicio.command`
  deja el proceso de pagos corriendo solo, cada 1 hora, en segundo plano.
  `estado_servicio.command` / `pausar_servicio.command` /
  `reanudar_servicio.command` / `desinstalar_servicio.command` lo
  administran.

El panel manual y el servicio automático son seguros de usar al mismo
tiempo: un lock de archivo (`datos/.ejecucion.lock`) evita que dos
corridas se pisen entre sí (ver `backend/orquestador/trabajador.py`).

## Sesiones

Las sesiones de SEMyT/SIGI se guardan en `datos/sesiones/` para no tener
que loguearse en cada corrida. Se limpian solas en dos casos, sin
intervención manual:

- **Todos los días a las 00:00**, para forzar un login fresco (ver
  `ProgramadorAutomatico`).
- **Ante cualquier error** durante una corrida, como auto-recuperación: si
  algo falla, se asume que puede ser una sesión vencida y se borra para
  que la próxima corrida arranque con login limpio (ver
  `orquestador/trabajador.py`).

No hace falta borrar `datos/sesiones/` a mano.

## Tests

```
pip install -r backend/requirements.txt pytest
python3 -m pytest
```

Corren solo sobre la lógica de cruce (`backend/orquestador/comparador.py`)
con datos de ejemplo en memoria: no abren navegador ni tocan la red.
