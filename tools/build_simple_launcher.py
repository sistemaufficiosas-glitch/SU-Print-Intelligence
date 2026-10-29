"""Crea un unico avvio Windows contenente il nostro pacchetto Python.

Il file è un batch con un archivio zipapp in coda: Python riconosce lo ZIP
anche con estensione .bat. Nessun download o installazione all'avvio.
"""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


HEADER = (
    '@echo off\r\n'
    'setlocal DisableDelayedExpansion\r\n'
    'py -3.11 -I "%~f0"\r\n'
    'set "SU_CRC_EXIT=%errorlevel%"\r\n'
    'if not "%SU_CRC_EXIT%"=="0" (\r\n'
    '    echo Avvio non completato. Mandami una foto di questa finestra.\r\n'
    '    pause\r\n'
    ')\r\n'
    'exit /b %SU_CRC_EXIT%\r\n'
)
ENTRY = 'from su_print_counters.acceptance import main\nraise SystemExit(main(automatic=True))\n'
SOURCE_ENTRY = 'from su_print_counters.prepare_integration import main\nraise SystemExit(main())\n'


def build(output, source_root=None, *, entry=ENTRY):
    root = Path(source_root) if source_root else Path(__file__).resolve().parents[1]
    package = root / "src" / "su_print_counters"
    files = sorted(package.glob("*.py"))
    if not files or not (package / "simple_selection.py").is_file():
        raise ValueError("Pacchetto Python incompleto")
    output = Path(output)
    output.write_bytes(HEADER.encode("ascii"))
    with ZipFile(output, "a", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        entries = [("__main__.py", entry.encode("utf-8"))]
        entries += [("su_print_counters/" + file.name, file.read_bytes()) for file in files]
        for name, data in entries:
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?")
    parser.add_argument("--sources", action="store_true", help="Prepara l'avvio per i soli sorgenti della Panoramica")
    args = parser.parse_args()
    output = args.output or ("PREPARA_PANORAMICA.bat" if args.sources else "AVVIA_CONTATORI.bat")
    print(build(output, entry=SOURCE_ENTRY if args.sources else ENTRY))
