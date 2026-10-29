"""Trova la copia guidata più recente senza cercare nel gestionale attivo."""

import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path


_FOLDER = re.compile(r"SU_Print_Collaudo_([0-9]{8}_[0-9]{6})_[a-z0-9_]{8}\Z")
_SCHEMA_LIMIT = 1024 * 1024
_REPARSE_POINT = 0x400


def _plain_entry(path, *, directory=False):
    """Non seguire collegamenti simbolici, junction o altri reparse point."""
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"La copia più recente non è completa: manca {path.name}."
        ) from error
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        raise ValueError(f"La copia contiene un collegamento: {path.name}. Serve una copia locale autonoma.")
    wanted_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not wanted_type(info.st_mode):
        raise ValueError(f"La copia più recente non è completa: {path.name} non è del tipo previsto.")


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chiave JSON ripetuta")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Costante JSON non standard")


def find_latest_snapshot(desktop):
    """Seleziona solo l'ultima cartella prodotta dal nostro helper.

    La data nel nome della cartella identifica la creazione, mentre la data di
    modifica del database può cambiare senza indicare una copia più recente.
    Se l'ultima copia è incompleta o dubbia non si ripiega su copie precedenti.
    Il contenuto SQLite sarà poi verificato dall'adattatore in sola lettura.
    """
    try:
        desktop = Path(desktop).resolve(strict=True)
    except FileNotFoundError as error:
        raise FileNotFoundError("La cartella Desktop non è disponibile.") from error
    if not desktop.is_dir():
        raise ValueError("La cartella Desktop non è disponibile.")
    candidates = []
    for child in desktop.iterdir():
        match = _FOLDER.fullmatch(child.name)
        if match is None:
            continue
        try:
            timestamp = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
        except ValueError:
            continue  # Un nome impossibile non è stato generato dall'helper.
        candidates.append((timestamp, child))
    if not candidates:
        raise FileNotFoundError("Non trovo una copia di collaudo sul Desktop.")
    newest_time = max(timestamp for timestamp, _ in candidates)
    newest = [path for timestamp, path in candidates if timestamp == newest_time]
    if len(newest) != 1:
        raise ValueError("Ci sono più copie create nello stesso istante: non posso scegliere con certezza.")
    folder = newest[0]
    _plain_entry(folder, directory=True)
    if folder.resolve(strict=True).parent != desktop:
        raise ValueError("La copia più recente non si trova direttamente sul Desktop.")
    snapshot = folder / "snapshot.sqlite"
    schema = folder / "schema_collaudo.json"
    _plain_entry(snapshot)
    _plain_entry(schema)
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(str(snapshot) + suffix):
            raise ValueError("La copia più recente ha file SQLite aggiuntivi: non è una copia autonoma.")
    try:
        with schema.open("rb") as stream:
            raw = stream.read(_SCHEMA_LIMIT + 1)
        if len(raw) > _SCHEMA_LIMIT:
            raise ValueError("Rapporto della struttura troppo grande")
        report = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_pairs,
                            parse_constant=_invalid_constant)
        if not (isinstance(report, dict) and type(report.get("format_version")) is int
                and report["format_version"] == 1
                and isinstance(report.get("source_file"), str) and report["source_file"].strip()
                and isinstance(report.get("objects"), list)):
            raise ValueError("Formato della struttura non riconosciuto")
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("La copia più recente non ha un rapporto della struttura valido.") from error
    with snapshot.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ValueError("Il file della copia più recente non è un database SQLite riconoscibile.")
    return snapshot
