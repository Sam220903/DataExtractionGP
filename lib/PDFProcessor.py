import fitz  # PyMuPDF: Asegúrate de instalarlo con 'pip install PyMuPDF'
import re
import os
from curl_cffi import requests
import time

class PDFProcessor:

    def download_pdf(pdf_url: str, save_dir: str, browser: str) -> str:
        """
        Descarga un archivo PDF y lo guarda en el directorio especificado.
        Retorna la ruta absoluta del archivo descargado.
        """
        # NUEVA LÓGICA DE NOMBRE DE ARCHIVO:
        # Buscamos el ID numérico en la URL (ej. id=56242) usando Expresiones Regulares
        match = re.search(r'id=(\d+)', pdf_url)
        
        if match:
            filename = f"acta_{match.group(1)}.pdf"
        else:
            # Plan B: Si no encuentra el ID, limpia todos los caracteres especiales
            nombre_crudo = pdf_url.split('/')[-1]
            filename = "".join(c for c in nombre_crudo if c.isalnum() or c in ('_', '-')) + ".pdf"
            
        filepath = os.path.join(save_dir, filename)

        # REGLA: Idempotencia. Si ya lo descargamos antes, nos saltamos la red.
        if os.path.exists(filepath):
            print(f" -> El archivo ya existe localmente: {filename}")
            return filepath

        try:
            # REGLA: Resiliencia de red y manejo de excepciones
            response = requests.get(pdf_url, impersonate=browser, timeout=15)
            
            if response.status_code == 200:
                # Guardamos en modo binario ('wb')
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                    
                # REGLA: Pausa ética
                time.sleep(1) 
                print(f" -> ✓ PDF descargado y guardado en: {filepath}")
                return filepath
            else:
                print(f" -> Advertencia: Código HTTP {response.status_code} al intentar descargar {pdf_url}")
                return ""
                
        except Exception as e:
            print(f" -> Error de red al descargar {pdf_url}: {e}")
            return ""


    def extract_text(self, pdf_path: str) -> str:
        """
        PASO 1: LECTURA DEL PDF
        Abre el PDF y extrae el texto crudo de todas las páginas.
        Maneja errores si el archivo está corrupto o no existe.
        """
        if not os.path.exists(pdf_path):
            print(f"Error: El archivo {pdf_path} no existe.")
            return ""

        raw_text = ""
        try:
            # fitz.open abre el documento. Usamos 'with' para que se cierre automáticamente.
            with fitz.open(pdf_path) as doc:
                for page in doc:
                    raw_text += page.get_text("text") + "\n"
            return raw_text
        except Exception as e:
            print(f"Error al leer el PDF {pdf_path}: {e}")
            return ""

    def clean_text(self, raw_text: str) -> str:
        """
        PASO 2: LIMPIEZA Y CONTINUIDAD (VERSIÓN RESILIENTE)
        Elimina basura sin importar el orden en que aparezca en el PDF.
        """
        if not raw_text:
            return ""

        # 1. Eliminar indicadores de página de PyMuPDF
        texto = re.sub(r'--- PAGE \d+ ---', ' ', raw_text, flags=re.IGNORECASE)

        # 2. Convertir todo a una sola línea (vital para que la votación sea continua)
        texto = texto.replace("\n", " ")

        # 3. Diccionario de "Frases Basura". 
        frases_basura = [
            r'H\. CONGRESO DEL ESTADO',
            r'P\s*U\s*E\s*B\s*L\s*A',           
            r'Secretaría General',
            r'ACTA',
            r'[A-Z]*TADOS UNIDOS MEXICA[A-Z]*', # <-- ¡OJO! La coma debe ir AQUÍ, antes del comentario.
            r'Marzo\.\s*Mes\s+de\s+las\s+Mujeres', # Usamos \s+ por si hay múltiples espacios
            r'Av\.\s+32\s+Oriente.*?72290',        # Simplificamos la dirección: "Av. 32 Oriente" seguido de lo que sea hasta "72290"
            r'www\.congresopuebla\.gob\.mx'
        ]

        for frase in frases_basura:
            texto = re.sub(frase, ' ', texto, flags=re.IGNORECASE)

        # 4. Reducir múltiples espacios consecutivos a uno solo
        texto = re.sub(r'\s+', ' ', texto)

        return texto.strip()