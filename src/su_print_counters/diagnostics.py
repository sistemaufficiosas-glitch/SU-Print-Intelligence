"""Diagnostica strutturale della copia: non certifica ancora le letture reali."""

import hashlib
import json
import math
import os
import queue
import sqlite3
import threading
import time
import uuid
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .engine import serial
from .snapshot import SnapshotCancelled, desktop_directory
from .source_index import index_sources


REQUIRED_COLUMNS = {
    "printers": ("id", "serial_number"),
    "su_remote_agents": ("id", "status", "revoked_at"),
    "su_remote_agent_printers": ("agent_id", "printer_id"),
    "su_remote_telemetry_log": ("id", "agent_id", "printer_id", "accepted", "received_at", "payload_json"),
    "machine_meter_readings": ("id", "machine_id", "agent_id", "source", "read_at", "raw_json"),
}

# I nomi non riconosciuti possono essere chiavi dinamiche contenenti IP, matricole
# o clienti. Collassarli senza esportarli; nessun valore libero viene pubblicato.
SAFE_KEYS = set("""
agent agent_id printer printer_id machine machine_id device device_info data
payload result results reading readings telemetry event metrics meters counters
print_counters meter counter serial serial_number printer_serial device_serial
device_serial_number matricola total total_counter counter_total total_pages
page_count total_count black black_counter counter_black bw mono color colour
color_counter counter_color timestamp observed_at read_at collected_at checked_at
received_at created_at time date status success valid is_valid counter_valid
snmp snmp_success snmp_status snmp_ok online reachable error error_code errors
message source meter_source manufacturer brand model firmware name address ip
ip_address target_ip supplies toner ink scan_counter scans copy_counter print_counter
""".split())
SAFE_NORMALIZED = {key.replace("_", "").casefold() for key in SAFE_KEYS}
SERIAL_KEYS = {key.replace("_", "") for key in (
    "serial", "serial_number", "printer_serial", "device_serial", "device_serial_number", "matricola"
)}
TIME_KEYS = {key.replace("_", "") for key in (
    "timestamp", "observed_at", "read_at", "collected_at", "checked_at", "received_at", "created_at", "time", "date"
)}
FLAG_KEYS = {key.replace("_", "") for key in (
    "success", "valid", "is_valid", "counter_valid", "snmp_success", "snmp_ok", "online", "reachable"
)}
SAFE_LABELS = set("""
active enabled enrolled revoked disabled pending inactive approved rejected online
offline ok success successful error failed failure valid invalid true false ready
remote_agent remote-agent agent remote telemetry agent_telemetry remote_agent_telemetry
snmp manual import local unknown accepted denied not_available
""".split())
MAX_JSON_CHARS = 512 * 1024
MAX_DEPTH = 8
MAX_NODES = 1500
MAX_ARRAY_ITEMS = 3
MAX_PROFILE_PATHS = 2000
MAX_KEY_CHARS = 64


def _type(value):
    if value is None:
        return "null"
    return {bool: "boolean", int: "integer", float: "number", str: "string", dict: "object", list: "array"}.get(type(value), "other")


def _time_form(value):
    if not isinstance(value, str) or not value.strip():
        return "missing_or_non_text"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "unrecognized"
    if len(value.strip()) == 10:
        return "date_only"
    return "with_timezone" if parsed.tzinfo is not None else "without_timezone"


def _labels(rows):
    counts = Counter()
    for value, count in rows:
        label = value.strip().casefold() if isinstance(value, str) else None
        counts[label if label in SAFE_LABELS else ("null" if value is None else "other")] += count
    return counts


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chiavi JSON duplicate")
        result[key] = value
    return result


def _nonstandard_number(value):
    raise ValueError("Numero JSON non standard")


def _json_profile(rows, total_rows, check):
    profile = {
        "total_rows": total_rows, "sampled_rows": 0, "invalid_json": 0,
        "root_types": Counter(), "paths": {}, "redacted_key_occurrences": 0,
        "truncated_values": 0, "database_timestamp_forms": Counter(),
    }

    for raw, reference_serial, database_timestamp in rows:
        check()
        profile["sampled_rows"] += 1
        profile["database_timestamp_forms"][_time_form(database_timestamp)] += 1
        if not isinstance(raw, str):
            profile["invalid_json"] += 1
            continue
        if len(raw) > MAX_JSON_CHARS:
            profile["truncated_values"] += 1
            continue
        try:
            value = json.loads(raw, object_pairs_hook=_json_object, parse_constant=_nonstandard_number)
        except (ValueError, RecursionError):
            profile["invalid_json"] += 1
            continue
        profile["root_types"][_type(value)] += 1
        budget = [MAX_NODES]

        def visit(node, path, depth, field=""):
            if depth > MAX_DEPTH or budget[0] <= 0:
                profile["truncated_values"] += 1
                return
            if path not in profile["paths"] and len(profile["paths"]) >= MAX_PROFILE_PATHS:
                profile["truncated_values"] += 1
                return
            budget[0] -= 1
            item = profile["paths"].setdefault(path, {"types": Counter()})
            item["types"][_type(node)] += 1
            normalized = field.replace("_", "").casefold()
            if normalized in SERIAL_KEYS:
                if not isinstance(node, str) or not serial(node):
                    category = "missing_or_non_text"
                elif not serial(reference_serial):
                    category = "missing_reference"
                else:
                    category = "matches_reference" if serial(node) == serial(reference_serial) else "different_reference"
                item.setdefault("serial_comparisons", Counter())[category] += 1
            if normalized in TIME_KEYS:
                item.setdefault("timestamp_forms", Counter())[_time_form(node)] += 1
            if normalized in FLAG_KEYS:
                if type(node) is bool:
                    flag = "boolean_true" if node else "boolean_false"
                elif type(node) is int and node in (0, 1):
                    flag = "integer_one" if node else "integer_zero"
                else:
                    flag = "other"
                item.setdefault("flags", Counter())[flag] += 1
            if normalized in {"status", "snmpstatus", "source", "metersource"}:
                label = node.strip().casefold() if isinstance(node, str) else None
                item.setdefault("labels", Counter())[label if label in SAFE_LABELS else "other"] += 1
            if isinstance(node, dict):
                for key, child in node.items():
                    if budget[0] <= 0:
                        profile["truncated_values"] += 1
                        break
                    known = len(key) <= MAX_KEY_CHARS and key.replace("_", "").casefold() in SAFE_NORMALIZED
                    if not known:
                        profile["redacted_key_occurrences"] += 1
                    visit(child, path + "." + (key if known else "<other>"), depth + 1, key if known else "")
            elif isinstance(node, list):
                if len(node) > MAX_ARRAY_ITEMS:
                    profile["truncated_values"] += 1
                for child in node[:MAX_ARRAY_ITEMS]:
                    visit(child, path + "[]", depth + 1)

        visit(value, "$", 0)
    return profile


def diagnose_snapshot(database, *, sample_limit=40, cancel=None, timeout_seconds=120.0):
    """Report di soli metadati/conteggi; stato sempre non certificato.

    Legge campioni recenti separati per accepted=1, accepted=0 e altri flag, più
    machine_meter_readings. La presenza di un campo non ne certifica il significato.
    """
    if type(sample_limit) is not int or not 1 <= sample_limit <= 200:
        raise ValueError("Il campione deve essere compreso fra 1 e 200")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Tempo limite non valido")
    cancel = cancel if cancel is not None else threading.Event()
    deadline = time.monotonic() + timeout_seconds

    def check():
        if cancel.is_set():
            raise SnapshotCancelled("Diagnostica annullata")
        if time.monotonic() >= deadline:
            raise TimeoutError("Tempo limite della diagnostica superato")

    check()
    path = Path(database).resolve(strict=True)
    if not path.is_file() or path.name.casefold() != "snapshot.sqlite":
        raise ValueError("Selezionare snapshot.sqlite, la copia di collaudo")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("La copia non è autonoma: presenti file WAL/SHM/journal")
    initial_stat = path.stat()
    fingerprint = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            check()
            fingerprint.update(chunk)
    report = {
        "format_version": 1, "status": "SEMANTICS_UNCONFIRMED",
        "snapshot_sha256": fingerprint.hexdigest(), "sample_limit_per_group": sample_limit,
        "sampling": "Righe più recenti per ID, separando accepted=1, accepted=0, altri flag e letture contatori",
        "limits": {"json_chars": MAX_JSON_CHARS, "depth": MAX_DEPTH, "nodes_per_json": MAX_NODES,
                   "array_items": MAX_ARRAY_ITEMS, "paths_per_profile": MAX_PROFILE_PATHS, "key_chars": MAX_KEY_CHARS},
        "missing_columns": [], "table_rows": {}, "json_profiles": {},
        "notes": [
            "Il rapporto non certifica che accepted=1 significhi contatore valido.",
            "I confronti di matricola riguardano campi omonimi: va confermato il percorso corretto.",
            "Certificate_serial è un identificativo del certificato Agent, non della stampante.",
            "Valori liberi e nomi JSON sconosciuti sono omessi; nessuna riga o payload è esportato.",
            "Un campione recente non certifica l'intero storico e può non coprire formati precedenti.",
        ],
    }
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            db.set_progress_handler(lambda: int(cancel.is_set() or time.monotonic() >= deadline), 1000)
            for table, required in REQUIRED_COLUMNS.items():
                columns = {row[0] for row in db.execute("SELECT name FROM pragma_table_info(?)", (table,))}
                report["missing_columns"].extend(f"{table}.{column}" for column in required if column not in columns)
            if report["missing_columns"]:
                report["status"] = "SCHEMA_INCOMPLETE"
            else:
                for table in REQUIRED_COLUMNS:
                    # I nomi provengono solo dalla costante del profilo, mai dal JSON.
                    report["table_rows"][table] = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                report["printer_identity"] = {
                    "missing_serials": db.execute("SELECT count(*) FROM printers WHERE serial_number IS NULL OR trim(serial_number)=''").fetchone()[0],
                    "duplicate_serial_groups": db.execute("SELECT count(*) FROM (SELECT upper(trim(serial_number)) FROM printers WHERE typeof(serial_number)='text' AND trim(serial_number)<>'' GROUP BY upper(trim(serial_number)) HAVING count(*)>1)").fetchone()[0],
                }
                report["agent_statuses"] = _labels(db.execute("SELECT status,count(*) FROM su_remote_agents GROUP BY status"))
                report["reading_sources"] = _labels(db.execute("SELECT source,count(*) FROM machine_meter_readings GROUP BY source"))
                report["relationships"] = {
                    "telemetry_missing_agent": db.execute("SELECT count(*) FROM su_remote_telemetry_log t WHERE NOT EXISTS(SELECT 1 FROM su_remote_agents a WHERE a.id=t.agent_id)").fetchone()[0],
                    "telemetry_missing_printer": db.execute("SELECT count(*) FROM su_remote_telemetry_log t WHERE NOT EXISTS(SELECT 1 FROM printers p WHERE p.id=t.printer_id)").fetchone()[0],
                    "telemetry_missing_current_binding": db.execute("SELECT count(*) FROM su_remote_telemetry_log t WHERE NOT EXISTS(SELECT 1 FROM su_remote_agent_printers b WHERE b.agent_id=t.agent_id AND b.printer_id=t.printer_id)").fetchone()[0],
                }
                predicates = {
                    "accepted_1": "typeof(t.accepted)='integer' AND t.accepted=1",
                    "accepted_0": "typeof(t.accepted)='integer' AND t.accepted=0",
                    "other_accepted": "NOT (typeof(t.accepted)='integer' AND t.accepted IN (0,1))",
                }
                for name, predicate in predicates.items():
                    total = db.execute("SELECT count(*) FROM su_remote_telemetry_log t WHERE " + predicate).fetchone()[0]
                    rows = db.execute(
                        "SELECT t.payload_json,p.serial_number,t.received_at FROM su_remote_telemetry_log t "
                        "LEFT JOIN printers p ON p.id=t.printer_id WHERE " + predicate + " ORDER BY t.id DESC LIMIT ?",
                        (sample_limit,),
                    )
                    report["json_profiles"][name] = _json_profile(rows, total, check)
                rows = db.execute(
                    "SELECT r.raw_json,p.serial_number,r.read_at FROM machine_meter_readings r "
                    "LEFT JOIN printers p ON p.id=r.machine_id ORDER BY r.id DESC LIMIT ?", (sample_limit,),
                )
                report["json_profiles"]["meter_readings"] = _json_profile(rows, report["table_rows"]["machine_meter_readings"], check)
    except sqlite3.OperationalError:
        check()
        raise
    check()
    if (path.stat().st_size, path.stat().st_mtime_ns) != (initial_stat.st_size, initial_stat.st_mtime_ns):
        raise ValueError("La copia è cambiata durante la diagnostica; utilizzare una copia chiusa")
    return report


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.withdraw()
    desktop = desktop_directory()
    copies = sorted((p for p in desktop.glob("SU_Print_Collaudo_*") if p.is_dir()), key=lambda p: p.name)
    initial = copies[-1] if copies else desktop
    selected = filedialog.askopenfilename(
        parent=root, title="Seleziona snapshot.sqlite nella cartella di collaudo",
        initialdir=str(initial), initialfile="snapshot.sqlite", filetypes=[("Copia di collaudo", "snapshot.sqlite")],
    )
    if not selected:
        root.destroy()
        return 0
    path = Path(selected)
    root.title("SU Print — Diagnostica della copia")
    ttk.Label(root, text="Analisi della struttura dei messaggi sulla copia locale.\nAttendere…", padding=20).pack()
    bar = ttk.Progressbar(root, mode="indeterminate", length=400)
    bar.pack(padx=20, pady=10)
    bar.start()
    cancel = threading.Event()
    messages = queue.Queue()
    outcome = [1]

    def request_cancel():
        cancel.set()
        button.configure(state="disabled")

    button = ttk.Button(root, text="Annulla", command=request_cancel)
    button.pack(pady=15)
    root.protocol("WM_DELETE_WINDOW", request_cancel)

    def worker():
        output = None
        created = False
        try:
            report = diagnose_snapshot(path, cancel=cancel)
            app_source = Path(r"C:\SU PRINT INTELLIGENCE\PROGRAMMA ATTIVO\SU Print Intelligence ATTIVO 1.2.1 CON DATABASE\app")
            report["source_code_index"] = index_sources(app_source)
            if cancel.is_set():
                raise SnapshotCancelled("Diagnostica annullata")
            filename = "diagnostica_collaudo_" + uuid.uuid4().hex[:12] + ".json"
            output = path.parent / filename
            with output.open("x", encoding="utf-8") as stream:
                created = True
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            if cancel.is_set():
                raise SnapshotCancelled("Diagnostica annullata")
            messages.put(("done", filename))
        except Exception as error:
            if created:
                try:
                    output.unlink()
                except OSError:
                    error = RuntimeError(f"Rapporto incompleto da rimuovere: {output}")
            messages.put(("error", error))

    def poll():
        try:
            kind, value = messages.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        bar.stop()
        root.withdraw()
        if kind == "done":
            outcome[0] = 0
            messagebox.showinfo(
                "Rapporto pronto",
                f"Nella cartella della copia trovi:\n{value}\n\nAllega questo piccolo rapporto in chat.\n"
                "La semantica dei campi resta da confermare: non è ancora il collaudo delle letture.", parent=root,
            )
            if os.name == "nt":
                try:
                    os.startfile(str(path.parent))
                except OSError:
                    messagebox.showinfo("Cartella del rapporto", str(path.parent), parent=root)
        elif isinstance(value, SnapshotCancelled):
            outcome[0] = 0
            messagebox.showinfo("Annullato", str(value), parent=root)
        else:
            messagebox.showerror("Diagnostica non completata", str(value), parent=root)
        root.destroy()

    root.deiconify()
    threading.Thread(target=worker, daemon=False).start()
    root.after(100, poll)
    root.mainloop()
    return outcome[0]


if __name__ == "__main__":
    raise SystemExit(main())
