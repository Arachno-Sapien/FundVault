import pathlib

from django.test import SimpleTestCase

BACKEND = pathlib.Path(__file__).resolve().parent.parent


class LegacySchemaHackTests(SimpleTestCase):
    def test_ensure_profile_schema_is_gone(self):
        hits = []
        for path in BACKEND.rglob("*.py"):
            if "migrations" in path.parts or path.name == __file__.rsplit("\\")[-1]:
                continue
            text = path.read_text(encoding="utf-8")
            if "ensure_profile_schema" in text:
                hits.append(str(path.relative_to(BACKEND)))
        self.assertEqual(hits, [], f"ensure_profile_schema still referenced in: {hits}")

    def test_no_pragma_table_info_anywhere(self):
        hits = []
        for path in BACKEND.rglob("*.py"):
            if path.name == pathlib.Path(__file__).name:
                continue
            if "PRAGMA table_info" in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(BACKEND)))
        self.assertEqual(hits, [], f"SQLite PRAGMA still present in: {hits}")
