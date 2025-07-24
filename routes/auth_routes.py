from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models.negocio import validar_login_usuario
from models.negocio import obtener_negocio_por_id

auth_routes = Blueprint('auth', __name__)

@auth_routes.route('/')
def inicio():
    return redirect(url_for('auth.login'))


@auth_routes.route('/login', methods=['GET', 'POST'])
def login():
    session.clear()
    if request.method == 'POST':
        usuario = request.form['usuario']
        clave = request.form['clave']

        user = validar_login_usuario(usuario, clave)
        if user:
            session['negocio_id'] = user['negocio_id']
            negocio = obtener_negocio_por_id(user['negocio_id'])

            if negocio and negocio['activo'] == 1:
               return redirect(url_for('auth.panel_negocio'))  # ← CAMBIO AQUÍ

            else:
                flash("Este negocio está inactivo o no existe.")
        
    return render_template('login.html')

@auth_routes.route('/panel-negocio')
def panel_negocio():
    if 'negocio_id' not in session:
        flash("Debes iniciar sesión.")
        return redirect(url_for('auth.login'))

    negocio = obtener_negocio_por_id(session['negocio_id'])

    if negocio:
        return render_template('dashboard_admin.html', negocio=negocio, session=session)
    else:
        flash("Negocio no encontrado.")
        return redirect(url_for('auth.login'))


@auth_routes.route('/logout')
def logout():
    session.clear()
    flash('👋 Sesión cerrada correctamente.', 'info')
    return redirect(url_for('auth.login'))
