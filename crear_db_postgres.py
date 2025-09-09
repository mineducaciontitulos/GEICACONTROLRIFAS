# crear_db_postgres.py
import os, sys
import psycopg2
from dotenv import load_dotenv

load_dotenv()  # lee .env local si existe

SCHEMA_SQL = """
BEGIN;

-- 1) superadmin
CREATE TABLE IF NOT EXISTS superadmin (
  id            BIGSERIAL PRIMARY KEY,
  usuario       TEXT NOT NULL,
  contrasena    TEXT NOT NULL
);

-- 2) negocios
CREATE TABLE IF NOT EXISTS negocios (
  id                       BIGSERIAL PRIMARY KEY,
  nombre_negocio           TEXT NOT NULL,
  nombre_propietario       TEXT NOT NULL,
  celular                  TEXT NOT NULL,
  correo                   TEXT NOT NULL,
  contrasena               TEXT NOT NULL,
  public_key_wompi         TEXT DEFAULT ''::text,
  private_key_wompi        TEXT DEFAULT ''::text,
  merchant_id_wompi        TEXT DEFAULT ''::text,
  integrity_secret_wompi   TEXT DEFAULT ''::text,
  checkout_url_wompi       TEXT DEFAULT ''::text,
  estado                   TEXT DEFAULT 'activo',
  -- NUEVO: número de WhatsApp del negocio para el bot
  wa_numero_receptor       TEXT,
  -- NUEVO: configuración del bot (por negocio)
  bot_config               JSONB
);

-- 3) rifas
CREATE TABLE IF NOT EXISTS rifas (
  id                BIGSERIAL PRIMARY KEY,
  id_negocio        BIGINT NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  nombre            TEXT NOT NULL,
  descripcion       TEXT,
  avaluo            TEXT,
  cifras            INTEGER NOT NULL,
  cantidad_numeros  INTEGER NOT NULL,
  valor_numero      INTEGER NOT NULL,
  nombre_loteria    TEXT,
  imagen_premio     TEXT,
  link_publico      TEXT UNIQUE,
  estado            TEXT DEFAULT 'activa',
  fecha_inicio      TIMESTAMPTZ,
  fecha_fin         TIMESTAMPTZ
);

-- 4) compradores
CREATE TABLE IF NOT EXISTS compradores (
  id        BIGSERIAL PRIMARY KEY,
  nombre    TEXT NOT NULL,
  cedula    TEXT NOT NULL,
  correo    TEXT NOT NULL,
  telefono  TEXT
);

-- 5) numeros
CREATE TABLE IF NOT EXISTS numeros (
  id               BIGSERIAL PRIMARY KEY,
  id_rifa          BIGINT NOT NULL REFERENCES rifas(id) ON DELETE CASCADE,
  numero           TEXT NOT NULL,
  estado           TEXT DEFAULT 'disponible',
  id_comprador     BIGINT REFERENCES compradores(id) ON DELETE SET NULL,
  reservado_hasta  TIMESTAMPTZ,
  CONSTRAINT numeros_unq UNIQUE (id_rifa, numero)
);

-- 6) compras
CREATE TABLE IF NOT EXISTS compras (
  id           BIGSERIAL PRIMARY KEY,
  id_comprador BIGINT NOT NULL REFERENCES compradores(id) ON DELETE CASCADE,
  id_rifa      BIGINT NOT NULL REFERENCES rifas(id) ON DELETE CASCADE,
  numeros      TEXT NOT NULL,
  total        INTEGER NOT NULL,
  fecha        TIMESTAMPTZ NOT NULL,
  estado       TEXT DEFAULT 'pendiente',
  id_pago      TEXT,
  referencia   TEXT UNIQUE
);

-- 7) pagos
CREATE TABLE IF NOT EXISTS pagos (
  id                 BIGSERIAL PRIMARY KEY,
  id_compra          BIGINT NOT NULL REFERENCES compras(id) ON DELETE CASCADE,
  estado_pago        TEXT NOT NULL,
  referencia_pago    TEXT,
  fecha_confirmacion TIMESTAMPTZ
);

-- índices
CREATE INDEX IF NOT EXISTS idx_numeros_rifa_estado ON numeros (id_rifa, estado);
CREATE INDEX IF NOT EXISTS idx_compras_rifa        ON compras (id_rifa);
CREATE INDEX IF NOT EXISTS idx_compras_referencia  ON compras (referencia);

COMMIT;
"""

def get_db_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        print("❌ Falta DATABASE_URL en tu entorno (.env o variables de Render).")
        sys.exit(1)
    # Normalizaciones típicas de Render/GitHub Actions
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url

def main():
    db_url = get_db_url()
    # Log seguro (oculta contraseña)
    safe_url = db_url.split("@")[-1]
    print("🔌 Conectando a:", safe_url)

    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        print("❌ No se pudo conectar a PostgreSQL:", e)
        print("👉 Revisa que copiaste la **URL EXTERNA** exacta desde Render (botón copiar).")
        sys.exit(1)

    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            print("🧩 Servidor:", cur.fetchone()[0])
            cur.execute(SCHEMA_SQL)
        conn.commit()
        print("✅ Esquema creado/actualizado correctamente.")
    except Exception as e:
        conn.rollback()
        print("❌ Error creando el esquema:", e)
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()