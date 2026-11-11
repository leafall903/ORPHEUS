"""Selected 104319 Sound Barrier cast, status graph and temporary shield host."""

from dataclasses import dataclass

from ow174.game import lucio_soundwave
from ow174.game.script import expr, graph, message_receive, runtime

CAST = 0x073A
STATUS = 0x073B
EFFECT = 0x04A0000000000191
START = 0x024000000000000F
DURATION = 0x0A4000000000002B


def model():
    binding = graph.effect_script(EFFECT)
    return graph.graph(STATUS) if binding is not None and binding["graph"] == STATUS else None


def caster(world):
    return next((p for p in world.match.players if p.has_body and p.body == world.body), None)


def owner_filter(world, cfg, instance):
    if instance.graph.index != CAST or cfg.get("$") != "STU_AC829876":
        return NotImplemented
    allowed = instance.graph.node(32).field("m_condition")["m_configVars"][0]
    if cfg != allowed:
        return NotImplemented
    owner = caster(world)
    return (model() is not None and owner is not None and not owner.mover.airborne
            and not world.match.combat.is_dead(owner) and not world.match.combat.is_stunned(owner))


def recipients(world, state):
    from ow174.game.combat import capsule_visible, segment_distance

    owner = caster(world)
    if owner is None:
        return ()
    fight = world.match.combat
    origin = fight._eye(owner)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    found = []
    for target in fight._targets():
        ally = not world.match.game_map.free_for_all and target.team == owner.team
        if ((target.owner is not owner and not ally) or fight.vitals_of(target).dead
                or getattr(target.owner, "body_script", None) is None):
            continue
        a, b = target.segment()
        if (target.owner is owner or (segment_distance(origin, a, b) - target.radius <= radius
                and capsule_visible(fight._collision(), origin, a, b, target.radius, walls=fight.walls))):
            found.append(target.entity)
    return tuple(found)


@dataclass
class Lease:
    component: runtime.Component
    instance_id: int
    rate: float
    last_tick: int


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.leases = {}

    def command(self, player, script, command):
        if command is None:
            return
        child = model()
        if child is None:
            return
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index != CAST:
                continue
            gate = instance.states.get(14)
            if gate is not None and gate.active and not gate.ending:
                # The cast's native landing predicate is polled on posed owner frames.
                gate.try_complete()
                script.component.run_now()
            slam = instance.states.get(6)
            if slam is None:
                continue
            seen = self.fight.melee_seen.setdefault(player.body, {})
            key = instance.id, slam.index
            previous = seen.get(key, 0)
            seen[key] = slam.counter
            if previous == slam.counter or not slam.active:
                continue
            apply = instance.states.get(0)
            if apply is None or not apply.active:
                continue
            effect = apply.evaluate(apply.node.field("m_A76A8CF6"), watch=False)
            if effect != expr.Asset(EFFECT):
                continue
            params = tuple(
                (expr.guid_of(row.get("m_B5051BCE")), apply.evaluate(row.get("m_value"), watch=False))
                for row in apply.node.field("m_params") or ()
            )
            duration = expr.to_float(dict(params).get(DURATION))
            if duration <= 0:
                continue
            selected = apply.evaluate(apply.node.field("m_targets"), watch=False)
            if not isinstance(selected, tuple):
                continue
            entities = {item.id for item in selected if isinstance(item, expr.Entity)}
            for target in self.fight._targets():
                if target.entity in entities:
                    self._grant(player, target, child, params, duration)

    def _grant(self, player, target, child, params, duration):
        from ow174.game.combat import TICK

        vitals = self.fight.vitals_of(target)
        if vitals.dead:
            return
        frame_ms = round(TICK * 1000)
        world = runtime.World(lucio_soundwave.HERO, self.fight.match.game_map.mode_guid)
        component = runtime.Component(target.entity, frame_ms, world)
        component.frame, component.now = self.fight.tick, self.fight.tick * frame_ms
        effect = component.create(child, values={(expr.INSTANCE, 27): expr.Entity(player.body)})
        packet = message_receive.Packet(START, player.body, player.body, params)
        if message_receive.receive(effect, packet) != 1:
            return
        component.run_now()
        shield = effect.states.get(2)
        amount = expr.to_float(shield.evaluate(shield.node.field("m_amount"), watch=False)) if shield else 0
        if shield is None or not shield.active or amount <= 0:
            return
        self.retire(target.entity)
        vitals.grant_temporary_shields(EFFECT, amount, amount)
        self.leases[target.entity] = Lease(component, effect.id, amount / duration, self.fight.tick)
        self.fight._sync_body_health(target.owner, vitals)

    def retire(self, entity):
        lease = self.leases.pop(entity, None)
        if lease is not None:
            instance = lease.component.instances.get(lease.instance_id)
            if instance is not None:
                lease.component.destroy(instance)
            vitals = self.fight.vitals.get(entity)
            if vitals is not None:
                pool = vitals.temporary_shields.get(EFFECT)
                if pool is not None:
                    vitals.decay_temporary_shields(EFFECT, pool.now)

    def update(self, tick):
        from ow174.game.combat import TICK

        targets = {target.entity: target for target in self.fight._targets()}
        for entity, lease in list(self.leases.items()):
            target = targets.get(entity)
            vitals = self.fight.vitals.get(entity)
            if target is None or vitals is None or vitals.dead:
                self.retire(entity)
                continue
            if tick <= lease.last_tick:
                continue
            removed = vitals.decay_temporary_shields(EFFECT, (tick - lease.last_tick) * TICK * lease.rate)
            lease.last_tick = tick
            pool = vitals.temporary_shields.get(EFFECT)
            if pool is None or pool.now <= 0:
                self.retire(entity)
            else:
                instance = lease.component.instances.get(lease.instance_id)
                if instance is not None:
                    instance.write((expr.INSTANCE, 199), pool.now)
                    lease.component.run_frame(tick)
            if removed > 0:
                self.fight._sync_body_health(target.owner, vitals)
