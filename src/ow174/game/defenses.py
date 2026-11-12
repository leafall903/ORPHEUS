"""Scoped reconstructed consumers of authored defensive ability lifetimes.

The ordinary VM owns activation, expiry and retirement. These combat policies
are not a general native CombatModFilter implementation or client acceptance.
The control host also treats active Fortify and Transcendence as stun-immune;
this is a scoped reconstruction, not native filter/tag dispatch.
"""

import math

from ow174.game.script import expr

ANA = 0x02E000000000013B
DVA, DVA_MECH = 0x02E000000000007A, 0x0400000000000407
GENJI = 0x02E0000000000029
MEI = 0x02E00000000000DD
ORISA = 0x02E000000000013E
REINHARDT = 0x02E0000000000007
REAPER = 0x02E0000000000002
SIGMA = 0x02E000000000023B
SOLDIER = 0x02E000000000006E
ZARYA = 0x02E0000000000068
ZENYATTA = 0x02E0000000000020
WORLD_CLOCK_HEROES = (REAPER, MEI, ZARYA, ORISA, ZENYATTA, GENJI, SIGMA, REINHARDT)
HAND_CANNON = 0x0B90000000000109
GRASP_GRAPH = 0x1D02
REINHARDT_BARRIER_GRAPH = 0x0095
SOLDIER_COOLDOWN_STATES = {0x0043: 6, 0x0254: 31, 0x0257: 0}  # Melee, Helix, Field.


def uses_world_clock(owner):
    hero = owner.hero
    return hero.guid in WORLD_CLOCK_HEROES or hero.body == DVA_MECH


def active_state(owner, hero, graph, number):
    identity = getattr(owner, "hero", None)
    if getattr(identity, "guid", identity) != hero:
        return None
    script = getattr(owner, "body_script", None)
    if script is None or getattr(script, "retired", False) or script.component.owner_dead:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == graph:
            state = instance.states.get(number)
            if state is not None and state.active:
                return state
    return None


def soldier_field_windup(owner):
    return active_state(owner, SOLDIER, 0x0257, 4)  # node8 wait before the canister volley


def ana_sleep_dart_windup(owner):
    return active_state(owner, ANA, 0x0BDD, 7)  # node15 wait before the dart volley


def soldier_sprint_running(owner):
    return active_state(owner, SOLDIER, 0x0255, 0) is not None  # node2 movement modifier


def soldier_input_recovery_active(owner):
    if soldier_sprint_running(owner):
        return True
    script = getattr(owner, "body_script", None)
    if script is None or script.hero != SOLDIER or script.retired or script.component.owner_dead:
        return False
    for instance in script.component.instances.values():
        if not instance.initialized or instance.stopped:
            continue
        graph = instance.graph.index
        cooldown = instance.states.get(SOLDIER_COOLDOWN_STATES.get(graph))
        if cooldown is not None and cooldown.active and cooldown.remaining() > 0:
            return True
        # The rifle retains #51 through recovery after node95 refills its magazine.
        if graph == 0x0254 and (reload := instance.vars.get(51)) is not None and expr.truthy(reload.value()):
            return True
    return False


def soldier_visor_running(owner) -> bool:
    return (active_state(owner, SOLDIER, 0x0259, 43) is not None  # startup wait
            or active_state(owner, SOLDIER, 0x0259, 6) is not None)  # active countdown


def fortify_state(owner):
    return active_state(owner, ORISA, 0x0CF4, 4)


def deflect_state(owner):
    return active_state(owner, GENJI, 0x03A3, 10)


def grasp_state(owner):
    return active_state(owner, SIGMA, GRASP_GRAPH, 1)


def matrix_state(owner):
    if getattr(getattr(owner, "hero", None), "body", None) != DVA_MECH:
        return None
    state = active_state(owner, DVA, 0x02CC, 3)
    return state if state is not None and state.node.cls == "STUStatescriptStateDeflectProjectiles" else None


def reinhardt_barrier_state(owner):
    return active_state(owner, REINHARDT, REINHARDT_BARRIER_GRAPH, 17)


def fortify_scale(owner):
    state = fortify_state(owner)
    if state is None:
        return None
    value = state.evaluate(state.node.field("m_50530EAB"), watch=False)
    if expr.kind(value) not in ("int", "float") or not math.isfinite(value):
        return None
    return max(0.0, min(1.0, 1.0 + value))


def knockback_scale(owner):
    """Live authored movement modifier26, using the mover's ordered operations.

    Reading current script states at impact is a host timing policy. This does
    not recover native force/mass/profile conversion or general control immunity.
    """
    from ow174.game.mover import apply_op, statescript_mods

    script = getattr(owner, "body_script", None)
    if script is None:
        return 1.0
    component = script.component
    entries = []
    for key, (index, op, value, priority) in statescript_mods(component).items():
        if index == 26:
            instance, state, _ = key
            start = component.instances[instance].states[state].start
            entries.append((priority, start, key, op, value))
    result = 1.0
    for _, _, _, op, value in sorted(entries):
        result = apply_op(op, result, value)
    return max(0.0, result) if math.isfinite(result) else 0.0


def intangible(owner):
    return active_state(owner, REAPER, 0x0020, 3) is not None


def cryo_protected(owner):
    state = active_state(owner, MEI, 0x05FE, 17)
    return (state is not None and state.node.cls == "STU_D07EBF01"
            and state.node.field("m_target") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0})


def cryo_capsule(player):
    """Delivered090B cover shape over the combined server's Cryo lifetime."""
    from ow174.game.script import nodes

    if not player.has_body or not player.spawned or player.gone or player.client is None:
        return None
    phase = active_state(player, MEI, 0x05FE, 6)
    if phase is None or phase.ending:
        return None
    script = player.body_script
    ability = phase.instance.states.get(0)
    if (script.component.fault is not None or script.component.entity != player.body
            or not isinstance(ability, nodes.Ability) or not ability.flags & nodes.Ability.RUNNING):
        return None
    x, y, z = player.mover.position
    return (x, y + .5, z), (x, y + 1.5, z), .5


def cryo_blockers(players, *, exclude=None):
    return tuple(capsule for player in players if player is not exclude
                 and (capsule := cryo_capsule(player)) is not None)


def cryo_healing_sample(owner):
    """Cumulative self-healing from Mei 05FE/state13's authored amount and duration."""
    if getattr(getattr(owner, "hero", None), "guid", None) != MEI:
        return None
    script = owner.body_script
    if script is None or script.retired or script.component.owner_dead:
        return None
    instances = script.component.instances.values()
    instance = next((item for item in instances if item.graph.index == 0x05FE), None)
    state = instance.states.get(13) if instance is not None else None
    if state is None or not state.counter:
        return None
    node = state.node
    health = node.field("m_modifyHealth")
    if (node.cls != "STUStatescriptStateModifyHealth" or not health or node.field("m_DD792FF0") != 3
            or node.field("m_FA3ECC43") != {"$": "STU_B5A0CAF0", "m_EE729DCB": 0}):
        return None
    amount = state.evaluate(health["m_amount"], watch=False)
    seconds = state.evaluate(node.field("m_95F1FB0B"), watch=False)
    if (expr.kind(amount) not in ("int", "float") or expr.kind(seconds) not in ("int", "float")
            or not math.isfinite(amount) or not math.isfinite(seconds) or amount <= 0 or seconds <= 0):
        return None
    duration = round(seconds * 1000)
    if duration <= 0:
        return None
    end = instance.component.now if state.active else state.ended_at
    elapsed = max(0, min(duration, end - state.start))
    return (instance.id, state.counter, state.start), amount * elapsed / duration


def timed_protection(owner):
    return intangible(owner) or cryo_protected(owner)


def call_mech_immune(actor):
    """The delivered owner-only026A entry/CombatModFilter intersection."""
    script = getattr(actor, "body_script", None)
    if getattr(script, "hero", None) != DVA:
        return False
    component = script.component
    if script.retired or component.owner_dead or component.fault is not None:
        return False
    for instance in component.instances.values():
        if instance.graph.index != 0x026A:
            continue
        entry = instance.states.get(12)
        state = instance.states.get(24)
        if (entry is not None and entry.active and not entry.ending
                and state is not None and state.active and not state.ending
                and state.node.cls == "STU_D07EBF01"
                and state.node.field("m_target") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0}):
            return True
    return False


def invulnerable(owner):
    return (timed_protection(owner) or call_mech_immune(owner)
            or active_state(owner, ZENYATTA, 0x017B, 3) is not None)


def protection(owner):
    """Retain the delivered protection query over combined defensive scopes."""
    from ow174.game import reaper

    if reaper.intangible(owner):
        return "intangible"
    return "immune" if invulnerable(owner) else None


def projectile_protection_active(owner):
    return (invulnerable(owner) or fortify_state(owner) is not None
            or deflect_state(owner) is not None or grasp_state(owner) is not None
            or matrix_state(owner) is not None or reinhardt_barrier_state(owner) is not None)


def advance_entry(owner):
    """Complete the authored zero-duration entry callbacks for these defenses."""
    state = (active_state(owner, ZENYATTA, 0x017B, 13)
             or active_state(owner, REAPER, 0x0020, 1)
             or active_state(owner, MEI, 0x05FE, 41))

    if state is None or state.node.cls != "STU_54FEB21C":
        return
    duration = state.node.field("m_ED97A97C")
    if duration is not None and state.evaluate(duration, watch=False) != 0:
        raise ValueError("unsupported defensive entry duration")
    state.request_finish()
    state.instance.update()
