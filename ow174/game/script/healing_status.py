"""Selected0267 effect hosts for Mercy's cross-hero healing interactions.

Original0C71/0B99/0C72 graphs own faction branch, amounts, filters and timers.
World clock, loaded same-owner PlayScript routing, ordinary-actor predicates and
neutral stats are explicit reconstruction bindings, not general native statuses.
"""

from bisect import bisect_right
from dataclasses import dataclass, field

from ow174.game.script import expr, graph, message_receive, play, play_queue, regeneration, runtime, support_effects

GRENADE = 0x04A0000000000267
RECEIVED_HEALING = 0x031A
# 0C72/node8 reads these live body stacks to end hostile anti-heal.
PROTECTION_STACKS = (6604, 8440, 478, 33551)


class StatusWorld(support_effects.EffectWorld):
    plays_scripts = True

    def __init__(self, match, source, owner):
        super().__init__(match, source, owner)
        self.hero = 0x02E000000000013B

    def native(self, cfg, instance):
        cls = cfg.get("$")
        if cls == "STU_53F4CA65":
            source = expr.to_entity(instance.evaluate(cfg.get("m_source")))
            target = expr.to_entity(instance.evaluate(cfg.get("m_target")))
            actors = {t.entity: t for t in self.match.combat._targets()}
            return (source in actors and target in actors and (source == target or (
                not self.match.game_map.free_for_all and actors[source].team == actors[target].team)))
        if cls == "STU_AC829876":
            # Ordinary live combat body profile only; special tag2ED exemptions
            # and the full native filter-bit table remain unbound.
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            target = next((t for t in self.match.combat._targets() if t.entity == entity), None)
            return (cfg.get("m_filter", {}).get("m_84065FD1") == 1 and target is not None
                    and not self.match.combat.vitals_of(target).dead)
        if cls == "STU_4049CF77":
            return instance.evaluate(cfg.get("m_A53100D5"))  # explicit neutral/default stat input
        if cls == "STU_9DB46B98":
            return expr.Entity(self.source)  # declared original impact instigator
        if cls == "STU_E72F6348":
            return True  # applied-effect source context retained until its authored expiry
        return super().native(cfg, instance)

    def play_script_selected(self, instance, node):
        if instance.graph.index != 0x0C71:
            return NotImplemented
        resource = play.resolve_resource(instance, node)
        if resource is not None:
            self.play_resolved_script(instance, node, resource)
        return True

    def play_script_context(self, instance, node):
        return play.InvocationContext(self.owner, self.owner, self.source)

    def submit_script_request(self, instance, node, request):
        if request.targets != (self.owner,) or request.resource.guid & 0xFFFF not in (0x0B99, 0x0C72):
            instance.component.unsupported(instance, "selected status PlayScript route", node.cls, node)

        def acquire(guid):
            model = graph.graph(guid & 0xFFFFFFFFFFFF)
            return play_queue.Load("ready", model) if model is not None else None

        # These explicit resources are already loaded. Native queue submission
        # flushes synchronously; no pending asynchronous state is discarded.
        queue = play_queue.Queue(instance.component, acquire)
        queue.submit(request)
        if queue.pending:
            instance.component.unsupported(instance, "unready status PlayScript resource", node.cls, node)


@dataclass
class Applied:
    source: int
    target: int
    component: runtime.Component
    root: runtime.Instance
    last_frame: int
    damage_scale: float = 1.0
    seen: set = field(default_factory=set)


class Statuses:
    def __init__(self, match):
        self.match = match
        self.applied = {}
        self.filter_history = {}

    @staticmethod
    def available():
        binding = graph.effect_script(GRENADE)
        return binding is not None and binding["graph"] == 0x0C71

    def retire(self, target):
        self.filter_history.pop(target, None)
        for key, item in list(self.applied.items()):
            if item.target == target:
                self._remove(key)
        player = next((p for p in self.match.players if p.has_body and p.body == target), None)
        if player is not None and player.body_script is not None:
            vars(player.body_script.world).pop("passive_healing_scales", None)

    def _remove(self, key):
        item = self.applied.pop(key, None)
        if item is not None:
            for instance in list(item.component.instances.values()):
                item.component.destroy(instance)

    def impact(self, shooter, target, damage_scale=1.0, *, boost_credit=None,
               source_entity=None, healing_scale=1.0, broken_particle_barriers=None):
        fight = self.match.combat
        vitals = fight.vitals_of(target)
        if vitals.dead or fight.now < vitals.invulnerable_until or not self.available():
            return
        source = (getattr(shooter, "body", getattr(shooter, "entity", 0))
                  if source_entity is None else source_entity)
        hostile = source != target.entity and (
            self.match.game_map.free_for_all or shooter.team != target.team
        )
        if hostile and fight.damage_immune(target.owner):
            return
        key = source, target.entity
        item = self.applied.get(key)
        if item is None:
            component = runtime.Component(target.entity, world=StatusWorld(self.match, source, target.entity))
            component.frame, component.now = fight.tick, fight.tick * component.frame_ms
            root = component.create(graph.graph(0x0C71))
            root.set_source_entity(target.entity)
            item = Applied(source, target.entity, component, root, fight.tick - 1)
            self.applied[key] = item
        item.damage_scale = damage_scale
        message_receive.receive(item.root, message_receive.Packet(support_effects.START, source, source, ()))
        self._advance(item, fight.tick, boost_credit=boost_credit, shooter=shooter,
                      healing_scale=healing_scale, broken_particle_barriers=broken_particle_barriers)
        self._record_filters(target, fight.tick)

    def update(self, frame):
        fight = self.match.combat
        targets = {t.entity: t for t in fight._targets()}
        # A sparse world update still crosses authored filter timers. Capture
        # those transitions before reading the current bodies' protection stacks.
        while self.applied:
            next_frame = min(
                (max(item.last_frame + 1, due // item.component.frame_ms)
                 for item in self.applied.values()
                 if (due := item.component.next_event_time()) is not None),
                default=frame,
            )
            if next_frame >= frame:
                break
            self._update_frame(next_frame, targets, historical=True)
        self._update_frame(frame, targets)

    def _update_frame(self, frame, targets, *, historical=False):
        fight = self.match.combat
        changed = {}
        for item in list(self.applied.values()):
            target = targets.get(item.target)
            if target is None or fight.vitals_of(target).dead:
                self.retire(item.target)
                continue
            if historical and item.last_frame >= frame:
                continue
            self._advance(item, frame, historical=historical)
            if not any(i.graph.index in (0x0B99, 0x0C72) for i in item.component.instances.values()):
                self._remove((item.source, item.target))
            changed[item.target] = target
        for target in changed.values():
            self._record_filters(target, frame)

    def _record_filters(self, target, frame):
        filters = self._filters(target.entity)
        history = self.filter_history.get(target.entity, [])
        if history and history[-1][0] == frame:
            history.pop()
        if filters != (history[-1][1] if history else ()):
            history.append((frame, filters))
        if history:
            self.filter_history[target.entity] = history
        else:
            self.filter_history.pop(target.entity, None)
        script = getattr(target.owner, "body_script", None)
        if (getattr(script, "hero", None) == regeneration.MERCY
                and script.entity == target.entity and not script.component.owner_dead):
            regeneration.record_healing_scale(script, frame, self._scale(filters, ()))

    def prune_filters(self, frame):
        # Any current Mercy may still submit a heal from her next command
        # frame. Keep the filter state at that boundary and all newer changes.
        if not self.filter_history:
            return
        oldest = min((p.body_script.frame for p in self.match.players
                      if p.body_script is not None and not p.gone and p.body_script.hero == regeneration.MERCY),
                     default=frame)
        for entity, history in list(self.filter_history.items()):
            boundary = bisect_right(history, oldest, key=lambda row: row[0]) - 1
            if boundary >= 0:
                start = boundary if history[boundary][1] else boundary + 1
                del history[:start]
            if not history:
                del self.filter_history[entity]

    def _advance(self, item, frame, *, boost_credit=None, historical=False, shooter=None,
                 healing_scale=1.0, broken_particle_barriers=None):
        fight = self.match.combat
        target = next((t for t in fight._targets() if t.entity == item.target), None)
        body_script = getattr(target.owner, "body_script", None) if target is not None else None
        if (not historical and body_script is not None and not getattr(body_script, "retired", False)
                and body_script.component.entity == item.target):
            for identifier in PROTECTION_STACKS:
                value = body_script.component.entity_var(identifier, create=False)
                if value is not None:
                    item.component.set(identifier, value.value())
        if item.last_frame != frame:
            item.component.run_frame(frame)
            item.last_frame = frame
        else:
            item.component.run_now()  # repeated impact's queued starts, not another frame
        source = (shooter if shooter is not None else
                  next((p for p in self.match.players if p.has_body and p.body == item.source), None))
        if target is None or source is None:
            return
        for instance in list(item.component.instances.values()):
            if instance.graph.index not in (0x0B99, 0x0C72):
                continue
            state = instance.states.get(0)
            if state is None or not state.active:
                continue
            key = instance.id, state.index, state.counter
            if key in item.seen:
                continue
            item.seen.add(key)
            modify = state.node.field("m_modifyHealth")
            amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
            if instance.graph.index == 0x0B99:
                fight._heal_target(source, target, amount * healing_scale,
                                   source_entity=item.source, healing_tick=frame)
            else:
                fight._damage(source, target, amount * item.damage_scale, False,
                              expr.guid_of(modify.get("m_loadout", {}).get("m_loadout")),
                              frame, world_tick=frame, boost_credit=boost_credit,
                              source_body=item.source, broken_particle_barriers=broken_particle_barriers)

    def _filters(self, entity):
        filters = []
        for item in self.applied.values():
            if item.target != entity:
                continue
            for instance in item.component.instances.values():
                for state in instance.states.values():
                    if not state.active or state.node.cls != "STUStatescriptStateCombatModFilter":
                        continue
                    node = state.node
                    if expr.guid_of(node.field("m_60703B1E")) & 0xFFFF != RECEIVED_HEALING:
                        continue
                    excluded = tuple(sorted({expr.guid_of(tag) for tag in node.field("m_5CFFCBAE") or ()}))
                    value = expr.to_float(state.evaluate(node.field("m_50530EAB"), watch=False))
                    filters.append((excluded, value))
        return tuple(filters)

    @staticmethod
    def _scale(filters, tags):
        bonus = 1.0
        tags = set(tags)
        for excluded, value in filters:
            if not tags.isdisjoint(excluded):
                continue
            if value <= 0:
                return 0.0
            bonus = max(bonus, value)  # declared non-stacking positive status policy
        return bonus

    def scale(self, entity, tags=(), *, frame=None):
        if frame is None or frame >= self.match.combat.tick:
            return self._scale(self._filters(entity), tags)
        for tick, filters in reversed(self.filter_history.get(entity, ())):
            if tick <= frame:
                return self._scale(filters, tags)
        return 1.0
