"""L'avvio per i sorgenti non deve eseguire il collaudo o il gestionale."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("_source_bundle_builder", ROOT / "tools" / "build_simple_launcher.py")
_BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BUILDER)


class PrepareIntegrationTests(unittest.TestCase):
    def test_embedded_source_entry_runs_only_preparation_in_isolated_process(self):
        with tempfile.TemporaryDirectory(prefix="su source launcher ") as folder:
            launcher = _BUILDER.build(Path(folder) / "Prepara & sorgenti à.bat", ROOT, entry=_BUILDER.SOURCE_ENTRY)
            with ZipFile(launcher) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read("__main__.py").decode(), _BUILDER.SOURCE_ENTRY)
                self.assertNotIn("sorgenti/", "\n".join(archive.namelist()))
            bootstrap = """
import json,runpy,sys
archive=sys.argv[1]
sys.path.insert(0,archive)
from su_print_counters import acceptance,prepare_integration,source_bundle
def wrong_entry(*args,**kwargs):
    raise AssertionError('Collaudo chiamato durante preparazione sorgenti')
acceptance.main=wrong_entry
def prepare():
    print(json.dumps({'source_module':source_bundle.__file__,'isolated':sys.flags.isolated,'argv0':sys.argv[0]}))
    return 0
prepare_integration.main=prepare
runpy.run_path(archive,run_name='__main__')
"""
            result = subprocess.run([sys.executable, "-I", "-c", bootstrap, str(launcher)],
                                    cwd=folder, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["isolated"], 1)
            self.assertEqual(output["argv0"], str(launcher))
            self.assertTrue(output["source_module"].startswith(str(launcher)))


if __name__ == "__main__":
    unittest.main()
