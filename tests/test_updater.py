import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from llm_harness.updater import (
    ReleaseArtifact,
    UpdateError,
    _write_launchers,
    _write_posix_launchers,
    current_release,
    install_release,
    rollback_release,
)


class UpdaterTests(unittest.TestCase):
    def _package(self, root: Path, name: str, content: str) -> tuple[Path, ReleaseArtifact]:
        package = root / f"{name}.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("scripts/hoh", content)
        data = package.read_bytes()
        artifact = ReleaseArtifact(
            version=name,
            platform="windows-x86_64",
            url="https://example.invalid/hoh.zip",
            sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data),
        )
        return package, artifact

    def test_installs_versioned_release_and_rolls_pointer_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_package, first = self._package(root, "0.6.0", "first")
            second_package, second = self._package(root, "0.7.0", "second")
            installed = install_release(first_package, first, root=root / "install")
            self.assertEqual("first", (installed / "scripts" / "hoh").read_text())
            install_release(second_package, second, root=root / "install")
            self.assertEqual("0.7.0", current_release(root / "install"))
            launcher_name = "launch-hoh.ps1" if os.name == "nt" else "hoh"
            self.assertTrue((root / "install" / launcher_name).is_file())
            self.assertEqual("0.6.0", rollback_release(root / "install"))
            install_release(second_package, second, root=root / "install")
            self.assertEqual("0.7.0", current_release(root / "install"))

    def test_rejects_hash_mismatch_and_archive_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, artifact = self._package(root, "0.6.0", "ok")
            bad = ReleaseArtifact(**{**artifact.__dict__, "sha256": "0" * 64})
            with self.assertRaisesRegex(UpdateError, "SHA-256"):
                install_release(package, bad, root=root / "install")

            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr("../escape.txt", "bad")
            data = unsafe.read_bytes()
            unsafe_artifact = ReleaseArtifact(
                version="0.6.1", platform="windows-x86_64", url="https://example.invalid/unsafe.zip",
                sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
            )
            with self.assertRaisesRegex(UpdateError, "unsafe path"):
                install_release(unsafe, unsafe_artifact, root=root / "install")
            self.assertFalse((root / "escape.txt").exists())

    @unittest.skipIf(os.name == "nt", "POSIX file modes are not meaningful on Windows.")
    def test_update_keeps_shell_entry_points_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "payload.zip"
            # PowerShell's Compress-Archive stores no Unix mode at all, so the payload
            # arrives with 0 in external_attr exactly like this.
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("scripts/hoh.sh", "#!/usr/bin/env sh\nexit 0\n")
                archive.writestr("scripts/hoh-gui.sh", "#!/usr/bin/env sh\nexit 0\n")
                archive.writestr("README.md", "not executable\n")
            data = package.read_bytes()
            artifact = ReleaseArtifact(
                version="1.0.1",
                platform="linux-x86_64",
                url="https://example.invalid/hoh.zip",
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
            )

            installed = install_release(package, artifact, root=root / "install")

            self.assertTrue(os.access(installed / "scripts" / "hoh.sh", os.X_OK))
            self.assertTrue(os.access(installed / "scripts" / "hoh-gui.sh", os.X_OK))
            self.assertFalse(os.access(installed / "README.md", os.X_OK))
            self.assertTrue(os.access(root / "install" / "hoh", os.X_OK))

    def test_posix_updates_write_cli_and_gui_launchers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_posix_launchers(root)

            self.assertIn("scripts/hoh.sh", (root / "hoh").read_text(encoding="utf-8"))
            self.assertIn("scripts/hoh-gui.sh", (root / "hoh-gui").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
