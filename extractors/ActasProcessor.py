# extractors/ActasProcessor.py
#
# Procesador de actas para la base "Iniciativas y Puntos de Acuerdo".
# NO usa IA: extrae con reglas el evento de PRESENTACIÓN (quién, de qué partido,
# qué asunto, a qué comisión se turnó, en qué punto del orden del día).
# Las votaciones ya vienen de unidad_participacion.json (extracción previa con IA).

import re
import unicodedata
from datetime import datetime

# ----------------------------------------------------------------------------
# Utilidades de texto
# ----------------------------------------------------------------------------

def strip_acc(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', str(s)) if unicodedata.category(c) != 'Mn')


def tokens(s: str) -> list:
    return re.sub(r'[^a-z0-9 ]+', ' ', strip_acc(s or '').lower()).split()


def norm_key(s: str) -> str:
    return ' '.join(tokens(s))


_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50,
}


def spanish_to_int(text: str) -> int:
    t = strip_acc(text).lower().strip()
    if t in _UNITS:
        return _UNITS[t]
    if " y " in t:
        a, b = t.split(" y ", 1)
        return _UNITS.get(a, 0) + _UNITS.get(b, 0)
    return 0


_PARTICLES = {"de", "del", "la", "las", "los", "el", "y", "e", "en", "para", "a", "con", "al"}


def title_case(s: str) -> str:
    out = []
    for i, w in enumerate(s.lower().split()):
        out.append(w if (w in _PARTICLES and i > 0) else w[:1].upper() + w[1:])
    return ' '.join(out)


_LAW_SPAN = re.compile(
    r'\b(?:LEY|C[ÓO]DIGO|CONSTITUCI[ÓO]N|REGLAMENTO)\b[A-ZÁÉÍÓÚÑ ]+?(?=[,;.“”"]| Y (?:EL|LA|LOS|LAS|SE)\b|$)')


def smart_case(s: str) -> str:
    """Pasa MAYÚSCULAS a minúsculas tipo oración, conservando nombres de leyes en título."""
    s = s.strip()
    spans = [(m.start(), m.end(), title_case(m.group())) for m in _LAW_SPAN.finditer(s)]
    low = s.lower()
    for a, b, rep in reversed(spans):
        low = low[:a] + rep + low[b:]
    low = re.sub(r'\bpuebla\b', 'Puebla', low)
    return low[:1].upper() + low[1:]


# ----------------------------------------------------------------------------
# Catálogos
# ----------------------------------------------------------------------------
TEMAS = {
    1: "Economía, Comercio y Competitividad", 2: "Pobreza y Política Social",
    3: "Seguridad Pública, Protección Civil y Narcotráfico", 4: "Partidos Políticos y Elecciones",
    5: "Medio Ambiente", 6: "Salud", 7: "Impuestos, Finanzas Públicas, Patrimonio estatal y municipal",
    8: "Educación y Cultura", 9: "Migración y fronteras", 10: "Transparencia y Rendición de Cuentas",
    11: "Infraestructura, Movilidad y Transporte", 12: "Agricultura, Ganadería y Pesca",
    13: "Medios de Comunicación", 14: "Justicia y Estado de Derecho", 15: "Telecomunicaciones",
    16: "Trabajo y Previsión Social", 17: "Política", 18: "Administración Pública",
    19: "Internos del Congreso", 20: "Justicia y Estado de Derecho en temas de Mujeres",
}

# Tema por comisión/comité al que se turna (cnorm: sin "De la" inicial ni acentos)
COMISION_TEMA = {
    "Asuntos Metropolitanos": 18, "Asuntos Municipales": 18,
    "Atención a Personas con Discapacidad": 2, "Atención a Personas en Situación de Vulnerabilidad": 2,
    "Bienestar": 2, "Ciencia, Humanidades, Tecnología e Innovación": 8,
    "Comunicaciones e Infraestructura": 11,
    "Control, Vigilancia y Evaluación de la Auditoría Superior del Estado": 10, "Cultura": 8,
    "Agenda 2030 y convenios o acuerdos que se determinen en el ámbito internacional para mejorar las necesidades de toda la población": 17,
    "Familia y los Derechos de la Niñez": 2, "Derechos Humanos": 14, "Desarrollo Económico": 1,
    "Desarrollo Rural": 12, "Desarrollo Urbano": 11, "Educación": 8,
    "Gobernación y Puntos Constitucionales": 17, "Hacienda y Patrimonio Municipal": 7,
    "Igualdad de Género": 20, "Instructora": 14, "Juventud y Deporte": 8,
    "Medio Ambiente, Recursos Naturales y Cambio Climático": 5, "Migración y Asuntos Internacionales": 9,
    "Organizaciones No Gubernamentales": 17, "Participación Ciudadana y Combate a la Corrupción": 10,
    "Presupuesto y Crédito Público": 7, "Procuración y Administración de Justicia": 14,
    "Protección Civil": 3, "Pueblos, Comunidades Indígenas y Afromexicanas": 2, "Salud": 6,
    "Seguridad Pública": 3, "Trabajo, Competitividad y Previsión Social": 16,
    "Transparencia y Acceso a la Información": 10, "Transportes y Movilidad": 11, "Turismo": 1,
    "Vivienda": 11, "Adquisiciones, Arrendamientos y Servicios": 18, "Atención Ciudadana": 17,
    "Comunicación Social": 13, "Diario de Debates, Crónica Legislativa y Asuntos Editoriales": 19,
    "Tecnología, Innovación y Sostenibilidad": 18, "Junta de Gobierno y Coordinación Política": 19,
}

PARTY_ALIAS = {"FXMP": "FXM", "PNA": "Nueva Alianza", "PMC": "MC"}   # perfiles.json -> códigos de la base

# ----------------------------------------------------------------------------
# Partidos
# ----------------------------------------------------------------------------

PARTY_PATTERNS = [
    (re.compile(r'\bMORENA\b'), 'MORENA'),
    (re.compile(r'PARTIDO DEL TRABAJO'), 'PT'),
    (re.compile(r'VERDE ECOLOGISTA'), 'PVEM'),
    (re.compile(r'ACCI[ÓO]N NACIONAL'), 'PAN'),
    (re.compile(r'REVOLUCIONARIO INSTITUCIONAL'), 'PRI'),
    (re.compile(r'MOVIMIENTO CIUDADANO'), 'MC'),
    (re.compile(r'NUEVA ALIANZA'), 'Nueva Alianza'),
    (re.compile(r'FUERZA POR M[ÉE]XICO'), 'FXM'),
]

# Descriptor de pertenencia: ", INTEGRANTE(S) [Y COORDINADOR(A)] DEL GRUPO LEGISLATIVO DE(L) [PARTIDO] X"
_DESCRIPTOR = re.compile(
    r',?\s*(?:DE LA\s+)?(?:INTEGRANTES?(?: Y COORDINADOR(?:A)?)?|COORDINADOR(?:A)?|REPRESENTACI[ÓO]N LEGISLATIVA)'
    r'(?:\s+DEL GRUPO LEGISLATIVO|\s+LEGISLATIVA)?\s*(?:DE LOS?|DEL|DE)?\s*'
    r'(?:PARTIDO\s+)?(?:DEL\s+)?(?:MORENA|TRABAJO|VERDE ECOLOGISTA DE M[ÉE]XICO|ACCI[ÓO]N NACIONAL|'
    r'REVOLUCIONARIO INSTITUCIONAL|MOVIMIENTO CIUDADANO|NUEVA ALIANZA|FUERZA POR M[ÉE]XICO)'
    r'(?:,?\s*RESPECTIVAMENTE)?')

_TRAILER = re.compile(r',?\s*(?:DE LA SEXAG[ÉE]SIMA SEGUNDA LEGISLATURA.*|DEL HONORABLE CONGRESO.*)$')


def parse_presenters(pres: str):
    """Devuelve (nombres, partidos_en_orden_de_aparición, tipo_presentador)."""
    p = pres.strip()
    pl = strip_acc(p).upper()
    # Presentadores institucionales
    if re.match(r'^(?:LA |EL )?COMISI[ÓO]N', p):
        nombre = re.sub(r'^(?:LA|EL)\s+', '', re.split(r'\s+DE LA SEXAG|,', p)[0])
        return [title_case(nombre)], [], 'comision'
    if re.match(r'^(?:LA |EL )?JUNTA DE GOBIERNO', p):
        return ['Junta de Gobierno y Coordinación Política'], [], 'jucopo'

    parties = []
    for m in _DESCRIPTOR.finditer(p):
        for rx, code in PARTY_PATTERNS:
            if rx.search(m.group()):
                parties.append(code)
                break
    base = _DESCRIPTOR.sub('', p)
    base = _TRAILER.sub('', base)
    base = re.sub(r'\b(?:LAS|LOS|LA|EL)\s+DIPUTAD[OA]S?\b', ' ', base)
    base = re.sub(r'\bDIPUTAD[OA]S?\b', ' ', base)
    parts = re.split(r',|\s+Y\s+(?=[A-ZÁÉÍÓÚÑ]{3,}\s+[A-ZÁÉÍÓÚÑ]{3,})', base)
    names = [title_case(re.sub(r'\s+', ' ', x).strip()) for x in parts if len(x.strip().split()) >= 2]
    return names, parties, 'diputados'


def assign_party(names, parties):
    if not parties:
        return None
    uniq = list(dict.fromkeys(parties))
    if len(names) <= 1:
        return uniq[0]
    return 'Conjunto: ' + _join(uniq)


def _join(items):
    items = list(items)
    if len(items) <= 1:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' y ' + items[-1]


# ----------------------------------------------------------------------------
# Procesador
# ----------------------------------------------------------------------------

_KINDS = (r'(?:INICIATIVA DE DECRETO|INICIATIVA DE LEY|INICIATIVA|PUNTOS? DE ACUERDO|'
          r'DICTAMEN CON MINUTA DE DECRETO|DICTAMEN|ACUERDO|OFICIO)')

_TITLE_END = (r'(?=; CON FUNDAMENTO|[;,] TURN[ÁA]NDOSE|; EN USO|; Y EN VIRTUD|, TERMINADA SU INTERVENCI|'
              r'[;,] CONCLUIDA|\. (?:EN EL|EL|CONTINUANDO|ACTO|ENSEGUIDA|ASIMISMO|FINALMENTE)\b|$)')

ITEM_RE = re.compile(
    rf'(?P<kind>{_KINDS}) QUE PRESENTAN? (?P<pres>.+?),? POR EL (?:QUE|CUAL) (?P<title>.+?){_TITLE_END}', re.S)

AG_RE = re.compile(
    r'EN USO DE LA PALABRA (?:LA DIPUTADA |EL DIPUTADO )?(?P<pres>[A-ZÁÉÍÓÚÑ ]+?), '
    r'(?:PRESENT[ÓO] EL|DIO CUENTA DEL) (?P<kind>PUNTO DE ACUERDO|INICIATIVA DE DECRETO) '
    rf'POR EL (?:QUE|CUAL) (?P<title>.+?){_TITLE_END}', re.S)

GROUP_RE = re.compile(
    r'PUNTOS DE ACUERDO PRESENTADOS POR (?P<pres>.+?), (?:EM|EN) LOS T[ÉE]RMINOS SIGUIENTES: '
    r'(?P<body>.+?)(?=\. NO HABIENDO|$)', re.S)

TURNO_RE = re.compile(
    r'(?:TURN[ÁA]NDOSE|TURNO(?: DE LA INICIATIVA DE DECRETO| DEL PUNTO DE ACUERDO| DE LA INICIATIVA| DEL ACUERDO)?) '
    r'A (?:LA |LAS |LOS )?(?P<c>(?:COMISI[ÓO]N(?:ES)?|COMIT[ÉE]S?|JUNTA DE GOBIERNO)[^;]*?)'
    r'(?:,? PARA SU (?:ESTUDIO|RESOLUCI[ÓO]N)|[;.]|$)', re.S)

PROPONE_RE = re.compile(
    r'SE PROCEDI[ÓO] A (?P<title>LA ELECCI[ÓO]N DE .+?); ACTO SEGUIDO, EN USO DE LA PALABRA '
    r'(?:LA |EL )?DIPUTAD[AO] (?:ELECT[AO] )?(?P<pres>[A-ZÁÉÍÓÚÑ ]+?), PROPUSO', re.S)

PUNTO_RE = re.compile(r'PUNTO ([A-ZÁÉÍÓÚ]+(?: Y [A-ZÁÉÍÓÚ]+)?) DEL ORDEN DEL D[ÍI]A')
ULTIMO_RE = re.compile(r'[ÚU]LTIMO PUNTO DEL ORDEN DEL D[ÍI]A,? (?:CORRESPONDIENTE A )?ASUNTOS GENERALES')

_FOOTERS = [
    re.compile(r'Av\. 32 Oriente No\. 202.*?www\.congresopuebla\.gob\.mx', re.S),
    re.compile(r'Av\. 5 Poniente #128.*?www\.congresopuebla\.gob\.mx', re.S),
]


class ActasProcessor:
    """Extrae presentaciones (iniciativas / puntos de acuerdo) de un acta ya convertida a texto."""

    def __init__(self, commission_names=None):
        # Nombres canónicos de comisiones (p. ej. de comisiones_contenido.json)
        self.commissions = sorted(set(commission_names or []), key=len, reverse=True)
        self._comm_norm = [(norm_key(c), c) for c in self.commissions]

    # ---- cabecera -----------------------------------------------------------
    def get_date(self, text: str):
        head = text[:1500]
        pat = (r"\bEL\s+(?:(?:LUNES|MARTES|MI[ÉE]RCOLES|JUEVES|VIERNES|S[ÁA]BADO|DOMINGO)\s+)?"
               r"([A-ZÁÉÍÓÚÑ]+(?:\s+Y\s+[A-ZÁÉÍÓÚÑ]+)?)\s+DE\s+([A-ZÁÉÍÓÚÑ]+)\s+DE\s+"
               r"(DOS MIL\s+[A-ZÁÉÍÓÚÑ]+(?:\s+Y\s+[A-ZÁÉÍÓÚÑ]+)?)")
        m = re.search(pat, head, re.I)
        if not m:
            return None
        months = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
                  "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
        day = spanish_to_int(m.group(1))
        month = months.get(strip_acc(m.group(2)).lower())
        rest = strip_acc(m.group(3)).lower().replace("dos mil", "").strip()
        year = 2000 + (spanish_to_int(rest) if rest else 0)
        if not (day and month and year > 2000):
            return None
        return datetime(year, month, day).strftime("%Y-%m-%d")

    def get_session_type(self, text: str) -> str:
        head = text[:1200].upper()
        for key, label in (("COMISIÓN PERMANENTE", "Comisión Permanente"), ("PREVIA", "Previa"),
                           ("SOLEMNE", "Solemne"), ("EXTRAORDINARIA", "Extraordinaria"),
                           ("ORDINARIA", "Ordinaria")):
            if key in head:
                return label
        return "Desconocida"

    # ---- helpers ------------------------------------------------------------
    @staticmethod
    def _clean(text: str) -> str:
        for rx in _FOOTERS:
            text = rx.sub(' ', text)
        return re.sub(r'\s+', ' ', text).strip()

    def _canon_commission(self, raw: str):
        """Devuelve la lista de comisiones canónicas mencionadas en el texto del turno."""
        rawn = norm_key(raw)
        found, used = [], []
        for cn, c in self._comm_norm:
            m = re.search(rf'\b{re.escape(cn)}\b', rawn)
            if m and not any(a < m.end() and m.start() < b for a, b in used):
                used.append((m.start(), m.end()))
                found.append((m.start(), c))
        if found:
            return [c for _, c in sorted(found)]
        if 'junta de gobierno' in rawn:
            return ['Junta de Gobierno y Coordinación Política']
        name = re.sub(r'^(?:COMISI[ÓO]N(?:ES)?(?: UNIDAS)? (?:DE LA |DE LOS |DE LAS |DE )?)', '', raw.strip(), flags=re.I)
        return [title_case(name)] if name else []

    def _turno_en(self, window: str):
        m = TURNO_RE.search(window)
        return self._canon_commission(m.group('c')) if m else []

    @staticmethod
    def _norma_articulos(title: str):
        norma = None
        m = re.search(r'\b(?:LEY|C[ÓO]DIGO|CONSTITUCI[ÓO]N|REGLAMENTO)\b[A-ZÁÉÍÓÚÑ ]+?'
                      r'(?=[,;.]| Y (?:EL|LA|LOS|LAS|SE)\b|$)', title)
        if m:
            norma = title_case(m.group())
        arts = []
        for a in re.finditer(r'ART[ÍI]CULOS? ((?:\d+(?: (?:BIS|TER|QUATER))?(?: FRACCI[ÓO]N [IVXLC]+)?(?:, | Y )?)+)', title):
            arts.append(a.group(1).strip(' ,').lower())
        return norma, '; '.join(arts) if arts else None

    @staticmethod
    def _lower_first(s: str) -> str:
        return s[:1].lower() + s[1:]

    @staticmethod
    def _kind_to_tipo(kind: str):
        k = kind.upper()
        if k.startswith('INICIATIVA') or k.startswith('DICTAMEN'):
            return 'Iniciativa', ('Iniciativa de decreto' if 'DECRETO' in k or k == 'INICIATIVA' else 'Iniciativa de ley')
        return 'Punto de Acuerdo', 'Punto de acuerdo'

    def _build(self, kind, pres, title, turno, numero, orden, base):
        tipo, label = self._kind_to_tipo(kind)
        names, parties, ptype = parse_presenters(pres)
        norma, arts = self._norma_articulos(title)
        presentador = _join(names) if names else smart_case(pres)
        rol = 'turnada' if turno else 'directa'
        return {
            **base,
            "Rol": rol,
            "Número": numero,
            "Orden": orden,
            "Tipo": tipo,
            "Descripción": f"{label} por el que {self._lower_first(smart_case(title))}".rstrip('.'),
            "Presentador": presentador,
            "Presentadores": names,
            "Tipo presentador": ptype,
            "Partido": assign_party(names, parties) if ptype == 'diputados' else 'N/A',
            "Turno": turno,
            "Norma": norma,
            "Artículos": arts,
        }

    # ---- API pública ----------------------------------------------------------
    def process_file(self, file_content: str, acta_id=None) -> list:
        text = self._clean(file_content)
        fecha = self.get_date(text)
        if not fecha:
            print(f"[ActasProcessor] acta {acta_id}: no se pudo extraer la fecha")
            return []
        base = {"Acta_id": acta_id, "Fecha": fecha, "Tipo de sesión": self.get_session_type(text)}

        ag_pos = ULTIMO_RE.search(text)
        ag_start = ag_pos.start() if ag_pos else None
        puntos = [(m.start(), spanish_to_int(m.group(1))) for m in PUNTO_RE.finditer(text)]

        def numero_en(pos):
            if ag_start is not None and pos > ag_start:
                return 'AG'
            prev = [n for p, n in puntos if p < pos]
            return prev[-1] if prev else None

        hits = []  # (start, end, tipo_match, match)
        for m in ITEM_RE.finditer(text):
            hits.append((m.start(), m.end(), 'item', m))
        for m in AG_RE.finditer(text):
            if not any(a <= m.start() < b for a, b, _, _ in hits):
                hits.append((m.start(), m.end(), 'ag', m))
        for m in GROUP_RE.finditer(text):
            hits.append((m.start(), m.end(), 'group', m))
        for m in PROPONE_RE.finditer(text):
            hits.append((m.start(), m.end(), 'propone', m))
        hits.sort(key=lambda h: h[0])

        records, orden = [], 0
        for i, (s, e, t, m) in enumerate(hits):
            nxt = hits[i + 1][0] if i + 1 < len(hits) else len(text)
            if t == 'propone':
                orden += 1
                records.append(self._build('PUNTO DE ACUERDO', m.group('pres'), m.group('title'),
                                           [], numero_en(s), orden, base))
            elif t in ('item', 'ag'):
                window = text[e:nxt]
                turno = self._turno_en(window)
                orden += 1
                records.append(self._build(m.group('kind'), m.group('pres'), m.group('title'),
                                           turno, numero_en(s), orden, base))
            else:  # varios puntos de acuerdo de una misma persona en un solo párrafo
                body = m.group('body')
                chunks = re.split(r'(?:\.\s+|;\s+Y\s+)(?=(?:POR EL QUE|EL QUE|Y EL QUE) )', body)
                for ch in chunks:
                    mm = re.match(r'(?:Y )?(?:POR )?EL QUE (?P<title>.+?),? (?:A LA|A LAS) (?P<c>COMISI.+?)(?:, PARA SU.*|$)', ch.strip(), re.S)
                    if not mm:
                        continue
                    orden += 1
                    records.append(self._build('PUNTO DE ACUERDO', m.group('pres'), mm.group('title'),
                                               self._canon_commission(mm.group('c')), numero_en(s), orden, base))
        return records


# ============================================================================
# FUSIÓN (sin IA): presentaciones de actas + votaciones + sesiones de comisiones
# ============================================================================

import json
from collections import Counter, defaultdict
from datetime import date, timedelta

OUT_KEYS = ["Número", "Tipo", "Descripción", "FechaPres", "Presentador", "Partido", "Tema",
            "C1", "C2", "C3", "C5", "C6", "Estatus", "Fecha de aprobación",
            "Diferencia de días entre fecha de presentación y aprobación", "Año",
            "Contenido", "Comentario", "F", "C", "A"]
C_SLOTS = ["C1", "C2", "C3", "C5", "C6"]   # la estructura solicitada no incluye C4

_STOP = set("de la el los las del al por que se en y a para con un una lo su sus e o u cual como entre otros "
            "acuerdo punto iniciativa decreto dictamen minuta".split())
_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
          "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def cnorm(s: str) -> str:
    return norm_key(re.sub(r'^\s*de (la |los |las )?', '', strip_acc(s or '').lower()))


def _stems(s: str) -> set:
    return {t[:6] for t in tokens(s) if t not in _STOP and len(t) > 1}


def dice(a: str, b: str) -> float:
    A, B = _stems(a), _stems(b)
    return 2 * len(A & B) / (len(A) + len(B)) if A and B else 0.0


def _fmt(d):
    return d.strftime('%d/%m/%y') if d else None


def legislative_year(d):
    if d is None:
        return None
    if d < date(2024, 9, 15):
        return 'N/A'
    if d < date(2025, 9, 15):
        return 'Primero'
    if d < date(2026, 9, 15):
        return 'Segundo'
    return 'Tercero'


_YEAR_MAP = {"Primer año": "Primero", "Segundo año": "Segundo", "Tercer año": "Tercero"}


class ActasMerger:
    def __init__(self, perfiles=None, link_thr_pa=0.55, link_thr_ini=0.45):
        self.thr = {'Punto de Acuerdo': link_thr_pa, 'Iniciativa': link_thr_ini}
        self.items, self.review = [], []
        self.tema_by_comm = {cnorm(k): v for k, v in COMISION_TEMA.items()}
        # partido por diputado, sembrado con perfiles.json
        self.party_by_name = {norm_key(r['Nombre']): PARTY_ALIAS.get(r['Partido'], r['Partido'])
                              for r in (perfiles or []) if r.get('Nombre') and r.get('Partido')}

    def _tema_for(self, desc, comisiones):
        d = strip_acc(desc or '').lower()
        if 'codigo penal' in d:
            return 14
        for c in comisiones:
            if c and cnorm(c) in self.tema_by_comm:
                return self.tema_by_comm[cnorm(c)]
        return None

    def _learn_parties(self, presentations):
        """Diccionario diputado->partido aprendido de las propias actas (descriptor 'integrante del grupo...')."""
        c = defaultdict(Counter)
        for p in presentations:
            if p['Tipo presentador'] == 'diputados' and len(p['Presentadores']) == 1 and p['Partido'] \
                    and not str(p['Partido']).startswith('Conjunto'):
                c[norm_key(p['Presentadores'][0])][p['Partido']] += 1
        self.party_by_name.update({k: v.most_common(1)[0][0] for k, v in c.items()})

    def _party_for(self, names, party):
        if party and party != 'N/A':
            return party
        found = [self.party_by_name.get(norm_key(n)) for n in names]
        if names and all(found):
            u = list(dict.fromkeys(found))
            return u[0] if len(names) == 1 else 'Conjunto: ' + _join(u)
        return party

    def _row_from_presentation(self, p):
        fp = date.fromisoformat(p['Fecha'])
        turno = (p['Turno'] + [None] * 5)[:5]
        return {
            "Número": p['Número'], "Tipo": p['Tipo'], "Descripción": p['Descripción'],
            "FechaPres": _fmt(fp), "Presentador": p['Presentador'],
            "Partido": self._party_for(p['Presentadores'], p['Partido']),
            "Tema": self._tema_for(p['Descripción'], p['Turno']),
            **dict(zip(C_SLOTS, turno)),
            "Estatus": "Pendiente", "Fecha de aprobación": None,
            "Diferencia de días entre fecha de presentación y aprobación": None,
            "Año": legislative_year(fp), "Contenido": None, "Comentario": None,
            "F": None, "C": None, "A": None, "_fp": fp, "_fa": None,
        }

    def _candidates(self, vote, fv):
        out = []
        for it in self.items:
            if it['Estatus'] != 'Pendiente' or not it['_fp'] or it['_fp'] > fv:
                continue
            s = dice(vote['Titulo'], it['Descripción'] or '')
            out.append((s * (1.0 if it['Tipo'] == vote['Tipo'] else 0.9), it))
        return sorted(out, key=lambda x: -x[0])

    def _apply_vote(self, it, v, fv, score):
        it['Estatus'], it['_fa'], it['Fecha de aprobación'] = 'Aprobada', fv, _fmt(fv)
        it['Diferencia de días entre fecha de presentación y aprobación'] = (fv - it['_fp']).days
        it['F'], it['C'], it['A'] = v.get('A favor'), v.get('Contra'), v.get('Abstenciones')
        it['Tema'] = v.get('Tema 1') or it['Tema']
        note = f"Aprobación vinculada automáticamente (similitud {score:.2f}) con la votación '{v['Titulo'][:80]}'"
        it['Comentario'] = (it['Comentario'] + ' | ' if it['Comentario'] else '') + note

    @staticmethod
    def _sessions(comisiones):
        out = []
        for c in comisiones or []:
            for s in c.get('Sesiones', []):
                m = re.match(r'\s*(\d{1,2}) de (\w+) (\d{4})', s.get('Fecha de sesión en comisión') or '')
                mes = _MESES.get(strip_acc(m.group(2)).lower()) if m else None
                if m and mes and s.get('Asunto abordado'):
                    names = [n.strip() for n in re.split(r',| y ', s.get('Presentador') or '') if len(n.split()) >= 2]
                    out.append({"comision": c['Nombre'], "fecha": date(int(m.group(3)), mes, int(m.group(1))),
                                "asunto": s['Asunto abordado'], "presentadores": names})
        return out

    def merge(self, presentations, votes, comisiones_json=None):
        self._learn_parties(presentations)
        directas = defaultdict(list)
        for p in presentations:
            if p['Rol'] == 'directa':
                directas[p['Fecha']].append(p)
            else:
                self.items.append(self._row_from_presentation(p))
        sess = self._sessions(comisiones_json)

        for v in sorted(votes, key=lambda x: x['Fecha']):
            fv = date.fromisoformat(v['Fecha'])
            thr = self.thr.get(v['Tipo'], 0.5)
            good = [(s, it) for s, it in self._candidates(v, fv) if s >= thr]
            if good:
                if len(good) > 1 and good[0][0] - good[1][0] < 0.03 and v['Tipo'] == 'Punto de Acuerdo':
                    self.review.append({"motivo": "Vinculación ambigua", "votacion": v['Titulo'][:120],
                                        "fecha": v['Fecha'], "candidatos": [it['Descripción'][:80] for _, it in good[:3]]})
                for s, it in [x for x in good if x[0] >= max(thr, 0.85 * good[0][0])]:
                    self._apply_vote(it, v, fv, s)
                continue
            # segundo salto: votación -> sesión de comisión -> presentador -> asunto pendiente
            sb = max(((dice(v['Titulo'], s['asunto']), s) for s in sess
                      if s['fecha'] <= fv and (fv - s['fecha']).days <= 150), key=lambda x: x[0], default=(0, None))
            if sb[0] >= 0.40 and sb[1]['presentadores']:
                s = sb[1]
                pc = [(dice(s['asunto'], it['Descripción'] or ''), it) for it in self.items
                      if it['Estatus'] == 'Pendiente' and it['_fp'] <= fv
                      and any(norm_key(n) in norm_key(it['Presentador'] or '') for n in s['presentadores'])]
                pc = sorted([x for x in pc if x[0] >= 0.35], key=lambda x: -x[0])
                if pc:
                    for sc, it in [x for x in pc if x[0] >= 0.85 * pc[0][0]]:
                        self._apply_vote(it, v, fv, sc)
                        it['Comentario'] += f" (vía sesión de comisión del {s['fecha']:%d/%m/%y})"
                    continue
            # sin presentación previa: asunto votado el mismo día
            pres, partido, num = None, None, None
            ds = directas.get(v['Fecha'], [])
            if ds:
                dsb = max(ds, key=lambda p: dice(v['Titulo'], p['Descripción']))
                if dice(v['Titulo'], dsb['Descripción']) >= 0.25:
                    pres, num = dsb['Presentador'], dsb['Número']
                    partido = self._party_for(dsb['Presentadores'], dsb['Partido'])
            if not pres and sb[0] >= 0.40 and sb[1]['presentadores']:
                pres = _join(sb[1]['presentadores'])
                partido = self._party_for(sb[1]['presentadores'], None)
            if not pres:
                self.review.append({"motivo": "Votación sin presentación ni presentador",
                                    "votacion": v['Titulo'][:120], "fecha": v['Fecha']})
            self.items.append({
                "Número": num, "Tipo": v['Tipo'], "Descripción": v['Titulo'], "FechaPres": _fmt(fv),
                "Presentador": pres, "Partido": partido, "Tema": v.get('Tema 1') or None,
                "C1": 'N/A', "C2": None, "C3": None, "C5": None, "C6": None,
                "Estatus": "Aprobada", "Fecha de aprobación": _fmt(fv),
                "Diferencia de días entre fecha de presentación y aprobación": 0,
                "Año": _YEAR_MAP.get(v.get('Año Legislatura'), legislative_year(fv)),
                "Contenido": None, "Comentario": None if pres else "Presentador no identificado en el acta: revisar",
                "F": v.get('A favor'), "C": v.get('Contra'), "A": v.get('Abstenciones'), "_fp": fv, "_fa": fv,
            })

        self._fill_content(sess)
        return self.finish()

    def _fill_content(self, sess):
        for it in self.items:
            comms = {cnorm(it[c]) for c in C_SLOTS if it[c] and it[c] != 'N/A'}
            lim = it['_fa'] or date.max
            best = max(((dice(s['asunto'], it['Descripción'] or ''), s) for s in sess
                        if cnorm(s['comision']) in comms and it['_fp'] <= s['fecha'] <= lim),
                       key=lambda x: x[0], default=(0, None))
            if best[0] >= 0.35:
                it['Contenido'] = best[1]['asunto']
                it['Comentario'] = (it['Comentario'] + ' | ' if it['Comentario'] else '') + \
                    f"Contenido tomado de la sesión de comisión del {best[1]['fecha']:%d/%m/%y}"

    def finish(self):
        def key(it):
            n = it['Número']
            return (it['_fp'], 10**6 if n == 'AG' else (n if isinstance(n, int) else -1))
        return [{k: it.get(k) for k in OUT_KEYS} for it in sorted(self.items, key=key)], self.review