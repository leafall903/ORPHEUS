"""Scoped host for the authored Sombra Hack and EMP body applications."""

import math
from copy import deepcopy
from dataclasses import dataclass, replace

from ow174.game import actors, bots, health_packs
from ow174.game.script import entity_queries, expr, graph, nodes, play, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E000000000012E
HACK, EMP, PULSE, EFFECT = 0x0B1B, 0x0B31, 0x0B32, 0x0B1F
SKILLS = frozenset((35, 36, 37))
SKILL_INPUT = 0x38


@dataclass
class Lock:
    application: runtime.Instance
    until: float
    pulse: runtime.Instance | None


class DeviceWorld(runtime.World):
    """The hosted receiver for a live deployable's actual 0B32 and 0B1F graphs."""

    def __init__(self, fight, body, tags, *, hero=0):
        super().__init__(hero, fight.match.game_map.mode_guid)
        self.fight = fight
        self.body = body
        self.tags = tags

    def component76_keys(self, entity):
        return entity_queries.Component76Keys(self.tags) if entity == self.body else NotImplemented

    def native(self, cfg, instance):
        portals = getattr(self.fight, "symmetra_portals", None)
        if (portals is not None and instance is not None and instance.graph.index == PULSE
                and cfg.get("$") == entity_queries.POSSESSED):
            for pos in (15, 16):
                if cfg is instance.graph.node(pos).field("m_targets")["m_configVars"][0]:
                    # The mounted pair supplies the body behind the authored owner/possession chain.
                    _, owner = portals.endpoint_owner(self.body)
                    return expr.Entity(owner.entity if owner is not None else 0)
        return super().native(cfg, instance)

    def entity_var_slot(self, entity, var, *, create=False):
        from ow174.game.symmetra_portal import ENDPOINT_SLOTS

        portals = getattr(self.fight, "symmetra_portals", None)
        if portals is None or var not in ENDPOINT_SLOTS:
            return NotImplemented
        pair, owner = portals.endpoint_owner(self.body)
        if owner is None or owner.entity != entity:
            return None
        endpoint = pair.endpoints[ENDPOINT_SLOTS.index(var)]
        slot = owner.entity_var(var, create=False)
        return slot if slot is not None and slot.value() == expr.Entity(endpoint.body) else None

    def play_script_selected(self, instance, node):
        return self.fight.sombra_controls.play(instance, node)

    def modify_health(self, state, elapsed_ms):
        if (state.instance.graph.index, state.node.pos) != (PULSE, 9):
            return NotImplemented
        if elapsed_ms == 0:
            self.fight.sombra_controls.drain_device(state)
            state.request_finish()
        return True


class Controls:
    def __init__(self, fight):
        self.fight = fight
        self.locks = {}
        self.held = {}
        self.device_receivers = {}

    def input(self, player, command):
        """Latch held skills until a received command releases them."""
        entity = player.body
        masked = self.held.get(entity, 0) & command.buttons
        if entity in self.locks:
            masked |= command.buttons & SKILL_INPUT
        if masked:
            self.held[entity] = masked
            return replace(command, buttons=command.buttons & ~masked)
        self.held.pop(entity, None)
        return command

    def player(self, body):
        return next((p for p in self.fight.match.players if p.has_body and not p.gone and p.body == body), None)

    def blocked(self, state):
        return (state.instance.component.entity in self.locks
                and any(button in SKILLS for button in state.buttons()))

    def interrupt(self, player, *, hack_only=False):
        script = getattr(player, "body_script", None)
        if script is None:
            return
        if not hack_only and getattr(script, "hero", None) == 0x02E0000000000008:
            script.interrupt_control()
        for instance in tuple(script.component.instances.values()):
            if hack_only and instance.graph.index != HACK:
                continue
            for state in tuple(instance.states.values()):
                if not isinstance(state, nodes.Ability):
                    continue
                if hack_only or self.blocked(state):
                    state.presses.clear()
                    state.stop()

    def retire(self, entity):
        self.held.pop(entity, None)
        self.device_receivers.pop(entity, None)
        for lock in self.locks.pop(entity, ()):
            self.destroy(lock)

    @staticmethod
    def destroy(lock):
        for application in (lock.application, lock.pulse):
            if application is None:
                continue
            component = application.component
            if component.instances.get(application.id) is application:
                component.destroy(application)

    def update(self):
        for component in tuple(self.device_receivers.values()):
            if type(component.world) is DeviceWorld and component.frame < self.fight.tick:
                component.run_frame(self.fight.tick)
        for entity, locks in tuple(self.locks.items()):
            vitals = self.fight.vitals.get(entity)
            if entity not in self.device_receivers and (vitals is None or vitals.dead):
                self.retire(entity)
                continue
            keep = []
            for lock in locks:
                if self.fight.now < lock.until:
                    keep.append(lock)
                else:
                    self.destroy(lock)
            if keep:
                self.locks[entity] = keep
            else:
                del self.locks[entity]
                self.device_receivers.pop(entity, None)

    def enemies(self, player):
        return (target for target in self.fight._enemies(player)
                if not isinstance(target.owner, actors.Deployable)
                and getattr(target.owner, "body_script", None) is not None
                and not self.fight.damage_immune(target.owner))

    def hackable(self, player, *, emp=False):
        from ow174.game.combat import Target

        yield from self.enemies(player)
        yield from (target for target, _ in self.device_targets(player, emp=emp))
        for pack in self.fight.health_packs.values():
            if health_packs.available_to(self.fight, pack, player):
                yield Target(pack.entity, None, pack.position, 0.7, 0.35, 0.35, pack)

    def device_targets(self, player, *, emp=False):
        from ow174.game import ball_mines, symmetra_portal, symmetra_sentry, torbjorn_turret
        from ow174.game.combat import Target

        fight = self.fight
        for ward in fight.baptiste.fields.values():
            actor = ward.actor
            if (actor.health > 0 and actor.owner is not player and not actor.owner.gone
                    and (fight.match.game_map.free_for_all or actor.team != player.team)):
                yield fight._deployable_target(actor), frozenset((0x6E6,))
        managers = (
            (getattr(fight, "torbjorn_turrets", None), torbjorn_turret.DEVICE_RADIUS, frozenset((0x34,))),
            (getattr(fight, "symmetra_sentries", None), symmetra_sentry.DEVICE_RADIUS, frozenset((0x36F,))),
            (getattr(fight, "symmetra_portals", None), symmetra_portal.DEVICE_RADIUS, None),
        )
        if emp:
            managers += ((getattr(fight, "ball_mines", None), ball_mines.DEVICE_RADIUS, ball_mines.TAGS),)
        for manager, radius, tags in managers:
            if manager is None:
                continue
            devices = (manager.enemies(player, include_flights=True) if manager is getattr(fight, "ball_mines", None)
                       else manager.enemies(player))
            for device in devices:
                if isinstance(device, symmetra_sentry.Sentry) and not device.attached:
                    continue
                x, y, z = device.centre()
                device_tags = tags
                if manager is getattr(fight, "symmetra_portals", None):
                    _, index = manager.endpoint_pair(device.body)
                    device_tags = symmetra_portal.ENDPOINT_TAGS[index]
                yield Target(device.body, None, (x, y - radius, z), 2 * radius, radius, radius, device), device_tags

    def apply_devices(self, instance, definition, resource, request, caster):
        from ow174.game import symmetra_sentry

        if caster is None:
            return
        for target, tags in self.device_targets(caster, emp=instance.graph.index in (EMP, PULSE)):
            if target.entity not in request.targets:
                continue
            component = self.device_receivers.get(target.entity)
            if component is None:
                component = getattr(target.owner, "component", None)
                if component is None:
                    world = DeviceWorld(self.fight, target.entity, tags)
                    component = runtime.Component(target.entity, world=world)
                    component.frame = self.fight.tick
                    component.now = component.frame * component.frame_ms
                self.device_receivers[target.entity] = component
            params, overrides = request.params, resource.overrides
            if component is not instance.component:
                # Transfer values without sharing either component's private VM storage.
                params, overrides = deepcopy((params, overrides))
            application = component.play_loaded_graph(
                definition, source=request.context.caller, params=params,
                overrides=overrides, policy=request.policy, instigator=request.context.instigator,
            )
            if application is None:
                if target.entity not in self.locks:
                    self.device_receivers.pop(target.entity, None)
                continue
            if definition.index == EFFECT:
                component.run_now()
                duration = expr.to_float(application.find_var(graph.INSTANCE, 215).value())
                if duration > 0 and math.isfinite(duration):
                    locks = self.locks.setdefault(target.entity, [])
                    locks[:] = [lock for lock in locks if lock.application is not application]
                    pulse = instance if instance.graph.index == PULSE else None
                    locks.append(Lock(application, self.fight.now + duration, pulse))
                    if isinstance(target.owner, symmetra_sentry.Sentry):
                        self.fight.symmetra_sentries.set_target(target.owner, None)
            elif component is not instance.component:
                component.run_now()
            if target.entity not in self.locks:
                self.device_receivers.pop(target.entity, None)

    def drain_device(self, state):
        entity = state.instance.component.entity
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth")["m_amount"], watch=False))
        portals = getattr(self.fight, "symmetra_portals", None)
        if portals is not None and math.isfinite(amount) and amount > 0:
            portals.drain_shields(entity, amount)

    def body_barrier(self, target):
        from ow174.game import barriers

        if target.owner is None or not hasattr(target.owner, "body_script"):
            return None
        for barrier in self.fight.barriers.scripted.values():
            protected = barrier.recipient if barrier.recipient is not None else barrier.parent
            if (protected is target.owner and (barrier.recipient_body or barrier.parent_body) == target.entity
                    and barrier.component.world.hero == barriers.ZARYA and barrier.vitals is not None
                    and barrier.vitals.total > 0 and barriers._live(self.fight, barrier)):
                return barrier
        return self.fight.barriers.for_target(target.owner) or self.fight._active_projected_barrier(target.entity)

    def clear(self, player, origin, ray, reach, *, barriers=True):
        from ow174.game.combat import ray_world

        if self.fight.match.collision_world is None:
            return False
        wall = ray_world(self.fight._collision(), origin, ray, reach)
        if wall is not None and wall < reach:
            return False
        if self.fight.walls.cast(origin, ray, reach) is not None:
            return False
        return not barriers or (
            self.fight._barrier_contact(player, origin, ray, reach) is None
            and self.fight.brigitte.barrier_ray(player, origin, ray, reach) is None
        )

    def native(self, body, cfg, instance):
        cls = cfg.get("$")
        if cls == "STU_AC829876" and cfg is instance.graph.node(204).field("m_condition"):
            player = self.player(body)
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            return player is not None and any(t.entity == entity for t in self.hackable(player))
        if (cls == "STU_53F4CA65"
                and cfg is instance.graph.node(164).field("m_condition")["m_configVars"][0]):
            entity = expr.to_entity(instance.find_var(graph.INSTANCE, 8909).value())
            pack = self.fight.health_packs.get(entity)
            if pack is not None:
                player = self.player(body)
                return player is not None and health_packs.available_to(self.fight, pack, player)
        return NotImplemented

    def targets(self, player, state):
        from ow174.game.combat import aim, ray_capsule

        scope = state.instance.graph.index, state.node.pos
        if scope not in ((HACK, 50), (HACK, 52), (HACK, 68), (EMP, 40)):
            return NotImplemented
        if self.fight.is_dead(player):
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        if not math.isfinite(radius) or radius <= 0:
            return ()
        origin = self.fight._eye(player)
        direction = aim(player.mover.yaw, player.mover.pitch)
        selected = state.instance.find_var(graph.INSTANCE, 375).value()
        found = []
        for target in self.hackable(player, emp=scope[0] == EMP):
            if scope[0] == HACK and self.body_barrier(target) is not None:
                continue
            a, b = target.segment()
            offset = (a[0] - origin[0], max(a[1], min(b[1], origin[1])) - origin[1], a[2] - origin[2])
            distance = math.hypot(*offset)
            if scope == (HACK, 52):
                if not isinstance(selected, tuple) or expr.Entity(target.entity) not in selected:
                    continue
                if sum(axis * facing for axis, facing in zip(offset, direction, strict=True)) <= 0:
                    continue
            if scope in ((HACK, 52), (EMP, 40)):
                ray = tuple(axis / distance for axis in offset) if distance else direction
                reach = max(0.0, distance - target.radius)
            else:
                ray = direction
                reach = ray_capsule(origin, ray, a, b, target.radius)
            if reach is not None and reach <= radius and self.clear(player, origin, ray, reach, barriers=scope[0] == HACK):
                found.append((reach, target.entity))
        found.sort()
        return tuple(expr.Entity(entity) for _, entity in (found if scope[0] == EMP else found[:1]))

    def preselect(self, player, command):
        if player.hero.guid != HERO or not command.buttons & 2 or self.fight.is_dead(player):
            return
        component = player.body_script.component
        if component.strict:
            return
        for instance in component.instances.values():
            if instance.graph.index != HACK:
                continue
            ability = instance.state(53)
            key = (graph.INSTANCE, 375)
            if ability.flags & nodes.Ability.RUNNING and instance.find_var(*key).value():
                return
            search = instance.node_state(instance.graph.node(68))
            instance.write(key, self.targets(player, search))

    def ray(self, player, state):
        key = state.instance.find_var(graph.INSTANCE, 8909).value()
        target = next((target for target in self.hackable(player) if expr.Entity(target.entity) == key), None)
        if target is None or self.body_barrier(target) is not None:
            return runtime.RaycastResult(False, expr.Entity(0), None, None)
        origin = self.fight._eye(player)
        x, y, z = target.feet
        point = (x, y + target.eye, z)
        delta = tuple(end - start for start, end in zip(origin, point, strict=True))
        distance = math.hypot(*delta)
        clear = not distance or self.clear(player, origin, tuple(axis / distance for axis in delta),
                                           max(0.0, distance - target.radius))
        return runtime.RaycastResult(clear, expr.Entity(target.entity if clear else 0), expr.Vec3(*point), None)

    def play(self, instance, node):
        if instance.component.strict or instance.graph.index not in (HACK, EMP, PULSE):
            return NotImplemented
        resource = play.resolve_resource(instance, node)
        if resource is None:
            return NotImplemented
        definition = graph.graph(resource.guid & 0xFFFFFFFFFFFF)
        if definition is None or (instance.graph.index, definition.index) not in (
            (HACK, EFFECT), (EMP, PULSE), (PULSE, EFFECT),
        ):
            return NotImplemented
        source = instance.source_entity() if instance.graph.index == PULSE else instance.component.entity
        if instance.graph.index == EMP:
            caster = self.player(source)
            if caster is not None:
                self.drain_barriers(caster, instance, definition)
        context = play.InvocationContext(source, source, source)
        request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
        if request is None:
            return True
        fight = self.fight
        caster = self.player(source)
        self.apply_devices(instance, definition, resource, request, caster)
        if fight.health_packs:
            for entity in request.targets:
                pack = fight.health_packs.get(entity)
                if pack is None or caster is None or not health_packs.available_to(fight, pack, caster):
                    continue
                if definition.index == EFFECT:
                    health_packs.apply_hack(pack, definition, resource, request)
                else:
                    health_packs.submit(pack.component, definition, resource, request)
        for target in fight._targets():
            if isinstance(target.owner, actors.Deployable):
                continue
            if target.entity not in request.targets or fight.vitals_of(target).dead or fight.damage_immune(target.owner):
                continue
            if instance.graph.index == HACK and self.body_barrier(target) is not None:
                continue
            script = getattr(target.owner, "body_script", None)
            if script is None:
                continue
            application = script.component.play_loaded_graph(
                definition, source=request.context.caller, params=request.params,
                overrides=resource.overrides, policy=request.policy, instigator=request.context.instigator,
            )
            if application is not None and definition.index == EFFECT:
                duration = expr.to_float(application.evaluate(application.graph.node(96).field("m_value")))
                if math.isfinite(duration) and duration > 0:
                    locks = self.locks.setdefault(target.entity, [])
                    locks[:] = [lock for lock in locks if lock.application is not application]
                    pulse = instance if instance.graph.index == PULSE else None
                    locks.append(Lock(application, fight.now + duration, pulse))
                    held = getattr(script, "buttons", 0) & SKILL_INPUT
                    if held:
                        self.held[target.entity] = self.held.get(target.entity, 0) | held
                    if isinstance(target.owner, bots.Bot):
                        from ow174.game import assassin, heavy_assault

                        assassin.stop(target.owner)
                        heavy_assault.stop(script.component)
                    self.interrupt(target.owner)
                    fight.breathers.retire(target.entity)
            if script.component is not instance.component:
                script.component.run_now()
        return True

    def drain_barriers(self, caster, instance, definition):
        """Apply the barrier branch to visible geometry already hosted by combat."""
        fight = self.fight
        radius = expr.to_float(instance.evaluate(instance.graph.node(40).field("m_radius")))
        config = definition.node(3).field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(config["m_amount"]))
        if not all(math.isfinite(value) and value > 0 for value in (radius, amount)):
            return
        origin = fight._eye(caster)
        from ow174.game import sigma_barrier

        for manager, actors, closest in (
            (fight.barriers.world, fight.barriers.world.enemies(caster), fight.barriers.world.nearest_shell),
            (fight.orisa_barriers, fight.orisa_barriers.opponents(caster), fight.orisa_barriers.closest),
            (fight.photon_barriers, fight.photon_barriers.opponents(caster), fight.photon_barriers.closest),
        ):
            for barrier in tuple(actors):
                point = closest(barrier, origin)
                if point is not None and self.emp_reaches(caster, origin, point, radius):
                    manager.damage(barrier, amount)
        for barrier in tuple(sigma_barrier.enemies(fight, caster)):
            if barrier.closest_visible_distance(fight, caster, origin, radius) is not None:
                sigma_barrier.damage_barrier(fight, barrier, amount)
        raised = fight.barriers.reinhardt
        for barrier in tuple(raised.enemies(caster)):
            if barrier.near(origin, radius, lambda point: self.emp_reaches(caster, origin, point, radius)):
                raised.damage(barrier, amount)
        for barrier, centre, _, right, up in fight.brigitte._barrier_faces(caster):
            offset = tuple(origin[k] - centre[k] for k in range(3))
            horizontal = max(-1, min(1, sum(a * b for a, b in zip(offset, right, strict=True))))
            vertical = max(-1.1, min(1.1, sum(a * b for a, b in zip(offset, up, strict=True))))
            point = tuple(centre[k] + horizontal * right[k] + vertical * up[k] for k in range(3))
            if self.emp_reaches(caster, origin, point, radius):
                fight.brigitte.damage_barrier(barrier, amount)

    def emp_reaches(self, caster, origin, point, radius):
        delta = tuple(end - start for start, end in zip(origin, point, strict=True))
        distance = math.hypot(*delta)
        return distance <= radius and (not distance or self.clear(
            caster, origin, tuple(axis / distance for axis in delta), distance, barriers=False,
        ))

    def drain(self, state):
        fight = self.fight
        entity = state.instance.component.entity
        target = next((target for target in fight._targets() if target.entity == entity), None)
        if target is None:
            return
        vitals = fight.vitals_of(target)
        if vitals.dead:
            return
        amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth")["m_amount"], watch=False))
        if not math.isfinite(amount) or amount <= 0:
            return
        from ow174.game import barriers

        barrier = self.body_barrier(target)
        if isinstance(barrier, barriers.Barrier):
            barriers.damage(fight, barrier, amount)
            return
        if isinstance(barrier, barriers.Bubble):
            fight.barriers.damage(barrier, amount)
            return
        if barrier is not None:
            barrier.health = max(0.0, barrier.health - amount)
            return
        amount -= vitals.spend_temporary_shield(amount)
        for pool in vitals.extra.values():
            if pool.kind == SHIELDS_POOL and not pool.removed:
                taken = min(amount, pool.now)
                pool.now -= taken
                amount -= taken
                vitals.changed.add(SHIELDS_POOL)
        vitals.now[SHIELDS_POOL] = max(0.0, vitals.now[SHIELDS_POOL] - amount)
        vitals.changed.add(SHIELDS_POOL)
        fight._sync_body_health(target.owner, vitals)
