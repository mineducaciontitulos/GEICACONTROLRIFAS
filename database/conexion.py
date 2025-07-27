import sqlite3

DATABASE_NAME = "geica_controlrifas.db"

def obtener_conexion():
    conexion = sqlite3.connect(DATABASE_NAME)
    conexion.row_factory = sqlite3.Row  # Esto sí lo acepta SQLite
    return conexion
