"""Execute Sigma damage scenarios against the five external authored NPC bodies.

This uses a flat collision fixture and fake transport, explicit base pools and
permissive body graphs. It does not certify AI, difficulty scaling or client
acceptance. Shared external runtime inputs are read only; reports stay outside
the source repository. Missing/failed behavior is reported, never counted as a
complete static hero acceptance.
"""

import argparse
import hashlib
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from extract_game_movers import mover_of  # noqa: E402
from test_game_combat import KANEZAKA_DM, FakeClient, Run, aim  # noqa: E402
from test_sigma_combat import SigmaCombatTests  # noqa: E402

from ow174.game import mover, npc  # noqa: E402
from ow174.game.commands import FIRE  # noqa: E402
from ow174.game.content import heroes  # noqa: E402
from ow174.game.script import graph  # noqa: E402

SIGMA = 0x02E000000000023B


class AuditClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.deaths = []

    def queue_bot_death(self, entity, event):
        self.deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        pass  # transport delivery is outside this fixture's scope


def clear_graph_caches():
    for function in (graph._data, graph._records, graph.graph):
        function.cache_clear()


def arena(definition, scenario):
    run = Run(KANEZAKA_DM)
    run.match.bots.clear()
    run.match._pending_bot_scripts.clear()
    player, = run.players
    player.client = AuditClient(player)
    run.match.switch_hero(player, heroes()[SIGMA])
    x, y, z = player.mover.position
    distance = 1 if scenario == "melee" else (22 if scenario in ("airburst", "miss") else 6)
    offset = 1 if scenario == "airburst" else (4 if scenario == "miss" else 0)
    if scenario == "ally":
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
    bot = npc.spawn(run.match, definition, (x + offset, y, z + distance),
                    team=player.team if scenario == "ally" else 1 - player.team, yaw=-32768)
    if bot.body_script is None:
        raise AssertionError("actual NPC body script did not load")
    if scenario == "wall":
        SigmaCombatTests().wall(run, sideways=False, offset=5)
    return run, player, bot


def scenario(definition, name):
    run, player, bot = arena(definition, name)
    fight = run.match.combat
    vitals = fight.vitals[bot.entity]
    before = vitals.total
    buttons = {"melee": 0x800, "accretion": 0x10}.get(name, FIRE)
    yaw, pitch = 0, 0
    if name in ("primary", "melee", "accretion", "ally", "wall"):
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(player), (x, y + mover.mover_data(bot.body).stand_height / 2, z))
    run.step({player: (yaw, pitch, buttons)})
    for _ in range(110):
        run.step({player: (yaw, pitch, 0)})
    expected = {"primary": 110, "airburst": 60, "melee": 30,
                "accretion": 70, "miss": 0, "ally": 0, "wall": 0}[name]
    lost = before - vitals.total
    return {"scenario": name, "expected_base_pool_damage": expected, "damage": lost,
            "passed": abs(lost - expected) < 1e-5, "body_script_loaded": bot.body_script is not None,
            "body_vm_fault": str(bot.body_script.component.fault) if bot.body_script else "script absent",
            "base_pools_before": before, "remaining": vitals.total}


def incoming(definition):
    """Explicit logical20 input into the actual NPC graph; no AI substitution."""
    run, player, bot = arena(definition, "incoming")
    fight, script = run.match.combat, bot.body_script
    target = next(t for t in fight._targets() if t.owner is player)
    vitals = fight.vitals_of(target)
    before = vitals.total
    bot.firing = True
    for frame in range(100):
        # This fixture supplies manual input to the same graph lifecycle that
        # Combat._bot_weapons consumes. NPC target acquisition is not restored.
        script.component.run_frame(run.frame + 1, [(20, True)] if frame == 0 else [])
        script.frame = run.frame + 1
        script._report_shots()
        run.step({})
    return {"scenario": "incoming_manual_logical20", "damage": before - vitals.total,
            "passed": vitals.total < before, "player_dead": vitals.dead,
            "body_vm_fault": str(script.component.fault),
            "limits": "Manual body input only; AI/target selection/specials remain open."}


def corpse(definition):
    """Kill each real base-pool body with real primary fire, then pass respawn time."""
    run, player, bot = arena(definition, "primary")
    fight = run.match.combat
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + mover.mover_data(bot.body).stand_height / 2, z))
    for _ in range(6000):
        run.step({player: (yaw, pitch, FIRE)})
        if bot.dead:
            break
    terminal_entity = bot.entity
    killed = bot.dead
    for _ in range(220):
        run.step({player: (yaw, pitch, 0)})
    vitals = fight.vitals.get(terminal_entity)
    kept = bot in run.match.bots and bot.entity == terminal_entity
    scheduled = any(who is bot for _, who in fight.respawns)
    excluded = bot not in [target.owner for target in fight._enemies(player)]
    script = bot.body_script
    passed = (killed and kept and not scheduled and excluded and vitals is not None and vitals.dead
              and script is not None and script.component.owner_dead and script.component.fault is None)
    return {"scenario": "primary_kill_retained_corpse", "passed": passed, "killed": killed,
            "retained_past_respawn_delay": kept, "respawn_scheduled": scheduled,
            "dead_target_excluded": excluded, "death_callbacks": len(player.client.deaths),
            "body_script_survives": script is not None,
            "body_vm_fault": str(script.component.fault) if script else "script absent",
            "limits": "Authoritative lifetime only; rendered corpse still needs client acceptance."}


def run_audit(catalog, graphs, body_xml):
    definitions = npc.load_catalog(catalog)
    table = dict(mover._movers())
    for definition in definitions.values():
        path = body_xml / f"{definition.body & 0xFFFFFFFFFFFF:012X}.003.xml"
        record = mover_of(path.read_text(encoding="utf-8"))
        if record is None:
            raise ValueError(f"NPC mover component missing: {path}")
        table[definition.body] = mover.MoverData(**record)
    rows = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(graph, "GRAPHS_PATH", graphs))
        stack.enter_context(patch.object(mover, "_movers", return_value=table))
        clear_graph_caches()
        try:
            for definition in definitions.values():
                results = [scenario(definition, name) for name in
                           ("primary", "airburst", "melee", "accretion", "miss", "ally", "wall")]
                try:
                    results.append(incoming(definition))
                except Exception as error:  # noqa: BLE001 -- retain per-NPC failure evidence in the audit
                    results.append({"scenario": "incoming_manual_logical20", "passed": False,
                                    "error": f"{type(error).__name__}: {error}"})
                try:
                    results.append(corpse(definition))
                except Exception as error:  # noqa: BLE001 -- preserve failed lifecycle evidence
                    results.append({"scenario": "primary_kill_retained_corpse", "passed": False,
                                    "error": f"{type(error).__name__}: {error}"})
                rows.append({"name": definition.name, "hero": f"0x{definition.hero:016X}",
                             "body": f"0x{definition.body:016X}", "results": results})
        finally:
            clear_graph_caches()
    return {"hero": "Sigma", "static_acceptance_complete": False,
            "scope": "Actual NPC bodies/base pools/movers, flat collision, fake transport, permissive VM.",
            "source_path": str(ROOT), "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
            "graphs_sha256": hashlib.sha256(graphs.read_bytes()).hexdigest(), "npcs": rows,
            "remaining": ["All Barrier/Flux target effects and Grasp temporary shields",
                          "Full attack/status/cooldown/cancel and cross-hero matrix",
                          "NPC special abilities, target acquisition and native movement",
                          "Original-client corpse and combat acceptance"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--graphs", type=Path, required=True)
    parser.add_argument("--body-xml", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("audit report must stay outside the source repository")
    result = run_audit(args.catalog, args.graphs, args.body_xml)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for row in result["npcs"]:
        passed = sum(item["passed"] for item in row["results"])
        print(f"{row['name']}: {passed}/{len(row['results'])} limited scenarios passed")
    print("Full static acceptance remains OPEN:", args.output)
    if any(not scenario["passed"] for row in result["npcs"] for scenario in row["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
