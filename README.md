# Estacionamiento Medido — Municipalidad de Villa María

Sistema web (frontend + backend + base de datos) para visualizar y gestionar
el estado de las actas de estacionamiento medido a través de SIGEMI, SEMyT
y SIGI.

## Stack

- **Backend:** FastAPI + SQLAlchemy (Python)
- **Base de datos:** SQLite por defecto (migrable a Postgres cambiando una variable)
- **Frontend:** HTML/CSS/JS plano, servido por el mismo proceso FastAPI (un solo puerto)

No usa Node/React a propósito: menos piezas móviles para mantener y desplegar
en un servidor municipal.

## 1. Instalación local (para probarlo ya)

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # en Windows: venv\Scripts\activate
pip install -r requirements.txt

# Datos de PRUEBA (no reales) para ver el sistema andando:
python seed_demo_data.py --reset --cantidad 50

# Levantar el servidor:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Abrí `http://localhost:8000` en el navegador. Ahí está todo: filtros, tabla,
combos de estado editables, paginación.

Documentación interactiva de la API (Swagger): `http://localhost:8000/docs`

## 2. Conectar los datos reales (SIGEMI + SEMyT + SIGI)

`backend/migrations/import_from_excel.py` es un **template**, no un script
terminado — no tengo el formato exacto de columnas de SEMyT ni de SIGI.
Tenés que:

1. Ajustar `mapear_estado_sigemi`, `mapear_estado_semyt`, `mapear_estado_sigi`
   con los valores reales que devuelve cada export.
2. Ajustar el nombre de la columna clave (`clave_sigemi`, `clave_semyt`,
   `clave_sigi`) — hoy asume `"ACTA"`.
3. Para SIGEMI y lo que hoy es Juzgado (ahora SIGI), ya tenés la lógica de
   descarga/lectura en `proyectoJuzgado` (los Excel `MULTAS_SIGEMI_CRUCE.xlsx`
   y `MULTAS_JUZGADO_CRUCE.xlsx`) — se puede reusar esa misma lógica de
   descarga y apuntarla a este script en vez de (o además de) `comparador.py`.

Una vez ajustado:

```bash
python migrations/import_from_excel.py \
    --sigemi ruta/MULTAS_SIGEMI_CRUCE.xlsx \
    --semyt  ruta/SEMYT_EXPORT.xlsx \
    --sigi   ruta/SIGI_EXPORT.xlsx
```

Esto hace upsert por Nº de Acta: si el acta ya existe la actualiza, si no
existe la crea. Podés correrlo tantas veces como quieras (ej: cron diario).

## 3. Fotos de vehículos

`foto_url` en la base es sólo una ruta/URL string. Si las fotos están en
un servidor propio, poné ahí la URL completa; si no tenés fotos para un
registro, el frontend cae automáticamente al placeholder gris.

## 4. Poner esto accesible para que otra persona entre

Opción simple con systemd (Linux) — recomendada para el servidor municipal:

Crear `/etc/systemd/system/estacionamiento.service`:

```ini
[Unit]
Description=Estacionamiento Medido
After=network.target

[Service]
User=www-data
WorkingDirectory=/ruta/al/proyecto/backend
Environment="PATH=/ruta/al/proyecto/backend/venv/bin"
ExecStart=/ruta/al/proyecto/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now estacionamiento
```

Después, para que la gente entre por un dominio/IP con https en vez de
`:8000`, poné nginx delante como reverse proxy:

```nginx
server {
    listen 80;
    server_name estacionamiento.tudominio.gob.ar;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Y le agregás certificado con `certbot` para https.

**Si el servidor es el mismo Mac donde corre proyectoJuzgado** (launchd),
el patrón es análogo al que ya usás ahí: un `.plist` que arranca
`uvicorn` en vez de un `LaunchAgent` para tu scheduler.

## 5. Sobre autenticación

Este entrega **no incluye login** — la tabla no lo tenía en el mockup y no
lo mencionaste. Si "que una persona pueda ingresar" implica que sólo
personal autorizado entre (no público en general), decime y agrego:
- lo más simple: HTTP Basic Auth a nivel nginx (5 minutos)
- lo más prolijo: login con usuario/contraseña + sesión en FastAPI

## Estructura del proyecto

```
estacionamiento_medido/
├── backend/
│   ├── app/
│   │   ├── main.py          # arranca FastAPI, monta API + frontend
│   │   ├── database.py      # conexión SQLite/Postgres
│   │   ├── models.py        # tabla Registro + enums de estado
│   │   ├── schemas.py       # validación request/response
│   │   ├── crud.py          # consultas con filtros y paginación
│   │   └── routers/
│   │       └── registros.py # endpoints /api/registros
│   ├── migrations/
│   │   └── import_from_excel.py   # template de migración real
│   ├── seed_demo_data.py    # datos de prueba
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html
    ├── styles.css
    ├── app.js
    └── static/photos/placeholder.jpg
```

## Endpoints principales

| Método | Ruta                          | Qué hace                                  |
|--------|-------------------------------|--------------------------------------------|
| GET    | `/api/registros`              | Lista paginada con filtros                  |
| GET    | `/api/registros/filtros`      | Valores posibles para los combos de estado  |
| PATCH  | `/api/registros/{id}`         | Cambia uno o más estados de un registro     |
| POST   | `/api/registros/{id}/refrescar` | Placeholder para refrescar contra SIGEMI/SEMyT/SIGI en vivo |

## RECREAR ENTORNO
deactivate
rm -rf venv
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
cd backend
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

http://localhost:8000

## CORRER LA API SEMYT
curl -X POST http://localhost:8000/api/procesamiento/semyt | python3 -m json.tool

## tema docker
Parar el contenedor (sin borrar datos):
docker compose stop
Volver a levantarl:
docker compose start
Ver logs:
docker compose logs -f db
Borrar todo (¡incluye los datos!):
docker compose down -v
## PASOS PARA LEVANTAR PROYECTO
docker compose down
docker compose down -v NUNCA USAR
Levantar proyecto
docker compose up -d

## conectar el backend con db, postgrest
matar procesos sueltos
kill -9 $(lsof -ti :8000)
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
uvicorn app.main:app --host 0.0.0.0 --reload
## ver si la api esta viva
curl http://localhost:8000/api/registros

## ver IP
ipconfig getifaddr en0
## hacer backaps
cd backend
python -c "from app.database import engine; u = engine.url; print(f'pg_dump -h {u.host} -p {u.port or 5432} -U {u.username} -d {u.database} -F c -f backup_pre_primera_carga.dump')"

pg_dump -h localhost -p 1234 -U nicolascesolari -d estacionamiento_medido -F c -f backup_por_las_dudas.dump

verificarlo
ls -lh nombre

## cargar actas en la base de datos
cd backend
prueba sin cargar
python cargar_actas_semyt.py
cargando sin interrupciones
caffeinate -i python cargar_actas_semyt.py --commit

## limpiar actas rechazadas por Emtupse antes de ser vencidas
cd backend
python limpiar_actas_rechazadas.py            # dry-run: lista qué borraría, no toca nada
python limpiar_actas_rechazadas.py --commit    # borra de verdad (registro + historial + foto)

## llenar causas sigemi
python llenar_actas_sigemi.py ruta/al/archivo_sigemi.txt --commit 
## actualizar estado sigemi
python actualizar_estado_sigemi.py ruta/al/archivo_sigemi.txt --commit 

### Ver el nombre del servicio/contenedor de postgres
docker compose ps

# Dump desde adentro del contenedor
docker compose exec -T postgres pg_dump -U nicolascesolari -d estacionamiento_medido > backup_$(date +%Y%m%d_%H%M).dump

## GIT HUB
# 1. Asegúrate de estar en la carpeta de tu proyecto y en la rama correcta (por ejemplo, main)
git checkout main

git remote add origin https://github.com/nico-cesolari/estacionamiento_medido 
# 2. Agrega todos los archivos actuales al área de preparación
git add .

# 3. Registra un commit con los cambios
git commit -m "Reemplazo completo del código"

# 4. Fuerza el envío hacia el repositorio remoto
git push origin main --force