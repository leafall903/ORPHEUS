"""Server services for Genji's authored combat states."""

import math

from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000029
STRIKE = 0x01DB
BLADE = 0x0317
DEFLECT = 0x03A3


class _MeleeContext(runtime.InstanceContext):
    def __init__(self, instance, target, source, forward):
        super().__init__(instance, None)
        self.target, self.source, self.forward = target, source, forward

    def parameter_result(self, var, *, initial=None):
        if var == 8:
            return True, expr.Vec3(*self.source)
        return super().parameter_result(var, initial=initial)

    def position(self, entity):
        if entity == self.target.entity:
            return expr.Vec3(*self.target.feet)
        return super().position(entity)

    def native(self, cfg):
        if (cfg.get("$") == "STU_021D01CD"
                and expr.evaluate(cfg.get("m_entity"), self) == expr.Entity(self.target.entity)):
            return expr.Vec3(*self.forward)
        return super().native(cfg)


def parries_melee(fight, target, source, settings):
    """Apply the live Deflect modifier to matching melee tags and source position."""
    from ow174.game import combat

    defender = target.owner
    if getattr(defender.hero, "guid", None) != HERO or fight.is_stunned(defender):
        return False
    script = defender.body_script
    if script is None or script.entity != target.entity or script.component.strict or script.component.owner_dead:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != DEFLECT:
            continue
        node = instance.graph.node(107)
        state = instance.states.get(node.state) if node is not None else None
        clock = instance.states.get(1)
        now = fight.tick * combat.TICK * 1000
        if (state is None or not state.active or state.ending or clock is None or not clock.active
                or not state.start <= now < clock.start + clock.ms):
            continue
        included = {graph.guid(tag) for tag in node.field("m_F60AA143") or ()}
        included.discard(None)
        tags = {graph.guid((tag.get("m_EE3055BD") or {}).get("m_identifier"))
                for tag in settings.get("m_148F3152") or ()}
        if (graph.guid(node.field("m_60703B1E")) != 0x0D80000000000318
                or not included.intersection(tags)
                or expr.to_float(state.evaluate(node.field("m_50530EAB"), watch=False)) != 0):
            continue
        condition = node.field("m_6AA5D70F.m_condition")
        forward = combat.aim(defender.mover.yaw, defender.mover.pitch)
        return expr.truthy(expr.evaluate(condition, _MeleeContext(instance, target, source, forward)))
    return False


def shuriken_fan(player, state, direction, yaw):
    node = state.node
    if player.hero.guid != HERO or (state.instance.graph.index, node.pos) != (0x0304, 29):
        return ()
    launch = node.field("m_5EC1EB38")
    if not launch or launch.get("$") != "STU_1145C42E":
        return ()
    count = expr.to_int(state.evaluate(node.field("m_numProjectilesPerShot"), watch=False))
    spread = expr.to_float(state.evaluate(launch["m_spread"], watch=False))
    if count != 3 or not math.isfinite(spread) or not 0 < spread < 90:
        return ()
    # Interpret the horizontal spread as degrees on either side of the aim.
    angle = math.radians(spread)
    axial, lateral = math.cos(angle), math.sin(angle)
    heading = math.tau * yaw / 65536
    right = (math.cos(heading), 0.0, -math.sin(heading))
    return tuple(tuple(a * axial + sign * b * lateral
                       for a, b in zip(direction, right, strict=True)) for sign in (-1, 1))


def strike_query(world, cfg, instance):
    kind = cfg.get("$")
    if (world.hero != HERO or world.match is None or instance is None or instance.component.strict
            or instance.graph.index != STRIKE):
        return NotImplemented
    if kind == "STU_AC829876":
        signature = {k: v for k, v in (cfg.get("m_filter") or {}).items() if v not in (None, 0)}
        if signature != {"$": "STUConfigVarFilter", "m_84065FD1": 1}:
            return NotImplemented
        target = instance.evaluate(cfg.get("m_entity"))
        if not isinstance(target, expr.Entity) or target.id == world.body:
            return False
        return any(p.has_body and p.body == target.id for p in world.match.players) or any(
            b.entity == target.id for b in world.match.bots
        )
    if (kind not in ("STU_21A13145", "STU_2200A66D")
            or instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body)):
        return NotImplemented
    owner = next((p for p in world.match.players if p.body == world.body and p.body_script is not None), None)
    if owner is None:
        return NotImplemented
    from ow174.game import combat

    value = (world.match.combat._eye(owner) if kind == "STU_21A13145"
             else combat.aim(owner.mover.yaw, owner.mover.pitch))
    return expr.Vec3(*value)


def reset_strike(player, victim_body):
    script = player.body_script
    if (player.hero.guid != HERO or script is None or script.entity != player.body
            or script.retired or script.component.strict):
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == STRIKE), None)
    if instance is not None and runtime.send_instance_message(
        instance, 0x0240000000000093, {0x15: expr.Entity(victim_body)}, player.body,
    ):
        script.component.run_now()
