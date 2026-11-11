"""Scoped target and completion queries for Sombra's ordinary Hack."""

from ow174.game import ball, barriers, sombra_emp
from ow174.game.script import expr

HACK = sombra_emp.ORDINARY_HACK


def track_targets(world, state):
    instance = state.instance
    if (world.hero != sombra_emp.SOMBRA or instance.graph.index != HACK
            or state.node.pos not in (50, 52, 68) or world.match is None):
        return False
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    target = selected_body(world, reach)
    instance.write(expr.lvalue(state.node.field("m_8A2905A0")), (expr.Entity(target),) if target else ())
    return True


def selected_body(world, reach):
    from ow174.game.combat import aim, ray_capsule, ray_world

    fight = world.match.combat
    caster = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    selected = 0
    if caster is not None and barriers._recipient_ready(fight, caster):
        origin = fight._eye(caster)
        direction = aim(caster.mover.yaw, caster.mover.pitch)
        wall = ray_world(fight._collision(), origin, direction, reach)
        closest = reach if wall is None else wall
        for target in fight._enemies(caster):
            if target.owner not in world.match.players or target.owner.hero.guid != ball.HERO:
                continue
            a, b = target.segment()
            distance = ray_capsule(origin, direction, a, b, target.radius)
            if distance is not None and distance < closest:
                selected = target.entity
                closest = distance
        if selected:
            if barriers.ray_hit(fight, caster, origin, direction, closest) is not None:
                return 0
            if next(barriers.projected_on(fight, selected), None) is not None:
                return 0
    return selected


def native(world, cfg, instance):
    if world.match is None or instance is None or instance.component.strict:
        return NotImplemented
    index = instance.graph.index
    if (world.hero == sombra_emp.SOMBRA and index == HACK
            and cfg is instance.graph.node(204).field("m_condition")):
        body = expr.to_entity(instance.evaluate(cfg["m_entity"]))
        search = instance.state(25)
        reach = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
        return bool(body and selected_body(world, reach) == body)
    if world.hero == ball.HERO and index == sombra_emp.EFFECT:
        emp_flag = instance.vars.get(10409)
        if emp_flag is None or expr.truthy(emp_flag.value()):
            return NotImplemented
        fight = world.match.combat
        if cfg is instance.graph.node(21).field("m_condition"):
            return any(p.body == world.body and p.body_script is not None
                       and p.body_script.component is instance.component
                       and barriers._recipient_ready(fight, p) for p in world.match.players)
        if cfg is instance.graph.node(19).field("m_condition"):
            source = expr.to_entity(instance.evaluate(cfg["m_entity"]))
            return any(p.body == source and p.has_body and not p.gone for p in world.match.players)
    return NotImplemented
