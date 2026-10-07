#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
script7.py
Arma el JSON principal (una fila por iniciativa / punto de acuerdo) a partir de:
  - unidad_participacion.json  (universo de filas)             <- Script 3 | Unidad y participación
  - comisiones_contenido.json  (comisiones, comités, sesiones) <- Script 5 | Comisiones y comités
  - perfiles.json              (diputados y partidos)          <- Script 1 | Perfiles

Uso:
    python script7.py [carpeta_con_los_json] [archivo_salida]
Por defecto: ./data  ->  ./data/iniciativas_pa.json
Si ./data no existe en el directorio actual, también se busca junto al script
(./data y ../data). Solo usa la librería estándar de Python.
"""
import argparse
import collections
import datetime
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ----------------------------------------------------------------- config
UMBRAL_CON_PISTA = 0.45   # similitud mínima si el presentador/título nombra la comisión
UMBRAL_SIN_PISTA = 0.65   # similitud mínima para comisiones no nombradas
MAX_C = 6                 # columnas C1..C6

CARPETA_DEFECTO = "./data"
SALIDA_NOMBRE = "iniciativas_pa.json"

# Archivos de entrada requeridos y el script que los genera
ARCHIVOS = {
    "unidad": ("unidad_participacion.json", "Script 3 | Unidad y participación"),
    "perfiles": ("perfiles.json", "Script 1 | Perfiles"),
    "comisiones": ("comisiones_contenido.json", "Script 5 | Comisiones y comités"),
}
# False: días de diferencia en valor absoluto. True: aprobación - presentación
# (negativo cuando la comisión aprobó antes de la fecha de la unidad de participación).
DIFERENCIA_CON_SIGNO = False
CADA_N = 100              # cada cuántos registros se imprime avance

STOP = set(
    "de la el los las en y a que por del al se para con un una su sus o e lo como es "
    "sea mediante virtud cual acuerdo punto exhorta presenta comision comisiones "
    "dictamen minuta decreto proyecto presentacion aprobacion congreso estado puebla "
    "honorable".split()
)
MESES = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}
ANIOS = {"primer": "Primero", "segundo": "Segundo", "tercer": "Tercero", "cuarto": "Cuarto"}
PARTIDO_ALIAS = {"PMC": "MC", "MOVIMIENTO CIUDADANO": "MC"}


# ----------------------------------------------------------------- utilidades
def norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def tokens(t):
    return [w for w in re.findall(r"[a-z0-9]+", norm(t)) if w not in STOP and len(w) > 2]


def fecha_texto(s):
    """'27 de agosto de 2026' / '04 de junio 2026' -> date"""
    m = re.match(r"\s*(\d{1,2}) de (\w+)(?: de)? (\d{4})", norm(s))
    if not m or m.group(2) not in MESES:
        return None
    return datetime.date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))


def fmt(d):
    return d.strftime("%d/%m/%y") if d else None


def limpiar_nombre(n):
    n = re.sub(r"^\s*(diputad[oa]s?|dip\.)\s+", "", n.strip(), flags=re.I)
    return norm(n).strip(" .")


# ----------------------------------------------------------------- entrada / validación
def buscar_archivo(carpeta: Path, nombre: str):
    """Ruta del archivo en la carpeta (sin distinguir mayúsculas), o None."""
    exacto = carpeta / nombre
    if exacto.is_file():
        return exacto
    if carpeta.is_dir():
        for f in carpeta.iterdir():
            if f.is_file() and f.name.casefold() == nombre.casefold():
                return f
    return None


def resolver_carpeta_datos(arg):
    """Carpeta con los JSON. Si se indica, se respeta tal cual. Si no, se prueba ./data
    (directorio actual) y luego ./data y ../data junto al script, y por último la carpeta
    actual y la del script, eligiendo la primera donde esté alguno de los JSON."""
    if arg:
        return Path(arg).expanduser().resolve()
    por_defecto = Path(CARPETA_DEFECTO).resolve()
    candidatas = [por_defecto, (BASE_DIR / "data").resolve(), (BASE_DIR / ".." / "data").resolve(),
                  Path.cwd(), BASE_DIR]
    for c in candidatas:
        if c.is_dir() and any(buscar_archivo(c, n) for n, _ in ARCHIVOS.values()):
            if c != por_defecto:
                print(f"  (No se encontraron los JSON en {por_defecto}; se usará {c})")
            return c
    return por_defecto


def cargar_json(ruta: Path):
    """Devuelve (registros, error). Acepta lista o {'registros': [...]}."""
    try:
        with open(ruta, encoding="utf-8-sig") as f:
            d = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"JSON mal formado ({e})"
    except OSError as e:
        return None, f"no se pudo leer ({e})"
    if isinstance(d, dict) and "registros" in d:
        d = d["registros"]
    if not isinstance(d, list):
        return None, "no contiene una lista de registros"
    if not d:
        return None, "está vacío (0 registros)"
    return d, None


def cargar_entradas(carpeta: Path, nombres: dict):
    """Verifica y carga los 3 JSON. Si falta alguno, avisa qué extracción hay que hacer."""
    print(f"[1/5] Verificando archivos de entrada en: {carpeta}")
    if not carpeta.is_dir():
        print(f"  ✗ La carpeta de datos no existe: {carpeta}")
    faltantes, datos = [], {}
    for clave, (nombre_def, script) in ARCHIVOS.items():
        nombre = nombres.get(clave) or nombre_def
        ruta = buscar_archivo(carpeta, nombre)
        if ruta is None:
            print(f"  ✗ {nombre}: NO ENCONTRADO")
            faltantes.append((nombre, script, "no existe"))
            continue
        registros, error = cargar_json(ruta)
        if error:
            print(f"  ✗ {ruta.name}: {error}")
            faltantes.append((nombre, script, error))
            continue
        print(f"  ✓ {ruta.name}: {len(registros)} registros")
        datos[clave] = registros
    if faltantes:
        print("\n" + "!" * 70)
        print("ALERTA: faltan datos para continuar.")
        for nombre, script, motivo in faltantes:
            print(f"  - Falta hacer la extracción de la base correspondiente a "
                  f"'{nombre}' ({motivo}): {script}")
        print("!" * 70)
        return None
    return datos


# ----------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser(description="Arma el JSON principal de iniciativas y puntos de acuerdo.")
    ap.add_argument("carpeta", nargs="?", default=None, help=f"carpeta de los JSON de entrada (por defecto {CARPETA_DEFECTO})")
    ap.add_argument("salida", nargs="?", default=None, help=f"archivo de salida (por defecto <carpeta>/{SALIDA_NOMBRE})")
    ap.add_argument("--unidad", default=None, help=f"nombre del JSON de unidad (por defecto {ARCHIVOS['unidad'][0]})")
    ap.add_argument("--perfiles", default=None, help=f"nombre del JSON de perfiles (por defecto {ARCHIVOS['perfiles'][0]})")
    ap.add_argument("--comisiones", default=None, help=f"nombre del JSON de comisiones (por defecto {ARCHIVOS['comisiones'][0]})")
    args = ap.parse_args()

    data_dir = resolver_carpeta_datos(args.carpeta)
    # salida: la indicada; si es una carpeta existente se usa el nombre por defecto dentro de ella
    destino = Path(args.salida).expanduser().resolve() if args.salida else data_dir / SALIDA_NOMBRE
    if destino.is_dir():
        destino = destino / SALIDA_NOMBRE

    datos = cargar_entradas(data_dir, {"unidad": args.unidad, "perfiles": args.perfiles,
                                       "comisiones": args.comisiones})
    if datos is None:
        print("\nNo se generó ningún archivo.")
        return 1
    unidad, perfiles, comisiones = datos["unidad"], datos["perfiles"], datos["comisiones"]

    # --- perfiles
    print("\n[2/5] Procesando perfiles (diputados y partidos)...")
    partido_de = {limpiar_nombre(p["Nombre"]): PARTIDO_ALIAS.get(p["Partido"], p["Partido"])
                  for p in perfiles if p.get("Nombre")}
    print(f"  {len(partido_de)} diputados con partido")

    # --- sesiones
    print("\n[3/5] Procesando comisiones y comités...")
    sesiones = []
    sin_fecha = 0
    for com in comisiones:
        for s in com.get("Sesiones", []):
            fecha = fecha_texto(s.get("Fecha de sesión en comisión", ""))
            if fecha is None and s.get("Fecha de sesión en comisión / comité"):
                try:
                    fecha = datetime.date.fromisoformat(s["Fecha de sesión en comisión / comité"])
                except ValueError:
                    fecha = None
            if fecha is None:
                sin_fecha += 1
            sesiones.append({"com": com["Nombre"], "s": s, "fecha": fecha,
                             "t": set(tokens(s.get("Asunto abordado", "")))})
        print(f"  · {com['Nombre']}: {len(com.get('Sesiones', []))} sesiones")
    print(f"  Total: {len(sesiones)} sesiones en {len(comisiones)} comisiones/comités"
          + (f" ({sin_fecha} sin fecha interpretable, se ignoran)" if sin_fecha else ""))

    df = collections.Counter()
    for e in sesiones:
        df.update(e["t"])
    N = len(sesiones)

    def idf(w):
        return math.log((N + 1) / (df.get(w, 0) + 1)) + 0.5

    def similitud(titulo, toks_sesion):
        """Fracción (ponderada por idf) de las palabras del título que aparecen en la sesión."""
        tt = set(tokens(titulo))
        if not tt:
            return 0.0
        return sum(idf(w) for w in tt & toks_sesion) / sum(idf(w) for w in tt)

    nombres_com = {c["Nombre"]: norm(c["Nombre"]) for c in comisiones}

    def comisiones_pista(x):
        txt = norm((x.get("Presentador") or "") + " " + (x.get("Título") or ""))
        return [n for n, k in nombres_com.items()
                if (f"comision de {k}" in txt or f"comite de {k}" in txt or f"y la de {k}" in txt
                    or f"comision de la {k}" in txt or f"comision del {k}" in txt)]

    def partidos_de(presentador, partido_sesion):
        if presentador:
            res = []
            for n in re.split(r",| y (?=[A-ZÁÉÍÓÚÑ])", presentador):
                p = partido_de.get(limpiar_nombre(n))
                if p and p not in res:
                    res.append(p)
            if res:
                return ", ".join(res)
        if partido_sesion and partido_sesion not in ("N/A", "null"):
            return PARTIDO_ALIAS.get(partido_sesion, partido_sesion)
        return None

    # --- unidad
    total = len(unidad)
    print(f"\n[4/5] Analizando {total} iniciativas y puntos de acuerdo...")
    salida = []
    for i, x in enumerate(unidad):
        if i % CADA_N == 0:
            print(f"  Analizando registro {i + 1} de {total} ({100 * i // total}%)...")
        try:
            f_pres = datetime.date.fromisoformat(x["Fecha"])
        except (KeyError, TypeError, ValueError):
            print(f"  ⚠ Registro {i + 2}: fecha inválida {x.get('Fecha')!r}; se omite")
            continue
        titulo = x.get("Título") or ""
        pistas = comisiones_pista(x)

        mejor = {}
        for e in sesiones:
            if e["fecha"] is None or e["fecha"] > f_pres:
                continue
            sc = similitud(titulo, e["t"])
            clave = (round(sc, 3), e["fecha"])  # empate -> sesión más reciente
            if e["com"] not in mejor or clave > mejor[e["com"]][0]:
                mejor[e["com"]] = (clave, sc, e)

        elegidas = []
        for com, (_, sc, e) in mejor.items():
            if sc >= (UMBRAL_CON_PISTA if com in pistas else UMBRAL_SIN_PISTA):
                elegidas.append((com in pistas, sc, e))
        elegidas.sort(key=lambda z: (not z[0], -z[1]))
        elegidas = elegidas[:MAX_C]

        fila = {"Número": i + 2,  # fila de Excel (encabezado = 1)
                "Tipo": x.get("Tipo"), "Descripción": titulo, "FechaPres": fmt(f_pres)}

        pres, part_ses = None, None
        for _, _, e in elegidas:
            if e["s"].get("Presentador"):
                pres, part_ses = e["s"]["Presentador"], e["s"].get("Partido")
                break
        if not pres:
            pres = x.get("Presentador")
        fila["Presentador"] = pres
        fila["Partido"] = partidos_de(pres, part_ses)
        fila["Tema"] = x.get("Tema 1")

        for k in range(MAX_C):
            fila[f"C{k + 1}"] = elegidas[k][2]["com"] if k < len(elegidas) else ("N/A" if k == 0 else None)

        aprob = next((e for _, _, e in elegidas if e["s"].get("Estatus") == "Aprobado"), None)
        if aprob:
            f_apr, fila["Estatus"] = aprob["fecha"], "Aprobada"
        elif elegidas:
            f_apr = None
            est = elegidas[0][2]["s"].get("Estatus")
            fila["Estatus"] = {"Aprobado": "Aprobada", "No aprobado": "No aprobada"}.get(est, est) or "N/A"
        else:
            # sin comisión: aprobado directo en el pleno (p.ej. Mesa Directiva)
            f_apr = f_pres if (x.get("A favor") or 0) > (x.get("Contra") or 0) else None
            fila["Estatus"] = "Aprobada" if f_apr else "N/A"

        fila["Fecha de aprobación"] = fmt(f_apr)
        if f_apr:
            dif = (f_apr - f_pres).days
            fila["Diferencia de días entre fecha de presentación y aprobación"] = dif if DIFERENCIA_CON_SIGNO else abs(dif)
        else:
            fila["Diferencia de días entre fecha de presentación y aprobación"] = None

        # Año legislativo: el de la fuente; si contradice la fecha (el año inicia el 15/09), manda la fecha
        n_ano = max(0, min(2, f_pres.year - 2024 if (f_pres.month, f_pres.day) >= (9, 15) else f_pres.year - 2025))
        por_fecha = ["Primero", "Segundo", "Tercero"][n_ano]
        ano = norm(x.get("Año Legislatura", "")).split()
        fila["Año"] = ANIOS[ano[0]] if ano and ANIOS.get(ano[0]) == por_fecha else por_fecha
        fila["Contenido"] = x.get("Contenido", "")
        fila["Comentario"] = x.get("Comentario", "")
        fila["F"] = x.get("A favor")
        fila["C"] = x.get("Contra")
        fila["A"] = x.get("Abstenciones")
        salida.append(fila)
    print(f"  Analizando registro {total} de {total} (100%)")

    # --- escritura
    print("\n[5/5] Guardando resultado...")
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(destino.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, indent=4)
        os.replace(tmp, destino)
    except PermissionError:
        print(f"  ✗ NO SE PUDO GUARDAR: sin permiso o el archivo está abierto en otro programa.\n  Ruta: {destino}")
        return 2
    except OSError as e:
        print(f"  ✗ NO SE PUDO GUARDAR: {e}\n  Ruta: {destino}")
        return 2

    print("\n" + "=" * 70)
    print(f"Archivo generado: {destino}")
    print(f"  {len(salida)} registros | con comisión: {sum(1 for r in salida if r['C1'] != 'N/A')}"
          f" | sin comisión (N/A): {sum(1 for r in salida if r['C1'] == 'N/A')}")
    print("  Estatus:", dict(collections.Counter(r["Estatus"] for r in salida)))
    print("  Sin partido:", sum(1 for r in salida if not r["Partido"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())