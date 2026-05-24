import unicodedata
from datetime import datetime

class EventClassifier:

    def classify_vote(self, text : str):

        # 1. Pasar a minúsculas
        clean_text = text.lower()

        # 2. Quitar acentos (reemplaza á por a, é por e, etc.)
        clean_text = ''.join(c for c in unicodedata.normalize('NFD', clean_text) if unicodedata.category(c) != 'Mn')

        # Palabras clave para determinar tipo de votación
        kw1 = [
            'dictamen', 'dictamenes', 
            'reforma', 'reformas', 'reformar', 'reforman', 'reformado',
            'decreto', 'decretos', 
            'proposicion', 'proposiciones', 
            'propuesta', 'propuestas', 'propuesto',
            'expide', 'expiden', 'expedir', 'expedicion', 'expediciones'
        ]

        kw2 = [
            'acuerdo', 'acuerdos', 'acordar', 'acuerdan',
            'exhorto', 'exhortos', 'exhorta', 'exhortan', 'exhortar',
            'eleccion', 'elecciones', 'elegir', 'eligen',
            'votacion', 'votaciones', 'votar', 'votan', 'voto', 'votos',
            'designacion', 'designaciones', 'designa', 'designan', 'designar',
            'oficio', 'oficios', 
            'aprobacion', 'aprobaciones', 'aprobar', 'aprueba', 'aprueban', 'aprobado',
            'habilita', 'habilitan', 'habilitar', 'habilitacion'
        ]

        # 3. Buscar si alguna palabra clave está en el texto
        for word in kw1:
            if word in clean_text:
                return 1    # 1 es para denotar una iniciativa
            
        for word in kw2:
            if word in clean_text:
                return 2    # 2 es para denotar un punto de acuerdo
            
        return 0    # Retornar 0 si "encaja" dentro de ninguno
    

    def classify_per_period(self, date : str):
        """
        Recibe una fecha y devuelve a qué periodo pertenece (1, 2 o 3).
        Soporta los formatos 'YYYY-MM-DD' y 'DD/MM/YY'.
        """
        try:
            # 1. Convertir el texto a un objeto de fecha real en Python
            if '-' in date:
                date_obj = datetime.strptime(date, "%Y-%m-%d")
            else:
                date_obj = datetime.strptime(date, "%d/%m/%y")
        except ValueError:
            return "Formato de fecha no compatible" # Por si alguna fecha viene vacía o corrupta

        # 2. Creamos una "tupla" (mes, día)
        # Ejemplo: El 20 de Septiembre se convierte en (9, 20)
        month_day = (date_obj.month, date_obj.day)

        # 3. Comparamos usando lógica matemática directa
        
        # PERIODO 1: Del 15 de Septiembre (9, 15) al 15 de Diciembre (12, 15)
        #            Se contempla el final del primer receso, el 14 de Enero
        #            Como cruza el cambio de año, usamos "or"
        if month_day >= (9, 15) or month_day <= (1, 14):
            return 1
            
        # PERIODO 2: Del 15 de Enero (1, 15) al 15 de Marzo (3, 15)
        #            Se contempla el final del segundo receso, el 14 de Mayo
        elif (1, 15) <= month_day <= (5, 14):
            return 2
            
        # PERIODO 3: Del 15 de Mayo (5, 15) al 15 de Julio (7, 15)
        #            Se contempla el final del tercer receso, el 14 de Septiembre
        elif (5, 15) <= month_day <= (9, 14):
            return 3
            
        # Caso por defecto, como todos los casos anteriores se complementan, no debería llegarse hasta este punto
        else:
            return 0


    def classify_per_year(self, date: str):
        """
        Calcula si la fecha corresponde al primer (1), segundo (2) o tercer (3) año legislativo.
        Considera que el año inicia el 15 de septiembre y las legislaturas duran 3 años (regla 3n + 2).
        Soporta formatos 'YYYY-MM-DD' y 'DD/MM/YY'.
        """
        try:
            if '-' in date:
                date_obj = datetime.strptime(date, "%Y-%m-%d")
            else:
                date_obj = datetime.strptime(date, "%d/%m/%y")
        except ValueError:
            return 0  # Retorna 0 si la fecha no es válida

        # 1. Ajustar el "año de ciclo" basado en el 15 de septiembre
        # Si es antes del 15 de sep, pertenece al ciclo legislativo que inició el año anterior
        if (date_obj.month, date_obj.day) >= (9, 15):
            cycle_year = date_obj.year
        else:
            cycle_year = date_obj.year - 1

        # 2. Calcular el año en que inició la legislatura actual
        # Tomando 2024 como base comprobada de la regla 3n + 2
        term_start_year = cycle_year - ((cycle_year - 2024) % 3)

        # 3. Calcular qué año de la legislatura es (1, 2 o 3)
        legislative_year = cycle_year - term_start_year + 1

        return legislative_year