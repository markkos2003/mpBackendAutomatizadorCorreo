import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

print(f"URL: {url}")
print(f"KEY (primeros 15 caracteres): {key[:15] if key else 'No encontrada'}")

supabase = create_client(url, key)

try:
    print("--- Probando consulta a 'comprobantes' ---")
    res1 = supabase.table("comprobantes").select("*").limit(1).execute()
    print(" Conexión exitosa a comprobantes:", res1.data)

    print("--- Probando consulta a 'user_tokens' ---")
    res2 = supabase.table("user_tokens").select("*").limit(1).execute()
    print(" Conexión exitosa a user_tokens:", res2.data)

except Exception as e:
    print(" Error de conexión:", type(e).__name__, "-", e)