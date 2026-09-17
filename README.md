# 🚀 Backend Engine - Extractor de Comprobantes SUNAT

Motor API REST desarrollado con **FastAPI** para la extracción, decodificación y almacenamiento automático de comprobantes de pago peruanos (Facturas, Boletas y Tickets) leídos directamente desde bandejas de correo electrónico (Gmail y MS Outlook).

---

## 🛠️ Tecnologías Utilizadas

* **Python 3.11+** - Lenguaje principal.
* **FastAPI** - Framework web para endpoints de alta velocidad.
* **zxing-cpp & PyMuPDF (Fitz)** - Decodificación ultrarrápida de QRs locales y lectura de documentos PDF sin consumo masivo de memoria RAM.
* **Google GenAI SDK** - Rescate por Inteligencia Artificial para extracción de datos en imágenes complejas sin QR.
* **Microsoft Graph & Gmail API** - Integración con servicios de correo electrónico.
* **Supabase** - Base de datos PostgreSQL en la nube.

---

## 📋 Funcionalidades Principales

* ⚡ **Procesamiento Híbrido:** Extrae comprobantes locales (PDF/XML/QR) en milisegundos mediante `zxingcpp` y usa resguardo por IA solo cuando es estrictamente necesario.
* 🧾 **Soporte de Documentos SUNAT:** Compatibilidad completa con Facturas (`01`), Boletas (`03`) y Tickets/Vouchers de máquinas registradoras (`10`).
* 🖼️ **Tratamiento de Imágenes:** Aplica realce de contraste dinámico para la lectura exitosa de tickets arrugados o doblados.
* 📩 **Filtro de Correos Optimizado:** Lectura por lotes paginados en MS Graph API y Gmail API para evitar sobrecarga de red.

---

## 🚀 Variables de Entorno

Para ejecutar este proyecto de forma local, crea un archivo `.env` en la raíz basado en `.env.example`:

```env
SUPABASE_URL=tu_url_supabase
SUPABASE_KEY=tu_clave_supabase
GEMINI_API_KEY=tu_gemini_api_key

git clone https://github.com/markkos2003/mpBackendAutomatizadorCorreo.git
cd mpBackendAutomatizadorCorreo

# 2. Crear y activar entorno virtual
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Iniciar servidor de desarrollo
uvicorn main:app --reload
