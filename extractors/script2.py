import sys
import os
# Le decimos a Python que añada la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import time # Añadido para la pausa ética
from lib.DateFormatter import DateFormatter
from lib.EventClassifier import EventClassifier

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

    # Crear un conjunto paracontener los enlaces y visitarlos uns sola vez
    visited_links = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Obtener la segunda etiqueta 'center', que contiene los botones con los años
    centers = soup.find_all('center')
    
    if len(centers) > 1:
        years_container = centers[1]

        # Obtener los años disponibles
        years = years_container.find_all('a')

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

                # Obtener todas las tablas de asistencia (son varias porque se dividen por meses)
                tables = year_soup.find_all('table', class_='tablaAsistencia')


                # Variables de control para determiar las votaciones que se hacen en un día
                previous_date = ''
                day_votation_counter = 0

                for table in tables:
                    # Seleccionar el cuerpo de la tabla para eliminar margen de error
                    tbody = table.find('tbody')
                    
                    # Validación: Asegurarnos de que el tbody existe antes de buscar adentro
                    if not tbody:
                        continue
                    
                    # Obtener todos los registros de la tabla, registro por registro añadirlo al JSON
                    records = tbody.find_all('tr')

                    for record in records:
                        # Obtener el enlace al registro de la votación
                        # Todas las celdas contienen una etiqueta 'a' con el mismo enlace de redirección
                        # Por ende, es indiferente cual celda se toma para esta referencia, en este caso se toma la primera
                        record_a = record.find('a')
                        
                        # Validación: Si la fila no tiene etiqueta 'a', la ignoramos
                        if not record_a or 'href' not in record_a.attrs:
                            continue
                            
                        # Corrección: Uso de comillas dobles por fuera y simples por dentro para no cortar el string
                        full_record_link =  f"{domain}{record_a['href']}"

                        # Verificar que no sea un enlace previamente visitado, en caso de serlo, ignorarlo
                        if full_record_link in visited_links:
                            continue

                        visited_links.add(full_record_link)
                        
                        record_page_response = requests.get(full_record_link, impersonate=browser)

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
                                    session = record_ps[2].find('strong').next_sibling.strip()
                                    description = record_ps[1].find('strong').next_sibling.strip()

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
                                    in_favor = int(vote_results.find('div', class_="favor").find('strong').next_sibling.strip())
                                    against = int(vote_results.find('div', class_="contra").find('strong').next_sibling.strip())
                                    abstentions = int(vote_results.find('div', class_="abstencion").find('strong').next_sibling.strip())

                                    # Inicializar el resto de contadores de votos
                                    abscences = 0
                                    secret_votes = 0
                                    total_votes = 0

                                    if vote_type.lower() == 'secreta':
                                        secret_votes = in_favor + against + abstentions
                                        total_votes = secret_votes

                                    else:
                                        attendances_table = record_soup.find('table', class_='tablaAsistencia')
                                        attendances_tbody =  attendances_table.find('tbody')
                                        attendances = attendances_tbody.find_all('tr')

                                        # Iniciar contadores manuales de votos para compararlos con el conteo de la página
                                        temp_in_favor = 0
                                        temp_against = 0
                                        temp_abstentions = 0

                                        for attendance in attendances:
                                            attendance_data = attendance.find_all('td')

                                            member_name = attendance_data[2].find('span').text.strip()
                                            member_political_party = attendance_data[1].find('img')['alt']
                                            member_vote = attendance_data[3].text.strip().lower()

                                            if member_vote == 'favor':
                                                temp_in_favor += 1
                                            elif member_vote == 'contra':
                                                temp_against += 1
                                            elif member_vote == 'abstencion':
                                                temp_abstentions += 1
                                            else: 
                                                abscences += 1

                                            new_congress_member = {
                                                "diputado" : member_name,
                                                "partido" : member_political_party,
                                                "voto" : member_vote
                                            }

                                            vote_list.append(new_congress_member)

                                        # Verificar que el conteo manual de votos sea el mismo que el mostrado en la página de la votación
                                        # Si el conteo manual es mayor, priorizar siempre el manual
                                        in_favor = in_favor if in_favor >= temp_in_favor else temp_in_favor
                                        against = against if against >= temp_against else temp_against
                                        abstentions = abstentions if abstentions >= temp_abstentions else temp_abstentions

                                        total_votes = in_favor + against + abstentions + abscences



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

        with open('votaciones.json', 'w', encoding='utf-8') as file:
            # json.dump convierte tu diccionario de Python a formato JSON
            # indent=4 lo formatea bonito para que sea legible por humanos
            json.dump(data, file, ensure_ascii=False, indent=5)

        print("\n¡Archivo JSON guardado con éxito!")
        print(f"\nRegistros extraídos: {total_records}")

    else:
        print("La estructura de la página cambió, no se encontró el contenedor de años.")
else:
    print(f"Error accediendo a la página inicial: {response.status_code}")
