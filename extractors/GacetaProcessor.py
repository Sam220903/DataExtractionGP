from datetime import datetime
import re
import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from lib.PDFProcessor import PDFProcessor
from lib.EventClassifier import EventClassifier

class GacetaProcessor:

    def __init__(self):
        pass
    
    def _spanish_to_int(self, text: str) -> int:
        """Convierte números en español a enteros"""
        numbers = {
            "cero": 0, "primero": 1, "un": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
            "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
            "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
            "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
            "veintiuno": 21, "veintidós": 22, "veintitres": 23, "veintitrés": 23,
            "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintiséis": 26,
            "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
            "treinta y uno": 31, "treinta y un": 31, "cuarenta": 40
        }
        tens = {"veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50}
        
        text = text.lower().strip()
        if text in numbers:
            return numbers[text]
        if " y " in text:
            parts = text.split(" y ")
            return tens.get(parts[0], 0) + numbers.get(parts[1], 0)
        return 0

    def get_date(self, cleaned_text: str) -> str:
        """Extrae la fecha del acta en formato YYYY-MM-DD"""
        months_map = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
        }

        pattern = (
            r"(?:CELEBRADA\s+)?EL\s+"
            r"(?:(?:LUNES|MARTES|MIÉRCLES|MIÉRCOLES|JUEVES|VIERNES|SÁBADO|SABADO|DOMINGO)\s+)?"
            r"([A-ZÁÉÍÓÚÑ]+(?:\s+Y\s+[A-ZÁÉÍÓÚÑ]+)?)\s+DE\s+"
            r"([A-ZÁÉÍÓÚÑ]+)\s+DE\s+"
            r"(DOS MIL\s+[A-ZÁÉÍÓÚÑ]+(?:\s+Y\s+[A-ZÁÉÍÓÚÑ]+)?)"
        )

        match = re.search(pattern, cleaned_text, re.IGNORECASE)
        if not match:
            return None

        day_text = match.group(1).strip().lower()
        month_text = match.group(2).strip().lower()
        year_text = match.group(3).strip().lower()

        day = self._spanish_to_int(day_text)
        month = months_map.get(month_text)
        
        year = 2000
        remainder_year = year_text.replace("dos mil", "").strip()
        if remainder_year:
            year += self._spanish_to_int(remainder_year)

        if not day or not month or year == 2000:
            return None

        date = datetime(year, month, day).strftime("%Y-%m-%d")
        return date

    def process_file(self, file_content: str) -> list[dict]:
        """Procesa el archivo y retorna un registro con la fecha extraída"""

        periods = { 1 : "Primer periodo", 2 : "Segundo periodo", 3 : "Tercer periodo" }

        years = { 1 : "Primer año", 2 : "Segundo año", 3 : "Tercer año" }
        
        # Extraer fecha
        date = self.get_date(file_content)

        # Llamar al objeto para clasificar eventos según diferentes condiciones
        classifier = EventClassifier()
        
        if not date:
            print("No se pudo extraer la fecha del documento")
            return []
        else:
            period = periods[classifier.classify_per_period(date)]
            legislative_year = years[classifier.classify_per_year(date)]

        
        # Por ahora retornamos un solo registro con solo la fecha
        # Los otros 4 campos calculados (Año Legislatura, Periodo, Folio periodo, Folio legislatura)
        # se agregarán en los siguientes pasos
        record = {
            "Año Legislatura" : legislative_year,
            "Periodo" : period,
            "Fecha": date,
        }
        
        return [record]


if __name__ == '__main__':
    pdf_processor = PDFProcessor()
    processor = GacetaProcessor()

    # Extraer y limpiar texto
    text = pdf_processor.extract_text('C:\\Users\\WARNE\\OneDrive\\Escritorio\\Projects\\Python\\DataExtractionGP\\data\\pdfs\\minutes\\acta_62488.pdf')
    cleaned_text = pdf_processor.clean_text(text)

    # Procesar datos
    data = processor.process_file(cleaned_text)

    # Definir el nombre del archivo temporal
    output_filename = "temp_output.json"
    
    # Exportar a JSON
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    print(f"¡Listo! Se extrajeron {len(data)} registros.")
    if data:
        print(f"Fecha extraída: {data[0]['Fecha']}")
    print(f"Los datos se han guardado en: {output_filename}")