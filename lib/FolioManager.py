# FolioManager.py

from datetime import datetime

class FolioManager:
    def __init__(self):
        # Contadores secuenciales
        self.folio_legislatura = 0
        self.folio_periodo = 0
        
        # Memoria de estado para detectar cuándo reiniciar los contadores
        self.current_term_start_year = None
        self.current_legislative_year = None
        self.current_period = None

    def _get_term_start_year(self, date: str) -> int:
        """
        Calcula el año base (inicio) de la legislatura actual usando la regla 3n + 2.
        Esto nos sirve como un 'ID único' para saber si cambiamos de legislatura.
        """
        if '-' in date:
            date_obj = datetime.strptime(date, "%Y-%m-%d")
        else:
            date_obj = datetime.strptime(date, "%d/%m/%y")
            
        # Ajuste del ciclo basado en el 15 de septiembre
        if (date_obj.month, date_obj.day) >= (9, 15):
            cycle_year = date_obj.year
        else:
            cycle_year = date_obj.year - 1
            
        return cycle_year - ((cycle_year - 2024) % 3)

    def generate_folios(self, date: str, legislative_year: int, period: int) -> tuple[int, int]:
        """
        Calcula y retorna una tupla con (folio_legislatura, folio_periodo).
        Debe llamarse por CADA registro extraído, no por archivo.
        """
        # Calculamos el identificador de la legislatura actual basado en la fecha
        term_start_year = self._get_term_start_year(date)
        
        # 1. EVALUAR RESET GLOBAL (Cambio de Legislatura)
        if self.current_term_start_year != term_start_year:
            self.current_term_start_year = term_start_year
            self.folio_legislatura = 0
            
            # Al cambiar de legislatura, forzosamente iniciamos nuevo periodo
            self.current_legislative_year = legislative_year
            self.current_period = period
            self.folio_periodo = 0

        # 2. EVALUAR RESET PARCIAL (Cambio de Año Legislativo o de Periodo)
        elif self.current_legislative_year != legislative_year or self.current_period != period:
            self.current_legislative_year = legislative_year
            self.current_period = period
            self.folio_periodo = 0

        # 3. Incrementar folios para el registro actual
        self.folio_legislatura += 1
        self.folio_periodo += 1

        return self.folio_legislatura, self.folio_periodo