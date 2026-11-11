"""Crossfade status and Amp It Up driven by the authored 0267 receiver."""

from dataclasses import dataclass

from ow174.game import actors
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000079
# Authored 0126.054 defaults for Hold to Crossfade, Backwards Wall Ride and Wall Jump on Release.
SETTING_DEFAULTS = {7766: False, 9473: False, 32856: True}
CROSSFADE = 0x0267


@dataclass
class Lease:
    instance: runtime.Instance
    created_tick: int


def keep_active_amp_on_stun(script, state) -> bool:
    """Keep applied 0277 Amp on its authored duration and cooldown during stun."""
    if script.hero != HERO:
        return False
    if state.instance.graph.index != 0x0277 or state.index != 0:
        return False
    marker = state.instance.component.entity_var(2381, create=False)
    return marker is not None and any(
        link.state.instance is state.instance and link.state.index == 3 and link.state.active
        for link in marker.links
    )


def effect_gap_frame(script, after, end):
    """Keep aura transitions and the Barrier cast timer without adding movement poses."""
    if script.hero != HERO or script.component.owner_dead:
        return end
    timer_states = {0x0266: (2, 41), 0x0277: (2, 4), 0x073A: (4,)}
    return min(
        (frame for instance in script.component.instances.values()
         for event in instance.queue
         if event.kind == runtime.TIMER and event.state is not None and event.state.active
         and event.state.index in timer_states.get(instance.graph.index, ())
         if after < (frame := event.time // script.frame_ms) < end),
        default=end,
    )


class Auras:
    def __init__(self, combat):
        self.combat = combat
        self.leases: dict[tuple[int, int], Lease] = {}
        self.healing: dict[tuple[int, int], list[tuple[int, tuple]]] = {}
        self.last_tick: int | None = None

    def retire(self, body):
        for key in list(self.leases):
            if body in key:
                self._drop(key)
        for key in list(self.healing):
            if body in key:
                del self.healing[key]

    def _drop(self, key):
        lease = self.leases.pop(key)
        lease.instance.component.destroy(lease.instance)

    def _play_controller(self, lease):
        """Bridge actual0267's self PlayScript to persistent, policy0 actual07C7.

        Legacy permissive actions otherwise skip PlayScript. Keep this bounded
        to the known self-targeted status action; arbitrary world routing and
        native inherited invocation/transport context remain unimplemented.
        """
        from ow174.game.script import play

        instance = lease.instance
        component = instance.component
        if any(item.graph.index == 0x07C7 for item in component.instances.values()):
            return  # authored policy0 skips an existing controller
        node = instance.graph.node(6)
        resource = play.resolve_resource(instance, node)
        if (resource is None or resource.guid != 0x05800000000007C7
                or instance.evaluate(node.field("m_targets")) != expr.Entity(component.entity)
                or node.field("m_AA4DE426") != 0 or node.field("m_7ABA409C") != 0
                or node.field("m_params") is not None or node.field("m_6D7701B7") is not None):
            raise ValueError("unsupported Crossfade controller invocation")
        definition = graph.graph(resource.guid & 0xFFFF)
        if definition is None:
            raise ValueError("Crossfade requires actual07C7 movement controller")
        controller = component.play_loaded_graph(
            definition, source=component.entity, overrides=resource.overrides, policy=0,
        )
        if controller is not None:
            controller.update()

    def refresh(self, command_frame=None):
        from ow174.game.combat import capsule_visible, segment_distance

        child = graph.graph(CROSSFADE)
        if child is None:
            return []
        fight, wanted = self.combat, set()
        recipients = {}
        targets = fight._targets()
        sample_tick = fight.tick if command_frame is None else max(fight.tick + 1, command_frame)
        for source in targets:
            owner = source.owner
            hero = getattr(owner, "hero", None)
            if (getattr(hero, "guid", None) != HERO or not hasattr(owner, "mover")
                    or fight.vitals_of(source).dead):
                continue
            script = owner.body_script
            if script is None or script.retired or script.component.strict:
                continue
            searches = [instance.states.get(3) for instance in script.component.instances.values()
                        if instance.graph.index == 0x0266]
            search = next((state for state in searches if state is not None and state.active), None)
            if search is None:
                continue
            radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
            if radius <= 0:
                continue
            origin = fight._eye(owner)
            for target in targets:
                ally = not fight.match.game_map.free_for_all and source.team == target.team
                if (isinstance(target.owner, actors.Deployable) or (target.owner is not owner and not ally)
                        or fight.vitals_of(target).dead):
                    continue
                target_script = getattr(target.owner, "body_script", None)
                if target_script is None:
                    continue
                a, b = target.segment()
                if segment_distance(origin, a, b) - target.radius > radius:
                    continue
                if target.owner is not owner and not capsule_visible(
                    fight._collision(), origin, a, b, target.radius, walls=fight.walls,
                ):
                    continue
                key = (source.entity, target.entity)
                wanted.add(key)
                lease = self.leases.get(key)
                component = target_script.component
                if lease is not None and lease.instance.component is not component:
                    self._drop(key)
                    lease = None
                if lease is None:
                    instance = component.create(
                        child, values={(graph.INSTANCE, 27): expr.Entity(source.entity)},
                    )
                    lease = self.leases[key] = Lease(instance, fight.tick)
                recipients.setdefault(component, []).append((key, lease, script.component.now))
        for key in list(self.leases):
            if key not in wanted:
                self._drop(key)
                self._record_healing(key, sample_tick, ())

        for component, entries in recipients.items():
            self._update_leases(component, entries)
            for key, lease, _ in entries:
                self._play_controller(lease)
                self._record_healing(key, max(sample_tick, lease.created_tick + 1), self._heals(lease))
        return targets

    @staticmethod
    def _update_leases(component, entries):
        target_now = component.now
        # Sibling 0267 leases share the receiver's priority stack. Drain their
        # notifications together before sampling any rate. The receiver's
        # command clock and its separate 07C7 controller stay at target_now.
        source_now = max(now for _, _, now in entries)
        instances = [lease.instance for _, lease, _ in entries]
        component.now = max(target_now, source_now)
        try:
            with component.operation():
                pending = instances
                while pending:
                    for instance in pending:
                        # The source owns the song and Amp slots. Recheck them on
                        # the recipient without installing cross-component watchers.
                        for position in (1, 3, 4):
                            state = instance.states.get(instance.graph.node(position).state)
                            if state is not None and state.active:
                                state.dependency_changed()
                        instance.update()
                    pending = [
                        instance for instance in instances
                        if not instance.stopped and instance.queue and instance.queue[0].time <= component.now
                    ]
        finally:
            component.now = target_now

    @staticmethod
    def _heals(lease):
        from ow174.game.combat import hit_def

        effects = []
        for state in lease.instance.states.values():
            if not state.active or state.node.cls != "STUStatescriptStateModifyHealth":
                continue
            # Only the two authored 0267 healing operations are supported.
            modify = state.node.field("m_modifyHealth") or {}
            if state.node.field("m_7B2B3F5C") != 1 or modify.get("m_93CDFD2F") != 0:
                continue
            hit = hit_def(state, {"m_modifyHealth": modify})
            if hit is None or hit.amount <= 0:
                continue
            tags = tuple(
                tag.guid for row in modify.get("m_148F3152") or ()
                if isinstance(tag := state.evaluate(row.get("m_EE3055BD"), watch=False), expr.Asset)
                and tag.guid
            )
            effects.append((hit.amount, tags))
        return tuple(effects)

    def _record_healing(self, key, tick, effects):
        timeline = self.healing.get(key)
        if timeline is None:
            if effects:
                self.healing[key] = [(tick, effects)]
            return
        last_tick, last_effects = timeline[-1]
        if effects == last_effects:
            return
        # The body VMs are forward-only. A later observation cannot undo a
        # rate already observed at a newer command frame.
        if tick <= last_tick:
            timeline[-1] = last_tick, effects
        else:
            timeline.append((tick, effects))

    def update(self, tick, *, observe=True):
        if self.last_tick is not None and tick <= self.last_tick:
            return
        previous = tick - 1 if self.last_tick is None else self.last_tick
        observed = self.refresh() if observe else self.combat._targets()
        targets = {target.entity: target for target in observed}
        self.last_tick = tick

        changes = {}
        active = {}
        for key, timeline in list(self.healing.items()):
            consumed = 0
            for at, effects in timeline:
                if at > tick:
                    break
                consumed += 1
                if at <= previous:
                    active[key] = effects
                else:
                    changes.setdefault(at, {})[key] = effects
            if consumed:
                # Retain the last nonempty sample as the next interval baseline.
                last = timeline[consumed - 1]
                del timeline[:consumed - bool(last[1])]
                if not timeline:
                    del self.healing[key]

        start = previous + 1
        for at, rates in sorted(changes.items()):
            self._settle_healing(targets, active, start, at)
            active.update(rates)
            start = at
        self._settle_healing(targets, active, start, tick + 1)

    def _settle_healing(self, targets, active, start, end):
        from ow174.game.combat import TICK

        if start >= end:
            return
        for (source_id, target_id), effects in active.items():
            source = targets.get(source_id)
            target = targets.get(target_id)
            if not effects or source is None or target is None:
                continue
            vitals = self.combat.vitals_of(target)
            if vitals.dead or self.combat.vitals_of(source).dead:
                continue
            elapsed = (end - start) * TICK
            for amount, tags in effects:
                given = self.combat._heal_target(
                    source.owner, target, amount * elapsed, source_entity=source_id,
                    tags=tags, seconds=elapsed, end_tick=end,
                )
                source.owner.body_script.gain_ult_charge(given)
