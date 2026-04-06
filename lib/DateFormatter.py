class DateFormatter:

    def format(self, date: str):
        # Diccionario para convertir los meses de texto a número
        months = {
            "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
            "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
            "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
        }
        
        try:
            # Convertimos todo a minúsculas y separamos por espacios
            # Ejemplo: "20 de Diciembre de 2024" -> ['20', 'de', 'diciembre', 'de', '2024']
            parts = date.lower().split()
            
            if len(parts) >= 5:
                # Día: zfill(2) asegura que si es "9", le ponga un cero al inicio -> "09"
                day = parts[0].zfill(2) 
                
                # Mes: Buscamos "diciembre" en nuestro diccionario -> "12"
                month = months.get(parts[2], "00") 
                
                # Año: Tomamos los últimos 2 caracteres (2024 -> 24)
                year = parts[4][-2:] 
                
                return f"{day}/{month}/{year}"
            
            # Si por alguna razón el texto no tiene el formato esperado, lo regresamos igual
            return date
            
        except Exception as e:
            # Si algo falla, es mejor regresar el texto original que detener el programa
            return date