#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador del Excel "Iniciativas y PA" a partir de uno o más JSON.

Estructura de carpetas esperada:
    proyecto/
    ├── writer/    <- este script
    ├── data/      <- JSON de entrada
    └── outputs/   <- Excel generado

Uso:
    python generar_excel.py
    python generar_excel.py --data-dir RUTA --output-dir RUTA
    python generar_excel.py --json a.json b.json --output-name "Otro.xlsx"

Las rutas por defecto se resuelven a partir de la ubicación de este archivo,
no del directorio desde el que se ejecuta.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, OrderedDict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# =============================================================================
# CONFIGURACIÓN
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = (BASE_DIR / ".." / "data").resolve()
DEFAULT_OUTPUT_DIR = (BASE_DIR / ".." / "outputs").resolve()

# JSON de entrada (dentro de la carpeta data/). Se pueden listar varios:
# todos los registros se juntan en una sola lista.
JSON_FILES = ["iniciativas_pa.json"]

# Nombre del archivo de salida. Se derivó del Excel original
# "Copia de Iniciativas y PA 2024-2027.xlsx" quitando "Copia de" y el rango de
# años. Sin fecha ni versión: cada ejecución sobrescribe el mismo archivo.
OUTPUT_NAME = "Iniciativas y PA.xlsx"

# Campo del JSON que decide a qué hoja va cada registro, y nombre de cada hoja.
TYPE_FIELD = "Tipo"
SHEETS = [
    ("Iniciativa", "Iniciativas"),
    ("Punto de Acuerdo", "Puntos de acuerdo"),
]
NO_TYPE_SHEET = "Sin tipo"  # registros sin valor en TYPE_FIELD

# Columnas conocidas del original (claves del JSON). Sirven solo para avisar de
# columnas nuevas o ausentes; las columnas reales salen siempre de los datos.
KNOWN_COLUMNS = [
    "Número", "Tipo", "Descripción", "FechaPres", "Presentador", "Partido",
    "Tema", "C1", "C2", "C3", "C5", "C6", "Estatus", "Fecha de aprobación",
    "Diferencia de días entre fecha de presentación y aprobación", "Año",
    "Contenido", "Comentario", "F", "C", "A",
]

# Encabezados que se muestran distinto a la clave del JSON.
# "*" aplica a todas las hojas; el resto, solo a la hoja indicada.
HEADER_ALIASES = {
    "*": {"Número": "No"},
    "Puntos de acuerdo": {"Comentario": "Comentarios"},
}

# Columnas con fecha (además de cualquier clave que empiece con "fecha").
DATE_COLUMNS = ["FechaPres", "Fecha de aprobación"]
DATE_PREFIXES = ["fecha"]
TIME_PREFIXES = ["hora"]  # columnas de hora: se convierten a hora real de Excel
DATE_FORMAT = "dd/mm/yy"
DATETIME_FORMAT = "dd/mm/yy hh:mm"
TIME_FORMAT = "hh:mm"

# Columnas que se convierten a número cuando el texto parece un número.
# Si no lo parece (por ejemplo "AG") se conserva el texto.
NUMERIC_COLUMNS = ["Número", "Tema", "F", "C", "A"]

# Valores que se tratan como vacío ("N/A" NO está aquí: se conserva tal cual).
NULL_STRINGS = {"", "null", "none", "nan"}

# Columnas derivadas: se escriben como FÓRMULA de Excel = fin - inicio.
# La columna se ubica donde aparezca su clave en el JSON; si no existe, se
# inserta justo después de la última de las dos columnas de origen.
DERIVED_COLUMNS = [
    {
        "key": "Diferencia de días entre fecha de presentación y aprobación",
        "start": "FechaPres",
        "end": "Fecha de aprobación",
    },
]

# Relleno de fila completa según el valor de una columna.
ROW_FILL_RULES = [
    {"column": "Estatus", "values": ["Aprobada"], "color": "00FF00"},
]

# Encabezados: bloque con relleno azul (de la primera a la última clave) y
# fuente usada para los encabezados que quedan después de ese bloque.
HEADER_FILL_FROM = "FechaPres"
HEADER_FILL_TO = "Diferencia de días entre fecha de presentación y aprobación"
HEADER_FILL_COLOR = "9CC2E5"
HEADER_FONT = ("Calibri", 11)
HEADER_FONT_AFTER_BLOCK = ("Arial", 10)
BODY_FONT = ("Arial", 10)
TEXT_COLOR = "FF000000"  # negro explícito (el "automático" se ve blanco sobre rellenos fuertes)

# Anchos de columna (por encabezado mostrado). El resto usa el ancho por defecto.
COLUMN_WIDTHS = {
    "Iniciativas": {"No": 4.0, "Tipo": 8.25, "C3": 8.25, "C5": 6.5, "C6": 7.0},
    "Puntos de acuerdo": {"No": 4.13, "C3": 6.88, "C5": 5.38, "C6": 6.0},
}

# Hoja de catálogo de temas (fija, no depende de los JSON).
TEMAS_SHEET = "Hoja 3"
TEMAS_HEADER = "Temas"
TEMAS = [
    (1, "Economía, Comercio y Competitividad"),
    (2, "Pobreza y Política Social"),
    (3, "Seguridad Pública, Protección Civil y Narcotráfico"),
    (4, "Partidos Políticos y Elecciones"),
    (5, "Medio Ambiente"),
    (6, "Salud"),
    (7, "Impuestos, Finanzas Públicas, Patrimonio estatal y municipal"),
    (8, "Educación y Cultura"),
    (9, "Migración y fronteras"),
    (10, "Transparencia y Rendición de Cuentas"),
    (11, "Infraestructura, Movilidad y Transporte"),
    (12, "Agricultura, Ganadería y Pesca"),
    (13, "Medios de Comunicación"),
    (14, "Justicia y Estado de Derecho"),
    (15, "Telecomunicaciones"),
    (16, "Trabajo y Previsión Social"),
    (17, "Política"),
    (18, "Administración Pública"),
    (19, "Internos del Congreso"),
    (20, "Justicia y Estado de Derecho en temas de Mujeres"),
]
TEMA_COLUMN = "Tema"  # columna cuyos valores se validan contra el catálogo

MAX_EXAMPLES = 8  # ejemplos mostrados por cada tipo de aviso

# Formatos de fecha aceptados (día primero). Se prueban en este orden.
DATE_PATTERNS = [
    "%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y",
    "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d",
    "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%y %H:%M",
    "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
]
TIME_PATTERNS = ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"]


# =============================================================================
# UTILIDADES
# =============================================================================
class Avisos:
    """Acumula avisos por categoría para mostrarlos al final."""

    def __init__(self):
        self.items: "OrderedDict[str, list[str]]" = OrderedDict()

    def add(self, categoria: str, detalle: str = ""):
        self.items.setdefault(categoria, []).append(detalle)

    def imprimir(self):
        print("\n" + "=" * 70)
        if not self.items:
            print("AVISOS: ninguno. Los datos cuadran.")
            return
        print("AVISOS")
        print("=" * 70)
        for categoria, detalles in self.items.items():
            print(f"\n- {categoria}: {len(detalles)}")
            for d in [x for x in detalles if x][:MAX_EXAMPLES]:
                print(f"    · {d}")
            restantes = len([x for x in detalles if x]) - MAX_EXAMPLES
            if restantes > 0:
                print(f"    · ... y {restantes} más")


def norm(texto) -> str:
    """Normaliza un nombre para compararlo (sin acentos, espacios ni mayúsculas)."""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip().casefold()


def es_nulo(v) -> bool:
    if v is None:
        return True
    return isinstance(v, str) and v.strip().casefold() in NULL_STRINGS


def limpiar_texto(v: str) -> str:
    return ILLEGAL_CHARACTERS_RE.sub("", v).strip()


def parse_fecha(v):
    """Devuelve datetime o None si no se puede interpretar."""
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 <= v <= 80000:  # número de serie de Excel
            return datetime(1899, 12, 30) + timedelta(days=float(v))
        return None
    if isinstance(v, str):
        s = v.strip()
        for patron in DATE_PATTERNS:
            try:
                return datetime.strptime(s, patron)
            except ValueError:
                continue
    return None


def parse_hora(v):
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    if isinstance(v, str):
        for patron in TIME_PATTERNS:
            try:
                return datetime.strptime(v.strip(), patron).time()
            except ValueError:
                continue
    return None


def parse_numero(v):
    """Convierte texto numérico a número; si no lo es, devuelve el valor igual."""
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d+[.,]\d+", s):
            return float(s.replace(",", "."))
    return v


def nombre_hoja_valido(nombre: str, usados: set) -> str:
    base = re.sub(r"[\[\]\*\?/\\:]", "-", str(nombre)).strip()[:31] or "Hoja"
    candidato, n = base, 2
    while candidato.casefold() in usados:
        sufijo = f" ({n})"
        candidato = base[: 31 - len(sufijo)] + sufijo
        n += 1
    return candidato


# =============================================================================
# LECTURA
# =============================================================================
def extraer_registros(data, tipo_por_defecto=None):
    """Acepta lista de objetos, o dict cuyas listas se toman como registros
    (si el registro no trae Tipo, se usa la clave del dict como Tipo)."""
    if isinstance(data, list):
        return [(r, tipo_por_defecto) for r in data]
    if isinstance(data, dict):
        salida = []
        for k, v in data.items():
            if isinstance(v, list):
                salida.extend(extraer_registros(v, k))
            elif isinstance(v, dict):
                salida.extend(extraer_registros(v, tipo_por_defecto))
        return salida
    return []


def leer_jsons(rutas, avisos: Avisos):
    registros = []
    for ruta in rutas:
        if not ruta.exists():
            avisos.add("Archivo JSON no encontrado (se omitió)", str(ruta))
            continue
        try:
            with open(ruta, encoding="utf-8-sig") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            avisos.add("JSON mal formado (se omitió)", f"{ruta.name}: {e}")
            continue
        for r, tipo_def in extraer_registros(data):
            if not isinstance(r, dict):
                avisos.add("Elemento que no es un objeto (se omitió)", f"{ruta.name}: {str(r)[:60]}")
                continue
            limpio = {str(k).strip(): v for k, v in r.items()}
            if tipo_def and es_nulo(limpio.get(TYPE_FIELD)):
                limpio[TYPE_FIELD] = tipo_def
            registros.append(limpio)
        print(f"Leído {ruta.name}")
    return registros


# =============================================================================
# CONSTRUCCIÓN DEL LIBRO
# =============================================================================
def construir_columnas(registros):
    """Columnas = unión de claves en orden de aparición en el JSON."""
    claves = []
    vistas = set()
    for r in registros:
        for k in r:
            if norm(k) not in vistas:
                vistas.add(norm(k))
                claves.append(k)
    # Insertar columnas derivadas que no vengan en el JSON
    for d in DERIVED_COLUMNS:
        if norm(d["key"]) in vistas:
            continue
        pos = [i for i, k in enumerate(claves) if norm(k) in (norm(d["start"]), norm(d["end"]))]
        if len(pos) == 2:
            claves.insert(max(pos) + 1, d["key"])
            vistas.add(norm(d["key"]))
    return claves


def encabezado(clave: str, hoja: str) -> str:
    for ambito in ("*", hoja):
        for k, v in HEADER_ALIASES.get(ambito, {}).items():
            if norm(k) == norm(clave):
                clave = v
    return clave


def tipo_de_columna(clave: str) -> str:
    n = norm(clave)
    if any(norm(d["key"]) == n for d in DERIVED_COLUMNS):
        return "derivada"
    if n in {norm(c) for c in DATE_COLUMNS} or any(n.startswith(p) for p in DATE_PREFIXES):
        return "fecha"
    if any(n.startswith(p) for p in TIME_PREFIXES):
        return "hora"
    if n in {norm(c) for c in NUMERIC_COLUMNS}:
        return "numero"
    return "texto"


def escribir_hoja(ws, nombre_hoja, claves, filas, avisos: Avisos):
    tipos = {k: tipo_de_columna(k) for k in claves}
    letra = {norm(k): get_column_letter(i + 1) for i, k in enumerate(claves)}
    ultima_letra = get_column_letter(max(len(claves), 1))

    # --- Encabezados ---
    i_ini = next((i for i, k in enumerate(claves) if norm(k) == norm(HEADER_FILL_FROM)), None)
    i_fin = next((i for i, k in enumerate(claves) if norm(k) == norm(HEADER_FILL_TO)), None)
    relleno_azul = PatternFill("solid", fgColor="FF" + HEADER_FILL_COLOR)
    borde = Side(style="thin")
    for i, k in enumerate(claves):
        c = ws.cell(1, i + 1, encabezado(k, nombre_hoja))
        despues = i_fin is not None and i > i_fin
        nombre, tam = HEADER_FONT_AFTER_BLOCK if despues else HEADER_FONT
        c.font = Font(name=nombre, size=tam, bold=True, color=TEXT_COLOR)
        c.alignment = Alignment(vertical="top")
        if i_ini is not None and i_fin is not None and i_ini <= i <= i_fin:
            c.fill = relleno_azul
        if i == 0:
            c.border = Border(left=borde, right=borde, top=borde, bottom=borde)

    # --- Reglas de relleno ---
    reglas = [
        (norm(r["column"]), {norm(v) for v in r["values"]}, PatternFill("solid", fgColor="FF" + r["color"]))
        for r in ROW_FILL_RULES
    ]
    fuente = Font(name=BODY_FONT[0], size=BODY_FONT[1], color=TEXT_COLOR)

    # --- Datos ---
    for n, reg in enumerate(filas):
        fila = n + 2
        reg_norm = {norm(k): v for k, v in reg.items()}
        fill_fila = None
        for col_n, valores, pf in reglas:
            v = reg_norm.get(col_n)
            if not es_nulo(v) and norm(v) in valores:
                fill_fila = pf

        for i, k in enumerate(claves):
            celda = ws.cell(fila, i + 1)
            celda.font = fuente
            if fill_fila is not None:
                celda.fill = fill_fila
            tipo = tipos[k]

            if tipo == "derivada":
                d = next(x for x in DERIVED_COLUMNS if norm(x["key"]) == norm(k))
                fin = f"{letra[norm(d['end'])]}{fila}"
                ini = f"{letra[norm(d['start'])]}{fila}"
                celda.value = f'=IF(AND(ISNUMBER({fin}),ISNUMBER({ini})),{fin}-{ini},"")'
                continue

            valor = reg_norm.get(norm(k))
            if es_nulo(valor):
                continue

            if tipo == "fecha":
                dt = parse_fecha(valor)
                if dt is None:
                    celda.value = limpiar_texto(valor) if isinstance(valor, str) else valor
                    avisos.add(
                        "Fecha no interpretable (se conservó el valor original)",
                        f"hoja '{nombre_hoja}', fila {fila}, columna '{encabezado(k, nombre_hoja)}': {valor!r}",
                    )
                else:
                    celda.value = dt
                    celda.number_format = DATETIME_FORMAT if (dt.hour or dt.minute or dt.second) else DATE_FORMAT
                    celda.alignment = Alignment(horizontal="right")
            elif tipo == "hora":
                t = parse_hora(valor)
                if t is None:
                    celda.value = limpiar_texto(valor) if isinstance(valor, str) else valor
                    avisos.add(
                        "Hora no interpretable (se conservó el valor original)",
                        f"hoja '{nombre_hoja}', fila {fila}, columna '{encabezado(k, nombre_hoja)}': {valor!r}",
                    )
                else:
                    celda.value = t
                    celda.number_format = TIME_FORMAT
            else:
                if tipo == "numero":
                    valor = parse_numero(valor)
                if isinstance(valor, str):
                    valor = limpiar_texto(valor)
                if isinstance(valor, (dict, list)):
                    valor = json.dumps(valor, ensure_ascii=False)
                celda.value = valor
                if isinstance(valor, str):
                    celda.data_type = "s"  # un texto que empiece con "=" no es fórmula

    # --- Hoja: panel, filtro, anchos ---
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{ultima_letra}{len(filas) + 1}"
    anchos = COLUMN_WIDTHS.get(nombre_hoja, {})
    for i, k in enumerate(claves):
        h = encabezado(k, nombre_hoja)
        if h in anchos:
            ws.column_dimensions[get_column_letter(i + 1)].width = anchos[h]


def escribir_catalogo_temas(wb):
    ws = wb.create_sheet(TEMAS_SHEET)
    ws.cell(1, 2, TEMAS_HEADER).font = Font(name="Helvetica Neue", size=11, color=TEXT_COLOR)
    for i, (num, nombre) in enumerate(TEMAS):
        fila = i + 2
        # Como en el original: las primeras filas en Helvetica Neue y las dos últimas en Arial
        f = (Font(name="Helvetica Neue", size=11, color=TEXT_COLOR) if i < len(TEMAS) - 2
             else Font(name="Arial", size=10, color=TEXT_COLOR))
        ws.cell(fila, 1, num).font = f
        ws.cell(fila, 2, nombre).font = f
    ws.column_dimensions["B"].width = 55


# =============================================================================
# VALIDACIONES (avisos)
# =============================================================================
def validar(registros, claves, nombres_hoja, avisos: Avisos):
    conocidas = {norm(c) for c in KNOWN_COLUMNS}
    presentes = {norm(c) for c in claves}
    nuevas = [c for c in claves if norm(c) not in conocidas]
    faltan = [c for c in KNOWN_COLUMNS if norm(c) not in presentes]
    if nuevas:
        avisos.add("Columnas nuevas respecto al Excel original (se agregaron al final)", ", ".join(nuevas))
    if faltan:
        avisos.add("Columnas del Excel original que no vienen en el JSON", ", ".join(faltan))

    catalogo = {n for n, _ in TEMAS}
    est = next((c for c in claves if norm(c) == norm("Estatus")), None)
    aprobadas = set()
    for r in ROW_FILL_RULES:
        aprobadas |= {norm(v) for v in r["values"]}

    for r in registros:
        hoja = nombres_hoja.get(id(r), "?")
        ref = f"hoja '{hoja}', {r.get('Descripción', '')[:50]!r}" if isinstance(r.get("Descripción"), str) else f"hoja '{hoja}'"
        # Tema fuera del catálogo
        t = parse_numero(r.get(TEMA_COLUMN))
        if not es_nulo(t) and t not in catalogo:
            avisos.add(f"Valores de '{TEMA_COLUMN}' que no están en el catálogo de temas", f"{t!r} ({ref})")
        # Estatus vs fecha de aprobación; negativos
        for d in DERIVED_COLUMNS:
            rn = {norm(k): v for k, v in r.items()}
            fecha_fin = rn.get(norm(d["end"]))
            fecha_ini = rn.get(norm(d["start"]))
            estatus = rn.get(norm("Estatus"))
            es_aprob = not es_nulo(estatus) and norm(estatus) in aprobadas
            if es_aprob and es_nulo(fecha_fin):
                avisos.add("Registros aprobados sin fecha de aprobación", ref)
            if not es_aprob and not es_nulo(fecha_fin):
                avisos.add("Registros no aprobados con fecha de aprobación", ref)
            a, b = parse_fecha(fecha_ini), parse_fecha(fecha_fin)
            if a and b:
                dias = (b - a).days
                if dias < 0:
                    avisos.add("Fecha de aprobación anterior a la de presentación", f"{ref} ({dias} días)")
                dato_json = rn.get(norm(d["key"]))
                if isinstance(dato_json, (int, float)) and int(dato_json) != dias:
                    avisos.add(
                        "Diferencia de días del JSON distinta a la calculada por la fórmula",
                        f"{ref}: JSON={dato_json}, fórmula={dias}",
                    )


# =============================================================================
# PRINCIPAL
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Genera el Excel Iniciativas y PA desde JSON.")
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help=f"carpeta de los JSON (por defecto {DEFAULT_DATA_DIR})")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help=f"carpeta de salida (por defecto {DEFAULT_OUTPUT_DIR})")
    ap.add_argument("--json", nargs="+", default=None, help="nombres de los JSON dentro de data-dir (por defecto los de JSON_FILES)")
    ap.add_argument("--output-name", default=OUTPUT_NAME, help=f"nombre del Excel (por defecto {OUTPUT_NAME})")
    args = ap.parse_args()

    avisos = Avisos()
    rutas = [args.data_dir / n for n in (args.json or JSON_FILES)]
    registros = leer_jsons(rutas, avisos)
    if not registros:
        avisos.imprimir()
        print("\nNo hay registros para escribir. No se generó ningún archivo.")
        return 1

    claves = construir_columnas(registros)

    # Reparto de registros por hoja (según Tipo), conservando el orden del JSON
    mapa = {norm(t): h for t, h in SHEETS}
    hojas: "OrderedDict[str, list]" = OrderedDict((h, []) for _, h in SHEETS)
    usados = {h.casefold() for h in hojas} | {TEMAS_SHEET.casefold()}
    nombres_hoja = {}
    for r in registros:
        t = r.get(TYPE_FIELD)
        if es_nulo(t):
            destino = NO_TYPE_SHEET
            avisos.add("Registros sin valor en el campo de tipo (van a la hoja 'Sin tipo')", str(r.get("Descripción", ""))[:60])
        elif norm(t) in mapa:
            destino = mapa[norm(t)]
        else:
            destino = nombre_hoja_valido(str(t), usados)
            usados.add(destino.casefold())
            mapa[norm(t)] = destino
            avisos.add("Tipo no previsto (se creó una hoja nueva)", f"{t!r} -> hoja '{destino}'")
        hojas.setdefault(destino, []).append(r)
        nombres_hoja[id(r)] = destino
    if not hojas.get(NO_TYPE_SHEET):
        hojas.pop(NO_TYPE_SHEET, None)

    validar(registros, claves, nombres_hoja, avisos)

    wb = Workbook()
    wb.remove(wb.active)
    for nombre, filas in hojas.items():
        ws = wb.create_sheet(nombre)
        escribir_hoja(ws, nombre, claves, filas, avisos)
    escribir_catalogo_temas(wb)
    wb.calculation.fullCalcOnLoad = True  # Excel recalcula las fórmulas al abrir

    args.output_dir.mkdir(parents=True, exist_ok=True)
    salida = args.output_dir / args.output_name
    try:
        wb.save(salida)
    except PermissionError:
        print(
            f"\nNO SE PUDO GUARDAR: el archivo '{salida.name}' parece estar abierto en Excel "
            f"(o no hay permiso de escritura).\nCiérralo y vuelve a ejecutar el script.\n"
            f"Ruta: {salida}"
        )
        return 2

    print("\n" + "=" * 70)
    print(f"Archivo generado: {salida}")
    for nombre, filas in hojas.items():
        print(f"  - {nombre}: {len(filas)} filas, {len(claves)} columnas")
    print(f"  - {TEMAS_SHEET}: {len(TEMAS)} temas")
    avisos.imprimir()
    return 0


if __name__ == "__main__":
    sys.exit(main())