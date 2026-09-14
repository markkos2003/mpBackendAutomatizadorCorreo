import io
import re
import json
import requests
from datetime import datetime
from PIL import Image
import pytesseract

from app.config import settings
from google import genai

# --- VALIDANTE DE RESULTADOS ---
def _es_resultado_valido(datos: dict) -> bool:
    """
    Verifica si el diccionario extraído contiene los datos mínimos de SUNAT.
    """
    if not datos:
        return False
    
    tiene_ruc = bool(datos.get("ruc_emisor"))
    tiene_serie = bool(datos.get("serie_numero"))
    tiene_monto = datos.get("monto_total", 0.0) > 0.0

    return tiene_ruc and tiene_serie and tiene_monto


def _extraer_datos_con_regex(texto: str, origen: str) -> dict:
    if not texto:
        return None

    txt_upper = texto.upper()

    # Regex de verificación
    ruc_match = re.search(r'\b(10|20)\d{9}\b', texto)
    serie_match = re.search(r'\b([FBE][A-Z0-9]{3})[\s\-_]*(\d{1,8})\b', texto)
    monto_match = re.search(r'(?:TOTAL|TOTAL\s+A\s+PAGAR|IMPORTE\s+TOTAL)\s*[:\.]?\s*(?:S/|\$)?\s*([\d,]+\.\d{2})', texto, re.IGNORECASE)

    # Si falta alguno de los 3 datos, la lectura falló
    if not (ruc_match and serie_match and monto_match):
        return None

    monto_total = float(monto_match.group(1).replace(',', ''))
    if monto_total <= 0.0:
        return None

    lineas = [l.strip() for l in texto.split('\n') if l.strip()]
    razon_social = lineas[0] if lineas else "EMISOR PROCESADO"

    fecha_match = re.search(r'\b(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}[/-]\d{2}[/-]\d{2})\b', texto)
    fecha_str = datetime.now().strftime('%Y-%m-%d')
    if fecha_match:
        raw_f = fecha_match.group(1).replace('/', '-')
        parts = raw_f.split('-')
        fecha_str = f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts[0]) == 2 else raw_f

    m_igv = round(monto_total - (monto_total / 1.18), 2)
    m_gravadas = round(monto_total - m_igv, 2)

    return {
        "ruc_emisor": ruc_match.group(0),
        "razon_social_emisor": razon_social[:100],
        "tipo_doc": "01" if "FACTURA" in txt_upper else "03",
        "serie_numero": f"{serie_match.group(1).upper()}-{serie_match.group(2).zfill(8)}",
        "fecha_emision": fecha_str,
        "moneda": "USD" if "$" in texto else "PEN",
        "op_gravadas": m_gravadas,
        "igv": m_igv,
        "monto_total": monto_total,
        "estado_sunat": "ACEPTADO",
        "origen_archivo": origen
    }

# --- HERRAMIENTA 1: Tesseract + Pillow ---
def _parsear_tesseract(imagen_bytes: bytes) -> dict:
    try:
        image = Image.open(io.BytesIO(imagen_bytes)).convert('L')
        texto = pytesseract.image_to_string(image, lang='spa')
        return _extraer_datos_con_regex(texto, "PYTESSERACT")
    except Exception:
        return None

# --- HERRAMIENTA 2: OCR.space API ---
def _parsear_ocr_space(imagen_bytes: bytes) -> dict:
    try:
        payload = {'apikey': settings.OCRAPI, 'language': 'spa', 'OCREngine': '2'}
        files = {'file': ('comprobante.jpg', imagen_bytes, 'image/jpeg')}
        res = requests.post('https://api.ocr.space/parse/image', files=files, data=payload, timeout=7)
        if res.status_code == 200:
            result = res.json()
            if result.get("ParsedResults"):
                texto = result["ParsedResults"][0].get("ParsedText", "")
                return _extraer_datos_con_regex(texto, "OCR_SPACE")
    except Exception:
        return None
    return None

# --- HERRAMIENTA 3: Gemini IA (Último Recurso) ---
def _parsear_gemini_ia(imagen_bytes: bytes) -> dict:
    try:
        client = genai.Client(api_key=settings.APIGEM)
        prompt = """
        Extrae la información de este comprobante SUNAT en JSON estricto:
        - ruc_emisor (11 dígitos)
        - razon_social_emisor (string)
        - tipo_doc ("01" o "03")
        - serie_numero (ej: "F001-00000123")
        - fecha_emision ("YYYY-MM-DD")
        - moneda ("PEN" o "USD")
        - monto_total (float)
        Devuelve SOLO el JSON sin markdown.
        """
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=[{"mime_type": "image/jpeg", "data": imagen_bytes}, prompt]
        )
        data = json.loads(response.text.replace("```json", "").replace("```", "").strip())
        
        monto_total = float(data.get("monto_total", 0.0))
        m_igv = round(monto_total - (monto_total / 1.18), 2)
        
        return {
            "ruc_emisor": str(data.get("ruc_emisor", "")),
            "razon_social_emisor": str(data.get("razon_social_emisor", "EMISOR IA"))[:100],
            "tipo_doc": str(data.get("tipo_doc", "01")),
            "serie_numero": str(data.get("serie_numero", "")),
            "fecha_emision": str(data.get("fecha_emision", datetime.now().strftime('%Y-%m-%d'))),
            "moneda": str(data.get("moneda", "PEN")),
            "op_gravadas": round(monto_total - m_igv, 2),
            "igv": m_igv,
            "monto_total": monto_total,
            "estado_sunat": "ACEPTADO",
            "origen_archivo": "IMAGEN"
        }
    except Exception:
        return None


# --- FUNCIÓN PRINCIPAL ---
