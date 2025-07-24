from flask import Blueprint, render_template, request, redirect, url_for, flash
import sqlite3
from database.conexion import obtener_conexion
from werkzeug.security import generate_password_hash

superadmin_routes = Blueprint('superadmin', __name__)


from flask import Blueprint, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash
from database.conexion import obtener_conexion


@superadmin_routes.route('/crear-cliente', methods=['GET', 'POST'])
def crear_cliente():
    if request.method == 'POST':
        nombre = request.form['nombre']
        propietario_nombre = request.form['propietario_nombre']
        propietario_telefono = request.form['propietario_telefono']
        ciudad = request.form['ciudad']
        slug = request.form.get('slug', '').strip()
        contrasena = request.form['contrasena']

        # 🔐 Encriptar la contraseña
        hashed_password = generate_password_hash(contrasena)

        # 🧠 Generar automáticamente el nombre de usuario a partir del slug
        usuario = f"admin_{slug.replace('-', '_')}"

        conn = obtener_conexion()
        cursor = conn.cursor()

        # Verifica si el slug ya existe
        cursor.execute("SELECT id FROM negocio WHERE slug = ?", (slug,))
        if cursor.fetchone():
            conn.close()
            flash('❌ El enlace (slug) ya está en uso. Elige otro.', 'error')
            return redirect(url_for('superadmin.crear_cliente'))

        # Inserta en la tabla negocio
        cursor.execute("""
            INSERT INTO negocio (
                nombre, propietario_nombre, propietario_telefono, ciudad, slug, contrasena
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (nombre, propietario_nombre, propietario_telefono, ciudad, slug, hashed_password))
        conn.commit()

        # Obtener el ID del negocio recién creado
        cursor.execute("SELECT id FROM negocio WHERE slug = ?", (slug,))
        negocio = cursor.fetchone()
        negocio_id = negocio['id']

        # Inserta el usuario de acceso en la tabla 'usuarios'
        cursor.execute("""
            INSERT INTO usuarios (negocio_id, usuario, clave)
            VALUES (?, ?, ?)
        """, (negocio_id, usuario, hashed_password))
        conn.commit()
        conn.close()

        flash(f'✅ Cliente y acceso creados correctamente. Usuario: {usuario}', 'success')
        return redirect(url_for('auth.login'))


    return render_template('crear_cliente.html')

@superadmin_routes.route('/panel-superadmin')
def panel_superadmin():
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM negocio ORDER BY id DESC")
    negocios = cursor.fetchall()
    conn.close()

    return render_template('panel_superadmin.html', negocios=negocios)

@superadmin_routes.route('/cambiar_estado_negocio', methods=['POST'])
def cambiar_estado_negocio():
    negocio_id = request.form['negocio_id']
    activo = 1 if 'activo' in request.form else 0

    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("UPDATE negocio SET activo = ? WHERE id = ?", (activo, negocio_id))
    conn.commit()
    conn.close()

    return redirect(url_for('superadmin.panel_superadmin'))


