import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Cargar las variables del archivo .env
load_dotenv()

# Leer la URL y la Key de las variables de entorno
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Validar que existan las credenciales
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Faltan las credenciales de Supabase en el archivo .env")

# Crear el cliente de conexión
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

print("¡Conexión exitosa a Supabase!")