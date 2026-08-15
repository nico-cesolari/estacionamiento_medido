-- ============================================================
-- Este archivo se ejecuta AUTOMÁTICAMENTE la primera vez que
-- Postgres crea el volumen de datos (base de datos vacía).
-- Si el volumen ya existe, este script NO se vuelve a correr.
-- Para forzar que corra de nuevo: docker compose down -v
-- ============================================================

-- 1. Esquema donde van a vivir las tablas que expone PostgREST
CREATE SCHEMA IF NOT EXISTS api;

-- 2. Roles necesarios para que PostgREST funcione con seguridad
--    web_anon: rol sin login, con permisos limitados (lo usan las requests anónimas)
--    authenticator: rol con login, que "se disfraza" de web_anon para cada request
CREATE ROLE web_anon NOLOGIN;
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD 'authenticator_pass';
GRANT web_anon TO authenticator;

GRANT USAGE ON SCHEMA api TO web_anon;

-- 3. Tabla de ejemplo: estacionamientos medidos
CREATE TABLE api.estacionamientos (
    id            SERIAL PRIMARY KEY,
    calle         TEXT NOT NULL,
    altura        INTEGER,
    latitud       NUMERIC(9,6),
    longitud      NUMERIC(9,6),
    tarifa_hora   NUMERIC(10,2) NOT NULL DEFAULT 0,
    activo        BOOLEAN NOT NULL DEFAULT true,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Permisos sobre la tabla para el rol anónimo (ajustá según lo que necesites exponer)
GRANT SELECT, INSERT, UPDATE, DELETE ON api.estacionamientos TO web_anon;
GRANT USAGE, SELECT ON SEQUENCE api.estacionamientos_id_seq TO web_anon;

-- 5. Un par de filas de prueba para testear rápido en pgAdmin4
INSERT INTO api.estacionamientos (calle, altura, latitud, longitud, tarifa_hora)
VALUES
    ('Av. Ejemplo', 1200, -32.408, -63.240, 150.00),
    ('San Martín',   450, -32.412, -63.245, 120.00);