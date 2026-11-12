"""Execute Barrier protection/recall baselines; failed functionality stays failed.

Uses actual input/body graphs and authoritative damage with flat collision/fake
transport. Resource parameters and a Pet State being active are diagnostics,
never a substitute for an executed protection result. Reports stay external.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from test_game_combat import aim  # noqa: E402
from test_sigma_combat import SigmaCombatTests  # noqa: E402

from ow174.game import sigma  # noqa: E402
from ow174.game.commands import FIRE, SECONDARY  # noqa: E402
from ow174.game.content import heroes  # noqa: E402
from ow174.game.script import expr, graph  # noqa: E402


def arena(*, enemy_sigma=False):
    run, player, enemy, _ = SigmaCombatTests().arena(offset=0, distance=10)
    if enemy_sigma:
        run.match.switch_hero(enemy, heroes()[sigma.HERO])
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 10]
    target = next(t for t in run.match.combat._targets() if t.owner is player)
    vitals = run.match.combat.vitals_of(target)
    for frame in range(50):
        run.step({player: (0, 0, SECONDARY if frame < 20 else 0)})
    instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1B55)
    return run, player, enemy, vitals, instance


def diagnostic(instance):
    state = instance.states.get(4)
    pet = instance.vars.get(512) or instance.component.entity_var(512, create=False)
    pet = pet.value() if pet else None
    current = instance.vars.get(586)
    match = instance.component.world.match
    actor = match.combat.barriers.get(pet.id) if match is not None and isinstance(pet, expr.Entity) else None
    return {"pet_state_active": bool(state and state.active),
            "pet_entity_output": pet.id if isinstance(pet, expr.Entity) else None,
            "pet_health_query": current.value() if current else None,
            "actor": {"active": actor.active, "phase": actor.phase, "position": actor.position,
                      "health": actor.vitals.total, "child_request_seen": actor.child_request_seen,
                      "geometry": actor.geometry.name} if actor else None,
            "owner_exists_gate": repr(instance.evaluate(instance.graph.node(64).field("m_condition"))),
            "resource_data_only": {
                "barrier_maximum": instance.vars[587].value(),
                "pet_definition": "0x0400000000002D25", "pet_script": "1A2A",
                "movement_speed": instance.evaluate(graph.graph(0x1A2A).node(22).field("m_speed"))},
            "limits": "These parameters/States are not passed combat acceptance."}


def attacked(kind):
    run, player, enemy, vitals, instance = arena(enemy_sigma=kind == "hyperspheres")
    x, y, z = player.mover.position
    yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 0.8, z))
    before = vitals.total
    button = SECONDARY if kind == "helix" else FIRE
    run.step({enemy: (yaw, pitch, button)})
    for frame in range(70):
        held = FIRE if kind == "rifle" and frame < 19 else 0
        run.step({enemy: (yaw, pitch, held)})
    volleys = [(f"{i.graph.index:04X}", state.index, state.counter)
               for i in enemy.body_script.component.instances.values()
               for state in i.states.values() if state.node.cls == "STUStatescriptStateWeaponVolley"
               and state.counter > 0]
    damage = before - vitals.total
    return {"scenario": f"deployed_barrier_blocks_{kind}", "passed": bool(volleys) and damage == 0,
            "damage_to_owner": damage, "executed_attacker_volleys": volleys,
            "diagnostic": diagnostic(instance)}


def recalled():
    run, player, enemy, vitals, instance = arena()
    x, y, z = player.mover.position
    yaw, pitch = aim(run.match.combat._eye(enemy), (x, y + 0.8, z))
    before = vitals.total
    run.step({enemy: (yaw, pitch, FIRE)})
    run.step({enemy: (yaw, pitch, 0)})
    protected = vitals.total == before
    run.step({player: (0, 0, SECONDARY)})
    for _ in range(40):
        run.step({enemy: (yaw, pitch, 0)})
    after_recall = vitals.total
    run.step({enemy: (yaw, pitch, FIRE)})
    run.step({enemy: (yaw, pitch, 0)})
    exposed = vitals.total < after_recall
    return {"scenario": "recall_removes_previously_proven_protection", "passed": protected and exposed,
            "protection_proven_before_recall": protected, "damage_after_recall": after_recall - vitals.total,
            "diagnostic": diagnostic(instance),
            "limits": "A missing barrier cannot pass recall merely because damage occurs."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path,
                        help="manifest-verified external C015 model with integrity sidecar")
    args = parser.parse_args()
    if args.model is not None:
        os.environ["ORPHEUS_SIGMA_BARRIER_MODEL"] = str(args.model.resolve())
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("report must stay outside the source repository")
    rows = [attacked(kind) for kind in ("rifle", "helix", "hyperspheres")]
    rows.append(recalled())
    result = {"hero": "Sigma", "source_path": str(ROOT), "static_acceptance_complete": False,
              "scope": "Actual commands/body graphs; flat collision, fake transport, permissive VM.",
              "model_input": os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"),
              "implementation": ("Reconstructed authoritative Pet actor using verified external C015 mesh; "
                                 "no native Pet/1A2A service or client replication claim."),
              "results": rows,
              "remaining": ["Native Pet/component43/1A2A services and receiver filter semantics",
                "Native Barrier health/ruleset services, full range/attack eligibility and replication",
                "Full cross-hero/player/five-NPC matrix and original-client acceptance"]}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(row["scenario"], "PASS" if row["passed"] else "FAIL")
    if any(not row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
