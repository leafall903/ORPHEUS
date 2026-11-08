"""Small resource-state adapters for combat; not the full native defense engine.

The three mapped skill graphs share zero-delay54FEB21C and authored true #10467e.
Their invulnerability and ghost/solid collision distinction are reconstructed
from the target skill behavior. No global meaning is assigned to either token.
"""

import math

from ow174.game import bots
from ow174.game.script import expr

# Hero GUID -> (authored skill graph, projectile-intangible). No NPC inference.
PHASES = {
    0x02E0000000000002: (0x0020, True),  # Reaper: Wraith Form
    0x02E00000000001A2: (0x0F7C, True),  # Moira: Fade
    0x02E00000000000DD: (0x05FE, False),  # Mei: Cryo-Freeze
}


def phase(owner) -> tuple[bool, bool]:
    """(immune, intangible) from live authored state, never a cached timer."""
    if isinstance(owner, bots.Bot):
        return False, False
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
    """Reconstructed immediate entry, only for three known human skill graphs.

    Leaving54FEB21C as an endless plain state prevents its authored continuation
    from starting the real duration Wait/ChaseVar. This supports only the zero
    entry configuration; it does not infer general54FEB21C/native semantics.
    """
    component = state.instance.component
    spec = PHASES.get(component.world.hero)
    if component.strict or spec is None or spec[0] != state.instance.graph.index:
        return False
    if any(state.node.field(name) is not None for name in (
            "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5")):
        return False
    cfg = state.node.field("m_ED97A97C")
    return cfg is None or state.evaluate(cfg, watch=False) == 0


def _box_hit(origin, direction, low, high, reach):
    """First segment entry into an authored local box (reconstructed placement)."""
    enter, leave = 0.0, reach
    for p, v, a, b in zip(origin, direction, low, high, strict=True):
        a, b = min(a, b), max(a, b)
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


def projectile_gate(origin, direction, reach, targets):
    """Nearest mapped Genji/Sigma projectile defense; no fabricated barrier.

    Boxes are evaluated from the active skill node and placed at the body eye,
    oriented by body yaw. Mode0 redirects to defender aim; mode1 consumes. This
    placement/mode policy is reconstructed; native pitch/filter/muzzle and Sigma
    shield conversion/transport remain separate open services.
    """
    best = None
    supported = {0x02E0000000000029: (0x03A3, 0), 0x02E000000000023B: (0x1D02, 1)}
    for target in targets:
        owner = target.owner
        if isinstance(owner, bots.Bot):
            continue
        spec = supported.get(owner.hero.guid)
        script = owner.body_script
        if spec is None or script is None:
            continue
        yaw = owner.mover.yaw * 2 * math.pi / 65536
        sine, cosine = math.sin(yaw), math.cos(yaw)
        x, y, z = target.feet
        delta = (origin[0] - x, origin[1] - y - target.eye, origin[2] - z)

        def local(v, cosine=cosine, sine=sine):
            return (cosine * v[0] - sine * v[2], v[1], sine * v[0] + cosine * v[2])

        local_origin, local_direction = local(delta), local(direction)
        for instance in script.component.instances.values():
            if instance.graph.index != spec[0]:
                continue
            for state in instance.states.values():
                if (not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles"
                        or state.node.field("m_8B251D53") != spec[1]):
                    continue
                for box in state.node.field("m_boxes") or []:
                    low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                    high = state.evaluate(box.get("m_AEF67365"), watch=False)
                    if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                        continue
                    distance = _box_hit(local_origin, local_direction, low, high, reach)
                    if distance is not None and (best is None or distance < best[0]):
                        best = (distance, target, spec[1])
    return best
