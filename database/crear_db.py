import sqlite3

conn = sqlite3.connect("geica_controlrifas.db")
cursor = conn.cursor()

# Tabla de negocios
cursor.execute('''
    CREATE TABLE IF NOT EXISTS negocio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        activo INTEGER DEFAULT 1  -- Nuevo campo para habilitar o deshabilitar el negocio
    )
''')

# Usuarios administradores (login)
cursor.execute('''
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    negocio_id INTEGER,
    usuario TEXT UNIQUE NOT NULL,
    clave TEXT NOT NULL,
    FOREIGN KEY (negocio_id) REFERENCES negocios(id)
)
''')

# Tabla rifas (actualizada con slug y lotería)
cursor.execute('''
CREATE TABLE IF NOT EXISTS rifas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    negocio_id INTEGER,
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
    loteria TEXT,
    slug TEXT UNIQUE,
    FOREIGN KEY (negocio_id) REFERENCES negocio(id)
)
''')

# Números generados
cursor.execute('''
CREATE TABLE IF NOT EXISTS numeros_rifa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rifa_id INTEGER,
    numero TEXT,
    estado TEXT DEFAULT 'disponible',
    nombre_cliente TEXT,
    whatsapp_cliente TEXT,
    FOREIGN KEY (rifa_id) REFERENCES rifas(id)
)
''')

# Compras
cursor.execute('''
CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rifa_id INTEGER,
    numero TEXT,
    nombre_cliente TEXT,
    whatsapp_cliente TEXT,
    estado_pago TEXT DEFAULT 'pendiente',
    fecha_compra TEXT,
    FOREIGN KEY (rifa_id) REFERENCES rifas(id)
)
''')

# Mensajes enviados (alertas)
cursor.execute('''
CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    compra_id INTEGER,
    mensaje TEXT,
    fecha_envio TEXT,
    FOREIGN KEY (compra_id) REFERENCES compras(id)
)
''')

# Tabla de reservas temporales
cursor.execute('''
CREATE TABLE IF NOT EXISTS reservas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rifa_id INTEGER,
    numero TEXT,
    nombre TEXT,
    whatsapp TEXT,
    metodo_pago TEXT,
    fecha_reserva TEXT,
    estado TEXT DEFAULT 'pendiente',
    FOREIGN KEY (rifa_id) REFERENCES rifas(id)
)
''')

conn.commit()
conn.close()



print("✅ Base de datos creada exitosamente.")
