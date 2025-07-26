from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from database.conexion import obtener_conexion
from datetime import datetime, timedelta
import sqlite3
from models.negocio import obtener_negocio_por_id
from models.rifa import crear_rifa
from models.rifa import obtener_rifas_por_slug, obtener_historial_por_slug
from models.negocio import obtener_negocio_por_slug
from models.rifa import obtener_datos_ganador
from utils.generador_codigo import generar_slug_rifa
import uuid
import unidecode



admin_routes = Blueprint('admin', __name__)

@admin_routes.route('/mis-rifas')
def mis_rifas():
    from datetime import datetime, timedelta

    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row  # Para acceder por nombre
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rifas ORDER BY fecha_sorteo DESC")
    rifas = cursor.fetchall()
    conn.close()

    rifas_con_enlace = []

    for rifa in rifas:
        enlace = None
        if rifa["fecha_creacion"]:
            fecha_creacion = datetime.strptime(rifa["fecha_creacion"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() - fecha_creacion < timedelta(hours=24):
                enlace = f"/rifa/{rifa['slug']}"

        rifas_con_enlace.append({
            **rifa,
            "enlace": enlace
        })

    return render_template('lista_rifas.html', rifas=rifas_con_enlace)


@admin_routes.route('/dashboard')
def dashboard():
    if 'usuario' not in session:
        return redirect(url_for('auth.login'))

    negocio_id = session.get("negocio_id")
    if not negocio_id:
        return render_template("mensaje.html", mensaje="❌ No se ha identificado el negocio actual.")

    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Obtener datos del negocio
    cursor.execute("SELECT * FROM negocio WHERE id = ?", (negocio_id,))
    negocio = cursor.fetchone()

    # Obtener rifas del negocio
    cursor.execute("SELECT * FROM rifas WHERE negocio_id = ?", (negocio_id,))
    rifas = cursor.fetchall()

    conn.close()

    if not negocio:
        return render_template("mensaje.html", mensaje="❌ El negocio no existe o fue eliminado.")

    return render_template('dashboard_admin.html', negocio=negocio, rifas=rifas)



@admin_routes.route('/crear-rifa', methods=['GET', 'POST'])
def crear_rifa_view():
    if 'negocio_id' not in session:
        return redirect(url_for('auth.login'))

    negocio_id = session['negocio_id']
    negocio = obtener_negocio_por_id(negocio_id)
    numero_admin = negocio['propietario_telefono'] if negocio else ''
    slug_negocio = negocio['slug'] if negocio else ''
    print("📞 WhatsApp del negocio:", numero_admin)

    conn = obtener_conexion()
    cursor = conn.cursor()

    # Obtener cuentas actuales del negocio
    cursor.execute("SELECT numero_nequi, numero_daviplata, cuenta_pse FROM negocio WHERE id = ?", (negocio_id,))
    cuentas = cursor.fetchone()
    mostrar_campos_pago = not cuentas or not any(cuentas)

    mensaje = ""
    enlace_rifa = None

    if request.method == 'POST':
        # Actualizar cuentas en la tabla negocio
        numero_nequi = request.form.get('numero_nequi', '').strip()
        numero_daviplata = request.form.get('numero_daviplata', '').strip()
        cuenta_pse = request.form.get('cuenta_pse', '').strip()

        cursor.execute('''
            UPDATE negocio SET numero_nequi = ?, numero_daviplata = ?, cuenta_pse = ?
            WHERE id = ?
        ''', (numero_nequi, numero_daviplata, cuenta_pse, negocio_id))

        # Datos de la rifa
        nombre_rifa = request.form['nombre_rifa']
        slug_rifa = generar_slug_rifa(nombre_rifa)
        descripcion = request.form['descripcion']
        avaluo = int(request.form['avaluo_premio'])
        cifras = int(request.form['cifras'])
        cantidad = int(request.form['cantidad_numeros'])
        precio = int(request.form['precio_numero'])
        fecha = request.form['fecha_sorteo']
        loteria = request.form.get('loteria', '').strip()
        fecha_creacion = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Generar el slug para compartir la rifa
        slug_rifa = generar_slug_rifa(nombre_rifa)

        # Insertar rifa
        cursor.execute('''
           INSERT INTO rifas 
           (negocio_id, nombre_rifa, descripcion, avaluo_premio, cifras, cantidad_numeros, precio_numero, fecha_sorteo, fecha_creacion, loteria, slug)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
           negocio_id, nombre_rifa, descripcion, avaluo, cifras, cantidad, precio, fecha, fecha_creacion, loteria, slug_rifa
        ))

        rifa_id = cursor.lastrowid

        # Insertar números del talonario con lógica especial para 3 y 4 cifras
        if cifras == 2:
            for i in range(cantidad):
                numero = str(i).zfill(cifras)
                cursor.execute('''
                    INSERT INTO numeros_rifa (rifa_id, numero)
                    VALUES (?, ?)
                ''', (rifa_id, numero))
        else:
            from random import sample
            limite = 10 ** cifras
            todos = [str(i).zfill(cifras) for i in range(limite)]
            numeros_aleatorios = sample(todos, cantidad)

            for numero in numeros_aleatorios:
                cursor.execute('''
                    INSERT INTO numeros_rifa (rifa_id, numero)
                    VALUES (?, ?)
                ''', (rifa_id, numero))

        conn.commit()
        conn.close()

        mensaje = "✅ Rifa creada y cuentas actualizadas con éxito."
        enlace_rifa = f"https://geicacontrolrifas.onrender.com/rifa/{slug_rifa}"
        print("✅ ENLACE GENERADO PARA WHATSAPP:", enlace_rifa)

        return render_template(
            'crear_rifa.html',
            mensaje=mensaje,
            enlace_rifa=enlace_rifa,
            numero_admin=numero_admin,
            mostrar_campos_pago=False,
            cuentas=cuentas,
            negocio=negocio  # ✅ Aquí se pasa el nombre del negocio
        )

    # GET - Primera vez cargando el formulario
    conn.close()
    return render_template(
        'crear_rifa.html',
        mensaje=mensaje,
        enlace_rifa=enlace_rifa,
        numero_admin=numero_admin,
        mostrar_campos_pago=mostrar_campos_pago,
        cuentas=cuentas,
        negocio=negocio  # ✅ También se pasa aquí para mostrar el nombre del negocio
    )


@admin_routes.route('/configurar-metodos-pago', methods=['GET'])
def configurar_metodos_pago():
    if 'negocio_id' not in session:
        return redirect(url_for('auth_routes.login'))

    negocio_id = session['negocio_id']
    conexion = obtener_conexion()
    cursor = conexion.cursor()
    cursor.execute("SELECT cuenta_nequi, cuenta_daviplata, cuenta_pse FROM negocio WHERE id = ?", (negocio_id,))
    resultado = cursor.fetchone()
    conexion.close()

    return render_template('configurar_metodos_pago.html', metodos=resultado)


@admin_routes.route('/guardar-metodos-pago', methods=['POST'])
def guardar_metodos_pago():
    if 'negocio_id' not in session:
        return redirect(url_for('auth_routes.login'))

    negocio_id = session['negocio_id']
    cuenta_nequi = request.form['cuenta_nequi']
    cuenta_daviplata = request.form['cuenta_daviplata']
    cuenta_pse = request.form['cuenta_pse']

    conexion = obtener_conexion()
    cursor = conexion.cursor()
    cursor.execute("""
        UPDATE negocio 
        SET cuenta_nequi = ?, cuenta_daviplata = ?, cuenta_pse = ?
        WHERE id = ?
    """, (cuenta_nequi, cuenta_daviplata, cuenta_pse, negocio_id))
    conexion.commit()
    conexion.close()

    flash("✅ Métodos de pago actualizados correctamente.")
    return redirect(url_for('admin_routes.configurar_metodos_pago'))



@admin_routes.route('/guardar-cuentas-negocio', methods=['POST'])
def guardar_cuentas_negocio():
    data = request.get_json()
    negocio_id = session.get('negocio_id')

    if not negocio_id:
        return jsonify({'success': False, 'error': 'Sesión inválida'})

    conn = obtener_conexion()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE negocio
            SET numero_nequi = ?, numero_daviplata = ?, cuenta_pse = ?
            WHERE id = ?
        """, (data['nequi'], data['daviplata'], data['pse'], negocio_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})
    
from datetime import datetime, timedelta

@admin_routes.route('/ver-rifas/<slug>')
def ver_rifas_admin_slug(slug):
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Buscar el negocio por slug
    cursor.execute("SELECT * FROM negocio WHERE slug = ?", (slug,))
    negocio = cursor.fetchone()

    if not negocio:
        return render_template("error.html", mensaje="❌ Negocio no encontrado.")

    if negocio["activo"] != 1:
        return render_template("error.html", mensaje="⚠️ Este negocio está inactivo.")

    # Calcular la fecha límite hace 15 días (con hora incluida)
    from datetime import datetime, timedelta
    fecha_limite = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")

    # Buscar rifas activas o creadas en los últimos 15 días
    cursor.execute("""
        SELECT * FROM rifas 
        WHERE negocio_id = ?
        AND (
            estado = 'activa' OR
            substr(fecha_creacion, 1, 19) >= ?
        )
        ORDER BY datetime(fecha_creacion) DESC
    """, (negocio["id"], fecha_limite))

    rifas = cursor.fetchall()
    conn.close()

    mensaje_info = "📆 Solo se muestran rifas activas y las creadas en los últimos 15 días."
    return render_template("ver_rifas_admin.html", rifas=rifas, negocio=negocio, mensaje_info=mensaje_info)



@admin_routes.route('/historial-ganadores/<slug>')
def historial_ganadores_admin(slug):
    negocio = obtener_negocio_por_slug(slug)
    if not negocio:
        flash("Negocio no encontrado", "danger")
        return redirect(url_for('auth.login'))

    historial = obtener_historial_por_slug(slug)
    return render_template('historial_ganadores_admin.html', negocio=negocio, historial=historial)

@admin_routes.route('/ganador/<slug>/<int:numero>')
def mostrar_ganador(slug, numero):
    ganador = obtener_datos_ganador(slug, numero)
    print("[DEBUG GANADOR]", ganador)
    if ganador:
        return render_template('ganador.html', ganador=ganador)
    else:
        return "❌ Número no encontrado para este negocio", 404

@admin_routes.route('/crear-rifa')
def crear_rifa():
    return render_template('crear_rifa.html')
    

@admin_routes.route('/panel-compras-admin/<slug>') 
def panel_compras_admin(slug):
    from datetime import datetime
    fecha_actual = datetime.now().date().isoformat()  # ✅ Primero se define antes de usarse

    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Obtener el negocio completo
    cursor.execute("SELECT * FROM negocio WHERE slug = ?", (slug,))
    negocio = cursor.fetchone()

    if not negocio:
        flash("⚠️ No se encontraron datos del negocio.")
        return redirect(url_for('admin.dashboard'))

    # ✅ Mostrar solo rifas activas (fecha de sorteo hoy o futura)
    cursor.execute("""
        SELECT rifas.*
        FROM rifas
        WHERE rifas.negocio_id = ? AND DATE(rifas.fecha_sorteo) >= DATE(?)
        ORDER BY rifas.fecha_creacion DESC
    """, (negocio['id'], fecha_actual))
    rifas = cursor.fetchall()

    # Obtener los números por rifa
    numeros_por_rifa = {}
    for rifa in rifas:
        cursor.execute("SELECT * FROM numeros_rifa WHERE rifa_id = ?", (rifa['id'],))
        numeros_por_rifa[rifa['id']] = cursor.fetchall()

    conn.close()

    return render_template(
        'ver_rifa_comprador.html',
        rifas=rifas,
        numeros_por_rifa=numeros_por_rifa,
        negocio=negocio,
        fecha_actual=fecha_actual,
        admin=True
    )





