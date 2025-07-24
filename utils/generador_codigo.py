import re
import unicodedata

def generar_slug_rifa(nombre_rifa):
    """
    Genera un slug amigable para URLs basado en el nombre de la rifa.
    Ejemplo: "Rifa del Carro 2025" -> "rifa-del-carro-2025"
    """
    # Normaliza el texto (quita tildes)
    nombre = unicodedata.normalize('NFKD', nombre_rifa).encode('ascii', 'ignore').decode('utf-8')
    # Convierte a minúsculas, reemplaza espacios y elimina caracteres no deseados
    nombre = re.sub(r'[^\w\s-]', '', nombre).strip().lower()
    slug = re.sub(r'[-\s]+', '-', nombre)
    return slug
