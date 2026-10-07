"""
Compara Asistencias.xlsx (generado con script2) contra asistencias_gacetas.json.

- Lee asistencias_gacetas.json y pinta las discrepancias directamente en
  Asistencias.xlsx (color + comentario con tipo y valor de la gaceta).
- Para no sobrescribir, usar --salida con otro nombre.
- Solo se comparan las sesiones que existen en ambos lados.

Uso:
    python comparar_gacetas.py
    python comparar_gacetas.py --excel ruta/Asistencias.xlsx --json ruta/asistencias_gacetas.json
"""
import argparse
import difflib
import json
import os
import re
import unicodedata
from collections import Counter

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill

# ── Colores ──────────────────────────────────────────────────────────────────
COLOR_DISTINTO   = "FF6B35"   # Naranja/rojo: ambos tienen dato pero es DIFERENTE
COLOR_SOLO_EXCEL = "FFE066"   # Amarillo: el Excel tiene dato y la gaceta no lista al diputado
COLOR_SOLO_GACETA = "9DC3E6"  # Azul claro: la gaceta tiene dato y el Excel está vacío / "-"

LABELS = {
    1: "Asistencia", 2: "Retardo Justificado", 3: "Retardo Injustificado",
    4: "Inasistencia Justificada", 5: "Inasistencia Injustificada",
    6: "Con Licencia", 7: "Sin información", 8: "Fallecimiento",
}

# Tipo de sesión del Excel -> tipo de la "Clave sesión" del JSON
TIPO_SESION = {
    "solemne": "solemne",
    "publica ordinaria": "ordinaria",
    "ordinaria": "ordinaria",
    "extraordinaria": "extraordinaria",
    "comision permanente": "comision-permanente",
    "previa": "previa",
}


def fill(hex_color):
    return PatternFill(start_color=hex_color, end_color=hex_color, fill_type="solid")


def norm(texto):
    """Minúsculas, sin acentos y sin espacios repetidos."""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip().casefold()


def describe(valor):
    if isinstance(valor, int) and valor in LABELS:
        return f"{valor} ({LABELS[valor]})"
    return str(valor)


def vacio(valor):
    return valor is None or str(valor).strip() in ("", "-")


# ── Lectura de la estructura del Excel ───────────────────────────────────────
def detectar_bloques(ws):
    """
    Detecta cada bloque (Ordinarias/Extraordinarias/Solemnes, Permanente...)
    buscando las filas donde la columna B dice 'fecha'.
    Devuelve [{fecha_row, tipo_row, first, last}, ...]
    """
    bloques = []
    for r in range(1, ws.max_row + 1):
        if norm(ws.cell(r, 2).value or "") == "fecha":
            # Buscar fila "Diputado" que cierra el encabezado
            r_dip = r + 1
            while r_dip <= ws.max_row and norm(ws.cell(r_dip, 2).value or "") != "diputado":
                r_dip += 1
            first = r_dip + 1
            last = first
            while last + 1 <= ws.max_row and ws.cell(last + 1, 2).value not in (None, "") \
                    and not str(ws.cell(last + 1, 2).value).startswith("Totales"):
                last += 1
            bloques.append({"fecha_row": r, "tipo_row": r + 1, "first": first, "last": last})
    return bloques


def columnas_por_sesion(ws, bloque):
    """{clave_sesion: columna}; la clave replica la del JSON: AAAA-MM-DD|tipo|n"""
    vistos = Counter()
    resultado = {}
    for c in range(3, ws.max_column + 1):
        fecha = ws.cell(bloque["fecha_row"], c).value
        tipo = ws.cell(bloque["tipo_row"], c).value
        if fecha is None or tipo is None or not hasattr(fecha, "strftime"):
            continue
        tipo_norm = TIPO_SESION.get(norm(tipo))
        if tipo_norm is None:
            continue
        k = (fecha.strftime("%Y-%m-%d"), tipo_norm)
        vistos[k] += 1
        resultado[f"{k[0]}|{k[1]}|{vistos[k]}"] = c
    return resultado


# ── Emparejar nombres (tolera pequeñas erratas) ──────────────────────────────
def mapear_nombres(nombres_gaceta, nombres_excel):
    """nombre_gaceta_normalizado -> nombre_excel_normalizado (o None)."""
    mapa = {}
    for n in nombres_gaceta:
        if n in nombres_excel:
            mapa[n] = n
            continue
        cercano = difflib.get_close_matches(n, nombres_excel, n=1, cutoff=0.9)
        mapa[n] = cercano[0] if cercano else None
    return mapa


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default=os.path.join(base, "..", "outputs", "Asistencias.xlsx"))
    ap.add_argument("--json", default=os.path.join(base, "..", "data", "asistencias_gacetas.json"))
    ap.add_argument("--salida", default=None, help="Por defecto sobrescribe el mismo Asistencias.xlsx")
    args = ap.parse_args()
    if args.salida is None:
        args.salida = args.excel

    with open(args.json, "r", encoding="utf-8") as f:
        gacetas = {r["Clave sesión"]: r for r in json.load(f)["registros"]}

    wb = load_workbook(args.excel)
    ws = wb["Hoja 1"] if "Hoja 1" in wb.sheetnames else wb.worksheets[0]

    bloques = detectar_bloques(ws)
    if not bloques:
        raise SystemExit("No se encontró la estructura esperada (fila 'fecha') en el Excel.")

    # Clave de sesión -> (bloque, columna)
    sesiones_excel = {}
    for b in bloques:
        for clave, col in columnas_por_sesion(ws, b).items():
            sesiones_excel[clave] = (b, col)

    # Nombres del Excel (normalizados)
    nombres_excel = {}
    for b in bloques:
        for r in range(b["first"], b["last"] + 1):
            v = ws.cell(r, 2).value
            if v:
                nombres_excel[norm(v)] = v
    nombres_gaceta = {norm(x["Diputado"]) for rec in gacetas.values() for x in rec["Asistencias"]}
    mapa = mapear_nombres(nombres_gaceta, list(nombres_excel))

    sin_columna = [k for k in gacetas if k not in sesiones_excel]
    sin_match_nombre = sorted(n for n, m in mapa.items() if m is None)

    discrepancias = []   # filas para la hoja "Discrepancias"
    conteo = Counter()

    for clave, rec in gacetas.items():
        if clave not in sesiones_excel:
            continue
        bloque, col = sesiones_excel[clave]

        # valores de la gaceta, ya traducidos al nombre del Excel
        gac = {}
        for x in rec["Asistencias"]:
            destino = mapa.get(norm(x["Diputado"]))
            if destino:
                gac[destino] = x["Asistencia"]

        for r in range(bloque["first"], bloque["last"] + 1):
            nombre_x = ws.cell(r, 2).value
            val_x = ws.cell(r, col).value
            val_g = gac.get(norm(nombre_x))
            celda = ws.cell(r, col)

            if vacio(val_x) and val_g is None:
                continue   # nada que comparar

            if val_g is None:
                tipo, color = "Solo en Excel (la gaceta no lista al diputado)", COLOR_SOLO_EXCEL
            elif vacio(val_x):
                tipo, color = "Solo en gaceta (Excel vacío o '-')", COLOR_SOLO_GACETA
            elif str(val_x).strip() != str(val_g).strip():
                tipo, color = "Valor distinto", COLOR_DISTINTO
            else:
                continue   # coinciden

            conteo[tipo] += 1
            celda.fill = fill(color)
            celda.comment = Comment(
                f"{tipo}\nGaceta: {describe(val_g) if val_g is not None else '(no aparece)'}",
                "comparar_gacetas",
            )
            celda.comment.width = 260
            celda.comment.height = 70
            fecha = ws.cell(bloque["fecha_row"], col).value
            discrepancias.append([
                clave, fecha.strftime("%d/%m/%Y"), ws.cell(bloque["tipo_row"], col).value,
                nombre_x, celda.coordinate,
                describe(val_x) if val_x is not None else "(vacío)",
                describe(val_g) if val_g is not None else "(no aparece)",
                tipo,
            ])

    os.makedirs(os.path.dirname(os.path.abspath(args.salida)), exist_ok=True)
    wb.save(args.salida)

    # ── Reporte en consola ───────────────────────────────────────────────────
    print(f"Sesiones comparadas: {sum(1 for k in gacetas if k in sesiones_excel)} de {len(gacetas)} en gacetas")
    print(f"Discrepancias totales: {len(discrepancias)}")
    for t, n in conteo.most_common():
        print(f"  - {t}: {n}")
    if sin_columna:
        print("Sesiones de gacetas que no existen en el Excel (no se compararon):")
        for k in sin_columna:
            print("   ", k)
    if sin_match_nombre:
        print("Nombres de gacetas sin equivalente en el Excel:")
        for n in sin_match_nombre:
            print("   ", n)
    print(f"Archivo generado: {os.path.abspath(args.salida)}")


if __name__ == "__main__":
    main()