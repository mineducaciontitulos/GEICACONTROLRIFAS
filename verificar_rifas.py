import psycopg2

conn = psycopg2.connect(
    host="dpg-d2ivijp5pdvs738ej5b0-a.oregon-postgres.render.com",
    database="geica_controlrifas",
    user="geicaadmin",
    password="Sv4CuoXa9oHGyRGb0Lvxny1yG1eKDkDM",
    port="5432",
    sslmode="require"
)

cur = conn.cursor()
cur.execute("SELECT id, nombre_rifa, slug, fecha_creacion FROM rifas ORDER BY id DESC LIMIT 5;")
rifas = cur.fetchall()

print("\nÚltimas rifas creadas:")
for rifa in rifas:
    print(f"ID: {rifa[0]} | Nombre: {rifa[1]} | Slug: {rifa[2]} | Fecha: {rifa[3]}")

cur.close()
conn.close()
