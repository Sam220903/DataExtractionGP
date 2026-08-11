"""
Extractor de ASISTENCIAS de diputados - Congreso de Puebla (LXII Legislatura)

Flujo:
  1. Se accede a la pantalla principal de Asistencias (listado de sesiones).
  2. Por cada sesión listada, se obtiene el enlace a su página de detalle
     (id=12716&d=N) y se visita.
  3. En el detalle se extraen: Fecha, Tipo de Sesión y la lista completa de
     diputados con su tipo de asistencia (tabla 'tablaAsistencia').
  4. Se arma el JSON final con la estructura solicitada.
"""

import sys
import os
# Añadimos la carpeta principal al path para poder importar desde lib/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import time
import unicodedata
from curl_cffi import requests
from bs4 import BeautifulSoup
from lib.EventClassifier import EventClassifier
from lib.FolioManager import FolioManager


# ---------------------------------------------------------------------------
# IMPORTANTE: NO se reutiliza lib/DateFormatter.py porque ese archivo ya es
# usado por otro script (votaciones.py) con su lógica original (año recortado
# a 2 dígitos). Para no romper esa lógica ajena, se define aquí una clase
# propia y local para este extractor, con el año completo (dd/mm/aaaa).
# ---------------------------------------------------------------------------
class AsistenciasDateFormatter:

    MESES = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }

    def _parse(self, date: str):
        # "24 de Julio de 2026" -> ['24', 'de', 'julio', 'de', '2026']
        partes = date.lower().split()
        if len(partes) >= 5:
            dia = partes[0].zfill(2)
            mes = self.MESES.get(partes[2], "00")
            anio = partes[4]  # año completo (4 dígitos)
            return dia, mes, anio
        return None

    def format(self, date: str) -> str:
        try:
            parsed = self._parse(date)
            if parsed:
                dia, mes, anio = parsed
                return f"{dia}/{mes}/{anio}"
            return date
        except Exception:
            return date

    def to_iso(self, date: str) -> str:
        """
        Devuelve la fecha en formato 'YYYY-MM-DD', que es uno de los dos
        formatos que aceptan EventClassifier y FolioManager.
        """
        try:
            parsed = self._parse(date)
            if parsed:
                dia, mes, anio = parsed
                return f"{anio}-{mes}-{dia}"
            return date
        except Exception:
            return date


# ---------------------------------------------------------------------------
# Clasificación del tipo de asistencia a su clave numérica
# ---------------------------------------------------------------------------
ASISTENCIA_CODIGOS = {
    "asistencia": 1,
    "retardo justificado": 2,
    "retardo injustificado": 3,
    "inasistencia justificada": 4,
    "inasistencia injustificada": 5,
    "con licencia": 6,
    "sin informacion": 7,
    "fallecimiento": 8,
    "ya no es diputado": "-",
}


def clasificar_asistencia(texto: str):
    # Normaliza: minúsculas y sin acentos, para no depender de la
    # capitalización/acentuación exacta que traiga la página
    limpio = texto.lower()
    limpio = ''.join(
        c for c in unicodedata.normalize('NFD', limpio)
        if unicodedata.category(c) != 'Mn'
    )
    limpio = limpio.strip()

    if limpio in ASISTENCIA_CODIGOS:
        return ASISTENCIA_CODIGOS[limpio]

    # Si aparece un texto no contemplado en el diccionario, se conserva
    # el texto original para no perder el dato, y se avisa en consola
    print(f"  [AVISO] Tipo de asistencia no reconocido: '{texto}'")
    return texto


# Mapeo inverso (código -> nombre) para poder sumar por categoría.
# No incluye "-" (ya no es diputado) porque no está en la lista de totales pedida.
CODIGO_A_NOMBRE = {
    1: "Asistencia",
    2: "Retardo Justificado",
    3: "Retardo Injustificado",
    4: "Inasistencia Justificada",
    5: "Inasistencia Injustificada",
    6: "Con Licencia",
    7: "Sin información",
    8: "Fallecimiento",
}


def sumar_totales_por_tipo(asistencias: list) -> dict:
    totales = {nombre: 0 for nombre in CODIGO_A_NOMBRE.values()}
    for registro_asistencia in asistencias:
        nombre = CODIGO_A_NOMBRE.get(registro_asistencia["Asistencia"])
        if nombre:
            totales[nombre] += 1
    return totales


# ---------------------------------------------------------------------------
# Parámetros iniciales
# ---------------------------------------------------------------------------
domain = "https://www.congresopuebla.gob.mx"
url_listado = f"{domain}/index.php?option=com_content&view=article&id=12715"
browser = "edge"
parser = "html.parser"

date_formatter = AsistenciasDateFormatter()
event_classifier = EventClassifier()
folio_manager = FolioManager()

print("Accediendo a la página de asistencias")
response = requests.get(url_listado, impersonate=browser)

data = {"registros": []}
total_sesiones = 0

if response.status_code == 200:
    soup = BeautifulSoup(response.text, parser)

    # Tabla principal del listado (contiene un enlace por sesión)
    tabla_listado = soup.find("table", class_="tablaAsistencia")

    if not tabla_listado:
        print("No se encontró la tabla de listado de sesiones.")
    else:
        filas = tabla_listado.find("tbody").find_all("tr")

        # El listado del sitio viene de la sesión más reciente a la más
        # antigua. FolioManager necesita recibir las sesiones en orden
        # cronológico ascendente para calcular bien los folios (compara
        # cada registro contra el "estado" del registro anterior), así
        # que invertimos el orden antes de procesar.
        filas = list(reversed(filas))
        visited_links = set()

        for fila in filas:
            link_tag = fila.find("a")

            # Validación: si la fila no trae enlace a la sesión, se ignora
            if not link_tag or "href" not in link_tag.attrs:
                continue

            session_link = f"{domain}{link_tag['href']}"

            if session_link in visited_links:
                continue
            visited_links.add(session_link)

            print(f"\nProcesando sesión: {session_link}")
            session_response = requests.get(session_link, impersonate=browser)

            if session_response.status_code != 200:
                print(f"  Error accediendo a la sesión: {session_response.status_code}")
                continue

            session_soup = BeautifulSoup(session_response.text, parser)

            # --- Información general de la sesión (Fecha / Tipo de Sesión) ---
            info_sesion = session_soup.find("div", class_="informacion-sesion")

            fecha_raw = None
            tipo_sesion = None

            if info_sesion:
                parrafos = info_sesion.find_all("p")
                # ps[0] = título "Información de la Sesión"
                # ps[1] = "Fecha: ..."
                # ps[2] = "Tipo de Sesión: ..."
                if len(parrafos) >= 3:
                    fecha_strong = parrafos[1].find("strong")
                    tipo_strong = parrafos[2].find("strong")

                    if fecha_strong and fecha_strong.next_sibling:
                        fecha_raw = fecha_strong.next_sibling.strip()
                    if tipo_strong and tipo_strong.next_sibling:
                        tipo_sesion = tipo_strong.next_sibling.strip()

            fecha_formateada = date_formatter.format(fecha_raw) if fecha_raw else None
            fecha_iso = date_formatter.to_iso(fecha_raw) if fecha_raw else None

            # --- Tabla de asistencias de diputados ---
            tabla_asistencias = session_soup.find("table", class_="tablaAsistencia")
            asistencias = []

            if tabla_asistencias:
                tbody = tabla_asistencias.find("tbody")
                filas_dip = tbody.find_all("tr") if tbody else []

                for fila_dip in filas_dip:
                    celdas = fila_dip.find_all("td")

                    # Validación: se esperan 4 columnas (#, Partido, Diputado, Tipo)
                    if len(celdas) < 4:
                        continue

                    # El nombre del diputado queda como texto tras el <br> del <img>
                    nombre_diputado = celdas[2].get_text(strip=True)
                    tipo_asistencia_raw = celdas[3].get_text(strip=True)
                    codigo_asistencia = clasificar_asistencia(tipo_asistencia_raw)

                    asistencias.append({
                        "Diputado": nombre_diputado,
                        "Asistencia": codigo_asistencia
                    })

            total_sesiones += 1

            # Folios: se calculan por cada sesión (registro), usando el año
            # legislativo y periodo que ya determina EventClassifier
            if fecha_iso:
                legislative_year = event_classifier.classify_per_year(fecha_iso)
                period = event_classifier.classify_per_period(fecha_iso)
                folio_legislatura, folio_periodo = folio_manager.generate_folios(
                    fecha_iso, legislative_year, period
                )
            else:
                legislative_year, period = None, None
                folio_legislatura, folio_periodo = None, None

            totales_por_tipo = sumar_totales_por_tipo(asistencias)

            registro = {
                "Folio legislatura": folio_legislatura,
                "Folio periodo": folio_periodo,
                "Año Legislatura": legislative_year,
                "Periodo": period,
                "Fecha": fecha_formateada,
                "Sesión": tipo_sesion,
                "Asistencias": asistencias,
                "Totales": totales_por_tipo
            }

            data["registros"].append(registro)
            print(f"  Sesión '{tipo_sesion}' del {fecha_formateada}: "
                  f"{len(asistencias)} diputados registrados")

            # Pausa ética para no saturar el servidor del Congreso
            time.sleep(1)

    # --- Guardado del archivo ---
    folder = "data"
    os.makedirs(folder, exist_ok=True)
    ruta_archivo = os.path.join(folder, "asistencias.json")

    with open(ruta_archivo, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=5)

    print("\n¡Archivo JSON de asistencias guardado con éxito!")
    print(f"Sesiones extraídas: {total_sesiones}")

else:
    print(f"Error accediendo a la página inicial: {response.status_code}")