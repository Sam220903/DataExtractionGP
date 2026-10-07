# ExtractionErrors.py

import re

# Errores tras los cuales no tiene sentido seguir llamando a la IA en esta ejecución
FATAL_ERROR_TYPES = ("cuota_diaria_agotada", "credenciales_invalidas")

# Mensajes para el usuario según la causa del fallo
ERROR_MESSAGES = {
    "alta_demanda": "El modelo está saturado por alta demanda (error 5xx de la API).",
    "cuota_agotada": "Se agotó o se alcanzó el límite de cuota/tokens de la API de Gemini (error 429).",
    "cuota_diaria_agotada": "Se agotó la cuota diaria de tokens/solicitudes de la API de Gemini (error 429).",
    "credenciales_invalidas": "La API key es inválida o no tiene permisos. Revisa GEMINI_API_KEY en el archivo .env.",
    "error_red": "Falló la conexión con la API de Gemini.",
    "respuesta_invalida": "El modelo devolvió una respuesta que no es un JSON válido.",
    "error_desconocido": "Ocurrió un error desconocido al llamar a la IA.",
    "sin_descarga": "No se pudo descargar el archivo.",
    "sin_texto": "No se pudo extraer texto legible del PDF.",
    "no_intentado": "No se intentó: el análisis con IA se detuvo antes de llegar a este documento.",
}


def classifyError(errorText: str) -> str:
    """
    Devuelve el código de la causa de un fallo de Gemini a partir del texto del error.
    Los procesadores SV/PM empaquetan el último error original dentro del mensaje de
    ExtractionFailedError ("... Último error: <error>"), así que se clasifica con ese texto.
    """
    text = str(errorText)

    # Nos quedamos solo con el último error original, no con el texto genérico del mensaje
    if "Último error:" in text:
        text = text.split("Último error:", 1)[1]
    text = text.lower()

    isQuotaError = re.search(r"\b429\b", text) or "resource_exhausted" in text or "quota" in text

    if isQuotaError and ("perday" in text or "per day" in text):
        return "cuota_diaria_agotada"

    credentialMarkers = ("api key not valid", "api_key_invalid", "api key expired",
                         "permission_denied", "unauthenticated")
    if any(marker in text for marker in credentialMarkers):
        return "credenciales_invalidas"

    if isQuotaError:
        return "cuota_agotada"

    isServerError = re.search(r"\b(500|502|503|504)\b", text)
    serverMarkers = ("unavailable", "overloaded", "internal", "deadline_exceeded")
    if isServerError or any(marker in text for marker in serverMarkers):
        return "alta_demanda"

    networkMarkers = ("connect", "timeout", "timed out", "name resolution", "network", "ssl")
    if any(marker in text for marker in networkMarkers):
        return "error_red"

    jsonMarkers = ("json", "expecting value", "extra data", "unterminated", "expecting property")
    if any(marker in text for marker in jsonMarkers):
        return "respuesta_invalida"

    return "error_desconocido"


def isFatalErrorType(errorType: str) -> bool:
    return errorType in FATAL_ERROR_TYPES


def describeErrorType(errorType: str) -> str:
    return ERROR_MESSAGES.get(errorType, errorType)


class ExtractionFailedError(Exception):
    """
    Se lanza cuando la extracción de datos vía Gemini falla tras agotar
    el número máximo de reintentos permitidos (por defecto 3).

    El código que llama a process_file() debe capturar esta excepción para
    decidir qué hacer con el registro (marcarlo como pendiente, no perderlo,
    reintentarlo en una ejecución futura, etc.), en vez de recibir un
    diccionario con valores vacíos indistinguible de una extracción exitosa
    que legítimamente no encontró datos.

    La causa del fallo (alta demanda, cuota agotada, etc.) se obtiene con la
    propiedad errorType, calculada a partir del mensaje; no hace falta cambiar
    cómo se lanza esta excepción en los procesadores.
    """

    @property
    def errorType(self) -> str:
        return classifyError(str(self))

    @property
    def isFatal(self) -> bool:
        return isFatalErrorType(self.errorType)