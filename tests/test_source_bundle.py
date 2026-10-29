"""Raccolta statica dei sorgenti; nessun dato operativo viene letto."""

import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from su_print_counters import source_bundle
from su_print_counters.snapshot import SnapshotCancelled


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su source bundle test ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app_root = self.root / "programma"
        self.app = self.app_root / "app"
        self.app.mkdir(parents=True)
        self.output = self.root / "risultati"
        self.output.mkdir()
        self.marker = self.output / "documento utente.txt"
        self.marker.write_bytes(b"da conservare")
        self.write("app/main.py", b"raise RuntimeError('questo sorgente non deve essere eseguito')\n")

    def write(self, relative, data):
        path = self.app_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def bundle(self, **kwargs):
        return source_bundle.bundle_sources(self.app_root, self.output, **kwargs)

    def test_only_source_allowlist_is_copied_and_inputs_are_unchanged(self):
        wanted = {
            "app/main.py", "app/ui/panoramica.ui", "app/ui/theme.qss", "app/config.py",
            "app/services/repository_OLD.py", "main.py", "requirements.txt", "pyproject.toml",
        }
        for name in wanted - {"app/main.py"}:
            self.write(name, ("sorgente: " + name).encode())
        unwanted = (
            "app/data/cliente.db", "app/logs/access.log", "app/backup_v1/originale.py",
            "app/.venv/lib/modulo.py", "app/cache/copied.py", "app/documents/contratto.pdf",
            "app/secrets/key.py", "app/services/credentials_service.py", "app/.env",
            "app/clienti.json", "app/clienti.csv", "app/ricevute.xml", "app/privatekey.pem",
            "app/icon.png", "data/su_print_intelligence.db", "logs/extra.py", "other/unrelated.py",
        )
        for name in unwanted:
            self.write(name, b"DATI PRIVATI DA NON COPIARE")
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.app_root.rglob("*") if p.is_file()}
        folder = self.bundle()
        with ZipFile(folder / source_bundle.ARCHIVE_NAME) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"manifest_sorgenti.json"} | {"sorgenti/" + name for name in wanted})
            manifest = json.loads(archive.read("manifest_sorgenti.json"))
            for item in manifest["files"]:
                original = (self.app_root / item["path"]).read_bytes()
                self.assertEqual(archive.read("sorgenti/" + item["path"]), original)
                self.assertEqual(item["sha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(item["size_bytes"], len(original))
            all_content = b"".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(b"DATI PRIVATI DA NON COPIARE", all_content)
            self.assertNotIn(str(self.app_root).encode(), all_content)
            self.assertIn("app/services/credentials_service.py", {i["path"] for i in manifest["excluded"]})
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in self.app_root.rglob("*") if p.is_file()})
        self.assertEqual(self.marker.read_bytes(), b"da conservare")

    def test_database_and_documents_are_never_opened(self):
        database = self.write("app/store.db", b"non aprire")
        document = self.write("app/manuale.pdf", b"non aprire")
        real_open = os.open

        def opening(path, *args, **kwargs):
            self.assertNotIn(Path(path), {database, document})
            return real_open(path, *args, **kwargs)

        with patch.object(source_bundle.os, "open", side_effect=opening):
            self.bundle()

    def test_private_keys_directory_is_excluded_but_regular_config_is_included(self):
        secret = self.write("app/private_keys/config.py", b"PRIVATE_KEY_DA_NON_LEGGERE")
        config = self.write("app/config.py", b"GUI_THEME = 'dark'\n")
        real_open = os.open

        def opening(path, *args, **kwargs):
            self.assertNotEqual(Path(path), secret)
            return real_open(path, *args, **kwargs)

        with patch.object(source_bundle.os, "open", side_effect=opening):
            folder = self.bundle()
        with ZipFile(folder / source_bundle.ARCHIVE_NAME) as archive:
            self.assertEqual(archive.read("sorgenti/app/config.py"), config.read_bytes())
            self.assertNotIn("sorgenti/app/private_keys/config.py", archive.namelist())
            manifest = json.loads(archive.read("manifest_sorgenti.json"))
            self.assertIn("app/private_keys", {i["path"] for i in manifest["excluded"]})

    def test_links_are_excluded_without_reading_their_targets(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.py").write_bytes(b"SECRETO FUORI DAL PROGRAMMA")
        try:
            (self.app / "linked").symlink_to(outside, target_is_directory=True)
            (self.app / "linked.py").symlink_to(outside / "private.py")
        except OSError:
            self.skipTest("Symbolic links unavailable")
        folder = self.bundle()
        with ZipFile(folder / source_bundle.ARCHIVE_NAME) as archive:
            self.assertNotIn("sorgenti/app/linked.py", archive.namelist())
            self.assertFalse(any(name.startswith("sorgenti/app/linked/") for name in archive.namelist()))
            manifest = json.loads(archive.read("manifest_sorgenti.json"))
            self.assertIn("app/linked.py", {i["path"] for i in manifest["excluded"]})

    def test_output_inside_program_is_rejected_without_changes(self):
        before = set(self.app_root.rglob("*"))
        with self.assertRaisesRegex(ValueError, "fuori"):
            source_bundle.bundle_sources(self.app_root, self.app)
        self.assertEqual(set(self.app_root.rglob("*")), before)

    def test_cancelled_before_start_does_not_create_outputs(self):
        cancel = threading.Event()
        cancel.set()
        before = set(self.output.iterdir())
        with self.assertRaises(SnapshotCancelled):
            self.bundle(cancel=cancel)
        self.assertEqual(set(self.output.iterdir()), before)

    def test_cancel_during_copy_removes_only_own_folder(self):
        cancel = threading.Event()
        before = set(self.output.iterdir())
        real_open = ZipFile.open

        def opening(archive, name, mode="r", *args, **kwargs):
            result = real_open(archive, name, mode, *args, **kwargs)
            if mode == "w":
                cancel.set()
            return result

        with patch.object(ZipFile, "open", opening):
            with self.assertRaises(SnapshotCancelled):
                self.bundle(cancel=cancel)
        self.assertEqual(set(self.output.iterdir()), before)
        self.assertEqual(self.marker.read_bytes(), b"da conservare")

    def test_changed_source_aborts_and_cleans_own_results(self):
        before = set(self.output.iterdir())
        real_open = os.open

        def opening(path, *args, **kwargs):
            if Path(path) == self.app / "main.py":
                Path(path).write_bytes(b"sorgente modificato durante apertura")
            return real_open(path, *args, **kwargs)

        with patch.object(source_bundle.os, "open", side_effect=opening):
            with self.assertRaisesRegex(ValueError, "cambiato"):
                self.bundle()
        self.assertEqual(set(self.output.iterdir()), before)

    def test_limits_are_enforced_and_do_not_remove_user_files(self):
        for constant, value in (("MAX_FILES", 0), ("MAX_FILE_BYTES", 1), ("MAX_TOTAL_BYTES", 1),
                                ("MAX_DIRECTORIES", 0), ("MAX_ENTRIES", 0), ("MAX_ARCHIVE_BYTES", 1)):
            with self.subTest(limit=constant):
                before = set(self.output.iterdir())
                with patch.object(source_bundle, constant, value):
                    with self.assertRaises(ValueError):
                        self.bundle()
                self.assertEqual(set(self.output.iterdir()), before)
                self.assertEqual(self.marker.read_bytes(), b"da conservare")

    def test_application_without_python_sources_is_rejected(self):
        (self.app / "main.py").unlink()
        self.write("app/main.ui", b"<ui/>")
        with self.assertRaisesRegex(ValueError, "Python"):
            self.bundle()


if __name__ == "__main__":
    unittest.main()
