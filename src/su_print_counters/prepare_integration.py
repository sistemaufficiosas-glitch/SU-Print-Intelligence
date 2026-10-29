"""Raccolta locale del codice necessario per preparare la Panoramica."""

import queue
import tempfile
import threading
from pathlib import Path


DEFAULT_APPLICATION = Path(r"C:\SU PRINT INTELLIGENCE\PROGRAMMA ATTIVO\SU Print Intelligence ATTIVO 1.2.1 CON DATABASE")


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from .acceptance import show_file_in_explorer
    from .snapshot import SnapshotCancelled
    from .source_bundle import bundle_sources

    root = tk.Tk()
    root.withdraw()
    application = DEFAULT_APPLICATION
    if not (application / "app").is_dir():
        selected = filedialog.askdirectory(
            parent=root, title="Seleziona la cartella del programma SU Print Intelligence",
            initialdir=str(DEFAULT_APPLICATION.parent if DEFAULT_APPLICATION.parent.is_dir() else Path.home()),
        )
        if not selected:
            root.destroy()
            return 0
        application = Path(selected)
    root.title("SU Print — Preparazione Panoramica")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    label = ttk.Label(frame, text="Preparo il codice della Panoramica. Attendere…", wraplength=520)
    label.pack(anchor="w", pady=12)
    ttk.Label(frame, text="Raccolgo i sorgenti del programma in un archivio locale.\nIl database e i documenti restano sul PC.", wraplength=520).pack(anchor="w", pady=8)
    bar = ttk.Progressbar(frame, mode="indeterminate", length=480)
    bar.pack(pady=12)
    bar.start()
    cancel = threading.Event()
    messages = queue.Queue()
    outcome = [1]

    def request_cancel():
        cancel.set()
        button.configure(state="disabled")
        label.configure(text="Annullamento in corso…")

    button = ttk.Button(frame, text="Annulla", command=request_cancel)
    button.pack(anchor="e", pady=8)
    root.protocol("WM_DELETE_WINDOW", request_cancel)

    def worker():
        try:
            folder = bundle_sources(application, Path(tempfile.gettempdir()), cancel=cancel)
            messages.put(("done", folder))
        except Exception as error:
            messages.put(("error", error))

    def poll():
        try:
            kind, value = messages.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        bar.stop()
        if kind == "done":
            outcome[0] = 0
            frame.destroy()
            root.title("SU Print — Sorgenti pronti")
            root.protocol("WM_DELETE_WINDOW", root.destroy)
            completed = ttk.Frame(root, padding=24)
            completed.pack(fill="both", expand=True)
            ttk.Label(completed, text="IL FILE È PRONTO", font=("Segoe UI", 15)).pack(anchor="w")
            ttk.Label(completed, text="Trascina SORGENTI_PANORAMICA.zip dalla cartella aperta a questa chat.", wraplength=520).pack(anchor="w", pady=16)
            ttk.Label(completed, text="Contiene il codice del programma per preparare l'integrazione.\nIl programma attivo resta invariato; nessun file viene inviato automaticamente.", wraplength=520).pack(anchor="w", pady=8)
            target = Path(value) / "SORGENTI_PANORAMICA.zip"
            ttk.Button(completed, text="Mostra ZIP da inviare", command=lambda: show_file_in_explorer(root, target)).pack(fill="x", pady=12)
            ttk.Button(completed, text="Chiudi", command=root.destroy).pack(anchor="e")
            root.after(200, lambda: show_file_in_explorer(root, target))
            return
        if isinstance(value, SnapshotCancelled):
            outcome[0] = 0
            messagebox.showinfo("Annullato", "Preparazione annullata.", parent=root)
        else:
            messagebox.showerror("Preparazione non completata", f"{value}\n\nMandami una foto di questa finestra per continuare.", parent=root)
        root.destroy()

    root.deiconify()
    threading.Thread(target=worker, daemon=False).start()
    root.after(100, poll)
    root.mainloop()
    return outcome[0]


if __name__ == "__main__":
    raise SystemExit(main())
