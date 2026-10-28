"""Analisi di snapshot SQLite senza scritture e senza dipendenze esterne."""

import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MAPPING = {
    "printers": {"table": "printers", "id": "id", "serial": "serial"},
    "agents": {"table": "remote_agents", "id": "id", "printer_id": "printer_id", "serial": "serial"},
    "readings": {"table": "telemetry", "id": "id", "agent_id": "agent_id", "serial": "serial", "timestamp": "observed_at", "total": "total_counter", "valid": "is_valid"},
}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Nome SQL non ammesso nella mappatura")
    return '"' + value + '"'


def serial(value):
    # Conservare punteggiatura e zeri iniziali: nessuna equivalenza euristica.
    return value.strip().upper() if isinstance(value, str) else ""


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp non testuale")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp senza fuso orario")
    return parsed.astimezone(timezone.utc)


def load_mapping(path=None):
    mapping = json.loads(Path(path).read_text(encoding="utf-8")) if path else DEFAULT_MAPPING
    if not isinstance(mapping, dict) or set(mapping) != set(DEFAULT_MAPPING):
        raise ValueError("La mappatura deve definire printers, agents e readings")
    for section, expected in DEFAULT_MAPPING.items():
        if not isinstance(mapping[section], dict) or set(mapping[section]) != set(expected):
            raise ValueError(f"Campi della mappatura non validi: {section}")
        for name in mapping[section].values():
            identifier(name)
    return mapping


def collect(database, mapping=None, *, now=None):
    """Restituisce storico accettato, ultima lettura e anomalie.

    Il chiamante fornisce una copia SQLite consistente e non in uso.
    Nessun parametro abilita scritture o accesso al programma attivo.
    """
    path = Path(database).resolve(strict=True)
    if not path.is_file():
        raise ValueError("La sorgente deve essere un file SQLite")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Snapshot non autonomo: presenti file WAL/SHM/journal")
    mapping = load_mapping(mapping)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now deve avere un fuso orario")
    anomalies = []

    def issue(kind, entity, detail):
        anomalies.append({"code": kind, "entity_id": entity, "detail": detail})

    # immutable evita anche la creazione di file journal/SHM; richiede snapshot chiuso.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")

        def rows(section):
            spec = mapping[section]
            fields = [key for key in spec if key != "table"]
            query = "SELECT " + ", ".join(identifier(spec[k]) for k in fields) + " FROM " + identifier(spec["table"])
            return [dict(zip(fields, row)) for row in db.execute(query)]

        printers, agents, readings = rows("printers"), rows("agents"), rows("readings")

    # Gli identificativi sorgente devono essere non nulli e univoci.
    for name, records in (("printers", printers), ("agents", agents), ("readings", readings)):
        ids = [record["id"] for record in records]
        if None in ids or len(set(ids)) != len(ids):
            raise ValueError(f"Identificativi nulli o duplicati: {name}")

    serial_counts = Counter(serial(p["serial"]) for p in printers)
    eligible = {}
    for printer in printers:
        identity = serial(printer["serial"])
        if not identity or serial_counts[identity] != 1:
            issue("AMBIGUOUS_PRINTER_SERIAL", printer["id"], "Matricola assente o duplicata; macchina esclusa")
        else:
            eligible[printer["id"]] = identity
    accepted_agents = {}
    for agent in agents:
        expected = eligible.get(agent["printer_id"])
        if expected is None:
            issue("UNKNOWN_OR_AMBIGUOUS_PRINTER", agent["id"], "Agent senza macchina identificabile")
        elif serial(agent["serial"]) != expected:
            issue("SERIAL_MISMATCH", agent["id"], "Matricola Agent discordante; Agent escluso")
        else:
            accepted_agents[agent["id"]] = agent

    history = {pid: [] for pid in eligible}
    for reading in readings:
        agent = accepted_agents.get(reading["agent_id"])
        if agent is None:
            issue("UNTRUSTED_AGENT", reading["id"], "Telemetria senza Remote Agent riconosciuto")
            continue
        pid = agent["printer_id"]
        if serial(reading["serial"]) != eligible[pid]:
            issue("SERIAL_MISMATCH", reading["id"], "Matricola telemetria discordante; lettura esclusa")
            continue
        if type(reading["valid"]) is not int or reading["valid"] != 1:
            issue("INVALID_TELEMETRY", reading["id"], "Flag di validità diverso da 1")
            continue
        try:
            observed = timestamp(reading["timestamp"])
            if observed > now:
                raise ValueError("Timestamp futuro")
            if type(reading["total"]) is not int or reading["total"] < 0:
                raise ValueError("Contatore non intero o negativo")
        except (ValueError, TypeError) as exc:
            issue("INVALID_READING", reading["id"], str(exc))
            continue
        history[pid].append({"reading_id": reading["id"], "agent_id": reading["agent_id"], "observed_at": observed.isoformat(), "total_counter": reading["total"]})

    result = []
    for pid, candidates in history.items():
        valid = []
        by_time = {}
        for candidate in candidates:
            by_time.setdefault(candidate["observed_at"], []).append(candidate)
        for instant, group in sorted(by_time.items()):
            if len({r["total_counter"] for r in group}) != 1:
                for r in group:
                    issue("CONFLICTING_READING", r["reading_id"], "Contatori discordanti allo stesso istante")
                continue
            # Le letture duplicate coerenti restano nello storico con provenienza.
            if valid and group[0]["total_counter"] < valid[-1]["total_counter"]:
                for r in group:
                    issue("COUNTER_REGRESSION", r["reading_id"], "Contatore inferiore all'ultima lettura accettata")
                continue
            valid.extend(sorted(group, key=lambda r: str(r["reading_id"])))
        if valid:
            result.append({"printer_id": pid, "serial": eligible[pid], "history": valid, "latest": valid[-1]})
    return {"version": "1.0", "printers": sorted(result, key=lambda p: p["serial"]), "anomalies": anomalies, "summary": {"source_printers": len(printers), "included_printers": len(result), "excluded_printers": len(printers) - len(result), "accepted_readings": sum(len(p["history"]) for p in result), "rejected_readings": len(readings) - sum(len(p["history"]) for p in result)}}
