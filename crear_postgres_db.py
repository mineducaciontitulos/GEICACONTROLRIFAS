import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Tabla negocio
cursor.execute('''
CREATE TABLE IF NOT EXISTS negocio (
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL,
    telefono TEXT,
    correo TEXT,
    direccion TEXT,
    logo TEXT,
    fondo TEXT,
    slug TEXT UNIQUE,
    categoria TEXT,
    tipo_categoria TEXT,
    propietario_nombre TEXT,
    propietario_telefono TEXT,
    numero_nequi TEXT,
    numero_daviplata TEXT,
    cuenta_pse TEXT,
    whatsapp TEXT,
    ciudad TEXT,
    contrasena TEXT,
    activo INTEGER DEFAULT 1
)
''')

# Tabla usuarios
cursor.execute('''
CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    negocio_id INTEGER REFERENCES negocio(id),
    usuario TEXT UNIQUE NOT NULL,
    clave TEXT NOT NULL
)
''')

# Rifas
cursor.execute('''
CREATE TABLE IF NOT EXISTS rifas (
    id SERIAL PRIMARY KEY,
    negocio_id INTEGER REFERENCES negocio(id),
    nombre_rifa TEXT,
    descripcion TEXT,
    avaluo_premio INTEGER,
    cifras INTEGER,
    cantidad_numeros INTEGER,
    precio_numero INTEGER,
    fecha_sorteo TEXT,
    numero_ganador TEXT,
    estado TEXT DEFAULT 'activa',
    fecha_creacion TEXT,
    loteria TEXT
)
''')

# Números rifa
cursor.execute('''
CREATE TABLE IF NOT EXISTS numeros_rifa (
    id SERIAL PRIMARY KEY,
    rifa_id INTEGER REFERENCES rifas(id),
    numero TEXT,
    estado TEXT DEFAULT 'disponible',
    nombre_cliente TEXT,
    whatsapp_cliente TEXT
)
''')

# Compras
cursor.execute('''
CREATE TABLE IF NOT EXISTS compras (
    id SERIAL PRIMARY KEY,
    rifa_id INTEGER REFERENCES rifas(id),
    numero TEXT,
    nombre_cliente TEXT,
    whatsapp_cliente TEXT,
    estado_pago TEXT DEFAULT 'pendiente',
    fecha_compra TEXT
)
''')

# Mensajes
cursor.execute('''
CREATE TABLE IF NOT EXISTS mensajes (
    id SERIAL PRIMARY KEY,
    compra_id INTEGER REFERENCES compras(id),
    mensaje TEXT,
    fecha_envio TEXT
)
''')

# Reservas
cursor.execute('''
CREATE TABLE IF NOT EXISTS reservas (
    id SERIAL PRIMARY KEY,
    rifa_id INTEGER REFERENCES rifas(id),
    numero TEXT,
    nombre TEXT,
    whatsapp TEXT,
    metodo_pago TEXT,
    fecha_reserva TEXT,
    estado TEXT DEFAULT 'pendiente'
)
''')

conn.commit()
cursor.close()
conn.close()

print("✅ Base PostgreSQL creada correctamente.")
