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

import os
import json
import time
from curl_cffi import requests
from bs4 import BeautifulSoup


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

    def format(self, date: str) -> str:
        try:
            # "24 de Julio de 2026" -> ['24', 'de', 'julio', 'de', '2026']
            partes = date.lower().split()
            if len(partes) >= 5:
                dia = partes[0].zfill(2)
                mes = self.MESES.get(partes[2], "00")
                anio = partes[4]  # año completo (4 dígitos), sin truncar
                return f"{dia}/{mes}/{anio}"
            return date
        except Exception:
            return date


# ---------------------------------------------------------------------------
# Parámetros iniciales
# ---------------------------------------------------------------------------
domain = "https://www.congresopuebla.gob.mx"
url_listado = f"{domain}/index.php?option=com_content&view=article&id=12715"
browser = "edge"
parser = "html.parser"

date_formatter = AsistenciasDateFormatter()

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
                    tipo_asistencia = celdas[3].get_text(strip=True)

                    asistencias.append({
                        "Diputado": nombre_diputado,
                        "Asistencia": tipo_asistencia
                    })

            total_sesiones += 1

            registro = {
                "No. de sesión": total_sesiones,
                "Fecha": fecha_formateada,
                "Sesión": tipo_sesion,
                "Asistencias": asistencias,
                "Total de asistencias": len(asistencias)
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