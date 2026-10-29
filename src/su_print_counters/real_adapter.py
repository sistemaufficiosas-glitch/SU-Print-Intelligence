"""Adattatore V1 del registro centrale Remote Agent, su snapshot autonomi."""

import hashlib
import json
import math
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .diagnostics import _json_object, _nonstandard_number
from .engine import serial
from .snapshot import SnapshotCancelled


METRICS = ("black_counter", "color_counter", "total_counter")
MAX_COUNTER = 2_147_483_647
PLACEHOLDERS = {"UNKNOWN", "N/A", "NA", "N/D", "ND", "NONE", "NULL", "-", "?", "0",
                "UNAVAILABLE", "UNDEFINED", "NOT AVAILABLE", "NON DISPONIBILE"}
REQUIRED = {
    "printers": ("id", "serial_number"),
    "su_remote_agents": ("id", "status", "revoked_at"),
    "su_remote_agent_printers": ("agent_id", "printer_id", "snmp_readonly"),
    "su_remote_telemetry_log": ("id", "agent_id", "printer_id", "accepted", "received_at", "payload_json"),
}


def _valid_serial(value):
    normalized = serial(value)
    characters = {character for character in normalized if character.isalnum()}
    return bool(normalized and normalized not in PLACEHOLDERS and characters and characters != {"0"})


def _iso(value):
    if not isinstance(value, str) or not ("T" in value or " " in value.strip()):
        raise ValueError("Timestamp completo mancante")
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def collect_real(database, *, now=None, cancel=None, timeout_seconds=180.0):
    """Controlla il registro accettato, conservando storia e anomalie separatamente.

    L'ultima utilizzabile proviene da un'associazione attualmente autorizzata.
    Lo storico accettato a monte può precedere il binding attuale. Il totale è
    quello dichiarato nel log: la provenienza del candidato non è ricostruibile.
    Nessuna dipendenza dai servizi o dalle credenziali dell'applicazione attiva.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Tempo limite non valido")
    cancel = cancel if cancel is not None else threading.Event()
    deadline = time.monotonic() + timeout_seconds
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now deve avere un fuso orario")
    now = now.astimezone(timezone.utc)

    def check():
        if cancel.is_set():
            raise SnapshotCancelled("Collaudo annullato")
        if time.monotonic() >= deadline:
            raise TimeoutError("Tempo limite del collaudo superato")

    check()
    path = Path(database).resolve(strict=True)
    if not path.is_file() or path.name.casefold() != "snapshot.sqlite":
        raise ValueError("Selezionare la copia chiusa snapshot.sqlite")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("La copia non è autonoma: presenti WAL/SHM/journal")
    initial_stat = path.stat()
    fingerprint = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            check()
            fingerprint.update(chunk)
    anomalies = []

    def issue(code, kind, entity_id, detail):
        anomalies.append({"code": code, "entity_type": kind, "entity_id": entity_id, "detail": detail})

    candidates = defaultdict(list)
    source_telemetry = 0
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            db.set_progress_handler(lambda: int(cancel.is_set() or time.monotonic() >= deadline), 1000)
            columns = {}
            for table, expected in REQUIRED.items():
                columns[table] = {row["name"] for row in db.execute("SELECT name FROM pragma_table_info(?)", (table,))}
                missing = set(expected) - columns[table]
                if missing:
                    raise ValueError(f"Schema non supportato: {table}, colonne mancanti: {', '.join(sorted(missing))}")

            def index(records, table):
                result = {}
                for record in records:
                    key = record["id"]
                    if type(key) is not int or key <= 0 or key in result:
                        raise ValueError(f"Identificativi non validi o duplicati: {table}")
                    result[key] = dict(record)
                return result

            excluded_column = "remote_agent_excluded" if "remote_agent_excluded" in columns["printers"] else "0 AS remote_agent_excluded"
            printers = index(db.execute("SELECT id,serial_number," + excluded_column + " FROM printers"), "printers")
            agents = index(db.execute("SELECT id,status,revoked_at FROM su_remote_agents"), "su_remote_agents")
            binding_address = "allowed_address" if "allowed_address" in columns["su_remote_agent_printers"] else "'' AS allowed_address"
            bindings = [dict(row) for row in db.execute("SELECT agent_id,printer_id,snmp_readonly," + binding_address + " FROM su_remote_agent_printers")]

            def optional_rules(table, fields):
                known = {row["name"] for row in db.execute("SELECT name FROM pragma_table_info(?)", (table,))}
                if not known:
                    return []
                if not set(fields) <= known:
                    raise ValueError(f"Schema delle esclusioni incompleto: {table}")
                return [dict(row) for row in db.execute("SELECT " + ",".join(fields) + " FROM " + table)]

            exclusions = optional_rules("su_remote_monitoring_exclusions", ("agent_id", "printer_id", "serial_number", "allowed_address"))
            ignored = optional_rules("su_remote_agent_ignored_devices", ("agent_id", "serial_number", "allowed_address"))
            identities = Counter(serial(p["serial_number"]) for p in printers.values())
            valid_printers = set()
            for pid, printer in printers.items():
                if not _valid_serial(printer["serial_number"]):
                    issue("MISSING_SERIAL", "printer", pid, "Matricola mancante o non identificativa")
                elif identities[serial(printer["serial_number"])] > 1:
                    issue("AMBIGUOUS_SERIAL", "printer", pid, "Matricola duplicata; nessuna fusione automatica")
                else:
                    valid_printers.add(pid)

            def denied(binding):
                pid, aid = binding["printer_id"], binding["agent_id"]
                if pid not in printers:
                    return True
                printer = printers[pid]
                flag = printer["remote_agent_excluded"]
                if type(flag) is not int or flag != 0:
                    return True
                address = str(binding["allowed_address"] or "").strip()
                for rule in exclusions + ignored:
                    if rule["agent_id"] != aid:
                        continue
                    if rule.get("printer_id") == pid:
                        return True
                    if serial(rule["serial_number"]) and serial(rule["serial_number"]) == serial(printer["serial_number"]):
                        return True
                    # Solo filtro di autorizzazione sul binding, mai identificazione
                    # o ricongiungimento di letture tramite indirizzo.
                    blocked_address = str(rule["allowed_address"] or "").strip()
                    if blocked_address and address and blocked_address == address:
                        return True
                return False

            authorized = set()
            for binding in bindings:
                aid, pid = binding["agent_id"], binding["printer_id"]
                agent = agents.get(aid)
                if (pid in valid_printers and agent and str(agent["status"] or "").strip().casefold() == "attivo"
                        and not str(agent["revoked_at"] or "").strip()
                        and type(binding["snmp_readonly"]) is int and binding["snmp_readonly"] == 1
                        and not denied(binding)):
                    authorized.add((aid, pid))

            timestamp_info = next(row for row in db.execute("SELECT name,dflt_value FROM pragma_table_info('su_remote_telemetry_log')") if row["name"] == "received_at")
            sqlite_utc_default = str(timestamp_info["dflt_value"] or "").strip().strip("() ").upper() == "CURRENT_TIMESTAMP"
            trigger_count = db.execute("SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND tbl_name='su_remote_telemetry_log'").fetchone()[0]
            naive_receipt_confirmed = sqlite_utc_default and trigger_count == 0
            seen_readings = set()
            for row in db.execute("SELECT id,agent_id,printer_id,accepted,received_at,payload_json FROM su_remote_telemetry_log"):
                check()
                source_telemetry += 1
                rid, aid, pid = row["id"], row["agent_id"], row["printer_id"]
                if type(rid) is not int or rid <= 0 or rid in seen_readings:
                    raise ValueError("Identificativi telemetria nulli o duplicati")
                seen_readings.add(rid)

                def reject(code, detail):
                    issue(code, "reading", rid, detail)

                if type(row["accepted"]) is not int or row["accepted"] != 1:
                    reject("REJECTED_TELEMETRY", "Messaggio non accettato dal registro centrale")
                    continue
                if aid not in agents:
                    reject("MISSING_AGENT", "Remote Agent non riconosciuto nel registro")
                    continue
                if pid not in valid_printers:
                    reject("UNIDENTIFIABLE_PRINTER", "Macchina assente, senza matricola o con matricola duplicata")
                    continue
                raw = row["payload_json"]
                try:
                    if not isinstance(raw, str) or len(raw) > 512 * 1024:
                        raise ValueError("JSON assente o troppo grande")
                    payload = json.loads(raw, object_pairs_hook=_json_object, parse_constant=_nonstandard_number)
                    if not isinstance(payload, dict):
                        raise ValueError("Il payload non è un oggetto")
                except (ValueError, RecursionError):
                    reject("INVALID_JSON", "Payload JSON non valido o ambiguo")
                    continue
                if type(payload.get("printer_id")) is not int or payload["printer_id"] != pid:
                    reject("PRINTER_ID_MISMATCH", "Identità macchina discordante tra riga e payload")
                    continue
                declared_serial = payload.get("serial_number")
                if not _valid_serial(declared_serial):
                    reject("MISSING_SERIAL", "La telemetria non dichiara una matricola identificativa")
                    continue
                if serial(declared_serial) != serial(printers[pid]["serial_number"]):
                    reject("SERIAL_MISMATCH", "Matricola discordante; lettura non riassociata ad altre macchine")
                    continue
                if payload.get("snmp_ok") is not True:
                    reject("SNMP_FAILED", "Manca una lettura SNMP esplicitamente riuscita")
                    continue
                values = {"black_counter": payload.get("black_counter"), "color_counter": payload.get("color_counter"), "total_counter": payload.get("total_counter_candidate")}
                if any(value is not None and (type(value) is not int or not 0 <= value <= MAX_COUNTER) for value in values.values()):
                    reject("INVALID_COUNTER", "Contatore non intero, negativo o fuori intervallo")
                    continue
                if all(value is None for value in values.values()):
                    reject("NO_COUNTERS", "Nessun contatore disponibile")
                    continue
                total = values["total_counter"]
                if total is not None and any(value is not None and value > total for value in (values["black_counter"], values["color_counter"])):
                    reject("INCONSISTENT_COUNTERS", "Totale dichiarato inferiore a una componente")
                    continue
                try:
                    received = _iso(row["received_at"])
                    if received.tzinfo is None:
                        if not naive_receipt_confirmed:
                            raise ValueError("Fuso di ricezione non confermato dallo schema")
                        received = received.replace(tzinfo=timezone.utc)
                    received = received.astimezone(timezone.utc)
                    collected = _iso(payload.get("collected_at"))
                    if received > now:
                        raise ValueError("Data di ricezione futura")
                    if collected.tzinfo is not None and collected.astimezone(timezone.utc) > received + timedelta(seconds=300):
                        raise ValueError("Data di raccolta futura rispetto alla ricezione")
                except (ValueError, TypeError, OverflowError):
                    reject("INVALID_TIMESTAMP", "Date mancanti/non valide o fuso di ricezione non confermato")
                    continue
                candidates[pid].append({
                    "reading_id": rid, "agent_id": aid, "received_at": received.isoformat(),
                    "collected_at": payload["collected_at"].strip(), "collected_at_has_timezone": collected.tzinfo is not None,
                    **values, "currently_authorized": (aid, pid) in authorized,
                })
    except sqlite3.OperationalError:
        check()
        raise

    included, archive = [], []
    for pid, printer in printers.items():
        check()
        groups = defaultdict(list)
        for row in candidates[pid]:
            check()
            groups[row["received_at"]].append(row)
        history = []
        baseline = {}
        for stamp, group in sorted(groups.items()):
            check()
            if any(len({row[key] for row in group if row[key] is not None}) > 1 for key in METRICS):
                for row in group:
                    issue("CONFLICTING_READING", "reading", row["reading_id"], "Contatori discordanti allo stesso istante di ricezione")
                continue
            for row in sorted(group, key=lambda r: r["reading_id"]):
                check()
                if any(row[key] is not None and key in baseline and row[key] < baseline[key] for key in METRICS):
                    issue("COUNTER_REGRESSION", "reading", row["reading_id"], "Un contatore scende rispetto al precedente valore accettato")
                    continue
                history.append(row)
                baseline.update({key: row[key] for key in METRICS if row[key] is not None})
        usable = [row for row in history if row["currently_authorized"]]
        item = {"printer_id": pid, "serial": serial(printer["serial_number"]), "history": history,
                "latest": usable[-1] if usable else None,
                "latest_by_counter": {key: next((row for row in reversed(usable) if row[key] is not None), None) for key in METRICS}}
        if usable:
            included.append(item)
        else:
            if pid not in valid_printers:
                reason = "MISSING_OR_AMBIGUOUS_SERIAL"
            elif not any(pair[1] == pid for pair in authorized):
                reason = "NO_CURRENT_AGENT"
            else:
                reason = "NO_VALID_CURRENT_READING"
            item["reason"] = reason
            archive.append(item)
            issue(reason, "printer", pid, "Macchina esclusa dall'elenco utilizzabile; storico separato se disponibile")
    check()
    final_stat = path.stat()
    if (final_stat.st_size, final_stat.st_mtime_ns) != (initial_stat.st_size, initial_stat.st_mtime_ns):
        raise ValueError("La copia è cambiata durante il collaudo")
    all_history = [row for printer in included + archive for row in printer["history"]]
    codes = dict(Counter(item["code"] for item in anomalies))
    return {
        "format_version": 1, "adapter": "su_print_v1", "status": "REVIEW_REQUIRED",
        "snapshot_sha256": fingerprint.hexdigest(),
        "time_policy": {"order_by": "received_at_utc", "naive_receipt_utc_default_confirmed": naive_receipt_confirmed,
                        "collected_at": "Conservato; nessun fuso inventato per valori senza offset"},
        "total_policy": {"source": "total_counter_candidate", "origin_verified": False,
                         "note": "Il codice precedente può derivare il candidato dai marker; il log non distingue l'origine. Nessuna somma, massimo o conversione aggiunti dal V1."},
        "provenance": {"source": "su_remote_telemetry_log", "offline_hmac_rechecked": False,
                       "historical_acceptance": "Attestazione del registro centrale; non una nuova verifica crittografica"},
        "printers": sorted(included, key=lambda item: item["serial"]),
        "archive": sorted(archive, key=lambda item: item["printer_id"]),
        "anomalies": anomalies, "anomalies_by_code": codes,
        "summary": {"source_printers": len(printers), "source_agents": len(agents), "source_telemetry": source_telemetry,
                    "included_printers": len(included), "excluded_printers": len(archive), "validated_readings": len(all_history),
                    "rejected_readings": source_telemetry - len(all_history),
                    "current_readings": sum(row["currently_authorized"] for row in all_history),
                    "historical_readings": sum(not row["currently_authorized"] for row in all_history),
                    "collected_without_timezone": sum(not row["collected_at_has_timezone"] for row in all_history)},
    }
