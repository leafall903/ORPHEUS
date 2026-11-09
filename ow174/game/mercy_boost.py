"""Scoped Mercy staff boost consumed at Zenyatta projectile launch.

The beam selection and combat multiplier are reconstructed. The 006B state
and 006C effect parameter and filter are executed from their authored graphs.
"""
import math

from ow174.game.script import expr, graph, message_receive, runtime

HERO = 0x02E0000000000004
BOOST = 0x04A0000000000016
START = 0x024000000000000F


def targets(world, state):
    if (world.hero != HERO or world.match is None
            or state.instance.graph.index != 0x006B or state.index not in (4, 5, 51)):
        return None
    from ow174.game import combat

    fight = world.match.combat
    player = next((p for p in world.match.players if p.body == world.body and p.has_body), None)
    if player is None or fight.is_dead(player) or world.match.game_map.free_for_all:
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    angle = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False), 360)
    direction = combat.aim(player.mover.yaw, player.mover.pitch)
    origin = fight._eye(player)
    if state.index == 5:
        held = state.instance.find_var(0, 375).value()
        retained = held if isinstance(held, tuple) else ()
    else:
        hold = state.instance.states.get(5)
        if hold is not None and hold.active:
            return ()
        retained = None
    found = []
    for target in fight._targets():
        if (target.owner is player or target.team != player.team or fight.vitals_of(target).dead
                or (retained is not None and expr.Entity(target.entity) not in retained)):
            continue
        centre = (target.feet[0], target.feet[1]+target.height*.5, target.feet[2])
        delta = tuple(b-a for a, b in zip(origin, centre, strict=True))
        distance = math.sqrt(sum(v*v for v in delta))
        if not 0 < distance <= radius:
            continue
        forward = tuple(v/distance for v in delta)
        alignment = sum(a*b for a, b in zip(direction, forward, strict=True))
        if angle < 360 and alignment < math.cos(math.radians(max(0, angle)*.5)):
            continue
        wall = combat.ray_world(fight._collision(), origin, forward, distance)
        if wall is None or wall >= distance:
            found.append((-alignment, distance, target.entity))
    return (expr.Entity(min(found)[2]),) if found else ()


class BoostWorld(runtime.World):
    def __init__(self, mode, source):
        super().__init__(HERO, mode)
        self.source = source

    def game_message_subject(self, instance, node, instigator):
        return self.source


def credit(fight, shooter):
    if fight.match.game_map.free_for_all:
        return None
    binding = (graph._data().get("effect_scripts") or {}).get(f"0x{BOOST:016X}")
    if binding is None or binding["graph"] != 0x006C:
        return None
    model = graph.graph(binding["graph"])
    if model is None:
        return None
    amount = 0.0
    source = None
    recipient = expr.Entity(shooter.body)
    for healer in fight.match.players:
        script = healer.body_script
        if healer.hero.guid != HERO or healer.team != shooter.team or script is None or fight.is_dead(healer):
            continue
        for staff in script.component.instances.values():
            if staff.graph.index != 0x006B:
                continue
            hold = staff.states.get(5)
            if hold is None or not hold.active or recipient not in (targets(script.world, hold) or ()):
                continue
            for state in staff.states.values():
                if not state.active or state.node.cls != "STU_431F3FCC":
                    continue
                asset = state.evaluate(state.node.field("m_A76A8CF6"), watch=False)
                target = state.evaluate(state.node.field("m_targets"), watch=False)
                if not isinstance(asset, expr.Asset) or asset.guid != BOOST or target != recipient:
                    continue
                parameters = tuple((expr.guid_of(row.get("m_B5051BCE")),
                                    state.evaluate(row.get("m_value"), watch=False))
                                   for row in state.node.field("m_params") or ())
                component = runtime.Component(shooter.body, script.frame_ms,
                                              BoostWorld(fight.match.game_map.mode_guid, healer.body))
                component.frame, component.now = script.frame, script.frame*script.frame_ms
                instance = component.create(model)
                instance.set_source_entity(shooter.body)
                packet = message_receive.Packet(START, healer.body, healer.body, parameters)
                if message_receive.receive(instance, packet) == 1:
                    component.run_frame(script.frame)
                    if any(s.active and s.node.cls == "STUStatescriptStateCombatModFilter"
                           for s in instance.states.values()):
                        candidate = expr.to_float(instance.find_var(0, 174).value())
                        if candidate > amount:
                            source, amount = script, candidate
    return (source, 1.0+amount) if source is not None else None


def bonus(fight, shooter):
    selected = credit(fight, shooter)
    return selected[1]-1 if selected is not None else 0.0


def award(source, target, applied, extra, coefficient):
    if source.retired or source.world.match is None or extra <= 0:
        return
    params = {0x74D: expr.f32(extra), 0x74C: expr.f32(coefficient),
              0x13: expr.f32(applied), 0x5C: expr.Entity(target)}
    source.world.match.queue_body_message(source.entity, 0x0240000000000D9A, params, source.entity)



def award_filter(world, cfg, instance):
    """Selected ordinary-victim bindings for the authored Mercy boost branch."""
    if (world.hero != HERO or world.match is None or instance is None
            or instance.graph.index != 0x006B):
        return NotImplemented
    staff = True
    cls = cfg.get("$")
    if cls == "STU_AC829876":
        if expr.dynamic(cfg.get("m_entity")) != (expr.INSTANCE, 536 if staff else 393):
            return NotImplemented
        spec = cfg.get("m_filter") or {}
        target = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        mask = spec.get("m_84065FD1")
        if mask == 1 and not any(value for key, value in spec.items() if key not in {"$", "m_84065FD1"}):
            return any(actor.entity == target for actor in world.match.combat._targets())
        if (staff and mask == 64 and spec.get("m_66747E7D") == 2
                and expr.guid_of((spec.get("m_8B2A4DF3") or {}).get("m_7D7C86A1")) == 0x0D6000000000003A):
            # For the selected ordinary victim profile, no dynamic3A tag is
            # supplied. Evaluate the authored victim marker condition.
            actors = {actor.entity: actor for actor in world.match.combat._targets()}
            return (target in actors and world.body in actors and target != world.body
                    and actors[target].team != actors[world.body].team
                    and expr.truthy(instance.evaluate(spec.get("m_condition"))))
        return NotImplemented
    return NotImplemented
