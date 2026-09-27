# GMProcessor.py
#
# Procesador de Gacetas Legislativas Mensuales (GM) del Congreso de Puebla.
#
# RONDA 2: reemplaza la versión placeholder. Toda la extracción de metadata
# de sesión se hace por expresiones regulares (SIN IA / sin Gemini), tal
# como se acordó. Los campos que dependen de EventClassifier/FolioManager
# (legislatura, periodo, folios) ya están enchufados con el código real que
# proporcionó el usuario.
#
# VALIDADO CONTRA: septiembre-2025pdf.pdf (Gaceta Legislativa No. 13,
# Septiembre 2025, Segundo Año, 1503 páginas, 7 sesiones). Los hallazgos de
# esa validación están documentados en los comentarios de cada método.
#
# PENDIENTE / A CONFIRMAR POR EL USUARIO (ver también el mensaje de chat):
#   1. "Fecha Gaceta Parlamentaria": no se pudo derivar de EventClassifier,
#      FolioManager ni del texto de la gaceta mensual. Queda en None.
#   2. "Número de sesión" = folio_legislatura y "Sesión letra" =
#      f"Sesión {folio_legislatura}" son una inferencia razonable a partir
#      del ejemplo de sesiones.json, no una regla explícita confirmada.
#      folio_periodo se calcula pero no se usa en ningún campo del schema.

import os
import sys
import re
import json
import unicodedata
from datetime import datetime, date, time, timedelta

# Nuevas importaciones del SDK de Gemini (comentadas: sin llamadas a IA en
# esta ronda, igual que en la versión placeholder anterior)
# from google import genai
# from google.genai import types
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from lib.EventClassifier import EventClassifier
from lib.FolioManager import FolioManager

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")


class GMProcessor:
    """Procesador de Gacetas Mensuales (GM) — extracción 100% por regex."""

    # ------------------------------------------------------------------
    # Diccionarios de conversión de texto en español
    # ------------------------------------------------------------------

    MESES = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
        "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
        "octubre": 10, "noviembre": 11, "diciembre": 12,
    }

    # Números en palabras (0-59). Suficiente para horas (0-23) y minutos
    # (0-59) y para los días citados en letra ("ONCE", "QUINCE",
    # "DIECIOCHO", "VEINTICINCO", etc.). Los compuestos 31-59 se resuelven
    # combinando decena + "Y" + unidad (ver _spanish_words_to_int).
    _NUM_PALABRAS = {
        "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3,
        "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
        "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
        "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
        "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21,
        "veintiun": 21, "veintidos": 22, "veintitres": 23,
        "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
        "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
        "treinta": 30, "cuarenta": 40, "cincuenta": 50,
    }

    # Frases-ancla de "asunto no abordado" (primera estimación, ver nota en
    # el documento de contexto: no se encontró ningún caso real en
    # septiembre 2025, así que esta lista es un punto de partida a afinar
    # cuando aparezca un caso real).
    _FRASES_NO_ABORDADO = [
        "no se abordo", "se retira del orden del dia",
        "queda pendiente", "no fue posible", "se pospone",
        "sin abordar", "se difiere",
    ]

    def __init__(self):
        self.event_classifier = EventClassifier()
        self.folio_manager = FolioManager()

        # Cuando se desarrolle la extracción con IA (votaciones, etc. si
        # llegara a aplicar a GM), descomentar:
        # self.client = genai.Client(api_key=API_KEY)
        # self.model_id = "gemini-3.1-flash-lite"

    # ------------------------------------------------------------------
    # Helpers de texto / normalización
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_accents(s: str) -> str:
        return ''.join(
            c for c in unicodedata.normalize('NFD', s)
            if unicodedata.category(c) != 'Mn'
        )

    @classmethod
    def _clean_footer_noise(cls, text: str) -> str:
        """
        Quita ruido de encabezado/pie de página que pdftotext/PDFProcessor
        suele dejar intercalado en medio del texto (dirección, sitio web,
        "Pag. N", saltos de página \\f). No es indispensable para que los
        regex de esta clase funcionen (la mayoría toleran texto intermedio),
        pero reduce falsos cortes -- en particular cuando un salto de
        página cae justo en medio de "SIENDO LAS ... HORAS" y deja basura
        de encabezado pegada ahí (comprobado en septiembre-2024pdf.pdf).
        """
        text = text.replace('\x0c', '\n')
        text = re.sub(r'Av\.\s*32\s*Oriente.*?C\.P\.\s*\d+', ' ', text)
        text = re.sub(r'www\.congresopuebla\.gob\.mx', ' ', text)
        text = re.sub(r'Pag\.\s*\d+', ' ', text)
        # Encabezado corrido de página propio de algunas gacetas (distinto
        # al de septiembre 2025): "H. CONGRESO DEL ESTADO", a veces partido
        # por un salto de línea en medio ("DEL \nESTADO") porque el salto
        # de página cayó ahí -- se usa \s+ en vez de espacio literal para
        # tolerar eso. "Secretaría General" es otra línea repetida del
        # mismo encabezado.
        text = re.sub(r'H\.\s*CONGRESO\s+DEL\s+ESTADO', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'Secretar[ií]a\s+General', ' ', text, flags=re.IGNORECASE)
        # "ACTA" como línea aislada (encabezado repetido de página) -- no
        # se quita como substring en cualquier parte (rompería "ACTA DE LA
        # SESIÓN..." y similares), solo cuando ocupa toda la línea.
        text = re.sub(r'^[ \t]*ACTA[ \t]*$', ' ', text, flags=re.MULTILINE | re.IGNORECASE)
        return text

    @classmethod
    def _spanish_words_to_int(cls, s: str):
        """
        Convierte un número en palabras (0-59) a entero. Acepta compuestos
        tipo "TREINTA Y SEIS" / "CINCUENTA Y TRES". Devuelve None si no
        se puede resolver (para que el llamador decida qué hacer).

        NOTA: si el proyecto ya tiene un _spanish_to_int en GacetaProcessor,
        lo ideal es reemplazar este método por una llamada a esa función
        para no mantener dos implementaciones. Se deja aquí una versión
        propia porque no se contó con ese archivo en esta ronda.
        """
        if s is None:
            return None
        s = cls._strip_accents(s.strip().lower())
        s = re.sub(r'\s+', ' ', s)

        if s in cls._NUM_PALABRAS:
            return cls._NUM_PALABRAS[s]

        if ' y ' in s:
            decena_txt, unidad_txt = s.split(' y ', 1)
            decena = cls._NUM_PALABRAS.get(decena_txt.strip())
            unidad = cls._NUM_PALABRAS.get(unidad_txt.strip())
            if decena is not None and unidad is not None:
                return decena + unidad

        return None

    @classmethod
    def _parse_dia_token(cls, token: str):
        """Un día de mes puede venir en dígito ("9") o en palabra ("ONCE")."""
        token = token.strip()
        if token.isdigit():
            return int(token)
        return cls._spanish_words_to_int(token)

    @classmethod
    def _parse_hora_texto(cls, hora_txt: str, minutos_txt: str = None) -> time:
        """'DIEZ' + 'TREINTA Y SEIS' -> time(10, 36, 0)."""
        hora = cls._spanish_words_to_int(hora_txt)
        minutos = cls._spanish_words_to_int(minutos_txt) if minutos_txt else 0
        if hora is None:
            return None
        if minutos is None:
            minutos = 0
        # Las horas en las actas son formato 12h sin am/pm explícito, pero
        # todas las sesiones observadas ocurren en horario matutino/
        # vespertino temprano (nunca pasan de las 14 horas), así que no
        # hace falta lógica de 12/24h aquí.
        return time(hour=hora % 24, minute=minutos % 60, second=0)

    # ------------------------------------------------------------------
    # Formateo de campos de salida
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_hora_24(t: time) -> str:
        return t.strftime("%H:%M:%S")

    @staticmethod
    def _fmt_hora_ampm(t: time) -> str:
        suffix = "a.m." if t.hour < 12 else "p.m."
        hour12 = t.hour % 12
        if hour12 == 0:
            hour12 = 12
        return f"{hour12:02d}:{t.minute:02d}:{t.second:02d} {suffix}"

    @staticmethod
    def _fmt_duracion(delta: timedelta) -> str:
        total_seconds = int(delta.total_seconds())
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"

    @staticmethod
    def _fmt_intervalo_hhmmss(delta: timedelta) -> str:
        total_seconds = int(delta.total_seconds())
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    # ------------------------------------------------------------------
    # Segmentación del documento en bloques por sesión
    # ------------------------------------------------------------------

    # "ORDEN DEL DÍA" como línea aislada (solo espacios antes/después) es
    # el ancla más confiable: aparece EXACTAMENTE una vez por sesión (se
    # validó: 7 apariciones para 7 sesiones en septiembre 2025), a
    # diferencia del encabezado del Acta, que varía de redacción y en un
    # caso (Comisión Permanente del 10/09/2025) ni siquiera trae la frase
    # "CELEBRADA EL <fecha>" porque el Acta de esa sesión es solo una
    # portada sin texto narrativo.
    #
    # OJO: en gacetas donde el Orden del día de una sesión ocupa varias
    # páginas, algunas repiten "ORDEN DEL DÍA" como encabezado corrido en
    # cada página siguiente (comprobado en septiembre-2024pdf.pdf: "ORDEN
    # DEL DÍA / Período Ordinario / Septiembre 19 de 2024_12hrs / Pág.2").
    # Esas anclas falsas se filtran en _segment_sessions con
    # _RE_QUE_CELEBRA (ver más abajo) antes de usarse como frontera.
    _ANCLA_ORDEN_DIA = re.compile(r'^[ \t]*ORDEN DEL D[IÍ]A[ \t]*$', re.MULTILINE)

    # Filtro anti-falsos-positivos para el caso de arriba: una ancla real
    # siempre trae "que celebra(n)" a corta distancia (la frase "Sesión
    # ... que celebra(n) la(s) ... Legislatura(s)"); una página de
    # continuación del Orden del día no.
    _RE_QUE_CELEBRA = re.compile(r'que\s+celebra', re.IGNORECASE)
    _VENTANA_VALIDACION_ANCLA = 400

    # Portada bajo "ORDEN DEL DÍA": "Sesión <tipo> que celebra(n) la(s)"
    # seguida, unas líneas después, de "<Día semana> <día> de <mes> del <año>".
    # NO se exige la palabra "la"/"las" después de "celebra": se comprobó
    # que la Sesión Previa de instalación de legislatura usa "que celebran
    # las" (plural) en vez de "que celebra la" (singular).
    _RE_TIPO_SESION = re.compile(
        r'Sesi[oó]n\s+(.+?)\s+que\s+celebra',
        re.IGNORECASE
    )
    _RE_FECHA_PORTADA = re.compile(
        r'(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+del?\s+(\d{4})',
        re.IGNORECASE
    )

    def _segment_sessions(self, text: str) -> list:
        """
        Divide el texto completo de la gaceta en bloques, uno por sesión,
        usando las apariciones de "ORDEN DEL DÍA" como frontera. Devuelve
        una lista de dicts: {"tipo_sesion", "fecha", "texto"} en el mismo
        orden en que aparecen en el documento (que en la práctica es
        orden cronológico ascendente, indispensable para que
        FolioManager acumule los folios correctamente).
        """
        todas = list(self._ANCLA_ORDEN_DIA.finditer(text))
        anchors = [
            m for m in todas
            if self._RE_QUE_CELEBRA.search(
                text[m.start():m.start() + self._VENTANA_VALIDACION_ANCLA]
            )
        ]
        sessions = []

        for i, m in enumerate(anchors):
            start = m.start()
            end = anchors[i + 1].start() if i + 1 < len(anchors) else len(text)
            block = text[start:end]

            # Solo se necesita "asomarse" a la portada (primeras ~500
            # caracteres) para tipo de sesión y fecha; el resto del bloque
            # se usa después para horas / conteo de asuntos.
            portada = block[:600]

            tipo_match = self._RE_TIPO_SESION.search(portada)
            tipo_raw = tipo_match.group(1).strip() if tipo_match else None
            tipo_sesion = self._normalizar_tipo_sesion(tipo_raw)

            fecha_match = self._RE_FECHA_PORTADA.search(portada)
            fecha_obj = None
            if fecha_match:
                dia = int(fecha_match.group(1))
                mes_txt = self._strip_accents(fecha_match.group(2).lower())
                mes = self.MESES.get(mes_txt)
                anio = int(fecha_match.group(3))
                if mes:
                    fecha_obj = date(anio, mes, dia)

            sessions.append({
                "tipo_sesion": tipo_sesion,
                "fecha": fecha_obj,
                "texto": block,
            })

        return sessions

    @staticmethod
    def _normalizar_tipo_sesion(tipo_raw: str):
        """
        'de la Comisión Permanente' -> 'Comisión Permanente'
        'Solemne' -> 'Solemne'
        'Pública Ordinaria' -> 'Ordinaria'
        'Extraordinaria' -> 'Extraordinaria'
        """
        if not tipo_raw:
            return None
        t = GMProcessor._strip_accents(tipo_raw.lower())
        if "comision permanente" in t:
            return "Comisión Permanente"
        if "solemne" in t:
            return "Solemne"
        if "extraordinaria" in t:
            return "Extraordinaria"
        if "previa" in t:
            return "Previa"
        if "ordinaria" in t:
            return "Ordinaria"
        return tipo_raw.strip()

    # ------------------------------------------------------------------
    # Diccionario {fecha : hora_citada} a partir de todos los cierres de
    # acta del mes (una sesión puede citar 0, 1 o 2 sesiones futuras)
    # ------------------------------------------------------------------

    # El verbo de convocatoria varía entre sesiones: "CITANDO A ... PARA
    # EL <fecha>", "CONVOCÓ A ... PARA LA SESIÓN ... A CELEBRARSE EL
    # <fecha>", y en la Sesión Extraordinaria de septiembre 2025:
    # "RECORDÓ ... DE LA CONVOCATORIA PARA LA CELEBRACIÓN ... EL <fecha>".
    # El día dentro de la fecha citada puede venir en dígito ("9") o en
    # palabra ("ONCE") -- se comprobó ambos casos en el mismo documento.
    _RE_CITA = re.compile(
        r'(?:CITANDO|CONVOC[OÓ]|CONVOCATORIA)[^.]{0,250}?'
        r'(?:EL|PARA\s+EL|A\s+CELEBRARSE\s+EL)\s+'
        r'(?:PR[OÓ]XIMO\s+)?'
        r'[A-ZÁÉÍÓÚÑ]+\s+'                       # día de la semana
        r'([A-ZÁÉÍÓÚÑ0-9]+)\s+DE\s+'              # día (dígito o palabra)
        r'([A-ZÁÉÍÓÚÑ]+)'                         # mes
        r'(?:\s+DEL?\s+(?:A[NÑ]O\s+EN\s+CURSO|(\d{4})))?'
        r'[^.]{0,120}?'
        r'A\s+LAS\s+([A-ZÁÉÍÓÚÑ]+)\s+HORAS'       # hora citada
        r'(?:\s+CON\s+([A-ZÁÉÍÓÚÑ ]+?)\s+MINUTOS)?',
        re.IGNORECASE | re.DOTALL
    )

    def _build_citas_dict(self, full_text_prose: str, anio_gaceta: int) -> dict:
        """
        full_text_prose debe venir con saltos de línea colapsados a
        espacios (ver _colapsar_espacios) para que el regex de cita, que
        puede cruzar varias líneas de layout, funcione de forma confiable.
        """
        citas = {}
        for m in self._RE_CITA.finditer(full_text_prose):
            dia_tok, mes_txt, anio_txt, hora_txt, min_txt = m.groups()

            dia = self._parse_dia_token(dia_tok)
            mes = self.MESES.get(self._strip_accents(mes_txt.lower()))
            anio = int(anio_txt) if anio_txt else anio_gaceta
            if dia is None or mes is None:
                continue

            try:
                fecha_citada = date(anio, mes, dia)
            except ValueError:
                continue

            hora_citada = self._parse_hora_texto(hora_txt, min_txt)
            if hora_citada is not None:
                citas[fecha_citada] = hora_citada

        return citas

    @staticmethod
    def _colapsar_espacios(text: str) -> str:
        """Colapsa saltos de línea/espacios múltiples a un solo espacio.
        Necesario para que los regex de "prosa" (apertura, cierre, citas)
        no se corten por el wrap de línea de pdftotext -layout."""
        return re.sub(r'\s+', ' ', text)

    # ------------------------------------------------------------------
    # Extracción de hora de inicio / fin de UNA sesión
    # ------------------------------------------------------------------

    # Validado en las 6 sesiones de septiembre 2025 que sí tienen Acta
    # narrativa (la 7ma, Comisión Permanente del 10/09, es un caso de
    # "acta ausente": solo portada, sin este patrón en absoluto).
    #
    # El verbo de apertura también varía: la Sesión Previa (instalación de
    # legislatura) de septiembre 2024 usa "SE INICIÓ LA SESIÓN PREVIA,
    # SIENDO LAS..." en vez de "SE ABRIÓ LA SESIÓN...SIENDO LAS...". El
    # tramo entre "SESIÓN" y "SIENDO LAS" se dejó como "cualquier cosa
    # menos un punto" ([^.]{0,60}?) en vez de exigir solo palabras en
    # mayúsculas, porque ahí puede haber una coma pegada al tipo de sesión
    # ("SESIÓN PREVIA, SIENDO...") que rompía el patrón anterior.
    _RE_INICIO = re.compile(
        r'(?:HUBO\s+QU[OÓ]RUM\s+Y\s+SE|SE)\s+(?:ABRI[OÓ]|INICI[OÓ])\s+LA\s+SESI[OÓ]N'
        r'[^.]{0,150}?'
        r'SIENDO\s+LAS\s+([A-ZÁÉÍÓÚÑ]+)\s+HORAS?'
        r'(?:\s+CON\s+([A-ZÁÉÍÓÚÑ ]+?)\s+MINUTOS?)?\.',
        re.IGNORECASE
    )

    _RE_FIN = re.compile(
        r'(?:LEVANT[OÓ]|CLAUSUR[OÓ])\s+LA\s+SESI[OÓ]N'
        r'[^.]{0,150}?'
        r'SIENDO\s+LAS\s+([A-ZÁÉÍÓÚÑ]+)\s+HORAS?'
        r'(?:\s+CON\s+([A-ZÁÉÍÓÚÑ ]+?)\s+MINUTOS?)?',
        re.IGNORECASE
    )

    def _extract_horas_sesion(self, block_prose: str):
        """Devuelve (hora_inicio, hora_fin) como datetime.time, o (None, None)
        si el bloque no trae Acta narrativa (caso "acta ausente")."""
        inicio_match = self._RE_INICIO.search(block_prose)
        fin_match = self._RE_FIN.search(block_prose)

        hora_inicio = None
        if inicio_match:
            hora_inicio = self._parse_hora_texto(*inicio_match.groups())

        hora_fin = None
        if fin_match:
            hora_fin = self._parse_hora_texto(*fin_match.groups())

        return hora_inicio, hora_fin

    # ------------------------------------------------------------------
    # Conteo de asuntos programados / no abordados (Orden del día)
    # ------------------------------------------------------------------

    # Se toma el máximo número de ítem del Orden del día, NO se busca
    # literalmente "Asuntos Generales": se comprobó que las sesiones
    # Solemne y Extraordinaria de septiembre 2025 terminan su Orden del
    # día en "Clausura de la Sesión ..." en vez de "Asuntos Generales",
    # así que ese texto no es un ancla confiable de cierre de la lista.
    _RE_ITEM_ORDEN_DIA = re.compile(r'^\s{0,10}(\d{1,3})\.\s+\S', re.MULTILINE)

    def _extract_asuntos_programados(self, block_original: str):
        """
        block_original: el bloque de la sesión CON saltos de línea reales
        (no colapsado), porque este regex depende de que cada ítem
        empiece al inicio de una línea (formato observado en
        pdftotext -layout). Si PDFProcessor usa un extractor distinto y
        no preserva ese salto de línea al inicio de cada ítem, este
        conteo puede fallar -- ver aviso en el mensaje de chat.
        """
        # Acotar al tramo entre "ORDEN DEL DÍA" y "LISTA DE ASISTENCIA"
        # para no contar numeraciones de otras secciones (Iniciativas,
        # Puntos de Acuerdo, etc. también usan "1.", "2." ...).
        fin_match = re.search(r'LISTA\s+DE\s+ASISTENCIA', block_original, re.IGNORECASE)
        tramo = block_original[:fin_match.start()] if fin_match else block_original[:4000]

        numeros = [int(n) for n in self._RE_ITEM_ORDEN_DIA.findall(tramo)]
        return max(numeros) if numeros else None

    # Marcadores que delimitan el final del tramo "Extractos + Acta de la
    # Sesión" (donde tiene sentido buscar frases de "no abordado"). Sin
    # este límite, la búsqueda se metía en el texto completo de
    # Iniciativas/Puntos de Acuerdo (decenas de páginas de argumentación
    # legal) y producía falsos positivos: se comprobó que la frase "no fue
    # posible" aparece ahí en una iniciativa sobre licencia de maternidad,
    # sin ninguna relación con el orden del día.
    _RE_FIN_TRAMO_ACTA = re.compile(
        r'INICIATIVAS|PUNTOS?\s+DE\s+ACUERDO|ACUERDOS\s+APROBADOS|DICT[AÁ]MENES',
        re.IGNORECASE
    )

    def _extract_acta_tramo(self, block_original: str) -> str:
        """Recorta el bloque de la sesión al tramo Lista de Asistencia ->
        Extractos -> Acta de la Sesión, excluyendo Iniciativas/Puntos de
        Acuerdo/Acuerdos Aprobados/Dictámenes (su propia prosa legal, no
        la narrativa de la sesión)."""
        inicio_match = re.search(r'LISTA\s+DE\s+ASISTENCIA', block_original, re.IGNORECASE)
        inicio = inicio_match.start() if inicio_match else 0
        fin_match = self._RE_FIN_TRAMO_ACTA.search(block_original, pos=inicio + 1)
        fin = fin_match.start() if fin_match else len(block_original)
        return block_original[inicio:fin]

    def _extract_asuntos_no_abordados(self, acta_tramo_prose: str) -> int:
        """
        Primera estimación (ver documento de contexto): si ninguna frase
        de _FRASES_NO_ABORDADO aparece, se asume 0. No se encontró ningún
        caso real en septiembre 2025 para calibrar el conteo cuando SÍ
        aparecen, así que de momento solo cuenta apariciones de las
        frases-ancla (aproximación a afinar con un caso real), buscando
        SOLO dentro del tramo Extractos/Acta (ver _extract_acta_tramo).
        """
        texto_norm = self._strip_accents(acta_tramo_prose.lower())
        count = 0
        for frase in self._FRASES_NO_ABORDADO:
            count += texto_norm.count(frase)
        return count

    # ------------------------------------------------------------------
    # Orquestación por archivo
    # ------------------------------------------------------------------

    def process_file(self, file_content: str, source_file: str = None) -> list:
        if source_file:
            print(f"Procesando (regex, sin IA): {source_file}")

        texto = self._clean_footer_noise(file_content)
        texto_prosa = self._colapsar_espacios(texto)

        sesiones = self._segment_sessions(texto)
        if not sesiones:
            print("  Aviso: no se encontraron anclas 'ORDEN DEL DÍA'; "
                  "no se generaron registros para este archivo.")
            return []

        anio_gaceta = next((s["fecha"].year for s in sesiones if s["fecha"]), None)
        citas_dict = self._build_citas_dict(texto_prosa, anio_gaceta) if anio_gaceta else {}

        registros = []
        for s in sesiones:
            registros.append(self._build_record(s, citas_dict))

        return registros

    def _build_record(self, sesion: dict, citas_dict: dict) -> dict:
        observaciones = []

        tipo_sesion = sesion["tipo_sesion"]
        fecha_obj = sesion["fecha"]
        block_original = sesion["texto"]
        block_prose = self._colapsar_espacios(block_original)

        # Sin fecha no se puede calcular nada que dependa de ella (duración,
        # retraso, cita, legislatura/periodo/folios): se deja constancia en
        # vez de fallar silenciosamente o tronar más abajo.
        if fecha_obj is None:
            observaciones.append(
                "No se pudo extraer la fecha de la portada de esta sesión "
                "(formato de portada distinto al validado); revisar "
                "manualmente esta gaceta. Año/Periodo legislatura, folios y "
                "Fecha quedan en None."
            )

        # --- Horas de inicio / fin (None si el Acta no tiene narrativa) ---
        hora_inicio, hora_fin = self._extract_horas_sesion(block_prose)
        if fecha_obj and hora_inicio is None and hora_fin is None:
            observaciones.append(
                "El Acta de esta sesión no contiene texto narrativo en esta "
                "gaceta (solo portada); no fue posible extraer horas de "
                "inicio/fin ni calcular duración/retraso."
            )

        # --- Hora de cita (para inicio de sesión) ---
        hora_cita = citas_dict.get(fecha_obj) if fecha_obj else None
        if fecha_obj and hora_cita is None:
            observaciones.append(
                "Hora de cita no disponible: no se encontró convocatoria "
                "previa para esta fecha en esta gaceta."
            )

        # --- Asuntos programados / abordados / no abordados ---
        programados = self._extract_asuntos_programados(block_original)
        acta_tramo = self._extract_acta_tramo(block_original)
        no_abordados = self._extract_asuntos_no_abordados(acta_tramo)
        abordados = None
        porcentaje = None
        coeficiente = None
        if programados is not None:
            no_abordados = min(no_abordados, programados)
            abordados = programados - no_abordados
            porcentaje = round((abordados / programados) * 100, 2) if programados else None
            coeficiente = round(abordados / programados, 4) if programados else None
            if hora_inicio is None and hora_fin is None:
                observaciones.append(
                    "Asuntos abordados asumidos igual a programados por "
                    "regla por defecto (sin Acta narrativa que lo confirme "
                    "para esta sesión en particular)."
                )

        # --- Duración / retraso (derivados en código) ---
        # Se exige fecha_obj además de hora_inicio/hora_fin: sin fecha,
        # datetime.combine() no se puede evaluar (antes tronaba aquí
        # cuando la portada no traía fecha reconocible).
        duracion_str = None
        if fecha_obj and hora_inicio and hora_fin:
            dt_inicio = datetime.combine(fecha_obj, hora_inicio)
            dt_fin = datetime.combine(fecha_obj, hora_fin)
            duracion_str = self._fmt_duracion(dt_fin - dt_inicio)

        retraso_str = None
        retraso_min = None
        hora_cita_str = None
        if hora_cita:
            hora_cita_str = self._fmt_hora_ampm(hora_cita)
            if hora_inicio:
                dt_cita = datetime.combine(fecha_obj, hora_cita)
                dt_inicio = datetime.combine(fecha_obj, hora_inicio)
                delta = dt_inicio - dt_cita
                retraso_str = self._fmt_intervalo_hhmmss(delta)
                retraso_min = int(delta.total_seconds() // 60)

        # --- Legislatura / periodo / folios (EventClassifier + FolioManager) ---
        anio_leg = periodo_num = folio_legislatura = None
        anio_leg_txt = periodo_txt = None
        fecha_iso = None
        if fecha_obj:
            fecha_iso = fecha_obj.strftime("%Y-%m-%d")
            anio_leg = self.event_classifier.classify_per_year(fecha_iso)
            periodo_num = self.event_classifier.classify_per_period(fecha_iso)
            folio_legislatura, _folio_periodo = self.folio_manager.generate_folios(
                fecha_iso, anio_leg, periodo_num
            )
            anio_leg_txt = {1: "Primer año", 2: "Segundo año", 3: "Tercer año"}.get(anio_leg)
            periodo_txt = {1: "Primer periodo", 2: "Segundo periodo", 3: "Tercer periodo"}.get(periodo_num)

        record = {
            # --- INFERENCIA A CONFIRMAR (ver mensaje de chat) ---
            "Número de sesión": folio_legislatura,
            "Sesión letra": f"Sesión {folio_legislatura}" if folio_legislatura else None,
            "Año legislatura": anio_leg_txt,
            "Periodo legislatura": periodo_txt,
            "Número del periodo": periodo_num,
            "Tipo de sesión": tipo_sesion,
            "Observaciones": "; ".join(observaciones) if observaciones else None,
            "Fecha": fecha_obj.strftime("%d/%m/%Y") if fecha_obj else None,
            # TODO: confirmar regla real de este campo (ver mensaje de chat)
            "Fecha Gaceta Parlamentaria": None,
            "Hora de cita para inicio de sesión": hora_cita_str,
            "Hora retraso de inicio de sesión": retraso_str,
            "Retraso de inicio de sesión (minutos)": retraso_min,
            "Retraso de inicio de sesión": retraso_str,
            "Inicio de sesión": self._fmt_hora_24(hora_inicio) if hora_inicio else None,
            "Fin de sesión": self._fmt_hora_24(hora_fin) if hora_fin else None,
            "Duración": duracion_str,
            "Total de asuntos programados por sesión": programados,
            "Total de asuntos abordados por sesión": abordados,
            "Total de asuntos no abordados por sesión": no_abordados if programados is not None else None,
            "Porcentaje de asuntos abordados por sesión": porcentaje,
            "Coeficiente de asuntos abordados por sesión": coeficiente,
        }
        return record


if __name__ == '__main__':
    from lib.PDFProcessor import PDFProcessor

    pdf_processor = PDFProcessor()
    processor = GMProcessor()

    file_path = "C:\\Users\\WARNE\\OneDrive\\Escritorio\\Projects\\Python\\DataExtractionGP\\data\\pdfs\\gms\\Diciembre-2024pdf.pdf"
    print(f"Procesando archivo: {file_path}")

    text = pdf_processor.extract_text(file_path)
    # clean_text_preserve_lines(), NO clean_text(): GMProcessor segmenta por
    # anclas de línea aislada y clean_text() colapsa todos los saltos de
    # línea a espacios (ver nota en gacetas_mensuales_scraper.py).
    cleaned_text = pdf_processor.clean_text_preserve_lines(text)

    data = processor.process_file(cleaned_text, source_file=file_path)

    output_filename = "temp_output_gm.json"
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    print(f"¡Listo! Se generaron {len(data)} registro(s).")
    print(f"Los datos se han guardado en: {output_filename}")