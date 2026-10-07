# lib/ActasRegistry.py

import os
import json
from datetime import datetime


class ActasRegistry:
    """
    Registro persistente de actas: guarda cuáles se descargaron, cuáles ya fueron
    analizadas con IA (junto con sus registros) y cuáles fallaron y por qué.
    Se escribe en disco después de cada cambio, así que si el proceso se corta
    (cuota agotada, alta demanda, Ctrl+C) no se pierde nada de lo ya analizado.
    """

    FILE_NAME = 'actas_registro.json'
    ORPHAN_ID = 'historico_sin_acta'

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, self.FILE_NAME)
        self.entries = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------
    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                content = json.load(f)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"El registro {self.path} está dañado ({error}). "
                f"Revísalo o restaura una copia antes de continuar, "
                f"para no volver a gastar tokens analizando todo."
            )
        self.entries = content.get('actas', {})

    def _save(self):
        os.makedirs(self.data_dir, exist_ok=True)
        tempPath = self.path + '.tmp'
        with open(tempPath, 'w', encoding='utf-8') as f:
            json.dump({'actas': self.entries}, f, ensure_ascii=False, indent=2)
        os.replace(tempPath, self.path)

    def _now(self):
        return datetime.now().isoformat(timespec='seconds')

    def _getOrCreate(self, aid):
        if aid not in self.entries:
            self.entries[aid] = {
                'url': None,
                'pdf': None,
                'analizado': False,
                'estado': 'pendiente',
                'prompt_version': None,
                'modelo': None,
                'fecha': None,
                'fecha_analisis': None,
                'registros': [],
                'error': None,
            }
        return self.entries[aid]

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def get(self, aid):
        return self.entries.get(aid, {})

    def pdf_on_disk(self, aid):
        """Devuelve la ruta del PDF si ya fue descargado y sigue existiendo en disco."""
        pdfPath = self.get(aid).get('pdf')
        if pdfPath and os.path.exists(pdfPath):
            return pdfPath
        return None

    def needs_analysis(self, aid, promptVersion, force=False, reanalyze_old=False):
        """True si el acta debe enviarse a la IA en esta ejecución."""
        entry = self.get(aid)
        if force:
            return True
        if not entry.get('analizado'):
            return True
        savedVersion = entry.get('prompt_version') or 0
        if reanalyze_old and savedVersion < promptVersion:
            return True
        return False

    def pending_failures(self):
        """Lista de (acta_id, error) de las actas que tienen un error registrado."""
        failures = []
        for aid, entry in self.entries.items():
            if entry.get('error'):
                failures.append((aid, entry['error']))
        return failures

    def all_records(self):
        records = []
        for entry in self.entries.values():
            if entry.get('analizado'):
                records.extend(entry.get('registros', []))
        return records

    def summary(self):
        total = 0
        analyzed = 0
        pending = 0
        withError = 0
        for aid, entry in self.entries.items():
            if aid == self.ORPHAN_ID:
                continue
            total += 1
            if entry.get('analizado'):
                analyzed += 1
            else:
                pending += 1
            if entry.get('error'):
                withError += 1
        return {
            'total': total,
            'analizadas': analyzed,
            'pendientes': pending,
            'con_error': withError,
        }

    # ------------------------------------------------------------------
    # Actualizaciones
    # ------------------------------------------------------------------
    def mark_downloaded(self, aid, url, path):
        entry = self._getOrCreate(aid)
        newUrl = url or entry.get('url')
        if entry.get('url') == newUrl and entry.get('pdf') == path:
            return
        entry['url'] = newUrl
        entry['pdf'] = path
        self._save()

    def mark_analyzed(self, aid, records, status, promptVersion, model, fecha):
        entry = self._getOrCreate(aid)
        entry['analizado'] = True
        entry['estado'] = status
        entry['prompt_version'] = promptVersion
        entry['modelo'] = model
        entry['fecha'] = fecha
        entry['fecha_analisis'] = self._now()
        entry['registros'] = records
        entry['error'] = None
        self._save()

    def mark_failed(self, aid, reason, detail=''):
        """
        Registra el motivo del fallo. Si el acta ya tenía un análisis previo
        (por ejemplo un reanálisis que falló) se conservan sus registros.
        """
        entry = self._getOrCreate(aid)
        if not entry.get('analizado'):
            entry['estado'] = reason
        entry['error'] = {
            'motivo': reason,
            'detalle': str(detail)[:500],
            'fecha': self._now(),
        }
        self._save()

    # ------------------------------------------------------------------
    # Migración del histórico previo (unidad_participacion.json)
    # ------------------------------------------------------------------
    def migrate(self, oldRecords, actasByDate, prompt_version=1):
        """
        Marca como analizadas las actas cuya fecha ya tiene registros en el JSON
        anterior. Los registros viejos no traen id de acta, así que se asignan a
        la primera acta de esa fecha. Los registros sin acta local conocida se
        guardan en una entrada aparte para no perderlos.
        Devuelve cuántas actas se reconocieron.
        """
        recordsByDate = {}
        for record in oldRecords:
            date = record.get('Fecha')
            recordsByDate.setdefault(date, []).append(record)

        recognized = 0
        for date, records in recordsByDate.items():
            actaIds = actasByDate.get(date, [])
            if not actaIds:
                self._addOrphanRecords(records, prompt_version)
                continue
            for position, aid in enumerate(actaIds):
                entry = self._getOrCreate(aid)
                entry['analizado'] = True
                entry['estado'] = 'migrado'
                entry['prompt_version'] = prompt_version
                entry['fecha'] = date
                entry['fecha_analisis'] = self._now()
                entry['error'] = None
                if position == 0:
                    entry['registros'] = records
                else:
                    entry['registros'] = []
                recognized += 1

        self._save()
        return recognized

    def _addOrphanRecords(self, records, promptVersion):
        entry = self._getOrCreate(self.ORPHAN_ID)
        entry['analizado'] = True
        entry['estado'] = 'migrado_sin_acta'
        entry['prompt_version'] = promptVersion
        entry['registros'].extend(records)