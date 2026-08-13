"""
json_a_excel_asistencias.py
----------------------------
Convierte asistencias.json (estructura: {"registros": [ {sesion...} ]})
en un archivo Excel casi idéntico a Copia_de_Asistencia_2024-2025.xlsx.

Estructura del JSON de entrada:
    Cada "registro" es UNA SESIÓN, con:
        Folio legislatura, Folio periodo, Año Legislatura, Periodo,
        Fecha, Sesión (tipo), Asistencias: [{Diputado, Asistencia}, ...],
        Totales: {Asistencia, Retardo Justificado, ...}

El Excel de salida pivotea esa información a filas = diputado, columnas = sesión,
igual que la plantilla original:

    - Bloque 1 ("normal"): todas las sesiones cuyo tipo NO es "Comisión Permanente"
      (Previa, Solemne, Ordinaria, Extraordinaria, etc.)
    - Bloque 2 ("Permanente"): solo sesiones tipo "Comisión Permanente"

Cada bloque tiene 4 filas de encabezado (num, fecha, Sesión, Periodo) + una fila
"Diputado" + una fila por diputado, y al final una fila de TOTALES por sesión
(con las 7 categorías del JSON, en la columna B a modo de etiqueta con fondo gris).

Valores de asistencia: se escriben como el NÚMERO tal cual viene en el JSON
(1, 2, 3, 4... o "No aplica" / "-" si el diputado no participó en esa sesión).
La hoja "Claves" se agrega/actualiza como referencia de esos números.

Pensado para volver a correrse cada vez que el JSON se actualice: no asume una
cantidad fija de sesiones ni de diputados.

Uso:
    python json_a_excel_asistencias.py [ruta_json] [ruta_salida_xlsx]

Si no se pasan argumentos, usa:
    entrada:  asistencias.json
    salida:   Asistencia_Actualizada.xlsx
"""

import json
import os
import sys
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# ---------------------------------------------------------------------------
# Config de estilo (replicando Copia_de_Asistencia_2024-2025.xlsx)
# ---------------------------------------------------------------------------
FUENTE_ENCABEZADO = Font(name="Calibri", bold=True)
FUENTE_ENCABEZADO_ROJO = Font(name="Calibri", bold=True, color="FFFFFFFF")
FUENTE_NORMAL = Font(name="Arial")
FUENTE_ETIQUETA_TOTALES = Font(name="Arial", bold=True)

RELLENO_TITULO_BLOQUE1 = PatternFill(start_color="FFEA4335", end_color="FFEA4335", fill_type="solid")
RELLENO_TITULO_BLOQUE2 = PatternFill(start_color="FFFF0000", end_color="FFFF0000", fill_type="solid")
RELLENO_FILA_FECHA = PatternFill(start_color="FFF3F3F3", end_color="FFF3F3F3", fill_type="solid")
RELLENO_TOTALES = PatternFill(start_color="FFD9D9D9", end_color="FFD9D9D9", fill_type="solid")  # gris

FORMATO_FECHA = "d/MM/yyyy"

# Etiquetas de las categorías de "Totales", en el orden en que se muestran
CATEGORIAS_TOTALES = [
    "Asistencia",
    "Retardo Justificado",
    "Retardo Injustificado",
    "Inasistencia Justificada",
    "Inasistencia Injustificada",
    "Con Licencia",
    "Sin información",
    "Fallecimiento",
]

# Datos para reconstruir la hoja "Claves" (igual que la plantilla original)
CLAVES = [
    (1, "Asistencia"),
    (2, "Retardo Justificado"),
    (3, "Retardo Injustificado"),
    (4, "Inasistencia Justificada"),
    (5, "Inasistencia Injustificada"),
    (6, "Con Licencia "),
    (7, "Sin información"),
    (8, "Fallecimiento"),
    ("-", "ya no es diputado"),
]

ANCHO_MINIMO = 8
ANCHO_MAXIMO = 45


# ---------------------------------------------------------------------------
# Carga y organización de datos
# ---------------------------------------------------------------------------
def cargar_registros(ruta_json: str) -> list[dict]:
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


def parsear_fecha(fecha_str: str):
    """Convierte 'dd/mm/aaaa' a datetime; si falla, regresa el string tal cual."""
    try:
        return datetime.strptime(fecha_str, "%d/%m/%Y")
    except (ValueError, TypeError):
        return fecha_str


def separar_bloques(registros: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Separa las sesiones en dos bloques, igual que la plantilla original:
      - normales: cualquier tipo de sesión distinto de 'Comisión Permanente'
      - permanente: solo 'Comisión Permanente'
    Cada bloque se ordena por Folio legislatura (orden cronológico del JSON).
    """
    normales = [r for r in registros if r.get("Sesión") != "Comisión Permanente"]
    permanente = [r for r in registros if r.get("Sesión") == "Comisión Permanente"]

    normales.sort(key=lambda r: r.get("Folio legislatura", 0))
    permanente.sort(key=lambda r: r.get("Folio legislatura", 0))

    return normales, permanente


def construir_orden_diputados(sesiones: list[dict]) -> list[str]:
    """
    Determina el orden de diputados (filas) para un bloque: el orden de
    primera aparición a lo largo de las sesiones, para mantener un orden
    estable y determinístico entre corridas del script.
    """
    orden = []
    vistos = set()
    for sesion in sesiones:
        for asistencia in sesion.get("Asistencias", []):
            nombre = asistencia.get("Diputado")
            if nombre and nombre not in vistos:
                vistos.add(nombre)
                orden.append(nombre)
    return orden


def mapa_asistencias_por_sesion(sesion: dict) -> dict:
    """Diccionario {nombre_diputado: valor_asistencia} para una sesión."""
    return {a.get("Diputado"): a.get("Asistencia") for a in sesion.get("Asistencias", [])}


# ---------------------------------------------------------------------------
# Escritura de un bloque de sesiones en la hoja
# ---------------------------------------------------------------------------
def escribir_bloque(ws, fila_inicio: int, titulo: str, relleno_titulo,
                     sesiones: list[dict]) -> int:
    """
    Escribe un bloque completo (título + encabezados + filas de diputados +
    fila de totales) a partir de fila_inicio. Regresa la fila siguiente libre
    después del bloque (dejando una fila en blanco de separación).
    """
    diputados = construir_orden_diputados(sesiones)
    n_sesiones = len(sesiones)

    fila_titulo = fila_inicio
    fila_num = fila_inicio + 1
    fila_fecha = fila_inicio + 2
    fila_tipo_sesion = fila_inicio + 3
    fila_periodo = fila_inicio + 4
    fila_diputado_label = fila_inicio + 5
    fila_primer_diputado = fila_inicio + 6

    # --- Título del bloque (columna B) ---
    celda_titulo = ws.cell(row=fila_titulo, column=2, value=titulo)
    celda_titulo.font = FUENTE_ENCABEZADO_ROJO
    celda_titulo.fill = relleno_titulo

    # --- Etiquetas de columna B para cada fila de encabezado ---
    etiquetas = {
        fila_num: "num",
        fila_fecha: "fecha",
        fila_tipo_sesion: "Sesión",
        fila_periodo: "Periodo",
        fila_diputado_label: "Diputado",
    }
    for fila, texto in etiquetas.items():
        c = ws.cell(row=fila, column=2, value=texto)
        c.font = FUENTE_ENCABEZADO
        if fila == fila_fecha:
            c.fill = RELLENO_FILA_FECHA

    # --- Columnas de sesión (a partir de columna C) ---
    for idx, sesion in enumerate(sesiones):
        col = 3 + idx  # C=3

        c_num = ws.cell(row=fila_num, column=col, value=idx + 1)
        c_num.font = FUENTE_ENCABEZADO

        fecha = parsear_fecha(sesion.get("Fecha"))
        c_fecha = ws.cell(row=fila_fecha, column=col, value=fecha)
        c_fecha.font = FUENTE_ENCABEZADO
        c_fecha.fill = RELLENO_FILA_FECHA
        if isinstance(fecha, datetime):
            c_fecha.number_format = FORMATO_FECHA

        c_tipo = ws.cell(row=fila_tipo_sesion, column=col, value=sesion.get("Sesión"))
        c_tipo.font = FUENTE_NORMAL

        c_periodo = ws.cell(row=fila_periodo, column=col, value=sesion.get("Periodo"))
        c_periodo.font = FUENTE_NORMAL

    # --- Filas de diputados ---
    mapas = [mapa_asistencias_por_sesion(s) for s in sesiones]
    fila_actual = fila_primer_diputado
    for i, nombre in enumerate(diputados):
        c_nombre = ws.cell(row=fila_actual, column=2, value=nombre)
        c_nombre.font = FUENTE_NORMAL

        for idx, mapa in enumerate(mapas):
            col = 3 + idx
            valor = mapa.get(nombre, "-")  # '-' = no participaba / ya no era diputado
            c_valor = ws.cell(row=fila_actual, column=col, value=valor)
            c_valor.font = FUENTE_NORMAL

        fila_actual += 1

    fila_ultimo_diputado = fila_actual - 1

    # --- Fila(s) de TOTALES por sesión (una fila por categoría, con etiqueta gris) ---
    fila_totales_inicio = fila_actual + 1  # una fila en blanco antes de los totales
    for cat_idx, categoria in enumerate(CATEGORIAS_TOTALES):
        fila_cat = fila_totales_inicio + cat_idx

        c_label = ws.cell(row=fila_cat, column=2, value=f"Totales - {categoria}")
        c_label.font = FUENTE_ETIQUETA_TOTALES
        c_label.fill = RELLENO_TOTALES

        for idx, sesion in enumerate(sesiones):
            col = 3 + idx
            valor = sesion.get("Totales", {}).get(categoria)
            c_val = ws.cell(row=fila_cat, column=col, value=valor)
            c_val.font = FUENTE_NORMAL
            c_val.fill = RELLENO_TOTALES

    fila_fin_bloque = fila_totales_inicio + len(CATEGORIAS_TOTALES)

    return fila_fin_bloque + 1  # deja una fila en blanco de separación


# ---------------------------------------------------------------------------
# Hoja Claves
# ---------------------------------------------------------------------------
def escribir_hoja_claves(wb):
    ws = wb.create_sheet("Claves")
    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 30
    for i, (clave, descripcion) in enumerate(CLAVES, start=1):
        c_a = ws.cell(row=i, column=1, value=clave)
        c_b = ws.cell(row=i, column=2, value=descripcion)
        c_a.font = FUENTE_NORMAL
        c_b.font = FUENTE_NORMAL


# ---------------------------------------------------------------------------
# Autoajuste de anchos
# ---------------------------------------------------------------------------
def autoajustar_anchos(ws, ultima_col: int, ultima_fila: int) -> None:
    for col_idx in range(2, ultima_col + 1):  # desde B; A se deja fija (num diputado, si aplica)
        letra = get_column_letter(col_idx)
        max_len = 0
        for fila_idx in range(1, ultima_fila + 1):
            valor = ws.cell(row=fila_idx, column=col_idx).value
            if valor is not None:
                largo = len(str(valor))
                if largo > max_len:
                    max_len = largo
        ancho = max(ANCHO_MINIMO, min(max_len + 2, ANCHO_MAXIMO))
        ws.column_dimensions[letra].width = ancho
    ws.column_dimensions["A"].width = 4.5


# ---------------------------------------------------------------------------
# Generación del Excel completo
# ---------------------------------------------------------------------------
def generar_excel(registros: list[dict], ruta_salida: str) -> None:
    normales, permanente = separar_bloques(registros)

    wb = Workbook()
    ws = wb.active
    ws.title = "Hoja 1"
    ws.cell(row=1, column=1, value=" ")

    fila_libre = 1
    fila_libre = escribir_bloque(ws, fila_libre, "Ordinarias / Extraordinarias / Solemnes",
                                  RELLENO_TITULO_BLOQUE1, normales)

    if permanente:
        fila_libre = escribir_bloque(ws, fila_libre, "Permanente",
                                      RELLENO_TITULO_BLOQUE2, permanente)

    ultima_fila = fila_libre
    max_sesiones = max(len(normales), len(permanente)) if permanente else len(normales)
    ultima_col = 2 + max_sesiones  # B + n sesiones

    autoajustar_anchos(ws, ultima_col, ultima_fila)
    ws.freeze_panes = "C2"

    escribir_hoja_claves(wb)

    wb.save(ruta_salida)
    print(f"Excel generado: {ruta_salida}")
    print(f"Sesiones bloque normal: {len(normales)}")
    print(f"Sesiones bloque Permanente: {len(permanente)}")
    print(f"Diputados bloque normal: {len(construir_orden_diputados(normales))}")
    print(f"Diputados bloque Permanente: {len(construir_orden_diputados(permanente))}")


def main():
    ruta_json = "../data/asistencias.json"
    ruta_salida = "../outputs/Asistencias.xlsx"

    if not os.path.exists(ruta_json):
        print(f"No se encontró el archivo: {ruta_json}")
        sys.exit(1)

    registros = cargar_registros(ruta_json)
    generar_excel(registros, ruta_salida)


if __name__ == "__main__":
    main()