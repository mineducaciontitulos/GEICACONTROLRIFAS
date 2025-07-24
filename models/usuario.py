from database.conexion import obtener_conexion



def crear_usuario(negocio_id, usuario, clave):
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO usuarios (negocio_id, usuario, clave)
        VALUES (?, ?, ?)
    ''', (negocio_id, usuario, clave))
    conn.commit()
    conn.close()

def validar_login(usuario, clave):
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM usuarios WHERE usuario = ? AND clave = ?', (usuario, clave))
    user = cursor.fetchone()
    conn.close()
    return user
