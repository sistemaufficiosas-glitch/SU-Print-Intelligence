import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .engine import collect


def main():
    parser = argparse.ArgumentParser(description="Centro Raccolta Contatori V1 — solo snapshot SQLite")
    parser.add_argument("database", type=Path, help="Copia SQLite consistente, chiusa e separata dal database attivo")
    parser.add_argument("--schema", choices=("reference", "su-print"), default="reference", help="Contratto di riferimento o registro reale SU Print")
    parser.add_argument("--mapping", type=Path, help="Mappatura JSON di tabelle e colonne")
    parser.add_argument("--output", type=Path, help="Nuovo file JSON; non sovrascrive file esistenti")
    args = parser.parse_args()
    try:
        if args.schema == "su-print":
            if args.mapping:
                raise ValueError("Il profilo su-print non usa --mapping")
            from .real_adapter import collect_real

            report = collect_real(args.database)
        else:
            report = collect(args.database, args.mapping)
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            # Creazione esclusiva: protegge anche database, hard link e file utente.
            with args.output.open("x", encoding="utf-8") as out:
                out.write(payload)
        else:
            sys.stdout.write(payload)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
