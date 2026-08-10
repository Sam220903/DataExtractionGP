import sys
import os
# Le decimos a Python que añada la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import re
import json
import time  # Añadido para la pausa ética
from curl_cffi import requests
from bs4 import BeautifulSoup

# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
listing_url = f'{domain}/index.php?option=com_content&view=article&id=11527'
detail_endpoint = f'{domain}/congreso/diputados/informacion.php'
browser = "edge"
parser = "html.parser"

# Catálogo reducido de grados de estudio, en orden de prioridad (el más alto encontrado gana)
degree_levels = [
    ('Doctorado', r'doctor(?:ado)?\b'),
    ('Maestría', r'maestr[íi]a\b'),
    ('Licenciatura', r'licenciad[oa]\b|licenciatura'),
    ('Ingeniería', r'ingenier[íi]a\b|ingenier[oa]\b'),
    ('Bachillerato', r'bachillerato|preparatoria'),
]

# Catálogo reducido de universidades públicas conocidas, para inferir "Privada o pública"
public_universities = [
    'unam', 'universidad nacional autónoma de méxico',
    'ipn', 'instituto politécnico nacional',
    'buap', 'benemérita universidad autónoma de puebla',
    'universidad veracruzana',
    'universidad autónoma de puebla',
]

# Palabras clave para inferir si hay experiencia legislativa previa
legislative_keywords = r'\b(diputad[oa]|senador|senadora|legislador|legisladora|regidor|regidora|síndic[oa])\b'

print("Accediendo a la página de perfiles")

response = requests.get(listing_url, impersonate=browser)

# Iniciar un contador de registros
total_records = 0

# Acceder a la pantalla principal del directorio
if response.status_code == 200:
    # Crear diccionario para contener los datos
    data = {
        "registros": []
    }

    # Crear un conjunto para contener los ids ya procesados y visitarlos una sola vez
    visited_ids = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Obtener la sección de "Mayoría Relativa"
    mr_section = soup.find('section', id='listaMayoriaRelativa').find('div', class_="contenedorFichaDips")

    # Obtener la sección de "Representación Proporcional"
    rp_section = soup.find('section', id="listaRepresentacionProporcional").find('div', class_="contenedorFichaDips")

    # Obtener todas las fichas de ambas secciones, junto con su tipo de elección
    mr_cards = mr_section.find_all('div', class_="fichaPerfil")
    rp_cards = rp_section.find_all('div', class_="fichaPerfil")
    all_cards = [(card, "Mayoría Relativa") for card in mr_cards] + [(card, "Representación Proporcional") for card in rp_cards]

    for card, election_type in all_cards:

        # Obtener el id del diputado desde el atributo onclick="informacionDip(701)"
        onclick = card.get('onclick', '')
        id_match = re.search(r'informacionDip\((\d+)\)', onclick)

        # Validación: Si la ficha no tiene id, se ignora
        if not id_match:
            continue
        member_id = int(id_match.group(1))

        # Verificar que no sea un id previamente visitado, en caso de serlo, ignorarlo
        if member_id in visited_ids:
            continue
        visited_ids.add(member_id)

        # Nombre completo
        name_tag = card.find('div', class_='nombreDipCV')
        full_name = name_tag.get_text(strip=True) if name_tag else None

        print(f"\nProcesando diputado {member_id}: {full_name}")

        # Partido (siglas, desde las clases de la ficha)
        party_abbreviation = None
        for class_name in card.get('class', []):
            if class_name not in ('infoFichaDipIndividualGral', 'fichaPerfil', 'DIPUTADAS', 'DIPUTADOS'):
                party_abbreviation = class_name
                break

        # Género, desde el div oculto 'sexo'
        gender = None
        gender_tag = card.find('div', class_='sexo')
        if gender_tag:
            gender_text = gender_tag.get_text(strip=True).lower()
            if 'femenino' in gender_text:
                gender = 'Femenino'
            elif 'masculino' in gender_text:
                gender = 'Masculino'

        # Distrito y cabecera distrital (solo aplica a Mayoría Relativa; si no existe, se marca N/A)
        district_number = 'N/A'
        district = 'N/A'
        district_container = card.find('div', class_='apartadoDipDistrito')
        if district_container:
            district_divs = district_container.find_all('div', class_='contenedorDistrito')
            if len(district_divs) >= 2:
                number_tag = district_divs[0].find('b')
                if number_tag:
                    number_text = number_tag.get_text(strip=True)
                    if number_text.isdigit() and int(number_text) != 0:
                        district_number = int(number_text)

                name_tag = district_divs[1].find('p')
                if name_tag and name_tag.get_text(strip=True):
                    district = name_tag.get_text(strip=True)

        # Correo y teléfono de oficina
        email = None
        office_phone = None
        contact_container = card.find('div', class_='contenedorCorreoElectronico')
        if contact_container:
            mail_link = contact_container.find('a', href=re.compile(r'^mailto:'))
            if mail_link:
                email = mail_link['href'].replace('mailto:', '').strip()

            phone_match = re.search(r'Tel\.[^<]*', contact_container.get_text(' ', strip=True))
            if phone_match:
                office_phone = phone_match.group(0).strip()

        # Redes sociales (Facebook, Twitter/X)
        facebook_url = None
        twitter_url = None
        social_container = card.find('div', class_='apartadoDipRedesSociales')
        if social_container:
            for link in social_container.find_all('a', href=True):
                icon = link.find('span')
                icon_classes = icon.get('class', []) if icon else []
                if 'icon-facebook' in icon_classes:
                    facebook_url = link['href']
                elif 'icon-equis' in icon_classes:
                    twitter_url = link['href']

        # Descargar la fotografía y guardarla en data/photos/{id}.jpg
        photo_tag = card.find('img')
        photo_source_url = f"{domain}{photo_tag['src']}" if photo_tag and photo_tag.get('src') else None
        photo_folder = os.path.join('data', 'photos')
        os.makedirs(photo_folder, exist_ok=True)

        if photo_source_url:
            photo_response = requests.get(photo_source_url, impersonate=browser)
            if photo_response.status_code == 200:
                with open(os.path.join(photo_folder, f'{member_id}.jpg'), 'wb') as photo_file:
                    photo_file.write(photo_response.content)

        photo_id = f'{member_id}.jpg'

        # Petición AJAX que reproduce el onclick="informacionDip(id)" de la página
        detail_response = requests.post(
            detail_endpoint,
            data={"diputad": member_id},
            impersonate=browser,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

        # Valores por defecto de los datos ampliados, en caso de que la petición falle
        constituency_office = None
        work_plan_url = None
        recess_memoirs = []
        academic_background = []
        work_experience = []

        if detail_response.status_code == 200:
            detail_soup = BeautifulSoup(detail_response.text, parser)

            # Casa de gestión
            contact_table = detail_soup.find('table', class_='tablasInformativaContacto')
            if contact_table:
                for row in contact_table.find_all('tr'):
                    if 'Casa de gestión' in row.get_text(' ', strip=True):
                        inner_table = row.find('table')
                        if inner_table:
                            inner_rows = inner_table.find_all('tr')
                            if len(inner_rows) >= 2:
                                constituency_office = inner_rows[1].find_all('td')[-1].get_text(' ', strip=True)

            # Currículum: formación académica y experiencia laboral
            curriculum_container = detail_soup.find('div', id='curriculo')
            if curriculum_container:
                for title_tag in curriculum_container.find_all('div', class_='tituloCurriculo'):
                    section_title = title_tag.get_text(strip=True).upper()
                    bullets_list = title_tag.find_next_sibling('ul')
                    bullets = [li.get_text(' ', strip=True) for li in bullets_list.find_all('li')] if bullets_list else []

                    if section_title == 'FORMACIÓN ACADÉMICA':
                        academic_background = bullets
                    elif section_title == 'EXPERIENCIA LABORAL':
                        work_experience = bullets

            # Plan de trabajo y memorias de receso
            work_info_container = detail_soup.find('div', id=f'divInformacion{member_id}')
            if work_info_container:
                document_list = work_info_container.find('ul', id='informes_memorias')
                if document_list:
                    for item in document_list.find_all('li', recursive=False):
                        link = item.find('a')
                        if link and 'plan de trabajo' in link.get_text(strip=True).lower():
                            work_plan_url = link['href']
                            break

                    for fieldset in document_list.find_all('fieldset', class_='fielSetInformesMemorias'):
                        legend_tag = fieldset.find('legend')
                        legend_text = legend_tag.get_text(strip=True) if legend_tag else ''
                        if legend_text.lower() != 'informes':
                            recess_memoirs.append(legend_text)

        else:
            print(f"  Error obteniendo el detalle del diputado {member_id}: {detail_response.status_code}")

        # Último grado de estudios, preparación, profesión y universidad,
        # inferidos a partir del texto libre de "Formación académica"
        education_level = 'Sin información'
        field_of_study = 'Sin información'
        profession = 'Sin información'
        university = 'Sin información'
        university_type = 'Sin información'

        selected_bullet = None
        for level_name, pattern in degree_levels:
            for bullet in academic_background:
                if re.search(pattern, bullet, re.IGNORECASE):
                    education_level = level_name
                    selected_bullet = bullet
                    break
            if selected_bullet:
                break

        if selected_bullet:
            if ':' in selected_bullet:
                university_part, degree_part = selected_bullet.split(':', 1)
                university = university_part.strip()
            else:
                degree_part = selected_bullet

            profession_match = re.search(
                r'((?:Licenciad[oa]|Ingenier[oa]|Doctor[oa]|Maestr[íi]a)[^.,;]*?en\s+[^.,;]+)',
                degree_part, re.IGNORECASE
            )
            if profession_match:
                profession = profession_match.group(1).strip()

            field_match = re.search(r'\ben\s+([^.,;]+)', degree_part, re.IGNORECASE)
            if field_match:
                field_of_study = field_match.group(1).strip()

            if university != 'Sin información':
                if any(keyword in university.lower() for keyword in public_universities):
                    university_type = 'Pública'
                else:
                    university_type = 'Privada'

        # Experiencia legislativa (Sí/No), inferida a partir de la experiencia laboral reportada
        has_legislative_experience = any(re.search(legislative_keywords, bullet, re.IGNORECASE) for bullet in work_experience)

        # Captura de todos los datos a exportar, limitados a los campos solicitados
        new_record = {
            "Nombre": full_name,
            "Partido": party_abbreviation,
            "Propietario o suplente": "Propietario",  # El directorio solo lista a quien ocupa la curul actualmente
            "Tipo de elección": election_type,
            "No. De Distrito": district_number,
            "Distrito": district,
            "Género": gender,
            "Folio foto": photo_id,
            "Plan de trabajo": "Sí" if work_plan_url else "No",
            "Experiencia legislativa": "Sí" if has_legislative_experience else "No",
            "Último grado de estudios": education_level if selected_bullet else 'Sin información',
            "Preparación": field_of_study,
            "Profesión": profession,
            "Universidad": university,
            "Privada o pública": university_type,
            "Teléfono de oficina": office_phone,
            "Correo": email,
            "Facebook": facebook_url,
            "Twitter": twitter_url,
            "Casa de gestión": constituency_office,
            "Año": None,     # Sin dato disponible por ahora
            "Periodo": None,  # Sin dato disponible por ahora
            "Memorias de receso": "Sí" if recess_memoirs else "No",
        }

        data["registros"].append(new_record)
        total_records += 1

        # Pausa ética entre peticiones
        time.sleep(1)

    folder = 'data'

    # Creamos la carpeta si no existe (exist_ok=True evita errores si ya fue creada antes)
    os.makedirs(folder, exist_ok=True)

    # Construimos la ruta segura cruzando plataformas (Windows usa '\', Mac/Linux usan '/')
    output_path = os.path.join(folder, 'perfiles.json')

    with open(output_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=5)

    print("\n¡Archivo JSON guardado con éxito!")
    print(f"\nRegistros extraídos: {total_records}")

else:
    print(f"Error accediendo a la página inicial: {response.status_code}")