"""Avvio singolo: contenuto riproducibile, isolamento e risultato da allegare.

Il processo isolato prova il caricamento zipapp di CPython. Non sostituisce
un'esecuzione del batch tramite cmd.exe su Windows.
"""

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from su_print_counters import acceptance


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "_su_print_simple_builder", ROOT / "tools" / "build_simple_launcher.py"
)
_BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BUILDER)


class SimpleLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su launcher test ")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_archive_is_reproducible_and_contains_exact_owned_python_sources(self):
        first = _BUILDER.build(self.folder / "first.bat", ROOT)
        second = _BUILDER.build(self.folder / "second.bat", ROOT)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        sources = {
            "su_print_counters/" + path.name: path.read_bytes()
            for path in (ROOT / "src" / "su_print_counters").glob("*.py")
        }
        with ZipFile(first) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), set(sources) | {"__main__.py"})
            self.assertEqual(len(archive.namelist()), len(sources) + 1)
            for name, expected in sources.items():
                self.assertEqual(archive.read(name), expected, name)

    def test_batch_stops_before_binary_payload_and_requests_isolated_python(self):
        launcher = _BUILDER.build(self.folder / "start.bat", ROOT)
        data = launcher.read_bytes()
        with ZipFile(launcher) as archive:
            first_offset = min(info.header_offset for info in archive.infolist())
        header = data[:first_offset]
        self.assertEqual(data[first_offset:first_offset + 4], b"PK\x03\x04")
        self.assertEqual(header.splitlines()[-1], b"exit /b %SU_CRC_EXIT%")
        self.assertIn(b'py -3.11 -I "%~f0"\r\n', header)
        self.assertIn(b"setlocal DisableDelayedExpansion\r\n", header)
        self.assertEqual(header.replace(b"\r\n", b"").find(b"\n"), -1)

    def test_isolated_process_loads_embedded_package_and_entry_requests_automatic_mode(self):
        launcher = _BUILDER.build(self.folder / "Collaudo con spazi & accenti à.bat", ROOT)
        # Un file omonimo nella cartella corrente non deve entrare negli import.
        (self.folder / "su_print_counters.py").write_text(
            "raise RuntimeError('modulo della cartella corrente importato')\n", encoding="utf-8"
        )
        bootstrap = """
import json, runpy, sys
archive = sys.argv[1]
sys.path.insert(0, archive)
from su_print_counters import acceptance, real_adapter, simple_selection, snapshot, file_discovery
modules = [acceptance, real_adapter, simple_selection, snapshot, file_discovery]
def fake_main(*, automatic=False):
    print(json.dumps({
        'automatic': automatic,
        'isolated': sys.flags.isolated,
        'entry_file': sys.argv[0],
        'origins': [module.__file__ for module in modules],
    }))
    return 0
acceptance.main = fake_main
runpy.run_path(archive, run_name='__main__')
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", bootstrap, str(launcher)],
            cwd=self.folder, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["automatic"])
        self.assertEqual(output["isolated"], 1)
        self.assertEqual(output["entry_file"], str(launcher))
        self.assertEqual(len(output["origins"]), 5)
        for origin in output["origins"]:
            self.assertTrue(origin.startswith(str(launcher)), origin)
            self.assertNotIn(str(ROOT / "src"), origin)

    def test_show_archive_passes_selected_zip_as_argument_without_shell(self):
        folder = self.folder / "Risultati & accenti à"
        root = Mock()
        with (
            patch.object(acceptance, "os", SimpleNamespace(name="nt")),
            patch.object(acceptance.subprocess, "Popen") as launch,
            patch("tkinter.messagebox.showerror") as error,
        ):
            acceptance.show_support_archive(root, folder)
        launch.assert_called_once_with([
            "explorer.exe", "/select,", str(folder / "supporto_collaudo.zip")
        ])
        self.assertFalse(launch.call_args.kwargs.get("shell", False))
        error.assert_not_called()

    def test_show_archive_reports_explorer_failure(self):
        root = Mock()
        with (
            patch.object(acceptance, "os", SimpleNamespace(name="nt")),
            patch.object(acceptance.subprocess, "Popen", side_effect=OSError("Explorer unavailable")),
            patch("tkinter.messagebox.showerror") as error,
        ):
            acceptance.show_support_archive(root, self.folder)
        error.assert_called_once_with("File non aperto", "Explorer unavailable", parent=root)

    def _run_automatic_flow(self, *, selected=None, discovery_error=None, picked=""):
        from su_print_counters.simple_selection import validate_snapshot_file

        selected = selected or self.folder / "Copia rinominata" / "snapshot.sqlite"
        result_folder = Path(selected).parent / "Risultati_Contatori_V1_test"
        root = Mock()
        pending_callbacks = []
        root.after.side_effect = lambda delay, callback: pending_callbacks.append(callback)

        def mainloop():
            for _ in range(10):
                if not pending_callbacks:
                    return
                pending_callbacks.pop(0)()
            self.fail("Il completamento non termina il ciclo di attesa")

        def inline_thread(*, target, daemon):
            worker = Mock()
            worker.start.side_effect = target
            return worker

        root.mainloop.side_effect = mainloop
        with (
            patch("tkinter.Tk", return_value=root),
            patch("tkinter.ttk.Frame"), patch("tkinter.ttk.Label"),
            patch("tkinter.ttk.Button"), patch("tkinter.ttk.Progressbar"),
            patch("tkinter.filedialog.askopenfilename", return_value=picked) as picker,
            patch("tkinter.messagebox.showerror") as error,
            patch.object(acceptance, "desktop_directory", return_value=self.folder),
            patch("su_print_counters.file_discovery.discover_snapshot",
                  return_value=selected, side_effect=discovery_error) as choose,
            patch("su_print_counters.simple_selection.validate_snapshot_file", wraps=validate_snapshot_file) as validate,
            patch.object(acceptance, "write_acceptance_report", return_value=result_folder) as collect,
            patch.object(acceptance.threading, "Thread", side_effect=inline_thread),
            patch.object(acceptance, "show_completed_report") as completed,
        ):
            result = acceptance.main(automatic=True)
        return result, SimpleNamespace(root=root, picker=picker, choose=choose, validate=validate,
                                       collect=collect, completed=completed, error=error,
                                       result_folder=result_folder)

    def test_automatic_mode_runs_renamed_copy_without_file_dialog_or_timestamp_parse(self):
        selected = self.folder / "Copia rinominata" / "snapshot.sqlite"
        result, ui = self._run_automatic_flow(selected=selected)
        self.assertEqual(result, 0)
        ui.picker.assert_not_called()
        ui.validate.assert_not_called()
        ui.choose.assert_called_once_with((
            self.folder, Path(sys.argv[0]).resolve().parent, Path.home() / "Desktop",
        ))
        self.assertEqual(ui.collect.call_args.args, (selected,))
        ui.completed.assert_called_once_with(ui.root, ui.result_folder, simple=True)
        ui.error.assert_not_called()

    def test_automatic_selection_error_does_not_fall_back_to_file_dialog(self):
        for error in (ValueError("Copia incompleta"), OSError("Scansione incompleta")):
            with self.subTest(error=error):
                result, ui = self._run_automatic_flow(discovery_error=error)
                self.assertEqual(result, 1)
                ui.picker.assert_not_called()
                ui.validate.assert_not_called()
                ui.collect.assert_not_called()
                ui.root.destroy.assert_called_once()
                ui.error.assert_called_once()

    def test_missing_copy_opens_picker_and_validates_selected_renamed_folder(self):
        from su_print_counters.file_discovery import SnapshotNotFound

        folder = self.folder / "Scelta manuale senza data"
        folder.mkdir()
        selected = folder / "snapshot.sqlite"
        selected.write_bytes(b"SQLite format 3\x00" + b"synthetic fixture")
        (folder / "schema_collaudo.json").write_text(json.dumps({
            "format_version": 1, "source_file": "synthetic.db", "objects": [],
        }), encoding="utf-8")
        result, ui = self._run_automatic_flow(
            selected=selected, discovery_error=SnapshotNotFound("Nessuna copia"), picked=str(selected),
        )
        self.assertEqual(result, 0)
        ui.picker.assert_called_once_with(
            parent=ui.root, title="Apri la copia snapshot.sqlite", initialdir=str(self.folder),
            initialfile="snapshot.sqlite", filetypes=[("Copia di collaudo", "snapshot.sqlite")],
        )
        ui.validate.assert_called_once_with(str(selected))
        self.assertEqual(ui.collect.call_args.args, (selected,))
        ui.completed.assert_called_once_with(ui.root, ui.result_folder, simple=True)
        ui.error.assert_not_called()

    def test_missing_copy_picker_cancel_does_not_start_collection(self):
        from su_print_counters.file_discovery import SnapshotNotFound

        result, ui = self._run_automatic_flow(discovery_error=SnapshotNotFound("Nessuna copia"))
        self.assertEqual(result, 0)
        ui.picker.assert_called_once()
        ui.validate.assert_not_called()
        ui.collect.assert_not_called()
        ui.completed.assert_not_called()
        ui.root.destroy.assert_called_once()
        ui.error.assert_not_called()

    def test_manual_picker_cannot_send_invalid_or_active_database_to_collector(self):
        from su_print_counters.file_discovery import SnapshotNotFound

        for name in ("su_print_intelligence.db", "snapshot.sqlite"):
            with self.subTest(name=name):
                selected = self.folder / name
                selected.write_bytes(b"invalid database fixture")
                result, ui = self._run_automatic_flow(
                    discovery_error=SnapshotNotFound("Nessuna copia"), picked=str(selected),
                )
                self.assertEqual(result, 1)
                ui.validate.assert_called_once_with(str(selected))
                ui.collect.assert_not_called()
                ui.completed.assert_not_called()
                ui.error.assert_called_once()

    def test_automatic_selection_to_real_report_preserves_synthetic_snapshot(self):
        import test_real_adapter as fixtures
        from su_print_counters.file_discovery import discover_snapshot

        fixture = fixtures.RealAdapterTests(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.reading(1)
        desktop = self.folder / "Desktop"
        copy_folder = desktop / "Copia pronta"
        copy_folder.mkdir(parents=True)
        snapshot = copy_folder / "snapshot.sqlite"
        shutil.copyfile(fixture.database, snapshot)
        (copy_folder / "schema_collaudo.json").write_text(json.dumps({
            "format_version": 1, "source_file": "synthetic.db", "objects": [],
        }), encoding="utf-8")
        digest = hashlib.sha256(snapshot.read_bytes()).digest()
        selected = discover_snapshot((desktop,))
        self.assertEqual(selected, snapshot)
        result_folder = acceptance.write_acceptance_report(selected)
        with ZipFile(result_folder / "supporto_collaudo.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {
                "risultati_contatori.json", "esito_collaudo.json", "LEGGIMI.txt",
            })
            report = json.loads(archive.read("risultati_contatori.json"))
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(len(report["printers"]), 1)
        self.assertEqual(report["printers"][0]["serial"], "SN-001")
        latest = report["printers"][0]["latest"]
        self.assertEqual((latest["black_counter"], latest["color_counter"], latest["total_counter"]),
                         (60, 40, 100))
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).digest(), digest)
        self.assertEqual(hashlib.sha256(fixture.database.read_bytes()).digest(), digest)
        self.assertFalse(any(Path(str(snapshot) + suffix).exists()
                             for suffix in ("-wal", "-shm", "-journal")))


if __name__ == "__main__":
    unittest.main()
