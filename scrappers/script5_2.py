import sys
import os
# Añadimos la carpeta principal al path para poder importar desde lib/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import time
import unicodedata
from curl_cffi import requests
from bs4 import BeautifulSoup

from lib.DateHandler import DateHandler
from lib.EventClassifier import EventClassifier


# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
comissions_url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=369'
commitees_url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=409'
browser = "edge"
parser = "html.parser"

# Parámetros de red: el sitio del congreso es lento y a veces corta la conexión
# a mitad de la descarga, así que usamos un timeout más amplio y reintentos.
REQUEST_TIMEOUT = 90      # segundos por intento (curl_cffi usa 30 por defecto)
MAX_RETRIES = 4           # intentos por URL
RETRY_BASE_DELAY = 3      # espera base entre intentos (backoff exponencial)
failed_urls = []          # URLs que no se pudieron descargar tras todos los intentos


def fetch(url, label=""):
    """
    GET con timeout amplio y reintentos con backoff exponencial.
    Regresa la respuesta si fue exitosa (status 200) o None si falló definitivamente,
    de modo que un fallo de red en una sola página no tumbe todo el script.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, impersonate=browser, timeout=REQUEST_TIMEOUT)
            if response is not None:
                return response
            print(f"  [!] {label or url}: HTTP {response.status_code} (intento {attempt}/{MAX_RETRIES})")
        except Exception as error:  # Timeout, CurlError, ConnectionError, etc.
            print(f"  [!] {label or url}: {type(error).__name__} (intento {attempt}/{MAX_RETRIES})")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BASE_DELAY * 2 ** (attempt - 1))

    failed_urls.append(label or url)
    return None

dh = DateHandler()
ec = EventClassifier()


def build_empty_year_structure():
    """
    Regresa la estructura vacía de conteo/clasificación de sesiones
    para un año legislativo (1, 2 o 3).
    """
    return {
        "sesiones_periodo_1 ": [],
        "sesiones_receso_1 ": [],
        "total_periodo_1": 0,
        "sesiones_periodo_2 ": [],
        "sesiones_receso_2 ": [],
        "total_periodo_2": 0,
        "sesiones_periodo_3 ": [],
        "sesiones_receso_3 ": [],
        "total_periodo_3": 0,
        "total_año": 0,
    }


def classify_sessions(session_texts):
    """
    Recibe la lista de fechas de sesión en texto (ej. "31 de agosto de 2026")
    y regresa el diccionario con sesiones_año_1/2/3, cada una con sus sesiones
    separadas por periodo y receso, y los totales correspondientes.
    """
    years_data = {
        "sesiones_año_1": build_empty_year_structure(),
        "sesiones_año_2": build_empty_year_structure(),
        "sesiones_año_3": build_empty_year_structure(),
    }

    for session_text in session_texts:
        day, month, year = dh.parseDate(session_text)
        iso_date = f"{year:04d}-{month:02d}-{day:02d}"
        final_date = dh.formatDateShort((day,month,year))

        legislative_year = ec.classify_per_year(iso_date)
        kind, period_number = ec.classify_period_or_recess(iso_date)

        year_key = f"sesiones_año_{legislative_year}"
        if year_key not in years_data:
            continue

        list_key = f"sesiones_{kind}_{period_number} "
        if list_key in years_data[year_key]:
            years_data[year_key][list_key].append(final_date)

    for year_data in years_data.values():
        total_year = 0
        for period_number in (1, 2, 3):
            total_period = (
                len(year_data[f"sesiones_periodo_{period_number} "])
                + len(year_data[f"sesiones_receso_{period_number} "])
            )
            year_data[f"total_periodo_{period_number}"] = total_period
            total_year += total_period
        year_data["total_año"] = total_year

    return years_data


data = []

# ----------------------------------------- Extracción de datos de comisiones -----------------------------------------
print("Accediendo a la página de comisiones")

response = fetch(comissions_url, "listado de comisiones")

# Iniciar un contador de registros
total_records = 0

if response is not None:

    # Conjunto para almacenar los ids ya procesados
    visited_ids = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Buscar el contenedor de comisiones
    commisions_container = soup.find('div', class_="contenedorComisiones")

    commisions = commisions_container.find_all('div', class_="tarjetaComision")

    # Iterar por cada comisión
    for commision in commisions:
        commision_a = commision.find('a')
        commision_title = commision_a.find('h3').find('p').text.strip()
        commision_link = f'{domain}{commision_a['href']}'

        print(f"\nProcesando la comisión: {commision_title}")

        commision_response = fetch(commision_link, commision_title)

        if commision_response is not None:
            commision_soup = BeautifulSoup(commision_response.text, parser)

            info_divs = commision_soup.find('div', class_="contenedorGeneralComision").find_all('div', recursive=False)
            name = info_divs[0].find('h1').text.strip()

            sessions_info_container = commision_soup.find('div', class_="contenedorGeneralComision").find('section', id="contenedorBotoneraPorCategorias", recursive=False)
            sessions_dates_divs = sessions_info_container.find_all('div', class_="tituloFechaSesion")
            sessions = []

            for session in sessions_dates_divs:
                sessions.append(session.text.strip())

            commission_data = {
                "Nombre": name,
                **classify_sessions(sessions),
            }

            data.append(commission_data)
            total_records += 1




#  ------------------------------------- Extracción de datos de comités ---------------------------------------
print("Accediendo a la página de comités")

response = fetch(commitees_url, "listado de comités")

# Iniciar un contador de registros
total_records = 0

if response is not None:

    # Conjunto para almacenar los ids ya procesados
    visited_ids = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Buscar el contenedor de comisiones
    commitees_container = soup.find('div', class_="itemListSubCategories")

    commitees = commitees_container.find_all('div', class_="fichaLegislador")

    # Iterar por cada comité
    for commitee in commitees:
        commitee_a = commitee.find('h4').find('a')
        commitee_title = commitee_a.text.strip()
        commitee_link = f'{domain}{commitee_a['href']}'

        print(f"\nProcesando el comité: {commitee_title}")

        commitee_response = fetch(commitee_link, commitee_title)

        if commitee_response is not None:
            commitee_soup = BeautifulSoup(commitee_response.text, parser)

            info_divs = commitee_soup.find('div', class_="contenedorGeneralComision").find_all('div', recursive=False)
            name = info_divs[0].find('h1').text.strip()

            sessions_info_container = info_divs[2].find('section', class_="content").find('section', id="contenedorBotoneraPorCategorias", recursive=False)
            sessions_dates_divs = sessions_info_container.find_all('div', class_="tituloFechaSesion")

            sessions = []

            for session in sessions_dates_divs:
                sessions.append(session.text.strip())

            commitee_data = {
                "Nombre": name,
                **classify_sessions(sessions),
            }

            data.append(commitee_data)
            total_records += 1


# ----------------------------------------------- Guardar resultados -----------------------------------------------
folder = "data"
os.makedirs(folder, exist_ok=True)
ruta_archivo = os.path.join(folder, "comisiones_sesiones.json")
with open(ruta_archivo, 'w', encoding='utf-8') as output_file:
    json.dump(data, output_file, ensure_ascii=False, indent=4)

print(f"\nProceso terminado. Registros guardados: {len(data)}")

if failed_urls:
    print(f"\n[!] No se pudieron descargar {len(failed_urls)} páginas tras {MAX_RETRIES} intentos:")
    for item in failed_urls:
        print(f"    - {item}")
    print("    Vuelve a ejecutar el script para completar esos registros.")