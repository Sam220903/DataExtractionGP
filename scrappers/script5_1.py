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
from lib.DeputyDataExtractor import (
    extractGenderFromCandidates,
    getPartyAbbreviationFromFileName,
    buildCommitteeMembersData,
)

# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
comissions_url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=369'
commitees_url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=409'
browser = "edge"
parser = "html.parser"

dh = DateHandler()

data = []

# ----------------------------------------- Extracción de datos de comisiones -----------------------------------------
print("Accediendo a la página de comisiones")

response = requests.get(comissions_url, impersonate=browser)

# Iniciar un contador de registros
total_records = 0

if response.status_code == 200:

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

        commision_response = requests.get(commision_link, impersonate=browser)

        if commision_response.status_code == 200:
            commision_soup = BeautifulSoup(commision_response.text, parser)

            info_divs = commision_soup.find('div', class_="contenedorGeneralComision").find_all('div', recursive=False)
            name = info_divs[0].find('h1').text.strip()

            work_plan_li = info_divs[4].find('fieldset').find('ul').find('li')
            work_plan_a = work_plan_li.find('a') if work_plan_li else None
            work_plan_link = f'{domain}{work_plan_a['href']}' if work_plan_a else None

            annual_reports = info_divs[5].find('fieldset').find('ul').find_all('li', recursive=False)
            report_1 = f'{domain}{annual_reports[0].find('a')['href']}' if annual_reports[0].find('a') else None
            report_2 = f'{domain}{annual_reports[1].find('a')['href']}' if annual_reports[1].find('a') else None
            report_3 = f'{domain}{annual_reports[2].find('a')['href']}' if annual_reports[2].find('a') else None

            sessions_info_container = commision_soup.find('div', class_="contenedorGeneralComision").find('section', id="contenedorBotoneraPorCategorias", recursive=False)
            sessions_dates_divs = sessions_info_container.find_all('div', class_="tituloFechaSesion")
            sessions_dates = [session_date_div.text.strip() for session_date_div in sessions_dates_divs]

            instalation_date = dh.getOldestDateFromTexts(sessions_dates)

            # ------------------- Extracción de datos de los integrantes -------------------
            members_container = info_divs[2].find('div', class_="contenedorFichaDips")
            members = members_container.find_all('div', recursive=False)

            members_raw_data = []

            for member in members:
                name_div = member.find('div', class_="nombreDipCV")
                role_div = member.find('div', class_="textoCargoComision")
                image = member.find('img')
                logo_image = member.find(
                    lambda tag: tag.name in ("amp-img", "img") and "logos/" in tag.get("src", "")
                )

                member_name = name_div.text.strip()
                member_role = role_div.text.strip()

                gender_candidate_texts = [
                    member.get("aria-label", ""),
                    image.get("title", "") if image else "",
                    image.get("alt", "") if image else "",
                ]
                member_gender = extractGenderFromCandidates(gender_candidate_texts)

                logo_file_name = os.path.basename(logo_image["src"])
                member_party = getPartyAbbreviationFromFileName(logo_file_name)

                members_raw_data.append({
                    "name": member_name,
                    "role": member_role,
                    "gender": member_gender,
                    "party": member_party,
                })

            members_data = buildCommitteeMembersData(members_raw_data)

            commision_data = {
                "Nombre": name,
                "Plan de trabajo": work_plan_link,
                "Informe anual primer año": report_1,
                "Informe anual segundo año": report_2,
                "Informe anual tercer año": report_3,
                "Fecha de instalación": instalation_date,
                **members_data,
                "Periodo": "",
                "Comisión / Comité": "Comisión",
            }

            data.append(commision_data)
            total_records += 1


#  ------------------------------------- Extracción de datos de comités ---------------------------------------
print("Accediendo a la página de comités")

response = requests.get(commitees_url, impersonate=browser)

# Iniciar un contador de registros
total_records = 0

if response.status_code == 200:

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

        commitee_response = requests.get(commitee_link, impersonate=browser)

        if commitee_response.status_code == 200:
            commitee_soup = BeautifulSoup(commitee_response.text, parser)

            # La página de comités tiene una estructura distinta a la de comisiones:
            # contenedorGeneralComision solo tiene 3 divs directos, y el plan de
            # trabajo, los informes anuales y las sesiones están anidados dentro
            # de info_divs[2], en un <section class="content">.
            info_divs = commitee_soup.find('div', class_="contenedorGeneralComision").find_all('div', recursive=False)
            name = info_divs[0].find('h1').text.strip()

            content_section = info_divs[2].find('section', class_="content")
            content_divs = content_section.find_all('div', recursive=False)

            work_plan_li = content_divs[1].find('fieldset').find('ul').find('li')
            work_plan_a = work_plan_li.find('a') if work_plan_li else None
            work_plan_link = f'{domain}{work_plan_a['href']}' if work_plan_a else None

            annual_reports = content_divs[2].find('fieldset').find('ul').find_all('li', recursive=False)
            report_1 = f'{domain}{annual_reports[0].find('a')['href']}' if annual_reports[0].find('a') else None
            report_2 = f'{domain}{annual_reports[1].find('a')['href']}' if annual_reports[1].find('a') else None
            report_3 = f'{domain}{annual_reports[2].find('a')['href']}' if annual_reports[2].find('a') else None

            sessions_info_container = info_divs[2].find('section', class_="content").find('section', id="contenedorBotoneraPorCategorias", recursive=False)
            sessions_dates_divs = sessions_info_container.find_all('div', class_="tituloFechaSesion")
            sessions_dates = [session_date_div.text.strip() for session_date_div in sessions_dates_divs]

            instalation_date = dh.getOldestDateFromTexts(sessions_dates)

            # ------------------- Extracción de datos de los integrantes -------------------
            members_container = info_divs[2].find('div', class_="contenedorFichaDips")
            members = members_container.find_all('div', recursive=False)

            members_raw_data = []

            for member in members:
                name_div = member.find('div', class_="nombreDipCV")
                role_div = member.find('div', class_="textoCargoComision")
                image = member.find('img')
                logo_image = member.find(
                    lambda tag: tag.name in ("amp-img", "img") and "logos/" in tag.get("src", "")
                )

                member_name = name_div.text.strip()
                member_role = role_div.text.strip()

                gender_candidate_texts = [
                    member.get("aria-label", ""),
                    image.get("title", "") if image else "",
                    image.get("alt", "") if image else "",
                ]
                member_gender = extractGenderFromCandidates(gender_candidate_texts)

                logo_file_name = os.path.basename(logo_image["src"])
                member_party = getPartyAbbreviationFromFileName(logo_file_name)

                members_raw_data.append({
                    "name": member_name,
                    "role": member_role,
                    "gender": member_gender,
                    "party": member_party,
                })

            members_data = buildCommitteeMembersData(members_raw_data)

            commitee_data = {
                "Nombre": name,
                "Plan de trabajo": work_plan_link,
                "Informe anual primer año": report_1,
                "Informe anual segundo año": report_2,
                "Informe anual tercer año": report_3,
                "Fecha de instalación": instalation_date,
                **members_data,
                "Periodo": "",
                "Comisión / Comité": "Comité",
            }

            data.append(commitee_data)
            total_records += 1


# ----------------------------------------------- Guardar resultados -----------------------------------------------
folder = "data"
os.makedirs(folder, exist_ok=True)
ruta_archivo = os.path.join(folder, "comisiones_integrantes.json")
with open(ruta_archivo, 'w', encoding='utf-8') as output_file:
    json.dump(data, output_file, ensure_ascii=False, indent=4)

print(f"\nProceso terminado. Registros guardados: {len(data)}")