from flask import Flask, render_template, session
from routes.auth_routes import auth_routes
from routes.superadmin_routes import superadmin_routes
from routes.cliente_routes import cliente_routes
from flask import Flask, render_template, request, redirect, url_for, session
from database.conexion import obtener_conexion  # Asegúrate de tener esto arriba
from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
import sqlite3
from flask import request, render_template
from PIL import Image, ImageDraw, ImageFont
import os
from dotenv import load_dotenv
load_dotenv()
from twilio.rest import Client
from models.rifa import obtener_rifas_por_slug, obtener_datos_ganador
from utils.whatsapp_api import enviar_mensaje_whatsapp
from routes.admin_routes import admin_routes
from flask import jsonify
import psycopg2
import psycopg2.extras


app = Flask(__name__)

app.register_blueprint(admin_routes, url_prefix='/admin')

app.register_blueprint(cliente_routes)

app.register_blueprint(superadmin_routes)


# Clave secreta de sesión
app.secret_key = os.getenv('SECRET_KEY', 'clave_por_defecto')

# Registro de Blueprints
app.register_blueprint(auth_routes)

# Página de error 404
@app.errorhandler(404)
def pagina_no_encontrada(e):
    return render_template("error.html", mensaje="Página no encontrada"), 404

# Página de error general
@app.errorhandler(500)
def error_interno(e):
    return render_template("error.html", mensaje="Error interno del servidor"), 500

# Inicio directo si no está logueado
@app.route('/inicio')
def inicio():
    return render_template("login.html")

@app.route('/rifas-disponibles')
def rifas_disponibles():
    conexion = obtener_conexion()
    cursor = conexion.cursor()
    cursor.execute("SELECT id, nombre_rifa, descripcion, precio_numero, fecha_sorteo FROM rifas")
    rifas = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]
    conexion.close()
    return render_template('ver_rifas_disponibles.html', rifas=rifas)

@app.route("/reservar-numeros", methods=["POST"])
def reservar_numeros():
    numeros = request.form["numeros_seleccionados"].split(",")
    nombre = request.form["nombre"]
    whatsapp = request.form["whatsapp"]
    metodo_pago = request.form["metodo_pago"]
    fecha_reserva = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Por ahora usamos ID de rifa 1 como ejemplo. Luego lo pasamos dinámico.
    rifa_id = int(request.form.get("rifa_id"))

    conn = sqlite3.connect("geica_controlrifas.db")
    c = conn.cursor()

    for numero in numeros:
        c.execute('''
            INSERT INTO reservas (rifa_id, numero, nombre, whatsapp, metodo_pago, fecha_reserva, estado)
            VALUES (?, ?, ?, ?, ?, ?, 'pendiente')
        ''', (rifa_id, numero.strip(), nombre, whatsapp, metodo_pago, fecha_reserva))

        # También actualizamos estado en tabla numeros_rifa si quieres bloquear visualmente
        c.execute('''
            UPDATE numeros_rifa SET estado = 'pendiente'
            WHERE rifa_id = ? AND numero = ?
        ''', (rifa_id, numero.strip()))

    conn.commit()
    conn.close()

    return render_template("mensaje.html", mensaje="✅ Números reservados correctamente. Tienes 1 hora para completar el pago o se liberarán.")

@app.route("/guardar-cuentas-negocio", methods=["POST"])
def guardar_cuentas_negocio():
    datos = request.get_json()
    nequi = datos.get("nequi", "").strip()
    daviplata = datos.get("daviplata", "").strip()
    pse = datos.get("pse", "").strip()

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        # Usamos el negocio_id desde la sesión (o por defecto 1)
        negocio_id = session.get("negocio_id", 1)

        # Verifica si el negocio existe
        cursor.execute("SELECT id FROM negocio WHERE id = ?", (negocio_id,))
        existe = cursor.fetchone()

        if existe:
            # Actualizamos los campos dentro de la tabla 'negocio'
            cursor.execute("""
                UPDATE negocio SET
                    numero_nequi = ?,
                    numero_daviplata = ?,
                    cuenta_pse = ?
                WHERE id = ?
            """, (nequi, daviplata, pse, negocio_id))
            conexion.commit()
            conexion.close()
            return {"success": True}
        else:
            conexion.close()
            return {"success": False, "error": "Negocio no encontrado"}
    except Exception as e:
        print(f"Error al guardar cuentas: {e}")
        return {"success": False}


@app.route("/procesar-compra", methods=["POST"])
def procesar_compra():
    numeros = request.form["numeros_seleccionados"].split(",")
    nombre = request.form["nombre"]
    whatsapp = request.form["whatsapp"]
    metodo_pago = request.form["metodo_pago"]
    accion = request.form["accion"]  # puede ser nequi, daviplata, pse o reservar
    rifa_id = 1  # ← cámbialo si es dinámico
    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect("geica_controlrifas.db")
    c = conn.cursor()

    if accion == "reservar":
        for numero in numeros:
            c.execute("""
                INSERT INTO reservas (rifa_id, numero, nombre, whatsapp, metodo_pago, fecha_reserva, estado)
                VALUES (?, ?, ?, ?, ?, ?, 'pendiente')
            """, (rifa_id, numero.strip(), nombre, whatsapp, metodo_pago, fecha_actual))
            c.execute("""
                UPDATE numeros_rifa SET estado = 'pendiente'
                WHERE rifa_id = ? AND numero = ?
            """, (rifa_id, numero.strip()))

        mensaje = "⏳ Números reservados correctamente. Tienes 1 hora para completar el pago o se liberarán."
        ticket_url = None  # no hay ticket si solo reservó

    else:
        for numero in numeros:
            c.execute("""
                INSERT INTO compras (rifa_id, numero, nombre_cliente, whatsapp_cliente, estado_pago, fecha_compra)
                VALUES (?, ?, ?, ?, 'pagado', ?)
            """, (rifa_id, numero.strip(), nombre, whatsapp, fecha_actual))
            c.execute("""
                UPDATE numeros_rifa SET estado = 'vendido', nombre_cliente = ?, whatsapp_cliente = ?
                WHERE rifa_id = ? AND numero = ?
            """, (nombre, whatsapp, rifa_id, numero.strip()))

        # Generar imagen del ticket
        ticket_path = generar_ticket_imagen(nombre, whatsapp, numeros, "Rifa Premium")
        ticket_url = ticket_path.split("static/")[1]    

        mensaje = f"🎉 ¡Gracias {nombre}! Tus números han sido pagados exitosamente. Pronto recibirás tu ticket por WhatsApp."

    conn.commit()
    conn.close()
    
    enviar_ticket_whatsapp(nombre, whatsapp, numeros, ticket_path)

    return render_template("mensaje.html", mensaje=mensaje, ticket_url=ticket_url)

@app.route('/panel_superadmin')
def panel_superadmin():
    if 'rol' not in session or session['rol'] != 'superadmin':
        return redirect(url_for('inicio'))

    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, propietario_nombre, propietario_telefono, ciudad, slug, activo FROM negocio ORDER BY id DESC")
    negocios = cursor.fetchall()
    conn.close()

    return render_template('panel_superadmin.html', negocios=negocios)

# Enviar el ticket por WhatsApp
def enviar_ticket_whatsapp(nombre, whatsapp_destino, numeros, ticket_path, nombre_rifa):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
    client = Client(account_sid, auth_token)

    texto = f"🎟️ Hola {nombre}, gracias por participar en la rifa \"{nombre_rifa}\".\nTus números: {', '.join(numeros)}\n¡Mucha suerte! 🍀"
    numero_formateado = f"whatsapp:+57{whatsapp_destino[-10:]}"  # Colombia

    try:
        # Enviar mensaje de texto
        client.messages.create(
            from_=twilio_number,
            to=numero_formateado,
            body=texto
        )

        # Enviar imagen (el ticket)
        ticket_url = request.url_root + ticket_path.replace("static/", "static/")
        client.messages.create(
            from_=twilio_number,
            to=numero_formateado,
            media_url=[ticket_url]
        )
    except Exception as e:
        print(f"Error enviando WhatsApp: {e}")

# Ruta general para redirigir slugs directamente
@app.route('/<slug>')
def redireccionar_slug(slug):
    from flask import redirect
    return redirect(url_for('admin.ver_rifas_admin_slug', slug=slug))


# -------- Función para generar ticket visual --------
def generar_ticket_imagen(nombre, whatsapp, numeros, nombre_rifa):
    ancho = 800
    alto = 500
    fondo_color = (255, 255, 255)
    texto_color = (0, 0, 0)
    ruta_fuente = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"  # cambia si usas Windows

    img = Image.new("RGB", (ancho, alto), fondo_color)
    draw = ImageDraw.Draw(img)

    try:
        fuente = ImageFont.truetype(ruta_fuente, 28)
        fuente_titulo = ImageFont.truetype(ruta_fuente, 34)
    except:
        fuente = fuente_titulo = None

    draw.text((30, 30), f"🎟️ Ticket de Participación", fill=texto_color, font=fuente_titulo)
    draw.text((30, 90), f"Rifa: {nombre_rifa}", fill=texto_color, font=fuente)
    draw.text((30, 140), f"Comprador: {nombre}", fill=texto_color, font=fuente)
    draw.text((30, 190), f"WhatsApp: {whatsapp}", fill=texto_color, font=fuente)
    draw.text((30, 240), f"Números: {', '.join(numeros)}", fill=(0, 51, 102), font=fuente)
    draw.text((30, 320), f"✅ ¡Gracias por tu compra y mucha suerte!", fill=(0, 128, 0), font=fuente)

    carpeta = "static/tickets"
    os.makedirs(carpeta, exist_ok=True)

    nombre_archivo = f"ticket_{nombre.replace(' ', '_')}_{whatsapp[-4:]}.png"
    path_guardado = os.path.join(carpeta, nombre_archivo)

    img.save(path_guardado)
    return path_guardado
    
def generar_ticket_imagen(nombre, whatsapp, numeros, nombre_rifa):
    ancho = 800
    alto = 500
    fondo_color = (255, 255, 255)
    texto_color = (0, 0, 0)
    ruta_fuente = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"  # Linux
    # Si estás en Windows, cambia por: "arial.ttf" o usa ruta absoluta

    img = Image.new("RGB", (ancho, alto), fondo_color)
    draw = ImageDraw.Draw(img)

    try:
        fuente = ImageFont.truetype(ruta_fuente, 28)
        fuente_titulo = ImageFont.truetype(ruta_fuente, 34)
    except:
        fuente = fuente_titulo = None  # fallback si no hay fuente

    draw.text((30, 30), f"🎟️ Ticket de Participación", fill=texto_color, font=fuente_titulo)
    draw.text((30, 90), f"Rifa: {nombre_rifa}", fill=texto_color, font=fuente)
    draw.text((30, 140), f"Comprador: {nombre}", fill=texto_color, font=fuente)
    draw.text((30, 190), f"WhatsApp: {whatsapp}", fill=texto_color, font=fuente)
    draw.text((30, 240), f"Números: {', '.join(numeros)}", fill=(0, 51, 102), font=fuente)
    draw.text((30, 320), f"✅ ¡Gracias por tu compra y mucha suerte!", fill=(0, 128, 0), font=fuente)

    carpeta = "static/tickets"
    os.makedirs(carpeta, exist_ok=True)

    nombre_archivo = f"ticket_{nombre.replace(' ', '_')}_{whatsapp[-4:]}.png"
    path_guardado = os.path.join(carpeta, nombre_archivo)

    img.save(path_guardado)
    return path_guardado

@app.route('/historial-ganadores/<slug>')
def historial_ganadores(slug):
    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row  # 🔥 Esto permite acceder por nombre de columna como en un diccionario
    cursor = conn.cursor()

    # Buscar el negocio por slug
    cursor.execute("""
        SELECT * FROM negocio WHERE slug = ?
    """, (slug,))
    negocio = cursor.fetchone()

    if not negocio:
        conn.close()
        return "Negocio no encontrado"

    # Consultar historial de ganadores usando JOIN con compras
    cursor.execute("""
        SELECT 
            rifas.nombre_rifa,
            rifas.numero_ganador,
            compras.nombre_cliente AS nombre_ganador,
            rifas.fecha_sorteo
        FROM 
            rifas
        JOIN 
            compras ON rifas.id = compras.rifa_id AND rifas.numero_ganador = compras.numero
        WHERE 
            rifas.negocio_id = ? AND rifas.numero_ganador IS NOT NULL
        ORDER BY 
            rifas.fecha_sorteo DESC
    """, (negocio['id'],))
    
    historial = cursor.fetchall()
    conn.close()

    return render_template('historial_ganadores_admin.html', historial=historial, negocio=negocio)



@app.route('/panel-compras/<slug>')
def panel_compras(slug):
    rifas = obtener_rifas_por_slug(slug)
    if not rifas:
        return render_template("mensaje.html", mensaje="❌ No hay rifas activas disponibles para este negocio.")

    conn = obtener_conexion()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Consulta completa del negocio
    cursor.execute("SELECT * FROM negocio WHERE slug = ?", (slug,))
    negocio = cursor.fetchone()
    conn.close()

    return render_template(
        'ver_rifa_comprador.html',
        rifas=rifas,
        numeros_por_rifa=None,
        negocio=negocio,   # ✅ Ahora sí se pasa el objeto completo
        admin=False
    )


@app.route('/notificar-ganador/<slug>')
def notificar_ganador(slug):
    rifas = obtener_rifas_por_slug(slug)
    return render_template("notificar_ganador.html", rifas=rifas, slug=slug)

@app.route('/enviar-notificacion-ganador', methods=["POST"])
def enviar_notificacion_ganador():
    slug = request.form.get("slug")
    numero_ganador = request.form.get("numero_ganador")

    if not slug or not numero_ganador:
        return render_template("mensaje.html", mensaje="⚠️ Debes seleccionar una rifa y escribir el número ganador.")

    ganador = obtener_datos_ganador(slug, numero_ganador)
    print("[✅ GANADOR ENCONTRADO]", ganador)

    if not ganador:
        return render_template("mensaje.html", mensaje="❌ No se encontró al comprador de ese número.")

    ganador = dict(ganador)  # 🔧 Cambio único: acceso limpio a los campos

    if 'whatsapp_cliente' not in ganador or not ganador['whatsapp_cliente']:
        return render_template("mensaje.html", 
            mensaje="❌ WhatsApp del ganador no disponible.", 
            negocio_slug=slug)

    # 🔔 Mensaje personalizado
    mensaje = f"""🎉 ¡Felicidades {ganador['nombre_cliente']}! Has ganado la rifa *{ganador['nombre_rifa']}* con el número {ganador['numero']}.\n📅 Fecha: {ganador['fecha_compra']}\n¡Nos pondremos en contacto contigo para la entrega del premio! 🏆✨"""

    numero_sin_prefijo = ganador['whatsapp_cliente'][-10:]
    destino = f"57{numero_sin_prefijo}"

    print("[📨 MENSAJE QUE SE ENVIARÁ]", mensaje)
    print("[📱 NÚMERO DESTINO]", destino)

    try:
        enviar_mensaje_whatsapp(destino, mensaje)
        print("[✅ WHATSAPP ENVIADO CON ÉXITO]")
    except Exception as e:
        print("[❌ ERROR WHATSAPP GANADOR]", e)

    return render_template("mensaje.html", 
        mensaje=f"✅ El ganador fue notificado exitosamente al número {ganador['whatsapp_cliente']}.", 
        negocio_slug=slug)

@app.route('/buscar-ganador')
def buscar_ganador():
    rifa_id = request.args.get("rifa_id")
    numero = request.args.get("numero")

    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT nombre_cliente, whatsapp_cliente
        FROM compras
        WHERE rifa_id = ? AND numero = ?
    """, (rifa_id, numero))
    resultado = cursor.fetchone()
    conn.close()

    if resultado:
        return jsonify({
            "encontrado": True,
            "nombre": resultado[0],
            "telefono": resultado[1]
        })
    else:
        return jsonify({"encontrado": False})
    
@app.route('/rifa/<slug>')
def mostrar_rifa(slug):
    conn = obtener_conexion()
    cursor = conn.cursor()

    # Buscar la rifa usando el nuevo slug único
    cursor.execute("SELECT * FROM rifas WHERE slug = ?", (slug,))
    rifa = cursor.fetchone()

    if not rifa:
        return render_template("error.html", mensaje="❌ Rifa no encontrada.")

    # Obtener información del negocio
    cursor.execute("SELECT * FROM negocio WHERE id = ?", (rifa['negocio_id'],))
    negocio = cursor.fetchone()

    # Obtener números disponibles para esta rifa
    cursor.execute("SELECT * FROM numeros_rifa WHERE rifa_id = ?", (rifa['id'],))
    numeros = cursor.fetchall()


    conn.close()

    return render_template(
        'ver_rifa_comprador.html',
        rifa=rifa,
        numeros=numeros,
        negocio=negocio,
        fecha_actual=datetime.now().date().isoformat(),
        admin=False
    )

       

if __name__ == '__main__':
    app.run(debug=True, port=5000)
