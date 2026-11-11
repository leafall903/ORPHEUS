"""Small resource-state adapters for combat; not the full native defense engine.

The three mapped skill graphs share zero-delay54FEB21C and authored true #10467e.
Their invulnerability and ghost/solid collision distinction are reconstructed
from the target skill behavior. No global meaning is assigned to either token.
"""

import math

from ow174.game import bots, dva_matrix
from ow174.game.script import expr

# Hero GUID -> (authored skill graph, projectile-intangible). No NPC inference.
PHASES = {
    0x02E0000000000002: (0x0020, True),  # Reaper: Wraith Form
    0x02E00000000001A2: (0x0F7C, True),  # Moira: Fade
    0x02E00000000000DD: (0x05FE, False),  # Mei: Cryo-Freeze
}

# FirstPerson standing and crouched offsets from the selected body resources.
DEFENSE_EYE_HEIGHTS = {
    0x0400000000000225: (1.2, .84),  # Genji
    0x0400000000002AB1: (2.1, 1.75),  # Sigma
    dva_matrix.MECH: (1.74, 1.74),
}


def defense_eye(owner):
    mover = owner.mover
    x, y, z = mover.position
    height = expr.f32(DEFENSE_EYE_HEIGHTS[owner.hero.body][mover.crouched])
    return x, expr.f32(y + expr.f32(height + mover.spring)), z


def _aimed_box_axes(yaw, pitch):
    yaw *= math.tau / 65536
    # C5E040 evaluates sin/cos after scaling the signed pitch word in float32.
    pitch = expr.f32(pitch * expr.f32(math.tau / 65536))
    sine, cosine = math.sin(yaw), math.cos(yaw)
    sp, cp = math.sin(pitch), math.cos(pitch)
    # 5F3BB0 builds right from world-up cross forward, including at the poles.
    side = math.copysign(1.0, cp)
    return ((side * cosine, 0, -side * sine),
            (side * sp * sine, abs(cp), side * sp * cosine),
            (cp * sine, -sp, cp * cosine))


def fortify_instance(owner):
    """Resolve only the current Orisa player's authored Fortify graph."""
    if isinstance(owner, bots.Bot):
        return None
    script = getattr(owner, "body_script", None)
    if script is None or script.hero != 0x02E000000000013E or script.component.owner_dead:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == 0x0CF4), None)


def fortified(owner) -> bool:
    instance = fortify_instance(owner)
    if instance is None:
        return False
    control = instance.component.entity_var(9161, create=False)
    return (control is not None and expr.truthy(control.value()) and any(
        state.active and state.node.cls == "STUStatescriptStateStack"
        and expr.lvalue(state.node.field("m_out_Var")) == (1, 9161)
        for state in instance.states.values()
    ))


def fortify_scale(owner) -> float:
    instance = fortify_instance(owner)
    state = instance.states.get(4) if instance is not None else None
    if state is None or not state.active:
        return 1.0
    reduction = state.evaluate(state.node.field("m_50530EAB"), watch=False)
    return max(0.0, min(1.0, 1.0 + expr.to_float(reduction)))


def transcendence(owner) -> bool:
    return transcendence_state(owner) is not None


def transcendence_state(owner):
    """The active 017B state 3 on the current Zenyatta body."""
    if getattr(getattr(owner, "hero", None), "guid", None) != 0x02E0000000000020:
        return None
    script = getattr(owner, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return None
    return next((state for instance in script.component.instances.values()
                 if instance.graph.index == 0x017B and (state := instance.states.get(3)) is not None
                 and state.active), None)


def phase(owner) -> tuple[bool, bool]:
    """(immune, intangible) from live authored state, never a cached timer."""
    if isinstance(owner, bots.Bot):
        return False, False
    if transcendence(owner):
        return True, False
    spec = PHASES.get(getattr(getattr(owner, "hero", None), "guid", None))
    script = getattr(owner, "body_script", None)
    if spec is None or script is None or script.component.owner_dead:
        return False, False
    variable = script.component.entity_var(10467, create=False)
    if variable is None or not variable.value():
        return False, False
    index, intangible = spec
    active = any(
        state.active and state.node.cls == "STUStatescriptStateStack"
        and expr.lvalue(state.node.field("m_out_Var")) == (1, 10467)
        for instance in script.component.instances.values() if instance.graph.index == index
        for state in instance.states.values()
    )
    return active, active and intangible


def zero_delay_phase_entry(state) -> bool:
    """Reconstructed immediate entry for the scoped human skill graphs.

    Leaving54FEB21C as an endless plain state prevents its authored continuation
    from starting the real duration Wait/ChaseVar. This supports only the zero
    entry configuration; it does not infer general54FEB21C/native semantics.
    """
    component = state.instance.component
    spec = PHASES.get(component.world.hero)
    zen = (component.world.hero == 0x02E0000000000020
           and state.instance.graph.index == 0x017B and state.node.pos == 26)
    if component.strict or (not zen and (spec is None or spec[0] != state.instance.graph.index)):
        return False
    if any(state.node.field(name) is not None for name in (
            "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5")):
        return False
    cfg = state.node.field("m_ED97A97C")
    return cfg is None or state.evaluate(cfg, watch=False) == 0


def _box_hit(origin, direction, low, high, reach, projectile_extent):
    """First segment entry into the expanded local box."""
    enter, leave = 0.0, reach
    for p, v, a, b in zip(origin, direction, low, high, strict=True):
        a, b = min(a, b) - projectile_extent, max(a, b) + projectile_extent
        if abs(v) < 1e-9:
            if p < a or p > b:
                return None
            continue
        near, far = (a - p) / v, (b - p) / v
        enter = max(enter, min(near, far))
        leave = min(leave, max(near, far))
        if enter > leave:
            return None
    return enter


def deflects_melee(owner, incoming) -> bool:
    """Use Genji's active Deflect cone after melee reaches his capsule."""
    from ow174.game.combat import aim

    if getattr(getattr(owner, "hero", None), "guid", None) != 0x02E0000000000029:
        return False
    script = owner.body_script
    if script is None:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != 0x03A3:
            continue
        for state in instance.states.values():
            if (state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"
                    and state.node.field("m_8B251D53") == 0):
                config = state.node.field("m_025D0C39")
                if config is None:
                    continue
                angle = expr.to_float(state.evaluate(config, watch=False))
                facing = aim(owner.mover.yaw, owner.mover.pitch)
                alignment = -sum(a * b for a, b in zip(facing, incoming, strict=True))
                return alignment > math.cos(math.radians(angle))
    return False


def projectile_gate(origin, direction, reach, targets, *, matrix_eligible=False, projectile_extent=0.0):
    """Nearest mapped Genji, Sigma or D.Va projectile defense.

    The mapped states opt into the aimed eye transform through m_ED96CD23.
    Mode 0 redirects, mode 1 accumulates Grasp shield credit, and mode 2
    consumes. The sweep tests the evaluated boxes with the projectile extent.
    """
    best = None
    supported = {0x02E0000000000029: (0x03A3, 0), 0x02E000000000023B: (0x1D02, 1)}
    for target in targets:
        owner = target.owner
        if isinstance(owner, bots.Bot):
            continue
        spec = supported.get(owner.hero.guid)
        script = owner.body_script
        matrix = matrix_eligible and owner.hero.guid == dva_matrix.HERO and owner.hero.body == dva_matrix.MECH
        if matrix:
            spec = dva_matrix.MATRIX, 2
        if spec is None or script is None or owner.hero.body not in DEFENSE_EYE_HEIGHTS:
            continue
        for instance in script.component.instances.values():
            if instance.graph.index != spec[0]:
                continue
            for state in instance.states.values():
                if (not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles"
                        or state.node.field("m_8B251D53") != min(spec[1], 1)
                        or not state.node.field("m_ED96CD23")
                        or (matrix and state.index != 3)):
                    continue
                axes = _aimed_box_axes(owner.mover.yaw, owner.mover.pitch)
                x, y, z = defense_eye(owner)
                delta = origin[0] - x, origin[1] - y, origin[2] - z

                def local(v):
                    return tuple(a * v[0] + b * v[1] + c * v[2] for a, b, c in axes)

                local_origin, local_direction = local(delta), local(direction)
                for box in state.node.field("m_boxes") or []:
                    low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                    high = state.evaluate(box.get("m_AEF67365"), watch=False)
                    if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                        continue
                    distance = _box_hit(local_origin, local_direction, low, high, reach, projectile_extent)
                    if distance is not None and (best is None or distance < best[0]):
                        best = (distance, target, spec[1], state)
    return best
