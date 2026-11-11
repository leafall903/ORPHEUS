"""Reconstructed Mercy staff selection and authored 006C damage multiplier."""
import math

from ow174.game.script import expr, graph, message_receive, runtime

HERO = 0x02E0000000000004
BOOST = 0x04A0000000000016
START = 0x024000000000000F


def tracks(state):
    component = state.instance.component
    return (not component.strict and component.world.hero == HERO
            and state.instance.graph.index == 0x006B and state.index in (4, 5, 51))


def targets(world, state):
    if world.match is None:
        return ()
    from ow174.game import combat

    fight = world.match.combat
    player = next((p for p in world.match.players if p.body == world.body and p.has_body), None)
    if player is None or fight.is_dead(player) or world.match.game_map.free_for_all:
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    angle_config = state.node.field("m_A30B383E")
    angle = expr.to_float(state.evaluate(angle_config, watch=False), 360)
    if state.index == 4 and angle == 0:
        # Reconstruct the aimed acquisition branch of the authored selector.
        angle = angle_config["m_expression"]["m_D99EF254"][1]
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
    from ow174.game.combat import _source_entity

    if fight.match.game_map.free_for_all:
        return None
    model = graph.graph(0x006C)
    if model is None:
        return None
    amount = 0.0
    source = None
    recipient = expr.Entity(_source_entity(shooter))
    for healer in fight.match.players:
        script = healer.body_script
        if healer.hero.guid != HERO or healer.team != shooter.team or script is None or fight.is_dead(healer):
            continue
        for staff in script.component.instances.values():
            if staff.graph.index != 0x006B:
                continue
            hold = staff.states.get(5)
            if hold is None or not hold.active or recipient not in targets(script.world, hold):
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
                component = runtime.Component(recipient.id, script.frame_ms,
                                              BoostWorld(fight.match.game_map.mode_guid, healer.body))
                component.frame, component.now = script.frame, script.frame*script.frame_ms
                instance = component.create(model)
                instance.set_source_entity(recipient.id)
                packet = message_receive.Packet(START, healer.body, healer.body, parameters)
                if message_receive.receive(instance, packet) == 1:
                    component.run_frame(script.frame)
                    if any(s.active and s.node.cls == "STUStatescriptStateCombatModFilter"
                           for s in instance.states.values()):
                        candidate = expr.to_float(instance.find_var(0, 174).value())
                        if math.isfinite(candidate) and candidate > amount:
                            amount, source = candidate, healer.body
    return (source, 1.0 + amount) if source is not None else None


def multiplier(fight, shooter):
    selected = credit(fight, shooter)
    return selected[1] if selected is not None else 1.0


def award(match, credit, target, dealt, extra):
    source, scale = credit
    params = {0x74D: expr.f32(extra), 0x74C: expr.f32(scale - 1),
              0x13: expr.f32(dealt), 0x5C: expr.Entity(target)}
    match.queue_body_message(source, 0x0240000000000D9A, params, source)


def award_filter(world, cfg, instance):
    """Resolve the selected victim queries on the staff's boost-credit receiver."""
    if (world.match is None or instance is None or instance.graph.index != 0x006B
            or expr.dynamic(cfg.get("m_entity")) != (expr.INSTANCE, 536)):
        return NotImplemented
    target = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    spec = cfg.get("m_filter") or {}
    mask = spec.get("m_84065FD1")
    actors = {actor.entity: actor for actor in world.match.combat._targets()}
    if mask == 1 and not any(value for key, value in spec.items() if key not in {"$", "m_84065FD1"}):
        return target in actors
    if (mask == 64 and spec.get("m_66747E7D") == 2
            and expr.guid_of((spec.get("m_8B2A4DF3") or {}).get("m_7D7C86A1")) == 0x0D6000000000003A):
        return (target in actors and world.body in actors and target != world.body
                and actors[target].team != actors[world.body].team
                and expr.truthy(instance.evaluate(spec.get("m_condition"))))
    return NotImplemented
