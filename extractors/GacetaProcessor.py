from datetime import datetime

import fitz  # PyMuPDF
import re
import os
import time
import sys

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from lib.PDFProcessor import PDFProcessor

class GacetaProcessor:

    def __init__(self):
        pass
    

    def _spanish_to_int(self, text: str) -> int:
        """Convierte palabras numéricas en español a enteros (1-31 y residuos de año)."""
        numbers = {
            "primero": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
            "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
            "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
            "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
            "veintiuno": 21, "veintidós": 22, "veintitres": 23, "veintitrés": 23,
            "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintiséis": 26,
            "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
            "treinta y uno": 31
        }
        tens = {"veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50}
        
        text = text.lower().strip()
        if text in numbers:
            return numbers[text]
        if " y " in text:
            parts = text.split(" y ")
            return tens.get(parts[0], 0) + numbers.get(parts[1], 0)
        return 0

    def get_date(self, cleaned_text : str) -> str:

        """
        Extrae la fecha a partir del texto ya limpio por clean_text().
        Busca el patrón: CELEBRADA EL [DÍA] DE [MES] DE [AÑO]
        """
        months_map = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
        }

        # Regex ultra-precisa para aislar texto numérico (ej: "VEINTIDÓS", "TREINTA Y UNO", "DOS MIL VEINTICUATRO")
        # Evita tragarse palabras adyacentes del PDF continuo.
        pattern = (
            r"(?:CELEBRADA\s+)?EL\s+"
            r"(?:(?:LUNES|MARTES|MIÉRCLES|MIÉRCOLES|JUEVES|VIERNES|SÁBADO|SABADO|DOMINGO)\s+)?"
            r"([A-ZÁÉÍÓÚÑ]+(?:\s+Y\s+[A-ZÁÉÍÓÚÑ]+)?)\s+DE\s+"
            r"([A-ZÁÉÍÓÚÑ]+)\s+DE\s+"
            r"(DOS MIL\s+[A-ZÁÉÍÓÚÑ]+(?:\s+Y\s+[A-ZÁÉÍÓÚÑ]+)?)"
        )

        match = re.search(pattern, cleaned_text, re.IGNORECASE)
        if not match:
            raise ValueError("No se encontró la estructura de fecha esperada en el texto limpio.")

        day_text = match.group(1).strip().lower()
        month_text = match.group(2).strip().lower()
        year_text = match.group(3).strip().lower()

        # Conversiones
        day = self._spanish_to_int(day_text)
        month = months_map.get(month_text)
        
        # Procesar año compuesto
        year = 2000
        remainder_year = year_text.replace("dos mil", "").strip()
        if remainder_year:
            year += self._spanish_to_int(remainder_year)

        if not day or not month or year == 2000:
            raise ValueError(f"Error al traducir texto a números -> Día: {day_text}, Mes: {month_text}, Año: {year_text}")

        date = datetime(year, month, day).strftime("%d/%m/%Y")

        return date
    
    def process_file(self, file_content : str) -> list[dict]:
        """
        Llama a todas las funciones de extracción de datos y concentra toda 
        la información en un diccionario (o diccionarios) listo para procesar

        Args:
            file_content (str): Contenido del archivo a procesar, en este caso 
            el contenido de una gaceta (acta) legislativa

        Returns:
            list[dict]: Lista de diccionarios según el número de registros encontrados
            Cada diccionario contiene todos los datos extraidos concentrados
        """

        record = {
            "Año Legislatura": None,
            "Periodo": None,
            "Folio periodo": None,
            "Folio legislatura": None,
            "Fecha": self.get_date(file_content),
            "Tipo": None,
            "Titulo": None,
            "Tema 1": None,
            "Tema": None,
            "A favor": None,
            "Contra": None,
            "Abstenciones": None,
            "Ausentes": None,
            "Total":  None,
            "Porcentaje de Participación": None,
            "Unidad": None
        }

        return [record]

if __name__ == '__main__':

    pdf_processor = PDFProcessor()
    processor = GacetaProcessor()

    text = pdf_processor.extract_text('C:\\Users\\WARNE\\OneDrive\\Escritorio\\Projects\\Python\\DataExtractionGP\\data\\pdfs\\minutes\\acta_54628.pdf')
    cleaned_text = pdf_processor.clean_text(text)

    print(processor.process_file(cleaned_text))

    


    