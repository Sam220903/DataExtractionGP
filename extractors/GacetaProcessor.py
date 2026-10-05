# GacetaProcessor.py

from datetime import datetime
import re
import os
import sys
import json

# Nuevas importaciones del SDK actualizado
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from lib.PDFProcessor import PDFProcessor
from lib.EventClassifier import EventClassifier

# ==========================================
# CONFIGURACIÓN DE LA API DE GEMINI 3.1
# ==========================================

# Cargar variables de entorno desde el archivo .env
load_dotenv()

# Obtener de variables de entorno, de archivo .env
API_KEY = os.getenv("GEMINI_API_KEY")

class GacetaProcessor:

    def __init__(self):
        # En el nuevo SDK, instanciamos un Cliente en lugar de configurar el módulo globalmente
        self.client = genai.Client(api_key=API_KEY)
        # Definimos el modelo actualizado que nos compartiste
        self.model_id = "gemini-3.1-flash-lite"
    
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

    def _extract_votes(self, cleaned_text: str) -> list[dict]:
        """Envía el texto a Gemini utilizando el prompt estructurado"""
        
        prompt_instructions = """
=== CONTEXTO ===

Eres un analista de documentos legislativos, recibes el texto extraido y sin frases basura de un acta de sesión donde se explica a detalle las acciones y 
actividades realizadas durante un día dentro de la cámara de diputados del estado de Puebla. 

Se mencionan eventos relacionados a votaciones, para las cuales en cada una se resalta el número de diputados que participaron en la votación, asi como 
un conteo de las personas que votaron a favor, en contra y las abstenciones de votos.

En cada votación se describe el tema del cual trató y en base a este tema se puede clasificar el tipo de votación que es, el cual puede ser o INICIATIVA o PUNTO DE ACUERDO.

Tu trabajo será identificar todas las votaciones que se hicieron en una sesión acorde al contenido del texto, y por cada votación se extraerán sus datos relacionados.

=== FIN DEL CONTEXTO === 

=== INSTRUCCIONES ===

1. Analiza a detalle el contenido del acta legislativa, la cual se encuentra dentro de los delimitadores de 'TEXTO'
2. Identifica todas las votaciones mencionadas en la sesión. Para detectar una votación de manera infalible, el ancla principal SIEMPRE será la declaración de los votos. Sigue estrictamente esta jerarquía y reglas:
    A. Ancla Principal (Detección de Votos): Todo registro válido debe contener obligatoriamente el resultado de la votación. Escanea el texto buscando menciones de la familia de palabras "voto", "votos", "votación" que cumplan con una de estas dos estructuras:
        - Estructura Nominal/Bloque: Secuencia que menciona explícitamente "[Número] VOTOS A FAVOR", seguida de "[Número] VOTOS EN CONTRA" y finalmente "[Número] VOTOS EN ABSTENCIÓN" (o "ABSTENCIÓN").
        - Estructura de Unanimidad: Mención exacta de las frases "UNANIMIDAD DE VOTOS" o "POR UNANIMIDAD DE VOTOS".

    B. Determinación de Asistencia (Quórum Base Opcional): Una vez localizada un ancla de votación (paso A), busca en el texto inmediatamente anterior el total de asistentes.
        - Palabras clave: "ASISTENCIA REGISTRADA" o "ASISTENCIA A TRAVÉS DEL SISTEMA ELECTRÓNICO".
        - Estructura esperada: Mención de la asistencia seguida de "DE", un número y "DIPUTADAS Y DIPUTADOS".
        - Comportamiento estricto por defecto: La mención de asistencia es opcional. Si no se detecta esta frase antes de los votos, el sistema asume automáticamente un quórum de 41 asistentes.

    C. Reglas de Exclusión Estricta (Filtro de Falsos Positivos): Debes ignorar por completo la votación detectada si en el texto cercano (antes o después de los votos) se encuentran palabras de mero trámite procesal legislativo, tales como:
    "DISPENSA", "LECTURA", "CONTENIDO", "ORDEN DEL DÍA", "ACTA" o "SESIÓN".

    D. Identificadores de Contexto (Clasificación Estructural): Tras confirmar que es una votación válida, observa el texto circundante para entender su contexto:
        - Resultados Estándar (Iniciativas/Acuerdos): Frecuentemente están precedidos por "RESULTANDO CON" y la frase de resolución tras el conteo suele ser "APROBADO EN TODOS SUS TÉRMINOS", mencionando "DICTAMEN" o "ACUERDO".
        - Unanimidad: La resolución suele indicar "APROBADO" o "ELECTO", y frecuentemente le sigue "PARA PRESIDIR" o la asignación de un cargo.
        - Votaciones en Bloque (Órganos internos): Suelen incluir frases detonantes como "PARA LA COMISIÓN", "PARA EL COMITÉ" o "PARA LAS COMISIONES". Exclusión adicional: Ignora estos bloques si mencionan "PRESIDENTE" o "DIPUTADO", o si el texto de la acción es excesivamente largo (más de 300 caracteres).

3. Para cada votación, clasificala o como "Punto de Acuerdo" o como "Iniciativa", para ello, debes buscar en el contenido de la votación palabras clave para cada tipo de votación,
tomando en cuenta tambien la definición de cada tipo de evento (Punto de Acuerdo e Iniciativa)
- Palabras clave por tipo:
    * Iniciativa: [
            'dictamen', 'dictamenes', 
            'reforma', 'reformas', 'reformar', 'reforman', 'reformado',
            'decreto', 'decretos', 
            'proposicion', 'proposiciones', 
            'propuesta', 'propuestas', 'propuesto',
            'expide', 'expiden', 'expedir', 'expedicion', 'expediciones',
            'minuta', 'minutas', 'minuta de decreto, ',
            'convoca', 'convocan'
        ]
    * Punto de Acuerdo:  [
            'acuerdo', 'acuerdos', 'acordar', 'acuerdan',
            'exhorto', 'exhortos', 'exhorta', 'exhortan', 'exhortar',
            'eleccion', 'elecciones', 'elegir', 'eligen',
            'votacion', 'votaciones', 'votar', 'votan', 'voto', 'votos',
            'designacion', 'designaciones', 'designa', 'designan', 'designar',
            'oficio', 'oficios', 
            'aprobacion', 'aprobaciones', 'aprobar', 'aprueba', 'aprueban', 'aprobado',
            'habilita', 'habilitan', 'habilitar', 'habilitacion'
        ]
    Nota: Estas palabras pueden contener acentos o tener ligeros errores o variaciones de escritura
    RESULTADO: Tipo de votación, donde el valor puede ser solamente o "Punto de Acuerdo" o "Iniciativa"  | <Tipo>

4. Por cada votación, genera su título uniendo un prefijo con la acción realizada. El prefijo se elige siguiendo estas REGLAS CONDICIONALES ESTRICTAS según las palabras clave encontradas en el texto de la acción:

    Si el tipo es "Punto de Acuerdo":
        - Si el texto contiene "ELECC", usa el prefijo "Elección de "
        - Si el texto contiene "EXHORT", usa el prefijo: "Acuerdo por el que se exhorta "
        - Si el texto contiene "PROPON" o "PROPUEST", usa el prefijo: "Acuerdo por el que se propone "
        - Si el texto contiene "CONVOC", usa el prefijo: "Acuerdo por el que se convoca "
        - Si el texto contiene "INTEGRA", usa el prefijo: "Acuerdo por el que se integra "
        - Si el texto contiene "ACEPT", usa el prefijo: "Acuerdo por el que se acepta "
        - Si el texto contiene "PRESENT", usa el prefijo: "Acuerdo que presenta "
        - Si el texto contiene "CONTIE" ó "QUE CONTIE", usa el prefijo:  "Acuerdo que contiene la propuesta" 
        - Si es explícitamente para comisiones/bloques, usa el prefijo: "Acuerdo para la elección e integración de "
        - Si no aplica ninguna de las anteriores, usa por defecto: "Acuerdo por el cual "

    Si el tipo es "Iniciativa":
        - Si el texto contiene "PROYECTO" y "PRESENTA", usa el prefijo: "Dictamen con minuta Proyecto de decreto que presenta "
        - Si el texto contiene "PROYECTO", usa el prefijo: "Dictamen con minuta Proyecto de decreto por el cual "
        - Si el texto contiene "PRESENTA", usa el prefijo: "Dictamen con minuta de decreto que presenta "
        - Si no aplica ninguna de las anteriores, usa por defecto: "Dictamen con minuta de decreto por el cual "

    Estructura del título: <Prefijo> <Acción (convirtiendo el texto a minúsculas, excepto nombres propios)>
    RESULTADO: Título asignado a la votación   | <Título>

5. Realiza un conteo de las asistencias y votaciones dentro de una votación, donde debes obtener 5 datos:
- Total de diputados: Total de diputados en ese momento, siempre tiene un valor de 41 a menos que se mencione dentro de la votación 
que solo participaron un miembro por partido político, en cuyo caso el total pasa a 9. Este dato solo puede tener dos valores, 41 o 9 | <Total>
- Votos a favor | <Votos a favor>
- Votos en contra | <Votos en contra>
- Abstenciones: Diputados que se abstuvieron de dar su voto <Abstenciones>
Observación: Si la votación es clasificada como 'Unanimidad', debes asignar obligatoriamente: Votos a favor = Total, Votos en contra = 0, Abstenciones = 0.

RESULTADO: Las cifras referentes a los 4 datos de voto (Total, Votos a favor, Votos en contra y Abstenciones)

6. Clasifica cada registro de votación en un tema según la acción que se realizó en la votación y su contenido. Clasifica cada votación entre solo uno
de los siguientes temas:
    | Número de tema | Tema |
    | 1 | Economía, Comercio y Competitividad |
    | 2 | Pobreza y Política Social |
    | 3 | Seguridad Pública, Protección Civil y Narcotráfico |
    | 4 | Partidos Políticos y Elecciones |
    | 5 | Medio Ambiente |
    | 6 | Salud |
    | 7 | Impuestos, Finanzas Públicas, Patrimonio estatal y municipal |
    | 8 | Educación y Cultura |
    | 9 | Migración y fronteras |
    | 10 | Transparencia y Rendición de Cuentas |
    | 11 | Infraestructura, Movilidad y Transporte |
    | 12 | Agricultura, Ganadería y Pesca |
    | 13 | Medios de Comunicación |
    | 14 | Justicia y Estado de Derecho |
    | 16 | Trabajo y Previsión Social |
    | 18 | Administración Pública |
    | 19 | Internos del Congreso |
    | 20 | Justicia y Estado de Derecho en temas de Mujeres | 
Resultado: El número de tema de la votación como <Tema 1> y el tema de la votación como <Tema>

7. Para cada votación extrae además 4 datos complementarios, usando ÚNICAMENTE lo que dice el texto (no inventes información):
    A. Contenido: resumen de 1 a 3 oraciones, en español neutro, de lo que propone o resuelve el asunto votado (qué ley, código o decreto se reforma, adiciona o expide y en qué consiste el cambio; a qué autoridad se exhorta y con qué fin; qué cargo, comisión o comité se elige o integra, etc.). Si el texto solo da el título del asunto, parafrasea ese título sin agregar detalles que no estén en el texto. | <Contenido>
    B. Comentario: observaciones relevantes que el acta consigna sobre ESTA votación, en una o dos oraciones: tipo de votación (nominal, secreta o económica), si hubo discusión y quién intervino, si el dictamen concentra varias iniciativas, ausencias o retardos justificados, instrucciones de publicación o envío. Si no hay nada relevante, usa "" (cadena vacía). | <Comentario>
    C. Presentador: persona, comisión u órgano que presenta o propone el asunto votado, tal como lo nombra el texto (por ejemplo "que presenta la Comisión de ...", "propuesta presentada por la diputada ..."). Si el texto no lo nombra, evalúa si se puede inferir con estas reglas:
        - Asuntos internos del Congreso (integración o elección de comisiones y comités, designaciones internas, habilitación de recintos, convocatorias a sesiones) cuyo origen no se atribuye a nadie en el texto: "Junta de Gobierno y Coordinación Política".
        - Asuntos que el texto atribuye a las coordinaciones de los grupos y representaciones legislativas o a una propuesta de las bancadas: "Coordinadores de bancadas".
        - Si el texto NO hace ninguna referencia al presentador y no aplica ninguna de las reglas anteriores, usa null. Nunca inventes nombres de personas.
      | <Presentador>
    D. Presentador fuente: "Acta" si el texto nombra al presentador; "Inferido" si lo asignaste con las reglas anteriores; "Ninguno" si el presentador es null. | <Presentador fuente>

8. Retorna un JSON que cumpla estrictamente con la estructura especificada en la sección de "SALIDA REQUERIDA", donde TODO DATO DEBE TENER UN VALOR ASIGNADO, con dos excepciones: "Presentador" puede ser null (solo si no hay ninguna referencia a quién lo presenta) y "Comentario" puede ser "".


=== FIN DE INSTRUCCIONES ===


=== SALIDA REQUERIDA ===


Arreglo de objetos en formato JSON, tu respuesta debe contener ÚNICA Y EXCLUSIVAMENTE el JSON válido. No incluyas saludos, explicaciones, ni texto en formato markdown 
(como ```json). Solo el texto crudo del arreglo JSON.". Dicho JSON debe tener la siguiente estructura con todos los datos a incluir:

[
    {
        "Tipo" : <Tipo>,
        "Título" : <Título>,
        "Tema" : <Tema>,
        "Tema 1" : <Tema 1>,
        "A favor" : <Votos a favor>,
        "Contra" : <Votos en contra>,
        "Abstenciones" : <Abstenciones>,
        "Total" : <Total>,
        "Contenido" : <Contenido>,
        "Comentario" : <Comentario>,
        "Presentador" : <Presentador>,
        "Presentador fuente" : <Presentador fuente>
    }
        
]

=== FIN DE SALIDA REQUERIDA ===
        """

        full_prompt = f"{prompt_instructions}\n\n=== TEXTO ===\n{cleaned_text}\n=== FIN DE TEXTO ==="
        
        try:
            # Nuevo formato de llamada: client.models.generate_content
            # Pasamos las configuraciones (como forzar el JSON) mediante types.GenerateContentConfig
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            extracted_data = json.loads(response.text)
            return extracted_data
            
        except Exception as e:
            print(f"Error procesando con Gemini: {e}")
            return []
        
    def get_absences(self, vote: dict) -> int:
        try:
            total = int(vote.get("Total", 41))
            a_favor = int(vote.get("A favor", 0))
            contra = int(vote.get("Contra", 0))
            abstenciones = int(vote.get("Abstenciones", 0))
            
            return total - (a_favor + contra + abstenciones)
        except ValueError:
            print("Advertencia: Se encontraron valores no numéricos en los votos.")
            return 0

    def process_file(self, file_content: str, folio_manager=None, acta_id=None) -> list[dict]:
        """Procesa el archivo combinando reglas estáticas y la extracción de la IA"""

        periods = { 1 : "Primer periodo", 2 : "Segundo periodo", 3 : "Tercer periodo" }
        years = { 1 : "Primer año", 2 : "Segundo año", 3 : "Tercer año" }
        
        date = self.get_date(file_content)
        classifier = EventClassifier()
        
        if not date:
            print("No se pudo extraer la fecha del documento")
            return []
        
        # Guardamos los valores enteros que nos da el clasificador, porque el FolioManager los necesita para calcular matemáticamente.
        period_int = classifier.classify_per_period(date)
        year_int = classifier.classify_per_year(date)

        # Convertimos a string para el registro JSON
        period_str = periods.get(period_int, "Desconocido")
        legislative_year_str = years.get(year_int, "Desconocido")

        base_record = {
            "Año Legislatura" : legislative_year_str,
            "Periodo" : period_str,
            "Fecha": date,
        }
        
        print("Enviando texto a Gemini 3.1 Flash Lite para extraer votaciones...")
        votes = self._extract_votes(file_content)
        
        final_records = []
        for vote in votes:
            # Función local para casteo seguro
            def safe_int(key, default=0):
                try:
                    # Usamos .get() para evitar KeyError y luego intentamos convertir
                    return int(vote.get(key, default))
                except (ValueError, TypeError):
                    # Si la IA mandó texto, vacío o None, regresamos el default
                    return default

            # Castear datos numéricos de forma segura
            vote["Tema 1"] = safe_int("Tema 1", default=0)
            vote["A favor"] = safe_int("A favor", default=0) 
            vote["Contra"] = safe_int("Contra", default=0)
            vote["Abstenciones"] = safe_int("Abstenciones", default=0)
            
            # Asumimos 41 por defecto para el total si algo sale mal
            vote["Total"] = safe_int("Total", default=41) 

            # Datos complementarios (solo los consume script7 / ActasProcessor; el writer de Excel los ignora).
            # Se sacan de 'vote' para añadirlos AL FINAL del registro y no alterar el orden de las llaves existentes.
            presenter = vote.pop("Presentador", None)
            presenter = str(presenter).strip() if presenter else None
            if presenter and presenter.lower() in ("null", "none", "n/a", "ninguno"):
                presenter = None
            presenter_src = str(vote.pop("Presentador fuente", "") or "").strip().capitalize()
            if not presenter:
                presenter_src = "Ninguno"
            elif presenter_src not in ("Acta", "Inferido"):
                presenter_src = "Inferido" if presenter in ("Junta de Gobierno y Coordinación Política",
                                                            "Coordinadores de bancadas") else "Acta"
            extras = {
                "Contenido": str(vote.pop("Contenido", "") or "").strip(),
                "Comentario": str(vote.pop("Comentario", "") or "").strip(),
                "Presentador": presenter,
                "Presentador fuente": presenter_src,
            }
            if acta_id:
                extras["Acta_id"] = str(acta_id)

            absences = self.get_absences(vote)
            
            # Generamos los folios específicos para ESTE registro
            if folio_manager and period_int and year_int:
                folio_leg, folio_per = folio_manager.generate_folios(date, year_int, period_int)
            else:
                folio_leg, folio_per = None, None

            merged_record = {**base_record, **vote}
            merged_record["Ausentes"] = absences
            merged_record["Folio legislatura"] = folio_leg
            merged_record["Folio periodo"] = folio_per
            merged_record.update(extras)
            
            final_records.append(merged_record)
            
        return final_records
    



if __name__ == '__main__':
    pdf_processor = PDFProcessor()
    processor = GacetaProcessor()

    file_path = 'C:\\Users\\WARNE\\OneDrive\\Escritorio\\Projects\\Python\\DataExtractionGP\\data\\pdfs\\minutes\\acta_60238.pdf'
    print(f"Procesando archivo: {file_path}")
    
    text = pdf_processor.extract_text(file_path)
    cleaned_text = pdf_processor.clean_text(text)

    data = processor.process_file(cleaned_text)

    output_filename = "temp_output.json"
    
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    print(f"¡Listo! Se extrajeron {len(data)} registros de votación.")
    print(f"Los datos se han guardado en: {output_filename}")