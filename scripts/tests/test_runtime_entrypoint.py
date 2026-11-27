"""Exercise the runtime CLI with a synthetic installation and stand-ins for the native tools."""

from contextlib import redirect_stderr, redirect_stdout
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime
import runtime_install
from runtime_validation import GRAPH, PRACTICE_MODE


def manifest(root):
    entries = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file() and path.name != "manifest.json"
    }
    (root / "manifest.json").write_text(json.dumps({
        "schema": "orpheus-runtime-copy-v1", "sha256": entries,
    }), encoding="utf-8")


def write_pack(folder):
    target = folder / GRAPH
    target.parent.mkdir(parents=True)
    graph = "0x058000000000002A"
    teams = {str(team): {"body": graph, "controller": graph} for team in (0, 1)}
    body = {"target_tags": ["0x0D60000000000001"]}
    bundle = {
        "version": 1, "graphs": [{"index": 42}],
        "mode_scripts": {PRACTICE_MODE: {"graph": graph, "teams": teams}},
        "mode_variables": {PRACTICE_MODE: {"10": True}},
        "mode_rulesets": [{"mode": PRACTICE_MODE, "rules": [{"id": 30, "value": 1}]}],
        "bodies": {f"0x0400{index:012X}": body for index in range(1, 33)},
    }
    target.write_bytes(gzip.compress(json.dumps(bundle).encode()))
    manifest(folder)


class RuntimeEntrypointTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.game = self.root / "game"
        self.retail = self.game / "_retail_"
        self.retail.mkdir(parents=True)
        (self.game / "data/casc/data").mkdir(parents=True)
        self.source = self.root / "inputs"
        self.cache = self.root / "cache"
        self.dest = self.root / "private" / "ready"
        self.repo = self.root / "repo"
        self.enterContext(patch.object(runtime, "ROOT", self.repo))
        self.generate = self.enterContext(patch.object(runtime_install, "generate", return_value=[]))
        self.native = self.enterContext(patch.object(runtime, "native"))
        self.build_info([("pro", 1, runtime.VERSION)])

    def build_info(self, rows):
        lines = ["Product!STRING:0|Active!DEC:1|Version!STRING:0"]
        lines.extend(f"{product}|{active}|{version}" for product, active, version in rows)
        (self.game / ".build.info").write_text("\n".join(lines), encoding="utf-8-sig")

    def inputs(self):
        for name in (*runtime.PRIVATE_INPUTS, "mode-rulesets.json"):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == "mode-rulesets.json":
                path.write_text("[]", encoding="utf-8")
            else:
                path.write_bytes(b"{}" if path.suffix == ".json" else b"synthetic test input")
        manifest(self.source)

    def build(self, *, game=None, dest=None, failure=None):
        args = ["build", str(game or self.retail), "--inputs", str(self.source),
                "--output", str(dest or self.dest), "--cache", str(self.cache)]
        stderr = io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            if failure is None:
                runtime.main(args)
            else:
                with self.assertRaises(SystemExit) as raised:
                    runtime.main(args)
                self.assertEqual(raised.exception.code, 1)
                self.assertIn(failure, stderr.getvalue())

    def assert_not_published(self):
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.dest.parent.glob(".orpheus-build-*")), [])

    def test_active_retail_product_and_existing_output_are_checked_without_rebuilding(self):
        self.build_info([("pro", 0, "1.0"), ("other", 1, "2.0"), ("pro", 1, runtime.VERSION)])
        write_pack(self.dest)
        original = (self.dest / "manifest.json").read_bytes()
        for game in (self.retail, self.game):
            self.build(game=game)
        self.assertEqual((self.dest / "manifest.json").read_bytes(), original)
        self.assertEqual(self.native.call_count, 2)
        for call in self.native.call_args_list:
            self.assertEqual(call.args, ("orpheus_server", "--runtime", self.dest, "--check-runtime-graphs"))
        self.generate.assert_not_called()
        self.assertFalse(self.cache.exists())
        (self.dest / GRAPH).write_bytes(b"corrupted")
        self.build(failure="SHA-256 mismatch")
        self.assertEqual((self.dest / GRAPH).read_bytes(), b"corrupted")
        self.generate.assert_not_called()
        self.assertEqual(self.native.call_count, 2)

    def test_inactive_other_product_and_conflicting_retail_versions_do_not_select_a_build(self):
        for rows in [
            [("pro", 0, runtime.VERSION), ("other", 1, runtime.VERSION)],
            [("pro", 1, "1.74.0.0.104318"), ("other", 1, runtime.VERSION)],
            [("pro", 1, runtime.VERSION), ("pro", 1, "1.74.0.0.104318")],
        ]:
            with self.subTest(rows=rows):
                self.build_info(rows)
                self.build(failure="active retail")
                self.assert_not_published()
        self.native.assert_not_called()
        self.generate.assert_not_called()

    def test_output_cannot_be_under_game_repository_cache_or_inputs(self):
        for root in (self.game, self.repo, self.cache, self.source):
            with self.subTest(root=root.name):
                destination = root / "private-packs" / "new"
                self.build(dest=destination, failure="overlaps")
                self.assertFalse(destination.parent.exists())
        self.native.assert_not_called()
        self.generate.assert_not_called()

    def test_tampered_input_aborts_before_generation(self):
        self.inputs()
        (self.source / runtime.PRIVATE_INPUTS[0]).write_bytes(b"changed")
        self.build(failure="SHA-256 mismatch")
        self.assert_not_published()
        self.generate.assert_not_called()
        self.native.assert_not_called()

    def test_unlisted_ruleset_in_supplement_is_not_packed(self):
        self.inputs()
        profiles = self.source / "mode-rulesets.json"
        profiles.unlink()
        manifest(self.source)
        profiles.write_text("[]", encoding="utf-8")
        self.build(failure="mode-rulesets.json")
        self.assert_not_published()
        self.native.assert_not_called()

    def test_check_command_requires_only_the_pack(self):
        write_pack(self.dest)
        with redirect_stdout(io.StringIO()):
            runtime.main(["check", str(self.dest)])
        self.native.assert_called_once_with("orpheus_server", "--runtime", self.dest, "--check-runtime-graphs")
        self.generate.assert_not_called()

    def test_generator_failure_is_not_published(self):
        self.inputs()
        self.generate.side_effect = OSError("synthetic extractor failure")
        self.build(failure="synthetic extractor failure")
        self.assert_not_published()
        self.native.assert_not_called()

    def test_failed_final_verification_is_not_published(self):
        self.inputs()
        for fail_at in ("packer", "hash", "server"):
            with self.subTest(fail_at=fail_at):
                def native(binary, *args):
                    self.assertFalse(self.dest.exists())
                    if binary == "prepare_server_runtime":
                        pack = args[args.index("--destination") + 1]
                        write_pack(pack)
                        if fail_at == "packer":
                            raise OSError("packer failed")
                        if fail_at == "hash":
                            (pack / GRAPH).write_bytes(b"changed")
                    elif binary == "orpheus_server" and fail_at == "server":
                        raise OSError("server failed")
                    else:
                        self.fail(f"unexpected invocation of {binary}")

                self.native.side_effect = native
                self.build(failure="SHA-256 mismatch" if fail_at == "hash" else f"{fail_at} failed")
                self.assert_not_published()

    def test_output_is_published_by_rename_only_after_verification(self):
        self.inputs()
        (self.source / "data/unlisted.json").write_text("{}", encoding="utf-8")
        events = []
        rename = Path.rename

        def native(binary, *args):
            self.assertFalse(self.dest.exists())
            if binary == "prepare_server_runtime":
                view = args[args.index("--source") + 1]
                self.assertFalse((view / "data/unlisted.json").exists())
                write_pack(args[args.index("--destination") + 1])
            else:
                self.assertEqual(binary, "orpheus_server")
                self.assertIn("--check-runtime-graphs", args)
            events.append(binary)

        def publish(pack, destination):
            self.assertEqual(pack.parent.parent, destination.parent)
            self.assertEqual(destination, self.dest)
            self.assertFalse(destination.exists())
            events.append("rename")
            return rename(pack, destination)

        self.native.side_effect = native
        with patch.object(Path, "rename", publish):
            self.build()
        self.generate.assert_called_once()
        self.assertEqual(events, ["prepare_server_runtime", "orpheus_server", "rename"])
        self.assertTrue((self.dest / GRAPH).is_file())
        self.assertEqual(list(self.dest.parent.glob(".orpheus-build-*")), [])


if __name__ == "__main__":
    unittest.main()
