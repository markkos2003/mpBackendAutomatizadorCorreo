from supabase import create_client, Client
from app.config import settings

if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
    raise ValueError("Faltan las credenciales de Supabase en el archivo .env")

# Cliente único de conexión a Supabase
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)