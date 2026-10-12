#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
script7.py
Arma el JSON principal (una fila por iniciativa / punto de acuerdo) a partir de:
  - unidad_participacion.json  (universo de filas)             <- Script 3 | Unidad y participación
  - comisiones_contenido.json  (comisiones, comités, sesiones) <- Script 5 | Comisiones y comités
  - perfiles.json              (diputados y partidos)          <- Script 1 | Perfiles

Reglas:
  - Hay exactamente una fila de salida por cada registro de unidad_participacion.json.
  - Comisiones (C1..C6): solo nombres que existen en comisiones_contenido.json.
      1) Si la fuente indica la comisión dictaminadora (campo Presentador de la unidad), esa manda.
      2) Si no, se busca por pista en el título y por similitud contra las sesiones.
  - Presentador:
      * Diputados: solo los que existen en perfiles.json (con su nombre tal como aparece ahí).
      * Puntos de acuerdo: diputados, "Junta de Gobierno y Coordinación Política",
        "Coordinadores de bancadas" o uno o más partidos.
      * Iniciativas: lo anterior, más actores que sí pueden presentarlas (Titular del Ejecutivo del
        Estado, Cámara de Senadoras y Senadores, Ayuntamientos, etc.). Las minutas de reformas a la
        Constitución federal se asignan a la "Cámara de Senadoras y Senadores" y las leyes de
        ingresos/egresos del Estado al Titular del Ejecutivo del Estado.
      * Comisiones, direcciones, secretarías, el Ejecutivo Federal, etc. nunca son presentadores.
      * Si no hay dato confiable, Presentador y Partido quedan en null (no se inventa).
  - Partido: se obtiene de perfiles.json (o de los partidos presentadores); "N/A" para
    presentadores institucionales.

Uso:
    python script7.py [carpeta_con_los_json] [archivo_salida]
Por defecto: ./data  ->  ./data/iniciativas_pa.json
Si ./data no existe en el directorio actual, también se busca junto al script
(./data y ../data). Solo usa la librería estándar de Python.
"""
import argparse
import collections
import datetime
import difflib
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ----------------------------------------------------------------- config
THRESHOLD_WITH_HINT = 0.45   # similitud mínima si la comisión ya está identificada
THRESHOLD_NO_HINT = 0.65     # similitud mínima para comisiones no identificadas
MAX_C = 6                    # columnas C1..C6
PRESENTER_MIN_SCORE = 0.60   # similitud mínima de la sesión para tomar de ella al presentador
FUZZY_NAME_RATIO = 0.88      # similitud mínima para aceptar un nombre de diputado con pequeñas variantes

DEFAULT_FOLDER = "./data"
OUTPUT_NAME = "iniciativas_pa.json"

# Archivos de entrada requeridos y el script que los genera
INPUT_FILES = {
    "unidad": ("unidad_participacion.json", "Script 3 | Unidad y participación"),
    "perfiles": ("perfiles.json", "Script 1 | Perfiles"),
    "comisiones": ("comisiones_contenido.json", "Script 5 | Comisiones y comités"),
}
# False: días de diferencia en valor absoluto. True: aprobación - presentación
# (negativo cuando la comisión aprobó antes de la fecha de la unidad de participación).
SIGNED_DIFFERENCE = False
PROGRESS_EVERY = 100         # cada cuántos registros se imprime avance
MAX_LISTED_WITHOUT_PRESENTER = 40

STOP_WORDS = set(
    "de la el los las en y a que por del al se para con un una su sus o e lo como es "
    "sea mediante virtud cual acuerdo punto exhorta presenta comision comisiones "
    "dictamen minuta decreto proyecto presentacion aprobacion congreso estado puebla "
    "honorable".split()
)
MONTHS = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}
YEAR_NAMES = {"primer": "Primero", "segundo": "Segundo", "tercer": "Tercero", "cuarto": "Cuarto"}
PARTY_ALIAS = {"PMC": "MC", "MOVIMIENTO CIUDADANO": "MC"}

# Nombres con los que las fuentes llaman a una comisión que en comisiones_contenido.json
# tiene otro nombre. El valor es el inicio del nombre oficial (normalizado); el nombre final
# siempre sale de comisiones_contenido.json.
COMMISSION_ALIASES = {
    "agenda 2030": "agenda 2030",
    "ciencia y tecnologia": "ciencia humanidades tecnologia e innovacion",
}

# Presentadores institucionales
JGCP_NAME = "Junta de Gobierno y Coordinación Política"
COORDINATORS_NAME = "Coordinadores de bancadas"
SENATE_NAME = "Cámara de Senadoras y Senadores"
INSTITUTIONAL_PARTY = "N/A"

# Actores que pueden presentar una INICIATIVA (no se usan para puntos de acuerdo).
# (expresión sobre texto normalizado sin acentos, nombre final)
EXTERNAL_INITIATORS = [
    (r"titular del (?:poder )?ejecutivo(?! federal)|gobernador(?:a)? del estado|ejecutivo del estado"
     r"|coordinador de gabinete", "Titular del Ejecutivo del Estado"),
    (r"camara de senadoras y senadores|camara de senadores|senado de la republica", SENATE_NAME),
    (r"camara de diputadas y diputados|camara de diputados", "Cámara de Diputadas y Diputados"),
    (r"tribunal superior de justicia", "Tribunal Superior de Justicia del Estado"),
    (r"fiscalia general del estado", "Fiscalía General del Estado"),
]
CITY_COUNCIL_REGEX = re.compile(
    r"Ayuntamiento de (?:la |el )?[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*(?: [A-ZÁÉÍÓÚÑ][\wáéíóúñ]*)*")

PARTY_PATTERNS = [
    (r"\bmorena\b", "MORENA"),
    (r"\bpan\b", "PAN"),
    (r"\bpri\b", "PRI"),
    (r"\bpvem\b|verde ecologista", "PVEM"),
    (r"\bpt\b|partido del trabajo", "PT"),
    (r"\bmc\b|\bpmc\b|movimiento ciudadano", "MC"),
    (r"\bpna\b|nueva alianza", "PNA"),
    (r"\bfxmp?\b|fuerza por mexico", "FXMP"),
]
PARTY_CONTEXT_REGEX = re.compile(
    r"grupos? legislativos?|bancadas?|conjunto|partidos?|fraccion(?:es)? parlamentarias?"
    r"|representacion(?:es)? legislativas?")
COORDINATORS_REGEX = re.compile(r"coordinadores? de (?:las |los )?(?:bancadas|grupos legislativos)")
HINT_REGEXES = [
    re.compile(r"(?:presentad[oa]s?|propuest[oa]s?|promovid[oa]s?) (?:originalmente )?por "
               r"(.{3,250}?)(?:[.;]|$| para | sobre | la cual | el cual | con el fin| en materia| relativ)"),
    re.compile(r"\bque presentan? (.{3,250}?)(?:[.;]|$| para | sobre | en materia| de la sexagesima)"),
]
NAME_TITLE_WORDS = {"diputado", "diputada", "diputados", "diputadas", "dip", "c", "ing", "dr", "dra",
                    "mtra", "mtro", "lic", "los", "las", "el", "la", "y", "e"}
EXECUTIVE_NAME = "Titular del Ejecutivo del Estado"
STATE_BUDGET_REGEX = re.compile(r"ley de (?:ingresos|egresos) del estado")
FEDERAL_CONSTITUTION_REGEX = re.compile(r"constitucion politica de los estados unidos mexicanos")


# ----------------------------------------------------------------- utilidades
def normalize(text):
    text = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def alphanumeric(text):
    """Minúsculas, sin acentos ni puntuación, con espacios simples."""
    return re.sub(r"[^a-z0-9]+", " ", normalize(text)).strip()


def tokenize(text):
    return [w for w in re.findall(r"[a-z0-9]+", normalize(text)) if w not in STOP_WORDS and len(w) > 2]


def parseSpanishDate(text):
    """'27 de agosto de 2026' / '04 de junio 2026' -> date"""
    m = re.match(r"\s*(\d{1,2}) de (\w+)(?: de)? (\d{4})", normalize(text))
    if not m or m.group(2) not in MONTHS:
        return None
    return datetime.date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))


def formatDate(d):
    if d is None:
        return None
    return d.strftime("%d/%m/%y")


def joinNatural(items):
    """['A', 'B', 'C'] -> 'A, B y C'"""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]


def uniqueInOrder(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


# ----------------------------------------------------------------- entrada / validación
def findFile(folder: Path, name: str):
    """Ruta del archivo en la carpeta (sin distinguir mayúsculas), o None."""
    exact = folder / name
    if exact.is_file():
        return exact
    if folder.is_dir():
        for f in folder.iterdir():
            if f.is_file() and f.name.casefold() == name.casefold():
                return f
    return None


def resolveDataFolder(arg):
    """Carpeta con los JSON. Si se indica, se respeta tal cual. Si no, se prueba ./data
    (directorio actual) y luego ./data y ../data junto al script, y por último la carpeta
    actual y la del script, eligiendo la primera donde esté alguno de los JSON."""
    if arg:
        return Path(arg).expanduser().resolve()
    default = Path(DEFAULT_FOLDER).resolve()
    candidates = [default, (BASE_DIR / "data").resolve(), (BASE_DIR / ".." / "data").resolve(),
                  Path.cwd(), BASE_DIR]
    for c in candidates:
        if c.is_dir() and any(findFile(c, n) for n, _ in INPUT_FILES.values()):
            if c != default:
                print(f"  (No se encontraron los JSON en {default}; se usará {c})")
            return c
    return default


def loadJson(path: Path):
    """Devuelve (registros, error). Acepta lista o {'registros': [...]}."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"JSON mal formado ({e})"
    except OSError as e:
        return None, f"no se pudo leer ({e})"
    if isinstance(data, dict) and "registros" in data:
        data = data["registros"]
    if not isinstance(data, list):
        return None, "no contiene una lista de registros"
    if not data:
        return None, "está vacío (0 registros)"
    return data, None


def loadInputs(folder: Path, names: dict):
    """Verifica y carga los 3 JSON. Si falta alguno, avisa qué extracción hay que hacer."""
    print(f"[1/5] Verificando archivos de entrada en: {folder}")
    if not folder.is_dir():
        print(f"  ✗ La carpeta de datos no existe: {folder}")
    missing, data = [], {}
    for key, (defaultName, script) in INPUT_FILES.items():
        name = names.get(key) or defaultName
        path = findFile(folder, name)
        if path is None:
            print(f"  ✗ {name}: NO ENCONTRADO")
            missing.append((name, script, "no existe"))
            continue
        records, error = loadJson(path)
        if error:
            print(f"  ✗ {path.name}: {error}")
            missing.append((name, script, error))
            continue
        print(f"  ✓ {path.name}: {len(records)} registros")
        data[key] = records
    if missing:
        print("\n" + "!" * 70)
        print("ALERTA: faltan datos para continuar.")
        for name, script, reason in missing:
            print(f"  - Falta hacer la extracción de la base correspondiente a "
                  f"'{name}' ({reason}): {script}")
        print("!" * 70)
        return None
    return data


# ----------------------------------------------------------------- diputados (perfiles.json)
def buildDeputyIndex(profiles):
    """Índice de diputados. Cada diputado tiene su nombre oficial, partido y alias únicos
    (nombre completo y prefijos suficientemente largos para no confundir a dos personas)."""
    deputies = []
    aliasOwners = collections.defaultdict(set)
    for p in profiles:
        if not p.get("Nombre"):
            continue
        officialName = re.sub(r"\s+", " ", p["Nombre"]).strip()
        tokens = alphanumeric(officialName).split()
        minPrefix = 3
        if len(tokens) <= 3:
            minPrefix = 2
        aliases = {" ".join(tokens)}
        for size in range(minPrefix, len(tokens)):
            aliases.add(" ".join(tokens[:size]))
        party = PARTY_ALIAS.get(p.get("Partido"), p.get("Partido"))
        deputy = {"name": officialName, "normalized": " ".join(tokens), "party": party, "aliases": aliases}
        deputies.append(deputy)
        for alias in aliases:
            aliasOwners[alias].add(officialName)
    aliasToName = {}
    for alias, owners in aliasOwners.items():
        if len(owners) == 1:
            aliasToName[alias] = next(iter(owners))
    sortedAliases = sorted(aliasToName, key=len, reverse=True)
    byName = {d["name"]: d for d in deputies}
    return {"deputies": deputies, "aliasToName": aliasToName, "sortedAliases": sortedAliases, "byName": byName}


def splitIntoChunks(text):
    """Parte un texto de presentadores en fragmentos (uno por posible persona) ya limpios."""
    parts = re.split(r"[,;:()]|\by\b|\be\b| con | junto con ", normalize(text))
    chunks = []
    for part in parts:
        tokens = alphanumeric(part).split()
        while tokens and tokens[0] in NAME_TITLE_WORDS:
            tokens = tokens[1:]
        if tokens:
            chunks.append(" ".join(tokens))
    return chunks


def matchDeputyInChunk(chunk, deputyIndex):
    padded = f" {chunk} "
    for alias in deputyIndex["sortedAliases"]:
        if f" {alias} " in padded:
            return deputyIndex["aliasToName"][alias]
    tokenCount = len(chunk.split())
    if tokenCount < 2 or tokenCount > 7:
        return None
    bestName, bestRatio = None, 0.0
    for deputy in deputyIndex["deputies"]:
        ratio = difflib.SequenceMatcher(None, chunk, deputy["normalized"]).ratio()
        if ratio > bestRatio:
            bestName, bestRatio = deputy["name"], ratio
    if bestRatio >= FUZZY_NAME_RATIO:
        return bestName
    return None


def findDeputies(text, deputyIndex):
    """Nombres oficiales (de perfiles.json) de los diputados mencionados en el texto."""
    if not text or normalize(text).strip().startswith("varios"):
        return []  # "Varios: ..." mezcla asuntos distintos; no se puede saber cuál corresponde
    found = []
    for chunk in splitIntoChunks(text):
        name = matchDeputyInChunk(chunk, deputyIndex)
        if name and name not in found:
            found.append(name)
    return found


# ----------------------------------------------------------------- otros presentadores permitidos
def findParties(normalizedText):
    """Partidos presentadores: solo si el texto habla de grupo legislativo, bancada, conjunto, etc."""
    if not PARTY_CONTEXT_REGEX.search(normalizedText):
        return []
    found = []
    for regex, sigla in PARTY_PATTERNS:
        m = re.search(regex, normalizedText)
        if m:
            found.append((m.start(), sigla))
    found.sort()
    return uniqueInOrder([sigla for _, sigla in found])


def findExternalInitiators(text):
    """Actores externos con facultad de presentar iniciativas (nombre canónico)."""
    normalizedText = normalize(text)
    found = []
    for regex, name in EXTERNAL_INITIATORS:
        if re.search(regex, normalizedText):
            found.append(name)
    for m in CITY_COUNCIL_REGEX.finditer(text or ""):
        found.append(m.group(0))
    return uniqueInOrder(found)


def partiesOfDeputies(names, deputyIndex):
    parties = []
    for name in names:
        party = deputyIndex["byName"][name]["party"]
        if party and party not in parties:
            parties.append(party)
    return parties


def extractPresenter(text, itemType, deputyIndex):
    """Interpreta un texto de presentador y devuelve un dict o None.
    {"parts": [...], "text": "...", "party": "...", "kind": "deputies|jgcp|coordinators|parties|external"}
    Solo devuelve entidades permitidas; comisiones, secretarías, etc. se descartan."""
    if not text or not str(text).strip():
        return None
    normalizedText = normalize(text)
    if JGCP_NAME.lower() and "junta de gobierno y coordinacion politica" in normalizedText:
        return {"parts": [JGCP_NAME], "text": JGCP_NAME, "party": INSTITUTIONAL_PARTY, "kind": "jgcp"}
    if COORDINATORS_REGEX.search(normalizedText):
        return {"parts": [COORDINATORS_NAME], "text": COORDINATORS_NAME, "party": INSTITUTIONAL_PARTY,
                "kind": "coordinators"}
    names = findDeputies(text, deputyIndex)
    if names:
        parties = partiesOfDeputies(names, deputyIndex)
        return {"parts": names, "text": joinNatural(names), "party": ", ".join(parties), "kind": "deputies"}
    parties = findParties(normalizedText)
    if parties:
        return {"parts": parties, "text": joinNatural(parties), "party": ", ".join(parties), "kind": "parties"}
    if itemType == "Iniciativa":
        externals = findExternalInitiators(text)
        if externals:
            return {"parts": externals, "text": joinNatural(externals), "party": INSTITUTIONAL_PARTY,
                    "kind": "external"}
    return None


def isFederalConstitutionMinute(item):
    """Minuta de reforma a la Constitución federal (la envía la Cámara de Senadoras y Senadores)."""
    if item.get("Tipo") != "Iniciativa":
        return False
    return bool(FEDERAL_CONSTITUTION_REGEX.search(normalize(item.get("Título") or "")))


def isStateBudgetLaw(item):
    """La Ley de Ingresos y la Ley de Egresos del Estado las presenta el Titular del Ejecutivo."""
    if item.get("Tipo") != "Iniciativa":
        return False
    return bool(STATE_BUDGET_REGEX.search(normalize(item.get("Título") or "")))


def extractHintTexts(item):
    """Fragmentos tipo 'presentada por ...' o 'que presenta ...' en título, contenido y comentario."""
    fragments = []
    for field in ("Título", "Contenido", "Comentario"):
        text = normalize(item.get(field) or "")
        for regex in HINT_REGEXES:
            for m in regex.finditer(text):
                fragments.append(m.group(1))
    return fragments


def resolvePresenter(item, selectedSessions, deputyIndex):
    """Orden de evidencia: 1) campo Presentador de la unidad, 2) minuta federal,
    3) 'presentada por ...' en los textos de la unidad, 4) sesiones de comisión emparejadas."""
    itemType = item.get("Tipo")
    presenter = extractPresenter(item.get("Presentador"), itemType, deputyIndex)
    if presenter:
        return presenter
    if isFederalConstitutionMinute(item):
        return {"parts": [SENATE_NAME], "text": SENATE_NAME, "party": INSTITUTIONAL_PARTY, "kind": "external"}
    if isStateBudgetLaw(item):
        return {"parts": [EXECUTIVE_NAME], "text": EXECUTIVE_NAME, "party": INSTITUTIONAL_PARTY, "kind": "external"}
    for fragment in extractHintTexts(item):
        presenter = extractPresenter(fragment, itemType, deputyIndex)
        if presenter:
            return presenter
    for session in selectedSessions:
        if session["score"] < PRESENTER_MIN_SCORE:
            continue
        presenter = extractPresenter(session["s"].get("Presentador"), itemType, deputyIndex)
        if presenter:
            return presenter
    return None


# ----------------------------------------------------------------- comisiones (comisiones_contenido.json)
def commissionKey(name):
    key = alphanumeric(name)
    return re.sub(r"^(?:de la|de los|de las|del|de) ", "", key)


def buildCommissionIndex(commissions):
    """Para cada comisión/comité de comisiones_contenido.json, las claves con que puede aparecer en un texto."""
    entries = []
    for com in commissions:
        name = com["Nombre"]
        key = commissionKey(name)
        keys = [key]
        for alias, targetPrefix in COMMISSION_ALIASES.items():
            if key.startswith(targetPrefix):
                keys.append(alias)
        for k in keys:
            entries.append({"name": name, "key": k})
    entries.sort(key=lambda e: len(e["key"]), reverse=True)
    return entries


def findCommissionsInPresenter(text, commissionIndex):
    """Comisiones nombradas en el campo Presentador de la unidad (comisión dictaminadora)."""
    remaining = f" {alphanumeric(text)} "
    found = []
    for entry in commissionIndex:
        pattern = r"(?<![a-z0-9])" + re.escape(entry["key"]) + r"(?![a-z0-9])"
        if re.search(pattern, remaining):
            if entry["name"] not in found:
                found.append(entry["name"])
            remaining = re.sub(pattern, " ", remaining)
    return found


def findCommissionsInTitle(text, commissionIndex):
    """Comisiones o comités citados en el título con la forma 'Comisión de X' / 'Comité de X'."""
    cleaned = f" {alphanumeric(text)} "
    found = []
    for entry in commissionIndex:
        key = re.escape(entry["key"])
        pattern = (r"(?:comisiones?(?: unidas)?|comites?) (?:de la |de los |de las |del |de )?" + key +
                   r"(?![a-z0-9])|\by la de " + key + r"(?![a-z0-9])")
        if re.search(pattern, cleaned) and entry["name"] not in found:
            found.append(entry["name"])
    return found


def buildSessions(commissions):
    sessions = []
    withoutDate = 0
    for com in commissions:
        for s in com.get("Sesiones", []):
            date = parseSpanishDate(s.get("Fecha de sesión en comisión", ""))
            if date is None and s.get("Fecha de sesión en comisión / comité"):
                try:
                    date = datetime.date.fromisoformat(s["Fecha de sesión en comisión / comité"])
                except ValueError:
                    date = None
            if date is None:
                withoutDate += 1
            sessions.append({"com": com["Nombre"], "s": s, "date": date,
                             "t": set(tokenize(s.get("Asunto abordado", "")))})
        print(f"  · {com['Nombre']}: {len(com.get('Sesiones', []))} sesiones")
    return sessions, withoutDate


def buildSimilarity(sessions):
    """Devuelve la función de similitud ponderada por idf contra las sesiones."""
    documentFrequency = collections.Counter()
    for e in sessions:
        documentFrequency.update(e["t"])
    total = len(sessions)

    def idf(word):
        return math.log((total + 1) / (documentFrequency.get(word, 0) + 1)) + 0.5

    def similarity(title, sessionTokens):
        """Fracción (ponderada por idf) de las palabras del título que aparecen en la sesión."""
        titleTokens = set(tokenize(title))
        if not titleTokens:
            return 0.0
        return sum(idf(w) for w in titleTokens & sessionTokens) / sum(idf(w) for w in titleTokens)

    return similarity


def bestSessionPerCommission(title, presentationDate, sessions, similarity):
    """Para cada comisión, la sesión (anterior o igual a la fecha) que mejor coincide con el título."""
    best = {}
    for e in sessions:
        if e["date"] is None or e["date"] > presentationDate:
            continue
        score = similarity(title, e["t"])
        key = (round(score, 3), e["date"])  # empate -> sesión más reciente
        if e["com"] not in best or key > best[e["com"]][0]:
            best[e["com"]] = (key, score, e)
    return best


def chooseCommissions(item, title, presentationDate, sessions, similarity, commissionIndex):
    """Devuelve (nombres_de_comisiones, sesiones_elegidas), todo proveniente de comisiones_contenido.json."""
    best = bestSessionPerCommission(title, presentationDate, sessions, similarity)

    named = findCommissionsInPresenter(item.get("Presentador") or "", commissionIndex)
    if named:
        names = named[:MAX_C]
        chosenSessions = []
        for name in names:
            if name in best and best[name][1] >= THRESHOLD_WITH_HINT:
                chosenSessions.append(dict(best[name][2], score=best[name][1]))
        return names, chosenSessions

    hinted = findCommissionsInTitle(item.get("Título") or "", commissionIndex)
    chosen = []
    for name, (_, score, session) in best.items():
        isHinted = name in hinted
        threshold = THRESHOLD_NO_HINT
        if isHinted:
            threshold = THRESHOLD_WITH_HINT
        if score >= threshold:
            chosen.append((isHinted, score, session))
    chosen.sort(key=lambda z: (not z[0], -z[1]))
    chosen = chosen[:MAX_C]
    return [z[2]["com"] for z in chosen], [dict(z[2], score=z[1]) for z in chosen]


# ----------------------------------------------------------------- estatus y fechas
def legislativeYear(presentationDate, sourceYearText):
    """Año legislativo: el de la fuente; si contradice la fecha (el año inicia el 15/09), manda la fecha."""
    if (presentationDate.month, presentationDate.day) >= (9, 15):
        index = presentationDate.year - 2024
    else:
        index = presentationDate.year - 2025
    index = max(0, min(2, index))
    byDate = ["Primero", "Segundo", "Tercero"][index]
    words = normalize(sourceYearText or "").split()
    if words and YEAR_NAMES.get(words[0]) == byDate:
        return YEAR_NAMES[words[0]]
    return byDate


def computeStatus(item, presentationDate, selectedSessions, commissionNames):
    """Devuelve (estatus, fecha_de_aprobación)."""
    approvedSession = None
    for session in selectedSessions:
        if session["s"].get("Estatus") == "Aprobado":
            approvedSession = session
            break
    if approvedSession:
        return "Aprobada", approvedSession["date"]
    if selectedSessions:
        sourceStatus = selectedSessions[0]["s"].get("Estatus")
        mapped = {"Aprobado": "Aprobada", "No aprobado": "No aprobada"}.get(sourceStatus, sourceStatus)
        return (mapped or "N/A"), None
    # sin sesión de comisión: aprobado directo en el pleno (p.ej. Mesa Directiva)
    inFavor = item.get("A favor") or 0
    against = item.get("Contra") or 0
    if presentationDate is not None and inFavor > against:
        return "Aprobada", presentationDate
    return "N/A", None


def daysBetween(approvalDate, presentationDate):
    if approvalDate is None or presentationDate is None:
        return None
    diff = (approvalDate - presentationDate).days
    if SIGNED_DIFFERENCE:
        return diff
    return abs(diff)


# ----------------------------------------------------------------- construcción de filas
def buildRow(index, item, context):
    """Una fila de salida por cada registro de unidad. Nunca se omite un registro."""
    try:
        presentationDate = datetime.date.fromisoformat(item["Fecha"])
    except (KeyError, TypeError, ValueError):
        print(f"  ⚠ Registro {index + 2}: fecha inválida {item.get('Fecha')!r}; se conserva sin fechas")
        presentationDate = None

    title = item.get("Título") or ""
    commissionNames, selectedSessions = [], []
    if presentationDate is not None:
        matchText = title + " " + (item.get("Contenido") or "")
        commissionNames, selectedSessions = chooseCommissions(
            item, matchText, presentationDate, context["sessions"], context["similarity"],
            context["commissionIndex"])

    presenter = resolvePresenter(item, selectedSessions, context["deputyIndex"])

    row = {"Número": index + 2,  # fila de Excel (encabezado = 1)
           "Tipo": item.get("Tipo"), "Descripción": title, "FechaPres": formatDate(presentationDate)}
    if presenter:
        row["Presentador"] = presenter["text"]
        row["Partido"] = presenter["party"]
    else:
        row["Presentador"] = None
        row["Partido"] = None
    row["Tema"] = item.get("Tema 1")

    for k in range(MAX_C):
        if k < len(commissionNames):
            row[f"C{k + 1}"] = commissionNames[k]
        elif k == 0:
            row["C1"] = "N/A"
        else:
            row[f"C{k + 1}"] = None

    status, approvalDate = computeStatus(item, presentationDate, selectedSessions, commissionNames)
    row["Estatus"] = status
    row["Fecha de aprobación"] = formatDate(approvalDate)
    row["Diferencia de días entre fecha de presentación y aprobación"] = daysBetween(approvalDate, presentationDate)

    if presentationDate is not None:
        row["Año"] = legislativeYear(presentationDate, item.get("Año Legislatura", ""))
    else:
        row["Año"] = None
    row["Contenido"] = item.get("Contenido", "")
    row["Comentario"] = item.get("Comentario", "")
    row["F"] = item.get("A favor")
    row["C"] = item.get("Contra")
    row["A"] = item.get("Abstenciones")
    return row, presenter


# ----------------------------------------------------------------- verificación del resultado
def validateOutput(rows, presenters, unidad, deputyIndex, commissions):
    """Comprueba las reglas duras. Devuelve la lista de errores encontrados."""
    errors = []
    if len(rows) != len(unidad):
        errors.append(f"Hay {len(rows)} filas y la unidad tiene {len(unidad)} registros")
    for i, (row, item) in enumerate(zip(rows, unidad)):
        if row["Descripción"] != (item.get("Título") or ""):
            errors.append(f"Fila {row['Número']}: la descripción no coincide con la unidad")
    validCommissions = {c["Nombre"] for c in commissions}
    for row in rows:
        for k in range(1, MAX_C + 1):
            value = row[f"C{k}"]
            if value not in (None, "N/A") and value not in validCommissions:
                errors.append(f"Fila {row['Número']}: comisión inexistente en comisiones_contenido.json: {value}")
    validParties = {sigla for _, sigla in PARTY_PATTERNS}
    externalNames = {name for _, name in EXTERNAL_INITIATORS}
    for row, presenter in zip(rows, presenters):
        if presenter is None:
            continue
        kind = presenter["kind"]
        if kind == "deputies":
            for name in presenter["parts"]:
                if name not in deputyIndex["byName"]:
                    errors.append(f"Fila {row['Número']}: diputado fuera de perfiles.json: {name}")
        elif kind == "parties":
            for sigla in presenter["parts"]:
                if sigla not in validParties:
                    errors.append(f"Fila {row['Número']}: partido no reconocido: {sigla}")
        elif kind in ("jgcp", "coordinators"):
            continue
        elif kind == "external":
            if row["Tipo"] != "Iniciativa":
                errors.append(f"Fila {row['Número']}: un punto de acuerdo no puede tener presentador externo")
            for name in presenter["parts"]:
                if name not in externalNames and not name.startswith("Ayuntamiento de"):
                    errors.append(f"Fila {row['Número']}: presentador externo no permitido: {name}")
    return errors


def printSummary(rows, presenters, destination):
    print("\n" + "=" * 70)
    print(f"Archivo generado: {destination}")
    withCommission = sum(1 for r in rows if r["C1"] != "N/A")
    print(f"  {len(rows)} registros | con comisión: {withCommission}"
          f" | sin comisión (N/A): {len(rows) - withCommission}")
    print("  Estatus:", dict(collections.Counter(r["Estatus"] for r in rows)))
    kinds = collections.Counter()
    for row, presenter in zip(rows, presenters):
        if presenter is None:
            kinds[(row["Tipo"], "sin dato")] += 1
        else:
            kinds[(row["Tipo"], presenter["kind"])] += 1
    print("  Presentadores:")
    for (itemType, kind), count in sorted(kinds.items()):
        print(f"    {itemType} · {kind}: {count}")
    withoutPresenter = [r for r, p in zip(rows, presenters) if p is None]
    if withoutPresenter:
        print(f"  Sin presentador confiable (quedan en null): {len(withoutPresenter)}")
        for row in withoutPresenter[:MAX_LISTED_WITHOUT_PRESENTER]:
            print(f"    Número {row['Número']} · {row['Tipo']} · {row['Descripción'][:90]}")
        if len(withoutPresenter) > MAX_LISTED_WITHOUT_PRESENTER:
            print(f"    ... y {len(withoutPresenter) - MAX_LISTED_WITHOUT_PRESENTER} más")


def writeOutput(rows, destination):
    """Guarda el JSON. Devuelve 0 si salió bien o 2 si falló."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=4)
        os.replace(tmp, destination)
    except PermissionError:
        print(f"  ✗ NO SE PUDO GUARDAR: sin permiso o el archivo está abierto en otro programa.\n  Ruta: {destination}")
        return 2
    except OSError as e:
        print(f"  ✗ NO SE PUDO GUARDAR: {e}\n  Ruta: {destination}")
        return 2
    return 0


# ----------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser(description="Arma el JSON principal de iniciativas y puntos de acuerdo.")
    ap.add_argument("carpeta", nargs="?", default=None, help=f"carpeta de los JSON de entrada (por defecto {DEFAULT_FOLDER})")
    ap.add_argument("salida", nargs="?", default=None, help=f"archivo de salida (por defecto <carpeta>/{OUTPUT_NAME})")
    ap.add_argument("--unidad", default=None, help=f"nombre del JSON de unidad (por defecto {INPUT_FILES['unidad'][0]})")
    ap.add_argument("--perfiles", default=None, help=f"nombre del JSON de perfiles (por defecto {INPUT_FILES['perfiles'][0]})")
    ap.add_argument("--comisiones", default=None, help=f"nombre del JSON de comisiones (por defecto {INPUT_FILES['comisiones'][0]})")
    args = ap.parse_args()

    dataDir = resolveDataFolder(args.carpeta)
    # salida: la indicada; si es una carpeta existente se usa el nombre por defecto dentro de ella
    if args.salida:
        destination = Path(args.salida).expanduser().resolve()
    else:
        destination = dataDir / OUTPUT_NAME
    if destination.is_dir():
        destination = destination / OUTPUT_NAME

    data = loadInputs(dataDir, {"unidad": args.unidad, "perfiles": args.perfiles,
                                "comisiones": args.comisiones})
    if data is None:
        print("\nNo se generó ningún archivo.")
        return 1
    unidad, profiles, commissions = data["unidad"], data["perfiles"], data["comisiones"]

    # --- perfiles
    print("\n[2/5] Procesando perfiles (diputados y partidos)...")
    deputyIndex = buildDeputyIndex(profiles)
    print(f"  {len(deputyIndex['deputies'])} diputados con partido")

    # --- sesiones
    print("\n[3/5] Procesando comisiones y comités...")
    sessions, withoutDate = buildSessions(commissions)
    print(f"  Total: {len(sessions)} sesiones en {len(commissions)} comisiones/comités"
          + (f" ({withoutDate} sin fecha interpretable, se ignoran)" if withoutDate else ""))
    context = {
        "deputyIndex": deputyIndex,
        "commissionIndex": buildCommissionIndex(commissions),
        "sessions": sessions,
        "similarity": buildSimilarity(sessions),
    }

    # --- unidad
    total = len(unidad)
    print(f"\n[4/5] Analizando {total} iniciativas y puntos de acuerdo...")
    rows, presenters = [], []
    for i, item in enumerate(unidad):
        if i % PROGRESS_EVERY == 0:
            print(f"  Analizando registro {i + 1} de {total} ({100 * i // total}%)...")
        row, presenter = buildRow(i, item, context)
        rows.append(row)
        presenters.append(presenter)
    print(f"  Analizando registro {total} de {total} (100%)")

    errors = validateOutput(rows, presenters, unidad, deputyIndex, commissions)
    if errors:
        print(f"\n⚠ La verificación encontró {len(errors)} problema(s):")
        for error in errors[:30]:
            print("  -", error)
    else:
        print("  ✓ Verificación: mismas filas que la unidad, presentadores y comisiones válidos")

    # --- escritura
    print("\n[5/5] Guardando resultado...")
    if writeOutput(rows, destination) != 0:
        return 2
    printSummary(rows, presenters, destination)
    return 0


if __name__ == "__main__":
    sys.exit(main())