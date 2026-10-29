"""Indice limitato dei moduli sorgente utili a chiarire il protocollo Agent.

Nessun modulo viene importato o eseguito; si esportano soltanto percorsi relativi
e i nomi delle tre tabelle cercate, mai righe o altri contenuti dei sorgenti.
"""

import math
import os
import time
from pathlib import Path


MARKERS = (
    "su_remote_telemetry_log",
    "machine_meter_readings",
    "su_remote_agent_printers",
)
MAX_SOURCE_BYTES = 1024 * 1024
MAX_CANDIDATES = 20
EXCLUDED_DIRECTORIES = frozenset({
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env",
    "site-packages", "dist-packages", "vendor", "vendors", "third_party",
    "third-party", "thirdparty", "node_modules", "build", "dist", "cache",
    "caches", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
})


def index_sources(root, *, max_files=500, timeout_seconds=5.0):
    """Cerca riferimenti alle tabelle, con limiti di dimensione, tempo e quantità.

    ``complete`` riguarda lo scope dei file .py, escluse le directory dichiarate
    in EXCLUDED_DIRECTORIES. Errori, percorsi esterni, file troppo grandi o altri
    limiti lo rendono False. ``scanned_files`` conta solo i file letti per intero.
    """
    result = {"available": False, "complete": False, "scanned_files": 0, "candidates": []}

    def finish():
        result["candidates"].sort(key=lambda candidate: candidate["relative_path"])
        return result

    try:
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            return finish()
    except (OSError, ValueError, RuntimeError):
        return finish()
    result["available"] = True
    if type(max_files) is not int or max_files < 0:
        return finish()
    if not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        return finish()
    deadline = time.monotonic() + timeout_seconds
    result["complete"] = True
    attempted_files = 0
    visited_directories = set()
    visited_files = set()
    pending = [root]
    while pending:
        if time.monotonic() >= deadline:
            result["complete"] = False
            return finish()
        directory = pending.pop()
        try:
            resolved_directory = directory.resolve(strict=True)
            if not resolved_directory.is_relative_to(root):
                result["complete"] = False
                continue
            if resolved_directory in visited_directories:
                continue
            visited_directories.add(resolved_directory)
            with os.scandir(resolved_directory) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline:
                        result["complete"] = False
                        return finish()
                    try:
                        path = Path(entry.path)
                        resolved = path.resolve(strict=True)
                        if not resolved.is_relative_to(root):
                            result["complete"] = False
                            continue
                        relative = resolved.relative_to(root)
                        if any(part.casefold() in EXCLUDED_DIRECTORIES for part in relative.parts[:-1]):
                            continue
                        if resolved.is_dir():
                            if path.name.casefold() not in EXCLUDED_DIRECTORIES and resolved.name.casefold() not in EXCLUDED_DIRECTORIES:
                                pending.append(resolved)
                            continue
                        if path.suffix.casefold() != ".py" or resolved.suffix.casefold() != ".py" or not resolved.is_file():
                            continue
                        if resolved in visited_files:
                            continue
                        if attempted_files >= max_files:
                            result["complete"] = False
                            return finish()
                        attempted_files += 1
                        visited_files.add(resolved)
                        if resolved.stat().st_size > MAX_SOURCE_BYTES:
                            result["complete"] = False
                            continue
                        with resolved.open("rb") as stream:
                            content = stream.read(MAX_SOURCE_BYTES + 1)
                        if len(content) > MAX_SOURCE_BYTES:
                            result["complete"] = False
                            continue
                        if time.monotonic() >= deadline:
                            result["complete"] = False
                            return finish()
                        result["scanned_files"] += 1
                        markers = [marker for marker in MARKERS if marker.encode("ascii") in content]
                        if markers:
                            if len(result["candidates"]) >= MAX_CANDIDATES:
                                result["complete"] = False
                                result["omitted_candidates"] = result.get("omitted_candidates", 0) + 1
                            else:
                                result["candidates"].append({
                                    "relative_path": relative.as_posix(),
                                    "markers": markers,
                                })
                    except (OSError, ValueError, RuntimeError):
                        result["complete"] = False
        except (OSError, ValueError, RuntimeError):
            result["complete"] = False
    return finish()
