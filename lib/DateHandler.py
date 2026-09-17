import re

class DateHandler:
    def __init__(self):
        self.months = {
                "enero": 1,
                "febrero": 2,
                "marzo": 3,
                "abril": 4,
                "mayo": 5,
                "junio": 6,
                "julio": 7,
                "agosto": 8,
                "septiembre": 9,
                "octubre": 10,
                "noviembre": 11,
                "diciembre": 12,
            }

        self.datePattern = re.compile(r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)(?:\s+de)?\s+(\d{4})")
        

    def parseDate(self, dateText):
        """
        Receives a date with format "14 de mayo de 2024" or "14 de mayo 2026"
        (with or without the second "de") and returns a tuple
        (day, month, year), e.g. (14, 5, 2024).
        """
        match = self.datePattern.search(dateText.strip().lower())

        if not match:
            raise ValueError(f"Invalid date format: '{dateText}'")

        dayText, monthText, yearText = match.groups()

        day = int(dayText)
        month = self.months.get(monthText)
        year = int(yearText)

        if month is None:
            raise ValueError(f"Unrecognized month: '{monthText}'")

        return (day, month, year)

    def getOldestDate(self, dateList):
        """
        Receives a list of tuples (day, month, year)
        and returns the tuple representing the oldest date.
        """
        if not dateList:
            raise ValueError("The date list is empty")

        return min(dateList, key=lambda date: (date[2], date[1], date[0]))

    def formatDate(self, dateTuple):
        """
        Receives a tuple (day, month, year), e.g. (14, 5, 2024),
        and returns it formatted as "14 de mayo de 2024".
        """
        day, month, year = dateTuple
 
        monthName = None
        for name, number in self.months.items():
            if number == month:
                monthName = name
                break
 
        if monthName is None:
            raise ValueError(f"Unrecognized month number: '{month}'")
 
        return f"{day} de {monthName} de {year}"

    def formatDateShort(self, dateTuple):
        """
        Receives a tuple (day, month, year), e.g. (14, 5, 2024),
        and returns it formatted as "14/05/24" (dd/mm/aa, two-digit year).
        """
        day, month, year = dateTuple

        return f"{day:02d}/{month:02d}/{year % 100:02d}"

    def getOldestDateFromTexts(self, dateTextList):
        """
        Receives a list of dates as text (e.g. "14 de mayo de 2024")
        and returns the oldest one as a tuple (day, month, year).
        """
        parsedDates = [self.parseDate(dateText) for dateText in dateTextList]
        return self.formatDate(self.getOldestDate(parsedDates))