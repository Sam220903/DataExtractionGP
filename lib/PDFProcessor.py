import fitz  # PyMuPDF
import re
import os
from curl_cffi import requests
import time

class PDFProcessor:

    def download_pdf(self, pdf_url: str, save_dir: str, browser: str) -> str:
        """
        Descarga un archivo PDF y lo guarda en el directorio especificado.
        Retorna la ruta absoluta del archivo descargado.
        """
        match = re.search(r'id=(\d+)', pdf_url)
        
        if match:
            filename = f"acta_{match.group(1)}.pdf"
        else:
            nombre_crudo = pdf_url.split('/')[-1]
            filename = "".join(c for c in nombre_crudo if c.isalnum() or c in ('_', '-')) + ".pdf"
            
        filepath = os.path.join(save_dir, filename)

        if os.path.exists(filepath):
            print(f" -> El archivo ya existe localmente: {filename}")
            return filepath

        try:
            response = requests.get(pdf_url, impersonate=browser, timeout=15)
            
            if response.status_code == 200:
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                    
                time.sleep(1) 
                print(f" ->PDF descargado y guardado en: {filepath}")
                return filepath
            else:
                print(f" -> Advertencia: Código HTTP {response.status_code} al intentar descargar {pdf_url}")
                return ""
                
        except Exception as e:
            print(f" -> Error de red al descargar {pdf_url}: {e}")
            return ""

    def extract_text(self, pdf_path: str) -> str:
        """Abre el PDF y extrae el texto crudo de todas las páginas."""
        if not os.path.exists(pdf_path):
            print(f"Error: El archivo {pdf_path} no existe.")
            return ""

        raw_text = ""
        try:
            with fitz.open(pdf_path) as doc:
                for page in doc:
                    raw_text += page.get_text("text") + "\n"
            return raw_text
        except Exception as e:
            print(f"Error al leer el PDF {pdf_path}: {e}")
            return ""

    def clean_text(self, raw_text: str) -> str:
        """Elimina basura sin importar el orden en que aparezca en el PDF."""
        if not raw_text:
            return ""

        texto = re.sub(r'--- PAGE \d+ ---', ' ', raw_text, flags=re.IGNORECASE)
        texto = texto.replace("\n", " ")

        frases_basura = [
            r'H\. CONGRESO DEL ESTADO',
            r'P\s*U\s*E\s*B\s*L\s*A',           
            r'Secretaría General',
            r'ACTA',
            r'[A-Z]*TADOS UNIDOS MEXICA[A-Z]*',
            r'Marzo\.\s*Mes\s+de\s+las\s+Mujeres',
            r'Av\.\s+32\s+Oriente.*?72290',        
            r'www\.congresopuebla\.gob\.mx'
        ]

        for frase in frases_basura:
            texto = re.sub(frase, ' ', texto, flags=re.IGNORECASE)

        texto = re.sub(r'\s+', ' ', texto)

        return texto.strip()

    # ------------------------------------------------------------------
    # NUEVO (no modifica nada de lo anterior): variante de clean_text()
    # que PRESERVA los saltos de línea del PDF.
    #
    # Se necesitaba para GMProcessor (Gacetas Mensuales), cuyo regex de
    # segmentación depende de anclas de LÍNEA AISLADA (ej. "ORDEN DEL
    # DÍA" centrado solo en su propia línea). clean_text() existente hace
    # texto.replace("\n", " ") y colapsa todo con \s+ -> ' ', lo cual
    # destruye esa estructura de línea y hace que la ancla nunca haga
    # match. Como otros módulos (GacetaProcessor, etc.) ya dependen de
    # clean_text() tal cual está, no se tocó: esto es una función
    # adicional e independiente.
    #
    # HALLAZGO al construirla (dejarlo documentado por si aplica también
    # a clean_text()): la regla r'P\s*U\s*E\s*B\s*L\s*A' de clean_text()
    # usa \s* (cero o más espacios), así que en realidad hace match con
    # la palabra "Puebla" tal cual aparece en cualquier parte del texto
    # (no solo el título de portada con letras espaciadas), incluyendo
    # dentro de "congresopuebla.gob.mx" -> lo deja como
    # "congreso .gob.mx". Aquí se corrigió a \s+ (espacio real
    # obligatorio) para que solo capture el título de portada espaciado
    # y no la palabra "Puebla" normal de la prosa del acta.
    # ------------------------------------------------------------------
    def clean_text_preserve_lines(self, raw_text: str) -> str:
        """
        Limpia el mismo tipo de ruido que clean_text() (encabezados de
        portada, dirección/sitio web del pie de página, marcadores de
        página), pero conservando los saltos de línea originales del PDF.
        Pensado para extractores que segmentan por anclas de línea
        aislada, como GMProcessor.
        """
        if not raw_text:
            return ""

        texto = re.sub(r'--- PAGE \d+ ---', ' ', raw_text, flags=re.IGNORECASE)

        frases_basura = [
            r'H\. CONGRESO DEL ESTADO',
            r'P\s+U\s+E\s+B\s+L\s+A',
            r'Secretaría General',
            r'[A-Z]*TADOS UNIDOS MEXICA[A-Z]*',
            r'Marzo\.\s*Mes\s+de\s+las\s+Mujeres',
            r'Av\.\s+32\s+Oriente.*?72290',
            r'www\.congresopuebla\.gob\.mx',
            r'Pag\.\s*\d+',
        ]

        for frase in frases_basura:
            texto = re.sub(frase, ' ', texto, flags=re.IGNORECASE)

        # Colapsar solo espacios/tabs horizontales, sin tocar '\n'
        texto = re.sub(r'[ \t]+', ' ', texto)
        # Colapsar 3+ saltos de línea seguidos (páginas casi en blanco) a 2
        texto = re.sub(r'\n{3,}', '\n\n', texto)

        return texto.strip()