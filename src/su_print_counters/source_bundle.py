"""Raccoglie staticamente i soli sorgenti del gestionale per la revisione privata.

Nessun sorgente viene importato o eseguito. Nessun database o documento viene
aperto e nessun risultato viene trasmesso in rete.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .snapshot import SnapshotCancelled


ARCHIVE_NAME = "SORGENTI_PANORAMICA.zip"
MAX_FILES = 5000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_DIRECTORIES = 3000
MAX_ENTRIES = 50000
MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
TIMEOUT_SECONDS = 30.0
_REPARSE_POINT = 0x400
_EXTENSIONS = frozenset({".py", ".ui", ".qss"})
_ROOT_TEXT = frozenset({"requirements.txt", "pyproject.toml"})
_EXCLUDED_DIRECTORIES = frozenset({
    "data", "documents", "documenti", "logs", "log", "cache", "caches",
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", "site-packages", "dist-packages", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
})
_SENSITIVE = frozenset({
    "secret", "secrets", "credential", "credentials", "password", "passwords",
    "token", "tokens", "privatekey", "privatekeys", "certificate", "certificates",
    "cert", "certs", "private", "key", "keys", "credenziali", "segreti", "chiavi",
})


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _sensitive(name):
    return name.casefold().startswith(".env") or bool(
        set(re.split(r"[^a-z0-9]+", Path(name).stem.casefold())) & _SENSITIVE
    )


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _check_parents(path, root):
    parent = path.parent
    while True:
        info = parent.lstat()
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("Una cartella dei sorgenti è cambiata durante la raccolta")
        if parent == root:
            return
        if root not in parent.parents:
            raise ValueError("Un sorgente non si trova nella cartella del programma")
        parent = parent.parent


def _member(name):
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def bundle_sources(application_root, output_root, *, cancel=None):
    """Restituisce una nuova cartella contenente lo ZIP dei sorgenti privati.

    Legge solo ``app/**/*.py|ui|qss``, i file Python direttamente nella radice
    del programma e requirements.txt/pyproject.toml. Le esclusioni nel manifesto
    sono percorsi relativi; non si esplorano le directory dati o backup.
    """
    cancel = cancel if cancel is not None else threading.Event()
    deadline = time.monotonic() + TIMEOUT_SECONDS

    def check():
        if cancel.is_set():
            raise SnapshotCancelled("Preparazione dei sorgenti annullata")
        if time.monotonic() >= deadline:
            raise ValueError("La raccolta dei sorgenti ha superato il tempo limite")

    check()
    original_root = Path(application_root)
    root_info = original_root.lstat()
    if _linked(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("La cartella del programma deve essere una cartella locale")
    root = original_root.resolve(strict=True)
    app = root / "app"
    app_info = app.lstat()
    if _linked(app_info) or not stat.S_ISDIR(app_info.st_mode):
        raise ValueError("Non trovo una cartella app locale con i sorgenti")
    output_root = Path(output_root).resolve(strict=True)
    if not output_root.is_dir() or output_root.is_relative_to(root):
        raise ValueError("I risultati devono essere salvati fuori dalla cartella del programma")

    files = []
    excluded = []
    total_bytes = entry_count = directory_count = 0
    pending = [(root, False), (app, True)]
    while pending:
        check()
        directory, recursive = pending.pop()
        directory_count += 1
        if directory_count > MAX_DIRECTORIES:
            raise ValueError("Ci sono troppe cartelle di sorgenti")
        info = directory.lstat()
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("Una cartella dei sorgenti è cambiata durante la raccolta")
        with os.scandir(directory) as entries:
            for entry in entries:
                check()
                entry_count += 1
                if entry_count > MAX_ENTRIES:
                    raise ValueError("Ci sono troppe voci nella cartella dei sorgenti")
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                info = entry.stat(follow_symlinks=False)
                name = entry.name.casefold()
                if _linked(info):
                    excluded.append({"path": relative, "reason": "collegamento"})
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if not recursive:
                        if path != app:
                            excluded.append({"path": relative, "reason": "fuori_app"})
                        continue
                    if name in _EXCLUDED_DIRECTORIES or name.startswith("backup") or _sensitive(entry.name):
                        excluded.append({"path": relative, "reason": "directory_esclusa"})
                    else:
                        pending.append((path, True))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    excluded.append({"path": relative, "reason": "tipo_non_regolare"})
                    continue
                allowed = (path.suffix.casefold() in _EXTENSIONS if recursive
                           else path.suffix.casefold() == ".py" or name in _ROOT_TEXT)
                if not allowed or _sensitive(entry.name):
                    excluded.append({"path": relative, "reason": "file_escluso"})
                    continue
                if "\\" in relative or any(part in ("", ".", "..") for part in relative.split("/")):
                    raise ValueError("Nome di sorgente non valido nell'archivio")
                if info.st_size > MAX_FILE_BYTES:
                    raise ValueError(f"Il sorgente {relative} supera il limite per file")
                total_bytes += info.st_size
                if total_bytes > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                    raise ValueError("I sorgenti superano il limite di raccolta")
                files.append((relative, path))
    if not any(Path(relative).suffix.casefold() == ".py" for relative, _ in files):
        raise ValueError("Non trovo sorgenti Python da preparare")
    if len({relative.casefold() for relative, _ in files}) != len(files):
        raise ValueError("Ci sono nomi di sorgenti ambigui")

    check()
    folder = Path(tempfile.mkdtemp(prefix="SU_Sorgenti_Panoramica_", dir=output_root))
    try:
        archive_path = folder / ARCHIVE_NAME
        manifest_files = []
        final_checks = []
        actual_total_bytes = 0
        with ZipFile(archive_path, "x", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, path in sorted(files):
                check()
                _check_parents(path, root)
                before = path.lstat()
                if _linked(before) or not stat.S_ISREG(before.st_mode):
                    raise ValueError("Un sorgente è cambiato durante la raccolta")
                if before.st_size > MAX_FILE_BYTES:
                    raise ValueError("Un sorgente supera il limite per file")
                digest = hashlib.sha256()
                copied = 0
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags)
                with os.fdopen(descriptor, "rb") as source:
                    if _signature(os.fstat(source.fileno())) != _signature(before):
                        raise ValueError("Un sorgente è cambiato durante l'apertura")
                    with archive.open(_member("sorgenti/" + relative), "w") as target:
                        for chunk in iter(lambda: source.read(64 * 1024), b""):
                            check()
                            copied += len(chunk)
                            actual_total_bytes += len(chunk)
                            if copied > MAX_FILE_BYTES or copied > before.st_size:
                                raise ValueError("Un sorgente è cambiato durante la lettura")
                            if actual_total_bytes > MAX_TOTAL_BYTES:
                                raise ValueError("I sorgenti superano il limite totale di raccolta")
                            digest.update(chunk)
                            target.write(chunk)
                    if _signature(os.fstat(source.fileno())) != _signature(before):
                        raise ValueError("Un sorgente è cambiato durante la lettura")
                if copied != before.st_size or _signature(path.lstat()) != _signature(before):
                    raise ValueError("Un sorgente è cambiato durante la raccolta")
                manifest_files.append({"path": relative, "size_bytes": copied, "sha256": digest.hexdigest()})
                final_checks.append((path, _signature(before)))
                if archive.fp.tell() > MAX_ARCHIVE_BYTES:
                    raise ValueError("Lo ZIP dei sorgenti supera il limite di dimensione")
            check()
            manifest = {"format_version": 1, "purpose": "revisione_integrazione_panoramica",
                        "files": manifest_files, "excluded": sorted(excluded, key=lambda item: item["path"]),
                        "total_source_bytes": sum(item["size_bytes"] for item in manifest_files)}
            archive.writestr(_member("manifest_sorgenti.json"),
                             json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("Lo ZIP dei sorgenti supera il limite di dimensione")
        check()
        with ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("Lo ZIP dei sorgenti non supera la verifica di integrità")
        for path, expected in final_checks:
            check()
            _check_parents(path, root)
            if _signature(path.lstat()) != expected:
                raise ValueError("I sorgenti sono cambiati durante la preparazione")
        return folder
    except BaseException as error:
        try:
            shutil.rmtree(folder)
        except OSError as cleanup_error:
            raise RuntimeError(f"Sorgenti incompleti da rimuovere: {folder}. {cleanup_error}") from error
        raise
