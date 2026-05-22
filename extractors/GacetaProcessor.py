from datetime import datetime
import fitz  # PyMuPDF
import re
import os
import time
import sys
import json

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from lib.PDFProcessor import PDFProcessor

class GacetaProcessor:

    def __init__(self):
        pass
    
    def _spanish_to_int(self, text: str) -> int:
        """Convierte palabras numéricas en español a enteros (0-50 aprox)."""
        numbers = {
            "cero": 0, "primero": 1, "un": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
            "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
            "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
            "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
            "veintiuno": 21, "veintidós": 22, "veintitres": 23, "veintitrés": 23,
            "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintiséis": 26,
            "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
            "treinta y uno": 31, "cuarenta": 40, "cuarenta y un": 41
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
        """Extrae la fecha a partir del texto ya limpio por clean_text()."""
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
            raise ValueError("No se encontró la estructura de fecha esperada en el texto limpio.")

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
            raise ValueError(f"Error al traducir texto a números -> Día: {day_text}, Mes: {month_text}, Año: {year_text}")

        date = datetime(year, month, day).strftime("%Y-%m-%d") # Cambiado a YYYY-MM-DD según tu Excel
        return date
    
    def _get_quorum(self, cleaned_text: str) -> int:
        """Busca la cantidad de asistentes (quórum) al inicio del acta."""
        # Busca frases como: "CON LA ASISTENCIA DE CUARENTA DIPUTADAS" o "ASISTENCIA REGISTRADA DE CUARENTA Y UN DIPUTADAS"
        pattern = r"ASISTENCIA\s+(?:REGISTRADA\s+)?DE\s+([A-ZÁÉÍÓÚÑ\s]+?)\s+DIPUTAD[AO]S"
        match = re.search(pattern, cleaned_text, re.IGNORECASE)
        if match:
            return self._spanish_to_int(match.group(1))
        return 41  # Default en caso de no encontrarlo explícitamente

    def _extract_votes(self, cleaned_text: str, total_asistentes: int) -> list[dict]:
        """Extrae todas las votaciones (en bloque o por unanimidad) del texto."""
        eventos = []
        
        # 1. BÚSQUEDA DE VOTACIONES EN BLOQUE
        patron_bloque = r"PARA\s+(LA COMISIÓN DE .*?|EL COMITÉ DE .*?),\s+([A-ZÁÉÍÓÚÑ\s]+)\s+VOTOS A FAVOR,\s+([A-ZÁÉÍÓÚÑ\s]+)\s+VOTOS EN CONTRA\s+Y\s+([A-ZÁÉÍÓÚÑ\s]+)\s+VOTOS EN ABSTENCIÓN"
        matches_bloque = re.finditer(patron_bloque, cleaned_text, re.IGNORECASE)
        
        for match in matches_bloque:
            tema_sucio = match.group(1).strip()
            votos_favor = self._spanish_to_int(match.group(2))
            votos_contra = self._spanish_to_int(match.group(3))
            votos_abstencion = self._spanish_to_int(match.group(4))
            ausentes = total_asistentes - (votos_favor + votos_contra + votos_abstencion)
            
            # Formateamos el título basado en cómo lo tienes en el Excel
            titulo = f"Acuerdo para la elección e integración de {tema_sucio.lower().capitalize()}"
            
            eventos.append({
                "Tipo": "Punto de Acuerdo",
                "Titulo": titulo,
                "Tema": "Internos del Congreso", # Por defecto basado en tu CSV para comisiones
                "A favor": votos_favor,
                "Contra": votos_contra,
                "Abstenciones": votos_abstencion,
                "Ausentes": ausentes,
                "Total": total_asistentes
            })
            
        # 2. BÚSQUEDA DE VOTACIONES POR UNANIMIDAD
        # Ej. "DECLARÓ ELECTA, POR UNANIMIDAD DE VOTOS, PARA PRESIDIR LA JUNTA..."
        patron_unanimidad = r"(?:APROBAD[OA]|ELECT[OA]),?\s+(?:POR\s+)?UNANIMIDAD DE VOTOS,?\s+(?:PARA\s+PRESIDIR\s+)?(.*?)(?=\s+A\s+LA\s+DIPUTAD|\s+A\s+EL\s+DIPUTAD|\.|;)"
        matches_unan = re.finditer(patron_unanimidad, cleaned_text, re.IGNORECASE)
        
        for match in matches_unan:
            tema_sucio = match.group(1).strip()
            # Limitar longitud si captura texto de más
            if len(tema_sucio) > 150:
                tema_sucio = tema_sucio[:147] + "..."
                
            eventos.append({
                "Tipo": "Punto de Acuerdo",
                "Titulo": tema_sucio.capitalize(),
                "Tema": "Internos del Congreso", 
                "A favor": total_asistentes,
                "Contra": 0,
                "Abstenciones": 0,
                "Ausentes": 0,
                "Total": total_asistentes
            })
            
        return eventos

    def process_file(self, file_content: str) -> list[dict]:
        """
        Llama a todas las funciones de extracción de datos y concentra toda 
        la información en diccionarios listos para procesar.
        """
        try:
            fecha_acta = self.get_date(file_content)
        except ValueError:
            fecha_acta = "Fecha no encontrada"
            
        total_asistentes = self._get_quorum(file_content)
        eventos_extraidos = self._extract_votes(file_content, total_asistentes)
        
        registros_finales = []

        # Si el acta no tiene eventos explícitos detectados, generamos un registro vacío de control
        if not eventos_extraidos:
            return [{
                "Año Legislatura": None, "Periodo": None, "Folio periodo": None, "Folio legislatura": None,
                "Fecha": fecha_acta, "Tipo": None, "Titulo": "Sin votaciones detectadas", 
                "Tema 1": None, "Tema": None, "A favor": 0, "Contra": 0, "Abstenciones": 0, 
                "Ausentes": total_asistentes, "Total": total_asistentes, 
                "Porcentaje de Participación": 0, "Unidad": 0
            }]

        # Procesar y estandarizar cada evento detectado para igualar el Excel
        for evento in eventos_extraidos:
            votos_emitidos = evento["A favor"] + evento["Contra"] + evento["Abstenciones"]
            participacion = votos_emitidos / evento["Total"] if evento["Total"] > 0 else 0
            unidad = 1 if (evento["A favor"] > 0 and evento["Contra"] == 0 and evento["Abstenciones"] == 0) else 0

            record = {
                "Año Legislatura": "Primer año",       # Puedes ajustar para que sea dinámico
                "Periodo": "Primer periodo",           # Puedes ajustar para que sea dinámico
                "Folio periodo": None,                 # Para rellenar en el orquestador
                "Folio legislatura": None,             # Para rellenar en el orquestador
                "Fecha": fecha_acta,
                "Tipo": evento["Tipo"],
                "Titulo": evento["Titulo"],
                "Tema 1": 19,                          # Valor estático según tu CSV
                "Tema": evento["Tema"],
                "A favor": evento["A favor"],
                "Contra": evento["Contra"],
                "Abstenciones": evento["Abstenciones"],
                "Ausentes": evento["Ausentes"],
                "Total": evento["Total"],
                "Porcentaje de Participación": round(participacion, 10),
                "Unidad": unidad
            }
            registros_finales.append(record)

        return registros_finales

if __name__ == '__main__':

    pdf_processor = PDFProcessor()
    processor = GacetaProcessor()

    text = pdf_processor.extract_text('C:\\Users\\WARNE\\OneDrive\\Escritorio\\Projects\\Python\\DataExtractionGP\\data\\pdfs\\minutes\\acta_54628.pdf')
    cleaned_text = pdf_processor.clean_text(text)

    data = processor.process_file(cleaned_text)

    print(json.dumps(data, indent=4, ensure_ascii=False))