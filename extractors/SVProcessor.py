# SVProcessor.py

import os
import re
import sys
import json
import time
from datetime import datetime

# Nuevas importaciones del SDK actualizado
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from lib.PDFProcessor import PDFProcessor
from lib.ExtractionErrors import ExtractionFailedError

# ==========================================
# CONFIGURACIÓN DE LA API DE GEMINI 3.1
# ==========================================

# Cargar variables de entorno desde el archivo .env
load_dotenv()

# Obtener de variables de entorno, de archivo .env
API_KEY = os.getenv("GEMINI_API_KEY")

# Tope máximo de espera entre reintentos (segundos). Se mantiene deliberadamente
# corto: si la cuota diaria/por minuto de Gemini está agotada, el retryDelay que
# regresa la API puede ser grande, y con muchos documentos por procesar esas
# esperas se acumulan hasta hacer que el proceso completo tome horas. Es
# preferible fallar rápido, marcar el registro como pendiente y seguir con el
# siguiente archivo; los pendientes se reintentan en la siguiente ejecución.
MAX_RETRY_WAIT_SECONDS = 10

# Número máximo de intentos por registro. Al agotarse, se lanza
# ExtractionFailedError para que el scraper marque el registro como pendiente
# y continúe de inmediato con el siguiente archivo.
MAX_ATTEMPTS = 2

class SVProcessor:

    def __init__(self):
        # En el nuevo SDK, instanciamos un Cliente en lugar de configurar el módulo globalmente
        self.client = genai.Client(api_key=API_KEY)
        # Definimos el modelo actualizado que nos compartiste
        self.model_id = "gemini-3.1-flash-lite"

    def _calculate_duration_minutes(self, start_str: str, end_str: str):
        """Calcula la diferencia en minutos entre el inicio y el fin de la sesión"""
        try:
            start = datetime.strptime(start_str.strip(), "%H:%M")
            end = datetime.strptime(end_str.strip(), "%H:%M")
            diff_minutes = (end - start).total_seconds() / 60

            # Si el fin quedó "antes" que el inicio, asumimos que la sesión cruzó la medianoche
            if diff_minutes < 0:
                diff_minutes += 24 * 60

            return int(diff_minutes)
        except (ValueError, AttributeError, TypeError):
            return None

    def _extract_retry_delay(self, error_text: str):
        """
        Busca un retryDelay explícito en el mensaje de error de la API
        (por ejemplo: 'Please retry in 5.088741897s.' o "'retryDelay': '5s'").
        Regresa los segundos a esperar, o None si no se encontró.
        """
        match = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s['\"]", error_text)
        if match:
            return float(match.group(1))

        match = re.search(r"retry in\s+(\d+(?:\.\d+)?)s", error_text, re.IGNORECASE)
        if match:
            return float(match.group(1))

        return None

    def _call_gemini_with_retry(self, full_prompt: str) -> dict:
        """
        Llama a la API de Gemini y parsea el JSON de respuesta, reintentando
        hasta MAX_ATTEMPTS veces ante cualquier falla (429 RESOURCE_EXHAUSTED,
        cortes de red, errores 5xx, respuesta con JSON mal formado, etc.).

        - Si el error trae un retryDelay explícito (típico del 429), se respeta ese tiempo.
        - Si no, se aplica backoff exponencial (2, 4, 8... segundos) con un tope máximo.
        - Si se agotan los MAX_ATTEMPTS intentos, se lanza ExtractionFailedError.
        """
        last_error = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_id,
                    contents=full_prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                return json.loads(response.text)

            except Exception as e:
                last_error = e
                error_text = str(e)
                print(f"Error procesando con Gemini (intento {attempt}/{MAX_ATTEMPTS}): {error_text}")

                if attempt == MAX_ATTEMPTS:
                    break

                wait_seconds = self._extract_retry_delay(error_text)

                if wait_seconds is None:
                    # Sin retryDelay explícito: una espera corta y fija basta,
                    # ya que solo hay un segundo intento por delante.
                    wait_seconds = 3
                else:
                    # Respetamos el retryDelay indicado por la API, pero acotado
                    # a MAX_RETRY_WAIT_SECONDS para no acumular esperas largas
                    # a lo largo de muchos documentos.
                    wait_seconds = min(wait_seconds + 1, MAX_RETRY_WAIT_SECONDS)

                print(f"Reintentando en {wait_seconds:.1f} segundos...")
                time.sleep(wait_seconds)

        raise ExtractionFailedError(
            f"No fue posible obtener una respuesta válida de Gemini tras {MAX_ATTEMPTS} intentos. "
            f"Último error: {last_error}"
        )

    def _extract_session_data(self, cleaned_text: str) -> dict:
        """Envía el texto a Gemini utilizando el prompt estructurado, con reintentos limitados"""

        prompt_instructions = """
=== CONTEXTO === 

Eres un analista de documentos legislativos, recibes el texto extraido y sin frases basura de una
transcripción escrita, palabra por palabra, de lo que se dice en una comisión o comité, denominado 
como "Versión estenográfica". Este documento explica lo que sucedió durante una sesión de la 
cámara de diputados del estado de Puebla, incluyendo el desarrollo del orden del día, las 
intervenciones de las y los diputados, y en su caso, la votación de los asuntos tratados.

Los datos que debes extraer de este documento son:
* Tipo
* Asunto abordado
* Fecha de sesión en comisión / comité
* Presentador
* Partido
* Estatus
* Hora Programada
* Inicio de la sesión
* Fin de la sesión
* Observaciones

Tu trabajo es analizar a detalle el texto proveniente de la Versión Estenográfica y extraer cada uno 
de los datos mencionados, siguiendo estrictamente las instrucciones siguientes.

=== FIN DE CONTEXTO ===


=== INSTRUCCIONES ===

1. Analiza a detalle el contenido de la versión estenográfica, la cual se encuentra dentro de los 
delimitadores de 'TEXTO'. Una versión estenográfica corresponde a UNA SOLA sesión, por lo que 
debes extraer un único conjunto de datos, no una lista.

2. IDENTIFICACIÓN DEL ASUNTO PRINCIPAL: Dentro del orden del día de la sesión existen puntos de 
mero trámite (pase de lista, declaración de quórum, lectura y aprobación del orden del día, 
dispensa y aprobación del acta de la sesión anterior, asuntos generales) y, casi siempre, un punto 
central que es el motivo real de la sesión (un acuerdo, un dictamen, una comparecencia, la 
presentación de un plan de trabajo, un informe, etc.). Identifica cuál es ese punto central: 
normalmente es el punto con mayor desarrollo y discusión dentro del documento, y sobre el cual 
gira el resto de las instrucciones siguientes. Ignora los puntos de mero trámite para las 
instrucciones 3 a 8, salvo cuando se indique lo contrario.

3. Clasifica el asunto principal identificado en el paso 2 como "Punto de Acuerdo", "Iniciativa" u 
"Otro", buscando en su contenido palabras clave para cada tipo, tomando en cuenta también la 
definición de cada tipo de evento:
- Palabras clave por tipo:
    * Iniciativa: [
            'dictamen', 'dictamenes', 
            'reforma', 'reformas', 'reformar', 'reforman', 'reformado',
            'decreto', 'decretos', 
            'proposicion', 'proposiciones', 
            'propuesta', 'propuestas', 'propuesto',
            'expide', 'expiden', 'expedir', 'expedicion', 'expediciones',
            'minuta', 'minutas', 'minuta de decreto',
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
    Nota: Estas palabras pueden contener acentos o tener ligeras variaciones de escritura.
    - Si el asunto principal no corresponde a ninguna de las dos categorías anteriores (por ejemplo: 
    instalación de la comisión, presentación de un plan de trabajo, una comparecencia, un informe, 
    una intervención o presentación temática), clasifícalo como "Otro".
    RESULTADO: Tipo de sesión, donde el valor debe ser EXCLUSIVAMENTE uno de los siguientes 
    números enteros:
        1 - Si el asunto principal es una Iniciativa
        2 - Si el asunto principal es un Punto de Acuerdo
        3 - Si es "Otro" (no aplica en ninguno de los dos anteriores)

4. Extrae el "Asunto abordado": redacta un párrafo que describa el asunto principal identificado en 
el paso 2, basándote en el contenido literal de la sesión (por ejemplo, la lectura del punto del orden 
del día, o el planteamiento que hace el área jurídica o el presentador). No es necesario copiar el 
texto de manera literal, pero SÍ debes conservar los datos, cifras y referencias legales exactas que 
se mencionen (números de artículos, leyes citadas, ayuntamientos involucrados, etc.). Redacta con 
buena ortografía y de forma clara, como si fuera la descripción de un punto de orden del día.

5. Obtén el nombre completo del "Presentador": la persona (diputada, diputado) o instancia 
(por ejemplo, "Dirección General de Asuntos Jurídicos, de Estudios y de Proyectos Legislativos", 
una Secretaría del Ejecutivo, etc.) que presenta, propone o expone el asunto principal identificado 
en el paso 2. Búscalo en frases como "el punto de acuerdo que presentó...", "propuesto por...", 
"a cargo de...", o en quien hace uso de la palabra para exponer el tema ante la comisión. 
Si quien expone es distinto de quien originalmente presentó el asunto (por ejemplo, el área jurídica 
da lectura y explica un acuerdo presentado meses antes por otra persona), el "Presentador" es quien 
ORIGINALMENTE presentó el asunto, no quien lo expone en la sesión. Si no es posible identificar a 
un presentador específico, puedes seguir 3 caminos:
    * El presentador puede ser la persona que expuso por primera vez los datos de mayor importancia en la
    sesión, por lo general, la presidenta de la sesión
    * El presentador puede ser literalmente la DIRECCIÓN GENERAL DE ASUNTOS JURÍDICOS, DE ESTUDIOS Y DE PROYECTOS LEGISLATIVOS,
    solo si asi se ha descrito explicitamente
    * Puede no haber presentador, para lo cual se asigna un valor de null.

6. Obtén el "Partido" político al que pertenece el Presentador identificado en el paso 5, convertido 
siempre a sus siglas oficiales (ejemplos: "Partido Verde Ecologista de México" → PVEM, "Acción 
Nacional" → PAN, "Movimiento Ciudadano" → MC, "Morena" → MORENA, "Partido del Trabajo" → PT, 
"Nueva Alianza" → Nueva Alianza, "Fuerza por México" → FXMX, "Partido Revolucionario 
Institucional" → PRI). Si el Presentador es una instancia (dirección jurídica, secretaría, ciudadanos, 
etc.) y no una o un diputado con partido, asigna "N/A". Si no se menciona el partido en absoluto, 
asigna null.

7. Determina el "Estatus" del asunto principal:
    - "Aprobado", si el texto indica que fue votado y aprobado (por unanimidad, por mayoría, etc.)
    - "No aprobado", si el texto indica explícitamente que fue rechazado o no alcanzó los votos 
    necesarios
    - "N/A", si el asunto principal no fue sometido a votación (por ejemplo: presentación de un 
    plan de trabajo, una comparecencia, un informe, una intervención, sesión de instalación)

8. Extrae los siguientes horarios, buscando frases como "se abre la sesión... siendo las/a las [hora]" 
para el inicio, y "se levanta la sesión siendo las [hora]" para el fin. Convierte los horarios a formato 
de 24 horas "HH:MM":
    - "Inicio de la sesión": hora exacta en que se declaró abierta la sesión
    - "Fin de la sesión": hora exacta en que se declaró levantada la sesión
    - "Hora Programada": la hora a la que fue convocada originalmente la sesión (mencionada en un 
    citatorio o convocatoria), SOLO si se menciona explícitamente en el texto. Si el documento no 
    menciona una hora de convocatoria distinta a la de inicio real, asigna null.
    Si alguno de estos datos no se menciona en el texto, asigna null.

9. Extrae las "Observaciones": cualquier dato relevante o fuera de lo común mencionado en la 
sesión que no quede capturado en los campos anteriores, por ejemplo:
    - Diputadas o diputados con falta justificada, o con inasistencia
    - Abstenciones en la votación y quién las emitió
    - Que se haya dispensado la lectura del acta de la sesión anterior
    - Que el asunto principal haya sido presentado originalmente por alguien de una legislatura 
    anterior, señalando quién y cuándo
    - Cualquier incidencia, aclaración o comentario relevante señalado por la presidencia o algún 
    integrante de la comisión
    Redacta un como máximo dos renglones. Si no hay nada relevante que señalar, asigna null.

10. Retorna un JSON que cumpla estrictamente con la estructura especificada en la sección de 
"SALIDA REQUERIDA". Todo dato debe tener un valor asignado (usa null cuando el dato 
verdaderamente no se pueda obtener del texto, siguiendo las instrucciones anteriores).

=== FIN DE INSTRUCCIONES ===


=== SALIDA REQUERIDA ===

Objeto JSON, tu respuesta debe contener ÚNICA Y EXCLUSIVAMENTE el JSON válido. No incluyas 
saludos, explicaciones, ni texto en formato markdown (como ```json). Solo el texto crudo del 
objeto JSON. Dicho JSON debe tener la siguiente estructura con todos los datos a incluir:

{
    "Tipo" : <1, 2 o 3>,
    "Asunto abordado" : <Asunto abordado>,
    "Fecha de sesión en comisión / comité" : <Fecha en formato YYYY-MM-DD, o null>,
    "Presentador" : <Presentador, o null>,
    "Partido" : <Partido, "N/A" o null>,
    "Estatus" : <"Aprobado", "No aprobado" o "N/A">,
    "Hora Programada" : <Hora en formato HH:MM, o null>,
    "Inicio de la sesión" : <Hora en formato HH:MM, o null>,
    "Fin de la sesión" : <Hora en formato HH:MM, o null>,
    "Observaciones" : <Observaciones, o null>
}

=== FIN DE SALIDA REQUERIDA ===
        """

        full_prompt = f"{prompt_instructions}\n\n=== TEXTO ===\n{cleaned_text}\n=== FIN DE TEXTO ==="

        # Lanza ExtractionFailedError si se agotan los MAX_ATTEMPTS intentos.
        # No se captura aquí a propósito: process_file y, en última instancia,
        # el scraper son quienes deciden qué hacer con un registro fallido.
        return self._call_gemini_with_retry(full_prompt)

    def process_file(self, file_content: str) -> dict:
        """
        Procesa el texto de una versión estenográfica y regresa los datos de la sesión.

        Lanza ExtractionFailedError si, tras MAX_ATTEMPTS intentos, no fue posible
        obtener una respuesta válida de Gemini. El llamador debe capturar esta
        excepción explícitamente; no se atrapa aquí para no confundir un registro
        fallido con uno cuyos campos legítimamente vienen vacíos.
        """

        default_record = {
            "Tipo": None,
            "Asunto abordado": None,
            "Fecha de sesión en comisión / comité": None,
            "Presentador": None,
            "Partido": None,
            "Estatus": None,
            "Hora Programada": None,
            "Inicio de la sesión": None,
            "Fin de la sesión": None,
            "Tiempo": None,
            "Observaciones": None
        }

        if not file_content:
            print("No se recibió texto para procesar")
            return default_record

        print("Enviando texto a Gemini 3.1 Flash Lite para extraer datos de la sesión...")
        extracted = self._extract_session_data(file_content)

        record = {**default_record, **extracted}

        # Calculamos "Tiempo" de forma aritmética, no se lo pedimos a la IA
        record["Tiempo"] = self._calculate_duration_minutes(
            record.get("Inicio de la sesión"),
            record.get("Fin de la sesión")
        )

        return record


if __name__ == '__main__':
    pdf_processor = PDFProcessor()
    processor = SVProcessor()

    file_path = 'C:\\Users\\WARNE\\OneDrive\\Escritorio\\Projects\\Python\\DataExtractionGP\\data\\pdfs\\svs\\ejemplo.pdf'
    print(f"Procesando archivo: {file_path}")

    text = pdf_processor.extract_text(file_path)
    cleaned_text = pdf_processor.clean_text(text)

    try:
        data = processor.process_file(cleaned_text)
    except ExtractionFailedError as e:
        print(f"La extracción falló tras varios intentos: {e}")
        data = None

    if data is not None:
        output_filename = "temp_sv_output.json"
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        print(f"¡Listo! Los datos se han guardado en: {output_filename}")