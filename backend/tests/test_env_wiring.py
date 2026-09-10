import pathlib

from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


class InstallScriptTests(SimpleTestCase):
    def test_installer_writes_env_where_manage_py_reads_it(self):
        script = (REPO / "install.bat").read_text(encoding="utf-8", errors="replace")
        self.assertIn(
            "backend\\.env",
            script,
            "install.bat must write backend\\.env — manage.py loads that path, not the repo root",
        )
        self.assertNotIn(
            '> .env\n',
            script,
            "install.bat still writes a root .env, which manage.py never reads",
        )

    def test_manage_py_loads_backend_env(self):
        manage = (REPO / "backend" / "manage.py").read_text(encoding="utf-8")
        self.assertIn('load_dotenv(Path(__file__).resolve().parent / ".env")', manage)
