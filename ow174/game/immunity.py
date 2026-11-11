"""Scoped consumers of authored Wraith, Fortify and Transcendence states.

DATA: graph0020/st1's zero startup parameter; authored main Wait3s, exit0.4s,
cooldown8s and active registered phase st0. RECONSTRUCTED: completing the missing
zero-parameter startup service synchronously, phase damage/control immunity and
projectile pass-through. Fortify and Transcendence use their live resource states.
Native startup, presentation, filters, stacking and cleanse remain open.
"""

import math

from ow174.game.script import expr

ORISA = 0x02E000000000013E
ZENYATTA = 0x02E0000000000020
TRANSCENDENCE = 0x017B


def _active_state(owner, hero, graph, index, entity=None):
    if getattr(getattr(owner, "hero", None), "guid", None) != hero:
        return None
    script = getattr(owner, "body_script", None)
    if (script is None or script.retired or script.component.owner_dead
            or (entity is not None and script.component.entity != entity)):
        return None
    for instance in script.component.instances.values():
        state = instance.states.get(index) if instance.graph.index == graph else None
        if state is not None and state.active:
            if hero == ZENYATTA:
                match = getattr(script.world, "match", None)
                service = getattr(getattr(match, "combat", None), "zenyatta", None)
                if service is not None and service.stale_ultimate_flag(owner):
                    return None
            return state
    return None


def damage_scale(owner, entity):
    if _active_state(owner, ZENYATTA, TRANSCENDENCE, 3, entity) is not None:
        return 0.0
    fortify = _active_state(owner, ORISA, 0x0CF4, 4, entity)
    if fortify is not None:
        scalar = fortify.evaluate(fortify.node.field("m_50530EAB"), watch=False)
        if type(scalar) in (int, float) and math.isfinite(scalar):
            return max(0.0, min(1.0, 1.0 + scalar))
    return 1.0


def fortified(owner, entity=None):
    return _active_state(owner, ORISA, 0x0CF4, 4, entity) is not None


def control_immune(owner, entity=None):
    return (wraith_active(owner, entity)
            or fortified(owner, entity)
            or _active_state(owner, ZENYATTA, TRANSCENDENCE, 3, entity) is not None)


def _transcendence_owner(world, state):
    if (world.match is None or world.match.ended or world.hero != ZENYATTA
            or state.instance.component.strict or state.instance.graph.index != TRANSCENDENCE):
        return None
    return next((owner for owner in world.match.players
                 if owner.body == world.body and owner.spawned and not owner.gone
                 and owner.body_script is not None and not owner.body_script.retired
                 and owner.body_script.component is state.instance.component
                 and not owner.body_script.component.owner_dead), None)


def transcendence_targets(world, state):
    owner = _transcendence_owner(world, state)
    if owner is None or state.index != 0:
        return NotImplemented
    from ow174.game import combat

    fight = world.match.combat
    if world.match.game_map.free_for_all:
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    origin = fight._eye(owner)
    targets = []
    for target in fight._targets():
        if target.owner is owner or target.team != owner.team or fight.vitals_of(target).dead:
            continue
        a, b = target.segment()
        if (combat.segment_distance(origin, a, b) - target.radius <= radius
                and combat.capsule_visible(world.match.collision_world, origin, a, b, target.radius)):
            targets.append(expr.Entity(target.entity))
    return tuple(targets)


def transcendence_heal(world, state, elapsed_ms):
    owner = _transcendence_owner(world, state)
    if owner is None or state.index != 6:
        return NotImplemented
    if not elapsed_ms:
        return True
    duration = expr.to_float(state.evaluate(state.node.field("m_95F1FB0B"), watch=False))
    config = state.node.field("m_modifyHealth") or {}
    total = expr.to_float(state.evaluate(config.get("m_amount"), watch=False))
    selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if duration > 0 and total > 0 and isinstance(selected, tuple):
        bodies = {expr.to_entity(value) for value in selected}
        fight = world.match.combat
        for target in fight._targets():
            if (target.entity in bodies and target.owner is not owner and target.team == owner.team
                    and not world.match.game_map.free_for_all):
                fight._heal_target(owner, target, total * elapsed_ms / (duration * 1000))
    return True


def advance_startup(component):
    """Complete scoped zero-duration startup services and let the graphs own lifetime."""
    for instance in tuple(component.instances.values()):
        if instance.graph.index == TRANSCENDENCE and component.world.hero == ZENYATTA:
            startup = instance.states.get(13)
            if (startup is not None and startup.active and startup.node.cls == 'STU_54FEB21C'
                    and startup.evaluate(startup.node.field('m_ED97A97C'), watch=False) == 0
                    and all(startup.node.field(name) is None for name in (
                        "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5",
                    ))):
                startup.request_finish()
                component.run_now()
            continue
        if instance.graph.index != 0x20:
            continue
        ability, startup = instance.states.get(10), instance.states.get(1)
        if (ability is None or not ability.flags & 1 or startup is None or not startup.active
                or startup.node.cls != 'STU_54FEB21C'):
            continue
        if startup.evaluate(startup.node.field('m_ED97A97C'), watch=False) == 0:
            startup.request_finish()
            component.run_now()


def wraith_active(owner, entity=None):
    script = getattr(owner, 'body_script', None)
    component = getattr(script, 'component', None)
    if (component is None or component.owner_dead
            or (entity is not None and component.entity != entity)):
        return False
    for instance in component.instances.values():
        if instance.graph.index != 0x20:
            continue
        ability, phase = instance.states.get(10), instance.states.get(0)
        if ability is not None and ability.flags & 1 and phase is not None and phase.active:
            return True
    return False
