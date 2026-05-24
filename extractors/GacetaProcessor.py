from datetime import datetime
import fitz  # PyMuPDF
import re
import os
import sys
import json

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from lib.PDFProcessor import PDFProcessor
from lib.EventClassifier import EventClassifier

class GacetaProcessor:

    def __init__(self):
        self.classifier = EventClassifier()
    
    def _spanish_to_int(self, text: str) -> int:
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

        date = datetime(year, month, day).strftime("%Y-%m-%d")
        return date
    
    def _get_quorum(self, cleaned_text: str) -> int:
    # Intenta con "ASISTENCIA REGISTRADA... DE X DIPUTADAS Y DIPUTADOS"
        pattern = r"ASISTENCIA\s+(?:REGISTRADA\s+)?(?:A\s+TRAVÉS\s+DEL\s+SISTEMA\s+ELECTRÓNICO,?\s+)?DE\s+([A-ZÁÉÍÓÚÑ\s]+?)\s+DIPUTAD[AO]S"
        match = re.search(pattern, cleaned_text, re.IGNORECASE)
        if match:
            resultado = self._spanish_to_int(match.group(1).strip())
            if resultado > 0:
                return resultado
        return 41  # fallback

    # =================================================================
    # NUEVA FUNCIÓN PARA GENERAR EL TÍTULO DINÁMICO
    # =================================================================
    def _format_dynamic_title(self, raw_text: str, event_type: str, is_block: bool = False) -> str:
        """Genera el prefijo del título basándose en el contenido y el tipo de evento."""
        # 1. Si es de bloque (comisiones), el título es estrictamente uno:
        if is_block:
            return f"Acuerdo para la elección e integración de {raw_text.lower()}".capitalize()
            
        upper_text = raw_text.upper().strip()
        
        # 2. Casos especiales quemados que se mantienen idénticos al Excel
        if "JUNTA DE GOBIERNO" in upper_text:
            return "Elección del Diputado o Diputada que presidirá la Junta de Gobierno y Coordinación Política de la LXII Legislatura del H. Congreso del Estado"
        if "MESA DIRECTIVA" in upper_text and "ELECCIÓN" in upper_text and len(raw_text) < 80:
            return "Elección de la Primera Mesa Directiva..."

        # 3. Limpieza: Quitamos artículos iniciales ("EL ", "LA ") para que el prefijo encaje perfecto
        cleaned = re.sub(r'^(EL|LA|LOS|LAS|UN|UNA)\s+', '', raw_text.strip(), flags=re.IGNORECASE).strip()

        # Si el texto ya tiene la estructura deseada desde el PDF, solo lo capitalizamos y lo devolvemos
        if cleaned.upper().startswith("ACUERDO") or cleaned.upper().startswith("DICTAMEN"):
            return cleaned.capitalize()

        # 4. Asignación de Prefijos basados en el Contexto (Palabras Clave)
        if event_type == "Punto de Acuerdo":
            if "EXHORT" in upper_text:
                prefix = "Acuerdo por el que se exhorta "
            elif "PROPON" in upper_text or "PROPUEST" in upper_text:
                prefix = "Acuerdo por el que se propone "
            elif "CONVOC" in upper_text:
                prefix = "Acuerdo por el que se convoca "
            elif "INTEGRA" in upper_text:
                prefix = "Acuerdo por el que se integra "
            elif "ACEPT" in upper_text:
                prefix = "Acuerdo por el que se acepta "
            elif "PRESENT" in upper_text:
                prefix = "Acuerdo que presenta "
            else:
                prefix = "Acuerdo por el cual "
        else:  # Caso Iniciativa
            if "PROYECTO" in upper_text and "PRESENTA" in upper_text:
                prefix = "Dictamen con minuta Proyecto de decreto que presenta "
            elif "PROYECTO" in upper_text:
                prefix = "Dictamen con minuta Proyecto de decreto por el cual "
            elif "PRESENTA" in upper_text:
                prefix = "Dictamen con minuta de decreto que presenta "
            else:
                prefix = "Dictamen con minuta de decreto por el cual "
                
        # Unimos prefijo con el texto limpio y capitalizamos como una oración formal
        final_title = f"{prefix}{cleaned.lower()}"
        return final_title.capitalize()


    def _extract_votes(self, cleaned_text: str, total_asistentes: int) -> list[dict]:
        eventos = []
        titulos_extraidos = set()

        # 1. VOTACIONES EN BLOQUE (comisiones/comités)
        patron_bloque = (
            r"PARA\s+((?:LA COMISIÓN|EL COMITÉ|LAS COMISIONES).*?)"
            r",?\s+"
            r"(CUARENTA[A-ZÁÉÍÓÚÑ\s]*|TREINTA[A-ZÁÉÍÓÚÑ\s]*|VEINTE[A-ZÁÉÍÓÚÑ\s]*|CERO|UN|UNO|DOS|TRES|[0-9]{1,2})\s+VOTOS A FAVOR,?\s+"
            r"([A-ZÁÉÍÓÚÑ0-9\s]{2,30})\s+VOTOS EN CONTRA\s+Y\s+"
            r"([A-ZÁÉÍÓÚÑ0-9\s]{2,30})\s+VOTOS EN ABSTENCIÓN"
        )
        for match in re.finditer(patron_bloque, cleaned_text, re.IGNORECASE):
            tema_sucio = match.group(1).strip()
            tema_upper = tema_sucio.upper()
            if len(tema_sucio) > 300 or "PRESIDENT" in tema_upper or "DIPUTAD" in tema_upper:
                continue
            votos_favor = self._spanish_to_int(match.group(2))
            votos_contra = self._spanish_to_int(match.group(3))
            votos_abstencion = self._spanish_to_int(match.group(4))
            suma_votos = votos_favor + votos_contra + votos_abstencion
            ausentes = total_asistentes - suma_votos if total_asistentes >= suma_votos else 0
            tipo_codigo = self.classifier.classify_vote(tema_sucio)
            tipo_final = "Iniciativa" if tipo_codigo == 1 else "Punto de Acuerdo"
            titulo_final = self._format_dynamic_title(tema_sucio, tipo_final, is_block=True)
            titulos_extraidos.add(titulo_final.lower())
            eventos.append({
                "Tipo": tipo_final, "Titulo": titulo_final, "Tema": "Internos del Congreso",
                "A favor": votos_favor, "Contra": votos_contra, "Abstenciones": votos_abstencion,
                "Ausentes": ausentes, "Total": total_asistentes
            })

        # 2. VOTACIONES UNÁNIMES CLÁSICAS
        patron_unanimidad = r"(?:APROBAD[OA]|ELECT[OA]),?\s+(?:POR\s+)?UNANIMIDAD DE VOTOS,?\s+(?:PARA\s+PRESIDIR\s+)?(.*?)(?=\s+A\s+LA\s+DIPUTAD|\s+A\s+EL\s+DIPUTAD|\.|;)"
        basura_procesal = ["DISPENSA", "LECTURA", "CONTENIDO", "ORDEN DEL DÍA", "ACTA", "SESIÓN"]
        for match in re.finditer(patron_unanimidad, cleaned_text, re.IGNORECASE):
            tema_sucio = match.group(1).strip()
            tema_upper = tema_sucio.upper()
            if len(tema_sucio) > 250 or "PRESIDENT" in tema_upper or "DIPUTAD" in tema_upper or any(p in tema_upper for p in basura_procesal):
                continue
            tipo_codigo = self.classifier.classify_vote(tema_sucio)
            tipo_final = "Iniciativa" if tipo_codigo == 1 else "Punto de Acuerdo"
            titulo_final = self._format_dynamic_title(tema_sucio, tipo_final, is_block=False)
            titulos_extraidos.add(titulo_final.lower())
            eventos.append({
                "Tipo": tipo_final, "Titulo": titulo_final, "Tema": "Internos del Congreso",
                "A favor": total_asistentes, "Contra": 0, "Abstenciones": 0,
                "Ausentes": 0, "Total": total_asistentes
            })

        # 3. BÚSQUEDA INVERSA: desde el resultado de votación nominal hacia el título
        patron_resultado_nominal = (
            r"RESULTANDO\s+(?:CON\s+)?"
            r"([A-ZÁÉÍÓÚÑ\s]{2,50}?)\s+VOTOS A FAVOR,?\s+"
            r"([A-ZÁÉÍÓÚÑ\s]{2,30}?)\s+VOTOS EN CONTRA\s+Y\s+"
            r"([A-ZÁÉÍÓÚÑ\s]{2,30}?)\s+VOTOS EN ABSTENCIÓN,?\s+"
            r"APROBADO EN TODOS SUS TÉRMINOS\s+"
            r"(?:EL\s+)?"
            r"(DICTAMEN CON MINUTA PROYECTO DE DECRETO[^;\.]{10,400}?|ACUERDO[^;\.]{10,300}?)"
            r"(?:;\s*ORDENÁNDOSE|,\s*ORDENÁNDOSE|\.\s)"
        )
        for match in re.finditer(patron_resultado_nominal, cleaned_text, re.IGNORECASE | re.DOTALL):
            v_favor   = self._spanish_to_int(match.group(1).strip())
            v_contra  = self._spanish_to_int(match.group(2).strip())
            v_abst    = self._spanish_to_int(match.group(3).strip())
            tema_sucio = match.group(4).strip()
            tema_sucio = re.sub(r'\s*\n\s*\d+\s*H\.\s*CONGRESO.*?P\s*U\s*E\s*B\s*L\s*A\s*', ' ', tema_sucio, flags=re.IGNORECASE)
            tema_sucio = re.sub(r'2[º°]', '2o.', tema_sucio)
            tema_sucio = re.sub(r'(?i)constitución política de los\s*,', 'Constitución Política de los Estados Unidos Mexicanos,', tema_sucio)
            tema_sucio = re.sub(r'\s{2,}', ' ', tema_sucio).strip()

            if len(tema_sucio) > 450:
                continue

            if tema_sucio.upper().startswith("ACUERDO"):
                tipo_final = "Punto de Acuerdo"
            else:
                tipo_codigo = self.classifier.classify_vote(tema_sucio)
                tipo_final = "Iniciativa" if tipo_codigo == 1 else "Punto de Acuerdo"

            titulo_final = self._format_dynamic_title(tema_sucio, tipo_final, is_block=False)

            if titulo_final.lower() in titulos_extraidos:
                continue
            titulos_extraidos.add(titulo_final.lower())

            suma_votos = v_favor + v_contra + v_abst
            ausentes   = total_asistentes - suma_votos if total_asistentes >= suma_votos else 0

            eventos.append({
                "Tipo": tipo_final, "Titulo": titulo_final, "Tema": "Legislativo",
                "A favor": v_favor, "Contra": v_contra, "Abstenciones": v_abst,
                "Ausentes": ausentes, "Total": total_asistentes
            })

        # 4. SWEEPER — acuerdos de Junta de Gobierno y ocursos
        patron_barrido = (
            r"(?:RELATIVO AL|CORRESPONDIÓ AL)\s+"
            r"(ACUERDO QUE PRESENTAN?\s+(?:LAS?\s+Y\s+LOS?\s+DIPUTADOS?\s+INTEGRANTES?\s+DE\s+)?LA\s+JUNTA DE GOBIERNO[^,]{10,250}?)"
            r"(?=,\s*(?:QUE,?\s+EN TÉRMINOS|CON FUNDAMENTO|POR EL QUE|QUE CONTIENE))"
            r"|"
            r"CUENTA DEL\s+"
            r"(OCURSO QUE PRESENTA[^,]{10,200}?)"
            r"(?=,\s*(?:POR EL QUE SOLICITA|INTEGRANTE))"
        )
        patron_contenido = (
            r"(?:,\s*)?(?:QUE\s+)?CONTIENE\s+LAS?\s+PROPUESTAS?\s+PARA\s+(?:LA\s+)?"
            r"((?:DESIGNACIÓN|SUSTITUCIÓN)(?:(?!CON FUNDAMENTO).){5,400}?)"
            r"(?=CON FUNDAMENTO)"
        )
        patron_propone = (
            r"(?:,\s*)?POR EL QUE SE PROPONE\s+"
            r"((?:ACEPTAR|NOMBRAR|DESIGNAR|A LA PERSONA)(?:(?!CON FUNDAMENTO).){5,300}?)"
            r"(?=;\s*CON FUNDAMENTO|,\s*CON FUNDAMENTO)"
        )
        patron_licencia = r"POR EL QUE SOLICITA LICENCIA PARA SEPARARSE DEL CARGO DE DIPUTADA? LOCAL ([^;\.]{5,150}?)(?=[;,]\s*(?:SURTIENDO|AL RESPECTO))"

        for match in re.finditer(patron_barrido, cleaned_text, re.IGNORECASE | re.DOTALL):
            es_vocalia = match.group(1) is not None
            base_texto = (match.group(1) or match.group(2)).strip()
            base_texto = re.sub(r'\s{2,}', ' ', base_texto).strip()

            inicio  = match.end()
            ventana = cleaned_text[inicio: min(inicio + 900, len(cleaned_text))]

            if es_vocalia:
                m_cont = re.search(patron_contenido, ventana, re.IGNORECASE | re.DOTALL)
                m_prop = re.search(patron_propone, ventana, re.IGNORECASE | re.DOTALL) if not m_cont else None

                if m_cont:
                    detalle = re.sub(r'\s{2,}', ' ', m_cont.group(1)).strip().rstrip(" ,;")
                    titulo_final = f"Acuerdo que contiene la propuesta para la {detalle.lower()}".capitalize()
                elif m_prop:
                    detalle = re.sub(r'\s{2,}', ' ', m_prop.group(1)).strip().rstrip(" ,;")
                    titulo_final = f"Acuerdo por el que se propone {detalle.lower()}".capitalize()
                else:
                    continue

                tipo_final = "Punto de Acuerdo"
                tema_final = "Legislativo"
            else:
                nombre = base_texto.upper().split("DIPUTADA")[-1].strip().title()
                m_lic = re.search(patron_licencia, ventana, re.IGNORECASE)
                if m_lic:
                    detalle = m_lic.group(1).strip().lower()
                    titulo_final = f"Ocurso de la diputada {nombre}, por el que solicita licencia para separarse del cargo de diputada local {detalle}".capitalize()
                else:
                    titulo_final = f"Ocurso de la diputada {nombre}, por el que solicita licencia por tiempo indefinido".capitalize()
                tipo_final = "Punto de Acuerdo"
                tema_final = "Internos del Congreso"

            if titulo_final.lower() in titulos_extraidos:
                continue
            titulos_extraidos.add(titulo_final.lower())

            if re.search(r"UNANIMIDAD", ventana, re.IGNORECASE):
                v_favor = total_asistentes; v_contra = 0; v_abst = 0; ausentes = 0
            else:
                m_votos = re.search(
                    r"RESULTANDO\s+(?:CON\s+)?([A-ZÁÉÍÓÚÑ0-9\s]{2,40}?)\s+VOTOS A FAVOR,?\s+"
                    r"([A-ZÁÉÍÓÚÑ0-9\s]{2,30}?)\s+VOTOS EN CONTRA\s+Y\s+"
                    r"([A-ZÁÉÍÓÚÑ0-9\s]{2,30}?)\s+VOTOS EN ABSTENCIÓN",
                    ventana, re.IGNORECASE
                )
                if m_votos:
                    v_favor  = self._spanish_to_int(m_votos.group(1))
                    v_contra = self._spanish_to_int(m_votos.group(2))
                    v_abst   = self._spanish_to_int(m_votos.group(3))
                    suma     = v_favor + v_contra + v_abst
                    ausentes = total_asistentes - suma if total_asistentes >= suma else 0
                else:
                    v_favor = total_asistentes; v_contra = 0; v_abst = 0; ausentes = 0

            es_ocurso = "OCURSO" in titulo_final.upper()
            eventos.append({
                "Tipo": tipo_final,
                "Titulo": titulo_final,
                "Tema": "Internos del Congreso" if es_ocurso else "Legislativo",
                "A favor": v_favor, "Contra": v_contra, "Abstenciones": v_abst,
                "Ausentes": ausentes, "Total": total_asistentes
            })

        return eventos

    def process_file(self, file_content: str) -> list[dict]:
        try:
            fecha_acta = self.get_date(file_content)
        except ValueError:
            fecha_acta = "Fecha no encontrada"
            
        total_asistentes = self._get_quorum(file_content)


        eventos_extraidos = self._extract_votes(file_content, total_asistentes)
        
        registros_finales = []

        if not eventos_extraidos:
            pass

        for evento in eventos_extraidos:
            votos_emitidos = evento["A favor"] + evento["Contra"] + evento["Abstenciones"]
            participacion = votos_emitidos / evento["Total"] if evento["Total"] > 0 else 0
            unidad = 1 if (evento["A favor"] > 0 and evento["Contra"] == 0 and evento["Abstenciones"] == 0) else 0

            record = {
                "Año Legislatura": "Primer año",       
                "Periodo": "Primer periodo",           
                "Folio periodo": None,                 
                "Folio legislatura": None,             
                "Fecha": fecha_acta,
                "Tipo": evento["Tipo"],
                "Titulo": evento["Titulo"],
                "Tema 1": 19,                          
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

    # Extraer y limpiar texto
    text = pdf_processor.extract_text('C:\\Users\\WARNE\\OneDrive\\Escritorio\\Projects\\Python\\DataExtractionGP\\data\\pdfs\\minutes\\acta_54938.pdf')
    cleaned_text = pdf_processor.clean_text(text)

    # Procesar datos
    data = processor.process_file(cleaned_text)

    # Definir el nombre del archivo temporal
    output_filename = "temp_output.json"
    
    # Exportar a JSON
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    print(f"¡Listo! Se extrajeron {len(data)} registros.")
    print(f"Los datos se han guardado temporalmente en el archivo: {output_filename}")