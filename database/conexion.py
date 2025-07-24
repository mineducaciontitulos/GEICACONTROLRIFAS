import sqlite3

DATABASE_NAME = "geica_controlrifas.db"

def obtener_conexion():
    conexion = sqlite3.connect(DATABASE_NAME)
    conexion.row_factory = sqlite3.Row
    return conexion

def obtener_conexion():
    return sqlite3.connect('geica_controlrifas.db', timeout=10)  # espera hasta 10s si hay bloqueo

