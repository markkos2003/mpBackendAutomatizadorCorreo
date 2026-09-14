import io
import re
import base64
import xml.etree.ElementTree as ET
from typing import List
import gc
from datetime import datetime
from app.services.parseo_imagenes import _es_resultado_valido
from app.services.parseo_imagenes import _parsear_tesseract
from app.services.parseo_imagenes import _parsear_ocr_space
from app.services.parseo_imagenes import _parsear_gemini_ia


import pytesseract
from PIL import Image
import pdfplumber
import requests
import msal

from fastapi import FastAPI, BackgroundTasks, HTTPException, APIRouter
from fastapi.responses import StreamingResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.database import supabase
from app.config import settings


router = APIRouter(prefix="/api", tags=["comprobantes"])

# --- FUNCION AUXILIAR PARA VALIDAR SI ES UN COMPROBANTE VALIDO ---

def es_comprobante_valido(datos: dict) -> bool:
    """
    Retorna True SOLO si el diccionario contiene datos reales de un comprobante de SUNAT.
    """
    if not datos:
        return False
    
    ruc = datos.get("ruc_emisor", "")
    serie = datos.get("serie_numero", "")
    monto = datos.get("monto_total", 0.0)

    # 1. El RUC debe ser peruano (11 dígitos comenzando en 10 o 20)
    if not (len(ruc) == 11 and ruc.isdigit() and (ruc.startswith("10") or ruc.startswith("20"))):
        return False

    # 2. La serie no puede ser inventada ni genérica
    if serie in ["F001-0000", "DESCONOCIDO", "IMG-0000"] or serie.startswith("REC-"):
        return False

    # 3. Debe tener un monto asignado
    if monto <= 0.0:
        return False

    return True

# --- 1. PARSER XML SUNAT ---

def parsear_xml_sunat(xml_bytes: bytes) -> dict:
    try:
        root = ET.fromstring(xml_bytes)
        ns = {
            'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
            'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
        }

        ruc_elem = root.find('.//cac:AccountingSupplierParty//cbc:ID', ns)
        razon_elem = root.find('.//cac:AccountingSupplierParty//cbc:RegistrationName', ns)
        tipo_elem = root.find('.//cbc:InvoiceTypeCode', ns)
        serie_elem = root.find('.//cbc:ID', ns)
        fecha_elem = root.find('.//cbc:IssueDate', ns)
        moneda_elem = root.find('.//cbc:DocumentCurrencyCode', ns)
        monto_elem = root.find('.//cac:LegalMonetaryTotal/cbc:PayableAmount', ns)

        igv_val = 0.0
        for tax_subtotal in root.findall('.//cac:TaxTotal/cac:TaxSubtotal', ns):
            tax_scheme = tax_subtotal.find('.//cac:TaxScheme/cbc:ID', ns)
            if tax_scheme is not None and tax_scheme.text in ['1000', 'IGV']:
                tax_amt = tax_subtotal.find('cbc:TaxAmount', ns)
                if tax_amt is not None and tax_amt.text:
                    igv_val = float(tax_amt.text)
                    break

        m_total = float(monto_elem.text) if (monto_elem is not None and monto_elem.text) else 0.0
        m_igv = igv_val if igv_val > 0 else round(m_total - (m_total / 1.18), 2)
        m_gravadas = round(m_total - m_igv, 2) if m_total > 0 else 0.0

        return {
            "ruc_emisor": ruc_elem.text if (ruc_elem is not None and ruc_elem.text) else "DESCONOCIDO",
            "razon_social_emisor": razon_elem.text if (razon_elem is not None and razon_elem.text) else "SIN RAZON SOCIAL",
            "tipo_doc": tipo_elem.text if (tipo_elem is not None and tipo_elem.text) else "01",
            "serie_numero": serie_elem.text if (serie_elem is not None and serie_elem.text) else "F001-0000",
            "fecha_emision": fecha_elem.text if (fecha_elem is not None and fecha_elem.text) else datetime.now().strftime('%Y-%m-%d'),
            "moneda": moneda_elem.text if (moneda_elem is not None and moneda_elem.text) else "PEN",
            "op_gravadas": m_gravadas,
            "igv": m_igv,
            "monto_total": m_total,
            "estado_sunat": "ACEPTADO",
            "origen_archivo": "XML"
        }
    except Exception as e:
        return None

# --- 2. PARSER PDF SUNAT ---

def parsear_pdf_sunat(pdf_bytes: bytes) -> dict:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pagina_factura = None
            texto_factura = ""

            # 1. Buscar ÚNICAMENTE la página que tiene la factura
            for page in pdf.pages:
                txt = page.extract_text() or ""
                if "FACTURA ELECTRONICA" in txt.upper() or "BOLETA DE VENTA" in txt.upper():
                    pagina_factura = page
                    texto_factura = txt
                    break

            if not pagina_factura:
                return None

            # 2. Extraer RUC (Ej: 20612012190)[cite: 1]
            ruc_match = re.search(r'\b(10|20)\d{9}\b', texto_factura)
            if not ruc_match:
                return None
            ruc_val = ruc_match.group(0)

            # 3. Extraer Serie y Número (Ej: F001-00000087)[cite: 1]
            serie_match = re.search(r'\b([FBE][A-Z0-9]{3})[\s\-_]*(\d{1,8})\b', texto_factura)
            if not serie_match:
                return None
            serie_val = f"{serie_match.group(1).upper()}-{serie_match.group(2).zfill(8)}"

            # 4. Extraer Razón Social
            lineas = [l.strip() for l in texto_factura.split('\n') if l.strip()]
            razon_social = lineas[0] if lineas else "EMISOR DESCONOCIDO"

            # 5. Extraer Fecha
            fecha_match = re.search(r'\b(\d{2}[/-]\d{2}[/-]\d{4})\b', texto_factura)
            fecha_str = datetime.now().strftime('%Y-%m-%d')
            if fecha_match:
                raw_f = fecha_match.group(1).replace('/', '-')
                d, m, y = raw_f.split('-')
                fecha_str = f"{y}-{m}-{d}"

            # 6. Extraer Total recortando SOLO el cuadrante inferior derecho de esa página[cite: 1]
            w, h = pagina_factura.width, pagina_factura.height
            # Recortamos desde el 50% del ancho y 50% de la altura hacia abajo
            bloque_totales = pagina_factura.crop((w * 0.4, h * 0.5, w, h)).extract_text() or ""

            monto_total = 0.0
            # Buscamos la palabra TOTAL dentro del cuadrante de totales[cite: 1]
            m_match = re.search(r'TOTAL\s*[:\.]?\s*(?:S/|\$)?\s*([\d,]+\.\d{2})', bloque_totales, re.IGNORECASE)
            
            if m_match:
                num_clean = m_match.group(1).replace(',', '')
                monto_total = float(num_clean)

            if monto_total <= 0.0:
                return None

            # 7. Desglose de impuestos
            m_igv = round(monto_total - (monto_total / 1.18), 2)
            m_gravadas = round(monto_total - m_igv, 2)

            return {
                "ruc_emisor": ruc_val,
                "razon_social_emisor": razon_social[:100],
                "tipo_doc": "01" if "FACTURA" in texto_factura.upper() else "03",
                "serie_numero": serie_val,
                "fecha_emision": fecha_str,
                "moneda": "USD" if "$" in texto_factura else "PEN",
                "op_gravadas": m_gravadas,
                "igv": m_igv,
                "monto_total": monto_total,
                "estado_sunat": "ACEPTADO",
                "origen_archivo": "PDF"
            }
    except Exception:
        return None
# --- 3. PARSER IMAGEN (OCR) ---

def parsear_imagen_sunat(imagen_bytes: bytes) -> dict:
    # 1. Intentar PyTesseract (Local)
    res = _parsear_tesseract(imagen_bytes)
    if _es_resultado_valido(res):
        return res

    # 2. Intentar OCR.space (API Gratis)
    res = _parsear_ocr_space(imagen_bytes)
    if _es_resultado_valido(res):
        return res

    # 3. ÚLTIMO RECURSO: Gemini IA
    res = _parsear_gemini_ia(imagen_bytes)
    if _es_resultado_valido(res):
        return res

    return None
# --- LÓGICA DE LECTURA DE BANDEJA ---

def procesar_bandeja_usuario(user_id: str, provider: str):
    """
    Recibe user_id y provider. Consulta únicamente ese registro en user_tokens
    y ejecuta la función correspondiente (Gmail u Outlook) sin iterar en bucle.
    """
    provider_clean = provider.lower().strip()

    # Busca únicamente el token coincidente con el usuario Y el proveedor
    res = supabase.table("user_tokens")\
        .select("*")\
        .eq("user_id", user_id)\
        .eq("provider", provider_clean)\
        .execute()
    
    if not res.data:
        print(f"❌ No se encontró un token guardado de {provider_clean} para el usuario {user_id}.")
        return

    # Extrae el registro sin hacer bucle
    token_record = res.data[0]
    refresh_token = token_record.get("refresh_token")

    if not refresh_token:
        print(f"⚠️ El registro para {provider_clean} no tiene refresh_token.")
        return

    # Ejecuta directamente la función según el proveedor recibido
    if provider_clean == "gmail":
        print(f" Iniciando escaneo de Gmail para {user_id}...")
        _procesar_gmail(user_id, refresh_token)

    elif provider_clean == "outlook":
        print(f" Iniciando escaneo de Outlook para {user_id}...")
        _procesar_outlook(user_id, refresh_token)

    else:
        print(f"❌ Proveedor no soportado: {provider_clean}")

# --- SUB-FUNCIONES PRIVADAS POR PROVEEDOR ---

def _procesar_gmail(user_id: str, refresh_token: str):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET
    )

    service = build('gmail', 'v1', credentials=creds)
    query = "has:attachment (filename:xml OR filename:pdf OR filename:png OR filename:jpg OR filename:jpeg)"
    messages = []
    next_page_token = None

    while True:
        results = service.users().messages().list(
            userId='me', 
            q=query, 
            pageToken=next_page_token,
            maxResults=500
        ).execute()
        
        mensajes_obtenidos = results.get('messages', [])
        messages.extend(mensajes_obtenidos)
        next_page_token = results.get('nextPageToken')

        if not next_page_token:
            break

    print(f"Se encontraron {len(messages)} correos en Gmail.")
    comprobantes_procesados = []
    archivos_procesados_count = 0 

    for msg in messages:
        try:
            msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
            payload = msg_data.get('payload', {})
            parts = payload.get('parts', [])
            
            for part in parts:
                filename = part.get('filename', '').lower()
                attachment_id = part.get('body', {}).get('attachmentId')

                if attachment_id and (filename.endswith('.pdf') or filename.endswith('.xml') or filename.endswith(('.png', '.jpg', '.jpeg'))):
                    try:
                        attachment = service.users().messages().attachments().get(
                            userId='me', messageId=msg['id'], id=attachment_id
                        ).execute()
                        
                        file_bytes = base64.urlsafe_b64decode(attachment['data'])
                        datos = None

                        if filename.endswith('.xml'):
                            datos = parsear_xml_sunat(file_bytes)
                        elif filename.endswith('.pdf'):
                            datos = parsear_pdf_sunat(file_bytes)
                        elif filename.endswith(('.png', '.jpg', '.jpeg')):
                            try:
                                datos = parsear_imagen_sunat(file_bytes)
                            except Exception as ocr_err:
                                print(f"⚠️ OCR falló en {filename}: {str(ocr_err)}")
                                continue

                        if es_comprobante_valido(datos):
                            datos["user_id"] = user_id
                            comprobantes_procesados.append(datos)
                            print(f"✅ VÁLIDO (Gmail): RUC {datos['ruc_emisor']} | {datos['serie_numero']} | S/ {datos['monto_total']}")
                    except Exception as err:
                        print(f"⚠️ Error en archivo {filename}: {str(err)}")

                    archivos_procesados_count += 1
                    if archivos_procesados_count % 50 == 0:
                        gc.collect()

        except Exception as msg_err:
            print(f"⚠️ Error leyendo mensaje Gmail ID {msg.get('id')}: {str(msg_err)}")

    _guardar_en_supabase(comprobantes_procesados, user_id)


def _procesar_outlook(user_id: str, refresh_token: str):
    client_app = msal.ConfidentialClientApplication(
        client_id=settings.OUTLOOK_CLIENT_ID,
        client_credential=settings.OUTLOOK_CLIENT_SECRET,
        authority="https://login.microsoftonline.com/common"
    )

    token_response = client_app.acquire_token_by_refresh_token(
        refresh_token=refresh_token,
        scopes=["https://graph.microsoft.com/Mail.Read"]
    )

    if "access_token" not in token_response:
        print(f"❌ Error renovando token Outlook: {token_response.get('error_description')}")
        return

    # Si se renueva el refresh_token, se guarda filtrando por provider=outlook
    if "refresh_token" in token_response and token_response["refresh_token"] != refresh_token:
        supabase.table("user_tokens").update(
            {"refresh_token": token_response["refresh_token"]}
        ).eq("user_id", user_id).eq("provider", "outlook").execute()

    headers = {"Authorization": f"Bearer {token_response['access_token']}"}
    url_mensajes = "https://graph.microsoft.com/v1.0/me/messages?$filter=hasAttachments eq true&$select=id"
    messages = []

    while url_mensajes:
        resp = requests.get(url_mensajes, headers=headers)
        if resp.status_code != 200:
            break
        data = resp.json()
        messages.extend(data.get("value", []))
        url_mensajes = data.get("@odata.nextLink")

    print(f"Se encontraron {len(messages)} correos con adjuntos en Outlook.")
    comprobantes_procesados = []
    archivos_procesados_count = 0

    for msg in messages:
        msg_id = msg['id']
        url_adjuntos = f"https://graph.microsoft.com/v1.0/me/messages/{msg_id}/attachments"
        try:
            resp_adj = requests.get(url_adjuntos, headers=headers)
            if resp_adj.status_code != 200:
                continue

            adjuntos = resp_adj.json().get("value", [])

            for adj in adjuntos:
                filename = adj.get("name", "").lower()
                if adj.get("@odata.type") == "#microsoft.graph.fileAttachment" and (
                    filename.endswith('.pdf') or filename.endswith('.xml') or filename.endswith(('.png', '.jpg', '.jpeg'))
                ):
                    try:
                        file_bytes = base64.b64decode(adj.get("contentBytes", ""))
                        datos = None

                        if filename.endswith('.xml'):
                            datos = parsear_xml_sunat(file_bytes)
                        elif filename.endswith('.pdf'):
                            datos = parsear_pdf_sunat(file_bytes)
                        elif filename.endswith(('.png', '.jpg', '.jpeg')):
                            try:
                                datos = parsear_imagen_sunat(file_bytes)
                            except Exception as ocr_err:
                                print(f"⚠️ OCR falló en {filename}: {str(ocr_err)}")
                                continue

                        if es_comprobante_valido(datos):
                            datos["user_id"] = user_id
                            comprobantes_procesados.append(datos)
                            print(f"✅ VÁLIDO (Outlook): RUC {datos['ruc_emisor']} | {datos['serie_numero']} | S/ {datos['monto_total']}")
                    except Exception as err:
                        print(f"⚠️ Error en archivo {filename}: {str(err)}")

                    archivos_procesados_count += 1
                    if archivos_procesados_count % 50 == 0:
                        gc.collect()

        except Exception as msg_err:
            print(f"⚠️ Error leyendo mensaje Outlook ID {msg_id}: {str(msg_err)}")

    _guardar_en_supabase(comprobantes_procesados, user_id)


def _guardar_en_supabase(comprobantes_procesados: list, user_id: str):
    if not comprobantes_procesados:
        print(f"ℹ️ No se encontraron nuevos comprobantes válidos para {user_id}")
        return

    # 1. Elimina duplicados que hayan venido en el mismo lote de correos usando una clave única
    comprobantes_unicos = {}
    for comp in comprobantes_procesados:
        clave_unica = (comp["user_id"], comp["ruc_emisor"], comp["serie_numero"])
        comprobantes_unicos[clave_unica] = comp

    lista_final = list(comprobantes_unicos.values())

    # 2. Inserta o actualiza (upsert) en la base de datos por lotes de 10 en 10
    if lista_final:
        lote_tamano = 10
        for i in range(0, len(lista_final), lote_tamano):
            sub_lista = lista_final[i:i + lote_tamano]
            supabase.table("comprobantes").upsert(
                sub_lista, 
                on_conflict="user_id, ruc_emisor, serie_numero",
                returning="minimal"
            ).execute()
        
        print(f"✅ Se registraron exitosamente {len(lista_final)} comprobantes únicos para {user_id}")
    
    # 3. Limpieza de memoria
    gc.collect()

# --- ENDPOINTS ---

@router.post("/procesar-correos")
async def procesar_correos(
    user_id: str, 
    provider: str, 
    background_tasks: BackgroundTasks
):
    background_tasks.add_task(procesar_bandeja_usuario, user_id, provider)
    return {
        "message": "Escaneo iniciado en segundo plano", 
        "user_id": user_id,
        "provider": provider
    }

@router.get("/registros")
def obtener_registros(user_id: str):
    try:
        # Consulta los comprobantes del usuario filtrados por su user_id
        response = supabase.table("comprobantes")\
            .select("*")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .execute()
            
        return response.data
    except Exception as e:
        print(f"❌ Error al consultar Supabase: {e}")
        raise HTTPException(status_code=500, detail="Error al recuperar registros")

