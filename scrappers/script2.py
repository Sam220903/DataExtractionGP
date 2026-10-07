import sys
import os
# Le decimos a Python que añada la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import time # Añadido para la pausa ética
import unicodedata
from lib.DateFormatter import DateFormatter
from lib.EventClassifier import EventClassifier


def normalizeText(text):
    # Quita acentos y pasa a minúsculas para comparar textos sin importar cómo los escriba la página
    decomposed = unicodedata.normalize('NFD', text)
    withoutAccents = ''.join(char for char in decomposed if unicodedata.category(char) != 'Mn')
    return withoutAccents.strip().lower()


def extractMemberData(attendance):
    # Extrae los datos de la tarjeta de un diputado
    diputado_card = attendance.find('div', class_="diputado-card")
    voto_card_meta = attendance.find('div', class_="voto-card-meta")
    voto_card_foot = attendance.find('div', class_="voto-card-foot")

    return {
        "name": diputado_card.find('span', class_='nombre-card').text.strip(),
        "party": diputado_card.find('span', class_='partido-card').find('img', class_='foto-logo')['alt'],
        "attendance": voto_card_meta.find('span', class_='badge-asistencia').text.strip().lower(),
        "vote": voto_card_foot.find('span', class_="badge-voto-sentido").text.strip().lower()
    }


def tallyVotes(members):
    # Cuenta cada diputado en una sola categoría: ausente o, si asistió, a favor / en contra / abstención
    tally = {
        "favor": 0,
        "against": 0,
        "abstentions": 0,
        "absences": 0,
        "unrecognized": 0
    }

    for member in members:
        if normalizeText(member["attendance"]) != 'asistencia':
            tally["absences"] += 1
            continue

        vote = normalizeText(member["vote"])

        if 'favor' in vote:
            tally["favor"] += 1
        elif 'contra' in vote:
            tally["against"] += 1
        elif 'abstencion' in vote:
            tally["abstentions"] += 1
        else:
            # Asistió pero su voto no coincide con ninguna categoría conocida
            tally["unrecognized"] += 1

    return tally


# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
url = f'{domain}/index.php?option=com_content&view=article&id=12718'
browser = "edge"
parser = "html.parser"

date_formatter = DateFormatter()
classifier = EventClassifier()

print("Accediendo a la página de votaciones")

response = requests.get(url, impersonate=browser)

# Iniciar un contador de registros
total_records = 0

# Acceder a la pantalla principal de votaciones
if response.status_code == 200:
    # Crear diccionario para contener los datos
    data = {
        "registros" : []
    }

    # Crear un conjunto para contener los enlaces y visitarlos uns sola vez
    visited_links = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Obtener el contenedor de botones de años
    years_nav = soup.find('div', class_='anios-nav')
    
    # Obtener los años disponibles
    years = years_nav.find_all('a', class_='anio-chip')

    # Iterar por cada año
    for year in years:
        # Limpiamos el texto para sacar el año puro (2024, 2025...)
        year_text = year.text.strip()
        # Unimos el dominio para tener la URL completa y lista para ser visitada
        full_year_link = f"{domain}{year['href']}" 
        
        print(f"\nProcesando el año: {year_text}")
        
        # Acceder a los registros de ese año
        year_page_response = requests.get(full_year_link, impersonate=browser)

        
        if year_page_response.status_code == 200:
            year_soup = BeautifulSoup(year_page_response.text, parser)

            # Obtener todos los bloques por mes
            blocks = year_soup.find_all('div', class_='bloque-mes')


            # Variables de control para determinar las votaciones que se hacen en un día
            previous_date = ''
            day_votation_counter = 0

            for block in blocks:

                # Obtener el mes para depuración
                month = block.find('div', class_='mes-separador')
                print(f"\nRegistros de {month.text.strip()} del {year_text}")

                # Obtener la tabla del mes
                table = block.find('div', class_="tabla-wrapper")
                
                # Validación: Asegurarnos de que el tbody existe antes de buscar adentro
                if not table:
                    continue
                
                # Obtener todos los registros de la tabla, registro por registro añadirlo al JSON
                records = table.find_all('a')

                for record in records:
                    # Obtener el enlace al registro de la votación
                    
                    # Validación: Si la fila no tiene etiqueta 'a', la ignoramos
                    if not record or 'href' not in record.attrs:
                        continue
                        
                    # Corrección: Uso de comillas dobles por fuera y simples por dentro para no cortar el string
                    record_link =  f"{domain}{record['href']}"

                    # Verificar que no sea un enlace previamente visitado, en caso de serlo, ignorarlo
                    if record_link in visited_links:
                        continue

                    visited_links.add(record_link)
                    
                    record_page_response = requests.get(record_link, impersonate=browser)

                    # Corrección: Añadido .status_code para que la validación funcione
                    if record_page_response.status_code == 200:
                        record_soup = BeautifulSoup(record_page_response.text, parser)

                        record_header = record_soup.find('div', class_='cabecera-tema')
                        
                        # Validación: Confirmar que la página cargó bien y tiene la cabecera
                        if record_header:
                            record_ps = record_header.find_all('p') 
                            
                            # Validación: Evitar IndexError si faltan párrafos
                            if len(record_ps) >= 3:
                                
                                # Captura de todos los datos a exportar
                                topic = record_header.find('h2').text.strip()
                                date = record_ps[0].find('strong').next_sibling.strip()
                                date = date_formatter.format(date)      # Formatear la fecha a dd/mm/aa
                                description = record_ps[1].find('strong').next_sibling.strip()
                                session = record_ps[2].find('strong').next_sibling.strip()

                                # Obtener número de votación del día
                                # Comparar si se esta evaluando un mismo día
                                if previous_date == date:
                                    day_votation_counter += 1
                                else: 
                                    day_votation_counter = 1

                                # Obtener el tipo de votación (Iniciativa - 1, Punto de Acuerdo - 2)
                                vote_classification = classifier.classify_vote(description)

                                # Obtener el periodo en el que se hizo la votación
                                period = classifier.classify_per_period(date)

                                vote_type = record_ps[3].find('strong').next_sibling.strip()

                                # Iniciar una lista para contener los votos de cada diputado del congreso
                                vote_list = []

                                # Obtener el conteo de resultados de los votos de la página
                                vote_results = record_header.find('div', class_="resultados-votacion")
                                in_favor = int(vote_results.find('button', class_="favor").find('strong').next_sibling.strip())
                                against = int(vote_results.find('button', class_="contra").find('strong').next_sibling.strip())
                                abstentions = int(vote_results.find('button', class_="abstencion").find('strong').next_sibling.strip())

                                # Inicializar el resto de contadores de votos
                                abscences = 0
                                secret_votes = 0
                                total_votes = 0

                                if vote_type.lower() == 'secreta':
                                    # En votación secreta no hay lista de diputados, se usan los conteos de la página
                                    secret_votes = in_favor + against + abstentions
                                    total_votes = secret_votes

                                else:
                                    attendances_table = record_soup.find('div', class_='tabla-contenedor')
                                    attendances_grid =  attendances_table.find('div', class_="votos-grid")
                                    attendances = attendances_grid.find_all('article', class_="voto-card")

                                    # Extraer los datos de cada diputado y armar la lista que se guarda en el JSON
                                    members = []

                                    for attendance in attendances:
                                        member = extractMemberData(attendance)
                                        members.append(member)

                                        new_congress_member = {
                                            "diputado" : member["name"],
                                            "partido" : member["party"],
                                            "voto" : member["vote"]
                                        }

                                        vote_list.append(new_congress_member)

                                    # Contar los votos a partir de la lista de diputados (fuente de verdad),
                                    # así los totales siempre coinciden con la lista guardada en "votaciones"
                                    tally = tallyVotes(members)

                                    # Avisar si el conteo de la página no coincide con el conteo manual
                                    if (in_favor != tally["favor"] or against != tally["against"] or abstentions != tally["abstentions"]):
                                        print(f"  Aviso: la página indica {in_favor}/{against}/{abstentions} (favor/contra/abstención) "
                                              f"y el conteo manual es {tally['favor']}/{tally['against']}/{tally['abstentions']}. Se usa el manual.")

                                    if tally["unrecognized"] > 0:
                                        print(f"  Aviso: {tally['unrecognized']} diputado(s) asistieron con un voto no reconocido.")

                                    in_favor = tally["favor"]
                                    against = tally["against"]
                                    abstentions = tally["abstentions"]
                                    abscences = tally["absences"]

                                    # El total es la suma de todas las categorías, es decir, todos los diputados de la lista
                                    total_votes = in_favor + against + abstentions + abscences + tally["unrecognized"]



                                new_record = {
                                    "tema" : topic,
                                    "fecha" : date,
                                    "sesion" : session,
                                    "numero_votacion" : day_votation_counter,   # Número de votación en el día
                                    "descripcion" : description,
                                    "clasificacion" : vote_classification,    # Se clasifica si es Iniciativa o Punto de Acuerdo
                                    "periodo": period,
                                    "tipo_votacion" : vote_type,            # El tipo de votación, si fue nominal o secreta         
                                    "votaciones" : vote_list,
                                    "a_favor" : in_favor,
                                    "en_contra" : against,
                                    "abstenciones" : abstentions,
                                    "en_secreto" : secret_votes,
                                    "ausencias": abscences,
                                    "total_votos" : total_votes
                                }

                                data["registros"].append(new_record)
                                print(f"Registro extraído: {new_record['tema'][:50]}...")
                                total_records += 1

                                previous_date = date
                    
                    # Pausa de 1 segundo para no saturar el servidor del Congreso
                    time.sleep(1)

    folder = 'data'

    # Creamos la carpeta si no existe (exist_ok=True evita errores si ya fue creada antes)
    os.makedirs(folder, exist_ok=True)
    
    # Construimos la ruta segura cruzando plataformas (Windows usa '\', Mac/Linux usan '/')
    ruta_archivo = os.path.join(folder, 'votaciones.json')

    # Usamos la nueva ruta en el 'with open'
    with open(ruta_archivo, 'w', encoding='utf-8') as file:
        # json.dump convierte tu diccionario de Python a formato JSON
        # indent=5 lo formatea bonito para que sea legible por humanos
        json.dump(data, file, ensure_ascii=False, indent=5)

    print("\n¡Archivo JSON guardado con éxito!")
    print(f"\nRegistros extraídos: {total_records}")

else:
    print(f"Error accediendo a la página inicial: {response.status_code}")