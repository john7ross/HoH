from pathlib import Path
import re
import subprocess
import unittest

from llm_harness.command_policy import OperatorCommandPolicy


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ScriptContractTests(unittest.TestCase):
    def test_public_release_metadata_declares_apache_license(self):
        license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
        notice_text = (PROJECT_ROOT / "NOTICE").read_text(encoding="utf-8")
        project = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        readme_ru = (PROJECT_ROOT / "README.ru.md").read_text(encoding="utf-8")

        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertIn("Copyright 2026 Sergey Lebedev", license_text)
        self.assertIn("Copyright 2026 Sergey Lebedev", notice_text)
        self.assertIn("https://www.apache.org/licenses/LICENSE-2.0", notice_text)
        self.assertIn('license = { file = "LICENSE" }', project)
        self.assertIn('authors = [{ name = "Sergey Lebedev" }]', project)
        self.assertIn("Apache License 2.0", readme)
        self.assertIn("Apache License 2.0", readme_ru)
        self.assertNotIn("PolyForm", readme)
        self.assertNotIn("PolyForm", readme_ru)

    def test_github_ci_and_release_workflows_cover_supported_native_targets(self):
        ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        release = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        release_config = (PROJECT_ROOT / ".github" / "release.yml").read_text(encoding="utf-8")

        for target in ("ubuntu-latest", "windows-latest", "macos-latest"):
            self.assertIn(target, ci)
            self.assertIn(target, release)
        self.assertIn("python -m unittest discover -s tests", ci)
        self.assertIn("protocol-conformance", ci)
        self.assertIn("HoH-Setup-*.exe", release)
        self.assertIn("hoh_*.deb", release)
        self.assertIn("HoH-*.dmg", release)
        # Releases are published by hand from packages installed on real machines.
        # A tag push used to start three runner builds for a release that already
        # existed, and the guard that stopped them ran only after they finished.
        # The build workflow now starts on request and cannot publish anything.
        self.assertNotIn("push:", release)
        self.assertIn("workflow_dispatch", release)
        self.assertNotIn("gh release create", release)
        self.assertNotIn("contents: write", release)
        self.assertIn("User-facing changes", release_config)

    def test_every_launcher_says_where_the_product_is_installed(self):
        """doctor cannot tell an installation from a project without being told.

        Resolving the embedded runtime against the project root reported the
        interpreter the user was running on as missing, and pointed at a build
        script that ships for maintainers. hoh-gui.sh delegates to hoh.sh.
        """
        for name in ("hoh.ps1", "hoh-gui.ps1", "hoh.sh"):
            script = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn("HOH_DISTRIBUTION_ROOT", script)

    def test_the_posix_launcher_names_a_python_that_is_too_old(self):
        """macOS ships 3.9 as python3, and a source checkout falls back to it.

        The run then reached the first 3.11 name and died with an ImportError
        about datetime.UTC, which says nothing about what to install.
        """
        script = (PROJECT_ROOT / "scripts" / "hoh.sh").read_text(encoding="utf-8")

        self.assertIn("version_info >= (3, 11)", script)
        self.assertIn("Python 3.11 or newer is required; $python_bin is $found.", script)
        self.assertIn("HOH_PYTHON", script)

    def test_launcher_uses_embedded_python_and_package_path(self):
        script = (PROJECT_ROOT / "scripts" / "hoh.ps1").read_text(encoding="utf-8")

        self.assertIn("runtime\\python\\python.exe", script)
        self.assertIn("runtime\\site-packages", script)
        self.assertIn("-m llm_harness", script)

    def test_gui_launcher_uses_embedded_pythonw_and_hidden_window(self):
        script = (PROJECT_ROOT / "scripts" / "hoh-gui.ps1").read_text(encoding="utf-8")

        self.assertIn("runtime\\python\\pythonw.exe", script)
        self.assertIn("runtime\\site-packages", script)
        self.assertIn('"gui"', script)
        self.assertIn("-WindowStyle Hidden", script)

    def test_offline_install_uses_local_wheelhouse_only(self):
        script = (PROJECT_ROOT / "scripts" / "install-offline.ps1").read_text(encoding="utf-8")

        self.assertIn("--no-index", script)
        self.assertIn("--find-links", script)
        self.assertIn("--target", script)
        self.assertIn("vendor\\wheels", script)
        self.assertIn("requirements.lock", script)
        self.assertIn("--requirement", script)
        self.assertIn("--no-deps", script)

    def test_bootstrap_can_build_and_install_offline(self):
        script = (PROJECT_ROOT / "scripts" / "bootstrap-runtime.ps1").read_text(encoding="utf-8")

        self.assertIn("BuildWheelhouse", script)
        self.assertIn("InstallOffline", script)
        self.assertIn("install-offline.ps1", script)

    def test_update_payload_requires_runtime_gates(self):
        script = (PROJECT_ROOT / "scripts" / "build-update-payload.ps1").read_text(encoding="utf-8")

        self.assertIn("runtime\\python\\python.exe", script)
        self.assertIn("runtime\\site-packages", script)
        self.assertIn("vendor\\wheels", script)
        self.assertIn("doctor", script)
        self.assertIn("audit", script)
        self.assertIn("-m unittest discover -s tests", script)
        self.assertIn("-m compileall -q src tests", script)
        self.assertIn("build-wheelhouse.ps1", script)
        self.assertIn("install-offline.ps1", script)
        self.assertIn("requirements.lock", script)
        self.assertIn('"LICENSE"', script)
        self.assertIn('"NOTICE"', script)
        self.assertIn('"THIRD-PARTY-NOTICES.md"', script)
        self.assertIn('"THIRD-PARTY-NOTICES.ru.md"', script)
        self.assertIn("SkipRefresh", script)
        self.assertIn("Compress-Archive", script)
        self.assertIn("HoH-update-payload.manifest.json", script)
        self.assertIn("hoh.release-manifest", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn("git_clean", script)
        self.assertIn("wheelhouse_refresh", script)
        self.assertIn("Package SHA-256", script)
        self.assertIn('"schemas"', script)
        self.assertIn('"examples"', script)
        self.assertIn('"catalogs"', script)
        self.assertIn("ReleaseUrl", script)
        self.assertIn("HoH-releases.signed.json", script)

    def test_no_tracked_file_names_the_checkout_it_lives_in(self):
        """A release must not tell users where the maintainer keeps the repository.

        docs/release.md and docs/workspace-cleanup.md hardcoded the maintainer's own
        path, and the Windows payload shipped it inside the installer. Everything
        addressable here uses the C:\\path\\to\\repo stand-in instead.
        """
        listed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
        )
        if listed.returncode != 0:
            self.skipTest("not a git checkout")
        marker = str(PROJECT_ROOT).encode("utf-8")
        offenders = []
        for name in listed.stdout.split(b"\0"):
            if not name:
                continue
            path = PROJECT_ROOT / name.decode("utf-8")
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            if marker in path.read_bytes():
                offenders.append(name.decode("utf-8"))

        self.assertEqual(offenders, [], f"these name the checkout path: {offenders}")

    def test_every_platform_package_refuses_a_dirty_checkout(self):
        """Two of the three refused; the Windows one only noted it in the manifest.

        That made the artifact most people install the one that could be cut from
        unreviewed edits.
        """
        for name in ("package-deb.sh", "package-macos.sh", "package-windows-installer.ps1"):
            script = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertIn("status --porcelain", script)
                self.assertIn("dirty Git checkout", script)

        windows = (PROJECT_ROOT / "scripts" / "package-windows-installer.ps1").read_text(encoding="utf-8")
        self.assertIn("changed while the release was being built", windows)

    def test_payload_build_drops_the_pip_console_script_and_gates_on_the_build_path(self):
        script = (PROJECT_ROOT / "scripts" / "build-update-payload.ps1").read_text(encoding="utf-8")

        self.assertIn("runtime\\site-packages\\bin", script)
        self.assertIn("Refusing to ship the build machine's path", script)

    def test_the_debian_package_carries_its_own_interpreter(self):
        """A user must not have to install Python to run HoH.

        Depending on the system python3 excluded three real cases: a machine with no
        network, because python3-tk has priority "optional" and apt cannot fetch it;
        any distribution older than Python 3.11, such as Ubuntu 22.04, where apt
        refuses the package outright; and any minimal image. The Windows installer has
        shipped its own runtime from the start.
        """
        deb = (PROJECT_ROOT / "scripts" / "package-deb.sh").read_text(encoding="utf-8")
        bootstrap = (PROJECT_ROOT / "scripts" / "bootstrap-runtime.sh").read_text(encoding="utf-8")
        launcher = (PROJECT_ROOT / "scripts" / "hoh.sh").read_text(encoding="utf-8")

        self.assertIn("'Depends: git'", deb)
        self.assertNotIn("python3-tk", deb)
        self.assertIn("runtime/python/bin/python3", deb)
        self.assertIn("bootstrap-runtime.sh", deb)
        # The mode sweep would otherwise leave the interpreter unexecutable.
        self.assertIn("$runtime_stage/bin", deb)
        self.assertIn("import tkinter", deb)
        # Pinned, so a build gets exactly this interpreter or fails.
        self.assertIn("python-build-standalone", bootstrap)
        self.assertIn("sha256sum", bootstrap)
        self.assertIn("Checksum mismatch", bootstrap)
        # A source checkout has no runtime and must still work.
        self.assertIn("runtime/python/bin/python3", launcher)
        self.assertIn("HOH_PYTHON", launcher)

    def test_the_debian_package_refuses_bytecode_and_the_build_path(self):
        """The .deb shipped 83 .pyc files naming the builder's home directory.

        The Windows payload build has refused compiled bytecode from the start, for
        the stated reason: a .pyc records the path it was compiled from, so it prints
        the maintainer's directories in a user's traceback. package-deb.sh had no
        such gate, while the checklist claimed no artifact carried build paths.
        """
        script = (PROJECT_ROOT / "scripts" / "package-deb.sh").read_text(encoding="utf-8")

        self.assertIn("__pycache__", script)
        self.assertIn("Refusing to ship compiled bytecode", script)
        self.assertIn("Refusing to ship the build machine's path", script)
        # The interpreter lives inside the checkout, so running it from there
        # rewrites its own stdlib bytecode with paths under the build directory.
        # Compiling with -d records the installed location instead.
        self.assertIn("-m compileall", script)
        self.assertIn('-d "/opt/hoh/runtime/python/lib/', script)

    def test_nothing_is_run_through_a_bit_windows_cannot_record(self):
        """Invoke the shell scripts as "bash script.sh", never as "./script.sh".

        Git stores the executable bit, and `git add` on Windows cannot see one, so
        a publication commit made there records every .sh as 100644 and a clone on
        Linux answers ./scripts/package-deb.sh with "Permission denied". Requiring
        the bit meant a manual fixup before every publication; not depending on it
        costs one word per call site.
        """
        workflows = sorted((PROJECT_ROOT / ".github" / "workflows").glob("*.yml"))
        documents = sorted(PROJECT_ROOT.glob("docs/*.md")) + [PROJECT_ROOT / "README.md"]
        for path in workflows + documents:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(path=path.name, line=number):
                    self.assertNotIn("./scripts/", line)

    def test_powershell_release_scripts_avoid_the_broken_include_filter(self):
        """`-Recurse -Include *.pyc` returns every .py file under Windows PowerShell 5.1.

        That is the shell that ships with Windows, and it is what `powershell` runs.
        The bytecode gate counted 3417 leftovers in a staging tree that held none, so
        the installer could not be built at all outside PowerShell 7.
        """
        for name in sorted(p.name for p in (PROJECT_ROOT / "scripts").glob("*.ps1")):
            script = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
            # The comment explaining the ban names the parameter, so read code only.
            for line in script.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                with self.subTest(script=name, line=line.strip()):
                    self.assertNotIn("-Include", line)

    def test_every_platform_package_ships_the_third_party_notices(self):
        """All three packages embed CPython, Tcl/Tk and OpenSSL, and none named them.

        The notices file was written and tracked, but no packager copied it, so the
        one document that lists the licences of the code inside the artifact was the
        one document a user could not find there.
        """
        packagers = {
            "package-deb.sh": ("THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.ru.md"),
            "package-macos.sh": ("THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.ru.md"),
            "build-update-payload.ps1": ('"THIRD-PARTY-NOTICES.md"', '"THIRD-PARTY-NOTICES.ru.md"'),
        }
        for name, expected in packagers.items():
            script = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                for token in expected:
                    self.assertIn(token, script)
        # Copying is not enough: a later edit to the item list must fail the build,
        # not produce an artifact that is quietly missing the notices.
        for name in ("package-deb.sh", "package-macos.sh", "build-update-payload.ps1"):
            script = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
            with self.subTest(gate=name):
                self.assertIn("missing required file", script)
        for name in ("THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.ru.md"):
            notices = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(document=name):
                for component in ("CPython", "Tcl", "OpenSSL"):
                    self.assertIn(component, notices)

    def test_payload_gate_commands_are_runnable_without_a_shell(self):
        """Grepping the script for a substring proved nothing about the commands in it.

        The release gate ran `set PYTHONPATH=src&& ...`, which needs cmd.exe, long
        after the audit stopped using a shell. The text assertions above were happy,
        and the Windows installer could not be built at all.
        """
        script = (PROJECT_ROOT / "scripts" / "build-update-payload.ps1").read_text(encoding="utf-8")
        checks = re.findall(r'^\s*\$\w*Check = "([^"]+)"', script, re.MULTILINE)
        policy = OperatorCommandPolicy()

        self.assertEqual(len(checks), 3, f"expected three gate commands, found {checks}")
        for command in checks:
            with self.subTest(command=command):
                argv = policy.resolve(command)
                self.assertTrue(argv)

    def test_self_signed_publisher_and_catalog_scripts_keep_private_key_separate(self):
        publisher = (PROJECT_ROOT / "scripts" / "new-self-signed-publisher.ps1").read_text(encoding="utf-8")
        signer = (PROJECT_ROOT / "scripts" / "sign-catalog.ps1").read_text(encoding="utf-8")

        self.assertIn("New-SelfSignedCertificate", publisher)
        self.assertIn("Export-PfxCertificate", publisher)
        self.assertIn("hoh-publisher.public.json", publisher)
        self.assertIn("ExportParameters($false)", publisher)
        self.assertIn("RSASignaturePadding]::Pkcs1", signer)
        self.assertIn("EphemeralKeySet", signer)
        self.assertIn("hoh.signed-catalog", signer)

    def test_internal_updater_verifies_manifest_before_extracting(self):
        script = (PROJECT_ROOT / "scripts" / "install-update-payload.ps1").read_text(encoding="utf-8")

        self.assertIn("hoh.release-manifest", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn("unsafe path", script)
        self.assertIn("current.json", script)
        self.assertIn("$env:LOCALAPPDATA", script)

    def test_windows_release_is_a_per_user_inno_setup_installer(self):
        builder = (PROJECT_ROOT / "scripts" / "package-windows-installer.ps1").read_text(encoding="utf-8")
        installer = (PROJECT_ROOT / "installer" / "windows" / "HoH.iss").read_text(encoding="utf-8")

        self.assertIn("ISCC.exe", builder)
        self.assertIn("HoH-Setup-$version.exe", builder)
        self.assertIn("build-update-payload.ps1", builder)
        self.assertIn("install-update-payload.ps1", builder)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\HoH", installer)
        self.assertIn("PrivilegesRequired=lowest", installer)
        self.assertIn("UninstallDisplayName=HoH", installer)
        self.assertIn("{autoprograms}\\HoH", installer)
        self.assertIn("SetupIconFile={#AppIcon}", installer)
        self.assertIn("hoh.ico", builder)

    def test_linux_release_is_an_installable_debian_desktop_package(self):
        script = (PROJECT_ROOT / "scripts" / "package-deb.sh").read_text(encoding="utf-8")
        desktop = (PROJECT_ROOT / "packaging" / "linux" / "hoh.desktop").read_text(encoding="utf-8")

        self.assertIn("dpkg-deb --build --root-owner-group", script)
        self.assertIn("git -c core.autocrlf=true", script)
        self.assertIn("Depends: git", script)
        self.assertIn("doctor.toml", script)
        self.assertIn("LICENSE NOTICE", script)
        self.assertIn('$(basename "$package")', script)
        self.assertIn('name = "release-smoke"', script)
        self.assertIn("| tr -d '\\r'", script)
        self.assertIn("/usr/bin/hoh-gui", script)
        self.assertIn("Exec=hoh-gui", desktop)
        self.assertIn("Terminal=false", desktop)

    def test_macos_release_bundles_python_and_tk_into_an_application_dmg(self):
        script = (PROJECT_ROOT / "scripts" / "package-macos.sh").read_text(encoding="utf-8")
        entry = (PROJECT_ROOT / "packaging" / "macos" / "hoh_gui.py").read_text(encoding="utf-8")

        self.assertIn("python3 -m PyInstaller", script)
        self.assertIn("hdiutil create", script)
        self.assertIn("codesign --force --deep --sign -", script)
        self.assertIn("HoH.app", script)
        self.assertIn("doctor.toml", script)
        self.assertIn("LICENSE NOTICE", script)
        self.assertIn('$(basename "$dmg")', script)
        self.assertIn('name = "release-smoke"', script)
        self.assertIn("| tr -d '\\r'", script)
        # Started with no arguments the bundle opens the GUI; the rest of the
        # interpreter-style argument handling is covered by test_frozen_entry.
        self.assertIn("plan_frozen_invocation(sys.argv[1:])", entry)

    def test_offline_refresh_removes_only_stale_hoh_package_versions(self):
        wheelhouse = (PROJECT_ROOT / "scripts" / "build-wheelhouse.ps1").read_text(encoding="utf-8")
        installer = (PROJECT_ROOT / "scripts" / "install-offline.ps1").read_text(encoding="utf-8")

        self.assertIn('llm_harness-*.whl', wheelhouse)
        self.assertIn('$_.Name -eq "llm_harness"', installer)
        self.assertIn('llm_harness-*.dist-info', installer)
        self.assertIn('Refusing to refresh package files outside the project', installer)

    def test_build_wheelhouse_enforces_dependency_lock(self):
        script = (PROJECT_ROOT / "scripts" / "build-wheelhouse.ps1").read_text(encoding="utf-8")

        self.assertIn("requirements.lock", script)
        self.assertIn("--requirement", script)
        self.assertIn("--no-deps", script)
        self.assertIn("Dependency lock file not found", script)

    def test_dependency_lock_is_tracked_and_runtime_only(self):
        lock = (PROJECT_ROOT / "requirements.lock").read_text(encoding="utf-8")

        self.assertIn("portable runtime dependency lock", lock)
        self.assertIn("no third-party package dependencies", lock)

    def test_telegram_watch_task_wrapper_uses_bounded_portable_launcher(self):
        script = (PROJECT_ROOT / "scripts" / "register-telegram-watch-task.ps1").read_text(encoding="utf-8")

        self.assertIn("New-ScheduledTaskAction", script)
        self.assertIn("Register-ScheduledTask", script)
        self.assertIn("Unregister-ScheduledTask", script)
        self.assertIn("telegram-watch", script)
        self.assertIn("scripts\\hoh.ps1", script)
        self.assertIn("--iterations", script)
        self.assertIn("Iterations must be 1 or greater for scheduled runs", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)

    def test_workspace_scheduler_task_runs_due_projects_without_overlap(self):
        script = (PROJECT_ROOT / "scripts" / "register-workspace-scheduler-task.ps1").read_text(encoding="utf-8")

        self.assertIn("workspace", script)
        self.assertIn("run-due", script)
        self.assertIn("scripts\\hoh.ps1", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)
        self.assertIn("-RunLevel LeastPrivilege", script)
        self.assertIn("Unregister-ScheduledTask", script)
        self.assertIn("Get-ScheduledTaskInfo", script)

    def test_posix_launchers_and_user_schedulers_are_packaged(self):
        launcher = (PROJECT_ROOT / "scripts" / "hoh.sh").read_text(encoding="utf-8")
        installer = (PROJECT_ROOT / "scripts" / "install-offline.sh").read_text(encoding="utf-8")
        scheduler = (PROJECT_ROOT / "scripts" / "register-workspace-scheduler.sh").read_text(encoding="utf-8")

        self.assertIn("python3", launcher)
        self.assertIn("runtime/site-packages", launcher)
        self.assertIn("--no-index", installer)
        self.assertIn("systemctl --user", scheduler)
        self.assertIn("Library/LaunchAgents", scheduler)
        self.assertIn("launchctl bootstrap", scheduler)

    def test_daily_ops_wrapper_runs_doctor_and_queue_loop(self):
        script = (PROJECT_ROOT / "scripts" / "run-daily-ops.ps1").read_text(encoding="utf-8")

        self.assertIn("scripts\\hoh.ps1", script)
        self.assertIn("doctor", script)
        self.assertIn("queue-run-loop", script)
        self.assertIn("--final-audit", script)
        self.assertIn("--final-check", script)
        self.assertIn("$doctorArgs", script)
        self.assertIn("$loopCommonArgs", script)
        self.assertIn("Doctor failed. Queue loop was not started.", script)

    def test_env_template_contains_redacted_values_only(self):
        script = (PROJECT_ROOT / "scripts" / "hoh-env.template.ps1").read_text(encoding="utf-8")

        self.assertIn("HOH_TELEGRAM_BOT_TOKEN", script)
        self.assertIn("<bot-token>", script)
        self.assertIn("<operator-chat-id>", script)
        self.assertIn("<allowed-operator-user-id>", script)

    def test_scripts_resolve_default_project_root_in_body(self):
        for name in (
            "bootstrap-runtime.ps1",
            "build-wheelhouse.ps1",
            "install-offline.ps1",
            "hoh-gui.ps1",
            "build-update-payload.ps1",
            "register-telegram-watch-task.ps1",
            "run-daily-ops.ps1",
        ):
            script = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn('[string]$ProjectRoot = ""', script)
            self.assertIn('Join-Path $PSScriptRoot ".."', script)


if __name__ == "__main__":
    unittest.main()
