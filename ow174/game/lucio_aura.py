"""Resource-owned Crossfade status and movement controller for 104319."""

import math
from dataclasses import dataclass, field

from ow174.game import lucio_soundwave
from ow174.game.mover import statescript_mods
from ow174.game.script import expr, graph, play, runtime

CROSSFADE = 0x0266
AMP = 0x0277
STATUS = 0x0267
CONTROLLER = 0x07C7
EFFECT = 0x04A00000000000A4
SONG, AMPLIFIED = 1735, 2381


def model():
    binding = graph.effect_script(EFFECT)
    if binding is None or binding["graph"] != STATUS or graph.graph(CONTROLLER) is None:
        return None
    return graph.graph(STATUS)


def preserve_amp(owner, state):
    """An applied Amp keeps its timed scope through stuns."""
    if getattr(getattr(owner, "hero", None), "guid", None) != lucio_soundwave.HERO:
        return False
    instance = state.instance
    if instance.graph.index != AMP or state.index != 0:
        return False
    applied = instance.states.get(3)
    return applied is not None and applied.active and not applied.ending


class AuraWorld(runtime.World):
    """Mirror only Crossfade's two foreign reads into this receiver's VM."""

    def __init__(self, hero, mode):
        super().__init__(hero, mode)
        self.component = None

    def key(self, entity, var):
        if entity == self.component.entity:
            return var
        # Private local slots never enter the owning player's network component.
        return 0x10000 + 2 * entity + (var == AMPLIFIED)

    def entity_var_slot(self, entity, var, *, create=False):
        if var not in (SONG, AMPLIFIED):
            return NotImplemented
        return self.component.entity_var(self.key(entity, var), create=create)

    def mirror(self, entity, var, value):
        key = self.key(entity, var)
        slot = self.component.entity_var(key, create=False)
        if slot is None or slot.value() != value:
            self.component.set(key, value)


@dataclass
class Receiver:
    component: runtime.Component
    sources: dict[int, int] = field(default_factory=dict)


class Auras:
    def __init__(self, fight):
        self.fight = fight
        self.receivers = {}
        self.amp_deadlines = {}
        self.last_tick = None

    def retire(self, entity):
        self.amp_deadlines.pop(entity, None)
        receiver = self.receivers.pop(entity, None)
        if receiver is not None:
            for instance in tuple(receiver.component.instances.values()):
                receiver.component.destroy(instance)
        for other in self.receivers.values():
            self._drop(other, entity)

    @staticmethod
    def _drop(receiver, source):
        number = receiver.sources.pop(source, None)
        if number is not None:
            component = receiver.component
            component.destroy(component.instances[number])
            # This private VM has no observer stream awaiting destroy acknowledgments.
            component.destroyed.clear()
            for var in (SONG, AMPLIFIED):
                key = component.world.key(source, var)
                slot = component.vars.get(key)
                if slot is not None and not slot.watchers and not slot.links:
                    del component.vars[key]

    def mods(self, entity):
        receiver = self.receivers.get(entity)
        if receiver is None:
            return {}
        return {(-instance, state, slot): value
                for (instance, state, slot), value in statescript_mods(receiver.component).items()}

    def _amp(self, script, tick):
        from ow174.game.combat import TICK

        slot = script.component.entity_var(AMPLIFIED, create=False)
        value = slot.value() if slot is not None else 0
        if expr.truthy(value):
            for instance in script.component.instances.values():
                if instance.graph.index != AMP:
                    continue
                timer = instance.states.get(4)
                if timer is None or not timer.active or timer.ending:
                    continue
                activation = instance.id, timer.counter
                prior = self.amp_deadlines.get(script.entity)
                if prior is None or prior[0] != activation:
                    duration = expr.to_float(timer.evaluate(timer.node.field("m_timeout"), watch=False))
                    remaining = duration + (timer.start - script.component.now) / 1000.0
                    prior = activation, tick + max(0, math.ceil(remaining / TICK) - 1)
                    self.amp_deadlines[script.entity] = prior
                return value if tick <= prior[1] else 0
        self.amp_deadlines.pop(script.entity, None)
        return 0

    def _eligible(self, tick, targets):
        from ow174.game.combat import capsule_visible, segment_distance

        wanted = {}
        fight = self.fight
        collision = fight._collision()
        for source in targets.values():
            owner = source.owner
            if (getattr(getattr(owner, "hero", None), "guid", None) != lucio_soundwave.HERO
                    or owner.gone or fight.vitals_of(source).dead or owner.body_script is None):
                continue
            script = owner.body_script
            search = next((i.states.get(3) for i in script.component.instances.values()
                           if i.graph.index == CROSSFADE), None)
            if search is None or not search.active:
                continue
            radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
            song = script.component.entity_var(SONG, create=False)
            values = song.value() if song is not None else 0, self._amp(script, tick)
            origin = fight._eye(owner)
            for target in targets.values():
                ally = not fight.match.game_map.free_for_all and source.team == target.team
                if ((target.entity != source.entity and not ally) or fight.vitals_of(target).dead
                        or getattr(target.owner, "body_script", None) is None):
                    continue
                a, b = target.segment()
                if target.entity != source.entity:
                    if segment_distance(origin, a, b) - target.radius > radius:
                        continue
                    if not capsule_visible(collision, origin, a, b, target.radius, walls=fight.walls):
                        continue
                wanted.setdefault(target.entity, {})[source.entity] = values
        return wanted

    @staticmethod
    def _controller(instance):
        component = instance.component
        if any(item.graph.index == CONTROLLER for item in component.instances.values()):
            return
        node = instance.graph.node(6)
        resource = play.resolve_resource(instance, node)
        if (resource is not None and resource.guid == ((graph.GRAPH_TYPE << 48) | CONTROLLER)
                and instance.evaluate(node.field("m_targets")) == expr.Entity(component.entity)):
            component.play_loaded_graph(
                graph.graph(CONTROLLER), source=component.entity, overrides=resource.overrides, policy=0,
            )

    def _receiver(self, target, tick):
        from ow174.game.combat import TICK

        world = AuraWorld(target.owner.body_script.component.world.hero, self.fight.match.game_map.mode_guid)
        frame_ms = round(TICK * 1000)
        component = world.component = runtime.Component(target.entity, frame_ms, world)
        component.frame, component.now = tick, tick * frame_ms
        receiver = Receiver(component)
        self.receivers[target.entity] = receiver
        return receiver

    def update(self, tick):
        from ow174.game import ultimate
        from ow174.game.combat import TICK, hit_def

        child = model()
        if child is None or (self.last_tick is not None and tick <= self.last_tick):
            return
        self.last_tick = tick
        targets = {target.entity: target for target in self.fight._targets()}
        wanted = self._eligible(tick, targets)
        for entity in tuple(self.receivers):
            target = targets.get(entity)
            if target is None or self.fight.vitals_of(target).dead:
                self.retire(entity)
        for entity, sources in wanted.items():
            target = targets[entity]
            receiver = self.receivers.get(entity)
            if receiver is None:
                receiver = self._receiver(target, tick)
            component = receiver.component
            for source, (song, amp) in sources.items():
                component.world.mirror(source, SONG, song)
                component.world.mirror(source, AMPLIFIED, amp)
                if source not in receiver.sources:
                    instance = component.create(child, values={(expr.INSTANCE, 27): expr.Entity(source)})
                    receiver.sources[source] = instance.id
                    self._controller(instance)
        for entity, receiver in self.receivers.items():
            for source in tuple(receiver.sources):
                if source not in wanted.get(entity, {}):
                    self._drop(receiver, source)
            component = receiver.component
            component.run_frame(tick)
            for source, number in receiver.sources.items():
                instance = component.instances[number]
                for state in instance.states.values():
                    if not state.active or state.node.pos not in (17, 35):
                        continue
                    hit = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
                    if hit is not None and hit.amount > 0:
                        healer, target = targets[source].owner, targets[entity]
                        given = self.fight._heal(healer, target, hit.amount * TICK)
                        ultimate.credit_healing(healer, target, given, hit.tags)
