import json
from contextlib import redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

from moh_ai.cli import doctor, import_text, main


class FoundationTests(unittest.TestCase):
    def test_doctor_is_honest_and_does_not_expose_credentials(self):
        result = doctor()
        self.assertTrue(result["strict_free"])
        self.assertEqual(result["generation"], "ollama_local_optional")
        self.assertEqual(result["model_mode"], "ollama_local_optional")
        self.assertNotIn("token", result)

    def test_import_preserves_hebrew_and_duplicate_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.txt"
            source.write_text("שלום לקהילת מוח AI", encoding="utf-8")
            first = import_text(root / "db.sqlite3", source, "מקור בדיקה", "text")
            second = import_text(root / "db.sqlite3", source, "מקור בדיקה", "text")
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(first["id"], second["id"])
            with sqlite3.connect(root / "db.sqlite3") as conn:
                text = conn.execute("SELECT content FROM imports").fetchone()[0]
            self.assertEqual(text, "שלום לקהילת מוח AI")

    def test_transcript_and_text_keep_distinct_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "transcript.txt"
            source.write_text("same content", encoding="utf-8")
            a = import_text(root / "db.sqlite3", source, "text", "text")
            b = import_text(root / "db.sqlite3", source, "transcript", "transcript")
            self.assertNotEqual(a["id"], b["id"])
            self.assertEqual(b["analysis_basis"], "transcript")

    def test_empty_input_rejected_before_database_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "empty.txt"
            source.write_text("  ", encoding="utf-8")
            with self.assertRaises(ValueError):
                import_text(root / "db.sqlite3", source, "title", "text")
            self.assertFalse((root / "db.sqlite3").exists())

    def test_cli_missing_file_returns_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(["--data-dir", directory, "ingest", "--file",
                             str(Path(directory) / "missing.txt"), "--title", "x"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(buffer.getvalue())["status"], "error")

    def test_large_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.txt"
            source.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                import_text(root / "db.sqlite3", source, "title", "text")


if __name__ == "__main__":
    unittest.main()
