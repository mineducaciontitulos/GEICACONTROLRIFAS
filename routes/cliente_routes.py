from flask import Blueprint, render_template
from controllers.rifa_controller import obtener_rifas_activas
from database.conexion import obtener_conexion
from flask import request, jsonify
from utils.whatsapp_api import enviar_mensaje_whatsapp
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, session, flash
import sqlite3
from flask import Blueprint
from datetime import datetime



cliente_routes = Blueprint('cliente_routes', __name__)

@cliente_routes.route('/rifa/<int:rifa_id>')
def ver_rifa(rifa_id):
    conn = obtener_conexion()
    cursor = conn.cursor()

    # Traer la rifa junto con el slug del negocio
    cursor.execute("""
        SELECT rifas.*, negocio.slug AS negocio_slug,
               negocio.numero_nequi, negocio.numero_daviplata, negocio.cuenta_pse,
               negocio.nombre AS nombre_negocio
        FROM rifas
        JOIN negocio ON rifas.negocio_id = negocio.id
        WHERE rifas.id = ?
    """, (rifa_id,))
    rifa = cursor.fetchone()

    if not rifa:
        conn.close()
        return "Rifa no encontrada", 404

    # Obtener los números de esa rifa
    cursor.execute("SELECT * FROM numeros_rifa WHERE rifa_id = ?", (rifa_id,))
    numeros = cursor.fetchall()

    cuentas_pago = {
        "nequi": rifa["numero_nequi"] if rifa["numero_nequi"] else "No disponible",
        "daviplata": rifa["numero_daviplata"] if rifa["numero_daviplata"] else "No disponible",
        "pse": rifa["cuenta_pse"] if rifa["cuenta_pse"] else "No disponible"
    }

    fecha_actual = datetime.now().date()  # ← ¡Clave para los filtros en Jinja2!

    conn.close()
    return render_template(
        "ver_rifa_comprador.html",
        rifa=rifa,
        numeros=numeros,
        negocio={"nombre": rifa["nombre_negocio"], "slug": rifa["negocio_slug"]},
        cuentas=cuentas_pago,
        fecha_actual=fecha_actual
    )

@cliente_routes.route('/rifas-disponibles')
def ver_rifas_disponibles():
    rifas = obtener_rifas_activas()
    return render_template('ver_rifas_comprador.html', rifas=rifas)


@cliente_routes.route('/cliente/rifas')
def rifas_disponibles():
    conexion = obtener_conexion()
    cursor = conexion.cursor()
    cursor.execute("SELECT id, nombre_rifa, descripcion, precio_numero, fecha_sorteo FROM rifas")
    rifas = [dict(zip([col[0] for col in cursor.description], fila)) for fila in cursor.fetchall()]
    conexion.close()
    return render_template('ver_rifas_disponibles.html', rifas=rifas)


@cliente_routes.route("/reportar-pago", methods=["POST"])
def reportar_pago():
    data = request.get_json()
    nombre = data.get("nombre")
    whatsapp = data.get("whatsapp")
    metodo_pago = data.get("metodo_pago")
    numeros = data.get("numeros")

    # Conexión y obtención de negocio vinculado al número
    conn = obtener_conexion()
    cursor = conn.cursor()

    # Buscar el primer número seleccionado y usarlo para encontrar la rifa y el negocio
    numero_split = numeros.split(",")[0]
    cursor.execute("SELECT rifa_id FROM numeros_rifa WHERE numero = ?", (numero_split,))
    rifa_info = cursor.fetchone()

    if not rifa_info:
        conn.close()
        return jsonify({"exito": False})

    cursor.execute("SELECT * FROM rifas WHERE id = ?", (rifa_info["rifa_id"],))
    rifa = cursor.fetchone()

    cursor.execute("SELECT * FROM negocio WHERE slug = ?", (rifa["negocio_slug"],))
    negocio = cursor.fetchone()
    conn.close()

    numero_admin = negocio["whatsapp"]

    mensaje = (
        f"📢 *Nuevo pago reportado*\n\n"
        f"👤 Cliente: {nombre}\n"
        f"📱 WhatsApp: {whatsapp}\n"
        f"🎯 Negocio: {negocio['nombre_negocio']}\n"
        f"💳 Método: {metodo_pago}\n"
        f"🔢 Números: {numeros}\n\n"
        f"✅ Verifícalo en el panel."
    )

    try:
        enviar_mensaje_whatsapp(numero_admin, mensaje)
        return jsonify({"exito": True})
    except Exception as e:
        print(f"Error al enviar WhatsApp: {e}")
        return jsonify({"exito": False})
    
@cliente_routes.route('/<slug>')
def acceder_negocio(slug):
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM negocio WHERE slug = ?", (slug,))
    negocio = cursor.fetchone()
    conn.close()

    if negocio is None:
        return render_template("error.html", mensaje="❌ Negocio no encontrado.")

    if negocio["activo"] != 1:
        return render_template("error.html", mensaje="⚠️ Este negocio está deshabilitado temporalmente.")

    # Si está activo, mostrar panel del negocio
    return render_template("cliente.html", negocio=negocio)

@cliente_routes.route('/api/negocio')
def api_negocio():
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT numero_nequi, numero_daviplata, cuenta_pse FROM negocio LIMIT 1")
    datos = cursor.fetchone()
    conn.close()

    if datos:
        return jsonify(dict(datos))
    else:
        return jsonify({"error": "No se encontró información"}), 404

@cliente_routes.route('/registrar-compra', methods=['POST'])
def registrar_compra():
    from app import generar_ticket_imagen, enviar_ticket_whatsapp  # ✅ Ya los tienes
    data = request.json
    conn = None
    numeros_comprados = data['numeros']
    try:
        conn = obtener_conexion()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for numero in numeros_comprados:
            cursor.execute('''
                INSERT INTO compras (rifa_id, numero, nombre_cliente, whatsapp_cliente, fecha_compra)
                VALUES (?, ?, ?, ?, datetime('now'))
            ''', (data['rifa_id'], numero, data['nombre_cliente'], data['whatsapp_cliente']))

            cursor.execute('''
                UPDATE numeros_rifa
                SET estado = 'vendido'
                WHERE rifa_id = ? AND numero = ?
            ''', (data['rifa_id'], numero))

        # 🔔 Obtener datos del negocio
        cursor.execute("""
            SELECT negocio.whatsapp, negocio.nombre, rifas.nombre_rifa
            FROM negocio
            JOIN rifas ON rifas.negocio_id = negocio.id
            WHERE rifas.id = ?
        """, (data['rifa_id'],))
        admin = cursor.fetchone()
        nombre_rifa = admin['nombre_rifa']


        conn.commit()

    except sqlite3.OperationalError as e:
        print(f"[⚠️ SQLite bloqueada] {e}")
        return jsonify({"error": "❌ Base de datos en uso. Inténtalo nuevamente."}), 500
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"[❌ Error inesperado] {e}")
        return jsonify({"error": "❌ Ocurrió un error al registrar la compra."}), 500
    finally:
        if conn:
            conn.close()

    # 🟢 Notificar al ADMIN por WhatsApp
    if admin and admin["whatsapp"]:
        try:
            from twilio.rest import Client
            import os
            from dotenv import load_dotenv

            load_dotenv()

            client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            twilio_whatsapp = os.getenv("TWILIO_WHATSAPP_NUMBER")
            numero_admin = f"whatsapp:+57{admin['whatsapp'][-10:]}"  # solo últimos 10 dígitos

            mensaje = f"""📣 NUEVA COMPRA REGISTRADA
👤 Cliente: {data['nombre_cliente']}
📱 WhatsApp: {data['whatsapp_cliente']}
🎟️ Números: {', '.join(numeros_comprados)}
🏪 Negocio: {admin['nombre']}"""

            client.messages.create(
                body=mensaje,
                from_=twilio_whatsapp,
                to=numero_admin
            )
        except Exception as e:
            print(f"[⚠️ Error al enviar WhatsApp al ADMIN] {e}")

    # 🟢 Notificar al COMPRADOR con su ticket
    try:
        ticket_path = generar_ticket_imagen(
            nombre=data['nombre_cliente'],
            whatsapp=data['whatsapp_cliente'],
            numeros=numeros_comprados,
            nombre_rifa=nombre_rifa
    )

        enviar_ticket_whatsapp(
            nombre=data['nombre_cliente'],
            whatsapp_destino=data['whatsapp_cliente'],
            numeros=numeros_comprados,
            ticket_path=ticket_path,
            nombre_rifa=nombre_rifa
    )

    except Exception as e:
        print(f"[⚠️ Error al enviar ticket al COMPRADOR] {e}")

    return jsonify({"mensaje": "🎉 ¡Compra registrada correctamente!"})


@cliente_routes.route('/notificar-compra-admin', methods=['POST'])
def notificar_compra_admin():
    data = request.get_json()
    rifa_id = data.get("rifa_id")
    cliente = data.get("cliente")
    whatsapp = data.get("whatsapp")
    numeros = data.get("numeros")

    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT n.whatsapp
        FROM rifas r
        JOIN negocio n ON r.negocio_id = n.id
        WHERE r.id = ?
    ''', (rifa_id,))
    admin = cursor.fetchone()
    conn.close()

    if not admin:
        return jsonify({"error": "No se encontró número de WhatsApp del administrador"}), 404

    mensaje = (
        f"📢 *Nueva compra registrada*\n\n"
        f"👤 Cliente: {cliente}\n"
        f"📱 WhatsApp: {whatsapp}\n"
        f"🔢 Números seleccionados: {', '.join(numeros)}\n\n"
        f"Revisa tu panel para confirmar el pago."
    )

    try:
        enviar_mensaje_whatsapp(admin['whatsapp'], mensaje)
        return jsonify({"mensaje": "✅ Notificación enviada correctamente"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cliente_routes.route('/marcar-numero-vendido', methods=['POST'])
def marcar_numero_vendido():
    data = request.get_json()
    rifa_id = data.get("rifa_id")
    numero = data.get("numero")

    conn = obtener_conexion()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE numeros_rifa
        SET estado = 'vendido'
        WHERE rifa_id = ? AND numero = ?
    ''', (rifa_id, numero))

    conn.commit()
    conn.close()

    return jsonify({"mensaje": f"✅ El número {numero} fue marcado como VENDIDO"})



