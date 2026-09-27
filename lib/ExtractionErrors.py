# ExtractionErrors.py

class ExtractionFailedError(Exception):
    """
    Se lanza cuando la extracción de datos vía Gemini falla tras agotar
    el número máximo de reintentos permitidos (por defecto 3).

    El código que llama a process_file() debe capturar esta excepción para
    decidir qué hacer con el registro (marcarlo como pendiente, no perderlo,
    reintentarlo en una ejecución futura, etc.), en vez de recibir un
    diccionario con valores vacíos indistinguible de una extracción exitosa
    que legítimamente no encontró datos.
    """
    pass