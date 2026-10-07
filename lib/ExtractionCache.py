# ExtractionCache.py

import json
import os


class ExtractionCache:
    """
    Guarda en disco, por enlace de documento (versión estenográfica o acta),
    si la extracción de datos vía Gemini ya se completó exitosamente y con
    qué resultado, para que una ejecución posterior del scraper:
      - No vuelva a descargar ni a reprocesar registros ya completados.
      - Solo reintente los registros que quedaron pendientes.

    Estructura interna (y del archivo JSON en disco):
    {
        "<url_del_documento>": {
            "status": "success" | "failed",
            "source": "SV" | "PM",
            "data": {...},   # el diccionario completo devuelto por process_file, o null si falló
            "error_type": "alta_demanda" | "cuota_agotada" | ...   # solo en "failed", si se conoce la causa
        },
        ...
    }
    """

    def __init__(self, cache_path: str):
        self.cache_path = cache_path
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                print(f"Aviso: no se pudo leer la caché en {self.cache_path}, se iniciará vacía.")
                return {}
        return {}

    def save(self):
        """Persiste la caché en disco. Se llama después de cada actualización
        para no perder el progreso si el proceso se interrumpe a la mitad."""
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=4)

    def get(self, link: str):
        return self._data.get(link)

    def is_success(self, link: str) -> bool:
        entry = self._data.get(link)
        return entry is not None and entry.get("status") == "success"

    def set_success(self, link: str, source: str, data: dict):
        self._data[link] = {
            "status": "success",
            "source": source,
            "data": data
        }
        self.save()

    def set_failed(self, link: str, source: str, errorType: str = None):
        # Si por alguna razón ya existía un éxito previo registrado para este
        # enlace, nunca lo degradamos a "failed": un éxito previo se conserva.
        existing = self._data.get(link)
        if existing and existing.get("status") == "success":
            return

        self._data[link] = {
            "status": "failed",
            "source": source,
            "data": None,
            "error_type": errorType
        }
        self.save()