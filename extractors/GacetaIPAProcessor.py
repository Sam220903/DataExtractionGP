# extractors/GacetaIPAProcessor.py
"""
Extrae Iniciativas y Puntos de Acuerdo de una Gaceta Legislativa mensual del
Congreso del Estado de Puebla (800-3000 páginas), 100% con expresiones
regulares (sin IA).

Estrategia (resumen)
--------------------
1. PÁGINAS: el texto crudo de PyMuPDF conserva el pie "Pag. N" de cada página.
   Se usa como separador para reconstruir páginas.
2. SESIONES Y SECCIONES: las páginas "divisoras" (casi vacías) marcan
   "SESIÓN PÚBLICA ...", "ACTA DE LA SESIÓN", "INICIATIVAS PRESENTADAS",
   "PUNTOS DE ACUERDO PRESENTADOS/APROBADOS", "DICTÁMENES", "ACUERDOS APROBADOS".
3. ESPINA DORSAL = ORDEN DEL DÍA de cada sesión (lista numerada con título,
   presentador y grupo legislativo de cada asunto). El número del punto es
   el campo "Número". Cada asunto se confirma contra su sección
   (Iniciativas / Puntos de acuerdo / Dictámenes / Acuerdos).
4. ACTA (enriquecimiento, nunca fuente de registros): por cada
   "PUNTO N DEL ORDEN DEL DÍA" se leen el turno a comisiones (C1..C6, empatadas
   con comisiones_contenido.json) y las votaciones (F, C, A).
5. FINALIZE: Estatus / Fecha de aprobación / F-C-A de una iniciativa o punto
   de acuerdo se resuelven cuando un DICTAMEN (otra sesión, a veces otra
   gaceta) lo aprueba; se empata por similitud del título.
"""

import json
import re
import unicodedata
from datetime import date

try:
    from rapidfuzz import fuzz
    def _sim(a, b):
        return fuzz.token_set_ratio(a, b)
except Exception:  # respaldo sin dependencia
    from difflib import SequenceMatcher
    def _sim(a, b):
        return 100 * SequenceMatcher(None, a, b).ratio()


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
MESES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
    'julio': 7, 'agosto': 8, 'septiembre': 9, 'setiembre': 9, 'octubre': 10,
    'noviembre': 11, 'diciembre': 12,
}

_UNIDADES = {
    'cero': 0, 'un': 1, 'uno': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4,
    'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
    'once': 11, 'doce': 12, 'trece': 13, 'catorce': 14, 'quince': 15,
    'dieciseis': 16, 'diecisiete': 17, 'dieciocho': 18, 'diecinueve': 19,
    'veinte': 20, 'veintiun': 21, 'veintiuno': 21, 'veintidos': 22,
    'veintitres': 23, 'veinticuatro': 24, 'veinticinco': 25, 'veintiseis': 26,
    'veintisiete': 27, 'veintiocho': 28, 'veintinueve': 29,
}
_DECENAS = {'treinta': 30, 'cuarenta': 40, 'cincuenta': 50, 'sesenta': 60,
            'setenta': 70, 'ochenta': 80, 'noventa': 90}


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


def norm(s):
    """Minúsculas, sin acentos ni puntuación, espacios colapsados."""
    s = strip_accents(s or '').lower()
    s = re.sub(r'[^a-z0-9ñ ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def words_to_int(phrase):
    """'TREINTA Y NUEVE' -> 39 ; 'UN' -> 1 ; devuelve None si no es número."""
    toks = norm(phrase).split()
    if not toks:
        return None
    total, i = 0, 0
    while i < len(toks):
        t = toks[i]
        if t in _DECENAS:
            total += _DECENAS[t]
            if i + 2 < len(toks) + 0 and toks[i + 1] == 'y' and i + 2 < len(toks) \
                    and toks[i + 2] in _UNIDADES:
                total += _UNIDADES[toks[i + 2]]
                i += 2
        elif t in _UNIDADES:
            total += _UNIDADES[t]
        else:
            return None
        i += 1
    return total


def trailing_number(phrase):
    """Número expresado en palabras al FINAL de la frase (ignora lo previo)."""
    toks = norm(phrase).split()
    for k in range(min(3, len(toks)), 0, -1):
        n = words_to_int(' '.join(toks[-k:]))
        if n is not None:
            return n
    return None


def fmt_date(d):
    return d.strftime('%d/%m/%y') if d else None


GRUPOS = [  # (patrón sobre texto normalizado, etiqueta)
    (r'accion nacional', 'PAN'),
    (r'\bmorena\b', 'MORENA'),
    (r'partido del trabajo', 'PT'),
    (r'verde ecologista', 'PVEM'),
    (r'movimiento ciudadano', 'MC'),
    (r'revolucionario institucional', 'PRI'),
    (r'nueva alianza', 'Nueva Alianza'),
    (r'fuerza por mexico', 'FXM'),
    (r'partido encuentro solidario|encuentro solidario', 'PES'),
    (r'sin partido|independiente', 'Independiente'),
]


def detect_parties(text):
    n = norm(text)
    found = []
    for pat, lab in GRUPOS:
        m = re.search(pat, n)
        if m:
            found.append((m.start(), lab))
    found.sort()
    return [l for _, l in found]


# ----------------------------------------------------------------------------
class GacetaIPAProcessor:

    SECCIONES = [
        ('acta', r'^ACTA DE LA SESI'),
        ('ini', r'^INICIATIVAS? PRESENTADAS?'),
        ('pa', r'^PUNTOS? DE ACUERDO (PRESENTADOS?|APROBADOS?)'),
        ('dict', r'^DICT[ÁA]MEN(ES)?$'),
        ('acu', r'^ACUERDOS? APROBADOS?'),
    ]

    # Nombres con que el Acta (y el Excel manual) llaman a comisiones que en
    # comisiones_contenido.json figuran con otro nombre.
    ALIAS_COMISION = {
        'Grupos Vulnerables': 'Atención a Personas en Situación de Vulnerabilidad',
        'Familia y los Derechos de la Niñez': 'De la Familia y los Derechos de la Niñez',
        'Ciencia y Tecnología': 'Ciencia, Humanidades, Tecnología e Innovación',
        'Agenda 2030 y Convenios o Acuerdos que se Determinen en el Ámbito Internacional':
            'De la Agenda 2030 y convenios o acuerdos que se determinen en el ámbito internacional para mejorar las necesidades de toda la población',
    }

    def __init__(self, comisiones_path=None, nombres_excel=False):
        """nombres_excel=True devuelve en C1..C6 los nombres antiguos que usa el
        Excel manual ('Grupos Vulnerables', 'Familia y los Derechos de la
        Niñez', 'Ciencia y Tecnología') en lugar de los del JSON."""
        self.nombres_excel = nombres_excel
        self.comisiones = []          # nombres canónicos
        self._com_norm = []           # (norm(nombre), nombre)
        if comisiones_path:
            self._load_comisiones(comisiones_path)
        self.roster = {}              # nombre normalizado -> partido
        self._dictamenes = []         # dictámenes aprobados (todas las gacetas)

    # ------------------------------------------------------------------
    def _load_comisiones(self, path):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            nombres = [c['Nombre'].strip() for c in data if c.get('Nombre')]
        except Exception as e:
            print(f"  Aviso: no se pudo leer {path}: {e}")
            return
        self.comisiones = nombres
        # más largos primero para que "Desarrollo Económico" no pise a
        # "Desarrollo Económico y ..."
        pares = [(norm(n), n) for n in nombres]
        for alias, canon in self.ALIAS_COMISION.items():
            if canon in nombres:
                pares.append((norm(alias), canon))
        self._com_norm = sorted(pares, key=lambda x: -len(x[0]))

    # ------------------------------------------------------------------
    # 1) Páginas y secciones
    # ------------------------------------------------------------------
    @staticmethod
    def _split_pages(text):
        parts = re.split(r'^[ \t]*Pag[.,]\s*(\d+)[ \t]*$', text, flags=re.M)
        pages = []
        for i in range(0, len(parts) - 1, 2):
            pages.append((int(parts[i + 1]), parts[i]))
        if len(parts) % 2 == 1 and parts[-1].strip():
            pages.append((-1, parts[-1]))
        return pages

    @staticmethod
    def _flat(txt):
        return ' '.join(txt.split())

    def _classify(self, flat, tail_lines=None):
        """Clasifica una página como divisora. Si la página es larga (p. ej. la
        primera de la gaceta: cola del índice + divisor de la 1a sesión, porque
        el divisor no trae pie propio), se evalúan solo sus últimas líneas."""
        cand = [flat]
        if tail_lines:
            cand.append(tail_lines)
        for c in cand:
            f = re.sub(r'H\. CONGRESO DEL ESTADO\s+P\s*U\s*E\s*B\s*L\s*A', ' ', c, flags=re.I)
            f = re.sub(r'P[áa]gina \d+ de \d+', ' ', f)
            f = ' '.join(f.split())
            if not f or len(f) > 170:
                continue
            for key, pat in self.SECCIONES:
                if re.match(pat, f, flags=re.I):
                    return key
            if re.search(r'SESI[ÓO]N\s+P[ÚU]BLICA', f, re.I) and self._parse_date(f):
                return 'session'
        return None

    @staticmethod
    def _parse_date(s):
        m = re.search(r'(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+(?:de|del)\s+(\d{4})', s, re.I)
        if not m:
            return None
        mes = MESES.get(strip_accents(m.group(2)).lower())
        if not mes:
            return None
        try:
            return date(int(m.group(3)), mes, int(m.group(1)))
        except ValueError:
            return None

    ITEM_RE = re.compile(
        r'(?m)^\s*\d{1,2}\.\s*\n?\s*(Lectura|Asuntos Generales|Toma de|Posicionamiento|Elecci|Mensaje|'
        r'Comparecencia|Informe|Clausura|Declaratoria|Discusi|Efem|Entrega|Votaci)')

    def _segment(self, pages):
        sessions, cur, section = [], None, None
        for no, txt in pages:
            flat = self._flat(txt)
            lines = [l.strip() for l in txt.split('\n') if l.strip()]
            tail = ' '.join(lines[-5:]) if len(flat) > 170 else None
            kind = self._classify(flat, tail)
            if kind == 'session':
                cur = {'fecha': self._parse_date(tail or flat), 'orden': [], 'acta': [],
                       'ini': [], 'pa': [], 'dict': [], 'acu': [], 'page': no}
                sessions.append(cur)
                section = 'pre'
                continue
            if cur is None:
                continue
            if kind:
                section = kind
                continue
            if section == 'pre':
                es_orden_inicio = re.search(r'ORDEN DEL D[ÍI]A', flat[:350], re.I)
                if es_orden_inicio or (cur['orden'] and self.ITEM_RE.search(txt)):
                    cur['orden'].append(txt)
                    if cur['fecha'] is None:
                        cur['fecha'] = self._parse_date(flat[:700])
                elif cur['orden']:
                    section = 'otro'   # terminó el orden del día
            elif section in cur:
                cur[section].append(txt)
        return sessions

    # ------------------------------------------------------------------
    # 2) Orden del día
    # ------------------------------------------------------------------
    def _parse_orden(self, pages):
        flat = self._flat('\n'.join(pages))
        flat = re.sub(r'ORDEN DEL D[ÍI]A\s+Per[ií]odo\s+\w+\s+[\w ]{3,25}?\d{4}\s+P[áa]g\.?\s*\d+', ' ', flat, flags=re.I)
        flat = re.sub(r'H\. CONGRESO DEL ESTADO\s+P\s*U\s*E\s*B\s*L\s*A', ' ', flat, flags=re.I)
        flat = ' '.join(flat.split())
        items, pos, n = [], 0, 1
        starts = []
        while True:
            m = re.compile(rf'(?:^|(?<=\s)){n}\.\s+(?=[A-ZÁÉÍÓÚÑ"“])').search(flat, pos)
            if not m:
                break
            starts.append((n, m.start(), m.end()))
            pos = m.end()
            n += 1
        for i, (num, s, e) in enumerate(starts):
            end = starts[i + 1][1] if i + 1 < len(starts) else len(flat)
            items.append((num, flat[e:end].strip()))
        return items

    TIPOS = [
        ('Iniciativa', r'^Lectura d(?:e la|e las|el|e los)\s+(?:Iniciativas?|Proyectos? de Decreto|Minuta)'),
        ('Punto de Acuerdo', r'^Lectura d(?:el|e los)\s+Puntos? de Acuerdo'),
        ('Acuerdo', r'^Lectura d(?:el|e los)\s+Acuerdos?\b'),
        ('Dictamen', r'^Lectura d(?:el|e los)\s+Dict[áa]men(?:es)?\b'),
        ('Oficios', r'^Lectura d(?:e los|el)\s+oficios?\b'),
    ]

    def _classify_item(self, txt):
        for tipo, pat in self.TIPOS:
            if re.match(pat, txt, re.I):
                return tipo
        return None

    DESC_SPLIT = re.compile(
        r'\b(por el que|por la que|por virtud del cual|por el cual|mediante el cual|'
        r'por medio del cual|por el que se|con el que|por los que|por las que)\b', re.I)

    def _parse_item(self, tipo, txt):
        m = re.match(r'^Lectura d\w+(?: \w+)?\s+(?P<obj>.+?)\s+que\s+(?:presentan?|remiten?|formulan?|suscriben?|envían?|envia)\s+(?P<resto>.*)$', txt, re.I)
        if not m:
            return None
        obj, resto = m.group('obj'), m.group('resto')
        d = self.DESC_SPLIT.search(resto)
        if d:
            bloque, desc = resto[:d.start()].strip(' ,;'), resto[d.start():].strip()
        else:
            bloque, desc = resto, ''
        obj = re.sub(r'\s+', ' ', obj).strip()
        if tipo == 'Iniciativa':
            obj_txt = obj
        elif tipo == 'Punto de Acuerdo':
            obj_txt = 'Punto de Acuerdo'
        else:
            obj_txt = obj  # "Acuerdo"
        descripcion = (obj_txt + ' ' + desc).strip() if desc else obj_txt
        presentador, partido = self._parse_presentador(bloque)
        return descripcion, presentador, partido

    def _parse_presentador(self, bloque):
        b = bloque.strip()
        partidos = detect_parties(b)
        # Quitar la parte institucional ("de la Sexagésima Segunda Legislatura...")
        b_corto = re.split(r',?\s+de la Sexag[ée]sima', b, flags=re.I)[0]
        # Conjunto de diputados (grupo legislativo)
        if re.match(r'^(las y los|las diputadas y los|los|las)\s+diputad', b_corto, re.I):
            if partidos:
                return 'Conjunto ' + ' y '.join(partidos), 'Conjunto: ' + ' y '.join(partidos)
            return 'Conjunto', 'Conjunto'
        m = re.match(r'^(?:la|el)\s+(?:Diputad[oa]|Ciudadan[oa]\s+Diputad[oa])\s+(?P<n>[^,]+)', b_corto, re.I)
        if m:
            nombre = m.group('n').strip()
            nombre = re.sub(r'\s+', ' ', nombre)
            key = norm(nombre)
            if partidos:
                self.roster[key] = partidos[0]
                return nombre, partidos[0]
            return nombre, self.roster.get(key, 'N/A')
        nb = norm(b_corto)
        if 'senador' in nb or 'camara de senadores' in nb:
            return 'Cámara de Senadoras y Senadores', 'N/A'
        if 'junta de gobierno' in nb:
            return 'Junta de Gobierno y Coordinación Política', 'N/A'
        if 'coordinador' in nb:
            return 'Coordinadores de bancadas', 'N/A'
        mc = re.match(r'^(?:la|las)\s+(Comisi[óo]n(?:es)?[^,]*)', b_corto, re.I)
        if mc:
            return mc.group(1).strip(), 'N/A'
        first = re.split(r',', b_corto)[0]
        first = re.sub(r'^(la|el|las|los)\s+', '', first, flags=re.I).strip()
        return (first[:120] or 'N/A'), 'N/A'

    # ------------------------------------------------------------------
    # 3) Acta: turno a comisiones y votaciones
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_acta(pages):
        t = ' '.join(' '.join(pages).split())
        t = re.sub(r'ACTA\s+Secretar[ií]a General\s+Sesi[óo]n .*?\d{4}', ' ', t, flags=re.I)
        t = re.sub(r'H\. CONGRESO DEL ESTADO\s+P\s*U\s*E\s*B\s*L\s*A', ' ', t, flags=re.I)
        t = re.sub(r'\bPag\.?\s*\d+(\s+\d+)?\b', ' ', t)
        return ' '.join(t.split())

    PUNTO_RE = re.compile(
        r'PUNTO\s+([A-ZÁÉÍÓÚ]+(?:\s+Y\s+[A-ZÁÉÍÓÚ]+)?)\s+DEL\s+ORDEN\s+DEL\s+D[ÍI]A')

    def _acta_segments(self, pages):
        t = self._clean_acta(pages)
        marks = []
        for m in self.PUNTO_RE.finditer(t):
            n = words_to_int(m.group(1))
            if n is not None:
                marks.append((m.start(), n))
        segs = {}
        for i, (s, n) in enumerate(marks):
            e = marks[i + 1][0] if i + 1 < len(marks) else len(t)
            segs[n] = segs.get(n, '') + ' ' + t[s:e]
        return segs

    VOTO_RE = re.compile(
        r'([A-ZÁÉÍÓÚ ]{2,60}?)\s+VOTOS?\s+A\s+FAVOR,?\s+([A-ZÁÉÍÓÚ ]{2,40}?)\s+VOTOS?\s+EN\s+CONTRA'
        r'(?:,|\s+Y)?\s+([A-ZÁÉÍÓÚ ]{2,40}?)\s+VOTOS?\s+EN\s+ABSTENCI')

    def _votos(self, seg):
        """Lista de (F, C, A, posición_fin) en el segmento."""
        out = []
        for m in self.VOTO_RE.finditer(seg):
            f = trailing_number(m.group(1))
            c = words_to_int(m.group(2))
            a = words_to_int(m.group(3))
            if f is not None and c is not None and a is not None:
                out.append((f, c, a, m.end()))
        return out

    def _comisiones_en(self, seg):
        """Comisiones canónicas mencionadas en la cláusula de TURNO."""
        m = re.search(r'TURN[A-ZÁÉÍÓÚ]*', seg)
        if not m:
            return []
        clause = seg[m.start():]
        # la cláusula termina en "PARA SU ESTUDIO" (o en el siguiente punto)
        e = re.search(r'PARA\s+SU\s+ESTUDIO|EN\s+EL\s+PUNTO|\.\s', clause)
        clause = clause[:e.end()] if e else clause[:900]
        n = norm(clause)
        hallazgos = []
        for nn, nombre in self._com_norm:
            for mm in re.finditer(r'(?<![a-z])' + re.escape(nn) + r'(?![a-z])', n):
                hallazgos.append((mm.start(), mm.end(), nombre))
        # quitar solapes (gana el más largo, ya ordenados por longitud)
        hallazgos.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
        usados, final = [], []
        for s, e2, nombre in hallazgos:
            if any(not (e2 <= us or s >= ue) for us, ue in usados):
                continue
            usados.append((s, e2))
            final.append((s, nombre))
        final.sort()
        res = []
        for _, nombre in final:
            if nombre not in res:
                res.append(nombre)
        res = res[:6]
        if self.nombres_excel:
            inv = {v: k for k, v in self.ALIAS_COMISION.items() if k != 'Agenda 2030 y Convenios o Acuerdos que se Determinen en el Ámbito Internacional'}
            res = [inv.get(r, r) for r in res]
        return res

    DICT_APROB_RE = re.compile(
        r'APROBAD[OA]S?\s+EN\s+TODOS\s+SUS\s+T[ÉE]RMINOS\s+(?:EL|LA|LOS)\s+(.+?)(?=;\s|\.\s|,\s+ORDENANDO|\s+ORDENANDO|$)')

    # ------------------------------------------------------------------
    # 4) Proceso por gaceta
    # ------------------------------------------------------------------
    @staticmethod
    def _anio(d):
        if not d:
            return 'N/A'
        ini = date(2024, 9, 15)
        if d < ini:
            return 'N/A'
        k = (d - ini).days // 365
        k = (d.year - 2024) - (1 if (d.month, d.day) < (9, 15) else 0)
        return {0: 'Primero', 1: 'Segundo', 2: 'Tercero'}.get(k, 'N/A')

    def process_file(self, text, source_file=None):
        pages = self._split_pages(text)
        sessions = self._segment(pages)
        records = []
        for s in sessions:
            if not s['fecha']:
                print(f"  Aviso: sesión sin fecha (pág. {s['page']}), se omite")
                continue
            items = self._parse_orden(s['orden'])
            segs = self._acta_segments(s['acta']) if s['acta'] else {}
            n_i = n_p = n_d = 0
            for num, txt in items:
                tipo = self._classify_item(txt)
                if tipo is None:
                    continue
                seg = segs.get(num, '')
                if tipo == 'Oficios':
                    records.extend(self._minutas_senado(s, num, seg, source_file))
                    continue
                if tipo == 'Dictamen':
                    n_d += self._registrar_dictamenes(s, num, txt, seg)
                    continue
                parsed = self._parse_item(tipo, txt)
                if not parsed:
                    continue
                desc, presentador, partido = parsed
                coms = self._comisiones_en(seg)
                votos = self._votos(seg)
                aprobada = bool(votos) and not coms and 'APROBAD' in seg
                rec = {
                    'Número': num,
                    'Tipo': 'Iniciativa' if tipo == 'Iniciativa' else 'Punto de Acuerdo',
                    'Descripción': desc,
                    'FechaPres': fmt_date(s['fecha']),
                    'Presentador': presentador,
                    'Partido': partido,
                    'Tema': None,
                    'C1': coms[0] if coms else 'N/A',
                    'C2': coms[1] if len(coms) > 1 else None,
                    'C3': coms[2] if len(coms) > 2 else None,
                    'C4': coms[3] if len(coms) > 3 else None,
                    'C5': coms[4] if len(coms) > 4 else None,
                    'C6': coms[5] if len(coms) > 5 else None,
                    'Estatus': 'Pendiente',
                    'Fecha de aprobación': None,
                    'Diferencia de días entre fecha de presentación y aprobación': None,
                    'Año': self._anio(s['fecha']),
                    'Contenido': None,
                    'Comentario': None,
                    'F': None, 'C': None, 'A': None,
                    '_fecha': s['fecha'], '_src': source_file,
                }
                if aprobada:
                    f, c, a, _ = votos[0]
                    rec.update({
                        'Estatus': 'Aprobada',
                        'Fecha de aprobación': fmt_date(s['fecha']),
                        'Diferencia de días entre fecha de presentación y aprobación': 0,
                        'F': f, 'C': c, 'A': a,
                    })
                records.append(rec)
                if tipo == 'Iniciativa':
                    n_i += 1
                else:
                    n_p += 1
            print(f"  Sesión {fmt_date(s['fecha'])}: {n_i} iniciativa(s), "
                  f"{n_p} punto(s) de acuerdo/acuerdo(s), {n_d} dictamen(es) aprobados")
        return records

    MINUTA_RE = re.compile(
        r'REMITE\s+(?:\d+\s+)?(PROYECTOS?\s+DE\s+DECRETO\s+.+?),?\s+A\s+LA(?:S)?\s+COMISI[ÓO]N(?:ES)?\s+'
        r'(?:GENERAL(?:ES)?\s+|UNIDAS\s+)?(?:DE\s+|DEL\s+)?(.+?)(?=;\s+Y\s+EL\s+OFICIO|;\s+EL\s+OFICIO|\.\s+(?:EL|LOS|LA|LAS)\s|\.\s*$|$)')

    @staticmethod
    def _sentence_case(t):
        t = t.strip().lower()
        t = re.sub(r'\s+', ' ', t)
        t = t[:1].upper() + t[1:]
        for a, b in (('constitución política de los estados unidos mexicanos',
                      'Constitución Política de los Estados Unidos Mexicanos'),
                     ('constitución política del estado libre y soberano de puebla',
                      'Constitución Política del Estado Libre y Soberano de Puebla'),
                     ('puebla', 'Puebla'), ('estado libre y soberano', 'Estado Libre y Soberano')):
            t = t.replace(a, b)
        return t

    def _minutas_senado(self, sesion, num, seg, source_file):
        """Proyectos de decreto que remite la Cámara de Senadores (reformas a la
        Constitución federal): se leen del punto 'oficios' del Acta y se turnan a
        comisión. En el Excel manual se registran como Iniciativa."""
        out = []
        for m in self.MINUTA_RE.finditer(seg):
            desc = self._sentence_case(m.group(1))
            coms = self._comisiones_en('TURNO A LA COMISION DE ' + m.group(2))
            out.append({
                'Número': num, 'Tipo': 'Iniciativa', 'Descripción': desc,
                'FechaPres': fmt_date(sesion['fecha']),
                'Presentador': 'Cámara de Senadoras y Senadores', 'Partido': 'N/A',
                'Tema': None,
                'C1': coms[0] if coms else 'N/A',
                'C2': coms[1] if len(coms) > 1 else None,
                'C3': coms[2] if len(coms) > 2 else None,
                'C4': None, 'C5': None, 'C6': None,
                'Estatus': 'Pendiente', 'Fecha de aprobación': None,
                'Diferencia de días entre fecha de presentación y aprobación': None,
                'Año': self._anio(sesion['fecha']),
                'Contenido': None, 'Comentario': None, 'F': None, 'C': None, 'A': None,
                '_fecha': sesion['fecha'], '_src': source_file,
            })
        return out

    def _registrar_dictamenes(self, sesion, num, txt, seg):
        """Guarda los dictámenes aprobados en el Acta para empatarlos después."""
        votos = self._votos(seg)
        if not votos:
            return 0
        cuenta = 0
        # Un título por cada "APROBADO EN TODOS SUS TÉRMINOS EL DICTAMEN ..."
        titulos = [(m.start(), m.group(1)) for m in self.DICT_APROB_RE.finditer(seg)]
        for f, c, a, fin in votos:
            titulo = None
            for pos, t in titulos:
                if pos >= fin - 5:
                    titulo = t
                    break
            if titulo is None:
                continue
            self._dictamenes.append({
                'fecha': sesion['fecha'], 'titulo': titulo, 'norm': norm(titulo),
                'F': f, 'C': c, 'A': a,
            })
            cuenta += 1
        return cuenta

    # ------------------------------------------------------------------
    # 5) Post-proceso entre gacetas
    # ------------------------------------------------------------------
    @staticmethod
    def _core(desc):
        """Parte que identifica el asunto: desde 'por el que ...'."""
        m = re.search(r'\b(por el que|por la que|por virtud del cual|por el cual)\b', desc, re.I)
        return norm(desc[m.start():]) if m else norm(desc)

    def finalize(self, records, umbral=88):
        for r in records:
            if r['Estatus'] == 'Aprobada':
                continue
            core = self._core(r['Descripción'])
            if len(core) < 25:
                continue
            mejor, mejor_s = None, 0
            for d in self._dictamenes:
                if d['fecha'] < r['_fecha']:
                    continue
                s = _sim(core, self._core(d['titulo']))
                if s > mejor_s:
                    mejor, mejor_s = d, s
            if mejor and mejor_s >= umbral:
                r['Estatus'] = 'Aprobada'
                r['Fecha de aprobación'] = fmt_date(mejor['fecha'])
                r['Diferencia de días entre fecha de presentación y aprobación'] = \
                    (mejor['fecha'] - r['_fecha']).days
                r['F'], r['C'], r['A'] = mejor['F'], mejor['C'], mejor['A']
        for r in records:
            for k in [k for k in r if k.startswith('_')]:
                del r[k]
        return records