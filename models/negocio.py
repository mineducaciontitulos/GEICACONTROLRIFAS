from database.conexion import obtener_conexion
from werkzeug.security import check_password_hash
import sqlite3

def crear_negocio(nombre_negocio, nombre_admin, whatsapp, ciudad, usuario, clave):
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO negocio (nombre_negocio, nombre_admin, whatsapp, ciudad, usuario, clave)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (nombre_negocio, nombre_admin, whatsapp, ciudad, usuario, clave))
    conn.commit()
    conn.close()

def obtener_negocio_por_usuario(usuario):
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row  # ✅ Añadir también aquí para que no falle si usas ['campo']
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM negocio WHERE usuario = ?', (usuario,))
    negocio = cursor.fetchone()
    conn.close()
    return negocio

def obtener_negocio_por_id(id):
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row  # ✅ Esta línea es crucial
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM negocio WHERE id = ?", (id,))
    negocio = cursor.fetchone()
    conn.close()
    print("🔎 Datos del negocio:", dict(negocio) if negocio else "Negocio no encontrado")
    return negocio

def validar_login_usuario(usuario, clave):
    from werkzeug.security import check_password_hash
    from database.conexion import obtener_conexion

    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM usuarios WHERE usuario = ?', (usuario,))
    user = cursor.fetchone()
    conn.close()

    if user and check_password_hash(user['clave'], clave):
        return user
    return None


def obtener_negocio_por_slug(slug):
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row  # ✅ Esto lo hace accesible como dict
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM negocio WHERE slug = ?", (slug,))
    negocio = cursor.fetchone()

    conn.close()
    return negocio




