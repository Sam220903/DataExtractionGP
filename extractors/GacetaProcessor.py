# GacetaProcessor.py

from datetime import datetime
import difflib
import re
import os
import sys
import json
import time
import unicodedata

# Nuevas importaciones del SDK actualizado
from google import genai
from google.genai import types
from google.genai import errors as genaiErrors
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

    # Versión del prompt: súbela cuando cambies el prompt para poder reanalizar actas antiguas
    PROMPT_VERSION = 3

    # Reintentos ante errores temporales (alta demanda, límite por minuto, red)
    MAX_ATTEMPTS = 4
    BASE_DELAY_SECONDS = 5

    # Errores tras los cuales no tiene sentido seguir llamando a la IA en esta ejecución
    FATAL_ERROR_TYPES = ("cuota_agotada", "cuota_gratuita_agotada", "credenciales_invalidas")

    # Errores que indican que se terminó el margen de uso de la IA (gratuito o de pago)
    QUOTA_ERROR_TYPES = ("cuota_agotada", "cuota_gratuita_agotada")

    # Mensajes para el usuario según el motivo del fallo
    ERROR_MESSAGES = {
        "alta_demanda": "El modelo está saturado por alta demanda. Intenta de nuevo más tarde.",
        "cuota_agotada": "Se agotó la cuota o los tokens de la API de Gemini.",
        "cuota_gratuita_agotada": "Se agotó el margen gratuito diario de la API de Gemini.",
        "credenciales_invalidas": "La API key es inválida o no tiene permisos. Revisa GEMINI_API_KEY en el archivo .env.",
        "error_red": "Falló la conexión con la API de Gemini.",
        "respuesta_invalida": "El modelo devolvió una respuesta que no es un JSON válido.",
        "error_api": "La API de Gemini devolvió un error inesperado.",
        "error_desconocido": "Ocurrió un error desconocido al llamar a la IA.",
        "error_ia": "Falló el procesamiento con IA.",
        "sin_fecha": "No se pudo extraer la fecha del acta.",
        "sin_texto": "No se pudo extraer texto del PDF.",
    }

    # ==========================================
    # CONFIGURACIÓN DE LA NORMALIZACIÓN DEL PRESENTADOR
    # ==========================================

    # perfiles.json (lo genera el Script 1). Si no existe, solo se omite el ajuste de nombres de diputados.
    PROFILES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'perfiles.json')

    # Presentadores institucionales con nombre único
    JGCP_NAME = "Junta de Gobierno y Coordinación Política"
    COORDINATORS_NAME = "Coordinadores de bancadas"

    # Valores que el modelo a veces escribe en lugar de null
    NULL_PRESENTER_VALUES = ("null", "none", "n/a", "ninguno", "ninguna", "no aplica", "sin presentador")

    # Quienes NUNCA cuentan como presentador (texto sin acentos ni puntuación). Para editarlo, agrega o quita patrones aquí.
    NON_PRESENTER_REGEX = re.compile(
        r"mesa directiva|ejecutivo federal|presidenta? de la republica|"
        r"direccion general de asuntos juridicos|secretaria general"
    )

    # "que presenta la ...", "presentada por el ...", "a propuesta del ..." al inicio del valor
    LEADING_PHRASE_REGEX = re.compile(
        r"^(?:(?:el|la|los|las)\s+)?(?:(?:propuesta|iniciativa|acuerdo|dictamen)s?\s+)?"
        r"(?:que\s+presentan?|presentad[oa]s?\s+por|a\s+propuesta\s+de(?:l)?)\s+(?:(?:el|la|los|las)\s+)?",
        re.IGNORECASE
    )

    # "... de la Sexagésima Segunda Legislatura del Honorable Congreso del Estado" al final del valor
    TRAILING_LEGISLATURE_REGEX = re.compile(
        r"\s+de\s+la\s+(?:sexag[eé]sima\s+segunda|lxii)\s+legislatura.*$", re.IGNORECASE
    )

    # "Diputado", "Diputada", "Dip." antes de un nombre
    HONORIFIC_REGEX = re.compile(r"^(?:(?:los|las|el|la)\s+)?(?:diputad[oa]s?|dips?\.?)\s+", re.IGNORECASE)

    # Una comisión o comité y todo lo que sigue (descarta "diputado presidente de la ...")
    COMMISSION_REGEX = re.compile(r"(comisi[oó]n(?:es)?|comit[eé]s?)\s+.*$", re.IGNORECASE)

    COORDINATORS_REGEX = re.compile(
        r"coordinadores? de (?:las |los )?(?:bancadas|grupos|representaciones)|^coordinadores"
    )

    # Similitud mínima para corregir una variante de escritura de un diputado ("Ariana" -> "Arianna")
    FUZZY_NAME_RATIO = 0.9

    def __init__(self):
        # En el nuevo SDK, instanciamos un Cliente en lugar de configurar el módulo globalmente
        self.client = genai.Client(api_key=API_KEY)
        # Definimos el modelo actualizado que nos compartiste
        self.model_id = "gemini-3.1-flash-lite"

        # Estado de la última extracción (el script principal lo consulta)
        self.last_extraction_ok = True
        self.last_error_type = None
        self.last_error_detail = ""

        # Si la cuota se agota, se guarda el tipo y ya no se envía NINGUNA solicitud más a la IA
        # mientras viva este objeto (no se reinicia con _resetStatus)
        self.quota_exhausted_type = None

        # Nombres oficiales de los diputados (perfiles.json), para escribirlos siempre igual
        self.deputyIndex = self._loadDeputyIndex()

    # ==========================================
    # ESTADO Y CLASIFICACIÓN DE ERRORES DE IA
    # ==========================================

    def _resetStatus(self):
        self.last_extraction_ok = True
        self.last_error_type = None
        self.last_error_detail = ""

    def _setFailure(self, errorType: str, detail: str):
        self.last_extraction_ok = False
        self.last_error_type = errorType
        self.last_error_detail = detail
        if errorType in self.QUOTA_ERROR_TYPES:
            self.quota_exhausted_type = errorType
        print(f"Error de IA ({errorType}): {detail}")

    def isFatalError(self) -> bool:
        """True si el último error obliga a detener las llamadas a la IA (cuota agotada, API key inválida)"""
        return self.last_error_type in self.FATAL_ERROR_TYPES

    def isQuotaError(self) -> bool:
        """True si el último error fue porque se agotó el margen de uso de la IA (gratuito o de pago)"""
        return self.last_error_type in self.QUOTA_ERROR_TYPES

    def _isFreeTierMessage(self, message: str) -> bool:
        return "freetier" in message or "free_tier" in message or "free tier" in message

    def _classifyError(self, error: Exception) -> tuple:
        """Devuelve (tipo_de_error, se_puede_reintentar)"""
        message = str(error).lower()

        if isinstance(error, genaiErrors.APIError):
            code = error.code

            if code == 429:
                # Cuota diaria agotada: reintentar no sirve. Límite por minuto: sí puede servir esperar.
                if "perday" in message or "per day" in message:
                    if self._isFreeTierMessage(message):
                        return "cuota_gratuita_agotada", False
                    return "cuota_agotada", False
                return "cuota_agotada", True

            if code in (500, 502, 503, 504):
                return "alta_demanda", True

            if code in (401, 403):
                return "credenciales_invalidas", False

            if code == 400 and "api key" in message:
                return "credenciales_invalidas", False

            return "error_api", False

        errorClassName = type(error).__name__.lower()
        if isinstance(error, (ConnectionError, TimeoutError)):
            return "error_red", True
        if "connect" in errorClassName or "timeout" in errorClassName:
            return "error_red", True

        return "error_desconocido", False

    def _waitBeforeRetry(self, attempt: int, errorType: str):
        delay = self.BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        print(f"  {errorType}: reintentando en {delay}s (intento {attempt} de {self.MAX_ATTEMPTS})...")
        time.sleep(delay)

    def _parseVotes(self, response) -> list:
        """Convierte la respuesta del modelo en lista de votaciones. Lanza ValueError si no es válida."""
        if not response.text:
            raise ValueError("La respuesta del modelo llegó vacía")

        data = json.loads(response.text)
        if not isinstance(data, list):
            raise ValueError("La respuesta del modelo no es un arreglo JSON")
        return data

    def has_vote_anchor(self, cleaned_text: str) -> bool:
        """True si el texto menciona un conteo de votos o 'unanimidad de votos' (ancla de la regla A del prompt)"""
        pattern = r"unanimidad\s+de\s+votos|votos?\s+a\s+favor"
        return re.search(pattern, cleaned_text, re.IGNORECASE) is not None
    
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

    # ==========================================
    # NORMALIZACIÓN DEL PRESENTADOR
    # Deja el valor que devolvió el modelo siempre en el mismo formato, sin inventar datos.
    # ==========================================

    def _plainText(self, text: str) -> str:
        """Minúsculas, sin acentos ni puntuación, con espacios simples."""
        decomposed = unicodedata.normalize("NFD", (text or "").lower())
        withoutAccents = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
        return re.sub(r"[^a-z0-9]+", " ", withoutAccents).strip()

    def _loadDeputyIndex(self) -> list:
        """Lista de (nombre oficial, palabras normalizadas) de perfiles.json. Vacía si no se puede leer."""
        try:
            with open(self.PROFILES_PATH, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []

        if isinstance(data, dict):
            data = data.get("registros", [])
        if not isinstance(data, list):
            return []

        index = []
        for profile in data:
            if not isinstance(profile, dict) or not profile.get("Nombre"):
                continue
            officialName = re.sub(r"\s+", " ", str(profile["Nombre"])).strip()
            index.append((officialName, self._plainText(officialName).split()))
        return index

    def _joinNatural(self, items: list) -> str:
        """['A', 'B', 'C'] -> 'A, B y C'"""
        if len(items) <= 1:
            return "".join(items)
        return ", ".join(items[:-1]) + " y " + items[-1]

    def _canonicalDeputyName(self, name: str) -> str:
        """Nombre oficial de perfiles.json si el texto es ese diputado (completo, truncado o con una errata).
        Si no se reconoce, se devuelve tal cual: nunca se inventa ni se descarta un nombre."""
        words = self._plainText(name).split()
        if len(words) < 2 or not self.deputyIndex:
            return name

        prefixMatches = []
        for officialName, officialWords in self.deputyIndex:
            if words == officialWords:
                return officialName
            isShorter = len(words) < len(officialWords)
            isLongEnough = len(words) >= 3 or len(officialWords) == 3
            if isShorter and isLongEnough and officialWords[:len(words)] == words:
                prefixMatches.append(officialName)
        if len(prefixMatches) == 1:
            return prefixMatches[0]

        joinedWords = " ".join(words)
        bestName, bestRatio = name, 0.0
        for officialName, officialWords in self.deputyIndex:
            ratio = difflib.SequenceMatcher(None, joinedWords, " ".join(officialWords)).ratio()
            if ratio > bestRatio:
                bestName, bestRatio = officialName, ratio
        if bestRatio >= self.FUZZY_NAME_RATIO:
            return bestName
        return name

    def _cleanPresenterText(self, raw) -> str:
        """Quita espacios, comillas, 'que presenta la ...' al inicio y 'de la Sexagésima Segunda Legislatura...' al final."""
        text = re.sub(r"\s+", " ", str(raw)).strip(" .;:\"'")
        for _ in range(3):
            withoutLead = self.LEADING_PHRASE_REGEX.sub("", text)
            if withoutLead == text:
                break
            text = withoutLead
        return self.TRAILING_LEGISLATURE_REGEX.sub("", text).strip(" .;:,")

    def _normalizeInstitution(self, plain: str):
        """Nombre fijo si el texto habla de la Junta de Gobierno o de los coordinadores de bancadas; si no, None."""
        if "comision" in plain or "comite" in plain:
            return None
        if "junta de gobierno" in plain:
            return self.JGCP_NAME
        if self.COORDINATORS_REGEX.search(plain):
            return self.COORDINATORS_NAME
        return None

    def _normalizeCommission(self, text: str):
        """'Comisión de X' sin 'que presenta', sin artículo y sin 'diputado presidente de la ...'; si no hay comisión, None."""
        match = self.COMMISSION_REGEX.search(text)
        if not match:
            return None
        commission = self.TRAILING_LEGISLATURE_REGEX.sub("", match.group(0)).strip(" .;:,")
        return commission[0].upper() + commission[1:]

    def _normalizeNames(self, text: str) -> str:
        """Quita 'Diputado/Diputada/Dip.' y unifica cada nombre con perfiles.json. Une con comas y ' y '."""
        chunks = [c.strip() for c in re.split(r"\s*,\s*|\s+y\s+", text) if c.strip()]
        names = []
        for chunk in chunks:
            chunk = self.HONORIFIC_REGEX.sub("", chunk).strip()
            looksLikeName = len(chunk.split()) <= 7 and not re.search(r"\d", chunk)
            if looksLikeName:
                chunk = self._canonicalDeputyName(chunk)
            names.append(chunk)
        return self._joinNatural(names)

    def _normalizePresenter(self, raw):
        """Valor de 'Presentador' ya limpio, o None si no hay un presentador válido.
        Es idempotente: normalizar dos veces da lo mismo que normalizar una."""
        if raw is None:
            return None
        text = self._cleanPresenterText(raw)
        if not text or text.lower() in self.NULL_PRESENTER_VALUES:
            return None

        plain = self._plainText(text)
        if self.NON_PRESENTER_REGEX.search(plain):
            return None

        institution = self._normalizeInstitution(plain)
        if institution:
            return institution

        commission = self._normalizeCommission(text)
        if commission:
            return commission

        return self._normalizeNames(text)

    def _resolvePresenterSource(self, presenter, rawSource: str) -> str:
        """'Presentador fuente' coherente con el presentador final."""
        if not presenter:
            return "Ninguno"
        if rawSource in ("Acta", "Inferido"):
            return rawSource
        if presenter in (self.JGCP_NAME, self.COORDINATORS_NAME):
            return "Inferido"
        return "Acta"

    def _extract_votes(self, cleaned_text: str) -> list[dict]:
        """Envía el texto a Gemini utilizando el prompt estructurado"""

        # Si en esta ejecución ya se agotó la cuota, NO se envía nada más a la IA
        if self.quota_exhausted_type:
            self._setFailure(
                self.quota_exhausted_type,
                "La cuota ya se agotó en esta ejecución: no se envían más solicitudes a la IA"
            )
            return []
        
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
    | 15 | Telecomunicaciones |
    | 16 | Trabajo y Previsión Social |
    | 18 | Administración Pública |
    | 19 | Internos del Congreso |
    | 20 | Justicia y Estado de Derecho en temas de Mujeres | 
Resultado: El número de tema de la votación como <Tema 1> y el tema de la votación como <Tema>

7. Para cada votación extrae además 4 datos complementarios, usando ÚNICAMENTE lo que dice el texto (no inventes información):
    A. Contenido: resumen de 1 a 3 oraciones, en español neutro, de lo que propone o resuelve el asunto votado (qué ley, código o decreto se reforma, adiciona o expide y en qué consiste el cambio; a qué autoridad se exhorta y con qué fin; qué cargo, comisión o comité se elige o integra, etc.). Si el texto solo da el título del asunto, parafrasea ese título sin agregar detalles que no estén en el texto. | <Contenido>
    B. Comentario: observaciones relevantes que el acta consigna sobre ESTA votación, en una o dos oraciones: tipo de votación (nominal, secreta o económica), si hubo discusión y quién intervino, si el dictamen concentra varias iniciativas, ausencias o retardos justificados, instrucciones de publicación o envío. Si no hay nada relevante, usa "" (cadena vacía). | <Comentario>
    C. Presentador: quién presenta o propone el asunto votado ante el Pleno, tal como lo nombra el texto del acta. Decide siguiendo este orden:
        1. Dictámenes: si el asunto es un dictamen (por ejemplo "dictamen que presenta la Comisión de ...", "a nombre de la Comisión ..."), el presentador es la COMISIÓN dictaminadora, aunque el texto mencione también a quien preside la comisión, a quien lee el dictamen en tribuna o a los autores de las iniciativas que el dictamen concentra. Si son varias comisiones, escribe "Comisiones Unidas de X y Y".
        2. Asuntos presentados por legisladores: si el texto nombra a la diputada o al diputado que presenta el asunto (o a varios), ellos son el presentador.
        3. Órganos: si el texto atribuye el asunto a un órgano o a una autoridad que puede presentarlo (por ejemplo "Junta de Gobierno y Coordinación Política", un grupo legislativo, "Titular del Poder Ejecutivo del Estado", "Cámara de Senadoras y Senadores"), usa ese nombre.
        4. Inferencia: si el texto NO nombra al presentador, evalúa si se puede inferir con estas reglas:
            - Asuntos internos del Congreso (integración o elección de comisiones y comités, designaciones internas, habilitación de recintos, convocatorias a sesiones) cuyo origen no se atribuye a nadie en el texto: "Junta de Gobierno y Coordinación Política".
            - Asuntos que el texto atribuye a las coordinaciones de los grupos y representaciones legislativas o a una propuesta de las bancadas: "Coordinadores de bancadas".
        5. Si el texto NO hace ninguna referencia al presentador y no aplica ninguna de las reglas anteriores, usa null. Nunca inventes nombres de personas.
      NO son presentadores: las personas que solo intervienen o hacen uso de la voz en la discusión; la Presidencia o las Secretarías de la Mesa Directiva cuando solo anuncian, someten a consideración o leen el resultado; la Presidenta de la República o el Ejecutivo Federal. Los trámites de la Mesa Directiva (dispensa de lectura, aprobación de actas, prórroga de la sesión, orden del día) no tienen presentador: usa null, salvo que el texto atribuya la solicitud a una diputada o a un diputado concreto, en cuyo caso esa persona es el presentador.
      FORMATO del valor (obligatorio): un solo texto, nunca una lista.
        - Comisión: "Comisión de <nombre>", sin "que presenta", sin la palabra "la" al inicio y sin "de la Sexagésima Segunda Legislatura del Honorable Congreso del Estado".
        - Diputadas y diputados: nombre y apellidos completos tal como aparecen en el acta, SIN "Diputado", "Diputada" ni "Dip."; si son varios, separados por coma y con " y " antes del último. Si quien aparece no es diputado (por ejemplo, una persona ciudadana que solicita licencia), no lo uses como presentador.
        - Órganos: su nombre completo y oficial, por ejemplo "Junta de Gobierno y Coordinación Política" o "Coordinadores de bancadas".
      | <Presentador>
    D. Presentador fuente: "Acta" si el texto nombra al presentador (persona, comisión u órgano); "Inferido" ÚNICAMENTE si lo asignaste con las reglas de inferencia del punto 4 de la sección C; "Ninguno" si el presentador es null. | <Presentador fuente>

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

        # Un fallo de IA ya NO se confunde con "acta sin votaciones": se registra el motivo
        # en last_error_type / last_error_detail y el script principal decide qué hacer.
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
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
            except Exception as error:
                errorType, canRetry = self._classifyError(error)
                if canRetry and attempt < self.MAX_ATTEMPTS:
                    self._waitBeforeRetry(attempt, errorType)
                    continue
                self._setFailure(errorType, str(error))
                return []

            try:
                return self._parseVotes(response)
            except ValueError as error:
                if attempt < self.MAX_ATTEMPTS:
                    self._waitBeforeRetry(attempt, "respuesta_invalida")
                    continue
                self._setFailure("respuesta_invalida", str(error))
                return []

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

        self._resetStatus()

        periods = { 1 : "Primer periodo", 2 : "Segundo periodo", 3 : "Tercer periodo" }
        years = { 1 : "Primer año", 2 : "Segundo año", 3 : "Tercer año" }
        
        date = self.get_date(file_content)
        classifier = EventClassifier()
        
        if not date:
            print("No se pudo extraer la fecha del documento")
            self._setFailure("sin_fecha", "No se pudo extraer la fecha del documento")
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

        # Si la IA falló, no se continúa: el script principal revisa last_extraction_ok
        if not self.last_extraction_ok:
            return []
        
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
            # El presentador se normaliza (formato único, sin honoríficos, nombres de diputados como en perfiles.json)
            presenter = self._normalizePresenter(vote.pop("Presentador", None))
            rawPresenterSource = str(vote.pop("Presentador fuente", "") or "").strip().capitalize()
            presenter_src = self._resolvePresenterSource(presenter, rawPresenterSource)
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

    if not processor.last_extraction_ok:
        reasonMessage = processor.ERROR_MESSAGES.get(processor.last_error_type, processor.last_error_type)
        print(f"El archivo NO fue procesado: {reasonMessage}")
        sys.exit(1)

    output_filename = "temp_output.json"
    
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    print(f"¡Listo! Se extrajeron {len(data)} registros de votación.")
    print(f"Los datos se han guardado en: {output_filename}")