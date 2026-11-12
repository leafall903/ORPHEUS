"""Execute charged Flux aimed at real player bodies; retain missing target effects as failures."""

import argparse
import json
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
from ow174.game.content import heroes  # noqa: E402
from ow174.game.script import expr, graph  # noqa: E402


def scenario(*, enemy_sigma=False):
    run, player, enemy, _previous_health = SigmaCombatTests().arena(offset=0, distance=6)
    if enemy_sigma:
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[sigma.HERO])
        enemy.mover.position = position  # establish the same6m fixture before the first command
    fight = run.match.combat
    target = next(t for t in fight._targets() if t.owner is enemy)
    health = fight.vitals_of(target)
    before, maximum = health.total, health.maximum
    feet = enemy.mover.position[1]
    peak = feet
    caster_floor = caster_peak = player.mover.position[1]
    script = player.body_script
    cost = script.component.entity_var(338).value()
    script.set_ult_charge(cost)  # charge gating/cost is tested separately
    instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1E41)
    point_to_select = tuple(enemy.mover.position)
    trace = []
    damage_events = []
    previous_health = before
    for frame in range(450):
        yaw, pitch = aim(fight._eye(player), point_to_select)
        buttons = 0x20 if frame == 0 else (0x01 if frame == 80 else 0)
        run.step({player: (yaw, pitch, buttons)})
        peak = max(peak, enemy.mover.position[1])
        caster_peak = max(caster_peak, player.mover.position[1])
        if health.total != previous_health:
            damage_events.append({"frame": frame, "amount": previous_health - health.total,
                                  "airborne": enemy.mover.airborne})
            previous_health = health.total
        if frame in (0, 60, 80, 100, 150, 250, 449):
            point = instance.vars.get(2478)
            targets = instance.vars.get(375)
            effects = [child for child in enemy.body_script.component.instances.values()
                       if child.graph.index == sigma.FLUX_EFFECT]
            target_effects = [
                {"id": child.id, "source": child.source_entity(),
                 "active_nodes": [state.node.pos for state in child.states.values() if state.active]}
                for child in effects
            ]
            trace.append({"frame_since_cast": frame, "health": health.total,
                          "caster_height": player.mover.position[1] - caster_floor,
                          "caster_gravity": player.mover.gravity_scale,
                          "selection_position": repr(point.value()) if point is not None else None,
                          "tracked_targets": repr(targets.value()) if targets is not None else None,
                          "active_nodes": [state.node.pos for state in instance.states.values()
                                           if state.active],
                          "target_effects": target_effects,
                          "target_state_activations": instance.states[15].counter
                          if 15 in instance.states else 0})
    damage, lift = before - health.total, peak - feet
    landed = not enemy.mover.airborne and abs(enemy.mover.position[1] - feet) < 0.01
    retired = not any(child.graph.index == sigma.FLUX_EFFECT
                      for child in enemy.body_script.component.instances.values())
    caster_landed = not player.mover.airborne and abs(player.mover.position[1] - caster_floor) < 0.01
    passed = (caster_peak > caster_floor and caster_landed and lift > 0 and landed and retired
              and len(damage_events) == 2
              and [event["amount"] for event in damage_events] == [50, maximum * 0.5]
              and not damage_events[-1]["airborne"])
    return {"scenario": "aimed_flux_enemy_sigma" if enemy_sigma else "aimed_flux_enemy_soldier",
            "passed": passed, "initial_charge": cost,
            "target_initial_health": before, "target_maximum": maximum,
            "damage": damage, "maximum_lift": lift, "trace": trace,
            "caster_maximum_lift": caster_peak - caster_floor, "caster_landed": caster_landed,
            "damage_events": damage_events, "landed": landed, "retired": retired,
            "limits": "Reconstructed player target trajectory on flat ground."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("report must stay outside the source repository")
    child = graph.graph(0x1DBC)
    rows = [scenario(), scenario(enemy_sigma=True)]
    result = {"hero": "Sigma", "static_acceptance_complete": False, "source_path": str(ROOT),
              "scope": "Charged Q/primary selection, actual body graphs, flat floor, fake transport.",
              "authored_initial_damage": expr.source(child.node(12).field("m_modifyHealth")["m_amount"]),
              "authored_slam_expression": expr.source(child.node(14).field("m_modifyHealth")["m_amount"]),
              "results": rows,
              "remaining": ["Native placement/index/mask/tag/LOS fidelity beyond scoped Match admission",
                            "Native caster and target velocity, mover override semantics and wire policy",
                            "Range/LOS/team/immunity/interrupt/death and five-NPC matrix"]}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(row["scenario"], "PASS" if row["passed"] else "FAIL",
              f"damage={row['damage']} lift={row['maximum_lift']}")
    if any(not row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
