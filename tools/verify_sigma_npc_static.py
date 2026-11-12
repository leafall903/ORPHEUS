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
import math
import os
import struct
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from extract_game_movers import mover_of  # noqa: E402
from test_game_combat import KANEZAKA_DM, FakeClient, Run, aim  # noqa: E402
from test_sigma_combat import SigmaCombatTests  # noqa: E402
from test_sigma_flux_placement import SigmaFluxPlacementTests  # noqa: E402

from ow174.game import absorption, barriers, combat, defenses, enforcer, heavy_assault, mover, npc, npc_control, sigma_barrier, sigma_flux
from ow174.game import sniper_reposition as sniper  # noqa: E402
from ow174.game.collision import World  # noqa: E402
from ow174.game.commands import FIRE  # noqa: E402
from ow174.game.content import heroes  # noqa: E402
from ow174.game.script import expr, graph, nodes  # noqa: E402

SIGMA = 0x02E000000000023B


class AuditClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.deaths = []

    def queue_bot_death(self, entity, event):
        self.deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        pass  # transport delivery is outside this fixture's scope

    def bot_stream(self, entity):
        return None  # no NPC transport streams are mounted in this authoritative-only fixture


def _grasp_pools(vitals):
    """Read the actual tagged pools; never construct a compatibility health ledger."""
    return tuple(pool for pool in vitals.extra.values()
                 if pool.identifier == absorption.SHIELD_TAG and pool.kind == combat.SHIELDS_POOL)


def _airborne_mover(bot):
    """Original effect-mover lifetime ended on landing; the sole mover may persist."""
    actual = bot.control_mover or bot.impulse_mover
    return actual if actual is not None and actual.airborne else None


def _particle_pet(fight, player):
    candidates = [actor for actor in fight.barriers.scripted.values()
                  if actor.component.world.hero == barriers.ZARYA and actor.vitals is not None
                  and (actor.recipient_body or actor.parent_body) == player.body
                  and barriers._live(fight, actor)]
    return min(candidates, key=lambda actor: barriers._particle_priority(fight, actor)) if candidates else None


def _sigma_barrier_separates(fight, shooter, source, target):
    a, b = target.segment()
    nearest = (a[0], max(a[1], min(b[1], source[1])), a[2])
    delta = tuple(value - start for value, start in zip(nearest, source))
    length = math.sqrt(sum(value * value for value in delta))
    reach = length - target.radius
    return (reach > combat.EPSILON and sigma_barrier.barrier_intersection(
        fight, shooter, source, tuple(value / length for value in delta), reach) is not None)


def clear_graph_caches():
    for function in (graph._data, graph._records, graph.graph):
        function.cache_clear()


@contextmanager
def player_damage_sources(fight, player, bot, vitals):
    """Track effective damage at contact, before later shield recovery."""
    dealt = {"npc": 0.0, "self": 0.0}
    original = fight._damage

    def observe(shooter, target, *args, **kwargs):
        before = vitals.total
        result = original(shooter, target, *args, **kwargs)
        if target.owner is player and (shooter is bot or shooter is player):
            dealt["npc" if shooter is bot else "self"] += before - vitals.total
        return result

    with patch.object(fight, "_damage", side_effect=observe):
        yield dealt


def arena(definition, scenario, *, names=("A",)):
    run = Run(KANEZAKA_DM, names=names)
    run.match.bots.clear()
    run.match._pending_bot_scripts.clear()
    player = run.players[0]
    for participant in run.players:
        participant.client = AuditClient(participant)
    run.match.switch_hero(player, heroes()[SIGMA])
    x, y, z = player.mover.position
    distance = 1 if scenario == "melee" else (22 if scenario in ("airburst", "miss") else 6)
    if scenario == "heavy_melee":
        distance = 2.15
    if scenario == "heavy_charge":
        distance = 10
    if scenario == "incoming" and definition.hero == 0x02E00000000001BB:
        distance = 2  # explicit contact fixture; reconstructed manual Assassin reach is3m
    offset = 1 if scenario == "airburst" else (4 if scenario == "miss" else 0)
    if scenario == "ally":
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
    bot = npc.spawn(run.match, definition, (x + offset, y, z + distance),
                    team=player.team if scenario == "ally" else 1 - player.team, yaw=-32768)
    if bot.body_script is None:
        raise AssertionError("actual NPC body script did not load")
    if scenario == "heavy_melee" and bot.hero == sniper.SNIPER:
        bot.body_script.component.rng.seed(50)  # Keep the first directional sample close to +Z.
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
    script = player.body_script
    script.ult_seconds = 0
    script.set_ult_charge(0)
    run.step({player: (yaw, pitch, buttons)})
    stun_ticks = 0
    origin = tuple(bot.position)
    lift = 0.0
    for _ in range(110):
        run.step({player: (yaw, pitch, 0)})
        stun_ticks = max(stun_ticks, fight.stuns.get(bot.entity, 0) - fight.tick)
        lift = max(lift, bot.position[1] - origin[1])
    expected = {"primary": 110, "airburst": 60, "melee": 30,
                "accretion": 70, "miss": 0, "ally": 0, "wall": 0}[name]
    lost = before - vitals.total
    charge = script.component.entity_var(342).value()
    expected_stun = 50 if name == "accretion" else 0
    displacement = math.hypot(bot.position[0] - origin[0], bot.position[2] - origin[2])
    impulse_passed = name != "accretion" or (
        lift > 0.05 and displacement > 0.5 and _airborne_mover(bot) is None
    )
    lateral_delta = bot.position[0] - origin[0]
    if name == "airburst":
        impulse_passed = lift > 0.02 and lateral_delta < -0.05 and _airborne_mover(bot) is None
    return {"scenario": name, "expected_base_pool_damage": expected, "damage": lost,
            "stun_ticks": stun_ticks, "expected_stun_ticks": expected_stun,
            "maximum_lift": lift, "horizontal_displacement": displacement,
            "lateral_displacement": lateral_delta,
            "ultimate_charge": charge,
            "passed": abs(lost - expected) < 1e-5 and stun_ticks == expected_stun
            and impulse_passed and charge == expected,
            "body_script_loaded": bot.body_script is not None,
            "body_vm_fault": str(bot.body_script.component.fault) if bot.body_script else "script absent",
            "base_pools_before": before, "remaining": vitals.total}


def mixed_airburst(definition, mode):
    """Resolve each authored airburst against an enemy player and one NPC."""
    run, player, bot = arena(definition, "airburst", names=("A", "B"))
    enemy = run.players[1]
    x, y, z = player.mover.position
    enemy.mover.position = [x - 1, y, z + 22]
    enemy.mover.body = None
    enemy.team = 1 - player.team
    radius = mover.mover_data(bot.body).stand_radius
    bot.position = (x + radius + (3.1 if mode == "outside" else 2.0), y, z + 22)
    if mode == "ally":
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        bot.team = player.team
    if mode == "covered":
        SigmaCombatTests().wall(run, sideways=True, offset=0.5)

    fight = run.match.combat
    enemy_target = next(target for target in fight._targets() if target.owner is enemy)
    enemy_health, victim_health = fight.vitals_of(enemy_target), fight.vitals[bot.entity]
    enemy_before = enemy_health.total
    script = player.body_script
    script.ult_seconds = 0
    script.set_ult_charge(0)
    original_explode = fight._explode
    blasts = []

    def observe(rocket, point, **kwargs):
        target = next(target for target in fight._targets() if target.owner is bot)
        low, high = target.segment()
        nearest = (low[0], min(high[1], max(low[1], point[1])), low[2])
        distance = max(0, math.dist(nearest, point) - target.radius)
        before = victim_health.total
        original_explode(rocket, point, **kwargs)
        expected = 30 * (1 - 0.7 * (distance - 1) / 1.85) if mode == "falloff" else 0
        blasts.append({
            "definition": f"{rocket.definition:016X}", "point": point, "npc_distance": distance,
            "damage": before - victim_health.total, "expected_npc_damage": expected,
            "impact": any(kwargs.values()), "remaining_life": rocket.left,
        })

    with patch.object(fight, "_explode", side_effect=observe):
        for frame in range(80):
            run.step({player: (0, 0, FIRE if frame == 0 else 0)})
    dealt = sum(blast["damage"] for blast in blasts)
    enemy_damage = enemy_before - enemy_health.total
    charge = script.component.entity_var(342).value()
    hit_count = len(player.client.messages(combat.HIT))
    inside = mode != "outside"
    return {
        "scenario": f"mixed_hypersphere_airburst_{mode}", "blasts": blasts,
        "npc_damage": dealt, "enemy_player_damage": enemy_damage,
        "ultimate_charge": charge, "hit_count": hit_count,
        "passed": len(blasts) == 2 and {b["definition"] for b in blasts} == {"0400000000002D35", "04000000000032B4"}
        and all(not b["impact"] and b["remaining_life"] <= 0
                and (1 < b["npc_distance"] < 2.85 if inside else b["npc_distance"] > 2.85)
                and math.isclose(b["damage"], b["expected_npc_damage"], abs_tol=1e-4) for b in blasts)
        and enemy_damage == 60 and math.isclose(charge, enemy_damage + dealt, abs_tol=1e-4)
        and hit_count == (4 if mode == "falloff" else 2) and not fight.rockets,
    }


def lethal_npc_return(definition):
    """Use actual weapons across a lethal return attack and fresh player body."""
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    position = tuple(player.mover.position)
    bot_script = bot.body_script
    bot_health = fight.vitals[bot.entity]
    bot_before = bot_health.total
    old_body = player.body
    old_script = player.body_script
    old_health = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
    npc_height = mover.mover_data(bot.body).stand_height

    def view():
        x, y, z = bot.position
        return aim(fight._eye(player), (x, y + npc_height * 0.5, z))

    yaw, pitch = view()
    for frame in range(65):
        run.step({player: (yaw, pitch, FIRE if frame == 0 else 0)})
    outgoing = bot_before - bot_health.total
    assert outgoing == 110 and not bot.dead and not fight.rockets, outgoing

    # Use a common lethal threshold for all five weapons.
    old_health.now[combat.HEALTH_POOL] = 4.0
    old_health.now[combat.SHIELDS_POOL] = 0.0
    damage = fight._damage
    hits = []

    def observe(source, target, amount, *args, **kwargs):
        vitals = fight.vitals_of(target)
        before = vitals.total
        result = damage(source, target, amount, *args, **kwargs)
        if source is bot and target.owner is player:
            hits.append({"frame": run.frame, "body": target.entity, "amount": amount, "dealt": before - vitals.total})
        return result

    yaw, pitch = view()
    run.step({player: (yaw, pitch, 0)})
    assert npc_control.start(run.match, player) == bot.entity
    primary = npc_control.button(bot)
    with patch.object(fight, "_damage", side_effect=observe):
        for _ in range(300):
            run.step({})
            if old_health.dead:
                break
        assert old_health.dead and old_health.killer == bot.entity and old_health.total == 0
        deadline, = (when for when, owner in fight.respawns if owner is player)
        notices = player.client.messages(combat.DEATH_NOTICE)
        assert notices == [{"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": bot.entity}}]
        assert old_script.component.owner_dead
        hit_count_at_death = len(hits)
        death_frame = run.frame
        for _ in range(12):
            run.step({})
            assert not bot.attack_input and primary not in bot_script.component.held
            assert player.body == old_body and old_body not in fight.pins.values()
        assert len(hits) == hit_count_at_death
        npc_control.stop(run.match)
        assert bot.attack_target is None and not bot.firing and not fight.rockets

        # Let the Match respawn the player before restoring the encounter placement.
        while player.body == old_body and run.frame < math.ceil(deadline / combat.TICK) + 2:
            run.step({})
        assert player.body != old_body and fight.now >= deadline and old_body not in fight.vitals
        assert player.body_script is not old_script and not player.body_script.component.owner_dead
        respawn_frame = run.frame
        player.mover.position = list(position)
        player.mover.body = None
        fresh = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        assert fresh.total == 400 and not fresh.dead
        for _ in range(12):
            run.step({})
            assert not bot.attack_input and primary not in bot_script.component.held
        assert fresh.total == 400 and len(hits) == hit_count_at_death

        yaw, pitch = view()
        run.step({player: (yaw, pitch, 0)})
        assert npc_control.start(run.match, player) == bot.entity
        for _ in range(300):
            run.step({})
            if fresh.total < 400:
                break
        npc_control.stop(run.match)

    assert bot.body_script is bot_script and bot_script.component.fault is None
    assert all(hit["body"] == old_body for hit in hits[:hit_count_at_death])
    assert hits[hit_count_at_death:] and all(hit["body"] == player.body for hit in hits[hit_count_at_death:])
    assert sum(hit["dealt"] for hit in hits[:hit_count_at_death]) == 4
    assert 0 < fresh.total < 400 and not fresh.dead
    return {"scenario": "npc_lethal_return_and_fresh_sigma_body", "passed": True,
            "sigma_pair_damage": outgoing, "npc_primary_button": primary, "npc": bot.entity,
            "old_body": old_body, "fresh_body": player.body, "death_frame": death_frame,
            "respawn_frame": respawn_frame, "respawn_deadline": deadline,
            "death_notice": notices[0], "hits": hits, "fresh_damage": 400 - fresh.total}


def npc_primary_gate_recovery(definition, mode):
    """Keep the same manual engagement through a temporary attack gate."""
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    component = bot.body_script.component
    primary = npc_control.button(bot)
    player_target = next(target for target in fight._targets() if target.owner is player)
    vitals = fight.vitals_of(player_target)
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + mover.mover_data(bot.body).stand_height * 0.5, z))
    position = list(player.mover.position)
    original_world, original_map, original_team = run.match.collision_world, run.match.game_map, bot.team
    shoot, damage = fight._shoot, fight._damage
    launches, hits = [], []

    def observe_shot(shooter, command, state, *args, **kwargs):
        if shooter is bot:
            launches.append({"frame": run.frame, "graph": f"{state.instance.graph.index:04X}"})
        return shoot(shooter, command, state, *args, **kwargs)

    def observe_damage(source, target, amount, *args, **kwargs):
        before = vitals.total
        result = damage(source, target, amount, *args, **kwargs)
        if source is bot and target.owner is player:
            hits.append({"frame": run.frame, "damage": before - vitals.total})
        return result

    def reach_hit(old_hits):
        for _ in range(300):
            run.step({player: (yaw, pitch, 0)})
            if len(hits) > old_hits and not any(rocket.shooter is bot for rocket in fight.rockets):
                return
        raise AssertionError("NPC attack did not reach Sigma and settle")

    run.step({player: (yaw, pitch, 0)})
    assert npc_control.start(run.match, player) == bot.entity
    with (patch.object(fight, "_shoot", side_effect=observe_shot),
          patch.object(fight, "_damage", side_effect=observe_damage)):
        reach_hit(0)
        initial_hits, initial_launches = len(hits), len(launches)
        first_damage = sum(hit["damage"] for hit in hits)
        assert first_damage > 0 and not vitals.dead
        assert bot.attack_input and primary in component.held
        assert bot.attack_target == player.entity and bot.firing

        if mode == "cover":
            SigmaCombatTests().wall(run, sideways=False, offset=(bot.position[2] - position[2]) / 2)
        elif mode == "range":
            retreat = 3.0 if definition.name == "Assassin" else npc_control.AIM_RANGE + 1.0
            player.mover.position = [position[0], position[1], position[2] - retreat]
            player.mover.body = None
        elif mode == "ally":
            run.match.game_map = replace(original_map, free_for_all=False)
            bot.team = player.team

        blocked_frame = run.frame + 1
        for _ in range(160):
            run.step({player: (yaw, pitch, 0)})
            assert not bot.attack_input and primary not in component.held
            assert bot.attack_target == player.entity and bot.firing
            assert bot.entity not in fight.pins
        assert (len(hits), len(launches)) == (initial_hits, initial_launches)

        if mode == "cover":
            run.match.collision_world = original_world
            player.mover.use(world=original_world)
        elif mode == "range":
            player.mover.position = position
            player.mover.body = None
        else:
            run.match.game_map, bot.team = original_map, original_team
        restored_frame = run.frame + 1
        reach_hit(initial_hits)
        npc_control.stop(run.match)

    resumed_damage = sum(hit["damage"] for hit in hits[initial_hits:])
    assert resumed_damage > 0 and not vitals.dead and component.fault is None
    assert all(hit["frame"] >= restored_frame for hit in hits[initial_hits:])
    return {
        "scenario": "npc_primary_gate_release_and_recovery", "mode": mode, "passed": True,
        "primary_button": primary, "blocked_frame": blocked_frame, "restored_frame": restored_frame,
        "first_damage": first_damage, "resumed_damage": resumed_damage, "hits": hits, "launches": launches,
        "manual_target_preserved": True,
    }


def incoming(definition, *, grasp=False, barrier=False):
    """Public manual engagement drives the body's authored slot0; no AI substitution."""
    run, player, bot = arena(definition, "incoming")
    fight, script = run.match.combat, bot.body_script
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + mover.mover_data(bot.body).stand_height / 2, z))
    shield = None
    if barrier:
        for frame in range(40):
            run.step({player: (yaw, 0, 0x02 if frame < 4 else 0)})
        shield, = fight.sigma_barriers.values()
    run.step({player: (yaw, pitch, 0)})  # establish the crosshair before public engagement selection
    target = next(t for t in fight._targets() if t.owner is player)
    if shield is not None:
        source = (x, y + mover.mover_data(bot.body).stand_height / 2, z)
        if not shield.active or not _sigma_barrier_separates(fight, bot, source, target):
            raise AssertionError("active Experimental Barrier must separate NPC and Sigma")
    vitals = fight.vitals_of(target)
    before = vitals.total
    precharge = 25 if grasp and definition.body == 0x0400000000001E7D else 0
    if grasp and not precharge:
        run.step({player: (yaw, pitch, 0x08)})
    grasp_instance = next((i for i in player.body_script.component.instances.values()
                           if i.graph.index == absorption.GRASP), None)
    grasp_started = bool(grasp_instance and grasp_instance.states.get(1)
                         and grasp_instance.states[1].active)
    damage_events = []
    volleys = []
    original_damage = fight._damage
    original_shoot = fight._shoot

    def record_shot(shooter, command, state, number, time, **kwargs):
        direct = combat.hit_def(state, state.node.field("m_6396149F"))
        before_launch = len(fight.rockets)
        original_shoot(shooter, command, state, number, time, **kwargs)
        volleys.append({"graph": f"{state.instance.graph.index:04X}", "state": state.index,
                        "direct": direct.amount if direct else None, "frame": run.frame,
                        "projectiles": len(fight.rockets) - before_launch})

    def record_damage(shooter, target, amount, *args, **kwargs):
        pool = _grasp_pools(vitals)
        before_temporary = sum(entry.now for entry in pool) if pool else 0
        before_total = vitals.total
        active = bool(grasp_instance and grasp_instance.states.get(1) and grasp_instance.states[1].active)
        result = original_damage(shooter, target, amount, *args, **kwargs)
        if target.owner is player:
            pool = _grasp_pools(vitals)
            damage_events.append({"frame": run.frame, "amount": amount, "dealt": before_total - vitals.total,
                                  "grasp_active": active, "temporary_before": before_temporary,
                                  "temporary_after": sum(entry.now for entry in pool) if pool else 0,
                                  "contact_control": fight.pins.get(bot.entity) == player.body})
        return result

    npc_control.start(run.match, player)
    primary_button = npc_control.button(bot)
    with (patch.object(fight, "_damage", side_effect=record_damage),
          patch.object(fight, "_shoot", side_effect=record_shot)):
        # The observed Sniper first shot is2s after input. Start Grasp after
        # 25 real precharge frames so the projectile enters its active window.
        for _ in range(precharge):
            run.step({})
        if precharge:
            run.step({player: (yaw, pitch, 0x08)})
        for frame in range(160 if grasp else 400):
            # Preserve the public manual policy and ordinary Combat/VM clocks.
            # Let the first precharged Sniper projectile resolve before stop-all.
            release = (120 if precharge else 100) if grasp else 300
            if frame == release:
                npc_control.stop(run.match)
            run.step({})
            settled = definition.hero != enforcer.HERO or not any(r.shooter is bot for r in fight.rockets)
            if barrier and settled and (shield.vitals.total < shield.vitals.maximum or damage_events):
                npc_control.stop(run.match)
                break
    full_volley = definition.hero != enforcer.HERO or (bool(volleys) and all(
        row["graph"] == "13EB" and row["projectiles"] == 25 and row["direct"] == 2 for row in volleys
    ))
    if barrier:
        dealt = sum(event["dealt"] for event in damage_events)
        shield_damage = shield.vitals.maximum - shield.vitals.total
        assassin = definition.hero == 0x02E00000000001BB
        controlled = any(event["contact_control"] for event in damage_events)
        released = bot.entity not in fight.pins
        survived = bot.body_script is script and script.component.fault is None
        return {"scenario": "experimental_barrier_against_actual_npc_primary",
                "passed": survived and full_volley and (
                    (dealt > 0 and controlled and released and shield_damage == 0) if assassin
                    else (bool(volleys) and shield_damage > 0 and dealt == 0)
                ) and (definition.hero != enforcer.HERO or shield_damage == 50),
                "damage": dealt, "barrier_damage": shield_damage, "contact_control": controlled,
                "control_released_after_stop": released, "executed_volleys": volleys,
                "body_script_survives": survived}
    if grasp:
        absorbed = grasp_instance.vars[21312].value()
        pool = _grasp_pools(vitals)
        bonus = sum(entry.now for entry in pool) if pool else 0
        base_damage = before - sum(vitals.now.values())
        temporary_spent = sum(event["temporary_before"] - event["temporary_after"] for event in damage_events)
        during_active = sum(event["dealt"] for event in damage_events if event["grasp_active"])
        converted = min(400, absorbed * 0.6)
        expected = max(0, converted - temporary_spent)
        healthy_script = bot.body_script is script and script.component.fault is None
        if definition.hero == 0x02E00000000001BB:
            interrupted = bool(grasp_instance and not grasp_instance.states[1].active)
            controlled = any(event["contact_control"] for event in damage_events)
            released = bot.entity not in fight.pins
            return {"scenario": "grasp_against_actual_assassin_contact", "absorbed": absorbed,
                    "base_pool_damage": base_damage, "temporary_shields": bonus,
                    "grasp_started": grasp_started, "grasp_interrupted": interrupted,
                    "contact_control_observed": controlled, "control_released_after_stop": released,
                    "damage_events": damage_events, "authored_primary_button": primary_button,
                    "body_script_survives": healthy_script,
                    "passed": (healthy_script and grasp_started and interrupted and controlled and released
                               and base_damage > 0 and absorbed == 0 and bonus == 0),
                    "limits": "Actual1157 graph with reconstructed manual3m contact/control/health clock; "
                              "unabsorbable contact, not a projectile absorption claim."}
        return {"scenario": "grasp_actual_npc_weapon_and_shield_conversion", "absorbed": absorbed,
                "base_pool_damage": base_damage, "temporary_shields": bonus,
                "converted_shields": converted, "expected_temporary_shields": expected,
                "damage_events": damage_events, "damage_during_grasp": during_active,
                "executed_volleys": volleys, "body_script_survives": bot.body_script is not None,
                "npc_precharge_frames": precharge,
                "authored_primary_button": primary_button,
                "passed": (healthy_script and full_volley and absorbed > 0 and abs(during_active) < 1e-5
                           and (definition.hero != enforcer.HERO or absorbed == 50)
                           and abs(bonus - expected) < 1e-5),
                "limits": "Manual NPC input; no emitted attack cannot establish absorption."}
    dealt = sum(event["dealt"] for event in damage_events)
    return {"scenario": "incoming_manual_authored_primary", "damage": dealt,
            "final_pool_deficit": before - vitals.total,
            "passed": (bot.body_script is script and script.component.fault is None
                       and full_volley and dealt > 0),
            "player_dead": vitals.dead,
            "executed_volleys": volleys, "body_script_survives": bot.body_script is not None,
            "authored_primary_button": primary_button,
            "body_vm_fault": str(script.component.fault),
            "limits": "Manual body input only; AI/target selection/specials remain open."}


def npc_attack_recovers_after_accretion(definition, *, charge_after=False):
    run, player, bot = arena(definition, "incoming")
    if charge_after:
        player.body_script.component.set(961, True)
    fight = run.match.combat
    x, y, z = bot.position
    if bot.hero == heavy_assault.HERO and not charge_after:
        z -= 1
        bot.position = (x, y, z)
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    run.step({player: (yaw, pitch, 0x10)})
    for _ in range(30):
        run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    target = next(t for t in fight._targets() if t.owner is player)
    vitals = fight.vitals_of(target)
    npc_vitals = fight.vitals[bot.entity]
    before, npc_before = vitals.total, npc_vitals.total
    starts, launches, stunned = [], [], []
    shoot = fight._shoot
    charge = heavy_assault.ability(bot.body_script.component, heavy_assault.CHARGE_GRAPH,
                                  heavy_assault.CHARGE_ABILITY)
    phase = charge.instance.vars[59] if charge is not None else None

    def observe(shooter, command, state, *args, **kwargs):
        if shooter is bot:
            launches.append((run.frame, state.instance.graph.index, fight.is_stunned(bot)))
        return shoot(shooter, command, state, *args, **kwargs)

    damage_during_stun = 0
    with patch.object(fight, "_shoot", side_effect=observe):
        for _ in range(280):
            old = vitals.total
            run.step({player: (yaw, pitch, 0)})
            if fight.is_stunned(bot):
                stunned.append(run.frame)
                damage_during_stun += old - vitals.total
            if phase is not None and phase.value() == 1:
                starts.append(run.frame)
            if stunned and run.frame > stunned[-1] and vitals.total < before:
                break
    damage = before - vitals.total
    expected = {"Trooper": 20, "Enforcer": 32, "Sniper": 100, "Heavy Assault": 5}[definition.name]
    passed = (len(stunned) == 50 and npc_before - npc_vitals.total == 70
              and damage_during_stun == 0 and bot.attack_target == player.entity)
    if charge_after:
        passed = passed and bool(starts and starts[0] > stunned[-1] and not launches and damage == 175)
    else:
        passed = (passed and bool(launches) and not starts and damage == expected
                  and all(frame > stunned[-1] and not control for frame, _, control in launches))
    return {"scenario": "npc_attack_recovery_after_accretion", "expected_charge": charge_after,
            "fixture_target_flag_961": charge_after,
            "stun_frames": len(stunned), "npc_damage": npc_before - npc_vitals.total,
            "damage_during_stun": damage_during_stun, "sigma_damage_after_stun": damage,
            "launches": launches, "charge_start": starts[0] if starts else None, "passed": passed}


def enforcer_volley_lifecycle(definition, mode):
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    npc_vitals = fight.vitals[bot.entity]
    before, npc_before = vitals.total, npc_vitals.total
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    run.step({player: (yaw, pitch, 0x10 if mode == "accretion" else 0)})
    npc_control.start(run.match, player)
    launches = []
    original_shoot = fight._shoot

    def observe(shooter, *args, **kwargs):
        count = len(fight.rockets)
        original_shoot(shooter, *args, **kwargs)
        if shooter is bot:
            pellets = fight.rockets[count:]
            launches.append((run.frame, len(pellets), len({r.direction for r in pellets})))

    stopped = False
    damage_during_stun = 0
    stunned = False
    with patch.object(fight, "_shoot", side_effect=observe):
        for _ in range(300):
            old_total = vitals.total
            was_stunned = fight.is_stunned(bot)
            run.step({player: (yaw, pitch, 0)} if mode == "accretion" else {})
            if was_stunned:
                damage_during_stun += old_total - vitals.total
            stunned |= fight.is_stunned(bot)
            if not stopped and launches and mode in ("stop", "wall"):
                stopped = True
                if mode == "stop":
                    npc_control.stop(run.match)
                else:
                    SigmaCombatTests().wall(run, sideways=False, offset=3)
            if mode == "accretion" and vitals.total < before:
                break
    damage = before - vitals.total
    complete = bool(launches) and all(count == distinct == 25 for _, count, distinct in launches)
    if mode == "accretion":
        passed = stunned and npc_before - npc_vitals.total == 70 and damage_during_stun == 0 and damage > 0
    else:
        passed = (stopped and damage == 0 and len(launches) == 1
                  and not any(r.shooter is bot for r in fight.rockets))
    return {"scenario": "enforcer_projectile_volley_lifecycle", "mode": mode, "launches": launches,
            "sigma_damage": damage, "damage_during_stun": damage_during_stun,
            "npc_damage": npc_before - npc_vitals.total, "stun_observed": stunned,
            "passed": passed and complete and bot.body_script.component.fault is None}


def enforcer_volley_range(definition, distance, *, late_stun=False):
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    target = next(t for t in fight._targets() if t.owner is player)
    vitals = fight.vitals_of(target)
    npc_vitals = fight.vitals[bot.entity]
    before, npc_before = vitals.total, npc_vitals.total
    x, y, z = player.mover.position
    bot.position = (x, y, z + distance)
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z + distance))
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    hits, travelled, launches = [], [], []
    damage, landed, shoot = fight._damage, fight._landed, fight._shoot

    def observe_damage(shooter, target, amount, *args, **kwargs):
        if shooter is bot:
            hits.append((amount, fight.is_stunned(bot)))
        return damage(shooter, target, amount, *args, **kwargs)

    def observe_landed(rocket, **kwargs):
        if rocket.shooter is bot:
            travelled.append(rocket.flown * rocket.speed)
        return landed(rocket, **kwargs)

    def observe_launch(shooter, *args, **kwargs):
        initial = len(fight.rockets)
        shoot(shooter, *args, **kwargs)
        if shooter is bot:
            launches.append((run.frame, len(fight.rockets) - initial))

    moved_out_of_range = False
    with (patch.object(fight, "_damage", side_effect=observe_damage),
          patch.object(fight, "_landed", side_effect=observe_landed),
          patch.object(fight, "_shoot", side_effect=observe_launch)):
        for frame in range(160):
            run.step({player: (yaw, pitch, 0x10 if late_stun and frame == 4 else 0)})
            if distance == 90 and launches and not moved_out_of_range:
                player.mover.position = [x, y, z - 15]
                player.mover._engine_body().teleport((x, y + player.mover._lift(), z - 15))
                moved_out_of_range = True
            if launches and not any(rocket.shooter is bot for rocket in fight.rockets):
                break
    amounts = [amount for amount, _ in hits]
    if distance == 90:
        passed = moved_out_of_range and not hits and bool(travelled) and math.isclose(max(travelled), 100)
    elif distance == 6:
        passed = len(hits) == 22 and all(amount == 2 for amount in amounts)
    elif distance == 11:
        passed = len(hits) == 14 and all(1.3 < amount < 1.5 for amount in amounts)
    else:
        passed = len(hits) == 9 and all(math.isclose(amount, 0.4, abs_tol=1e-6) for amount in amounts)
    if late_stun:
        passed &= npc_before - npc_vitals.total == 70 and all(stunned for _, stunned in hits)
    return {"scenario": "enforcer_projectile_range", "launch_distance": distance, "late_stun": late_stun,
            "launches": launches, "damage": before - vitals.total, "hit_amounts": amounts,
            "hits_during_stun": sum(stunned for _, stunned in hits),
            "max_flight": max(travelled, default=0), "npc_damage": npc_before - npc_vitals.total,
            "passed": bool(passed and len(launches) == 1 and launches[0][1] == len(travelled) == 25)}


def enforcer_fractional_shield_recovery(definition):
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    target = next(t for t in fight._targets() if t.owner is player)
    vitals = fight.vitals_of(target)
    shield = combat.SHIELDS_POOL
    body_health = vitals.now[combat.HEALTH_POOL]
    x, y, z = player.mover.position
    bot.position = (x, y, z + 18)
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z + 18))
    script = player.body_script
    script.ult_seconds = 0  # isolate incoming damage from passive accrual
    script.set_ult_charge(73)
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    pool_values = []
    queue = player.client.queue_body_health

    def observe_pool(entity, components):
        if entity == player.body:
            pool = components[51][shield * combat.POOLS]
            pool_values.append(struct.unpack_from("<Iff", pool))
        return queue(entity, components)

    shield_max = vitals.most[shield]
    hit_tick = None
    with patch.object(player.client, "queue_body_health", side_effect=observe_pool):
        for _ in range(100):
            run.step({player: (yaw, pitch, 0)})
            if vitals.now[shield] < shield_max:
                hit_tick = fight.tick
                npc_control.stop(run.match)
                break
        damaged = vitals.now[shield]
        damage_record = pool_values[-1] if pool_values else None
        deadline = vitals.shield_recovery_tick
        first_recovery = None
        early_recovery = False
        for _ in range(240):
            run.step({})
            if vitals.now[shield] > damaged and first_recovery is None:
                first_recovery = fight.tick
                early_recovery = deadline is None or fight.tick <= deadline
            if vitals.now[shield] == shield_max:
                break
    charge = script.component.entity_var(342).value()
    passed = (math.isclose(shield_max - damaged, 3.6, abs_tol=1e-6)
              and damage_record is not None and damage_record[0] == vitals.ids[shield]
              and math.isclose(damage_record[2], damaged, abs_tol=1e-5)
              and hit_tick is not None and deadline is not None and first_recovery is not None
              and 187 < first_recovery - hit_tick <= 188 and not early_recovery
              and vitals.now[shield] == shield_max and vitals.now[combat.HEALTH_POOL] == body_health
              and charge == 73 and not player.client.messages(combat.HIT))
    return {"scenario": "enforcer_fractional_shield_recovery", "shield_damage": shield_max - damaged,
            "component_51_damage_record": damage_record, "hit_tick": hit_tick,
            "first_recovery_tick": first_recovery, "ordinary_shield_final": vitals.now[shield],
            "ultimate_charge": charge, "passed": passed}


def enforcer_shield_recovery_across_tick_gap(definition):
    runs = []
    for skipped in (False, True):
        run, player, bot = arena(definition, "incoming")
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
        run.step({player: (yaw, pitch, 0)})
        owner_frame = player.body_script.frame
        shield, health = combat.SHIELDS_POOL, combat.HEALTH_POOL
        initial_health = vitals.now[health]

        def update(tick, match=run.match):
            match.update(tick * combat.TICK, tick, frames=False)

        npc_control.start(run.match, player)
        for tick in range(run.frame + 1, run.frame + 90):
            update(tick)
            if vitals.now[shield] < vitals.most[shield]:
                break
        else:
            return {"scenario": "enforcer_shield_recovery_across_tick_gap", "passed": False,
                    "error": "initial authored volley did not hit"}
        first_hit = tick
        initial_shields = vitals.now[shield]
        npc_control.stop(run.match)

        update(first_hit + 120)
        npc_control.start(run.match, player)
        gap_start = first_hit + 175
        for tick in range(first_hit + 121, gap_start + 1):
            update(tick)
        no_in_flight_pellets = not fight.rockets
        gap_end = first_hit + 195
        ticks = (gap_end,) if skipped else range(gap_start + 1, gap_end + 1)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for tick in ticks:
                update(tick)
        hits = [call.kwargs["world_tick"] - first_hit for call in damage.call_args_list
                if call.args[0] is bot and call.args[1].owner is player]
        after_impact = vitals.now[shield]
        deadline = vitals.shield_recovery_tick
        npc_control.stop(run.match)

        update(first_hit + 375)
        before_recovery = vitals.now[shield]
        update(first_hit + 376)
        resumed = vitals.now[shield]
        update(first_hit + 600)
        runs.append({
            "skipped_ticks": 20 if skipped else 0, "first_hit": first_hit,
            "initial_shields": initial_shields, "follow_up_hits": len(hits),
            "follow_up_hit_offsets": sorted(set(hits)), "shield_after_impact": after_impact,
            "recovery_deadline": deadline, "shield_before_recovery": before_recovery,
            "shield_after_recovery_resumes": resumed, "final_shields": vitals.now[shield],
            "passed": (
                no_in_flight_pellets and initial_shields == 56 and hits == [188] * 22
                and math.isclose(after_impact, 12.24, abs_tol=1e-6)
                and deadline == first_hit + 375.5 and before_recovery == after_impact
                and math.isclose(resumed, 12.48, abs_tol=1e-6)
                and vitals.now[shield] == 100 and vitals.shield_recovery_tick is None
                and vitals.now[health] == initial_health and player.body_script.frame == owner_frame
            ),
        })
    return {"scenario": "enforcer_shield_recovery_across_tick_gap", "runs": runs,
            "passed": all(row["passed"] for row in runs)}


def enforcer_grasp_shield_spill(definition):
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    shield = combat.SHIELDS_POOL
    shield_max = vitals.most[shield]
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    script = player.body_script
    script.ult_seconds = 0  # isolate combat credit from passive accrual
    cost = script.component.entity_var(338).value()
    script.set_ult_charge(cost - 60)
    run.step({player: (yaw, pitch, 0x08)})
    npc_control.start(run.match, player)
    grasp = next(i for i in player.body_script.component.instances.values() if i.graph.index == absorption.GRASP)
    absorbed = 0
    for _ in range(110):
        run.step({player: (yaw, pitch, 0)})
        absorbed = grasp.vars[21312].value()
        if absorbed >= 50:
            npc_control.stop(run.match)
            break
    for _ in range(140):
        run.step({player: (yaw, pitch, 0)})
        if bool(_grasp_pools(vitals)):
            break
    granted = _grasp_pools(vitals)
    grant = sum(entry.now for entry in granted) if bool(granted) else 0
    first_activation = grasp.states[1].counter
    ability = grasp.states[0]
    cooldown_at_retry = ability.remaining()
    run.step({player: (yaw, pitch, 0x08)})
    npc_control.start(run.match, player)
    impacted = False
    for _ in range(160):
        run.step({player: (yaw, pitch, 0)})
        if vitals.now[shield] < shield_max:
            impacted = True
        if impacted and not any(rocket.shooter is bot for rocket in fight.rockets):
            npc_control.stop(run.match)
            break
    temporary = _grasp_pools(vitals)
    remaining = sum(entry.now for entry in temporary) if bool(temporary) else 0
    shield_damage = shield_max - vitals.now[shield]
    body_damage = vitals.most[combat.HEALTH_POOL] - vitals.now[combat.HEALTH_POOL]
    denied = grasp.states[1].counter == first_activation and not grasp.states[1].active
    for _ in range(1000):
        if ability.remaining() <= 0:
            break
        run.step({player: (yaw, pitch, 0)})
    ready = ability.remaining() == 0
    before_second = sum(vitals.now.values())
    run.step({player: (yaw, pitch, 0x08)})
    npc_control.start(run.match, player)
    with patch.object(fight.absorption, "absorbed", wraps=fight.absorption.absorbed) as record_absorption:
        for _ in range(110):
            run.step({player: (yaw, pitch, 0)})
            if record_absorption.call_count >= 25:
                npc_control.stop(run.match)
                break
    second_absorbed = sum(absorption.amount(call.args[1], call.args[2]) or 0 for call in record_absorption.call_args_list)
    for _ in range(140):
        run.step({player: (yaw, pitch, 0)})
        pool = _grasp_pools(vitals)
        if bool(pool) and sum(entry.now for entry in pool) > 0:
            break
    second_pool = _grasp_pools(vitals)
    second_bonus = sum(entry.now for entry in second_pool) if second_pool else 0
    second_damage = before_second - sum(vitals.now.values())
    unchanged_charge = script.component.entity_var(342).value()
    economy = enforcer_earned_flux(run, player, bot, yaw, pitch, cost)
    return {"scenario": "enforcer_grasp_shield_spill", "absorbed": absorbed, "temporary_granted": grant,
            "temporary_remaining": remaining, "ordinary_shield_damage": shield_damage,
            "health_damage": body_damage, "cooldown_at_retry": cooldown_at_retry, "retry_denied": denied,
            "ready": ready, "second_absorbed": second_absorbed, "second_temporary_grant": second_bonus,
            "second_base_damage": second_damage, "charge_after_two_grasps": unchanged_charge,
            "earned_flux": economy,
            "passed": (absorbed == 50 and math.isclose(grant, 30, abs_tol=1e-5)
                       and remaining == body_damage == 0
                       and math.isclose(shield_damage, 14, abs_tol=1e-5)
                       and cooldown_at_retry > 0 and denied and ready
                       and grasp.states[1].counter == first_activation + 1
                       and record_absorption.call_count == 25
                       and second_absorbed == 50 and math.isclose(second_bonus, 30, abs_tol=1e-5)
                       and second_damage == 0 and unchanged_charge == cost - 60 and economy["passed"])}


def enforcer_earned_flux(run, player, bot, yaw, pitch, cost):
    fight = run.match.combat
    script = player.body_script
    charge = script.component.entity_var(342)
    flux = SigmaCombatTests().ability(player, sigma_flux.FLUX)
    run.step({player: (yaw, pitch, 0x20)})
    denied = not flux.flags & nodes.Ability.RUNNING and charge.value() == cost - 60
    run.step({player: (yaw, pitch, 0)})
    vitals = fight.vitals[bot.entity]
    before = vitals.total
    run.step({player: (yaw, pitch, 0x10)})
    for _ in range(110):
        run.step({player: (yaw, pitch, 0)})
    accretion_damage = before - vitals.total
    earned = charge.value()
    before_flux = vitals.total
    SigmaFluxPlacementTests().select(run, player, tuple(bot.position))
    spent = charge.value()
    started = bool(flux.flags & nodes.Ability.RUNNING)
    for _ in range(320):
        run.step({})
    damage = before_flux - vitals.total
    final_charge = charge.value()
    return {"undercharge_denied": denied, "accretion_damage": accretion_damage,
            "earned_charge": earned, "charge_after_confirm": spent, "flux_damage": damage,
            "final_charge": final_charge,
            "passed": (denied and accretion_damage == 70 and earned == cost == 1960
                       and started and spent == final_charge == 0 and damage == 50 + vitals.maximum / 2
                       and not flux.flags & nodes.Ability.RUNNING and _airborne_mover(bot) is None)}


def enforcer_kills_flux_preview(definition):
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    vitals.now[combat.HEALTH_POOL] = 25
    vitals.now[combat.SHIELDS_POOL] = 0
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    script = player.body_script
    script.ult_seconds = 0
    charge = script.component.entity_var(342)
    script.set_ult_charge(script.component.entity_var(338).value())
    flux = SigmaCombatTests().ability(player, sigma_flux.FLUX)
    run.step({player: (yaw, pitch, 0x20)})
    entered_preview = bool(flux.flags & nodes.Ability.RUNNING)
    after_start = charge.value()
    old_body = player.body
    npc_vitals = fight.vitals[bot.entity]
    npc_before = npc_vitals.total
    effect_seen = False
    for _ in range(250):
        run.step({player: (yaw, pitch, 0)})
        effect_seen |= any(i.graph.index == sigma_flux.FLUX_EFFECT
                           for i in bot.body_script.component.instances.values())
        if fight.is_dead(player):
            break
    died = fight.is_dead(player)
    stopped = not flux.flags & nodes.Ability.RUNNING
    at_death = charge.value()
    selection_phase = flux.instance.vars[59].value()
    npc_control.stop(run.match)
    respawn_charge = None
    respawn = fight._respawn

    def observe_respawn(who):
        nonlocal respawn_charge
        respawn(who)
        if who is player and player.body != old_body:
            respawn_charge = player.body_script.component.entity_var(342).value()

    with patch.object(fight, "_respawn", side_effect=observe_respawn):
        for frame in range(750):
            # A real primary edge after death must not confirm the old preview.
            run.step({player: (yaw, pitch, FIRE if frame == 0 else 0)})
            effect_seen |= any(i.graph.index == sigma_flux.FLUX_EFFECT
                               for i in bot.body_script.component.instances.values())
            if respawn_charge is not None:
                break
    damage = npc_before - npc_vitals.total
    return {"scenario": "enforcer_lethal_flux_preview", "entered_preview": entered_preview,
            "charge_after_start": after_start, "died": died, "npc_kill_credit": vitals.killer == bot.entity,
            "selection_phase_at_death": selection_phase, "flux_stopped": stopped, "charge_at_death": at_death,
            "charge_at_respawn": respawn_charge, "npc_damage": damage, "target_effect_seen": effect_seen,
            "passed": (entered_preview and died and vitals.killer == bot.entity and selection_phase == 1
                       and stopped and after_start == at_death == respawn_charge == 0
                       and damage == 0 and not effect_seen)}


def enforcer_barrier_redeploy(run, player, bot, barrier, vitals, yaw, pitch):
    cooldown = barrier.instance.states[111]
    remaining = cooldown.remaining()
    run.step({player: (yaw, 0, 0x02)})
    run.step({player: (yaw, pitch, 0)})
    denied = not barrier.active
    for _ in range(350):
        if cooldown.remaining() <= 0:
            break
        run.step({player: (yaw, pitch, 0)})
        denied &= not barrier.active
    ready = cooldown.remaining() == 0
    restored = barrier.vitals.total
    for frame in range(40):
        run.step({player: (yaw, 0, 0x02 if frame < 16 else 0)})
    deployed = barrier.active
    deployed_health = barrier.vitals.total
    before = sum(vitals.now.values())
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    fight = run.match.combat
    for _ in range(150):
        run.step({player: (yaw, pitch, 0)})
        if barrier.vitals.total < deployed_health and not any(r.shooter is bot for r in fight.rockets):
            npc_control.stop(run.match)
            break
    shield_damage = deployed_health - barrier.vitals.total
    body_damage = before - sum(vitals.now.values())
    return {"cooldown_at_early_press": remaining, "early_press_denied": denied, "ready": ready,
            "health_when_ready": restored, "health_on_redeploy": deployed_health,
            "redeployed": deployed, "follow_up_barrier_damage": shield_damage, "body_damage": body_damage,
            "passed": (remaining > 0 and denied and ready and 0 < restored < barrier.vitals.maximum
                       and deployed and shield_damage == 50 and body_damage == 0)}


def enforcer_defense_order(definition, mode):
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    before = sum(vitals.now.values())
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    hold = 3 if mode == "grasp_first" else 16
    for frame in range(40):
        run.step({player: (yaw, 0, 0x02 if frame < hold else 0)})
    barrier, = fight.sigma_barriers.values()
    if mode == "barrier_breaks":
        barrier.vitals.damage(680)  # leave 20 durability to exercise a break within one volley
    durability = barrier.vitals.total
    run.step({player: (yaw, pitch, 0x08)})
    npc_control.start(run.match, player)
    grasp = next(i for i in player.body_script.component.instances.values() if i.graph.index == absorption.GRASP)
    contacts = []
    shoot = fight._shoot

    def observe(shooter, *args, **kwargs):
        count = len(fight.rockets)
        shoot(shooter, *args, **kwargs)
        if shooter is not bot:
            return
        for pellet in fight.rockets[count:]:
            shield = sigma_barrier.barrier_intersection(fight, bot, pellet.position, pellet.direction, 6)
            field = absorption.grasp_intersection(fight, bot, pellet.position, pellet.direction, 6)
            contacts.append((shield[0] if shield else None, field[0] if field else None))

    with patch.object(fight, "_shoot", side_effect=observe):
        for _ in range(120):
            run.step({player: (yaw, pitch, 0)})
            if contacts and not any(rocket.shooter is bot for rocket in fight.rockets):
                npc_control.stop(run.match)
                break
    shield_damage = durability - barrier.vitals.total
    active_after_impact = barrier.active
    immediate_denied = True
    if mode == "barrier_breaks":
        run.step({player: (yaw, 0, 0x02)})
        run.step({player: (yaw, pitch, 0)})
        immediate_denied = not barrier.active
    for _ in range(130):
        run.step({})
    absorbed = grasp.vars[21312].value()
    temporary = _grasp_pools(vitals)
    bonus = sum(entry.now for entry in temporary) if bool(temporary) else 0
    base_damage = before - sum(vitals.now.values())
    barrier_first = sum(a is not None and b is not None and a < b for a, b in contacts)
    grasp_first = sum(a is not None and b is not None and b < a for a, b in contacts)
    expected_shield, expected_absorbed = {
        "grasp_first": (0, 50), "barrier_first": (50, 0), "barrier_breaks": (20, 30),
    }[mode]
    expected_first = grasp_first if mode == "grasp_first" else barrier_first
    health_after_grasp = barrier.vitals.total
    redeploy = (enforcer_barrier_redeploy(run, player, bot, barrier, vitals, yaw, pitch)
                if mode == "barrier_breaks" else None)
    return {"scenario": "enforcer_barrier_grasp_order", "mode": mode, "pellets": len(contacts),
            "barrier_first_geometry": barrier_first, "grasp_first_geometry": grasp_first,
            "barrier_damage": shield_damage, "absorbed": absorbed, "temporary_shields": bonus,
            "base_pool_damage": base_damage, "barrier_active_after_impact": active_after_impact,
            "barrier_health_after_grasp_grant": health_after_grasp,
            "immediate_redeploy_denied": immediate_denied, "redeploy": redeploy,
            "passed": (len(contacts) == expected_first == 25 and shield_damage == expected_shield
                       and absorbed == expected_absorbed and base_damage == 0
                       and active_after_impact == (mode != "barrier_breaks")
                       and immediate_denied and (redeploy is None or redeploy["passed"])
                       and math.isclose(bonus, absorbed * 0.6, abs_tol=1e-5))}


def incoming_npc_melee(definition, mode):
    run, player, bot = arena(definition, "incoming" if mode == "range" else "heavy_melee")
    heavy = definition.hero == heavy_assault.HERO
    fight = run.match.combat
    target = next(t for t in fight._targets() if t.owner is player)
    vitals = fight.vitals_of(target)
    x, y, z = bot.position
    source = (x, y + 1.25, z)
    yaw, pitch = aim(fight._eye(player), source)
    shield = None
    for _ in range(12):
        run.step({})  # allow the Trooper's authored initialization wait
    if mode == "barrier":
        for frame in range(9):
            run.step({player: (yaw, 0, 0x02 if frame < 5 else 0)})
        shield, = fight.sigma_barriers.values()
    buttons = {"grasp": 0x08, "accretion": 0x10}.get(mode, 0)
    run.step({player: (yaw, pitch, buttons)})
    if mode == "accretion":
        for _ in range(40):
            run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    component = bot.body_script.component
    attack = heavy_assault.ability(component)
    grasp = next((i for i in player.body_script.component.instances.values()
                  if i.graph.index == absorption.GRASP), None)
    began = False
    contact_frame = None
    defense_active = False
    npc_vitals = fight.vitals[bot.entity]
    npc_before = npc_vitals.total
    stunned_before_contact = False
    with player_damage_sources(fight, player, bot, vitals) as dealt:
        for frame in range(80 if mode == "range" else 40):
            grasp_active = grasp is not None and grasp.states.get(1) and grasp.states[1].active
            separated = shield is not None and shield.active and _sigma_barrier_separates(fight, bot, source, target)
            run.step({})
            if attack.flags & nodes.Ability.RUNNING and not began:
                began = True
                if mode == "cancel":
                    npc_control.stop(run.match)
                elif mode == "wall":
                    SigmaCombatTests().wall(run, sideways=False, offset=1)
            stunned_before_contact |= began and fight.is_stunned(bot) and dealt["npc"] == 0
            if dealt["npc"]:
                contact_frame = frame
                defense_active |= bool(mode == "grasp" and grasp_active) or separated
                break
    damage = dealt["npc"]
    velocity = tuple(player.mover.velocity) if contact_frame is not None else None
    barrier_damage = shield.vitals.maximum - shield.vitals.total if shield is not None else 0
    absorbed_slot = grasp.vars.get(21312) if grasp is not None else None
    absorbed = absorbed_slot.value() if absorbed_slot is not None else 0
    remaining = attack.remaining()
    running = bool(attack.flags & nodes.Ability.RUNNING)
    if mode in ("cancel", "wall", "accretion"):
        passed = began and damage == 0 and not running and remaining > 0
        if mode != "accretion":
            passed &= not any(player.mover.velocity)
        if mode == "accretion":
            passed &= stunned_before_contact and npc_before - npc_vitals.total == 70
    elif mode == "range":
        passed = damage == 20 and not began
    else:
        impulse = velocity is not None and (
            (velocity[1] > 7 and velocity[2] < -19) if heavy else (velocity[1] == 0 and velocity[2] < 0)
        )
        passed = began and damage == (80 if heavy else 30) and impulse
        passed &= mode == "open" or defense_active
    return {"scenario": "heavy_assault_authored_melee" if heavy else "trooper_authored_melee",
            "mode": mode, "damage": damage, "sigma_self_damage": dealt["self"],
            "frame": contact_frame, "velocity": velocity, "defense_active_at_contact": defense_active,
            "barrier_damage": barrier_damage,
            "absorbed": absorbed, "cooldown_remaining": remaining, "windup_started": began,
            "npc_damage": npc_before - npc_vitals.total, "stunned_before_contact": stunned_before_contact,
            "passed": passed and component.fault is None and barrier_damage == absorbed == 0}


def npc_melee_targets(definition, mode):
    names = ("Sigma", "Second Sigma", "NPC Ally", "Zarya") if mode == "projected" else (
        "Sigma", "Second Sigma", "NPC Ally",
    )
    run, player, bot = arena(definition, "heavy_melee", names=names)
    match = run.match
    fight = match.combat
    other, ally = run.players[1:3]
    match.game_map = replace(match.game_map, free_for_all=False)
    other.team, ally.team = player.team, bot.team
    match.switch_hero(other, heroes()[0x02E0000000000002 if mode == "phase" else SIGMA])
    x, y, z = player.mover.position
    other.mover.position[:] = (x + .95, y, z + .55)
    ally.mover.position[:] = (x - .95, y, z + .55)
    other.mover.body = ally.mover.body = None
    if mode == "cover":
        plane = z + 1.4
        a, b = (x + .22, y - .25, plane), (x + 1.55, y - .25, plane)
        c, d = (x + 1.55, y + 4, plane), (x + .22, y + 4, plane)
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
        for body in run.players:
            body.mover.use(world=match.collision_world)
    elif mode == "projected":
        zarya = run.players[3]
        zarya.team = player.team
        match.switch_hero(zarya, heroes()[0x02E0000000000068])
        zarya.mover.position[:] = (x + 5, y, z + .55)
        zarya.mover.body = None

    bx, by, bz = bot.position
    facing = aim(fight._eye(player), (bx, by + 1.25, bz))
    for _ in range(12):
        run.step({})
    run.step({player: (*facing, 0)})
    npc_control.start(match, player)
    attack = heavy_assault.ability(bot.body_script.component)
    vitals = {item.owner: fight.vitals_of(item) for item in fight._targets() if item.owner in run.players}
    bubble = None
    phased = False
    for _ in range(45):
        commands = {}
        if mode == "projected" and bubble is None and attack.flags & nodes.Ability.RUNNING:
            commands[zarya] = (*aim(fight._eye(zarya), fight._eye(other)), 0x10)
        if mode == "phase" and not phased and attack.flags & nodes.Ability.RUNNING:
            commands[other] = (0, 0, 0x08)
        run.step(commands)
        if mode == "projected" and bubble is None:
            bubble = _particle_pet(fight, other)
        phased = defenses.intangible(other)
        if vitals[player].total < vitals[player].maximum:
            break
    chosen = attack.instance.vars.get(375)
    selected = {expr.to_entity(entry) for entry in (chosen.value() or ())} if chosen is not None else set()
    expected = {player.body} if mode in ("cover", "phase") else {player.body, other.body}
    damage = [vitals[body].maximum - vitals[body].total for body in (player, other, ally)]
    amount = 80 if definition.hero == heavy_assault.HERO else 30
    durability = bubble.vitals.total if bubble is not None else None
    energy = (zarya.body_script.component.entity_var(87).value() or 0) if mode == "projected" else None
    side_pushed = other.mover.velocity[0] > 0
    passed = (selected == expected and damage == [amount, amount if mode == "open" else 0, 0]
              and side_pushed == (mode == "open") and not any(ally.mover.velocity)
              and (mode != "projected" or durability == 200 - amount and energy == amount / 5)
              and (mode != "phase" or phased)
              and attack.instance.component.fault is None)
    return {"scenario": "npc_melee_contact_targets", "mode": mode, "selected_expected_bodies": selected == expected,
            "damage_primary_secondary_friendly": damage, "secondary_pushed": side_pushed,
            "projected_barrier_durability": durability, "zarya_energy": energy,
            "secondary_phased_at_contact": phased, "passed": passed}


def npc_melee_vs_sigma_melee(definition, *, lethal):
    run, player, bot = arena(definition, "heavy_melee")
    fight = run.match.combat
    health = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
    npc_health = fight.vitals[bot.entity]
    npc_health.now[combat.HEALTH_POOL] = 30 if lethal else 31
    npc_health.now[combat.ARMOUR_POOL] = 0
    x, y, z = bot.position
    facing = aim(fight._eye(player), (x, y + 1.25, z))
    for _ in range(12):
        run.step({})
    run.step({player: (*facing, 0)})
    npc_control.start(run.match, player)
    attack = heavy_assault.ability(bot.body_script.component)
    for _ in range(25):
        run.step({})
        if attack.flags & nodes.Ability.RUNNING:
            break
    windup = bool(attack.flags & nodes.Ability.RUNNING) and health.total == health.maximum
    run.step({player: (*facing, 0x800)})
    for _ in range(25):
        run.step({})
        if not lethal and health.total < health.maximum:
            break
    damage = health.maximum - health.total
    expected = 80 if definition.hero == heavy_assault.HERO else 30
    passed = (windup and npc_health.total == (0 if lethal else 1) and damage == (0 if lethal else expected)
              and (npc_health.killer == player.body if lethal else not npc_health.dead)
              and (not lethal or bot.dead and bot in run.match.bots and len(player.client.deaths) == 1))
    return {"scenario": "npc_melee_versus_sigma_quick_melee", "lethal": lethal, "windup_observed": windup,
            "npc_health": npc_health.total, "npc_dead": npc_health.dead, "sigma_damage": damage, "passed": passed}


def trooper_melee_during_flux(definition):
    run, player, bot = arena(definition, "heavy_melee", names=("Sigma", "Ally"))
    ally = run.players[1]
    ally.team = player.team
    run.match.game_map = replace(run.match.game_map, free_for_all=False)
    run.match.switch_hero(ally, heroes()[SIGMA])
    x, y, z = player.mover.position
    ally.mover.position[:] = (x + 1, y, z)
    fight = run.match.combat
    health = fight.vitals_of(next(target for target in fight._targets() if target.owner is ally))
    before = health.total
    trooper_health = fight.vitals[bot.entity]
    trooper_before = trooper_health.total
    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
    SigmaFluxPlacementTests().select(run, player, tuple(bot.position))
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(ally), (x, y + 1.25, z))
    for _ in range(60):
        run.step({ally: (yaw, pitch, 0)})
    npc_control.start(run.match, ally)
    attack = heavy_assault.ability(bot.body_script.component)
    windup_before_lift = False
    for _ in range(65):
        windup_before_lift = bool(attack.flags & nodes.Ability.RUNNING)
        run.step({})
        if _airborne_mover(bot) is not None and _airborne_mover(bot).airborne:
            break
    lifted = _airborne_mover(bot) is not None and _airborne_mover(bot).airborne
    ally_damage_at_lift = before - health.total
    cooldown = attack.remaining()
    melee_at_lift = bool(attack.flags & nodes.Ability.RUNNING)
    melee_during_lift = False
    at_lift = health.total
    airborne_damage = 0
    cooldown_expired_in_air = False
    for _ in range(330):
        run.step({})
        if _airborne_mover(bot) is None:
            break
        melee_during_lift |= bool(attack.flags & nodes.Ability.RUNNING)
        airborne_damage = at_lift - health.total
        cooldown_expired_in_air |= attack.remaining() <= 0
    landed = _airborne_mover(bot) is None
    at_landing = health.total
    fresh_melee = False
    for _ in range(40):
        run.step({})
        fresh_melee |= bool(attack.flags & nodes.Ability.RUNNING)
        if fresh_melee and at_landing - health.total >= 30:
            break
    flux_damage = trooper_before - trooper_health.total
    return {"scenario": "trooper_melee_during_flux", "windup_before_lift": windup_before_lift,
            "lifted": lifted, "ally_damage_at_lift": ally_damage_at_lift, "cooldown_at_lift": cooldown,
            "melee_at_lift": melee_at_lift, "melee_during_lift": melee_during_lift,
            "flux_damage": flux_damage, "airborne_damage": airborne_damage,
            "cooldown_expired_in_air": cooldown_expired_in_air,
            "fresh_melee_after_landing": fresh_melee, "post_landing_damage": at_landing - health.total,
            "passed": (windup_before_lift and lifted and ally_damage_at_lift == 0 and cooldown > 0
                       and not melee_at_lift and not melee_during_lift and flux_damage == 150
                       and airborne_damage == 120 and cooldown_expired_in_air
                       and landed and fresh_melee and at_landing - health.total == 30)}


def npc_melee_cooldown(definition):
    run, player, bot = arena(definition, "heavy_melee")
    fight = run.match.combat
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    for _ in range(12):
        run.step({player: (yaw, pitch, 0)})
    component = bot.body_script.component
    attack = heavy_assault.ability(component)
    npc_control.start(run.match, player)
    run.step({player: (yaw, pitch, 0)})
    windup = bool(attack.flags & nodes.Ability.RUNNING)
    npc_control.stop(run.match)
    cooldown = attack.remaining()
    expected = 80 if definition.hero == heavy_assault.HERO else 30
    hits = []
    damage = fight._damage

    def observe(shooter, target, amount, *args, **kwargs):
        if shooter is bot and target.owner is player:
            hits.append((run.frame, amount))
        return damage(shooter, target, amount, *args, **kwargs)

    cancelled_at = run.frame
    next_windup = None
    previous_cooldown = None
    early_starts = 0
    with patch.object(fight, "_damage", side_effect=observe):
        for frame in range(105):
            if frame in (0, 6, 12):
                if frame:
                    npc_control.stop(run.match)
                npc_control.start(run.match, player)
            before = attack.remaining()
            run.step({player: (yaw, pitch, 0)})
            if next_windup is None and attack.flags & nodes.Ability.RUNNING:
                next_windup, previous_cooldown = run.frame, before
                early_starts = int(before > component.frame_ms / 1000)
            if hits and hits[-1][1] == expected:
                break
    melee_hits = [frame for frame, amount in hits if amount == expected]
    elapsed = (next_windup - cancelled_at) * component.frame_ms / 1000 if next_windup else 0
    passed = (windup and 0.95 <= cooldown <= 1.0 and early_starts == 0
              and previous_cooldown is not None and 0 <= previous_cooldown <= component.frame_ms / 1000
              and elapsed >= cooldown - component.frame_ms / 1000
              and len(melee_hits) == 1 and melee_hits[0] > next_windup)
    return {"scenario": "npc_melee_cancel_cooldown_reengagement", "cooldown_after_cancel": cooldown,
            "immediate_reengagement_frames": [0, 6, 12], "seconds_to_next_windup": elapsed,
            "cooldown_before_next_windup": previous_cooldown, "early_starts": early_starts,
            "next_windup": next_windup, "melee_hits": melee_hits, "hits": hits,
            "passed": passed and component.fault is None}


def incoming_heavy_charge(definition, mode):
    run, player, bot = arena(definition, "heavy_charge")
    player.body_script.component.set(961, True)  # 1408/100's authored flagged-contact target
    fight = run.match.combat
    target = next(t for t in fight._targets() if t.owner is player)
    vitals = fight.vitals_of(target)
    start = bot.position
    x, y, z = start
    source = (x, y + 1.25, z)
    yaw, pitch = aim(fight._eye(player), source)
    shield = None
    separated = False
    if mode == "barrier":
        for frame in range(26):
            run.step({player: (yaw, 0, 0x02 if frame < 20 else 0)})
        shield, = fight.sigma_barriers.values()
        separated = shield.active and _sigma_barrier_separates(fight, bot, source, target)
    run.step({player: (yaw, pitch, 0x08 if mode == "grasp" else 0)})
    npc_control.start(run.match, player)
    component = bot.body_script.component
    attack = heavy_assault.ability(component, heavy_assault.CHARGE_GRAPH, heavy_assault.CHARGE_ABILITY)
    grasp = next((i for i in player.body_script.component.instances.values()
                  if i.graph.index == absorption.GRASP), None)
    interrupted_at = None
    grasp_at_contact = False
    phase = attack.instance.vars[59]
    hit_heavy = False
    heavy_vitals = fight.vitals[bot.entity]
    heavy_before = heavy_vitals.total
    with player_damage_sources(fight, player, bot, vitals) as dealt:
        for frame in range(130):
            grasp_active = grasp is not None and grasp.states.get(1) and grasp.states[1].active
            charging = phase.value() == 1
            use_rock = mode == "accretion" and frame == 40
            run.step({player: (yaw, pitch, 0x10)} if use_rock else {})
            displacement = math.dist(start, bot.position)
            if interrupted_at is None and displacement > 1 and mode in ("cancel", "wall"):
                interrupted_at = bot.position
                if mode == "cancel":
                    npc_control.stop(run.match)
                else:
                    SigmaCombatTests().wall(run, sideways=False, offset=5)
            if charging and heavy_vitals.total < heavy_before and fight.is_stunned(bot):
                interrupted_at = bot.position
                hit_heavy = True
            if dealt["npc"]:
                grasp_at_contact = bool(grasp_active)
                break
    damage = dealt["npc"]
    cooldown = attack.remaining()
    barrier_damage = shield.vitals.maximum - shield.vitals.total if shield is not None else 0
    crossed = separated and bot.position[2] < shield.position[2]
    absorbed_slot = grasp.vars.get(21312) if grasp is not None else None
    absorbed = absorbed_slot.value() if absorbed_slot is not None else 0
    if mode in ("cancel", "wall"):
        passed = (interrupted_at is not None and damage == 0 and cooldown > 12
                  and bot.position == interrupted_at and not any(bot.velocity) and phase.value() == 0)
    elif mode == "accretion":
        passed = (interrupted_at is not None and hit_heavy and damage == 0
                  and cooldown > 12 and phase.value() == 0)
    else:
        passed = damage == 175 and 8 < displacement < 10 and cooldown == 15
        if mode == "barrier":
            passed &= crossed and barrier_damage == 0
        if mode == "grasp":
            passed &= grasp_at_contact and absorbed == 0
    return {"scenario": "heavy_assault_authored_charge", "mode": mode, "damage": damage,
            "fixture_target_flag_961": True,
            "sigma_self_damage": dealt["self"],
            "displacement": displacement, "cooldown_remaining": cooldown, "phase": phase.value(),
            "interruption_triggered": interrupted_at is not None,
            "barrier_crossed": crossed, "barrier_damage": barrier_damage,
            "grasp_active_at_contact": grasp_at_contact, "absorbed": absorbed,
            "heavy_stunned_with_damage": hit_heavy,
            "passed": passed and component.fault is None}


def heavy_charge_carry(definition, mode):
    ally_action = mode in ("source_death", "accretion")
    names = ("Sigma", "Ally") if ally_action else ("Sigma",)
    run, player, bot = arena(definition, "heavy_charge", names=names)
    fight = run.match.combat
    target = next(t for t in fight._targets() if t.owner is player)
    vitals = fight.vitals_of(target)
    x, y, z = player.mover.position
    if ally_action:
        ally = run.players[1]
        ally.team = player.team
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        if mode == "accretion":
            run.match.switch_hero(ally, heroes()[SIGMA])
            ally.mover.position[:] = (x + 3, y, z - 12)
        else:
            ally.mover.position[:] = (x + 6, y, z + 1)
        ally.mover.body = None
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z + 10))
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    component = player.body_script.component
    grasp = next(i for i in component.instances.values() if i.graph.index == absorption.GRASP)
    attack = heavy_assault.ability(bot.body_script.component, heavy_assault.CHARGE_GRAPH, heavy_assault.CHARGE_ABILITY)
    before_pin = False
    for frame in range(120):
        before_pin = grasp.states.get(1) is not None and grasp.states[1].active
        run.step({player: (yaw, pitch, 8 if frame == 65 else 0)})
        if fight.pins.get(bot.entity) == player.body:
            break
    child = next((i for i in component.instances.values() if i.graph.index == 0x1407), None)
    flag = component.entity_var(961, create=False)
    captured = (child is not None and child.source_entity() == bot.entity
                and component.entity_var(7539).value() == expr.Entity(bot.entity))
    grasp_interrupted = before_pin and not grasp.states[1].active and grasp.vars[21312].value() == 0
    undamaged_at_capture = vitals.total == 400
    npc_damage = stun_frames = 0
    if mode == "lethal_wall":
        vitals.now[combat.HEALTH_POOL] = 200
    before_impact = vitals.total
    if mode in ("wall", "lethal_wall"):
        SigmaCombatTests().wall(run, sideways=False, offset=-1)
        for _ in range(20):
            run.step({})
            if bot.entity not in fight.pins:
                break
    elif mode == "stop":
        for _ in range(10):
            run.step({})
        npc_control.stop(run.match)
    elif mode == "source_death":
        bot_vitals = fight.vitals[bot.entity]
        bot_vitals.now[combat.HEALTH_POOL] = 1
        bot_vitals.now[combat.ARMOUR_POOL] = 0
        for _ in range(20):
            bx, by, bz = bot.position
            shot = aim(fight._eye(ally), (bx, by + 1.1, bz))
            run.step({ally: (*shot, FIRE)})
            if bot_vitals.dead:
                break
        source_killed = bot_vitals.dead and bot_vitals.killer == ally.body
    elif mode == "accretion":
        bot_vitals = fight.vitals[bot.entity]
        before = bot_vitals.total
        for frame in range(80):
            bx, by, bz = bot.position
            shot = aim(fight._eye(ally), (bx, by + 1.1, bz - 1.8))
            run.step({ally: (*shot, 0x10 if frame == 0 else 0)})
            if fight.is_stunned(bot) or bot.entity not in fight.pins:
                break
        npc_damage = before - bot_vitals.total
        stun_frames = fight.stuns.get(bot.entity, fight.tick) - fight.tick
    displacement = z - player.mover.position[2]
    damage = before_impact - vitals.total
    released = (bot.entity not in fight.pins and not any(
        i.graph.index == 0x1407 for i in component.instances.values()
    ) and component.entity_var(7539).value() is None)
    recovery_seconds = None
    if mode in ("wall", "lethal_wall"):
        result = damage == 300 and 0 < displacement < 1 and attack.remaining() > 14
        result &= vitals.dead == (mode == "lethal_wall")
        if mode == "lethal_wall":
            result &= vitals.killer == bot.entity
        phase = attack.instance.vars[59]
        result &= phase.value() == 2
        start_frame = run.frame
        stopped = bot.position
        for _ in range(145):
            run.step({})
            if phase.value() != 2:
                break
        recovery_seconds = (run.frame - start_frame) * combat.TICK
        result &= (phase.value() == 0 and 1.984 <= recovery_seconds <= 2.032
                   and bot.position == stopped and vitals.total == before_impact - damage
                   and 12.9 <= attack.remaining() <= 13.1)
    elif mode == "stop":
        result = damage == 0 and displacement > 1 and attack.remaining() > 14
    elif mode == "source_death":
        result = damage == 0 and source_killed
    else:
        result = (damage == 0 and npc_damage == 70 and stun_frames == 50
                  and attack.instance.vars[59].value() == 0 and attack.remaining() > 14)
    return {"scenario": "heavy_assault_unflagged_charge_carry", "mode": mode,
            "capture_graph": captured, "grasp_interrupted": grasp_interrupted, "damage": damage,
            "npc_damage": npc_damage, "stun_frames": stun_frames,
            "victim_displacement": displacement, "released": released, "cooldown_remaining": attack.remaining(),
            "recovery_seconds": recovery_seconds,
            "passed": (not (flag and expr.truthy(flag.value())) and captured and grasp_interrupted
                       and undamaged_at_capture and released and result)}


def heavy_charge_secondary(definition):
    run, player, bot = arena(definition, "heavy_charge", names=("Sigma", "Ally"))
    ally = run.players[1]
    ally.team = player.team
    run.match.game_map = replace(run.match.game_map, free_for_all=False)
    fight = run.match.combat
    x, y, z = player.mover.position
    ally.mover.position[:] = (x + .75, y, z + 1)
    ally.mover.body = None
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z + 10))
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    ally_vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is ally))
    for _ in range(110):
        run.step({})
        if vitals.total < 400:
            break
    attack = heavy_assault.ability(bot.body_script.component, heavy_assault.CHARGE_GRAPH, heavy_assault.CHARGE_ABILITY)
    selected = attack.instance.vars[563].value()
    captured_ally = fight.pins.get(bot.entity) == ally.body
    ally_damage = ally_vitals.maximum - ally_vitals.total
    damage = 400 - vitals.total
    _, up, back = player.mover.velocity
    for _ in range(40):
        run.step({})
    no_repeat = vitals.total == 350
    npc_control.stop(run.match)
    return {"scenario": "heavy_assault_charge_secondary_contact", "damage": damage,
            "captured_ally": captured_ally, "ally_damage": ally_damage,
            "launch_velocity": [up, back], "no_repeat": no_repeat,
            "passed": (captured_ally and selected == (expr.Entity(player.body),)
                       and ally_damage == 0 and damage == 50 and up > 0 and back < 0 and no_repeat)}


def heavy_charge_projected_barrier(definition, *, expires_during_approach):
    run, player, bot = arena(definition, "heavy_charge", names=("Sigma", "Zarya"))
    zarya = run.players[1]
    zarya.team = player.team
    run.match.game_map = replace(run.match.game_map, free_for_all=False)
    run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
    fight = run.match.combat
    x, y, z = player.mover.position
    zarya.mover.position[:] = (x + 5, y, z)
    zarya.mover.body = None
    aim_npc = aim(fight._eye(player), (x, y + 1.25, z + 10))
    aim_ally = aim(fight._eye(zarya), fight._eye(player))
    run.step({player: (*aim_npc, 0), zarya: (*aim_ally, 0)})
    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    if expires_during_approach:
        run.step({player: (*aim_npc, 0), zarya: (*aim_ally, 0x10)})
        for _ in range(37):
            run.step({player: (*aim_npc, 0)})
    npc_control.start(run.match, player)
    bubble = _particle_pet(fight, player)
    protected_in_range = captured = captured_while_protected = False
    durability = None
    for frame in range(110):
        run.step({zarya: (*aim_ally, 0x10)} if frame == 65 and not expires_during_approach else {})
        active = _particle_pet(fight, player)
        if bubble is None and active is not None:
            bubble = active
        if active is not None:
            if abs(bot.position[2] - z) <= 2.1:
                protected_in_range = True
            durability = active.vitals.total
        if fight.pins.get(bot.entity) == player.body:
            captured = True
            captured_while_protected = active is not None
            break
    energy = zarya.body_script.component.entity_var(87).value() or 0
    passed = (bubble is not None and protected_in_range and durability == 150 and energy == 10
              and vitals.total == 400 and not captured_while_protected
              and captured == expires_during_approach)
    return {"scenario": "heavy_assault_charge_projected_barrier", "expires_during_approach": expires_during_approach,
            "protected_in_range": protected_in_range, "barrier_durability": durability, "zarya_energy": energy,
            "captured": captured, "captured_while_protected": captured_while_protected,
            "body_damage": 400 - vitals.total, "passed": passed}


def heavy_charge_terrain(definition, mode):
    run, player, bot = arena(definition, "heavy_charge")
    fight = run.match.combat
    x, y, z = player.mover.position
    facing = aim(fight._eye(player), (x, y + 1.25, z + 10))
    run.step({player: (*facing, 0)})
    npc_control.start(run.match, player)
    attack = heavy_assault.ability(bot.body_script.component, heavy_assault.CHARGE_GRAPH, heavy_assault.CHARGE_ABILITY)
    instance = attack.instance
    carrying = mode == "carried_low"
    tall = mode == "tall"
    for _ in range(100):
        run.step({})
        if (fight.pins.get(bot.entity) == player.body if carrying else bot.position[2] < z + 9):
            break
    running_before_wall = instance.vars[59].value() == 1 and bot.position[2] < z + 9
    carrying_before_wall = fight.pins.get(bot.entity) == player.body
    wall = bot.position[2] - 1.8
    if not carrying:
        player.mover.position[:] = (x + 12, y, z)
        player.mover.body = None
    floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
             (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
    top = y + (3 if tall else .65)
    a, b = (x - .9, y - .25, wall), (x + .9, y - .25, wall)
    c, d = (x + .9, top, wall), (x - .9, top, wall)
    run.match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
    player.mover.use(world=run.match.collision_world)
    hits = {}
    world = instance.component.world
    original_cast = world.body_raycast

    def record_cast(state):
        hit = original_cast(state)
        if hit is True:
            hits.setdefault(state.node.pos, bot.position[2] - wall)
        return hit

    with (patch.object(world, "body_raycast", side_effect=record_cast),
          patch.object(instance, "follow", wraps=instance.follow) as follow):
        for _ in range(20):
            run.step({})
            if instance.vars[59].value() == 0:
                break
    authored_exit = any(call.args[0].pos == (152 if carrying else 169) and call.args[1] == "m_onTruePlug"
                        for call in follow.call_args_list)
    clearance = bot.position[2] - wall
    cooldown = attack.remaining()
    outputs_cleared = all(instance.vars[key].value() is None for key in (2824, 13757))
    health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player)).total
    lower = hits.get(168 if carrying else 170)
    raised = hits.get(172)
    passed = (running_before_wall and carrying_before_wall == carrying
              and lower is not None and 0 < lower <= 1
              and (raised is not None and 1 < raised <= 2 if tall else raised is None)
              and authored_exit == (not tall) and .6 < clearance < 1.05
              and instance.vars[59].value() == 0 and cooldown > 14
              and not fight.pins and not any(bot.velocity) and outputs_cleared and health == 400)
    return {"scenario": "heavy_assault_charge_terrain_probes", "mode": mode,
            "first_hit_distances_by_probe_node": hits, "authored_early_exit": authored_exit,
            "clearance": clearance, "cooldown_remaining": cooldown, "outputs_cleared": outputs_cleared,
            "sigma_damage": 400 - health, "passed": passed}


def heavy_charge_during_flux(definition):
    run, player, bot = arena(definition, "heavy_charge", names=("Sigma", "Ally"))
    ally = run.players[1]
    ally.body_script.component.set(961, True)
    ally.team = player.team
    run.match.game_map = replace(run.match.game_map, free_for_all=False)
    x, y, z = player.mover.position
    ally.mover.position[:] = (x + 2, y, z)
    fight = run.match.combat
    ally_health = fight.vitals_of(next(target for target in fight._targets() if target.owner is ally))
    before = ally_health.total
    heavy_health = fight.vitals[bot.entity]
    heavy_before = heavy_health.total
    script = player.body_script
    script.set_ult_charge(script.component.entity_var(338).value())
    ability = SigmaCombatTests().ability(player, sigma_flux.FLUX)
    flux_yaw, flux_pitch = SigmaFluxPlacementTests().select(run, player, tuple(bot.position), click=False)
    start = bot.position
    x, floor, z = start
    yaw, pitch = aim(fight._eye(ally), (x, floor + 1.25, z))
    run.step({ally: (yaw, pitch, 0)})
    npc_control.start(run.match, ally)
    for _ in range(10):
        run.step({player: (flux_yaw, flux_pitch, 0)})
    run.step({player: (flux_yaw, flux_pitch, FIRE)})
    charge = heavy_assault.ability(bot.body_script.component, heavy_assault.CHARGE_GRAPH,
                                  heavy_assault.CHARGE_ABILITY)
    phase = charge.instance.vars[59]
    entry = None
    stayed_grounded = True
    for _ in range(115):
        run.step({})
        stayed_grounded &= bot.position[1] == floor and _airborne_mover(bot) is None
        if entry is None and heavy_health.total < heavy_before:
            entry = {"charge_phase": phase.value(), "ally_damage": before - ally_health.total,
                     "displacement": math.dist(start, bot.position)}
        if ally_health.total != before:
            break
    impact = before - ally_health.total
    cooldown = charge.remaining()
    cast_running = bool(ability.flags & nodes.Ability.RUNNING)
    npc_control.stop(run.match)
    for _ in range(260):
        run.step({})
        stayed_grounded &= bot.position[1] == floor and _airborne_mover(bot) is None
    flux_damage = heavy_before - heavy_health.total
    return {"scenario": "heavy_assault_charge_during_flux", "entry": entry, "impact": impact,
            "fixture_target_flag_961": True,
            "cooldown": cooldown, "flux_damage": flux_damage, "flux_running_on_impact": cast_running,
            "stayed_grounded": stayed_grounded,
            "passed": (entry is not None and entry["charge_phase"] == 1 and entry["ally_damage"] == 0
                       and entry["displacement"] > 0 and impact == 175 and cooldown == 15 and cast_running
                       and flux_damage == 50 and stayed_grounded)}


def heavy_charge_cooldown(definition):
    run, player, bot = arena(definition, "heavy_charge")
    player.body_script.component.set(961, True)
    fight = run.match.combat
    target = next(t for t in fight._targets() if t.owner is player)
    vitals = fight.vitals_of(target)
    start = bot.position
    x, y, z = start
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    component = bot.body_script.component
    charge = heavy_assault.ability(component, heavy_assault.CHARGE_GRAPH, heavy_assault.CHARGE_ABILITY)
    phase = charge.instance.vars[59]
    for _ in range(100):
        run.step({player: (yaw, pitch, 0)})
        if math.dist(bot.position, start) > 0.6:
            break
    windup = phase.value() == 1
    npc_control.stop(run.match)
    cooldown = charge.remaining()
    stopped_at = run.frame
    stopped_position = bot.position
    early_frames = 0
    second_start = None
    reengaged = False
    for frame in range(1100):
        before = charge.remaining()
        if frame in (0, 8):
            if frame:
                npc_control.stop(run.match)
            npc_control.start(run.match, player)
        if frame == 20:
            npc_control.stop(run.match)
        if frame > 20 and not reengaged and before <= 0.25:
            npc_control.start(run.match, player)
            reengaged = True
        run.step({player: (yaw, pitch, 0)})
        if before > component.frame_ms / 1000 and second_start is None:
            early_frames += int(phase.value() != 0 or bot.position != stopped_position)
        if second_start is None and phase.value() == 1:
            second_start = run.frame
        if phase.value() == 2:
            break
    seconds_to_retry = (second_start - stopped_at) * component.frame_ms / 1000 if second_start else 0
    displacement = math.dist(stopped_position, bot.position)
    passed = (windup and 14.9 <= cooldown <= 15 and early_frames == 0
              and seconds_to_retry >= 14.98 and phase.value() == 2
              and displacement > 7 and charge.remaining() == 15 and vitals.maximum - vitals.total >= 175)
    return {"scenario": "heavy_charge_cancel_cooldown_reengagement", "cooldown_after_cancel": cooldown,
            "fixture_target_flag_961": True,
            "early_frames": early_frames, "seconds_to_next_charge": seconds_to_retry,
            "second_charge_displacement": displacement, "sigma_damage": vitals.maximum - vitals.total,
            "passed": passed and component.fault is None}


def sniper_retreat(definition, mode):
    run, player, bot = arena(definition, "heavy_melee")
    fight = run.match.combat
    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    npc_vitals = fight.vitals[bot.entity]
    if mode == "lethal":
        npc_vitals.now[combat.HEALTH_POOL] = 50
    npc_before = npc_vitals.total
    start = bot.position
    x, y, z = start
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    accretion = mode in ("accretion", "lethal")
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    state = sniper.reposition_state(bot)
    grasp = SigmaCombatTests().ability(player, absorption.GRASP).instance
    interrupted_at = None
    stunned = False
    stunned_damage = 0
    displacement = 0
    move_speed = None
    retreated = None
    committed = started_move = landed = recovery_end = fire_enabled = first_volley = None
    shield = None
    barrier_input_frames = 0
    barrier_damage = absorbed = 0
    fired_during_special = False
    with player_damage_sources(fight, player, bot, vitals) as dealt:
        for _ in range(430):
            moving = state.active
            was_stunned = fight.is_stunned(bot)
            old_damage = dealt["npc"]
            buttons = 0
            phase = sniper.retreat_phase(state)
            if phase == 1 and committed is None:
                committed = run.frame
                if mode == "windup_stop":
                    interrupted_at = bot.position
                    npc_control.stop(run.match)
            # The swept rock reaches the capsule earlier. Delay the cast so
            # these controls still interrupt an already moving Sniper.
            if accretion and committed is not None and run.frame - committed == 7:
                buttons = 0x10
            if mode == "barrier" and moving and barrier_input_frames < 4:
                buttons = 2
                barrier_input_frames += 1
            if mode == "grasp" and fire_enabled is not None and run.frame - fire_enabled == 25:
                buttons = 8
            run.step({player: (yaw, 0 if mode == "barrier" else pitch, buttons)})
            phase = sniper.retreat_phase(state)
            if state.active and started_move is None:
                started_move = run.frame
            if phase == 2 and landed is None:
                landed = run.frame
            if landed is not None and phase == 0 and recovery_end is None:
                recovery_end = run.frame
            if bot.attack_input and fire_enabled is None:
                fire_enabled = run.frame
            if first_volley is None and any(rocket.shooter is bot for rocket in fight.rockets):
                first_volley = run.frame
            fired_during_special |= bot.attack_input and phase in (1, 2)
            if mode == "barrier" and shield is None:
                shield = next(iter(fight.sigma_barriers.values()), None)
            barrier_damage = shield.vitals.maximum - shield.vitals.total if shield is not None else 0
            slot = grasp.vars.get(21312)
            absorbed = slot.value() if slot is not None else 0
            if barrier_damage or absorbed:
                npc_control.stop(run.match)
                break
            if was_stunned:
                stunned_damage += dealt["npc"] - old_damage
            if moving or state.active:
                displacement = math.dist(start, bot.position)
            movement = state.move_request()
            if movement is not None:
                move_speed = movement[1]
            if interrupted_at is None and displacement > 0.8:
                if mode == "stop":
                    interrupted_at = bot.position
                    npc_control.stop(run.match)
                elif mode == "wall":
                    interrupted_at = bot.position
                    SigmaCombatTests().wall(run, sideways=False, offset=4)
            stunned |= moving and fight.is_stunned(bot) and not state.active
            if state.counter and not state.active and retreated is None:
                retreated = bot.position
            if dealt["npc"]:
                break
    damage = dealt["npc"]
    if mode == "stop":
        passed = interrupted_at is not None and bot.position == interrupted_at and damage == 0
    elif mode == "windup_stop":
        passed = committed is not None and bot.position == start and damage == 0 and not bot.attack_input
    elif mode == "wall":
        passed = interrupted_at is not None and interrupted_at[2] < bot.position[2] < start[2] + 1.85
        passed &= bot.position == retreated
    elif mode == "accretion":
        passed = (stunned and stunned_damage == 0 and npc_before - npc_vitals.total == 70
                  and 0 < displacement < 2 and damage == 100)
    elif mode == "lethal":
        passed = bot.dead and npc_vitals.total == 0 and damage == 0 and bot.position == retreated
        passed &= 0 < displacement < 2 and bot in run.match.bots and len(player.client.deaths) == 1
    elif mode in ("barrier", "grasp"):
        passed = 2.49 <= displacement <= 2.51 and damage == 0
        if mode == "barrier":
            passed &= barrier_damage == 100
        else:
            passed &= absorbed == 100
            for _ in range(130):
                run.step({})
                if bool(_grasp_pools(vitals)):
                    break
            bonus = _grasp_pools(vitals)
            passed &= bool(bonus) and math.isclose(sum(entry.now for entry in bonus), 60, abs_tol=1e-5)
    else:
        passed = 2.49 <= displacement <= 2.51 and damage == 100
    frame_ms = state.instance.component.frame_ms
    windup_ms = (started_move - committed) * frame_ms if started_move is not None else None
    recovery_ms = (recovery_end - landed) * frame_ms if recovery_end is not None else None
    if mode in ("open", "barrier", "grasp"):
        passed &= (windup_ms is not None and 736 <= windup_ms <= 784
                   and recovery_ms is not None and 736 <= recovery_ms <= 784
                   and first_volley is not None and first_volley >= fire_enabled > recovery_end)
    expected_moves = 0 if mode == "windup_stop" else 1
    return {"scenario": "sniper_authored_retreat", "mode": mode, "retreat_displacement": displacement,
            "movement_speed": move_speed, "movement_activations": state.counter,
            "accretion_interrupted_movement": stunned, "npc_damage": npc_before - npc_vitals.total,
            "sigma_damage": damage, "sigma_self_damage": dealt["self"], "damage_during_stun": stunned_damage,
            "npc_dead": bot.dead, "windup_ms": windup_ms, "recovery_ms": recovery_ms,
            "first_volley_frame": first_volley, "fired_during_special": fired_during_special,
            "barrier_damage": barrier_damage, "grasp_absorption": absorbed,
            "passed": passed and (move_speed == 20 or expected_moves == 0)
            and state.counter == expected_moves and not state.active and not fired_during_special
            and bot.body_script.component.fault is None}


def sniper_retreat_during_flux(definition):
    run, player, bot = arena(definition, "heavy_melee", names=("Sigma", "Ally"))
    ally = run.players[1]
    ally.team = player.team
    run.match.game_map = replace(run.match.game_map, free_for_all=False)
    run.match.switch_hero(ally, heroes()[SIGMA])
    x, y, z = player.mover.position
    ally.mover.position[:] = (x + 1, y, z)
    fight = run.match.combat
    ally_health = fight.vitals_of(next(target for target in fight._targets() if target.owner is ally))
    sniper_health = fight.vitals[bot.entity]
    before = sniper_health.total
    start = bot.position
    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
    SigmaFluxPlacementTests().select(run, player, start)
    x, y, z = start
    yaw, pitch = aim(fight._eye(ally), (x, y + 1.25, z))
    for _ in range(12):
        run.step({ally: (yaw, pitch, 0)})
    npc_control.start(run.match, ally)
    state = sniper.reposition_state(bot)
    retreat_position = None
    retreating_before_lift = False
    for _ in range(65):
        retreating_before_lift |= state.active
        run.step({})
        if _airborne_mover(bot) is not None and _airborne_mover(bot).airborne:
            break
        if state.active and bot.position != start:
            retreat_position = bot.position
    lifted = _airborne_mover(bot) is not None and _airborne_mover(bot).airborne
    lift_damage = before - sniper_health.total
    resumed_retreat = False
    for _ in range(330):
        run.step({})
        resumed_retreat |= state.active or state.counter > 1
        if _airborne_mover(bot) is None:
            break
    landed = _airborne_mover(bot) is None and abs(bot.position[1] - start[1]) < 0.01
    at_landing = ally_health.total
    for _ in range(145):
        run.step({})
        resumed_retreat |= state.active or state.counter > 1
        if ally_health.total < at_landing:
            break
    displacement = math.dist(start, retreat_position) if retreat_position is not None else 0
    damage = before - sniper_health.total
    return {"scenario": "sniper_retreat_during_flux", "retreat_displacement": displacement,
            "retreating_before_lift": retreating_before_lift,
            "lifted": lifted, "lift_damage": lift_damage, "landed": landed, "flux_damage": damage,
            "retreat_activations": state.counter, "retreat_resumed": resumed_retreat,
            "post_landing_attack": at_landing - ally_health.total,
            "passed": (retreating_before_lift and 0 < displacement < 2.5 and lifted and lift_damage == 50
                       and landed and damage == 50 + sniper_health.maximum / 2 and state.counter == 1
                       and not resumed_retreat and at_landing - ally_health.total == 100)}


def sniper_retreat_cooldown(definition):
    run, player, bot = arena(definition, "heavy_melee")
    fight = run.match.combat
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z))
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    state = sniper.reposition_state(bot)
    for _ in range(105):
        run.step({player: (yaw, pitch, 0)})
        if state.counter == 1 and not state.active:
            break
    first_position = bot.position
    move = player.take_commands

    def approach(commands):
        if math.dist(player.mover.position, first_position) > 2.55:
            commands = [replace(command, forward=127) for command in commands]
        return move(commands)

    near_frames = 0
    second_search = None
    search = state.instance.node_state(state.instance.graph.node(39))
    cooldown = None
    reengagements = 0
    resume_at = None
    for _ in range(810):
        wait = state.instance.states.get(state.instance.graph.node(73).state)
        if (wait is not None and wait.active and wait.ms == 10000 and run.frame % 100 == 0
                and reengagements < 4 and resume_at is None):
            npc_control.stop(run.match)
            resume_at = run.frame + 70
        bx, by, bz = bot.position
        facing = aim(fight._eye(player), (bx, by + 1.25, bz))
        with patch.object(player, "take_commands", side_effect=approach):
            run.step({player: (*facing, 0)})
        if resume_at == run.frame:
            npc_control.start(run.match, player)
            resume_at = None
            reengagements += 1
        if state.counter == 1 and math.dist(player.mover.position, bot.position) < 3:
            near_frames += 1
        wait = state.instance.states.get(state.instance.graph.node(73).state)
        if wait is not None and wait.active and wait.ms == 10000 and cooldown is None:
            cooldown = wait.start
        if search.counter == 2 and second_search is None:
            second_search = search.start
        if state.counter == 2:
            if not state.active:
                break
    delay = second_search - cooldown if second_search is not None and cooldown is not None else 0
    displacement = math.dist(first_position, bot.position)
    passed = (cooldown is not None and reengagements == 4 and near_frames > 500 and 10000 <= delay <= 10032
              and state.counter == 2 and not state.active and 2.49 <= displacement <= 2.51)
    return {"scenario": "sniper_retreat_cooldown_reengagement", "nearby_frames_before_retry": near_frames,
            "manual_reengagements": reengagements,
            "second_search_after_cooldown_began_ms": delay, "second_retreat_displacement": displacement,
            "movement_activations": state.counter,
            "passed": passed and bot.body_script.component.fault is None}


def sniper_retreat_search(definition, mode):
    run, player, bot = arena(definition, "heavy_melee")
    match = run.match
    fight = match.combat
    clear = match.collision_world
    start = bot.position
    original_feet = tuple(player.mover.position)
    x, y, z = start
    facing = aim(fight._eye(player), (x, y + 1.25, z))
    run.step({player: (*facing, 0)})
    npc_control.start(match, player)
    def floor(left, right, back, front):
        a, b, c, d = (left, y, back), (left, y, front), (right, y, front), (right, y, back)
        return [(*a, *b, *c), (*a, *c, *d)]

    # Keep the player side walkable while every outward ray ends off the Sniper's ledge.
    match.collision_world = World(floor(x - 8, x + 8, z - 10, z - 0.5)
                                  + floor(x - 0.9, x + 0.9, z - 0.5, z + 0.9))
    player.mover.use(world=match.collision_world)
    move = sniper.reposition_state(bot)
    instance = move.instance
    search = instance.node_state(instance.graph.node(39))
    retry = instance.node_state(instance.graph.node(41))
    gate = instance.node_state(instance.graph.node(56))
    attempts = []
    original_search = instance.component.world.search_position
    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    shot = None

    def observe(state):
        attempts.append(state.now)
        return original_search(state)

    def destination():
        slot = instance.vars.get(1220)
        return slot.value() if slot is not None else None

    with patch.object(instance.component.world, "search_position", side_effect=observe):
        for _ in range(90):
            run.step({})
            if len(attempts) >= 3:
                break
        blocked = (search.active and gate.current and move.counter == 0 and destination() is None
                   and bot.position == start and retry.counter >= 3 and len(attempts) == 3)
        intervals = [end - begin for begin, end in zip(attempts[:-1], attempts[1:], strict=True)]
        if mode == "ally":
            match.game_map = replace(match.game_map, free_for_all=False)
            bot.team = player.team
        elif mode == "los":
            SigmaCombatTests().wall(run, sideways=False, offset=1)
        elif mode == "range":
            px, py, pz = original_feet
            player.mover.position[:] = (px + 5, py, pz)
            player.mover.body = None
        canceled = None
        if mode == "return_fire":
            for _ in range(155):
                run.step({})
                shot = next((rocket for rocket in fight.rockets if rocket.shooter is bot), None)
                if shot is not None:
                    break
            blocked &= search.active and move.counter == 0 and destination() is None and bot.position == start
        elif mode != "retry":
            for _ in range(35):
                run.step({})
            canceled = (not search.active and (not gate.active or not gate.current) and move.counter == 0
                        and destination() is None and bot.position == start and len(attempts) == 3)
            bot.team = 1 - player.team
            player.mover.position[:] = original_feet
            player.mover.body = None
        match.collision_world = clear
        player.mover.use(world=clear)
        attempts_before_clear = len(attempts)
        cleared_at = instance.component.now
        for _ in range(180):
            run.step({})
            if move.counter and not move.active:
                break
    delay = attempts[attempts_before_clear] - cleared_at if len(attempts) > attempts_before_clear else None
    searches = 1 if mode in ("retry", "return_fire") else 2
    displacement = math.dist(start, bot.position)
    shot_settled = shot is not None and shot not in fight.rockets and vitals.total == 300
    passed = (blocked and all(248 <= interval <= 264 for interval in intervals)
              and delay is not None and 0 < delay <= 264 and search.counter == searches
              and (searches == 1 or canceled) and 2.49 <= displacement <= 2.51
              and (mode != "return_fire" or shot_settled)
              and move.counter == 1 and not move.active and instance.component.fault is None)
    return {"scenario": "sniper_retreat_destination_search", "mode": mode, "blocked": blocked,
            "authored_retry_intervals_ms": intervals, "canceled_invalid_search": canceled,
            "first_search_after_clearing_ms": delay, "search_activations": search.counter,
            "retreat_displacement": displacement, "sigma_damage": 400 - vitals.total,
            "blocked_search_projectile_settled": shot_settled, "passed": passed}


def sniper_retreat_sample(definition, *, blocked_center):
    run, player, bot = arena(definition, "heavy_melee")
    fight = run.match.combat
    start = bot.position
    x, y, z = start
    if blocked_center:
        wall = z + 1.2
        a, b = (x - .12, y - .25, wall), (x + .12, y - .25, wall)
        c, d = (x + .12, y + 3, wall), (x - .12, y + 3, wall)
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        run.match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
        player.mover.use(world=run.match.collision_world)
    else:
        bot.body_script.component.rng.seed(17)
    facing = aim(fight._eye(player), (x, y + 1.25, z))
    run.step({player: (*facing, 0)})
    npc_control.start(run.match, player)
    move = sniper.reposition_state(bot)
    instance = move.instance
    for _ in range(120):
        run.step({})
        if move.counter and not move.active:
            break
    selected = instance.vars[1220].value()
    direction = instance.vars[2083].value()
    horizontal = math.hypot(direction.x, direction.z)
    distance = math.hypot(selected.x - x, selected.z - z)
    dot = ((selected.x - x) * direction.x + (selected.z - z) * direction.z) / (distance * horizontal)
    angle = math.degrees(math.acos(max(-1, min(1, dot))))
    center = (x + 2.5 * direction.x / horizontal, z + 2.5 * direction.z / horizontal)
    center_open = sniper.edge(run.match.collision_world, start, *center, mover.mover_data(bot.body)) is not None
    arrived = math.dist(bot.position, tuple(selected)) < .01
    expected_angle = 14.99 <= angle <= 15.01 if blocked_center else angle < .01 and selected.x > x + .25
    passed = (math.isclose(distance, 2.5, abs_tol=1e-5) and expected_angle and center_open != blocked_center
              and arrived and instance.vars[431].value() == selected
              and move.counter == 1 and not move.active and instance.component.fault is None)
    return {"scenario": "sniper_retreat_directional_sampling", "center_blocked": blocked_center,
            "center_path_open": center_open, "landing_spacing": distance, "angle_from_authored_heading": angle,
            "actual_displacement": math.dist(start, bot.position), "passed": passed}


def incoming_flux(definition, *, early=False):
    run, player, bot = arena(definition, "incoming")
    fight = run.match.combat
    health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    npc_health = fight.vitals[bot.entity]
    before, npc_before, floor = health.total, npc_health.total, bot.position[1]
    script = player.body_script
    script.set_ult_charge(script.component.entity_var(338).value())
    ability = SigmaCombatTests().ability(player, sigma_flux.FLUX)
    if early:
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, 0x20)})
    else:
        SigmaFluxPlacementTests().select(run, player, tuple(bot.position))
        for _ in range(85):
            run.step({})
            # Give the Sniper's authored charge time to finish during the lift.
            if definition.hero == sniper.SNIPER and bot.position[1] - floor > 1:
                break
    x, y, z = bot.position
    yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
    run.step({player: (yaw, pitch, 0)})
    npc_control.start(run.match, player)
    effects = bot.body_script.component.instances
    airborne_at_engagement = _airborne_mover(bot) is not None and _airborne_mover(bot).airborne
    volleys = []
    original_shoot = fight._shoot

    def observe(shooter, *args, **kwargs):
        if shooter is bot:
            volleys.append({"height": bot.position[1] - floor,
                            "flux_running": bool(ability.flags & nodes.Ability.RUNNING)})
        return original_shoot(shooter, *args, **kwargs)

    with patch.object(fight, "_shoot", side_effect=observe):
        for _ in range(145):
            lift = next(
                (i.states.get(16) for i in effects.values() if i.graph.index == sigma_flux.FLUX_EFFECT),
                None,
            )
            pre_descent = bool(lift and lift.active)
            run.step({})
            if health.total < before:
                break
    pin = fight.pins.get(bot.entity) == player.body
    running = bool(ability.flags & nodes.Ability.RUNNING)
    retained = any(i.graph.index == sigma_flux.FLUX_EFFECT for i in effects.values())
    incoming_damage = before - health.total
    charge_after_contact = script.component.entity_var(342).value()
    npc_control.stop(run.match)
    for _ in range(245):
        run.step({})
    assassin = definition.hero == 0x02E00000000001BB
    heavy = definition.hero == 0x02E00000000001BA
    expected = 0 if early else 50 if heavy else 50 + npc_health.maximum / 2
    if assassin:
        if early:
            attack_passed = (not airborne_at_engagement and pin and not running and not retained
                             and incoming_damage == 25 and charge_after_contact < 10)
        else:
            attack_passed = airborne_at_engagement and not pin and incoming_damage == 0
    else:
        attack_passed = incoming_damage > 0 and running and bool(volleys) and volleys[0]["flux_running"] and (
            volleys[0]["height"] == 0 if heavy else airborne_at_engagement and volleys[0]["height"] > 1
        )
    landed = abs(bot.position[1] - floor) < 0.01 and _airborne_mover(bot) is None
    return {"scenario": "npc_primary_against_flux_early" if early else "npc_primary_against_flux",
            "incoming_damage": incoming_damage, "flux_damage": npc_before - npc_health.total,
            "airborne_at_engagement": airborne_at_engagement,
            "charge_after_contact": charge_after_contact,
            "expected_flux_damage": expected, "volleys": volleys, "pin": pin,
            "pre_descent": pre_descent, "retained_effect_after_contact": retained,
            "running_after_contact": running, "landed": landed,
            "passed": attack_passed and landed
            and abs(npc_before - npc_health.total - expected) < 1e-5}


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


def run_audit(catalog, graphs, body_xml, movers=None, *,
              include_barrier=False, include_flux_return=False,
              include_heavy_melee=False, include_heavy_charge=False, include_trooper_melee=False,
              include_sniper_retreat=False, include_enforcer_volley=False, include_accretion_recovery=False):
    barrier_model = None
    if include_barrier or include_heavy_melee or include_heavy_charge or include_trooper_melee or include_sniper_retreat:
        configured = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
        if not configured:
            raise ValueError("The Barrier audit requires ORPHEUS_SIGMA_BARRIER_MODEL")
        model = Path(configured)
        barrier_model = {"path": str(model), "sha256": hashlib.sha256(model.read_bytes()).hexdigest()}
    definitions = npc.load_catalog(catalog)
    table = dict(mover._movers())
    prepared = json.loads(movers.read_text(encoding="utf-8")) if movers else None
    mover_sources = {}
    for definition in definitions.values():
        path = body_xml / f"{definition.body & 0xFFFFFFFFFFFF:012X}.003.xml"
        record = mover_of(path.read_text(encoding="utf-8"))
        if record is None:
            raise ValueError(f"NPC mover component missing: {path}")
        xml_values = mover.MoverData(**record)
        if prepared is not None:
            entry = prepared.get(f"0x{definition.body:016X}")
            if entry is None:
                raise ValueError(f"Prepared NPC mover missing: {definition.body:016X}")
            values = mover.MoverData(**{key: value for key, value in entry.items()
                                       if key in asdict(xml_values)})
            if values != xml_values:
                raise ValueError(f"Prepared NPC mover differs from body XML: {path}")
        else:
            values = xml_values
        table[definition.body] = values
        mover_sources[definition.body] = {
            "body_xml": str(path), "body_xml_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "values": asdict(values), "prepared_matches_body_xml": prepared is not None,
        }
    rows = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(graph, "GRAPHS_PATH", graphs))
        stack.enter_context(patch.object(mover, "_movers", return_value=table))
        clear_graph_caches()
        try:
            for definition in definitions.values():
                results = [scenario(definition, name) for name in
                           ("primary", "airburst", "melee", "accretion", "miss", "ally", "wall")]
                results.extend(mixed_airburst(definition, mode) for mode in ("falloff", "covered", "ally", "outside"))
                try:
                    results.append(lethal_npc_return(definition))
                except Exception as error:  # noqa: BLE001 -- keep the rest of the NPC audit available
                    results.append({"scenario": "npc_lethal_return_and_fresh_sigma_body", "passed": False,
                                    "error": f"{type(error).__name__}: {error}"})
                try:
                    results.append(incoming(definition))
                except Exception as error:  # noqa: BLE001 -- retain per-NPC failure evidence in the audit
                    results.append({"scenario": "incoming_manual_authored_primary", "passed": False,
                                    "error": f"{type(error).__name__}: {error}"})
                for mode in ("cover", "range", "ally"):
                    try:
                        results.append(npc_primary_gate_recovery(definition, mode))
                    except Exception as error:  # noqa: BLE001 -- retain failure evidence for each attack gate
                        results.append({"scenario": "npc_primary_gate_release_and_recovery", "mode": mode,
                                        "passed": False, "error": f"{type(error).__name__}: {error}"})
                try:
                    results.append(incoming(definition, grasp=True))
                except Exception as error:  # noqa: BLE001 -- preserve missing defensive-interaction evidence
                    results.append({"scenario": "grasp_actual_npc_weapon_and_shield_conversion",
                                    "passed": False,
                                    "error": f"{type(error).__name__}: {error}"})
                try:
                    results.append(corpse(definition))
                except Exception as error:  # noqa: BLE001 -- preserve failed lifecycle evidence
                    results.append({"scenario": "primary_kill_retained_corpse", "passed": False,
                                    "error": f"{type(error).__name__}: {error}"})
                if include_barrier:
                    try:
                        results.append(incoming(definition, barrier=True))
                    except Exception as error:  # noqa: BLE001 -- retain NPC Barrier failure evidence
                        results.append({"scenario": "experimental_barrier_against_actual_npc_primary",
                                        "passed": False, "error": f"{type(error).__name__}: {error}"})
                if include_flux_return:
                    timings = (False, True) if definition.hero == 0x02E00000000001BB else (False,)
                    for early in timings:
                        try:
                            results.append(incoming_flux(definition, early=early))
                        except Exception as error:  # noqa: BLE001 -- preserve per-NPC failure evidence
                            results.append({"scenario": "npc_primary_against_flux", "early": early,
                                            "passed": False, "error": f"{type(error).__name__}: {error}"})
                if include_heavy_melee and definition.hero == heavy_assault.HERO:
                    for mode in ("open", "grasp", "barrier", "cancel"):
                        try:
                            results.append(incoming_npc_melee(definition, mode))
                        except Exception as error:  # noqa: BLE001 -- preserve failed special evidence
                            results.append({"scenario": "heavy_assault_authored_melee", "mode": mode,
                                            "passed": False, "error": f"{type(error).__name__}: {error}"})
                    results.append(npc_melee_cooldown(definition))
                    results.extend(npc_melee_targets(definition, mode)
                                   for mode in ("open", "cover", "projected", "phase"))
                    results.extend(npc_melee_vs_sigma_melee(definition, lethal=lethal) for lethal in (False, True))
                if include_trooper_melee and definition.hero == heavy_assault.TROOPER:
                    for mode in ("open", "grasp", "barrier", "cancel", "wall", "range", "accretion"):
                        try:
                            results.append(incoming_npc_melee(definition, mode))
                        except Exception as error:  # noqa: BLE001 -- preserve failed special evidence
                            results.append({"scenario": "trooper_authored_melee", "mode": mode,
                                            "passed": False, "error": f"{type(error).__name__}: {error}"})
                    results.append(npc_melee_cooldown(definition))
                    results.extend(npc_melee_targets(definition, mode)
                                   for mode in ("open", "cover", "projected", "phase"))
                    results.extend(npc_melee_vs_sigma_melee(definition, lethal=lethal) for lethal in (False, True))
                    if include_flux_return:
                        results.append(trooper_melee_during_flux(definition))
                if include_heavy_charge and definition.hero == heavy_assault.HERO:
                    for mode in ("open", "grasp", "barrier", "cancel", "wall", "accretion"):
                        try:
                            results.append(incoming_heavy_charge(definition, mode))
                        except Exception as error:  # noqa: BLE001 -- preserve failed special evidence
                            results.append({"scenario": "heavy_assault_authored_charge", "mode": mode,
                                            "passed": False, "error": f"{type(error).__name__}: {error}"})
                    results.append(heavy_charge_cooldown(definition))
                    results.extend(heavy_charge_carry(definition, mode)
                                   for mode in ("wall", "lethal_wall", "stop", "source_death", "accretion"))
                    results.append(heavy_charge_secondary(definition))
                    results.extend(heavy_charge_projected_barrier(definition, expires_during_approach=expires)
                                   for expires in (False, True))
                    results.extend(heavy_charge_terrain(definition, mode) for mode in ("low", "tall", "carried_low"))
                    if include_flux_return:
                        results.append(heavy_charge_during_flux(definition))
                if include_sniper_retreat and definition.hero == sniper.SNIPER:
                    for mode in ("open", "stop", "windup_stop", "wall", "accretion", "lethal", "barrier", "grasp"):
                        try:
                            results.append(sniper_retreat(definition, mode))
                        except Exception as error:  # noqa: BLE001 -- preserve failed special evidence
                            results.append({"scenario": "sniper_authored_retreat", "mode": mode,
                                            "passed": False, "error": f"{type(error).__name__}: {error}"})
                    results.append(sniper_retreat_cooldown(definition))
                    results.extend(sniper_retreat_search(definition, mode)
                                   for mode in ("retry", "ally", "los", "range", "return_fire"))
                    results.extend(sniper_retreat_sample(definition, blocked_center=blocked)
                                   for blocked in (False, True))
                    if include_flux_return:
                        results.append(sniper_retreat_during_flux(definition))
                if include_enforcer_volley and definition.hero == enforcer.HERO:
                    for mode in ("stop", "wall", "accretion"):
                        results.append(enforcer_volley_lifecycle(definition, mode))
                    for distance in (6, 11, 18, 90):
                        results.append(enforcer_volley_range(definition, distance))
                    results.append(enforcer_volley_range(definition, 18, late_stun=True))
                    results.append(enforcer_fractional_shield_recovery(definition))
                    results.append(enforcer_shield_recovery_across_tick_gap(definition))
                    results.append(enforcer_grasp_shield_spill(definition))
                    results.append(enforcer_kills_flux_preview(definition))
                    if include_barrier:
                        for mode in ("grasp_first", "barrier_first", "barrier_breaks"):
                            results.append(enforcer_defense_order(definition, mode))
                if include_accretion_recovery and definition.hero != 0x02E00000000001BB:
                    results.append(npc_attack_recovers_after_accretion(definition))
                    if definition.hero == heavy_assault.HERO:
                        results.append(npc_attack_recovers_after_accretion(definition, charge_after=True))
                rows.append({"name": definition.name, "hero": f"0x{definition.hero:016X}",
                             "body": f"0x{definition.body:016X}",
                             "mover_input": mover_sources[definition.body], "results": results})
        finally:
            clear_graph_caches()
    return {"hero": "Sigma", "static_acceptance_complete": False,
            "scope": "Actual NPC bodies/base pools/movers, flat collision, fake transport, permissive VM.",
            "source_path": str(ROOT), "catalog_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
            "prepared_movers": str(movers) if movers else None,
            "prepared_movers_sha256": hashlib.sha256(movers.read_bytes()).hexdigest() if movers else None,
            "barrier_model": barrier_model,
            "graphs_sha256": hashlib.sha256(graphs.read_bytes()).hexdigest(), "npcs": rows,
            "remaining": ["Remaining Barrier, Flux and Grasp interactions outside the executed cases",
                          "Full attack/status/cooldown/cancel and cross-hero matrix",
                          "NPC special abilities, target acquisition and native movement",
                          "Native equivalence of manual Assassin controller/contact/control/health clock",
                          "Original-client corpse and combat acceptance"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--graphs", type=Path, required=True)
    parser.add_argument("--body-xml", type=Path, required=True)
    parser.add_argument("--movers", type=Path,
                        help="Prepared mover table; require every NPC row to match its body XML")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--barrier", action="store_true",
                        help="Audit Experimental Barrier using ORPHEUS_SIGMA_BARRIER_MODEL")
    parser.add_argument("--flux-return", action="store_true", help="Audit manual NPC primary during Flux")
    parser.add_argument("--heavy-melee", action="store_true",
                        help="Audit Heavy Assault's authored close attack")
    parser.add_argument("--heavy-charge", action="store_true",
                        help="Audit Heavy Assault's authored charge")
    parser.add_argument("--trooper-melee", action="store_true",
                        help="Audit Trooper's authored close attack")
    parser.add_argument("--sniper-retreat", action="store_true",
                        help="Audit Sniper's authored MoveTo with reconstructed retreat selection")
    parser.add_argument("--enforcer-volley", action="store_true",
                        help="Audit the Enforcer projectile volley lifecycle")
    parser.add_argument("--accretion-recovery", action="store_true",
                        help="Audit ranged NPC recovery from Accretion")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("audit report must stay outside the source repository")
    result = run_audit(args.catalog, args.graphs, args.body_xml, args.movers,
                       include_barrier=args.barrier, include_flux_return=args.flux_return,
                       include_heavy_melee=args.heavy_melee, include_heavy_charge=args.heavy_charge,
                       include_trooper_melee=args.trooper_melee, include_sniper_retreat=args.sniper_retreat,
                       include_enforcer_volley=args.enforcer_volley,
                       include_accretion_recovery=args.accretion_recovery)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for row in result["npcs"]:
        passed = sum(item["passed"] for item in row["results"])
        print(f"{row['name']}: {passed}/{len(row['results'])} limited scenarios passed")
    print("Full static acceptance remains OPEN:", args.output)
    if any(not scenario["passed"] for row in result["npcs"] for scenario in row["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
