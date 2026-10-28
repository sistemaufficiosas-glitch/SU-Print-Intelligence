"""Copia SQLite per il collaudo e rapporto della sola struttura, senza record."""

import json
import math
import os
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path


class SnapshotCancelled(Exception):
    """Annullamento richiesto dall'utente prima del completamento."""


def prepare_snapshot(source, output_root, *, cancel=None, on_progress=None, timeout_seconds=600.0):
    """Salva snapshot.sqlite e schema_collaudo.json in una nuova cartella.

    La sorgente è aperta mode=ro e query_only; la backup API include anche WAL
    committati. Solo il nuovo snapshot è scrivibile. Nessun record, SQL o valore
    di default viene esportato nel rapporto della struttura.
    """
    cancel = cancel if cancel is not None else threading.Event()
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Il tempo limite deve essere positivo e finito")
    deadline = time.monotonic() + timeout_seconds

    def check():
        if cancel.is_set():
            raise SnapshotCancelled("Copia annullata")
        if time.monotonic() >= deadline:
            raise TimeoutError("Tempo limite superato durante la copia o la verifica")

    check()
    source = Path(source).resolve(strict=True)
    output_root = Path(output_root).resolve(strict=True)
    if not source.is_file() or not output_root.is_dir():
        raise ValueError("Selezionare un file sorgente e una cartella di destinazione esistenti")
    wal = Path(str(source) + "-wal")
    needed = source.stat().st_size + (wal.stat().st_size if wal.exists() else 0) + 1024 * 1024
    if shutil.disk_usage(output_root).free < needed:
        raise OSError("Spazio libero insufficiente per una copia sicura")
    prefix = "SU_Print_Collaudo_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_"
    folder = Path(tempfile.mkdtemp(prefix=prefix, dir=output_root))
    try:
        def progress(status, remaining, total):
            check()
            if on_progress is not None:
                on_progress(total - remaining, total)

        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as reader:
            reader.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(folder / "snapshot.sqlite")) as target:
                reader.backup(target, pages=256, progress=progress, sleep=0.1)
                check()
                # Rendere autonoma la copia anche quando la sorgente usa WAL.
                target.execute("PRAGMA journal_mode=DELETE")
                target.execute("PRAGMA query_only=ON")
                target.set_progress_handler(
                    lambda: int(cancel.is_set() or time.monotonic() >= deadline), 1000
                )
                try:
                    if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise ValueError("La copia non ha superato la verifica SQLite")
                    objects = []
                    for name, kind in target.execute(
                        "SELECT name, type FROM sqlite_schema "
                        "WHERE type IN ('table', 'view') AND name NOT GLOB 'sqlite_*' ORDER BY name"
                    ).fetchall():
                        check()
                        columns = [
                            {"name": row[0], "type": row[1], "not_null": bool(row[2]),
                             "primary_key_position": row[3], "hidden": row[4]}
                            for row in target.execute(
                                'SELECT name, type, "notnull", pk, hidden FROM pragma_table_xinfo(?)',
                                (name,),
                            )
                        ]
                        keys = [
                            dict(zip(("table", "from", "to", "on_update", "on_delete"), row))
                            for row in target.execute(
                                'SELECT "table", "from", "to", on_update, on_delete FROM pragma_foreign_key_list(?)',
                                (name,),
                            )
                        ]
                        objects.append({"name": name, "kind": kind, "columns": columns, "foreign_keys": keys})
                except sqlite3.OperationalError:
                    check()  # Tradurre l'interruzione SQLite in annullamento/timeout.
                    raise
        check()
        report = {"format_version": 1, "source_file": source.name, "objects": objects}
        with (folder / "schema_collaudo.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        check()
        return folder
    except BaseException as error:
        try:
            shutil.rmtree(folder)
        except OSError as cleanup_error:
            raise RuntimeError(f"Copia incompleta da rimuovere: {folder}. {cleanup_error}") from error
        raise


def desktop_directory():
    """Usare il Desktop effettivo anche se spostato o reindirizzato da Windows."""
    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer) == 0:
            desktop = Path(buffer.value)
            if desktop.is_dir():
                return desktop
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.withdraw()
    initial = Path(r"C:\SU PRINT INTELLIGENCE\PROGRAMMA ATTIVO\SU Print Intelligence ATTIVO 1.2.1 CON DATABASE\data")
    source = filedialog.askopenfilename(
        parent=root, title="Seleziona il database di cui creare una copia di collaudo",
        initialdir=str(initial if initial.is_dir() else Path.home()),
        initialfile="su_print_intelligence.db",
        filetypes=[("Database SQLite", "*.db *.sqlite *.sqlite3"), ("Tutti i file", "*.*")],
    )
    if not source:
        root.destroy()
        return 0
    output = desktop_directory()
    messages = queue.Queue()
    cancel = threading.Event()
    root.title("SU Print — Copia di collaudo")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=20)
    frame.pack()
    ttk.Label(frame, text="Copia per il Centro Raccolta Contatori", font=("Segoe UI", 12)).pack(anchor="w")
    text = tk.StringVar(value="Preparazione della copia. Il database sorgente viene letto in sola lettura.")
    ttk.Label(frame, textvariable=text, wraplength=500).pack(anchor="w", pady=12)
    bar = ttk.Progressbar(frame, maximum=100, length=500)
    bar.pack()
    ttk.Label(frame, text=f"Destinazione: nuova cartella in {output}", wraplength=500).pack(anchor="w", pady=12)

    def request_cancel():
        cancel.set()
        cancel_button.configure(state="disabled")
        text.set("Annullamento in corso. Attendere la chiusura dei file.")

    cancel_button = ttk.Button(frame, text="Annulla", command=request_cancel)
    cancel_button.pack(anchor="e")
    root.protocol("WM_DELETE_WINDOW", request_cancel)

    def worker():
        try:
            folder = prepare_snapshot(
                source, output, cancel=cancel,
                on_progress=lambda copied, total: messages.put(("progress", (copied, total))),
            )
        except Exception as error:
            messages.put(("error", error))
        else:
            messages.put(("done", folder))

    def poll():
        try:
            while True:
                kind, value = messages.get_nowait()
                if kind == "progress":
                    copied, total = value
                    if total and not cancel.is_set():
                        bar["value"] = copied * 100 / total
                        text.set("Verifica della copia in corso…" if copied == total else "Copia del database in corso…")
                elif kind == "error":
                    if isinstance(value, SnapshotCancelled):
                        messagebox.showinfo("Copia annullata", "Operazione annullata.", parent=root)
                    else:
                        messagebox.showerror("Copia non completata", str(value), parent=root)
                    root.destroy()
                    return
                elif kind == "done":
                    root.withdraw()
                    messagebox.showinfo(
                        "Copia pronta",
                        f"Copia e rapporto salvati in:\n{value}\n\n"
                        "Per continuare in chat allega SOLO schema_collaudo.json.\n"
                        "snapshot.sqlite contiene i dati: conservalo sul PC.\n\n"
                        "Premi OK per aprire la cartella.", parent=root,
                    )
                    if os.name == "nt":
                        try:
                            os.startfile(str(value))
                        except OSError:
                            messagebox.showinfo("Cartella di collaudo", str(value), parent=root)
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(100, poll)

    root.deiconify()
    threading.Thread(target=worker, daemon=False).start()
    root.after(100, poll)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
