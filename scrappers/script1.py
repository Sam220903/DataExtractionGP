import sys
import os
# Le decimos a Python que añada la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import re
import json
import time  # Pausa ética entre peticiones
import unicodedata
from curl_cffi import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Parámetros iniciales
# ---------------------------------------------------------------------------
domain = 'https://www.congresopuebla.gob.mx'
listing_url = f'{domain}/index.php?option=com_content&view=article&id=11527'
detail_endpoint = f'{domain}/congreso/diputados/informacion.php'
browser = "edge"
parser = "html.parser"

# Si es True, una línea como "Diputada Federal Suplente..." NO cuenta como experiencia legislativa
EXCLUDE_SUPLENTE_AS_EXPERIENCE = True

# ---------------------------------------------------------------------------
# Catálogos
# ---------------------------------------------------------------------------

# (etiqueta, rango, patrón). El rango decide cuál es el "último grado de estudios".
degree_levels = [
    ('Doctorado', 6, r'\bdoctor(?:ado|a)?\b'),
    ('Maestría', 5, r'\bmaestr(?:[íi]a|[oa])\b'),
    ('Postgrado', 4, r'\bpost?grado\b'),
    ('Especialidad', 3, r'\bespecialidad\b|\bespecializaci[oó]n\b'),
    ('Licenciatura', 2, r'\blicenciatura\b|\blicenciad[oa]\b'),
    ('Ingeniería', 2, r'\bingenier(?:[íi]a|[oa])\b'),
    ('Técnico Superior', 1.5, r'\bt[eé]cnic[oa]\s+superior\b|\btsu\b|^\s*t[eé]cnic[oa]\s+en\b'),
    ('Bachillerato', 1, r'\bbachillerato\b|\bpreparatoria\b'),
]
any_degree = re.compile('|'.join(f'(?:{p})' for _, _, p in degree_levels), re.IGNORECASE)

# Orden de preferencia (por rango) para elegir la universidad de referencia: primero licenciatura/ingeniería
university_rank_preference = (2, 3, 4, 5, 6, 1.5, 1)

# Palabras que delatan el inicio del nombre de una institución educativa
institution_words = (
    r'(?:universidad|instituto|escuela|centro\s+universitario|centro\s+de|colegio|facultad|'
    r'tecnol[óo]gico|benem[ée]rita|academia|polit[ée]cnic[oa]|unam|buap|ipn|upaep|udlap|itam|uvm|itesm)\b'
)

# Separa "grado + carrera" de la universidad cuando ésta va al final:
#   "Licenciatura en Derecho en la Universidad X", "Maestría en Y Universidad X",
#   "Licenciatura en Derecho Cursado en el Instituto X", "Licenciatura en Derecho, Universidad X"
institution_split = re.compile(
    r'(?:\s*[,:;\-–—]\s*|\s+(?:cursad[oa]s?\s+)?(?:(?:en|por|de|con)\s+(?:la\s+|el\s+)?)?)'
    r'(?P<uni>' + institution_words + r'.*)$',
    re.IGNORECASE | re.DOTALL
)

# Un grado que aparece justo después de estas palabras forma parte de la carrera, no es otro grado
stop_before_degree = re.compile(r'\b(?:en|de|del|la|el|y|e|con)\s*$', re.IGNORECASE)

# Clasificación pública/privada (sobre texto normalizado: sin acentos y en minúsculas).
# Se evalúa primero la lista de privadas porque algunas incluyen "autónoma" (UPAEP, ITAM).
private_patterns = [
    r'\bitam\b', r'instituto tecnologico autonomo', r'anahuac', r'iberoamericana', r'\budlap\b',
    r'universidad de las americas', r'\bupaep\b', r'popular autonoma', r'\buvm\b', r'valle de mexico',
    r'tecnologico de monterrey', r'\bitesm\b', r'estudios superiores de monterrey',
    r'interamericana para el desarrollo', r'\bunid\b', r'latinoamericano', r'centro universitario',
    r'la salle', r'autonoma de guadalajara', r'autonoma de durango', r'autonoma del noreste',
]
public_patterns = [
    r'\bunam\b', r'universidad nacional autonoma de mexico', r'\bipn\b', r'politecnico nacional',
    r'\bbuap\b', r'benemerita universidad autonoma', r'universidad veracruzana', r'\buam\b',
    r'autonoma metropolitana', r'universidad pedagogica', r'\bupn\b', r'escuela normal',
    r'universidad tecnologica', r'universidad politecnica', r'universidad intercultural',
    r'universidad autonoma (?:de|del)\b', r'instituto tecnologico de (?!estudios)',
    r'universidad de guadalajara', r'universidad juarez', r'universidad michoacana',
    r'colegio de mexico', r'colegio de puebla', r'\bcide\b', r'colegio de postgraduados',
    r'instituto de administracion publica', r'\biap\b', r'instituto tecnologico superior', r'ludwig',
]

# Experiencia legislativa: legislador (federal/local), integrante de cabildo o trabajo en el Congreso del Estado.
# Se descartan las líneas de suplentes y las de juntas/administraciones auxiliares (ej. "Regidor ... Administración Auxiliar").
legislative_keywords = re.compile(
    r'\b(?:diputad[oa]|senador(?:a)?|legislador(?:a)?|regidor(?:a)?|s[ií]ndic[oa])\b'
    r'|congreso\s+del\s+(?:estado|edo)',
    re.IGNORECASE
)
legislative_excluded = re.compile(r'\bauxiliar(?:es)?\b', re.IGNORECASE)

# Qué poner en "Experiencia legislativa" cuando la ficha no trae currículum
NO_CURRICULUM_EXPERIENCE = 'Sin información'

# Siglas con las que aparecen las universidades en el Excel de referencia (se evalúa sobre texto normalizado)
university_aliases = [
    (r'universidad de las americas,?\s+(?:de\s+)?puebla', 'UDLAP'),
    (r'universidad popular autonoma del estado de puebla', 'UPAEP'),
    (r'benemerita universidad autonoma de puebla', 'BUAP'),
    (r'instituto de administracion publica', 'IAP'),
    (r'universidad anahuac', 'Anáhuac'),
    (r'universidad interamericana para el desarrollo', 'UNID'),
]

# "Profesión" en el Excel es la ocupación asociada a la carrera; por omisión: Político/Política
occupation_rules = [
    (r'^educacion\b|^pedagogia\b', 'Docente'),
    (r'medic', 'Médico'),
    (r'contadur|mercadotecnia', 'Administrador'),
]

# Qué poner cuando no hay Facebook/Twitter (el Excel usa "Sin información")
MISSING_SOCIAL = 'Sin información'

email_regex = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')

NO_INFO = 'Sin información'


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------
def clean(text):
    """Colapsa espacios, tabuladores, saltos de línea y espacios duros."""
    return ' '.join(text.split()) if text else ''


def norm(text):
    """Minúsculas y sin acentos, para comparar."""
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def tidy(text):
    """Limpia espacios y separadores sobrantes en los extremos."""
    return re.sub(r'^[\s,:;\-–—|.]+|[\s,:;\-–—|.]+$', '', clean(text))


def li_lines(li):
    """
    Devuelve las líneas de una viñeta. Si la viñeta trae <br>, cada parte es una línea.
    Se usa un marcador para no confundir los saltos de línea del código fuente con saltos reales.
    """
    for br in li.find_all('br'):
        br.replace_with('§§')
    return [clean(part) for part in li.get_text().split('§§') if clean(part)]


# ---------------------------------------------------------------------------
# Estudios
# ---------------------------------------------------------------------------
def level_of(keyword):
    for name, rank, pattern in degree_levels:
        if re.search(pattern, keyword, re.IGNORECASE):
            return name, rank
    return None, 0


def split_degrees(bullet):
    """
    Una viñeta puede traer más de un grado ("Ingeniería en X Maestría en Y").
    Devuelve una lista de (prefijo, segmento), donde el prefijo es el texto previo al primer grado
    (normalmente la universidad: "ITAM: Licenciada en ...").
    """
    starts = []
    for i, match in enumerate(any_degree.finditer(bullet)):
        if i > 0 and stop_before_degree.search(bullet[:match.start()]):
            continue  # es parte del nombre de la carrera, no un grado nuevo
        starts.append(match.start())

    segments = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(bullet)
        prefix = bullet[:start] if idx == 0 else ''
        segments.append((prefix, bullet[start:end]))
    return segments


def clean_university(university):
    """Quita años, campus y 'titulado por...' y aplica las siglas usadas en el Excel."""
    u = clean(university)
    u = re.sub(r'\s+titulad[oa]\s+por\s+.*$', '', u, flags=re.IGNORECASE)
    u = re.sub(r'\s*\(?\b(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\b\)?', '', u)
    u = re.sub(r',?\s*campus\s+.*$', '', u, flags=re.IGNORECASE)
    return tidy(u) or NO_INFO


def shorten_university(university):
    normalized = norm(university)
    for pattern, short_name in university_aliases:
        if re.search(pattern, normalized):
            return short_name
    return university


def parse_degree(prefix, segment):
    """Obtiene nivel, universidad y carrera de un segmento. Devuelve None si el grado está en curso."""
    keyword_match = any_degree.search(segment)
    if not keyword_match:
        return None

    # "(titulación en curso)" = carrera terminada pero sin título: se conserva como nota del nivel.
    # "en curso" a secas = grado inconcluso: no cuenta como grado obtenido.
    pending_title = re.search(r'titulaci[oó]n\s+en\s+(?:curso|proceso)', segment, re.IGNORECASE)
    if not pending_title and re.search(r'\ben\s+curso\b|\bcursando\b', segment, re.IGNORECASE):
        return None
    note = 'titulación en curso' if pending_title else ''
    segment = re.sub(r'[\s\(\-–,]*titulaci[oó]n\s+en\s+(?:curso|proceso)\)?', '', segment, flags=re.IGNORECASE)

    level, rank = level_of(keyword_match.group(0))
    keyword_text = keyword_match.group(0)
    tail = segment[keyword_match.end():]

    # Universidad al final del segmento (si la hay)
    suffix_university = ''
    tail_degree = tail
    split_match = institution_split.search(tail)
    if split_match:
        suffix_university = tidy(split_match.group('uni'))
        tail_degree = tail[:split_match.start()]

    # Universidad al inicio ("ITAM: Licenciada en ...")
    prefix_university = tidy(prefix)
    if prefix_university and not re.search(institution_words, prefix_university, re.IGNORECASE):
        prefix_university = ''

    university = clean_university(prefix_university or suffix_university)

    tail_degree = tidy(tail_degree)
    if level == 'Ingeniería' and re.match(r'ingenier[oa]$', keyword_text, re.IGNORECASE):
        # "Ingeniero Industrial En Producción" -> "Ing Industrial en Producción" (así viene en el Excel)
        field_of_study = tidy('Ing ' + re.sub(r'\bEn\b', 'en', tail_degree))
    elif level == 'Técnico Superior':
        field_of_study = tidy(keyword_text + ' ' + tail_degree)
    else:
        field_match = re.search(r'\ben\s+(.+)$', tail_degree, re.IGNORECASE | re.DOTALL)
        field_of_study = tidy(field_match.group(1)) if field_match else tail_degree

    return {
        'level': level,
        'rank': rank,
        'note': note,
        'university': university,
        'field': field_of_study or NO_INFO,
    }


def classify_university(university):
    """Pública / Privada. Si no se reconoce, se asume Privada (y se avisa para ampliar el catálogo)."""
    if not university or university == NO_INFO:
        return NO_INFO
    normalized = norm(university)
    if any(re.search(p, normalized) for p in private_patterns):
        return 'Privada'
    if any(re.search(p, normalized) for p in public_patterns):
        return 'Pública'
    print(f"  [REVISAR] Universidad no clasificada, se asume Privada: {university}")
    return 'Privada'


def analyze_education(bullets):
    """
    - Último grado de estudios: el nivel más alto encontrado.
    - Preparación, Universidad y Privada o pública: salen de la universidad de referencia
      (licenciatura/ingeniería), sin importar cuál sea el último grado. Si no hay licenciatura,
      se usa el siguiente nivel disponible. Los grados "en curso" no cuentan.
    """
    degrees = []
    for bullet in bullets:
        for prefix, segment in split_degrees(bullet):
            parsed = parse_degree(prefix, segment)
            if parsed:
                degrees.append(parsed)

    empty = {'last_degree': NO_INFO, 'field': NO_INFO, 'university': NO_INFO, 'university_type': NO_INFO}
    if not degrees:
        return empty

    highest = max(degrees, key=lambda d: d['rank'])  # en empate gana el primero listado
    last_degree = highest['level'] + (f" ({highest['note']})" if highest['note'] else '')

    # Orden de preferencia para la universidad de referencia
    ordered = []
    for wanted_rank in university_rank_preference:
        ordered.extend(d for d in degrees if d['rank'] == wanted_rank)
    reference = ordered[0]
    university = next((d['university'] for d in ordered if d['university'] != NO_INFO), NO_INFO)
    if reference['university'] != NO_INFO:
        university = reference['university']

    return {
        'last_degree': last_degree,
        'field': reference['field'],
        'university': shorten_university(university),
        'university_type': classify_university(university),
    }


def infer_occupation(field_of_study, gender, has_curriculum):
    """Ocupación asociada a la carrera ("Profesión" del Excel). Por omisión: Político/Política."""
    if not has_curriculum:
        return NO_INFO
    label = None
    for pattern, occupation in occupation_rules:
        if re.search(pattern, norm(field_of_study)):
            label = occupation
            break
    feminine = gender == 'Femenino'
    if label == 'Administrador':
        return 'Administradora' if feminine else 'Administrador'
    if label:
        return label
    return 'Política' if feminine else 'Político'


# ---------------------------------------------------------------------------
# Experiencia legislativa
# ---------------------------------------------------------------------------
def has_legislative_experience(bullets):
    for bullet in bullets:
        if not legislative_keywords.search(bullet):
            continue
        if legislative_excluded.search(bullet):
            continue
        if EXCLUDE_SUPLENTE_AS_EXPERIENCE and re.search(r'suplente', bullet, re.IGNORECASE):
            continue
        return True
    return False


def format_phone(raw):
    """Formato del Excel: '01 (222) 372 11 00 Ext. 159'."""
    if not raw:
        return None
    text = re.sub(r'^tel\.?\s*', '', clean(raw), flags=re.IGNORECASE)
    text = re.sub(r'ext\.?\s*(\d+)', r'Ext. \1', text, flags=re.IGNORECASE)
    return text if text.startswith('01') else f'01 {text}'


# ---------------------------------------------------------------------------
# Contacto y redes
# ---------------------------------------------------------------------------
def classify_social(href):
    link = href.strip().lower()
    if re.match(r'https?://(?:www\.|m\.|mobile\.)?(?:facebook\.com|fb\.com)', link):
        return 'facebook'
    if re.match(r'https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)', link):
        return 'twitter'
    return None


def contact_value(table, icon_class):
    """Devuelve el texto de la celda contigua al ícono indicado."""
    icon = table.find('span', class_=icon_class)
    cell = icon.find_parent('td') if icon else None
    value_cell = cell.find_next_sibling('td') if cell else None
    return clean(value_cell.get_text(' ')) if value_cell else ''


def parse_detail(detail_html, member_id):
    """Procesa la ficha ampliada (respuesta del endpoint informacion.php)."""
    soup = BeautifulSoup(detail_html, parser)
    result = {
        'email': None, 'phone': None, 'facebook': None, 'twitter': None,
        'office': None, 'work_plan_url': None, 'sections': {},
    }

    # --- Contacto ---------------------------------------------------------
    table = soup.find('table', class_='tablasInformativaContacto')
    if table:
        # Correo: viene como texto plano dentro de <sub>, no como enlace mailto
        email_match = email_regex.search(contact_value(table, 'icon-correo2'))
        if email_match:
            result['email'] = email_match.group(0)
        else:
            for link in table.find_all('a', href=re.compile(r'^mailto:', re.IGNORECASE)):
                email_match = email_regex.search(link['href'])
                if email_match:
                    result['email'] = email_match.group(0)
                    break

        # Teléfono de oficina
        result['phone'] = contact_value(table, 'icon-telefono') or None

        # Casa(s) de gestión: puede haber más de una
        offices = []
        for icon in table.find_all('span', class_='icon-casaGestion2b'):
            cell = icon.find_parent('td')
            value_cell = cell.find_next_sibling('td') if cell else None
            if value_cell and clean(value_cell.get_text(' ')):
                offices.append(clean(value_cell.get_text(' ')))
        result['office'] = ' | '.join(offices) if offices else None

        # Redes sociales: se identifican por dominio, no por la clase del ícono
        for link in table.find_all('a', href=True):
            kind = classify_social(link['href'])
            if kind and not result[kind]:
                result[kind] = link['href'].strip()

    # --- Currículum -------------------------------------------------------
    curriculum = soup.find('div', id='curriculo')
    if curriculum:
        for title_tag in curriculum.find_all('div', class_='tituloCurriculo'):
            key = norm(title_tag.get_text(strip=True))
            bullets_list = title_tag.find_next_sibling('ul')
            lines = []
            if bullets_list:
                for li in bullets_list.find_all('li'):
                    lines.extend(li_lines(li))
            result['sections'].setdefault(key, []).extend(lines)

    # --- Plan de trabajo --------------------------------------------------
    work_info = soup.find('div', id=f'divInformacion{member_id}')
    if work_info:
        for link in work_info.find_all('a', href=True):
            if 'plan de trabajo' in link.get_text(strip=True).lower():
                result['work_plan_url'] = link['href']
                break

    return result


# ---------------------------------------------------------------------------
# Proceso principal
# ---------------------------------------------------------------------------
def main():
    print("Accediendo a la página de perfiles")

    response = requests.get(listing_url, impersonate=browser)

    # Iniciar un contador de registros
    total_records = 0

    # Acceder a la pantalla principal del directorio
    if response.status_code != 200:
        print(f"Error accediendo a la página inicial: {response.status_code}")
        return

    # Crear diccionario para contener los datos
    data = {"registros": []}

    # Conjunto de ids ya procesados para visitarlos una sola vez
    visited_ids = set()
    raw_curricula = {}

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Sección de "Mayoría Relativa" y de "Representación Proporcional"
    mr_section = soup.find('section', id='listaMayoriaRelativa').find('div', class_="contenedorFichaDips")
    rp_section = soup.find('section', id="listaRepresentacionProporcional").find('div', class_="contenedorFichaDips")

    # Todas las fichas de ambas secciones, junto con su tipo de elección
    mr_cards = mr_section.find_all('div', class_="fichaPerfil")
    rp_cards = rp_section.find_all('div', class_="fichaPerfil")
    all_cards = [(card, "Mayoría Relativa") for card in mr_cards] + \
                [(card, "Representación Proporcional") for card in rp_cards]

    for card, election_type in all_cards:

        # Obtener el id del diputado desde el atributo onclick="informacionDip(701)"
        onclick = card.get('onclick', '')
        id_match = re.search(r'informacionDip\((\d+)\)', onclick)

        # Validación: si la ficha no tiene id, se ignora
        if not id_match:
            continue
        member_id = int(id_match.group(1))

        # Verificar que no sea un id previamente visitado
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

        # Distrito y cabecera distrital (solo aplica a Mayoría Relativa; si no existe, N/A)
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

                district_tag = district_divs[1].find('p')
                if district_tag and district_tag.get_text(strip=True):
                    district = district_tag.get_text(strip=True)

        # Contacto de la ficha del listado (solo como respaldo de la ficha ampliada)
        card_email = None
        card_phone = None
        contact_container = card.find('div', class_='contenedorCorreoElectronico')
        if contact_container:
            for mail_link in contact_container.find_all('a', href=re.compile(r'^mailto:', re.IGNORECASE)):
                found = email_regex.search(mail_link['href'])
                if found:
                    card_email = found.group(0)
                    break

            phone_match = re.search(r'Tel\.\s*[\d\s()\-–]+(?:Ext\.?\s*\d+)?',
                                    contact_container.get_text(' ', strip=True))
            if phone_match:
                card_phone = clean(phone_match.group(0))

        # Redes de la ficha del listado (respaldo)
        card_socials = {'facebook': None, 'twitter': None}
        social_container = card.find('div', class_='apartadoDipRedesSociales')
        if social_container:
            for link in social_container.find_all('a', href=True):
                kind = classify_social(link['href'])
                if kind and not card_socials[kind]:
                    card_socials[kind] = link['href'].strip()

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

        detail = {
            'email': None, 'phone': None, 'facebook': None, 'twitter': None,
            'office': None, 'work_plan_url': None, 'sections': {},
        }
        if detail_response.status_code == 200:
            detail = parse_detail(detail_response.text, member_id)
        else:
            print(f"  Error obteniendo el detalle del diputado {member_id}: {detail_response.status_code}")

        # La ficha ampliada manda; la del listado queda de respaldo
        email = detail['email'] or card_email
        office_phone = detail['phone'] or card_phone
        facebook_url = detail['facebook'] or card_socials['facebook']
        twitter_url = detail['twitter'] or card_socials['twitter']

        # Secciones del currículum
        academic_background = detail['sections'].get('formacion academica', [])
        work_experience = detail['sections'].get('experiencia laboral', [])
        general_information = detail['sections'].get('informacion general', [])

        # Estudios (grado más alto)
        education = analyze_education(academic_background)

        # Currículum completo, para depurar diferencias contra el Excel
        has_curriculum = any(detail['sections'].values())
        raw_curricula[str(member_id)] = {'nombre': full_name, **detail['sections']}

        # Experiencia legislativa, a partir de experiencia laboral e información general
        if has_curriculum:
            experience_label = "Sí" if has_legislative_experience(work_experience + general_information) else "No"
        else:
            experience_label = NO_CURRICULUM_EXPERIENCE

        new_record = {
            "Nombre": full_name,
            "Partido": party_abbreviation,
            "Propietario o suplente": "Propietario",  # El directorio solo lista a quien ocupa la curul actualmente
            "Tipo de elección": election_type,
            "No. De Distrito": district_number,
            "Distrito": district,
            "Género": gender,
            "Folio foto": photo_id,
            "Plan de trabajo": "Sí" if detail['work_plan_url'] else "No",
            "Experiencia legislativa": experience_label,
            "Último grado de estudios": education['last_degree'],
            "Preparación": education['field'],
            "Profesión": infer_occupation(education['field'], gender, has_curriculum),
            "Universidad": education['university'],
            "Privada o pública": education['university_type'],
            "Teléfono de oficina": format_phone(office_phone),
            "Correo": email,
            "Facebook": facebook_url or MISSING_SOCIAL,
            "Twitter": twitter_url or MISSING_SOCIAL,
            "Casa de gestión": detail['office'],
        }

        data["registros"].append(new_record)
        total_records += 1

        # Pausa ética entre peticiones
        time.sleep(1)

    folder = 'data'
    os.makedirs(folder, exist_ok=True)
    output_path = os.path.join(folder, 'perfiles.json')

    with open(output_path, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=5)

    with open(os.path.join(folder, 'curriculos_raw.json'), 'w', encoding='utf-8') as raw_file:
        json.dump(raw_curricula, raw_file, ensure_ascii=False, indent=2)

    print("\n¡Archivo JSON guardado con éxito!")
    print(f"\nRegistros extraídos: {total_records}")


if __name__ == "__main__":
    main()