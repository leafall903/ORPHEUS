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
from runtime_validation import GRAPH, PRACTICE_COLLISION, PRACTICE_MODE


def manifest(root):
    entries = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file() and path.name != "manifest.json"
    }
    (root / "manifest.json").write_text(json.dumps({
        "schema": "orpheus-runtime-copy-v1", "sha256": entries,
    }), encoding="utf-8")


def write_pack(folder, *, stock_models=True):
    target = folder / GRAPH
    target.parent.mkdir(parents=True, exist_ok=True)
    graph = "0x058000000000002A"
    teams = {str(team): {"body": graph, "controller": graph} for team in (0, 1)}
    body = {"target_tags": ["0x0D60000000000001"]}
    if stock_models:
        body["model_hardpoints"] = [{
            "skin": "0x0000000000000000",
            "reference_positions": {"0x0DC0000000000042": [0, 1, 0]},
            "reference_bones": [[1, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]]],
        }]
    bundle = {
        "version": 1, "graphs": [{"index": 42}],
        "mode_scripts": {PRACTICE_MODE: {"graph": graph, "teams": teams}},
        "mode_variables": {PRACTICE_MODE: {"10": True}},
        "mode_rulesets": [{"mode": PRACTICE_MODE, "rules": [{"id": 30, "value": 1}]}],
        "bodies": {f"0x0400{index:012X}": body for index in range(1, 33)},
    }
    for name in PRACTICE_COLLISION:
        cache = folder / name
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(b"synthetic collision test data")
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

    def test_missing_ray_cache_reenters_extraction_and_cannot_be_published(self):
        self.inputs()
        write_pack(self.source)
        (self.source / "collision/0688.rays.col").unlink()
        for name in (*runtime.GENERATED, "game_queues_174.json"):
            path = self.source / "data" / name
            if not path.exists():
                path.write_bytes(b"{}")
        manifest(self.source)
        def native(binary, *args):
            self.assertEqual(binary, "prepare_server_runtime")
            pack = args[args.index("--destination") + 1]
            write_pack(pack)
            (pack / "collision/0688.rays.col").unlink()
            manifest(pack)
        self.native.side_effect = native
        self.build(failure="Missing Practice Range collision cache: collision/0688.rays.col")
        self.generate.assert_called_once()
        self.assert_not_published()

    def test_check_command_requires_only_the_pack(self):
        write_pack(self.dest)
        with redirect_stdout(io.StringIO()):
            runtime.main(["check", str(self.dest)])
        self.native.assert_called_once_with("orpheus_server", "--runtime", self.dest, "--check-runtime-graphs")
        self.generate.assert_not_called()

    def test_existing_legacy_pack_reports_missing_models_without_being_overwritten(self):
        write_pack(self.dest, stock_models=False)
        before = (self.dest / GRAPH).read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            runtime.main(["check", str(self.dest)])
        self.assertIn("Missing stock model", output.getvalue())
        self.assertIn("0x0400000000000001", output.getvalue())
        self.assertIn("--inputs", output.getvalue())
        self.build(failure="Missing stock model")
        self.generate.assert_not_called()
        self.assertEqual((self.dest / GRAPH).read_bytes(), before)

    def test_build_requires_landed_entity_definitions_used_by_the_gameplay_graphs(self):
        write_pack(self.dest)
        with gzip.open(self.dest / GRAPH, "rt", encoding="utf-8") as stream:
            graph = json.load(stream)
        entity = "0x0400000000000101"
        graph["graphs"][0]["nodes"] = [{
            "class": "STUStatescriptStateWeaponVolley",
            "fields": {
                "m_55E8761C": {"m_147777B3": {"$": "STUConfigVarBool", "m_value": True}},
                "m_projectileEntity": {"m_entityDef": {"$": "STU_8556841E", "m_entityDef": entity}},
            },
        }]
        def save():
            (self.dest / GRAPH).write_bytes(gzip.compress(json.dumps(graph).encode()))
            manifest(self.dest)
        save()
        self.build(failure="Missing persistent projectile")
        self.native.assert_not_called()
        graph["persistent_projectiles"] = {entity: {"graphs": [], "graph_refs": [], "components": ["STUTargetTagComponent"]}}
        save()
        self.build()
        self.native.assert_called_once()

    def test_check_defaults_to_the_normal_install_directory(self):
        default_pack = self.root / "ORPHEUS/runtime-104319"
        write_pack(default_pack)
        with patch.object(runtime.os, "environ", {"LOCALAPPDATA": str(self.root)}), redirect_stdout(io.StringIO()):
            runtime.main(["check"])
        self.native.assert_called_once_with("orpheus_server", "--runtime", default_pack, "--check-runtime-graphs")
        self.generate.assert_not_called()

    def test_generator_failure_is_not_published(self):
        self.inputs()
        self.generate.side_effect = OSError("synthetic extractor failure")
        self.build(failure="synthetic extractor failure")
        self.assert_not_published()
        self.native.assert_not_called()

    def test_generated_stock_models_require_bones_on_every_body(self):
        contract = json.loads((Path(__file__).resolve().parents[2]
                               / "fixtures/behavior/owner-model-hardpoints-v1.json").read_bytes())
        model = contract["input"]["model_hardpoints"][0].copy()
        bones = [{"id": index, "model_reference_matrix": matrix}
                 for index, matrix in model.pop("reference_bones")]
        bodies = [f"0x0400{index:012X}" for index in range(1, 33)]
        export = {"build": 104319, "units": "metres", "pose": "model reference",
                  "matrix_convention": "row vector, row-major; root parent is -1",
                  "bodies": {body: {"skin": model["skin"], "bones": bones} for body in bodies}}
        (self.root / "stock-model-anchors.json").write_text(json.dumps({
            "build": 104319, "bodies": {body: [model] for body in bodies}
        }), encoding="utf-8")
        skeletons = self.root / "stock-skeletons.json"
        skeletons.write_text(json.dumps(export), encoding="utf-8")
        def fresh():
            return {"bodies": {body: {} for body in bodies}}

        self.assertEqual(runtime_install.import_stock_models(fresh(), self.root), (32, 32))
        last = bodies[-1]
        export["bodies"][last]["bones"] = []
        skeletons.write_text(json.dumps(export), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, last):
            runtime_install.import_stock_models(fresh(), self.root)
        del export["bodies"][last]
        skeletons.write_text(json.dumps(export), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, last):
            runtime_install.import_stock_models(fresh(), self.root)

    def test_failed_final_verification_is_not_published(self):
        self.inputs()
        for fail_at, error in [("packer", "packer failed"), ("hash", "SHA-256 mismatch"),
                               ("models", "Missing stock model"), ("server", "server failed")]:
            with self.subTest(fail_at=fail_at):
                def native(binary, *args):
                    self.assertFalse(self.dest.exists())
                    if binary == "prepare_server_runtime":
                        pack = args[args.index("--destination") + 1]
                        write_pack(pack, stock_models=fail_at != "models")
                        if fail_at == "packer":
                            raise OSError("packer failed")
                        if fail_at == "hash":
                            (pack / GRAPH).write_bytes(b"changed")
                    elif binary == "orpheus_server" and fail_at == "server":
                        raise OSError("server failed")
                    else:
                        self.fail(f"unexpected invocation of {binary}")

                self.native.side_effect = native
                self.build(failure=error)
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
