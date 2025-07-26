import sqlite3
from datetime import datetime
from database.conexion import obtener_conexion    
import uuid
from unidecode import unidecode

def crear_rifa(datos_rifa, negocio_id):
    conn = obtener_conexion()
    cursor = conn.cursor()

    # Generar slug único y seguro
    nombre_sanitizado = unidecode(datos_rifa['nombre_rifa'].lower().replace(" ", "-"))
    slug_rifa = f"{nombre_sanitizado}-{str(uuid.uuid4())[:6]}"

    cursor.execute('''
        INSERT INTO rifas (
            negocio_id, nombre_rifa, descripcion, avaluo_premio, cifras,
            cantidad_numeros, precio_numero, fecha_sorteo, fecha_creacion,
            nequi, daviplata, pse, slug
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        negocio_id,
        datos_rifa['nombre_rifa'],
        datos_rifa['descripcion'],
        datos_rifa['avaluo_premio'],
        datos_rifa['cifras'],
        datos_rifa['cantidad_numeros'],
        datos_rifa['precio_numero'],
        datos_rifa['fecha_sorteo'],
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        datos_rifa.get('nequi', ''),
        datos_rifa.get('daviplata', ''),
        datos_rifa.get('pse', ''),
        slug_rifa
    ))

    rifa_id = cursor.lastrowid

    for i in range(datos_rifa['cantidad_numeros']):
        numero = str(i).zfill(datos_rifa['cifras'])
        cursor.execute('INSERT INTO numeros_rifa (rifa_id, numero) VALUES (?, ?)', (rifa_id, numero))

    conn.commit()
    conn.close()

    # Retornar también el slug (sin afectar nada más)
    return rifa_id, slug_rifa


def obtener_rifa_por_id(rifa_id):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM rifas WHERE id = ?', (rifa_id,))
    rifa = cursor.fetchone()

    cursor.execute('SELECT * FROM numeros_rifa WHERE rifa_id = ?', (rifa_id,))
    numeros = cursor.fetchall()

    conn.close()
    return rifa, numeros


def obtener_rifas_por_negocio(negocio_id):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM rifas WHERE negocio_id = ? ORDER BY fecha_creacion DESC', (negocio_id,))
    rifas = cursor.fetchall()

    conn.close()
    return rifas


def registrar_compra(numero_id, comprador, telefono, metodo_pago):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE numeros_rifa
        SET vendido = 1,
            comprador = ?,
            telefono = ?,
            metodo_pago = ?,
            fecha_venta = ?
        WHERE id = ?
    ''', (
        comprador,
        telefono,
        metodo_pago,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        numero_id
    ))

    conn.commit()
    conn.close()


def obtener_datos_numero(numero_id):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM numeros_rifa WHERE id = ?', (numero_id,))
    numero = cursor.fetchone()

    conn.close()
    return numero


def obtener_rifas_activas_db():
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM rifas WHERE fecha_sorteo >= date("now") ORDER BY fecha_sorteo ASC')
    rifas = cursor.fetchall()

    conn.close()
    return rifas


def obtener_rifa_completa(rifa_id):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM rifas WHERE id = ?', (rifa_id,))
    rifa = cursor.fetchone()

    cursor.execute('SELECT * FROM numeros_rifa WHERE rifa_id = ? ORDER BY numero ASC', (rifa_id,))
    numeros = cursor.fetchall()

    conn.close()
    return rifa, numeros


def obtener_rifa_y_negocio(rifa_id):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT r.*, n.nombre AS nombre_negocio, n.whatsapp
        FROM rifas r
        JOIN negocio n ON r.negocio_id = n.id
        WHERE r.id = ?
    ''', (rifa_id,))
    resultado = cursor.fetchone()

    conn.close()
    return resultado

def obtener_rifas_por_slug(slug):
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row  # 🔥 Esto convierte los resultados en diccionarios
    cursor = conn.cursor()

    # Primero obtener el negocio por slug
    cursor.execute("SELECT id FROM negocio WHERE slug = ?", (slug,))
    negocio = cursor.fetchone()

    if negocio:
        cursor.execute("SELECT * FROM rifas WHERE negocio_id = ?", (negocio["id"],))
        rifas = cursor.fetchall()
        conn.close()
        return rifas
    else:
        conn.close()
        return []

def obtener_historial_por_slug(slug):
    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT 
        r.nombre_rifa AS nombre_rifa,
        c.numero,
        c.nombre_cliente,
        c.whatsapp_cliente AS whatsapp,
        c.fecha_compra
    FROM compras c
    INNER JOIN rifas r ON c.rifa_id = r.id
    INNER JOIN negocio n ON r.negocio_id = n.id
    WHERE n.slug = ?
    ORDER BY c.fecha_compra DESC
""", (slug,))

    
    resultados = cursor.fetchall()
    conn.close()
    return resultados or []


def obtener_datos_ganador(slug_negocio, numero_ganador):
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.nombre_cliente, c.whatsapp_cliente, c.numero, r.nombre_rifa, c.fecha_compra
        FROM compras c
        JOIN rifas r ON c.rifa_id = r.id
        JOIN negocio n ON r.negocio_id = n.id
        WHERE c.numero = ? AND n.slug = ? AND r.id = (
            SELECT r2.id
            FROM rifas r2
            JOIN negocio n2 ON r2.negocio_id = n2.id
            WHERE n2.slug = ?
            ORDER BY r2.fecha_sorteo DESC
            LIMIT 1
        )
        LIMIT 1
    """, (numero_ganador, slug_negocio, slug_negocio))

    resultado = cursor.fetchone()
    conn.close()
    return resultado




