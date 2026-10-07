#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compara Votaciones.xlsx contra unidad_participacion.json (solo lectura) y marca
las discrepancias directamente en el Excel (color + comentario de 2 líneas).

Campos comparados (JSON -> fila de totales del Excel):
    "A favor"      -> "Votos a favor"
    "Contra"       -> "Votos en contra"
    "Abstenciones" -> "Abstención"
    "Ausentes"     -> "Ausencia"
    "Total"        -> "Sumatoria total"

Colores:
    Naranja : ambos tienen dato pero es DISTINTO.
    Amarillo: el Excel tiene dato y el JSON no lo lista.
    Azul    : el JSON tiene dato y el Excel está vacío o con "-".

Emparejamiento de columnas del Excel con registros del JSON:
    1) Misma fecha (obligatorio).
    2) Dentro de la fecha, similitud de texto. El texto de cada columna del
       Excel se enriquece con la "descripcion" completa de votaciones.json
       (el JSON con el que se generó el Excel; la columna se identifica sin
       ambigüedad por fecha + número de votación + sesión). Ese texto se
       compara contra "Título" y "Contenido" de la referencia y se toma la
       mayor similitud (el orden NO es confiable: los
       dos archivos lo traen permutado). Asignación uno a uno, de mayor a
       menor similitud. Los empates se resuelven con: coinciden A favor/Contra/
       Abstenciones y cercanía de posición (solo desempate, nunca decide solo).
    3) Umbral de título: --umbral-titulo (por defecto 0.60); si en esa fecha
       hay exactamente 1 columna y 1 registro, se usa --umbral-unico (0.25).
    Lo que no empareja se reporta en consola y NO se marca.

Uso:
    python comparar_votaciones.py [--excel RUTA] [--json RUTA]
                                  [--json-origen RUTA] [--salida RUTA]
"""
import argparse
import difflib
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill
from openpyxl.utils import column_index_from_string

# ── Rutas por defecto (relativas al script, como script2.py) ────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXCEL = os.path.join(BASE, "..", "outputs", "Votaciones.xlsx")
DEFAULT_JSON = os.path.join(BASE, "..", "data", "unidad_participacion.json")
DEFAULT_JSON_ORIGEN = os.path.join(BASE, "..", "data", "votaciones.json")
BAJA_CONFIANZA = 0.70   # similitud por debajo de la cual se avisa en consola

# ── Configuración ────────────────────────────────────────────────────────────
AUTOR = "Comparador Gaceta"

# (campo del JSON, etiquetas posibles de la fila en el Excel, ya normalizadas)
CAMPOS = [
    ("A favor",      ["votos a favor"]),
    ("Contra",       ["votos en contra"]),
    ("Abstenciones", ["abstencion", "abstenciones"]),
    ("Ausentes",     ["ausencia", "ausencias", "ausentes"]),
    ("Total",        ["sumatoria total", "total"]),
]
JSON_FECHA = "Fecha"
JSON_TITULO = "Título"
# Campos de texto del JSON contra los que se compara el "tema" del Excel
# (el Excel suele traer la parte resolutiva, que en el JSON está en "Contenido").
JSON_TEXTOS = ["Título", "Contenido"]
JSON_ORDEN = "Folio legislatura"

COLORES = {
    "distinto": "FFA500",   # naranja
    "amarillo": "FFFF00",   # amarillo
    "azul":     "9BC2E6",   # azul
}
NOMBRE_TIPO = {
    "distinto": "Naranja",
    "amarillo": "Amarillo",
    "azul":     "Azul",
}
TEXTO_TIPO = {
    "distinto": "Valor distinto",
    "amarillo": "Excel tiene dato; la Gaceta no lo lista",
    "azul":     "Gaceta tiene dato; Excel vacío",
}


# ── Utilidades de texto / fechas ─────────────────────────────────────────────
def sin_acentos(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c))


def norm_etiqueta(s):
    """Normaliza etiquetas: sin acentos, minúsculas, espacios colapsados."""
    s = sin_acentos(s).lower()
    return re.sub(r"\s+", " ", s).strip()


_STOP = set("de la el los las del por que se en y a al con para un una su sus "
            "sobre lo como es o".split())


def norm_titulo(s):
    s = sin_acentos(s or "").lower()
    s = re.sub(r"^\s*[a-z]{0,2}\d+[\.\)]\s+", "", s)   # prefijos tipo "P42. "
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sim_titulo(a, b):
    a, b = norm_titulo(a), norm_titulo(b)
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta = {t for t in a.split() if t not in _STOP and len(t) > 2}
    tb = {t for t in b.split() if t not in _STOP and len(t) > 2}
    if not ta or not tb:
        return ratio
    inter = len(ta & tb)
    jaccard = inter / len(ta | tb)
    contenido = 0.9 * inter / min(len(ta), len(tb))   # tolera títulos truncados
    return max(ratio, jaccard, contenido)


def parse_fecha(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                pass
    return None


def fmt_fecha(d):
    return d.strftime("%d/%m/%Y") if d else "(sin fecha)"


def es_vacio(v):
    return v is None or (isinstance(v, str) and v.strip() in ("", "-"))


def a_numero(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt_valor(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return "(no aparece)"
    n = a_numero(v)
    if n is not None and n == int(n):
        return str(int(n))
    return str(v)


# ── Lectura de celdas con fórmulas ───────────────────────────────────────────
# Votaciones.xlsx lo genera openpyxl: las fórmulas (COUNTIF / SUM) NO traen
# valor calculado guardado, así que load_workbook() entrega el texto
# "=COUNTIF(...)" y data_only=True entregaría None. Se evalúan aquí mismo
# (solo estos dos tipos de fórmula) para no depender de abrir el archivo en Excel.
_RE_COUNTIF = re.compile(r"^=COUNTIF\(\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+),\s*(-?\d+)\)$", re.I)
_RE_SUM = re.compile(r"^=SUM\(\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)\)$", re.I)


def valor_celda(ws, fila, col, _prof=0):
    """Valor de la celda; si es fórmula COUNTIF/SUM la calcula."""
    v = ws.cell(fila, col).value
    if not (isinstance(v, str) and v.startswith("=")) or _prof > 3:
        return v
    m = _RE_COUNTIF.match(v.strip())
    if m:
        c1, f1, c2, f2, codigo = m.groups()
        c1, c2 = column_index_from_string(c1.upper()), column_index_from_string(c2.upper())
        n = 0
        for c in range(c1, c2 + 1):
            for f in range(int(f1), int(f2) + 1):
                x = ws.cell(f, c).value
                if a_numero(x) == float(codigo):
                    n += 1
        return n
    m = _RE_SUM.match(v.strip())
    if m:
        c1, f1, c2, f2 = m.groups()
        c1, c2 = column_index_from_string(c1.upper()), column_index_from_string(c2.upper())
        total = 0
        for c in range(c1, c2 + 1):
            for f in range(int(f1), int(f2) + 1):
                x = valor_celda(ws, f, c, _prof + 1)
                n = a_numero(x)
                if n is not None:
                    total += n
        return total
    print(f"[aviso] Fórmula no soportada en {ws.cell(fila, col).coordinate}: {v}")
    return None


# ── Detección dinámica de la estructura del Excel ────────────────────────────
def etiquetas_fila(ws, r, cols_etiqueta=2):
    """Etiquetas (normalizadas) de las primeras columnas de la fila r."""
    out = []
    for c in range(1, cols_etiqueta + 1):
        v = ws.cell(r, c).value
        if isinstance(v, str) and v.strip():
            out.append(norm_etiqueta(v))
    return out


def detectar_bloques(ws):
    """
    Un bloque = una fila cuya etiqueta es 'fecha'. De cada bloque se obtiene:
      fila_fecha, fila_tema (opcional), columnas (con fecha) y filas de totales.
    """
    filas_fecha = [r for r in range(1, ws.max_row + 1)
                   if "fecha" in etiquetas_fila(ws, r)]
    bloques = []
    for i, f in enumerate(filas_fecha):
        siguiente = filas_fecha[i + 1] if i + 1 < len(filas_fecha) else None
        limite = (siguiente - 1) if siguiente else ws.max_row

        # fila de título/tema: la más cercana por debajo de 'fecha'
        fila_tema = None
        for r in range(f, min(f + 12, limite) + 1):
            if "tema" in etiquetas_fila(ws, r):
                fila_tema = r
                break

        # filas 'num' y 'sesión' (identifican la votación en el JSON de origen)
        fila_num = fila_sesion = None
        for r in range(max(1, f - 3), min(f + 4, limite) + 1):
            et = etiquetas_fila(ws, r)
            if fila_num is None and "num" in et:
                fila_num = r
            if fila_sesion is None and "sesion" in et:
                fila_sesion = r

        # filas de totales: etiqueta exacta (normalizada) debajo de 'fecha'
        filas_campo = {}
        for campo, posibles in CAMPOS:
            for r in range(f + 1, limite + 1):
                if any(e in posibles for e in etiquetas_fila(ws, r)):
                    filas_campo[campo] = r
                    break

        # columnas de votación: las que tienen fecha en la fila 'fecha'
        columnas = []
        for c in range(2, ws.max_column + 1):
            fv = parse_fecha(ws.cell(f, c).value)
            if fv is not None:
                columnas.append(c)

        bloques.append({
            "hoja": ws.title, "ws": ws, "fila_fecha": f, "fila_tema": fila_tema,
            "fila_num": fila_num, "fila_sesion": fila_sesion,
            "filas_campo": filas_campo, "columnas": columnas,
        })
    return bloques


# ── JSON de origen (el que generó el Excel): solo lectura ────────────────────
def cargar_origen(ruta):
    """
    Indexa votaciones.json por (fecha, numero_votacion, sesión normalizada).
    Devuelve {} si no se puede leer (el script sigue solo con el 'tema').
    """
    if not ruta or not os.path.exists(ruta):
        print(f"[aviso] No se encontró el JSON de origen ({ruta}); "
              "se usa solo el 'tema' del Excel para emparejar.")
        return {}
    with open(ruta, "r", encoding="utf-8") as f:
        regs = json.load(f)["registros"]
    idx = defaultdict(list)
    for rg in regs:
        clave = (parse_fecha(rg.get("fecha")), a_numero(rg.get("numero_votacion")),
                 norm_etiqueta(rg.get("sesion", "")))
        idx[clave].append(rg)
    return idx


def textos_columna(ws, bloque, c, origen):
    """
    Textos con los que se identifica una columna: el 'tema' del Excel y, si la
    columna se verifica contra votaciones.json, su 'descripcion' completa
    (y sus partes separadas por tabulador).
    Devuelve (lista_de_textos, verificada).
    """
    textos = []
    if bloque["fila_tema"]:
        t = ws.cell(bloque["fila_tema"], c).value
        if isinstance(t, str) and t.strip():
            textos.append(t)
    if not origen or not bloque["fila_num"] or not bloque["fila_sesion"]:
        return textos, False
    clave = (parse_fecha(ws.cell(bloque["fila_fecha"], c).value),
             a_numero(ws.cell(bloque["fila_num"], c).value),
             norm_etiqueta(ws.cell(bloque["fila_sesion"], c).value or ""))
    cands = origen.get(clave, [])
    if not cands:
        return textos, False
    if len(cands) > 1 and textos:   # desempate por el inicio del 'tema'
        ini = norm_titulo(textos[0])[:40]
        mejores = [r for r in cands if norm_titulo(r.get("tema", ""))[:40] == ini]
        cands = mejores or cands
    desc = cands[0].get("descripcion") or ""
    if desc:
        textos.append(desc)
        textos += [p for p in desc.split("\t") if p.strip()]
    return textos, True


# ── Emparejamiento ───────────────────────────────────────────────────────────
def emparejar_dia(cols, regs, ws, bloque, umbral_titulo, umbral_unico, textos):
    """
    cols: lista de columnas Excel (en orden) de una fecha.
    regs: lista de registros JSON (en orden) de la misma fecha.
    Devuelve lista de (col, reg, similitud).
    """
    fila_tema = bloque["fila_tema"]
    fc = bloque["filas_campo"]
    umbral = umbral_unico if (len(cols) == 1 and len(regs) == 1) else umbral_titulo

    candidatos = []
    for i, c in enumerate(cols):
        textos_x = textos.get(c, [])
        conteo_x = tuple(a_numero(valor_celda(ws, fc[k], c)) if k in fc else None
                         for k in ("A favor", "Contra", "Abstenciones"))
        for j, rg in enumerate(regs):
            if textos_x:
                s = max(sim_titulo(t, rg.get(k)) for t in textos_x for k in JSON_TEXTOS)
            else:   # sin fila de título: solo posición
                s = 1.0 if i == j else 0.0
            conteo_r = tuple(a_numero(rg.get(k))
                             for k in ("A favor", "Contra", "Abstenciones"))
            bonus = 0.02 if conteo_x == conteo_r else 0.0
            candidatos.append((s + bonus - 0.002 * abs(i - j), s, i, j))

    candidatos.sort(reverse=True)
    usados_i, usados_j, pares = set(), set(), []
    for _, s, i, j in candidatos:
        if i in usados_i or j in usados_j or s < umbral:
            continue
        usados_i.add(i)
        usados_j.add(j)
        pares.append((cols[i], regs[j], s))
    return pares


# ── Comparación y marcado ────────────────────────────────────────────────────
def clasificar(val_excel, val_json):
    """Devuelve 'distinto' / 'amarillo' / 'azul' / None."""
    json_vacio = val_json is None or (isinstance(val_json, str) and not val_json.strip())
    if json_vacio:
        return None if es_vacio(val_excel) else "amarillo"
    if es_vacio(val_excel):
        return "azul"
    ne, nj = a_numero(val_excel), a_numero(val_json)
    if ne is not None and nj is not None:
        return None if ne == nj else "distinto"
    return None if norm_etiqueta(val_excel) == norm_etiqueta(val_json) else "distinto"


def marcar(celda, tipo, val_json):
    celda.fill = PatternFill(start_color=COLORES[tipo], end_color=COLORES[tipo],
                             fill_type="solid")
    texto = f"{TEXTO_TIPO[tipo]}\nGaceta: {fmt_valor(val_json)}"
    com = Comment(texto, AUTOR)
    com.width, com.height = 230, 60
    celda.comment = com


def limpiar_marcas_previas(ws):
    """Si se vuelve a correr sobre el mismo archivo, quita lo marcado antes."""
    for fila in ws.iter_rows():
        for c in fila:
            if c.comment is not None and c.comment.author == AUTOR:
                c.comment = None
                c.fill = PatternFill(fill_type=None)


# ── Programa principal ───────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--excel", default=DEFAULT_EXCEL, help="Excel a revisar")
    ap.add_argument("--json", default=DEFAULT_JSON, help="JSON de referencia")
    ap.add_argument("--json-origen", default=DEFAULT_JSON_ORIGEN,
                    help="votaciones.json con el que se generó el Excel (solo lectura)")
    ap.add_argument("--salida", default=None,
                    help="Excel de salida (por defecto sobrescribe --excel)")
    ap.add_argument("--umbral-titulo", type=float, default=0.60)
    ap.add_argument("--umbral-unico", type=float, default=0.25)
    args = ap.parse_args()
    salida = args.salida or args.excel

    # JSON de referencia: SOLO lectura
    with open(args.json, "r", encoding="utf-8") as f:
        ref = json.load(f)["registros"]
    ref_por_fecha = defaultdict(list)
    for idx, rg in enumerate(ref):
        rg["_idx"] = idx
        ref_por_fecha[parse_fecha(rg.get(JSON_FECHA))].append(rg)
    for lst in ref_por_fecha.values():
        lst.sort(key=lambda r: (r.get(JSON_ORDEN) is None, r.get(JSON_ORDEN) or 0, r["_idx"]))

    origen = cargar_origen(args.json_origen)
    cols_verificadas = 0
    cols_no_verificadas = 0

    wb = load_workbook(args.excel)

    pares_total = 0
    pares_baja_conf = 0                       # emparejados con similitud < BAJA_CONFIANZA
    celdas_comparadas = 0
    disc_tipo = defaultdict(int)
    disc_campo = defaultdict(lambda: defaultdict(int))
    ref_emparejados = set()
    cols_sin_pareja = defaultdict(list)       # fecha -> [(hoja, col)]
    dias_conteo_distinto = []                 # info por día
    fechas_excel = set()
    ejemplos = []

    for ws in wb.worksheets:
        limpiar_marcas_previas(ws)
        bloques = detectar_bloques(ws)
        if not bloques:
            print(f"[aviso] Hoja '{ws.title}': no se encontró fila 'fecha'; se omite.")
            continue
        for b in bloques:
            faltan = [c for c, _ in CAMPOS if c not in b["filas_campo"]]
            print(f"Hoja '{ws.title}' | fila fecha={b['fila_fecha']} | "
                  f"fila tema={b['fila_tema']} | columnas={len(b['columnas'])} "
                  f"| filas de totales={b['filas_campo']}")
            if faltan:
                print(f"  [aviso] No se encontró fila para: {', '.join(faltan)}")

            cols_por_fecha = defaultdict(list)
            for c in b["columnas"]:
                cols_por_fecha[parse_fecha(ws.cell(b["fila_fecha"], c).value)].append(c)
            fechas_excel.update(cols_por_fecha)

            for fecha, cols in sorted(cols_por_fecha.items(), key=lambda x: x[0]):
                regs = ref_por_fecha.get(fecha, [])
                if not regs:
                    cols_sin_pareja[fecha] += [(ws.title, c) for c in cols]
                    continue
                textos = {}
                for c in cols:
                    textos[c], ok = textos_columna(ws, b, c, origen)
                    if ok:
                        cols_verificadas += 1
                    else:
                        cols_no_verificadas += 1
                pares = emparejar_dia(cols, regs, ws, b,
                                      args.umbral_titulo, args.umbral_unico, textos)
                emparejadas = {c for c, _, _ in pares}
                for c in cols:
                    if c not in emparejadas:
                        cols_sin_pareja[fecha].append((ws.title, c))
                for _, rg, _ in pares:
                    ref_emparejados.add(rg["_idx"])
                if len(cols) != len(regs):
                    dias_conteo_distinto.append(
                        (fecha, len(cols), len(regs), len(pares)))

                for c, rg, s in pares:
                    pares_total += 1
                    if s < BAJA_CONFIANZA:
                        pares_baja_conf += 1
                    for campo, _ in CAMPOS:
                        fila = b["filas_campo"].get(campo)
                        if fila is None:
                            continue
                        celda = ws.cell(fila, c)
                        val_json = rg.get(campo)
                        celdas_comparadas += 1
                        tipo = clasificar(valor_celda(ws, fila, c), val_json)
                        if tipo:
                            marcar(celda, tipo, val_json)
                            disc_tipo[tipo] += 1
                            disc_campo[campo][tipo] += 1
                            if len(ejemplos) < 400:
                                ejemplos.append((ws.title, celda.coordinate, campo,
                                                 tipo, celda.value, val_json,
                                                 fecha, round(s, 2)))

    os.makedirs(os.path.dirname(os.path.abspath(salida)), exist_ok=True)
    wb.save(salida)

    # ── Reporte en consola ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    print(f"Registros del JSON de referencia: {len(ref)}")
    print(f"Columnas del Excel emparejadas con un registro: {pares_total}")
    print(f"  (de ellas, con similitud de texto baja <{BAJA_CONFIANZA:.2f} y a revisar a ojo: {pares_baja_conf})")
    print(f"Columnas identificadas contra votaciones.json: {cols_verificadas} "
          f"(sin identificar, solo por 'tema': {cols_no_verificadas}; "
          "se cuentan solo las de fechas que existen en la referencia)")
    print(f"Celdas comparadas: {celdas_comparadas}")
    print("Discrepancias (celdas marcadas):")
    for t in ("distinto", "amarillo", "azul"):
        print(f"  {NOMBRE_TIPO[t]:<9} ({TEXTO_TIPO[t]}): {disc_tipo[t]}")
    print(f"  TOTAL: {sum(disc_tipo.values())}")
    print("Por campo:")
    for campo, _ in CAMPOS:
        d = disc_campo[campo]
        print(f"  {campo:<13} naranja={d['distinto']:<4} amarillo={d['amarillo']:<4} azul={d['azul']}")

    print("\nDías con distinto número de registros (Excel vs referencia):")
    if not dias_conteo_distinto:
        print("  (ninguno)")
    for fecha, nx, nr, np_ in sorted(dias_conteo_distinto):
        estado = "SI" if np_ == min(nx, nr) else ("PARCIAL" if np_ else "NO")
        print(f"  {fmt_fecha(fecha)}  Excel={nx:<3} Ref={nr:<3} emparejados={np_:<3} -> {estado}")

    sin_excel = [rg for rg in ref if rg["_idx"] not in ref_emparejados]
    print(f"\nRegistros del JSON sin columna en el Excel: {len(sin_excel)}")
    for rg in sorted(sin_excel, key=lambda r: (parse_fecha(r.get(JSON_FECHA)) or date.min, r["_idx"])):
        motivo = ("fecha no existe en el Excel"
                  if parse_fecha(rg.get(JSON_FECHA)) not in fechas_excel
                  else "sin título parecido ese día")
        print(f"  {rg.get(JSON_FECHA)} folio={rg.get(JSON_ORDEN)} "
              f"[{motivo}] {str(rg.get(JSON_TITULO))[:70]}")

    total_sin = sum(len(v) for v in cols_sin_pareja.values())
    print(f"\nColumnas del Excel sin registro en el JSON: {total_sin}")
    for fecha in sorted(cols_sin_pareja, key=lambda d: d or date.min):
        print(f"  {fmt_fecha(fecha)}: {len(cols_sin_pareja[fecha])} columna(s)")

    print("\nNombres sin equivalente: no aplica (la referencia solo trae totales "
          "por votación, no votos por persona).")

    print(f"\nArchivo guardado en: {os.path.abspath(salida)}")


if __name__ == "__main__":
    sys.exit(main())