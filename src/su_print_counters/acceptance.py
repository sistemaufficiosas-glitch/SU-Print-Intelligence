"""Collaudo locale dello snapshot: risultati privati e riepilogo condivisibile."""

import html
import json
import os
import queue
import re
import shutil
import tempfile
import threading
from collections import Counter
from pathlib import Path

from .snapshot import SnapshotCancelled, desktop_directory


COUNT_KEYS = (
    "source_printers", "source_agents", "source_telemetry", "included_printers",
    "excluded_printers", "archived_printers", "validated_readings", "rejected_readings",
    "current_readings", "historical_readings", "collected_without_timezone",
)
ANOMALY_CODES = frozenset("""
AMBIGUOUS_SERIAL AMBIGUOUS_PRINTER_SERIAL MISSING_SERIAL UNKNOWN_PRINTER MISSING_AGENT
REJECTED_TELEMETRY INVALID_JSON PRINTER_ID_MISMATCH SERIAL_MISMATCH SNMP_FAILED
INVALID_COUNTER TOTAL_BELOW_COMPONENT NO_COUNTERS INVALID_TIMESTAMP
UNCERTAIN_RECEIPT_TIME UNKNOWN_RECEIPT_TIME INVALID_RECEIPT_TIME FUTURE_TIMESTAMP
CLOCK_FUTURE COUNTER_REGRESSION CONFLICTING_READING NO_CURRENT_AGENT
PRINTER_EXCLUDED NO_VALID_READING INVALID_AGENT BINDING_EXCLUDED
EXCLUSION_RULE_UNCERTAIN TOTAL_CANDIDATE_PROVENANCE UNTRUSTED_TELEMETRY
INACTIVE_AGENT AGENT_REVOKED NO_AUTHORIZED_READING
UNIDENTIFIABLE_PRINTER INCONSISTENT_COUNTERS MISSING_OR_AMBIGUOUS_SERIAL
NO_VALID_CURRENT_READING
""".split())


def collect_real(*args, **kwargs):
    # Importare soltanto il nostro adattatore indipendente, mai servizi del gestionale.
    from .real_adapter import collect_real as collect

    return collect(*args, **kwargs)


def shareable_summary(report):
    """Copiare solo contatori e codici noti; nessun valore libero dal report."""
    source = report.get("summary", {})
    counts = {
        name: source[name] for name in COUNT_KEYS
        if type(source.get(name)) is int and source[name] >= 0
    }
    raw_codes = report.get("anomalies_by_code", source.get("anomalies_by_code"))
    if not isinstance(raw_codes, dict):
        raw_codes = Counter(
            item.get("code") for item in report.get("anomalies", [])
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        )
    codes = Counter()
    for code, count in raw_codes.items():
        if type(count) is int and count >= 0:
            codes[code if code in ANOMALY_CODES else "OTHER_ANOMALY"] += count
    shared = {
        "format_version": 1, "status": "REVIEW_REQUIRED",
        "summary": counts, "anomalies_by_code": dict(sorted(codes.items())),
    }
    checks = {}
    for name, section, field in (
        ("naive_receipt_utc_default_confirmed", "time_policy", "naive_receipt_utc_default_confirmed"),
        ("total_origin_verified", "total_policy", "origin_verified"),
        ("offline_hmac_rechecked", "provenance", "offline_hmac_rechecked"),
    ):
        source = report.get(section)
        if isinstance(source, dict) and type(source.get(field)) is bool:
            checks[name] = source[field]
    shared["checks"] = checks
    fingerprint = report.get("snapshot_sha256")
    if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint):
        shared["snapshot_sha256"] = fingerprint.lower()
    return shared


def _escape(value):
    return html.escape(str(value), quote=True)


def _counter(value):
    return f"{value:,}".replace(",", ".") if type(value) is int and value >= 0 else "—"


def render_html(report):
    """Pagina statica autonoma: nessun codice o risorsa esterna dal database."""
    rows = []
    for printer in report.get("printers", []):
        latest = printer.get("latest") or {}
        collected = str(latest.get("collected_at") or "—")
        if collected != "—" and not latest.get("collected_at_has_timezone", False):
            collected += " (fuso non indicato)"
        cells = [
            printer.get("serial", ""), latest.get("received_at", "—"), collected,
            _counter(latest.get("black_counter")), _counter(latest.get("color_counter")),
            _counter(latest.get("total_counter")),
        ]
        rows.append("<tr>" + "".join("<td>" + _escape(cell) + "</td>" for cell in cells) + "</tr>")
    if not rows:
        rows.append('<tr><td colspan="6">Nessuna macchina inclusa. Consultare le anomalie nel rapporto JSON.</td></tr>')
    included = shareable_summary(report)["summary"].get("included_printers", len(report.get("printers", [])))
    return """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>SU Print — Collaudo contatori</title>
<style>body{font:16px system-ui,sans-serif;margin:2rem;color:#142030;background:#f5f7fa}
h1{margin-bottom:.3rem}.status{padding:1rem;background:#fff0ca;border:1px solid #a76a00}
table{border-collapse:collapse;width:100%;background:white}th,td{padding:.65rem;border:1px solid #ccd3df;text-align:left}
th{background:#e7edf5}p,li{line-height:1.5}.note{font-size:.95rem}td{overflow-wrap:anywhere}</style>
</head><body><h1>Centro Raccolta Contatori V1</h1>
<p class="status"><strong>COLLAUDO DA VERIFICARE</strong> — risultati locali delle regole V1;
la correttezza delle letture reali richiede il confronto con casi noti.</p>
<p>Macchine incluse: <strong>""" + _escape(included) + """</strong>.</p>
<p>Ultima telemetria valida ricevuta e utilizzabile secondo le regole V1,
ordinata per ricezione sul server (UTC). I dati provengono dal log Remote Agent nella copia SQLite.</p>
<table><thead><tr><th>Matricola</th><th>Ricevuto sul server (UTC)</th>
<th>Raccolto, come inviato dall'Agent</th><th>Nero</th><th>Colore</th><th>Totale dichiarato*</th>
</tr></thead><tbody>""" + "".join(rows) + """</tbody></table>
<ul class="note"><li>— indica un contatore assente nell'ultima telemetria: non equivale a zero.
La tabella non riporta avanti valori di letture precedenti.</li>
<li>* Il totale è il candidato dichiarato nel payload; la sua origine non è semanticamente certificata.
Non viene ricostruito sommando nero e colore o scegliendo il massimo di altri contatori.</li>
<li>Ricevuto (UTC) e raccolto sono due date diverse. Un timestamp di raccolta senza fuso resta
senza fuso; non viene reinterpretato automaticamente come ora locale o UTC.</li>
<li>Storico, ultime letture per singolo contatore, macchine escluse e anomalie sono nel file
<strong>risultati_contatori.json</strong>, da conservare sul PC.</li>
<li>Il file <strong>esito_collaudo.json</strong> contiene solo conteggi e codici e può essere usato
per proseguire il collaudo. Nessuna integrazione con il gestionale è stata eseguita.</li></ul>
</body></html>
"""


def write_acceptance_report(snapshot, *, cancel=None):
    """Crea quattro file in una cartella propria, senza modificare lo snapshot."""
    cancel = cancel if cancel is not None else threading.Event()

    def check():
        if cancel.is_set():
            raise SnapshotCancelled("Collaudo annullato")

    check()
    snapshot = Path(snapshot).resolve(strict=True)
    if not snapshot.is_file() or snapshot.name.casefold() != "snapshot.sqlite":
        raise ValueError("Selezionare snapshot.sqlite nella cartella di collaudo")
    report = collect_real(snapshot, cancel=cancel)
    check()
    folder = Path(tempfile.mkdtemp(prefix="Risultati_Contatori_V1_", dir=snapshot.parent))
    try:
        outputs = {
            "risultati_contatori.json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            "esito_collaudo.json": json.dumps(shareable_summary(report), ensure_ascii=False, indent=2) + "\n",
            "risultati_contatori.html": render_html(report),
            "LEGGIMI.txt": (
                "SU PRINT - COLLAUDO DA VERIFICARE\n\n"
                "Apri risultati_contatori.html con doppio clic per vedere la tabella.\n"
                "Confronta le letture con casi noti prima di autorizzare integrazioni.\n"
                "Il totale e' il candidato dichiarato, con origine non certificata; non viene ricostruito.\n"
                "Le date di raccolta senza fuso non vengono reinterpretate automaticamente.\n\n"
                "Allega in chat SOLO esito_collaudo.json (conteggi e codici).\n"
                "Conserva risultati_contatori.json sul PC: contiene matricole, contatori e storico.\n"
                "Lo snapshot e il programma attivo non sono stati modificati dal collaudo.\n"
            ),
        }
        for name, content in outputs.items():
            check()
            with (folder / name).open("x", encoding="utf-8") as stream:
                stream.write(content)
        check()
        return folder
    except BaseException as error:
        try:
            shutil.rmtree(folder)
        except OSError as cleanup_error:
            raise RuntimeError(f"Risultati incompleti da rimuovere: {folder}. {cleanup_error}") from error
        raise


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.withdraw()
    desktop = desktop_directory()
    copies = sorted((p for p in desktop.glob("SU_Print_Collaudo_*") if p.is_dir()), key=lambda p: p.name)
    selected = filedialog.askopenfilename(
        parent=root, title="Seleziona snapshot.sqlite per il collaudo delle letture",
        initialdir=str(copies[-1] if copies else desktop), initialfile="snapshot.sqlite",
        filetypes=[("Copia di collaudo", "snapshot.sqlite")],
    )
    if not selected:
        root.destroy()
        return 0
    root.title("SU Print — Collaudo delle letture")
    ttk.Label(root, text="Controllo delle telemetrie sulla copia locale.\nAttendere…", padding=20).pack()
    bar = ttk.Progressbar(root, mode="indeterminate", length=420)
    bar.pack(padx=20, pady=10)
    bar.start()
    messages = queue.Queue()
    cancel = threading.Event()
    outcome = [1]

    def request_cancel():
        cancel.set()
        button.configure(state="disabled")

    button = ttk.Button(root, text="Annulla", command=request_cancel)
    button.pack(pady=15)
    root.protocol("WM_DELETE_WINDOW", request_cancel)

    def worker():
        try:
            messages.put(("done", write_acceptance_report(selected, cancel=cancel)))
        except Exception as error:
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
                "Rapporti pronti — collaudo da verificare",
                "I rapporti sono stati creati.\n\n"
                "Apri risultati_contatori.html per vedere le letture.\n"
                "Allega in chat SOLO esito_collaudo.json.\n"
                "Conserva il JSON completo sul PC.\n\nPremi OK per aprire la cartella.", parent=root,
            )
            if os.name == "nt":
                try:
                    os.startfile(str(value))
                except OSError:
                    messagebox.showinfo("Cartella dei risultati", str(value), parent=root)
        elif isinstance(value, SnapshotCancelled):
            outcome[0] = 0
            messagebox.showinfo("Annullato", str(value), parent=root)
        else:
            messagebox.showerror("Collaudo non completato", str(value), parent=root)
        root.destroy()

    root.deiconify()
    threading.Thread(target=worker, daemon=False).start()
    root.after(100, poll)
    root.mainloop()
    return outcome[0]


if __name__ == "__main__":
    raise SystemExit(main())
