import sqlite3

conn = sqlite3.connect('geicacontrolrifas.db')
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS superadmin (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario TEXT NOT NULL,
    contrasena TEXT NOT NULL
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS negocios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_negocio TEXT NOT NULL,
    nombre_propietario TEXT NOT NULL,
    celular TEXT NOT NULL,
    correo TEXT NOT NULL,
    contrasena TEXT NOT NULL,
    public_key_wompi TEXT DEFAULT '',
    private_key_wompi TEXT DEFAULT '',
    merchant_id_wompi TEXT DEFAULT '',
    integrity_secret_wompi TEXT DEFAULT '',
    checkout_url_wompi TEXT DEFAULT '',
    estado TEXT DEFAULT 'activo',
    -- NUEVO
    wa_numero_receptor TEXT,
    -- NUEVO (en SQLite guardamos JSON como texto)
    bot_config TEXT
);
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS rifas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_negocio INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    descripcion TEXT,
    avaluo TEXT,
    cifras INTEGER NOT NULL,
    cantidad_numeros INTEGER NOT NULL,
    valor_numero INTEGER NOT NULL,
    nombre_loteria TEXT,
    imagen_premio TEXT,
    link_publico TEXT UNIQUE,
    estado TEXT DEFAULT 'activa',
    fecha_inicio TEXT,
    fecha_fin TEXT,
    FOREIGN KEY (id_negocio) REFERENCES negocios(id)
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS numeros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_rifa INTEGER NOT NULL,
    numero TEXT NOT NULL,
    estado TEXT DEFAULT 'disponible',  -- disponible, reservado, pagado
    id_comprador INTEGER,
    reservado_hasta TEXT,
    FOREIGN KEY (id_rifa) REFERENCES rifas(id),
    UNIQUE (id_rifa, numero)
)
""")

# Índices útiles
cur.execute("CREATE INDEX IF NOT EXISTS idx_numeros_rifa_estado ON numeros(id_rifa, estado)")
# Nota: este índice de compras lo creamos después de definir la tabla compras (abajo)

cur.execute("""
CREATE TABLE IF NOT EXISTS compradores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    cedula TEXT NOT NULL,
    correo TEXT NOT NULL,
    telefono TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_comprador INTEGER NOT NULL,
    id_rifa INTEGER NOT NULL,
    numeros TEXT NOT NULL,
    total INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    estado TEXT DEFAULT 'pendiente',
    id_pago TEXT,               -- opcional: id de la transacción en Wompi
    referencia TEXT UNIQUE,     -- referencia "compra_123"
    FOREIGN KEY (id_comprador) REFERENCES compradores(id),
    FOREIGN KEY (id_rifa) REFERENCES rifas(id)
)
""")

# Índices de compras (después de crear la tabla)
cur.execute("CREATE INDEX IF NOT EXISTS idx_compras_rifa ON compras(id_rifa)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_compras_referencia ON compras(referencia)")

cur.execute("""
CREATE TABLE IF NOT EXISTS pagos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_compra INTEGER NOT NULL,
    estado_pago TEXT NOT NULL,
    referencia_pago TEXT,
    fecha_confirmacion TEXT,
    FOREIGN KEY (id_compra) REFERENCES compras(id)
)
""")

conn.commit()
conn.close()
print("Base de datos creada exitosamente con reservas, mejoras y soporte para bot por negocio.")