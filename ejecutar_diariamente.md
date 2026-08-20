## CARGA
cd backend
python alta/cargar_actas_semyt.py --commit

cd backend
python alta/llenar_actas_sigi.py --commit

cd backend
python alta/llenar_actas_sigemi.py --commit

## ACTUALIZAR
cd backend
python update/actualizar_estado_semyt.py --commit

cd backend
python update/actualizar_estado_sigi.py --commit

cd backend
python update/actualizar_fecha_cobro_sigi.py --commit

cd backend 
python update/actualizar_estado_sigemi.py --commit

cd backend
python update/actualizar_fecha_cobro_sigemi.py --commit

# LIMPIAR
cd backend
python baja/limpiar_actas_rechazadas.py --commit

pgAdmin4
UPDATE registros 
SET foto_url = NULL 
WHERE foto_url = 'https://ciudad.villamaria.gob.ar/assets/img/user.jpg';
