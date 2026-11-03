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

import hashlib
import json
import os
import re
import time
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


NOISE_RE = [
    re.compile(r'Av\.\s*32\s+Oriente.*?72290', re.I),
    re.compile(r'www\.congresopuebla\.gob\.mx', re.I),
    re.compile(r'P[áa]gina\s+\d+\s+de\s+\d+', re.I),
    re.compile(r'Marzo\.\s*Mes\s+de\s+las\s+Mujeres', re.I),
    re.compile(r'H\. CONGRESO DEL ESTADO\s+P\s*U\s*E\s*B\s*L\s*A', re.I),
]


def quitar_ruido(flat):
    """Elimina pies/encabezados de página que se cuelan en medio de las frases."""
    for rx in NOISE_RE:
        flat = rx.sub(' ', flat)
    return ' '.join(flat.split())


_CENT = {'cien': 100, 'ciento': 100, 'doscientos': 200, 'trescientos': 300,
         'cuatrocientos': 400, 'quinientos': 500, 'seiscientos': 600,
         'setecientos': 700, 'ochocientos': 800, 'novecientos': 900}
_RX_CENT = re.compile(
    r'\b(' + '|'.join(_CENT) + r')(?:\s+((?:treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa)'
    r'(?:\s+y\s+(?:un|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve))?'
    r'|diez|once|doce|trece|catorce|quince|diecis[ée]is|diecisiete|dieciocho|diecinueve|'
    r'veinte|veinti\w+|un|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve))?\b', re.I)


def numeros_grandes_a_digitos(texto):
    """'doscientos diecisiete' -> '217' (solo centenas; el Excel deja 'cuatro' en letra)."""
    def rep(m):
        n = _CENT[m.group(1).lower()]
        if m.group(2):
            r = words_to_int(m.group(2))
            if r is None:
                return m.group(0)
            n += r
        return str(n)
    return _RX_CENT.sub(rep, texto)


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

    PARTIDO_PERFIL = {'FXMP': 'FXM', 'PNA': 'Nueva Alianza', 'PMC': 'MC'}

    # --- IA (solo Contenido de Iniciativas y Comentario extraordinario) ---
    IA_MODEL = "gemini-3.1-flash-lite"
    IA_MAX_ATTEMPTS = 4
    IA_BASE_DELAY = 5
    IA_BATCH = 6               # iniciativas por llamada
    IA_HEAD, IA_TAIL = 3500, 2000   # caracteres del documento que se envían

    def __init__(self, comisiones_path=None, nombres_excel=False,
                 perfiles_path=None, usar_ia=False, ia_cache_path=None):
        """nombres_excel=True devuelve en C1..C6 los nombres antiguos que usa el
        Excel manual ('Grupos Vulnerables', 'Familia y los Derechos de la
        Niñez', 'Ciencia y Tecnología') en lugar de los del JSON."""
        self.nombres_excel = nombres_excel
        self.deputies = []            # (nombre oficial, palabras, partido)
        if perfiles_path:
            self._load_perfiles(perfiles_path)
        self.usar_ia = usar_ia
        self.ia_cache_path = ia_cache_path
        self.ia_cache = {}
        self._ia_client = None
        self._ia_types = None
        self.ia_disabled_reason = None
        if usar_ia:
            self._init_ia()
        self.comisiones = []          # nombres canónicos
        self._com_norm = []           # (norm(nombre), nombre)
        if comisiones_path:
            self._load_comisiones(comisiones_path)
        self.roster = {}              # nombre normalizado -> partido
        self._dictamenes = []         # dictámenes aprobados (todas las gacetas)

    # ------------------------------------------------------------------
    # Perfiles de diputados (partido y nombre oficial)
    # ------------------------------------------------------------------
    def _load_perfiles(self, path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  Aviso: no se pudo leer {path}: {e}")
            return
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        for d in data if isinstance(data, list) else []:
            if not isinstance(d, dict) or not d.get('Nombre'):
                continue
            nombre = re.sub(r'\s+', ' ', str(d['Nombre'])).strip()
            partido = str(d.get('Partido') or 'N/A').strip()
            partido = self.PARTIDO_PERFIL.get(partido, partido)
            self.deputies.append((nombre, norm(nombre).split(), partido))

    def _match_diputado(self, nombre, umbral=90):
        """(nombre oficial, partido) del diputado en perfiles.json, o None.
        Acepta nombre completo, truncado (prefijo) o con una errata."""
        words = norm(nombre).split()
        if len(words) < 2 or not self.deputies:
            return None
        for off, ow, par in self.deputies:
            if words == ow:
                return off, par
        pref = [(off, par) for off, ow, par in self.deputies
                if len(words) < len(ow) and len(words) >= 2 and ow[:len(words)] == words]
        if len(pref) == 1:
            return pref[0]
        j = ' '.join(words)
        mejor, mejor_s = None, 0
        for off, ow, par in self.deputies:
            sc = _sim(j, ' '.join(ow))
            if sc > mejor_s:
                mejor, mejor_s = (off, par), sc
        return mejor if mejor_s >= umbral else None

    # ------------------------------------------------------------------
    # IA: Contenido (solo Iniciativas) y Comentario (solo casos extraordinarios)
    # ------------------------------------------------------------------
    def _init_ia(self):
        try:
            from google import genai
            from google.genai import types
            from dotenv import load_dotenv
            load_dotenv()
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("falta GEMINI_API_KEY en .env")
            self._ia_client = genai.Client(api_key=key)
            self._ia_types = types
        except Exception as e:
            self.ia_disabled_reason = f"IA desactivada: {e}"
            print(f"  Aviso: {self.ia_disabled_reason}")
        if self.ia_cache_path and os.path.exists(self.ia_cache_path):
            try:
                with open(self.ia_cache_path, encoding='utf-8') as f:
                    self.ia_cache = json.load(f)
            except (OSError, ValueError):
                self.ia_cache = {}

    def _save_ia_cache(self):
        if not self.ia_cache_path:
            return
        try:
            with open(self.ia_cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.ia_cache, f, ensure_ascii=False, indent=1)
        except OSError as e:
            print(f"  Aviso: no se pudo guardar caché de IA: {e}")

    IA_PROMPT = """
=== CONTEXTO ===
Eres un analista legislativo del Congreso del Estado de Puebla. Recibes varias INICIATIVAS DE DECRETO.
De cada una tienes su "titulo" (tomado del Orden del día) y un "extracto" del documento con tres partes:
[ENCABEZADO] (donde el documento nombra su propia iniciativa), [MOTIVOS] (exposición de motivos) y
[ARTICULADO] (el decreto propuesto). Usa ÚNICAMENTE el texto recibido; no inventes información.
=== FIN DEL CONTEXTO ===

=== INSTRUCCIONES ===
PASO 0 - Verificación. Compara el título con el [ENCABEZADO]. Si el extracto claramente trata de OTRA
iniciativa (otra ley u otro asunto distinto al del título), no lo uses: resume solo con el título y deja
Comentario vacío. Si el extracto dice "(sin extracto...)", también usa solo el título.

A. Contenido (obligatorio, nunca null): 1 o 2 oraciones, máximo 300 caracteres, en español neutro.
   Explica la SUSTANCIA: qué problema atiende o qué cambia en la práctica y para quién (derechos,
   obligaciones, facultades, sanciones, procedimientos). NO repitas números de artículos, fracciones
   ni el nombre completo de la ley como si fuera el resumen; el título ya los contiene. No empieces con
   "La iniciativa propone reformar...". Si el extracto solo permite parafrasear el título, hazlo en una
   oración breve sin detalles inventados.
   Mal:  "Propone reformar los artículos 41 Bis y 43 Bis de la Ley de Turismo del Estado de Puebla."
   Bien: "Precisa las definiciones de turismo gastronómico y cultural y las actividades que abarca cada
          uno, para dar certeza a prestadores y autoridades."

B. Comentario: déjalo SIEMPRE como "" (cadena vacía), salvo que en el extracto haya evidencia explícita
   de algo extraordinario en el trámite o en el contenido, por ejemplo: la iniciativa se retira o se
   modifica, se solicita dispensa de trámite o urgencia, se adhieren legisladores de otros grupos, o el
   propio documento señala un riesgo o controversia concreta. Una frase como máximo.
   NO son extraordinarios, y por tanto van en "": que modifique varias leyes, que sea una reforma
   constitucional o de gran extensión, que cree un municipio, que reforme el Reglamento o la Ley
   Orgánica del Congreso, o cualquier observación deducida solo del título.
   Se espera que menos de 1 de cada 20 iniciativas tenga Comentario.

No incluyas saludos ni markdown.
=== FIN DE INSTRUCCIONES ===

=== SALIDA REQUERIDA ===
Únicamente un arreglo JSON válido, un objeto por iniciativa recibida y en el mismo orden:
[ {"id": "<id recibido>", "Contenido": "<texto>", "Comentario": "<texto o vacío>"} ]
=== FIN DE SALIDA REQUERIDA ===
"""

    def _llamar_modelo(self, prompt):
        """Llama a Gemini con reintentos. Devuelve lista de dicts o None si falla.
        Si se agota la cuota o la API key es inválida, desactiva la IA el resto de la corrida."""
        for intento in range(1, self.IA_MAX_ATTEMPTS + 1):
            try:
                resp = self._ia_client.models.generate_content(
                    model=self.IA_MODEL, contents=prompt,
                    config=self._ia_types.GenerateContentConfig(response_mime_type="application/json"))
                data = json.loads(resp.text)
                if not isinstance(data, list):
                    raise ValueError("la respuesta no es un arreglo JSON")
                return data
            except Exception as e:
                msg = str(e).lower()
                code = getattr(e, 'code', None)
                fatal = code in (401, 403) or "api key" in msg or (code == 429 and ("per day" in msg or "perday" in msg))
                if fatal:
                    self.ia_disabled_reason = f"IA desactivada por error fatal: {e}"
                    print(f"  {self.ia_disabled_reason}")
                    return None
                reintentable = code in (429, 500, 502, 503, 504) or isinstance(e, (ValueError, ConnectionError, TimeoutError))
                if reintentable and intento < self.IA_MAX_ATTEMPTS:
                    espera = self.IA_BASE_DELAY * (2 ** (intento - 1))
                    print(f"  IA: reintentando en {espera}s (intento {intento}/{self.IA_MAX_ATTEMPTS})...")
                    time.sleep(espera)
                    continue
                print(f"  IA: error no recuperable: {e}")
                return None
        return None

    @staticmethod
    def _ia_key(rec):
        h = hashlib.md5(norm(rec['Descripción'])[:300].encode()).hexdigest()[:12]
        return f"{rec['FechaPres']}|{rec['Número']}|{h}"

    def _localizar_documentos(self, paginas_ini, recs):
        """Asigna a cada iniciativa su texto dentro de la sección 'Iniciativas presentadas'.
        Los documentos van en el mismo orden que el Orden del día, por eso la búsqueda
        avanza siempre hacia adelante. Devuelve {id(rec): texto o None}."""
        if not paginas_ini:
            return {id(r): None for r in recs}
        pn = [norm(quitar_ruido(self._flat(t)))[:7000] for t in paginas_ini]
        # Cada documento abre con el encabezado "C. Diputados integrantes de la Mesa Directiva..."
        starts = [i for i, t in enumerate(pn)
                  if re.search(r'mesa directiva', t[:400])]
        if len(starts) == len(recs):
            inicios = list(starts)               # mismo número de documentos y de asuntos: por orden
        else:
            candidatas = list(range(len(pn)))
            umbral = 80
            inicios, cursor = [], 0
            for r in recs:
                clave = self._clave_doc(r['Descripción'])
                hallado = None
                if len(clave) >= 40:
                    # 1a pasada hacia adelante (orden del documento); 2a pasada global si falló
                    for desde in (cursor, 0):
                        for i in candidatas:
                            if i < desde:
                                continue
                            try:
                                from rapidfuzz import fuzz as _fz
                                ok = _fz.partial_ratio(clave, pn[i]) >= umbral
                            except Exception:
                                ok = clave[:70] in pn[i]
                            if ok:
                                hallado = i
                                break
                        if hallado is not None:
                            break
                inicios.append(hallado)
                if hallado is not None and hallado > cursor:
                    cursor = hallado
        textos = {}
        for k, r in enumerate(recs):
            i = inicios[k]
            if i is None:
                textos[id(r)] = None
                continue
            fin = next((inicios[j] for j in range(k + 1, len(recs)) if inicios[j] is not None and inicios[j] > i),
                       min(len(paginas_ini), i + 25))
            fin = min(fin, i + 25)
            doc = quitar_ruido(self._flat('\n'.join(paginas_ini[i:max(fin, i + 1)])))
            doc = self._extracto(doc)
            textos[id(r)] = doc
        return textos

    @staticmethod
    def _clave_doc(desc):
        """Texto del título a partir del verbo ('se reforma...'), para que 'por el que' (Orden del día)
        y 'por virtud del cual' (documento) no impidan el empate."""
        n = norm(desc)
        m = re.search(r'\bse\s+(?:reforma|reforman|adiciona|adicionan|deroga|derogan|expide|expiden|crea|abroga|'
                      r'modifica|modifican|declara|eleva|inscribe|establece|otorga|autoriza|aprueba)\b', n)
        return (n[m.start():] if m else n)[:160]

    RX_MOTIVOS = re.compile(r'C\s*O\s*N\s*S\s*I\s*D\s*E\s*R\s*A\s*N\s*D\s*O|EXPOSICI[ÓO]N\s+DE\s+MOTIVOS', re.I)
    RX_ARTICULADO = re.compile(r'A\s*R\s*T\s*[ÍI]\s*C\s*U\s*L\s*O\s+(?:Ú\s*N\s*I\s*C\s*O|PRIMERO)|\bPRIMERO\.?\s*[-–]', re.I)

    def _extracto(self, doc):
        """Parte útil del documento: encabezado (para verificar que es la iniciativa correcta),
        exposición de motivos y articulado propuesto. Omite fundamentos legales y firmas."""
        enc = re.search(r'sometemos?\s+a\s+(?:la\s+)?consideraci[óo]n|somete\s+a\s+(?:la\s+)?consideraci[óo]n', doc, re.I)
        encabezado = doc[enc.start():enc.start() + 450] if enc else doc[:450]
        mot = self.RX_MOTIVOS.search(doc, enc.start() if enc else 0)
        cuerpo = doc[mot.end():] if mot else doc[(enc.start() + 450 if enc else 450):]
        art = self.RX_ARTICULADO.search(cuerpo, 500)
        motivos = cuerpo[:art.start()] if art else cuerpo
        articulado = cuerpo[art.start():] if art else ''
        out = '[ENCABEZADO] ' + encabezado
        out += ' [MOTIVOS] ' + motivos[:self.IA_HEAD] + (' [...]' if len(motivos) > self.IA_HEAD else '')
        if articulado:
            out += ' [ARTICULADO] ' + articulado[:self.IA_TAIL]
        return out

    def _enriquecer_con_ia(self, sesion, recs):
        """Rellena Contenido/Comentario de las iniciativas de la sesión (una llamada por lote)."""
        if not self.usar_ia or self.ia_disabled_reason or not self._ia_client:
            return
        ini = [r for r in recs if r['Tipo'] == 'Iniciativa']
        textos = self._localizar_documentos(sesion['ini'], [r for r in ini if r.get('_orden')])
        pendientes = []
        for r in ini:
            k = self._ia_key(r)
            if k in self.ia_cache:
                r['Contenido'] = self.ia_cache[k].get('Contenido') or None
                r['Comentario'] = self.ia_cache[k].get('Comentario') or None
            else:
                pendientes.append(r)
        for a in range(0, len(pendientes), self.IA_BATCH):
            if self.ia_disabled_reason:
                return
            lote = pendientes[a:a + self.IA_BATCH]
            bloque = []
            for r in lote:
                bloque.append({'id': self._ia_key(r), 'titulo': r['Descripción'],
                               'extracto': textos.get(id(r)) or '(sin extracto: usa solo el título)'})
            prompt = self.IA_PROMPT + "\n=== INICIATIVAS ===\n" + json.dumps(bloque, ensure_ascii=False) + "\n=== FIN ==="
            out = self._llamar_modelo(prompt)
            if out is None:
                continue
            por_id = {str(o.get('id')): o for o in out if isinstance(o, dict)}
            for r in lote:
                o = por_id.get(self._ia_key(r))
                if not o:
                    continue
                cont = str(o.get('Contenido') or '').strip() or None
                com = str(o.get('Comentario') or '').strip() or None
                r['Contenido'], r['Comentario'] = cont, com
                self.ia_cache[self._ia_key(r)] = {'Contenido': cont, 'Comentario': com}
            self._save_ia_cache()

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
        parts = re.split(r'^[ \t]*Pag[.,]\s*(\d+)[ \t]*$', text, flags=re.M | re.I)
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
            if re.search(r'SESI[ÓO]N\s+P[ÚU]BLICA', f, re.I) and self._parse_date(f) \
                    and not re.match(r'ACTA', f, re.I) and not re.search(r'FIRMAS|EXTRACTO|CORRESPONDE', f, re.I):
                return 'session'
            for key, pat in self.SECCIONES:
                if re.match(pat, f, flags=re.I):
                    return key
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

    def _sesion_solo_orden(self, text):
        """Gacetas de la Comisión Permanente que solo traen el Orden del día (sin páginas
        divisoras ni Acta): se toma el Orden como una sesión única."""
        i = re.search(r'ORDEN DEL D[ÍI]A', text)
        if not i:
            return []
        ini = i.start()
        fecha = self._parse_date(self._flat(text[ini:ini + 1500]))
        fin = re.search(r'\n\s*\d{1,2}\.\s*\n?\s*Clausura', text[ini:])
        corte = ini + (fin.end() + 400 if fin else 120000)
        return [{'fecha': fecha, 'orden': [text[ini:corte]], 'acta': [], 'ini': [],
                 'pa': [], 'dict': [], 'acu': [], 'page': 1}] if fecha else []

    # ------------------------------------------------------------------
    # 2) Orden del día
    # ------------------------------------------------------------------
    def _parse_orden(self, pages):
        flat = self._flat('\n'.join(pages))
        flat = re.sub(r'ORDEN DEL D[ÍI]A\s+Per[ií]odo\s+\w+\s+[\w ]{3,25}?\d{4}\s+P[áa]g\.?\s*\d+', ' ', flat, flags=re.I)
        flat = quitar_ruido(flat)
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
            items.append((num, re.sub(r'\s+\d{1,3}$', '', flat[e:end].strip())))
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
        r'por medio del cual|por el que se|con el que|por los que|por las que|que contienen?)\b', re.I)

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
        descripcion = self._normalizar_desc(tipo, (obj_txt + ' ' + desc).strip() if desc else obj_txt)
        presentador, partido = self._parse_presentador(bloque)
        return descripcion, presentador, partido

    def _normalizar_desc(self, tipo, d):
        """Deja la descripción en el formato del Excel manual."""
        d = quitar_ruido(d)
        d = re.sub(r'\s+\d{1,3}$', '', d)
        d = re.sub(r'\s+respetuosamente\b', '', d, flags=re.I)
        d = numeros_grandes_a_digitos(d)
        d = re.sub(r'(?:,|;)?\s*(?:y\s*,?\s*)?en\s+su\s+caso,?\s+aprobaci[óo]n\.?$', '', d, flags=re.I).strip(' ,;')
        d = re.sub(r',?\s+entre\s+otr[oa]s?\s+(?:resolutivos?|puntos?)\.?$', '', d, flags=re.I)
        if tipo == 'Punto de Acuerdo':
            d = re.sub(r'^Punto de Acuerdo por el que (?!se\b)', 'Acuerdo que ', d, flags=re.I)
            d = re.sub(r'^Punto de Acuerdo por el que se\b', 'Acuerdo por el que se', d, flags=re.I)
            d = re.sub(r'^Punto de Acuerdo\b', 'Acuerdo', d, flags=re.I)
        if tipo == 'Acuerdo':
            d = re.sub(r',?\s+en\s+t[ée]rminos\s+de\s+lo\s+dispuesto.*$', '', d, flags=re.I)
        if tipo != 'Iniciativa' and len(d) > 600:      # el Excel manual rara vez pasa de ~600
            d = d[:600].rsplit(' ', 1)[0]
        d = d.strip(' ,;')
        if d and not d.endswith('.'):
            d += '.'
        return d

    def _parse_presentador(self, bloque):
        b = bloque.strip()
        partidos = detect_parties(b)
        # Quitar la parte institucional ("de la Sexagésima Segunda Legislatura...")
        b_corto = re.split(r',?\s+de la Sexag[ée]sima', b, flags=re.I)[0]
        nb0 = norm(b_corto)
        es_dip = bool(re.match(r'^(la|el)\s+diputad', b_corto, re.I))
        if 'junta de gobierno' in nb0 and not es_dip:
            return 'Junta de Gobierno y Coordinación Política', None
        if re.match(r'^(las y los |los |las )?(coordinadores|coordinadoras)', nb0) and not es_dip:
            return 'Coordinadores de bancadas', None
        # Conjunto de diputados (grupo legislativo)
        if re.match(r'^(las y los|las diputadas y los|los|las)\s+diputad', b_corto, re.I):
            if partidos:
                return 'Conjunto ' + ' y '.join(partidos), 'Conjunto: ' + ' y '.join(partidos)
            return 'Conjunto', 'Conjunto'
        m = re.match(r'^(?:la|el)\s+(?:Diputad[oa]|Ciudadan[oa]\s+Diputad[oa])\s+(?P<n>[^,]+)', b_corto, re.I)
        if m:
            nombres = [re.sub(r'\s+', ' ', n).strip()
                       for n in re.split(r'\s+y\s+(?:la|el)\s+(?:Diputad[oa]\s+)?', m.group('n'), flags=re.I)]
            oficiales, pars = [], []
            for nombre in nombres:
                hit = self._match_diputado(nombre)
                if hit:
                    nombre, par = hit
                else:
                    par = partidos[0] if partidos and len(nombres) == 1 else self.roster.get(norm(nombre))
                    if par and len(nombres) == 1:
                        self.roster[norm(nombre)] = par
                oficiales.append(nombre)
                if par and par not in pars:
                    pars.append(par)
            if not pars:
                return ' y '.join(oficiales), 'N/A'
            return ' y '.join(oficiales), (pars[0] if len(pars) == 1 else 'Conjunto: ' + ' y '.join(pars))
        nb = norm(b_corto)
        if 'senador' in nb or 'camara de senadores' in nb:
            return 'Cámara de Senadoras y Senadores', None
        if 'junta de gobierno' in nb:
            return 'Junta de Gobierno y Coordinación Política', None
        if 'coordinador' in nb:
            return 'Coordinadores de bancadas', None
        mc = re.match(r'^(?:la|las)\s+(Comisi[óo]n(?:es)?[^,]*)', b_corto, re.I)
        if mc:
            return mc.group(1).strip(), None
        first = re.split(r',', b_corto)[0]
        first = re.sub(r'^(la|el|las|los)\s+', '', first, flags=re.I).strip()
        return (first[:120] or 'N/A'), None

    # ------------------------------------------------------------------
    # 3) Acta: turno a comisiones y votaciones
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_acta(pages):
        t = ' '.join(' '.join(pages).split())
        t = re.sub(r'ACTA\s+Secretar[ií]a General\s+Sesi[óo]n .*?\d{4}', ' ', t, flags=re.I)
        t = quitar_ruido(t)
        t = re.sub(r'\bPag\.?\s*\d+(\s+\d+)?\b', ' ', t)
        return ' '.join(t.split())

    PUNTO_RE = re.compile(
        r'PUNTO\s+([A-ZÁÉÍÓÚ]+(?:\s+Y\s+[A-ZÁÉÍÓÚ]+)?)\s+DEL\s+ORDEN\s+DEL\s+D[ÍI]A')

    RANGO_RE = re.compile(
        r'PUNTOS?\s+(?:DEL\s+)?([A-ZÁÉÍÓÚ]+(?:\s+Y\s+[A-ZÁÉÍÓÚ]+)?)\s+AL?\s+([A-ZÁÉÍÓÚ]+(?:\s+Y\s+[A-ZÁÉÍÓÚ]+)?)\s+DEL\s+ORDEN\s+DEL\s+D[ÍI]A')

    def _acta_segments(self, pages):
        """Devuelve (segs, rangos). segs: número de punto -> texto del Acta.
        rangos: [(a, b, texto)] para frases agrupadas como 'PUNTOS DEL CUATRO AL
        DIECIOCHO DEL ORDEN DEL DÍA', donde el Acta lista varios asuntos juntos."""
        t = self._clean_acta(pages)
        marks = []
        for m in self.RANGO_RE.finditer(t):
            a, b = words_to_int(m.group(1)), words_to_int(m.group(2))
            if a is not None and b is not None:
                marks.append((m.start(), ('R', a, b)))
        for m in self.PUNTO_RE.finditer(t):
            n = words_to_int(m.group(1))
            if n is not None and not any(abs(pos - m.start()) < 40 and k[0] == 'R' for pos, k in marks):
                marks.append((m.start(), n))
        marks.sort(key=lambda x: x[0])
        segs, rangos = {}, []
        for i, (st, n) in enumerate(marks):
            e = marks[i + 1][0] if i + 1 < len(marks) else len(t)
            if isinstance(n, tuple):
                rangos.append((n[1], n[2], t[st:e]))
            else:
                segs[n] = segs.get(n, '') + ' ' + t[st:e]
        return segs, rangos

    @staticmethod
    def _chunks_rango(texto):
        """Parte un rango agrupado en un fragmento por asunto (cada turno termina en
        'PARA SU ESTUDIO Y RESOLUCIÓN PROCEDENTE')."""
        out, ini = [], 0
        for m in re.finditer(r'PARA\s+SU\s+ESTUDIO\s+Y\s+RESOLUCI[ÓO]N\s+PROCEDENTE', texto):
            out.append(texto[ini:m.end()])
            ini = m.end()
        return out

    def _seg_de_rango(self, rangos, num, core):
        mejor, mejor_s = '', 0
        for a, b, texto in rangos:
            if not (a <= num <= b):
                continue
            for ch in self._chunks_rango(texto):
                sc = _sim(core, norm(ch))
                if sc > mejor_s:
                    mejor, mejor_s = ch, sc
        return mejor if mejor_s >= 70 else ''

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
        if not sessions:
            sessions = self._sesion_solo_orden(text)
        records = []
        for s in sessions:
            if not s['fecha']:
                print(f"  Aviso: sesión sin fecha (pág. {s['page']}), se omite")
                continue
            items = self._parse_orden(s['orden'])
            segs, rangos = self._acta_segments(s['acta']) if s['acta'] else ({}, [])
            n_i = n_p = n_d = 0
            for num, txt in items:
                tipo = self._classify_item(txt)
                if tipo is None:
                    continue
                seg = segs.get(num, '')
                if not seg.strip() and rangos:
                    seg = self._seg_de_rango(rangos, num, self._core(txt))
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
                    '_fecha': s['fecha'], '_src': source_file, '_orden': True,
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
            self._enriquecer_con_ia(s, [r for r in records if r['_fecha'] == s['fecha']])
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
                'Presentador': 'Cámara de Senadoras y Senadores', 'Partido': None,
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


if __name__ == '__main__':
    # Prueba rápida:  python GacetaIPAProcessor.py ruta/gaceta.pdf [comisiones.json] [perfiles.json]
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from lib.PDFProcessor import PDFProcessor

    pdf = sys.argv[1]
    com = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), '..', 'data', 'comisiones_contenido.json')
    per = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(__file__), '..', 'data', 'perfiles.json')

    proc = GacetaIPAProcessor(comisiones_path=com, perfiles_path=per, usar_ia=False)
    texto = PDFProcessor().extract_text(pdf)           # texto crudo: conserva los pies "Pag. N"
    registros = proc.finalize(proc.process_file(texto, source_file=pdf))

    salida = 'temp_output_ipa.json'
    with open(salida, 'w', encoding='utf-8') as f:
        json.dump(registros, f, ensure_ascii=False, indent=4)
    print(f"{len(registros)} registro(s) -> {salida}")