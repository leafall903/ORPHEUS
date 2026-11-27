"""Offline tests for acquiring pinned tool sources without touching a game installation."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import runtime_tools as tools


class ExtractionToolsTests(unittest.TestCase):
    def test_origin_requires_the_expected_host_and_repository(self):
        for url in (tools.OWLIB_REPO, "git@github.com:overtools/owlib.git",
                    "ssh://git@github.com/overtools/OWLib"):
            self.assertTrue(tools._origin_matches(url, tools.OWLIB_REPO))
        for url in ("overtools/OWLib.git", "https://example.com/overtools/OWLib.git",
                    "https://github.com/elsewhere/OWLib.git", tools.PYTHON_REPO):
            self.assertFalse(tools._origin_matches(url, tools.OWLIB_REPO))

    def test_existing_sources_require_the_pin_and_clean_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            tools._git(folder, "init", "--quiet")
            tools._git(folder, "remote", "add", "origin", tools.PYTHON_REPO)
            sample = folder / "sample.py"
            sample.write_text("value = 1\n", encoding="utf-8")
            tools._git(folder, "add", "sample.py")
            tools._git(folder, "-c", "user.name=Test", "-c", "user.email=test@invalid",
                       "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            commit = tools._git(folder, "rev-parse", "HEAD")
            tools._verify(folder, tools.PYTHON_REPO, commit)
            with self.assertRaisesRegex(RuntimeError, "Expected"):
                tools._verify(folder, tools.PYTHON_REPO, "0" * 40)
            sample.write_text("value = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Tracked files"):
                tools._verify(folder, tools.PYTHON_REPO, commit)

    def test_interrupted_fetch_does_not_publish_a_partial_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with mock.patch.object(tools, "_git", side_effect=subprocess.CalledProcessError(1, "git")):
                with self.assertRaises(subprocess.CalledProcessError):
                    tools._checkout(cache, "python", tools.PYTHON_REPO, tools.PYTHON_COMMIT)
            self.assertEqual(list(cache.iterdir()), [])

    def test_build_distinguishes_an_installed_runtime_from_an_sdk(self):
        with mock.patch.object(tools, "_program", return_value="dotnet"):
            for installed in ("", "6.0.100 [sdk]\n7.0.300 [sdk]\n"):
                with mock.patch.object(subprocess, "check_output", return_value=installed):
                    with self.assertRaisesRegex(RuntimeError, "x64 .NET 8 SDK"):
                        tools._build_sdk()
            with mock.patch.object(subprocess, "check_output", return_value="8.0.404 [sdk]\n"):
                self.assertEqual(tools._build_sdk(), "dotnet")

    def test_nested_msbuild_git_never_receives_a_partial_command_config(self):
        sample = dict(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="example.value", GIT_CONFIG_VALUE_0="",
                      GIT_CONFIG_NOSYSTEM="1", PATH="example")
        with mock.patch.dict(os.environ, sample, clear=True):
            self.assertEqual(tools._msbuild_environment(), {"GIT_CONFIG_NOSYSTEM": "1", "PATH": "example"})
            self.assertEqual(dict(os.environ), sample)

    def test_cache_inside_game_or_project_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for marker in (".build.info", ".git"):
                (root / marker).touch()
                with self.assertRaisesRegex(RuntimeError, "outside"):
                    tools._cache_dir(root / "cache")
                self.assertFalse((root / "cache").exists())
                (root / marker).unlink()

    @unittest.skipUnless(os.name == "nt", "Windows extractor")
    def test_explicit_tools_need_no_cache_download_or_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference, owlib, explicit = root / "ref", root / "src", root / "provided/DataTool.exe"
            markers = [reference / "tools/prepare_client_catalogs.py", owlib / tools.PROJECT, explicit]
            for file in markers:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            env = dict(ORPHEUS_PYTHON_TOOLS=str(reference), ORPHEUS_OWLIB=str(owlib),
                       ORPHEUS_DATATOOL=str(explicit))
            with mock.patch.dict(os.environ, env), mock.patch.object(tools, "_verify") as verify, \
                    mock.patch.object(tools, "_verify_tool") as verify_tool:
                self.assertEqual(tools.extraction_tools(root / "cache"), (reference, owlib, explicit))
            self.assertFalse((root / "cache").exists())
            self.assertEqual(verify.call_count, 2)
            verify_tool.assert_called_once_with(explicit)

    def test_data_tool_metadata_must_match_the_selected_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Static").mkdir()
            for name in ("DataTool.exe", "DataTool.dll", "TACTLib.dll", "TankLib.dll", "Static/GUIDNames.csv"):
                (root / name).touch()
            with mock.patch.object(tools, "_program", return_value="powershell"), \
                    mock.patch.object(subprocess, "check_output", return_value="1.74.0.0+develop+7afe5f52"):
                tools._verify_tool(root / "DataTool.exe")
            with mock.patch.object(tools, "_program", return_value="powershell"), \
                    mock.patch.object(subprocess, "check_output", return_value="1.74.0.0+develop+12345678"):
                with self.assertRaisesRegex(RuntimeError, "OWLib"):
                    tools._verify_tool(root / "DataTool.exe")


if __name__ == "__main__":
    unittest.main()
