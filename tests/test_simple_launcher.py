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
from su_print_counters import acceptance, real_adapter, simple_selection, snapshot
modules = [acceptance, real_adapter, simple_selection, snapshot]
def fake_main(*, automatic=False):
    print(json.dumps({
        'automatic': automatic,
        'isolated': sys.flags.isolated,
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
        self.assertEqual(len(output["origins"]), 4)
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

    def test_automatic_mode_runs_selected_copy_without_file_dialog(self):
        selected = self.folder / "SU_Print_Collaudo_20261029_192453_test1234" / "snapshot.sqlite"
        result_folder = selected.parent / "Risultati_Contatori_V1_test"
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
            patch("tkinter.filedialog.askopenfilename") as picker,
            patch.object(acceptance, "desktop_directory", return_value=self.folder),
            patch("su_print_counters.simple_selection.find_latest_snapshot", return_value=selected) as choose,
            patch.object(acceptance, "write_acceptance_report", return_value=result_folder) as collect,
            patch.object(acceptance.threading, "Thread", side_effect=inline_thread),
            patch.object(acceptance, "show_completed_report") as completed,
        ):
            self.assertEqual(acceptance.main(automatic=True), 0)
        picker.assert_not_called()
        choose.assert_called_once_with(self.folder)
        self.assertEqual(collect.call_args.args, (selected,))
        completed.assert_called_once_with(root, result_folder, simple=True)

    def test_automatic_selection_error_does_not_fall_back_to_file_dialog(self):
        root = Mock()
        with (
            patch("tkinter.Tk", return_value=root),
            patch("tkinter.filedialog.askopenfilename") as picker,
            patch("tkinter.messagebox.showerror") as error,
            patch.object(acceptance, "desktop_directory", return_value=self.folder),
            patch("su_print_counters.simple_selection.find_latest_snapshot", side_effect=ValueError("Copia incompleta")),
        ):
            self.assertEqual(acceptance.main(automatic=True), 1)
        picker.assert_not_called()
        root.destroy.assert_called_once()
        error.assert_called_once()

    def test_automatic_selection_to_real_report_preserves_synthetic_snapshot(self):
        import test_real_adapter as fixtures
        from su_print_counters.simple_selection import find_latest_snapshot

        fixture = fixtures.RealAdapterTests(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.reading(1)
        desktop = self.folder / "Desktop"
        copy_folder = desktop / "SU_Print_Collaudo_20261029_192453_test1234"
        copy_folder.mkdir(parents=True)
        snapshot = copy_folder / "snapshot.sqlite"
        shutil.copyfile(fixture.database, snapshot)
        (copy_folder / "schema_collaudo.json").write_text(json.dumps({
            "format_version": 1, "source_file": "synthetic.db", "objects": [],
        }), encoding="utf-8")
        digest = hashlib.sha256(snapshot.read_bytes()).digest()
        selected = find_latest_snapshot(desktop)
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
