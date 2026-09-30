#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador dinámico del Excel de Comisiones y Comités (Congreso de Puebla, LXII Legislatura).

Lee tres archivos JSON y produce un libro de Excel con el mismo formato del original
"Comisiones 2026-2027":

    comisiones_integrantes.json  ->  hoja "Integrantes"
    comisiones_sesiones.json     ->  hoja "Sesiones"
    comisiones_contenido.json    ->  hoja "Copia de Contenido trabajo"

Todo se calcula a partir de los datos en cada ejecución:
  * Integrantes: las columnas se toman de las claves del JSON (si aparece una nueva, aparece en el Excel).
  * Sesiones: los años, periodos y la cantidad de columnas por periodo/receso se ajustan al máximo
    de sesiones que haya en los datos. Los totales son fórmulas (COUNT / suma), no valores fijos.
  * Contenido: una fila por sesión; "Tiempo" es fórmula cuando hay hora de inicio y fin.

Uso:
    python generar_comisiones.py
    python generar_comisiones.py --carpeta ./datos --salida ./Comisiones.xlsx
    python generar_comisiones.py --integrantes a.json --sesiones b.json --contenido c.json

Requiere: openpyxl  (pip install openpyxl)
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, time
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as letra

# =====================================================================================
# CONFIGURACIÓN (lo único que normalmente hay que tocar)
# =====================================================================================
ARCHIVO_INTEGRANTES = "comisiones_integrantes.json"
ARCHIVO_SESIONES = "comisiones_sesiones.json"
ARCHIVO_CONTENIDO = "comisiones_contenido.json"
ARCHIVO_SALIDA = "Comisiones.xlsx"

HOJA_INTEGRANTES = "Integrantes"
HOJA_SESIONES = "Sesiones"
HOJA_CONTENIDO = "Copia de Contenido trabajo"

CLAVE_NOMBRE = "Nombre"           # clave que identifica a la comisión/comité en los 3 JSON
PREFIJO_COMITE = ""               # ponga "Comité: " para reproducir los nombres del original
ANIO_INICIO_POR_DEFECTO = 2026    # se usa si no se puede deducir de las fechas de instalación
COLUMNAS_COMISION_EXTRA = 3       # "Comisión 2..4" en Contenido (0 para quitarlas)
MIN_COLUMNAS_SESION = 1           # mínimo de columnas por periodo en Sesiones
MIN_COLUMNAS_RECESO = 2           # mínimo de columnas por receso en Sesiones (el original usa 2-3)
INCLUIR_ANIOS_SIN_DATOS = False   # True = crea bloques de año aunque no tengan ninguna fecha
ORDEN_SESIONES = "asc"            # "asc" = de la más antigua a la más reciente; "desc" = inverso

# =====================================================================================
# FORMATO (replica del original)
# =====================================================================================
ANCHO_COL_DEFECTO = 12.63
ALTO_FILA_DEFECTO = 15.75

FUENTE_BASE = Font(name="Arial", size=10, color="000000")
FUENTE_LINK = Font(name="Arial", size=10, color="0000FF", underline="single")
FUENTE_ENC = Font(name="Calibri", size=11, bold=True, color="000000")
FUENTE_ENC_PEQ = Font(name="Calibri", size=9, bold=True, color="000000")
FUENTE_TOTAL = Font(name="Calibri", size=11, bold=True, color="274E13")
FUENTE_TOTAL_ANIO = Font(name="Arial", size=10, bold=True, color="5B0F00")

LADO = Side(style="thin", color="000000")
BORDE = Border(left=LADO, right=LADO, top=LADO, bottom=LADO)
SIN_BORDE = Border()

# (color fila 1, color fila 2) por periodo; el receso usa siempre el mismo par
COLORES_PERIODO = {1: ("FCE5CD", "F7CAAC"), 2: ("FFF2CC", "FFE599"), 3: ("D9EAD3", "B6D7A8")}
COLORES_RECESO = ("CFE2F3", "3C78D8")

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
NOMBRE_MES = {1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio", 7: "julio",
              8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"}
ORDINAL = {1: "Primer", 2: "Segundo", 3: "Tercer", 4: "Cuarto", 5: "Quinto"}

# Calendario legislativo: (día, mes, desfase de año) de inicio y fin; el desfase se suma al año del ciclo.
CAL_PERIODO = {1: ((15, 9, 0), (15, 12, 0)), 2: ((15, 1, 1), (15, 3, 1)), 3: ((15, 5, 1), (15, 7, 1))}
CAL_RECESO = {1: ((16, 12, 0), (14, 1, 1)), 2: ((16, 3, 1), (14, 5, 1)), 3: ((16, 7, 1), (14, 9, 1))}


# =====================================================================================
# UTILIDADES DE LECTURA Y LIMPIEZA
# =====================================================================================
AVISOS = []


def avisar(msg):
    if msg not in AVISOS:
        AVISOS.append(msg)


def sin_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def clave(s):
    """Normaliza una clave/nombre para comparar: sin acentos, minúsculas, espacios colapsados."""
    return " ".join(sin_acentos(str(s)).casefold().split())


def norm_claves(d):
    """Devuelve el dict con claves sin espacios sobrantes y en NFC (los JSON traen 'sesiones_periodo_1 ')."""
    return {unicodedata.normalize("NFC", str(k)).strip(): v for k, v in d.items()}


def limpiar(v):
    """Recorta textos y convierte '', 'null' y 'none' en vacío."""
    if isinstance(v, str):
        v = v.strip()
        if v == "" or v.casefold() in ("null", "none"):
            return None
    return v


def cargar_json(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        sys.exit(f"ERROR: no se encontró el archivo {ruta}")
    with open(ruta, encoding="utf-8-sig") as f:
        datos = json.load(f)
    if isinstance(datos, dict):  # tolera {"datos": [...]}
        listas = [v for v in datos.values() if isinstance(v, list)]
        if len(listas) != 1:
            sys.exit(f"ERROR: {ruta.name} debe ser una lista de registros")
        datos = listas[0]
    if not isinstance(datos, list):
        sys.exit(f"ERROR: {ruta.name} debe ser una lista de registros")
    return [norm_claves(r) for r in datos if isinstance(r, dict)]


def parse_fecha(v):
    """Acepta datetime, '26 de septiembre de 2024', '23 de septiembre 2026', '13/12/24', '13/12/2024', '2024-12-13'."""
    if isinstance(v, datetime):
        return v
    v = limpiar(v)
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+(?:de\s+)?(\d{4})", v)
    if m and sin_acentos(m[2]).casefold() in MESES:
        return _fecha(int(m[3]), MESES[sin_acentos(m[2]).casefold()], int(m[1]))
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})", v)
    if m:
        anio = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        return _fecha(anio, int(m[2]), int(m[1]))
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?", v)
    if m:
        return _fecha(int(m[1]), int(m[2]), int(m[3]))
    return None


def _fecha(a, m, d):
    try:
        return datetime(a, m, d)
    except ValueError:
        return None


def parse_hora(v):
    """'11:13' -> time. Si no es una hora (p. ej. 'Sin hora') devuelve el texto tal cual."""
    if isinstance(v, time):
        return v
    v = limpiar(v)
    if not isinstance(v, str):
        return v
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", v)
    if m and int(m[1]) < 24 and int(m[2]) < 60:
        return time(int(m[1]), int(m[2]), int(m[3] or 0))
    return v


def es_link(v):
    return isinstance(v, str) and v.lower().startswith(("http://", "https://"))


def ordenar_fechas(fechas):
    return sorted(fechas, reverse=(ORDEN_SESIONES == "desc"))


# =====================================================================================
# UTILIDADES DE ESTILO
# =====================================================================================
def rgb(hex6):
    return PatternFill("solid", start_color="FF" + hex6, end_color="FF" + hex6)


def estilizar(ws, r1, c1, r2, c2, font=None, fill=None, align=None, border=None, fmt=None):
    for fila in ws.iter_rows(min_row=r1, max_row=r2, min_col=c1, max_col=c2):
        for c in fila:
            if font is not None:
                c.font = font
            if fill is not None:
                c.fill = fill
            if align is not None:
                c.alignment = align
            if border is not None:
                c.border = border
            if fmt is not None:
                c.number_format = fmt


def preparar_hoja(ws):
    ws.sheet_format.defaultColWidth = ANCHO_COL_DEFECTO
    ws.sheet_format.defaultRowHeight = ALTO_FILA_DEFECTO
    ws.sheet_format.customHeight = True


def nombre_visible(nombre, tipo):
    if PREFIJO_COMITE and clave(tipo or "") == "comite" and not nombre.startswith(PREFIJO_COMITE):
        return PREFIJO_COMITE + nombre
    return nombre


# =====================================================================================
# HOJA 1: INTEGRANTES
# =====================================================================================
def colorear_encabezado_integrantes(h):
    k = clave(h)
    if k.startswith("plan de trabajo"):
        return "CCCCCC"
    if k.startswith("informe anual") or k in ("periodo", "comision / comite", "total de reuniones"):
        return "B7B7B7"
    return "FFFFFF"


def hoja_integrantes(wb, regs, tipo_de):
    ws = wb.active
    ws.title = HOJA_INTEGRANTES
    preparar_hoja(ws)

    # Encabezados = unión ordenada de las claves de todos los registros
    encabezados = []
    for r in regs:
        for k in r:
            if k not in encabezados:
                encabezados.append(k)
    if CLAVE_NOMBRE not in encabezados:
        sys.exit(f"ERROR: los integrantes no traen la clave '{CLAVE_NOMBRE}'")
    # 'Nombre' siempre primera
    encabezados.remove(CLAVE_NOMBRE)
    encabezados.insert(0, CLAVE_NOMBRE)

    for j, h in enumerate(encabezados, 1):
        c = ws.cell(1, j, h)
        c.font, c.border = FUENTE_ENC, BORDE
        c.fill = rgb(colorear_encabezado_integrantes(h))
        c.alignment = Alignment(vertical="bottom")

    fila = 2
    periodo_prev = None
    for idx, r in enumerate(regs):
        if "Periodo" in encabezados and idx > 0 and limpiar(r.get("Periodo")) != periodo_prev:
            # Separador entre bloques de periodo, como en el original
            c = ws.cell(fila, 1, "-" * 86)
            c.font, c.border = FUENTE_BASE, BORDE
            fila += 1
        periodo_prev = limpiar(r.get("Periodo"))

        for j, h in enumerate(encabezados, 1):
            v = limpiar(r.get(h))
            if h == CLAVE_NOMBRE and v:
                v = nombre_visible(v, tipo_de.get(clave(v)))
            c = ws.cell(fila, j)
            c.font, c.border = FUENTE_BASE, BORDE
            c.alignment = Alignment(vertical="bottom")
            if "fecha" in clave(h) and v is not None:
                f = parse_fecha(v)
                if f:
                    c.value = f
                    c.number_format = "d/mm/yyyy"
                    c.alignment = Alignment(horizontal="right", vertical="bottom")
                else:
                    c.value = v
                    avisar(f"Integrantes: fecha no reconocida en '{h}' fila {fila}: {v!r}")
            elif es_link(v):
                c.value = v
                c.hyperlink = v
                c.font = FUENTE_LINK
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                c.value = v
                c.alignment = Alignment(horizontal="right", vertical="bottom")
            else:
                c.value = v
        fila += 1

    # Anchos: 'Nombre' ancho; columnas con nombres de personas ~29 (como K, N y R del original)
    ws.column_dimensions["A"].width = 47.75
    for j, h in enumerate(encabezados, 1):
        if re.match(r"(?i)^(presidente|secretari|vocal \d)", h):
            ws.column_dimensions[letra(j)].width = 29
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{letra(len(encabezados))}{fila - 1}"
    return len(regs)


# =====================================================================================
# HOJA 2: SESIONES
# =====================================================================================
def detectar_estructura(regs):
    """Devuelve {año: {periodo: {'ses': n máx, 'rec': n máx}}} a partir de las claves y del contenido."""
    est = {}
    for r in regs:
        for k, v in r.items():
            m = re.fullmatch(r"sesiones_a\w*o_(\d+)", k)
            if not m or not isinstance(v, dict):
                continue
            anio = int(m[1])
            for kk, vv in norm_claves(v).items():
                mm = re.fullmatch(r"sesiones_(periodo|receso)_(\d+)", kk)
                if not mm or not isinstance(vv, list):
                    continue
                p = est.setdefault(anio, {}).setdefault(int(mm[2]), {"ses": 0, "rec": 0})
                clave_n = "ses" if mm[1] == "periodo" else "rec"
                p[clave_n] = max(p[clave_n], len(vv))
            # periodos declarados aunque estén vacíos
    return est


def fechas_de(reg, anio, tipo, periodo):
    """Lista de fechas (datetime) ordenadas de un registro de sesiones."""
    bloque = norm_claves(reg.get(next((k for k in reg if re.fullmatch(rf"sesiones_a\w*o_{anio}", k)), ""), {}) or {})
    lista = bloque.get(f"sesiones_{tipo}_{periodo}") or []
    out = []
    for s in lista:
        f = parse_fecha(s)
        if f:
            out.append(f)
        elif limpiar(s) is not None:
            avisar(f"Sesiones: fecha no reconocida ({reg.get(CLAVE_NOMBRE)}, año {anio}, {tipo} {periodo}): {s!r}")
    return ordenar_fechas(out)


def anio_base(regs_integrantes):
    anios = []
    for r in regs_integrantes:
        f = parse_fecha(r.get("Fecha de instalación"))
        if f:
            anios.append(f.year)
    return min(anios) if anios else ANIO_INICIO_POR_DEFECTO


def titulos_periodo(p, anio_ciclo):
    if p not in CAL_PERIODO:
        return f"PERIODO {p} DE SESIONES", f"Receso {p}"
    (d1, m1, o1), (d2, m2, o2) = CAL_PERIODO[p]
    tit = (f"{ORDINAL.get(p, p)} PERIODO DE SESIONES".upper() +
           f"\n ({d1} de {NOMBRE_MES[m1]} al {d2} de {NOMBRE_MES[m2]} del {anio_ciclo + o2})")
    (rd1, rm1, ro1), (rd2, rm2, ro2) = CAL_RECESO[p]
    rec = (f"{ORDINAL.get(p, p)} receso\n"
           f"{rd1:02d}-{rm1:02d}-{(anio_ciclo + ro1) % 100:02d} / {rd2:02d}-{rm2:02d}-{(anio_ciclo + ro2) % 100:02d}")
    return tit, rec


def hoja_sesiones(wb, regs, orden_nombres, tipo_de, anio0):
    ws = wb.create_sheet(HOJA_SESIONES)
    preparar_hoja(ws)
    est = detectar_estructura(regs)
    if not est:
        sys.exit("ERROR: no se encontraron claves 'sesiones_año_N' en el JSON de sesiones")

    # Solo años con datos (siempre el primero)
    anios = []
    for a in sorted(est):
        hay = any(fechas_de(r, a, t, p) for r in regs for p in est[a] for t in ("periodo", "receso"))
        if hay or INCLUIR_ANIOS_SIN_DATOS or a == min(est):
            anios.append(a)

    # ---- Plan de columnas
    col = 2
    plan = []
    for a in anios:
        bloque = {"anio": a, "periodos": []}
        for p in sorted(est[a]):
            n_ses = max(est[a][p]["ses"], MIN_COLUMNAS_SESION)
            n_rec = max(est[a][p]["rec"], MIN_COLUMNAS_RECESO)
            bloque["periodos"].append({
                "p": p, "ini": col, "n_ses": n_ses, "n_rec": n_rec,
                "rec_ini": col + n_ses, "total": col + n_ses + n_rec,
            })
            col += n_ses + n_rec + 1
        bloque["total_anio"] = col
        col += 1
        plan.append(bloque)
    ultima_col = col - 1

    # ---- Encabezados
    ws.row_dimensions[1].height = 45
    c = ws.cell(2, 1, "Nombre")
    c.font, c.fill, c.border = FUENTE_ENC, rgb("FFFFFF"), BORDE
    c.alignment = Alignment(vertical="bottom")

    centro = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for b in plan:
        anio_ciclo = anio0 + (b["anio"] - 1)
        for pp in b["periodos"]:
            color_p = COLORES_PERIODO[((pp["p"] - 1) % 3) + 1]
            tit, rec = titulos_periodo(pp["p"], anio_ciclo)

            # Periodo (fila 1 combinada + números en fila 2)
            fin_ses = pp["ini"] + pp["n_ses"] - 1
            ws.cell(1, pp["ini"], tit)
            estilizar(ws, 1, pp["ini"], 1, fin_ses, font=FUENTE_ENC, fill=rgb(color_p[0]), align=centro, border=BORDE)
            if fin_ses > pp["ini"]:
                ws.merge_cells(start_row=1, start_column=pp["ini"], end_row=1, end_column=fin_ses)
            for i in range(pp["n_ses"]):
                ws.cell(2, pp["ini"] + i, i + 1)
            estilizar(ws, 2, pp["ini"], 2, fin_ses, font=FUENTE_ENC_PEQ, fill=rgb(color_p[1]),
                      align=Alignment(horizontal="center", vertical="bottom"), border=BORDE)

            # Receso
            fin_rec = pp["rec_ini"] + pp["n_rec"] - 1
            ws.cell(1, pp["rec_ini"], rec)
            estilizar(ws, 1, pp["rec_ini"], 1, fin_rec, font=FUENTE_ENC, fill=rgb(COLORES_RECESO[0]), align=centro, border=BORDE)
            if fin_rec > pp["rec_ini"]:
                ws.merge_cells(start_row=1, start_column=pp["rec_ini"], end_row=1, end_column=fin_rec)
            for i in range(pp["n_rec"]):
                ws.cell(2, pp["rec_ini"] + i, i + 1)
            estilizar(ws, 2, pp["rec_ini"], 2, fin_rec, font=FUENTE_ENC_PEQ, fill=rgb(COLORES_RECESO[1]),
                      align=Alignment(horizontal="center", vertical="bottom"), border=BORDE)

            # Total del periodo (combinado filas 1-2)
            ws.cell(1, pp["total"], "Total")
            estilizar(ws, 1, pp["total"], 2, pp["total"], font=FUENTE_TOTAL, align=centro, border=BORDE)
            ws.merge_cells(start_row=1, start_column=pp["total"], end_row=2, end_column=pp["total"])

        ws.cell(1, b["total_anio"], "Total de sesiones")
        estilizar(ws, 1, b["total_anio"], 2, b["total_anio"], font=FUENTE_TOTAL_ANIO, align=centro, border=SIN_BORDE)
        ws.merge_cells(start_row=1, start_column=b["total_anio"], end_row=2, end_column=b["total_anio"])

    # ---- Datos
    por_nombre = {clave(r.get(CLAVE_NOMBRE, "")): r for r in regs}
    fila = 3
    for nombre in orden_nombres:
        r = por_nombre.get(clave(nombre))
        c = ws.cell(fila, 1, nombre_visible(nombre, tipo_de.get(clave(nombre))))
        c.font = FUENTE_BASE
        if r is not None:
            for b in plan:
                totales = []
                for pp in b["periodos"]:
                    for tipo, ini, n in (("periodo", pp["ini"], pp["n_ses"]), ("receso", pp["rec_ini"], pp["n_rec"])):
                        for i, f in enumerate(fechas_de(r, b["anio"], tipo, pp["p"])):
                            cel = ws.cell(fila, ini + i, f)
                            cel.number_format = "dd/mm/yy"
                            cel.font = FUENTE_BASE
                    rango = f"{letra(pp['ini'])}{fila}:{letra(pp['total'] - 1)}{fila}"
                    t = ws.cell(fila, pp["total"], f"=COUNT({rango})")
                    t.font = FUENTE_BASE
                    totales.append(f"{letra(pp['total'])}{fila}")
                    # Verificación contra el total que trae el JSON
                    bloque = norm_claves(r.get(next((k for k in r if re.fullmatch(rf"sesiones_a\w*o_{b['anio']}", k)), ""), {}) or {})
                    declarado = bloque.get(f"total_periodo_{pp['p']}")
                    real = len(fechas_de(r, b["anio"], "periodo", pp["p"])) + len(fechas_de(r, b["anio"], "receso", pp["p"]))
                    if isinstance(declarado, int) and declarado != real:
                        avisar(f"Sesiones: '{nombre}' año {b['anio']} periodo {pp['p']}: total del JSON={declarado}, fechas={real}")
                t = ws.cell(fila, b["total_anio"], "=" + "+".join(totales))
                t.font = FUENTE_BASE
        else:
            avisar(f"Sesiones: '{nombre}' no aparece en el JSON de sesiones")
        fila += 1

    ws.column_dimensions["A"].width = 36.75
    ws.freeze_panes = "B3"
    return len(orden_nombres), plan


# =====================================================================================
# HOJA 3: CONTENIDO
# =====================================================================================
def especificacion_contenido():
    """Cada columna: encabezado, clave normalizada en el JSON, tipo de dato, ancho, alineación, comentario."""
    cols = [
        dict(h="No.", k=None, t="numero", w=6.63, a="center"),
        dict(h="Comisión 1", k="@comision", t="texto", w=47.75),
    ]
    for n in range(2, 2 + COLUMNAS_COMISION_EXTRA):
        cols.append(dict(h=f"Comisión {n}", k=f"comision {n}", t="texto"))
    cols += [
        dict(h="Tipo", k="tipo", t="entero", a="center",
             nota="1. Iniciativa de ley\n2. Punto de Acuerdo o exhorto\n3. Otro"),
        dict(h="Asunto abordado", k="asunto abordado", t="texto", w=84.13, wrap=True),
        dict(h="Fecha de sesión en Comisión", k="fecha de sesion en comision", t="fecha", a="center"),
        dict(h="Presentador", k="presentador", t="texto", w=15.75, a="center"),
        dict(h="Partido", k="partido", t="texto", a="center"),
        dict(h="Estatus", k="estatus", t="texto", a="center"),
        dict(h="Hora programada", k="hora programada", t="hora", w=13.63, a="center"),
        dict(h="Inicio de la sesión", k="inicio de la sesion", t="hora", a="center"),
        dict(h="Fin de la sesión", k="fin de la sesion", t="hora", a="center"),
        dict(h="Tiempo", k="tiempo", t="tiempo", a="center"),
        dict(h="Observaciones", k="observaciones", t="texto", w=56.5,
             nota="Solo se indicará si hay ausencia de documentos: citatorio, orden del día, versión estenográfica"),
        dict(h="Estenográfica", k="estenografica", t="link", w=30),
        dict(h="Acta", k="acta", t="link", w=30),
    ]
    return cols


def hoja_contenido(wb, regs, orden_nombres, tipo_de):
    ws = wb.create_sheet(HOJA_CONTENIDO)
    ws.sheet_format.defaultColWidth = ANCHO_COL_DEFECTO
    ws.sheet_format.defaultRowHeight = ALTO_FILA_DEFECTO   # sin customHeight: Excel autoajusta el texto envuelto
    cols = especificacion_contenido()
    idx = {c["h"]: j for j, c in enumerate(cols, 1)}
    l_ini, l_fin = letra(idx["Inicio de la sesión"]), letra(idx["Fin de la sesión"])

    # ---- Encabezados
    for j, c in enumerate(cols, 1):
        cel = ws.cell(1, j, c["h"])
        if c["h"] == "No.":
            cel.font, cel.border = Font(name="Arial", size=10, bold=True), SIN_BORDE
        else:
            cel.font, cel.border = FUENTE_ENC, BORDE
        cel.alignment = Alignment(horizontal=c.get("a"), wrap_text=True)
        if c.get("nota"):
            cm = Comment(c["nota"], "Sistema")
            cm.width, cm.height = 260, 90
            cel.comment = cm
        if c.get("w"):
            ws.column_dimensions[letra(j)].width = c["w"]
    cm = Comment("La información sale de los archivos descargables de las versiones estenográficas de cada sesión.", "Sistema")
    cm.width, cm.height = 260, 90
    ws.cell(1, 1).comment = cm

    # ---- Datos (una fila por sesión)
    por_nombre = {clave(r.get(CLAVE_NOMBRE, "")): r for r in regs}
    fila = 2
    n_sesiones = 0
    for nombre in orden_nombres:
        r = por_nombre.get(clave(nombre))
        if r is None:
            avisar(f"Contenido: '{nombre}' no aparece en el JSON de contenido")
            continue
        sesiones = [norm_claves(s) for s in (r.get("Sesiones") or []) if isinstance(s, dict)]
        sesiones = [{clave(k): v for k, v in s.items()} for s in sesiones]
        # Orden por fecha; las que no se puedan interpretar quedan al final
        sesiones.sort(key=lambda s: (parse_fecha(s.get("fecha de sesion en comision")) is None,
                                     parse_fecha(s.get("fecha de sesion en comision")) or datetime.min),
                      reverse=False)
        if ORDEN_SESIONES == "desc":
            con = [s for s in sesiones if parse_fecha(s.get("fecha de sesion en comision"))]
            sin = [s for s in sesiones if not parse_fecha(s.get("fecha de sesion en comision"))]
            sesiones = con[::-1] + sin

        for s in sesiones:
            n_sesiones += 1
            for j, c in enumerate(cols, 1):
                cel = ws.cell(fila, j)
                cel.font = FUENTE_BASE
                t = c["t"]
                v = None if c["k"] is None else (nombre_visible(nombre, tipo_de.get(clave(nombre)))
                                                   if c["k"] == "@comision" else limpiar(s.get(c["k"])))
                if t == "numero":
                    v = n_sesiones
                elif t == "fecha" and v is not None:
                    f = parse_fecha(v)
                    if f:
                        v = f
                        cel.number_format = "dd-mm-yyyy"
                    else:
                        avisar(f"Contenido: fecha no reconocida ({nombre}, fila {fila}): {v!r}")
                elif t == "hora" and v is not None:
                    v = parse_hora(v)
                    if isinstance(v, time):
                        cel.number_format = "h:mm"
                elif t == "link" and es_link(v):
                    cel.hyperlink = v
                    cel.font = FUENTE_LINK
                elif t == "tiempo":
                    ini = parse_hora(s.get("inicio de la sesion"))
                    fin = parse_hora(s.get("fin de la sesion"))
                    if isinstance(ini, time) and isinstance(fin, time):
                        v = f"=ROUND(MOD({l_fin}{fila}-{l_ini}{fila},1)*1440,0)"   # minutos, sin depender del JSON
                    cel.number_format = "0"
                cel.value = v
                if c.get("a"):
                    cel.alignment = Alignment(horizontal=c["a"], wrap_text=c.get("wrap", False))
                elif c.get("wrap"):
                    cel.alignment = Alignment(wrap_text=True)
            fila += 1

    ws.freeze_panes = "A2"
    return n_sesiones


# =====================================================================================
# PRINCIPAL
# =====================================================================================
def main():
    aqui = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Genera el Excel de comisiones a partir de 3 JSON.")
    ap.add_argument("--carpeta", default=str(aqui.parent / "data"), help="Carpeta donde están los JSON")
    ap.add_argument("--integrantes")
    ap.add_argument("--sesiones")
    ap.add_argument("--contenido")
    ap.add_argument("--salida", help=f"Archivo de salida (por defecto {ARCHIVO_SALIDA} en la carpeta)")
    a = ap.parse_args()

    carpeta = Path(a.carpeta)
    r_int = Path(a.integrantes) if a.integrantes else carpeta / ARCHIVO_INTEGRANTES
    r_ses = Path(a.sesiones) if a.sesiones else carpeta / ARCHIVO_SESIONES
    r_con = Path(a.contenido) if a.contenido else carpeta / ARCHIVO_CONTENIDO
    salida = (
    Path(a.salida)
    if a.salida
    else aqui.parent / "outputs" / ARCHIVO_SALIDA
    )

    integrantes = cargar_json(r_int)
    sesiones = cargar_json(r_ses)
    contenido = cargar_json(r_con)

    # Orden canónico = orden de Integrantes; luego cualquier nombre que solo esté en los otros archivos
    orden, vistos = [], set()
    for fuente in (integrantes, sesiones, contenido):
        for r in fuente:
            n = limpiar(r.get(CLAVE_NOMBRE))
            if n and clave(n) not in vistos:
                vistos.add(clave(n))
                orden.append(n)
    # Tipo (Comisión / Comité) por nombre, para el prefijo opcional
    tipo_de = {}
    for r in integrantes:
        tipo_de[clave(r.get(CLAVE_NOMBRE, ""))] = limpiar(r.get("Comisión / Comité"))
    for r in contenido:
        tipo_de.setdefault(clave(r.get(CLAVE_NOMBRE, "")), limpiar(r.get("Tipo")))

    wb = Workbook()
    n_int = hoja_integrantes(wb, integrantes, tipo_de)
    n_ses, plan = hoja_sesiones(wb, sesiones, orden, tipo_de, anio_base(integrantes))
    n_con = hoja_contenido(wb, contenido, orden, tipo_de)
    wb.calculation.fullCalcOnLoad = True   # Excel calcula las fórmulas al abrir

    salida.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(salida)
    except PermissionError:
        sys.exit(f"ERROR: no se pudo guardar {salida}. ¿Está abierto en Excel?")

    print(f"Archivo generado: {salida}")
    print(f"  Integrantes: {n_int} filas")
    print(f"  Sesiones:    {n_ses} comisiones, {len(plan)} año(s) de legislatura, {plan[-1]['total_anio']} columnas")
    print(f"  Contenido:   {n_con} sesiones")
    if AVISOS:
        print(f"\n{len(AVISOS)} aviso(s):")
        for m in AVISOS[:50]:
            print("  -", m)
        if len(AVISOS) > 50:
            print(f"  ... y {len(AVISOS) - 50} más")


if __name__ == "__main__":
    main()