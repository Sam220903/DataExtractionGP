import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Ruta base: carpeta donde vive este script, para que las rutas relativas
# no dependan del directorio desde el que se ejecute (cwd)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Config de estilo (replicando Perfiles_Plantilla.xlsx)
# ---------------------------------------------------------------------------
FUENTE_ENCABEZADO = Font(name="Calibri", size=11, bold=True)
RELLENO_ENCABEZADO = PatternFill(start_color="FF9CC2E5", end_color="FF9CC2E5", fill_type="solid")
ALINEACION_ENCABEZADO = Alignment(vertical="bottom")

FUENTE_DATO = Font(name="Arial", size=11, color="FF000000")

BORDE_FINO = Border(
    left=Side(style="thin", color="FF000000"),
    right=Side(style="thin", color="FF000000"),
    top=Side(style="thin", color="FF000000"),
    bottom=Side(style="thin", color="FF000000"),
)

VALOR_VACIO = "N/A"  # criterio de la plantilla original para celdas sin dato

ANCHO_MINIMO = 8
ANCHO_MAXIMO = 60  # evita columnas gigantes cuando hay textos muy largos (direcciones, preparación, etc.)


# ---------------------------------------------------------------------------
# Orden alfabético por apellido paterno
# ---------------------------------------------------------------------------
# Formato en que viene el nombre completo en el JSON:
#   "nombre_primero"   -> "Juan Carlos Pérez López"  (nombre(s) + paterno + materno)
#   "apellido_primero" -> "Pérez López Juan Carlos"  (paterno + materno + nombre(s))
# Si el nombre trae coma ("Pérez López, Juan Carlos") se detecta solo, sin
# importar esta opción.
FORMATO_NOMBRE = "nombre_primero"

_TITULOS = {"c", "dip", "diputado", "diputada", "lic", "licda", "mtro", "mtra",
            "dr", "dra", "ing", "prof", "profa", "arq", "sen", "senador", "senadora"}
_PARTICULAS = {"de", "del", "la", "las", "los", "le", "san", "santa", "da", "di", "van", "von"}


def _clave_texto(s: str) -> str:
    """Minúsculas y sin acentos; la ñ queda después de la n (como en español)."""
    s = str(s or "").lower().replace("ñ", "n\uffff")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def _grupos(tokens: list[str]) -> list[str]:
    """Une las partículas con su apellido: ['De','la','Cruz'] -> ['De la Cruz']."""
    grupos, actual = [], []
    for t in reversed(tokens):
        actual.insert(0, t)
        if t.lower() not in _PARTICULAS:
            grupos.insert(0, " ".join(actual))
            actual = []
    if actual:  # partículas sueltas al inicio
        if grupos:
            grupos[0] = " ".join(actual) + " " + grupos[0]
        else:
            grupos.append(" ".join(actual))
    return grupos


def separar_nombre(nombre_completo: str) -> tuple[str, str, str]:
    """Devuelve (apellido_paterno, apellido_materno, nombres)."""
    texto = re.sub(r"\s+", " ", str(nombre_completo or "")).strip()
    if not texto:
        return "", "", ""

    # Quita títulos al inicio (Dip., C., Lic., ...)
    tokens = texto.split(" ")
    while len(tokens) > 1 and tokens[0].lower().strip(".") in _TITULOS:
        tokens.pop(0)
    texto = " ".join(tokens)

    # "Apellidos, Nombres"
    if "," in texto:
        apellidos, nombres = [p.strip() for p in texto.split(",", 1)]
        g = _grupos(apellidos.split())
        return (g[0] if g else ""), (" ".join(g[1:]) if len(g) > 1 else ""), nombres

    g = _grupos(texto.split())
    if len(g) == 1:
        return g[0], "", ""

    if FORMATO_NOMBRE == "apellido_primero":
        if len(g) == 2:
            return g[0], "", g[1]
        return g[0], g[1], " ".join(g[2:])

    # nombre_primero
    if len(g) == 2:
        return g[1], "", g[0]
    return g[-2], g[-1], " ".join(g[:-2])


def clave_orden_nombre(nombre_completo: str) -> tuple:
    pat, mat, nom = separar_nombre(nombre_completo)
    return (_clave_texto(pat), _clave_texto(mat), _clave_texto(nom))


# Campo del JSON que trae el nombre completo. None = detectarlo solo.
CAMPO_NOMBRE = None
_CAMPOS_NOMBRE = ["nombre completo", "nombre_completo", "nombre", "nombres", "diputado", "diputada"]
_CAMPOS_PATERNO = ["apellido paterno", "apellido_paterno", "primer apellido", "paterno"]
_CAMPOS_MATERNO = ["apellido materno", "apellido_materno", "segundo apellido", "materno"]


def _buscar_campo(claves: list[str], candidatos: list[str]):
    mapa = {_clave_texto(k): k for k in claves}
    for c in candidatos:
        if c in mapa:
            return mapa[c]
    return None


def ordenar_por_apellido(registros: list[dict]) -> list[dict]:
    """Ordena alfabéticamente empezando por el apellido paterno."""
    claves = construir_columnas(registros)

    campo_pat = _buscar_campo(claves, _CAMPOS_PATERNO)
    if campo_pat and not CAMPO_NOMBRE:
        campo_mat = _buscar_campo(claves, _CAMPOS_MATERNO)
        campo_nom = _buscar_campo(claves, _CAMPOS_NOMBRE)

        def clave(r):
            return (_clave_texto(r.get(campo_pat)),
                    _clave_texto(r.get(campo_mat)) if campo_mat else "",
                    _clave_texto(r.get(campo_nom)) if campo_nom else "")

        print(f"Orden: por '{campo_pat}' (apellido paterno).")
        return sorted(registros, key=clave)

    campo = CAMPO_NOMBRE or _buscar_campo(claves, _CAMPOS_NOMBRE)
    if not campo:
        print("[aviso] No se encontró el campo con el nombre; se deja el orden original. "
              "Define CAMPO_NOMBRE al inicio del script.")
        return registros

    print(f"Orden: por apellido paterno a partir del campo '{campo}'.")
    return sorted(registros, key=lambda r: clave_orden_nombre(r.get(campo)))


def autoajustar_anchos(ws, ultima_col: int, ultima_fila: int) -> None:
    """
    Recorre cada columna y ajusta su ancho al contenido más largo que tenga,
    dentro de un mínimo y un máximo razonables.
    """
    for col_idx in range(1, ultima_col + 1):
        letra = get_column_letter(col_idx)
        max_len = len(str(ws.cell(row=1, column=col_idx).value or ""))

        for fila_idx in range(2, ultima_fila + 1):
            valor = ws.cell(row=fila_idx, column=col_idx).value
            if valor is not None:
                largo = len(str(valor))
                if largo > max_len:
                    max_len = largo

        ancho = max(ANCHO_MINIMO, min(max_len + 2, ANCHO_MAXIMO))
        ws.column_dimensions[letra].width = ancho


def cargar_registros(ruta_json: str) -> list[dict]:
    """Lee el JSON y devuelve la lista de registros bajo la clave 'registros'."""
    with open(ruta_json, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "registros" in data:
        registros = data["registros"]
    elif isinstance(data, list):
        registros = data
    else:
        raise ValueError(
            "Formato de JSON no reconocido: se esperaba un objeto con la clave "
            "'registros' o directamente una lista de registros."
        )

    if not registros:
        raise ValueError("El JSON no contiene registros.")

    return registros


def construir_columnas(registros: list[dict]) -> list[str]:
    """
    Determina el orden de columnas a partir de las claves del primer registro,
    y agrega al final cualquier clave nueva que aparezca en otros registros
    (por si el JSON se actualiza con campos adicionales en el futuro).
    """
    columnas = list(registros[0].keys())
    vistas = set(columnas)

    for registro in registros[1:]:
        for clave in registro.keys():
            if clave not in vistas:
                columnas.append(clave)
                vistas.add(clave)

    return columnas


def normalizar_valor(valor):
    """Aplica el mismo criterio que la plantilla: None -> 'N/A'."""
    if valor is None:
        return VALOR_VACIO
    if isinstance(valor, str) and valor.strip() == "":
        return VALOR_VACIO
    return valor


def generar_excel(registros: list[dict], ruta_salida: str) -> None:
    columnas = construir_columnas(registros)

    wb = Workbook()
    ws = wb.active
    ws.title = "Hoja 1"

    # --- Encabezados ---
    # Columna A sin encabezado (igual que la plantilla: es el consecutivo)
    ws.cell(row=1, column=1, value=None)
    for idx, nombre_col in enumerate(columnas, start=2):
        celda = ws.cell(row=1, column=idx, value=nombre_col)
        celda.font = FUENTE_ENCABEZADO
        celda.fill = RELLENO_ENCABEZADO
        celda.alignment = ALINEACION_ENCABEZADO
        celda.border = BORDE_FINO

    # --- Datos ---
    for fila_idx, registro in enumerate(registros, start=2):
        # Consecutivo en columna A
        celda_a = ws.cell(row=fila_idx, column=1, value=fila_idx - 1)
        celda_a.font = FUENTE_DATO

        for col_idx, nombre_col in enumerate(columnas, start=2):
            valor = normalizar_valor(registro.get(nombre_col))
            celda = ws.cell(row=fila_idx, column=col_idx, value=valor)
            celda.font = FUENTE_DATO

    ultima_fila = len(registros) + 1
    ultima_col = len(columnas) + 1
    ultima_col_letra = get_column_letter(ultima_col)

    # --- Anchos de columna ajustados al contenido ---
    autoajustar_anchos(ws, ultima_col, ultima_fila)

    # --- Paneles inmovilizados (igual que la plantilla: C2) ---
    ws.freeze_panes = "C2"

    # --- Autofiltro sobre todo el rango de datos ---
    ws.auto_filter.ref = f"A1:{ultima_col_letra}{ultima_fila}"

    wb.save(ruta_salida)
    print(f"Excel generado: {ruta_salida}")
    print(f"Registros escritos: {len(registros)}")
    print(f"Columnas: {len(columnas)}")


def main():
    ruta_json = os.path.join(BASE_DIR, "..", "data", "perfiles.json")
    ruta_salida = os.path.join(BASE_DIR, "..", "outputs", "Perfiles.xlsx")

    if not os.path.exists(ruta_json):
        print(f"No se encontró el archivo: {ruta_json}")
        sys.exit(1)

    os.makedirs(os.path.dirname(ruta_salida), exist_ok=True)

    registros = cargar_registros(ruta_json)
    registros = ordenar_por_apellido(registros)
    generar_excel(registros, ruta_salida)


if __name__ == "__main__":
    main()