"""Client catalog preparation with a temporary install layout and simulated DataTool."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.paths import CLIENT_CATALOGS
from tools.prepare_client_catalogs import MODES, prepare_catalogs


class ClientCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        folder = Path(temporary).resolve()
        self.game = folder / "Overwatch 安装"
        (self.game / "data" / "casc" / "data").mkdir(parents=True)
        self.build_info = self.game / ".build.info"
        self.record_build("1.74.0.0.104319")
        self.tool = folder / "DataTool.exe"
        self.tool.touch()
        self.destination = folder / "catalogs"

    def record_build(self, version):
        self.build_info.write_text(
            f"Product!STRING:0|Version!STRING:0|Active!DEC:1\npro|{version}|1\n",
            encoding="utf-8-sig",
        )

    @staticmethod
    def export_catalog(command, **_):
        output = Path(next(arg.removeprefix("--out=") for arg in command if arg.startswith("--out=")))
        output.write_text('{"generated": {"fixture": true}}', encoding="utf-8")

    def test_game_folder_generates_all_catalogs(self):
        with patch("tools.prepare_client_catalogs.subprocess.run", side_effect=self.export_catalog) as run:
            prepare_catalogs(self.game, self.tool, self.destination)
        self.assertEqual({path.name for path in self.destination.iterdir()}, set(CLIENT_CATALOGS))
        self.assertEqual([call.args[0][2] for call in run.call_args_list], list(MODES))
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:2], [str(self.tool), str(self.game)])
            self.assertTrue({"--skip-keys", "--online=false", "--args-reset"}.issubset(command))
            self.assertEqual(call.kwargs["cwd"], self.tool.resolve().parent)

    def test_wrong_build_stops_before_running_datatool(self):
        self.record_build("2.25.1.0.155284N")
        with (
            patch("tools.prepare_client_catalogs.subprocess.run") as run,
            self.assertRaisesRegex(ValueError, "Expected Overwatch 1.74.0.0.104319"),
        ):
            prepare_catalogs(self.game, self.tool, self.destination)
        run.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_export_failure_does_not_publish_an_incomplete_catalog_directory(self):
        def fail_second(command, **kwargs):
            if command[2] == MODES[1]:
                raise subprocess.CalledProcessError(1, "DataTool")
            self.export_catalog(command, **kwargs)

        with (
            patch("tools.prepare_client_catalogs.subprocess.run", side_effect=fail_second),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            prepare_catalogs(self.game, self.tool, self.destination)
        self.assertFalse(self.destination.exists())
