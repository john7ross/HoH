import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from llm_harness.portable_package import PortablePackageError, build_posix_portable_package


class PortablePackageTests(unittest.TestCase):
    def test_builds_hash_bound_linux_archive_without_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts" / "hoh.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "src" / "llm_harness" / "__pycache__").mkdir(parents=True)
            (root / "src" / "llm_harness" / "__init__.py").write_text("", encoding="utf-8")
            (root / "src" / "llm_harness" / "__pycache__" / "bad.pyc").write_bytes(b"bad")
            (root / "README.md").write_text("HoH", encoding="utf-8")
            with (
                patch("llm_harness.portable_package.platform.system", return_value="Linux"),
                patch("llm_harness.portable_package.platform.machine", return_value="x86_64"),
            ):
                result = build_posix_portable_package(
                    root, root / "dist", product_version="0.6.0", source_commit="abc123", source_clean=True
                )
            manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
            with zipfile.ZipFile(result.package) as archive:
                names = archive.namelist()
            self.assertIn("scripts/hoh.sh", names)
            self.assertNotIn("src/llm_harness/__pycache__/bad.pyc", names)
            self.assertEqual(result.sha256, manifest["package"]["sha256"])
            self.assertEqual("linux-x86_64", manifest["target"]["platform"])

    def test_an_embedded_interpreter_is_packaged_and_named_in_the_manifest(self):
        """A self-contained install must not be updated into one that needs host Python.

        The Debian package carries its own interpreter, so a machine can have none at
        all. An update payload built from the same tree that dropped the interpreter,
        or that claimed a host Python in its manifest, would break exactly the
        installations this exists for.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts" / "hoh.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "src" / "llm_harness").mkdir(parents=True)
            (root / "src" / "llm_harness" / "__init__.py").write_text("", encoding="utf-8")
            interpreter = root / "runtime" / "python" / "bin" / "python3"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text("#!/bin/sh\necho 3.11.16\n", encoding="utf-8")
            interpreter.chmod(0o755)
            (root / "runtime" / "python" / "lib" / "python3.11").mkdir(parents=True)
            (root / "runtime" / "python" / "lib" / "python3.11" / "os.py").write_text("", encoding="utf-8")

            with (
                patch("llm_harness.portable_package.platform.system", return_value="Linux"),
                patch("llm_harness.portable_package.platform.machine", return_value="x86_64"),
                patch(
                    "llm_harness.portable_package.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, "3.11.16\n", ""),
                ),
            ):
                result = build_posix_portable_package(
                    root, root / "dist", product_version="1.0.0", source_commit="abc123", source_clean=True
                )

            manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
            with zipfile.ZipFile(result.package) as archive:
                names = archive.namelist()

            self.assertIn("runtime/python/bin/python3", names)
            self.assertIn("runtime/python/lib/python3.11/os.py", names)
            self.assertEqual("Python 3.11.16 (embedded)", manifest["target"]["runtime"])

    def test_rejects_dirty_or_unsupported_build_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("llm_harness.portable_package.platform.system", return_value="Windows"):
                with self.assertRaisesRegex(PortablePackageError, "only on Linux or macOS"):
                    build_posix_portable_package(
                        root, root / "dist", product_version="0.6.0", source_commit="abc", source_clean=True
                    )
            with patch("llm_harness.portable_package.platform.system", return_value="Linux"):
                with self.assertRaisesRegex(PortablePackageError, "dirty"):
                    build_posix_portable_package(
                        root, root / "dist", product_version="0.6.0", source_commit="abc", source_clean=False
                    )


if __name__ == "__main__":
    unittest.main()
