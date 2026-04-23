import fitz  # PyMuPDF: Asegúrate de instalarlo con 'pip install PyMuPDF'
import re
import os

class PDFProcessor:
    def __init__(self):
        # PASO 3: DICCIONARIO DE TRADUCCIÓN DETERMINISTA
        # Convertimos la redacción legal (texto) a números (enteros) para poder hacer cálculos.
        self.NUMBER_MAP = {
            "CERO": 0, "UN": 1, "UNO": 1, "UNA": 1, "DOS": 2, "TRES": 3, 
            "CUATRO": 4, "CINCO": 5, "SEIS": 6, "SIETE": 7, "OCHO": 8, 
            "NUEVE": 9, "DIEZ": 10, "ONCE": 11, "DOCE": 12, "TRECE": 13,
            "CATORCE": 14, "QUINCE": 15, "DIECISÉIS": 16, "DIECISIETE": 17, 
            "DIECIOCHO": 18, "DIECINUEVE": 19, "VEINTE": 20, "TREINTA": 30, 
            "CUARENTA": 40, "CUARENTA Y UN": 41, "CUARENTA Y UNA": 41,
            "CUARENTA Y DOS": 42
        }

    def text_to_int(self, text_number: str) -> int:
        """
        Convierte texto en mayúsculas a número entero basado en NUMBER_MAP.
        Si encuentra algo raro, devuelve 0 de forma segura.
        """
        text_clean = text_number.strip().upper()
        return self.NUMBER_MAP.get(text_clean, 0)

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
        PASO 2: LIMPIEZA Y CONTINUIDAD
        Elimina encabezados, pies de página y une los párrafos rotos.
        """
        if not raw_text:
            return ""

        # 1. Eliminar el indicador de página (ej. "--- PAGE 5 ---")
        texto_limpio = re.sub(r'--- PAGE \d+ ---', ' ', raw_text, flags=re.IGNORECASE)

        # 2. Reemplazar saltos de línea con un espacio. 
        # ESTO ES VITAL: Convierte todo el documento en un solo bloque continuo.
        texto_limpio = texto_limpio.replace("\n", " ")

        # 3. Limpiar encabezados repetitivos basados en los patrones de las actas de Puebla.
        # Quitamos frases como "TADOS UNIDOS MEXICA... H. CONGRESO DEL ESTADO..."
        texto_limpio = re.sub(r'TADOS UNIDOS MEXICA.*?H\. CONGRESO DEL ESTADO.*?PUEBLA', ' ', texto_limpio, flags=re.IGNORECASE)
        texto_limpio = re.sub(r'DOS UNIDOS MEXICAN.*?H\. CONGRESO DEL ESTADO.*?PUEBLA', ' ', texto_limpio, flags=re.IGNORECASE)
        
        # 4. Reducir múltiples espacios consecutivos a un solo espacio limpio
        texto_limpio = re.sub(r'\s+', ' ', texto_limpio)

        return texto_limpio.strip()