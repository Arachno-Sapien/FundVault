import pathlib

from django.test import SimpleTestCase

BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SELF_NAME = pathlib.Path(__file__).name

# Project code only, not a venv or other unrelated tree that might live
# inside backend/.
_PROJECT_ROOTS = [BACKEND / "apps", BACKEND / "fundvault_backend"]
_PROJECT_FILES = [BACKEND / "manage.py"]


def _project_py_files():
    for root in _PROJECT_ROOTS:
        yield from root.rglob("*.py")
    yield from _PROJECT_FILES


class LegacySchemaHackTests(SimpleTestCase):
    def test_ensure_profile_schema_is_gone(self):
        hits = []
        for path in _project_py_files():
            if "migrations" in path.parts or path.name == _SELF_NAME:
                continue
            text = path.read_text(encoding="utf-8")
            if "ensure_profile_schema" in text:
                hits.append(str(path.relative_to(BACKEND)))
        self.assertEqual(hits, [], f"ensure_profile_schema still referenced in: {hits}")

    def test_no_pragma_table_info_anywhere(self):
        hits = []
        for path in _project_py_files():
            if path.name == _SELF_NAME:
                continue
            if "PRAGMA table_info" in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(BACKEND)))
        self.assertEqual(hits, [], f"SQLite PRAGMA still present in: {hits}")
