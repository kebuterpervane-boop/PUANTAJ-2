import unittest
from pathlib import Path


class SourceEncodingTests(unittest.TestCase):
    def test_python_sources_are_utf8(self):
        root = Path(__file__).resolve().parent.parent
        excluded = {"build", "dist", "__pycache__", ".git", "migrations_backups"}
        bad = []
        for path in root.rglob("*.py"):
            if any(part in excluded for part in path.parts):
                continue
            try:
                path.read_text(encoding="utf-8")
            except Exception as exc:
                bad.append(f"{path}: {exc}")
        self.assertEqual(bad, [], msg="\n".join(bad))


if __name__ == "__main__":
    unittest.main()
