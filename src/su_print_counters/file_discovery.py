"""Individua copie locali dai file, senza dipendere dal nome della cartella."""

import os
import stat
import time
from pathlib import Path


_SKIP = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__"})
_REPARSE_POINT = 0x400


class SnapshotNotFound(FileNotFoundError):
    """La ricerca completata non ha individuato alcun file della copia."""


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def discover_snapshot(locations, *, max_depth=6, max_directories=4000,
                      max_entries=50000, timeout_seconds=5):
    """Cerca file di copia in un insieme limitato di cartelle locali.

    La data di modifica del rapporto schema, scritto per ultimo dal nostro
    helper, è il criterio di completamento disponibile. Non prova la creazione
    fisica della copia. Una copia recente incompleta non viene scartata per
    selezionarne silenziosamente una vecchia.
    """
    from .simple_selection import _plain_entry, validate_snapshot_file

    if (type(max_depth) is not int or max_depth < 0
            or type(max_directories) is not int or max_directories < 1
            or type(max_entries) is not int or max_entries < 1
            or timeout_seconds <= 0):
        raise ValueError("Limiti di ricerca non validi")
    started = time.monotonic()

    def check_time():
        if time.monotonic() - started >= timeout_seconds:
            raise ValueError("La ricerca delle copie richiede troppo tempo: non posso scegliere da una ricerca incompleta.")

    roots = []
    root_keys = set()
    for location in locations:
        check_time()
        try:
            root = Path(location).resolve(strict=True)
        except FileNotFoundError:
            continue
        if not root.is_dir():
            continue
        key = os.path.normcase(str(root))
        if key not in root_keys:
            root_keys.add(key)
            roots.append(root)
    pending = [(root, 0, root) for root in roots]
    visited = {}
    candidates = {}
    directory_count = entry_count = 0
    while pending:
        check_time()
        folder, depth, root = pending.pop()
        key = os.path.normcase(str(folder))
        remaining_depth = max_depth - depth
        if visited.get(key, -1) >= remaining_depth:
            continue
        visited[key] = remaining_depth
        directory_count += 1
        if directory_count > max_directories:
            raise ValueError("Ci sono troppe cartelle: la ricerca delle copie non è completa.")
        _plain_entry(folder, directory=True)
        names = {}
        children = []
        with os.scandir(folder) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > max_entries:
                    raise ValueError("Ci sono troppi file: la ricerca delle copie non è completa.")
                check_time()
                name = entry.name.casefold()
                if name in ("snapshot.sqlite", "schema_collaudo.json"):
                    if name in names:
                        raise ValueError("Ci sono nomi di file di copia ambigui nella stessa cartella.")
                    names[name] = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode) and not _linked(info) and name not in _SKIP:
                    children.append(Path(entry.path))
        if names:
            marker = names.get("schema_collaudo.json", names.get("snapshot.sqlite"))
            timestamp = marker.lstat().st_mtime_ns
            snapshot = names.get("snapshot.sqlite", folder / "snapshot.sqlite")
            candidates[key] = (timestamp, snapshot, root)
        if depth < max_depth:
            pending.extend((child, depth + 1, root) for child in children)
    check_time()
    if not candidates:
        searched = "\n".join(str(root) for root in roots[:6])
        raise SnapshotNotFound("Non trovo snapshot.sqlite nelle cartelle controllate:\n" + searched)
    newest_time = max(item[0] for item in candidates.values())
    newest = [item for item in candidates.values() if item[0] == newest_time]
    if len(newest) != 1:
        raise ValueError("Ci sono più copie con la stessa data di completamento: non posso scegliere con certezza.")
    _, snapshot, root = newest[0]
    # Ricontrollare l'intero percorso scelto prima di aprire i file della copia.
    parent = snapshot.parent
    while parent != root:
        _plain_entry(parent, directory=True)
        if root not in parent.parents:
            raise ValueError("La copia non si trova nelle cartelle controllate.")
        parent = parent.parent
    _plain_entry(root, directory=True)
    return validate_snapshot_file(snapshot)
