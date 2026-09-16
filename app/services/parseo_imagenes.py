import io
import re
import json
import requests
import zxingcpp
from datetime import datetime
from PIL import Image
from google.genai import types
from PIL import Image, ImageEnhance

from app.config import settings
from google import genai

# 1. Instancia global reutilizable de Gemini (Mantiene abierta la conexión SSL)
client_gemini = genai.Client(api_key=settings.APIGEM)


def _optimizar_imagen(imagen_bytes: bytes) -> bytes:
    """
    Reduce imágenes pesadas de celular a un máximo de 1280px con 80% de calidad.
    Mantiene la RAM de Render por debajo de los 300 MB.
    """
    try:
        img = Image.open(io.BytesIO(imagen_bytes))
        img.thumbnail((1280, 1280))
        buffer = io.BytesIO()
        img.convert('RGB').save(buffer, format="JPEG", quality=80)
        return buffer.getvalue()
    except Exception:
        return imagen_bytes


def _es_resultado_valido(datos: dict) -> bool:
    """
    Garantiza que se hayan capturado los 3 campos mínimos obligatorios de SUNAT.
    """
    if not datos:
        return False
    tiene_ruc = bool(datos.get("ruc_emisor")) and len(datos.get("ruc_emisor", "")) == 11
    tiene_serie = bool(datos.get("serie_numero"))
    tiene_monto = float(datos.get("monto_total", 0.0)) > 0.0
    return tiene_ruc and tiene_serie and tiene_monto


# --- NIVEL 0: LECTOR DE CÓDIGO QR SUNAT (0.05 segundos, 100% Precisión) ---
def _intentar_leer_qr_sunat(imagen_bytes: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(imagen_bytes))
        
        options = zxingcpp.ReaderOptions()
        options.try_harder = True
        options.try_rotate = True
        options.formats = zxingcpp.BarcodeFormat.QRCode

        barcodes = zxingcpp.read_barcodes(img, options)

        if not barcodes:
            img_mejora = ImageEnhance.Contrast(img).enhance(2.5)
            barcodes = zxingcpp.read_barcodes(img_mejora, options)

        for barcode in barcodes:
            texto_qr = barcode.text
            if texto_qr and texto_qr.count('|') >= 5:
                partes = [p.strip() for p in texto_qr.split('|')]
                
                ruc = partes[0]
                tipo_doc = partes[1].zfill(2)
                serie = partes[2]
                numero = partes[3]
                igv = float(partes[4]) if partes[4] else 0.0
                monto_total = float(partes[5]) if partes[5] else 0.0
                fecha = partes[6] if len(partes) > 6 and partes[6] else datetime.now().strftime('%Y-%m-%d')

                if len(ruc) == 11 and monto_total > 0:
                    return {
                        "ruc_emisor": ruc,
                        #  Valor dinámico universal para cualquier proveedor
                        "razon_social_emisor": f"EMISOR RUC {ruc}",
                        "tipo_doc": tipo_doc,
                        "serie_numero": f"{serie.upper()}-{numero.zfill(8)}",
                        "fecha_emision": fecha,
                        "moneda": "PEN",
                        "op_gravadas": round(monto_total - igv, 2),
                        "igv": igv,
                        "monto_total": monto_total,
                        "estado_sunat": "ACEPTADO",
                        "origen_archivo": "CODIGO_QR"
                    }
    except Exception:
        return None
    return None

# --- NIVEL 1: OCR.space API + REGEX (Gratuito con Timeout de 3s) ---
def _extraer_datos_con_regex(texto: str, origen: str) -> dict:
    if not texto:
        return None

    txt_upper = texto.upper()
    ruc_match = re.search(r'\b(10|20)\d{9}\b', texto)
    serie_match = re.search(r'\b([FBE][A-Z0-9]{3})[\s\-_]*(\d{1,8})\b', texto)
    monto_match = re.search(r'(?:TOTAL|IMPORTE\s+TOTAL|TOTAL\s+A\s+PAGAR)\s*[:\.]?\s*(?:S/|\$)?\s*([\d,]+\.\d{2})', texto, re.IGNORECASE)

    if not (ruc_match and serie_match and monto_match):
        return None

    try:
        monto_total = float(monto_match.group(1).replace(',', ''))
        if monto_total <= 0.0:
            return None
    except ValueError:
        return None

    lineas = [l.strip() for l in texto.split('\n') if l.strip()]
    razon_social = lineas[0] if lineas else "EMISOR DESCONOCIDO"

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


def _parsear_ocr_space(imagen_bytes: bytes) -> dict:
    try:
        payload = {'apikey': settings.OCRAPI, 'language': 'spa', 'OCREngine': '2'}
        files = {'file': ('comprobante.jpg', imagen_bytes, 'image/jpeg')}
        
        res = requests.post('https://api.ocr.space/parse/image', files=files, data=payload, timeout=3)
        if res.status_code == 200:
            result = res.json()
            if result.get("ParsedResults"):
                texto = result["ParsedResults"][0].get("ParsedText", "")
                return _extraer_datos_con_regex(texto, "OCR_SPACE")
    except Exception:
        return None
    return None


# --- NIVEL 2: GEMINI IA (Visión Multimodal para Imágenes Borrosas/Complejas) ---
def _parsear_gemini_ia(imagen_bytes: bytes) -> dict:
    try:
        prompt = """
        Analiza la imagen adjunta (puede ser una Factura, Boleta, Ticket o Voucher/Formulario de Pago tributario) y extrae los siguientes datos en JSON estricto:

        - ruc_emisor: RUC del emisor, contribuyente o arrendador (cadena de 11 dígitos).
        - razon_social_emisor: Nombre comercial o Razón Social del emisor/banco.
        - tipo_doc: "01" si es Factura, "03" si es Boleta, "10" si es Voucher/Formulario/Ticket de pago.
        - serie_numero: Serie y número correlativo (ej: "F001-00000123"), o en su defecto el Número de Orden / Operación.
        - fecha_emision: Fecha de emisión o pago en formato "YYYY-MM-DD".
        - moneda: "PEN" o "USD".
        - monto_total: El importe total pagado como número flotante (limpia asteriscos o símbolos de moneda).

        Responde ÚNICAMENTE con el objeto JSON estructurado, sin bloques de código ```json ni texto adicional.
        
        """
        imagen_part = types.Part.from_bytes(
            data=imagen_bytes,
            mime_type="image/jpeg",
        )
        
        # Configuración para forzar respuesta JSON estricta
        config = types.GenerateContentConfig(
            response_mime_type="application/json"
        )
        
        response = client_gemini.models.generate_content(
            model='gemini-2.5-flash',
            contents=[imagen_part, prompt],
            config=config
        )
        
        data = json.loads(response.text)
        
        # --- VALIDACIÓN SEGURA DE MONTO (Evita el error 'NoneType') ---
        raw_monto = data.get("monto_total")
        if raw_monto is None:
            return None
            
        try:
            monto_total = float(raw_monto)
        except (ValueError, TypeError):
            return None

        if monto_total <= 0.0:
            return None
        # ---------------------------------------------------------------
        
        m_igv = round(monto_total - (monto_total / 1.18), 2)
        
        return {
            "ruc_emisor": str(data.get("ruc_emisor", "")).strip(),
            "razon_social_emisor": str(data.get("razon_social_emisor", "EMISOR IA"))[:100],
            "tipo_doc": str(data.get("tipo_doc", "01")),
            "serie_numero": str(data.get("serie_numero", "")).strip().upper(),
            "fecha_emision": str(data.get("fecha_emision", datetime.now().strftime('%Y-%m-%d'))),
            "moneda": str(data.get("moneda", "PEN")),
            "op_gravadas": round(monto_total - m_igv, 2),
            "igv": m_igv,
            "monto_total": monto_total,
            "estado_sunat": "ACEPTADO",
            "origen_archivo": "IMAGEN"
        }
    except Exception as e:
        print(f" Error al procesar con Gemini IA: {str(e)}")
        return None


# --- ORQUESTADOR PRINCIPAL PARA IMÁGENES ---
def procesar_imagen_comprobante(imagen_bytes: bytes) -> dict:
    """
    Procesa la imagen: 
    1. Filtra firmas/logos por dimensiones.
    2. Decodifica QR localmente (Gratis y ultra rápido).
    3. Si es una foto/ticket sin QR legible, consulta a Gemini IA.
    """
    if not imagen_bytes:
        return None

    # --- FILTRO 1: Descartar firmas e iconos por dimensiones ---
    try:
        img_temp = Image.open(io.BytesIO(imagen_bytes))
        ancho, alto = img_temp.size
        if ancho < 400 or alto < 400 or (ancho / alto > 3.0):
            print(f"  └─ ⏭ Imagen ignorada ({ancho}x{alto}px): Es un logo o icono de firma.")
            return None
    except Exception:
        return None

    if len(imagen_bytes) < 35000:
        return None

    # Reducir peso en RAM
    img_optimizada = _optimizar_imagen(imagen_bytes)

    # --- NIVEL 1: Lectura Local por Código QR SUNAT (0.05s | Gratis) ---
    res_qr = _intentar_leer_qr_sunat(img_optimizada)
    if _es_resultado_valido(res_qr):
        print("  └─  Leído exitosamente mediante Código QR SUNAT")
        return res_qr

    # --- NIVEL 2: Gemini IA (Solo para fotos/tickets reales sin QR legible) ---
    print("  └─  Rescatando datos con Gemini IA...")
    res_ia = _parsear_gemini_ia(img_optimizada)
    if _es_resultado_valido(res_ia):
        print("  └─  Leído exitosamente mediante Gemini IA")
        return res_ia

    print("  └─  La imagen no contenía un comprobante legible válido.")
    return None