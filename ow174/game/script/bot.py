"""The NPC body's statescript, shared by all observers."""

from ow174.game import heavy_assault, npc_melee, observers, sigma_flux, sniper_special
from ow174.game.bits import BitWriter
from ow174.game.bots import FIRING
from ow174.game.content import PRACTICE_RANGE_MODE
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import (
    OWNER_HEALTH,
    ReplicatedBody,
    Snapshot,
    create_body_graphs,
    play_sombra_body_script,
)

DIED = 0x024000000000003E  # 003E.025, the body's authoritative death message
RESET = 0x0240000000000013  # 0013.025, the placement's eligibility reset
FIRING_GRAPH = FIRING & 0xFFFF
PRACTICE_NAMEPLATE = 0x0D24
PRACTICE_NAMEPLATE_PARENT = 0x0C18
PRACTICE_BOT_HEROES = frozenset(range(0x02E000000000016B, 0x02E000000000016F))
PRACTICE_NAMEPLATE_FILTER = {"$": "STUConfigVarFilter", "m_84065FD1": 0x3FFFFFFF, "m_4EA686DB": 0x100}


def practice_nameplate(snapshot: Snapshot) -> int | None:
    for instance, (graph_id, parent) in snapshot.instances.items():
        if (
            graph_id == PRACTICE_NAMEPLATE and parent is not None and parent[1] == 3
            and snapshot.instances.get(parent[0], (None, None))[0] == PRACTICE_NAMEPLATE_PARENT
        ):
            return instance
    return None


class BotWorld(runtime.World):
    """Resolve the supported authored predicates and input bindings for an NPC body."""

    def __init__(
        self, hero: int, mode: int, weapon_buttons: dict[str, int], owner_position: expr.Vec3 | None = None,
        *, controller_components: tuple[int, ...] | None = None,
    ) -> None:
        super().__init__(hero, mode)
        self.weapon_buttons = weapon_buttons
        self.owner_position = owner_position
        self.match = None  # explicit live-body services installed by Match after initialization
        self.controller_components = controller_components
        self.manual_target = None

    @property
    def combat(self):
        return self.match.combat if self.match is not None else None

    def body_ability_allowed(self, state):
        return self.combat is None or self.combat.body_ability_allowed(state)

    def body_button_allowed(self, component, button):
        return self.combat is None or self.combat.body_button_allowed(component, button)

    def play_body_script(self, instance, node):
        play_sombra_body_script(self, instance, node)

    def body_modify_health(self, state):
        if self.combat is not None:
            self.combat.apply_emp_health(state)
            npc_melee.modify_health(self, state)
            heavy_assault.entry_damage(self, state)

    def track_body_targets(self, state):
        from ow174.game import dva

        if dva.shell_targets(self, state):
            return NotImplemented  # the blast query writes its paired arrays once
        if state.instance.graph.index == dva.BOOSTERS and self.combat is not None:
            actor = next((bot for bot in self.match.bots if bot.entity == state.instance.component.entity
                          and bot.dva_mover is not None and not bot.dead), None)
            return dva.boost_targets(self.combat, actor, state)
        result = heavy_assault.targets(self, state)
        return npc_melee.targets(self, state) if result is NotImplemented else result

    def permissive_state_enter(self, state):
        sniper_special.search(self, state)

    def entity_var_slot(self, entity: int, var: int, *, create: bool = False):
        controller = getattr(self, "dva_controller", None)
        if controller is not None and self.match is not None and var == 10640:
            pilot = self.match._message_player(entity)
            if pilot is not None and pilot.entity == controller and pilot.body_script is not None:
                return pilot.body_script.component.entity_var(var, create=False)
        if self.hero == heavy_assault.HERO and self.match is not None:
            player = next((p for p in self.match.players if p.has_body and p.body == entity), None)
            if player is not None and player.body_script is not None:
                return player.body_script.component.entity_var(var, create=create)
        return super().entity_var_slot(entity, var, create=create)

    def position(self, entity: int):
        if self.hero == 0x02E00000000001BA and self.match is not None:
            position = self.match.script_body_position(entity, entity)
            if position is not None:
                return position
        return super().position(entity)

    def body_raycast(self, state):
        if (self.hero != 0x02E00000000001BA or self.match is None
                or state.instance.graph.index != 0x1408 or state.node.pos not in (171, 172)):
            return NotImplemented
        from ow174.game.script.nodes import raycast_request

        collision = self.match.collision_world
        if collision is None or npc_melee.attacker(self.combat, state) is None:
            return NotImplemented
        request = raycast_request(state.instance, state.node)
        if request is None:
            return NotImplemented
        # The static host uses mover collision for the authored raised probe.
        return collision.cast_ray(tuple(request.origin), tuple(request.direction), request.length) is not None

    def ai_weapon_button(self, slot: int) -> int | None:
        if self.hero == 0x02E00000000001BB and self.controller_components is not None and slot == 0:
            return 36
        return self.weapon_buttons.get(str(slot))

    def selects_ai_behavior(self, instance: runtime.Instance) -> bool:
        # Reconstruction policy for the single authored firing placement behavior.
        return self.owner_position is not None and instance.graph.index == FIRING_GRAPH

    def send_message(self, target: int, message: int, params: dict, sender: int) -> None:
        from ow174.game import dva

        if (self.match is not None and message in (dva.RETURN_TO_PILOT, dva.SELF_DESTRUCT_COMPLETE)
                and self.match.queue_body_message(target, message, params, sender)):
            return
        super().send_message(target, message, params, sender)

    def native(self, cfg: dict, instance: runtime.Instance):
        if instance is not None:
            result = sigma_flux.flux_effect_native(self, cfg, instance)
            if result is not NotImplemented:
                return result
        from ow174.game import assassin, npc_context, orisa_halt

        result = orisa_halt.native(self, cfg, instance)
        if result is not NotImplemented:
            return result

        if (getattr(self, 'dva_controller', None) is not None and instance is not None
                and instance.graph.index in (0x0276, 0x02CC, 0x0B2F) and cfg.get('$') == 'STU_F7262B36'
                and expr.to_entity(instance.evaluate(cfg.get('m_801B298F'))) == instance.component.entity):
            return expr.Entity(self.dva_controller)
        if (getattr(self, 'dva_controller', None) is not None and instance is not None
                and instance.graph.index in (0x02CC, 0x0B2F) and cfg.get('$') == 'STU_FDBB0B67'
                and expr.to_entity(instance.evaluate(cfg.get('m_entity'))) == instance.component.entity):
            return 1.0
        if (self.match is not None and getattr(self, 'dva_controller', None) is not None
                and instance is not None and instance.graph.index in (0x0276, 0x0B2F)
                and cfg.get('$') == 'STU_0AC15F78'
                and expr.to_entity(instance.evaluate(cfg.get('m_entity'))) == self.dva_controller):
            pilot = next((p for p in self.match.players
                          if p.entity == self.dva_controller and not p.gone), None)
            return expr.Entity(pilot.body if pilot is not None else 0)
        if (instance is not None and instance.graph.index == 0x0B2F and cfg.get('$') == 'STU_F76C486C'
                and (cfg.get('m_D63CBEC2') or {}).get('$') == 'STU_B5A0CAF0'):
            from ow174.game import dva

            shell = dva.shell_owner(self, instance)
            if shell is not None:
                return expr.Entity(shell.spawner['dva_controller'])

        value = assassin.native(self, cfg, instance)
        if value is NotImplemented:
            value = npc_context.native(self, cfg, instance)
        if value is NotImplemented:
            value = heavy_assault.native(self, cfg, instance)
        if value is NotImplemented:
            value = sniper_special.native(self, cfg, instance)
        if value is not NotImplemented:
            return value
        if self.combat is not None:
            if (self.hero == sniper_special.HERO and instance is not None
                    and instance.graph.index == sniper_special.GRAPH and cfg.get("$") == "STU_71A87E92"
                    and expr.to_entity(instance.evaluate(cfg.get("m_entity"))) == instance.component.entity):
                entity = instance.component.entity
                return self.match.script_body_position(entity, entity)
            if (self.hero == 0x02E00000000001BA and instance is not None and instance.graph.index == 0x1408
                    and cfg.get("$") == "STU_2200A66D"
                    and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
                from ow174.game.combat import aim

                bot = next((bot for bot in self.match.bots if bot.entity == instance.component.entity
                            and not bot.dead and bot.spawner.get("manual_spawn") is True), None)
                if bot is not None:
                    # The authored graph flattens this facing vector and selects its cast direction.
                    return expr.Vec3(*aim(bot.yaw, 0))
            if cfg.get("$") == "STU_B96D3A98" and instance.graph.index in (0x13EB, 0x1122, 0x1134):
                # The manual engagement owner supplies the AI permission branch;
                # no native autonomous-controller behavior is claimed here.
                entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
                target = next((t for t in self.combat._targets() if t.entity == entity), None)
                if target is not None:
                    owner = target.owner
                    return (getattr(owner, "spawner", {}).get("manual_spawn") is True
                            and not owner.dead and owner.firing)
            if cfg.get("$") == "STU_E72F6348" and instance.graph.index == 0x0B1F:
                entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
                return any(t.entity == entity for t in self.combat._targets())
            if cfg.get("$") == "STU_9B7648C3" and instance.graph.index in (0x13EB, 0x1122, 0x1134):
                # Reconstructed living-body eligibility for these weapon graphs'
                # sole >0 comparison. This is not a general native query meaning.
                entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
                target = next((t for t in self.combat._targets() if t.entity == entity), None)
                if target is not None:
                    vitals = self.combat.vitals_of(target)
                    return 0.0 if vitals.dead else vitals.total
        if (
            cfg.get("$") == "STU_AC829876"
            and self.mode == PRACTICE_RANGE_MODE
            and self.hero in PRACTICE_BOT_HEROES
            and instance.graph.index == PRACTICE_NAMEPLATE_PARENT
        ):
            owner, target_filter = cfg.get("m_entity"), cfg.get("m_filter")
            # Reconstruct the stock 0C18 owner gate for its authored 0D24 child.
            # The native meaning of filter flag 0x100 remains unverified.
            if (
                owner in ({"$": "STU_B5A0CAF0"}, {"$": "STU_B5A0CAF0", "m_EE729DCB": 0})
                and isinstance(target_filter, dict)
                and {key: value for key, value in target_filter.items() if value not in (None, 0)}
                == PRACTICE_NAMEPLATE_FILTER
            ):
                return True
        if self.selects_ai_behavior(instance):
            owner = cfg.get("m_entity")
            if isinstance(owner, dict) and owner.get("$") == "STU_0AC15F78":
                entity = owner.get("m_entity")
                if isinstance(entity, dict) and entity.get("$") == "STU_B5A0CAF0":
                    if cfg.get("$") == "STU_71A87E92":
                        return self.owner_position
                    if cfg.get("$") == "STU_295D117D":
                        return None  # this NPC has no player possessor
        return super().native(cfg, instance)

    def destroy_body_entity(self, instance, node):
        from ow174.game import dva

        shell = dva.shell_owner(self, instance)
        if shell is not None and node.pos == 13:
            shell.dva_shell_spent = True

    def modify_health(self, state, elapsed_ms):
        from ow174.game import assassin, dva

        result = dva.boost_damage(self, state, elapsed_ms)
        if result is not NotImplemented:
            return result
        result = sigma_flux.flux_modify_health(self, state, elapsed_ms)
        if result is not NotImplemented:
            return result
        result = dva.shell_damage(self, state, elapsed_ms)
        if result is NotImplemented:
            result = heavy_assault.secondary_damage(self, state, elapsed_ms)
        return assassin.modify_health(self, state, elapsed_ms) if result is NotImplemented else result

    def end_health(self, state):
        from ow174.game import assassin

        if hasattr(state, "heavy_contacts"):
            del state.heavy_contacts
        assassin.end_health(self, state)


class BotScript(ReplicatedBody):
    """Run the NPC's body and supported placement behavior on the server clock."""

    def __init__(
        self, body: int, hero: int, entity: int, frame: int, mode: int, health: float,
        *, firing_position: tuple | None = None, record: dict | None = None,
        world: runtime.World | None = None, resolve_graph=None, source_entity: int | None = None,
        controller_components: tuple[int, ...] | None = None,
        dva_controller: int | None = None,
    ) -> None:
        record = graph.entity_body(body) if record is None else record
        if record is None:
            raise ValueError(f"no statescript data for entity body {body:016X}")
        self.frame = frame
        buttons = record.get("ai_weapon_buttons") or {}
        firing = graph.graph(FIRING_GRAPH) if buttons and firing_position is not None else None
        position = expr.Vec3(*firing_position) if firing is not None else None
        world = world or BotWorld(hero, mode, buttons, position, controller_components=controller_components)
        if dva_controller is not None:
            world.dva_controller = dva_controller
        component = self.component = runtime.Component(entity, world=world, seed=entity if firing else 1)
        component.frame = frame
        component.now = frame * component.frame_ms
        self.set_health(health, health)
        for instance in create_body_graphs(component, record, resolve_graph=resolve_graph):
            if source_entity is not None:
                instance.set_source_entity(source_entity)
            instance.init()
        component.run_frame(frame)
        self.server_only = self.initial_exclusions()
        self.behavior = component.create(firing) if firing is not None else None
        if self.behavior is not None:
            # Scope the reconstructed spawn reset to this instance. Other 0013 receivers have their own rules.
            runtime.send_instance_message(self.behavior, RESET, {}, entity)
            component.run_now()
        snapshot = observers.snapshot(self)
        self.latest_live = snapshot
        self._initial_frame: BitWriter | None = observers.full_frame(self, snapshot)
        self.death_frame: int | None = None
        self._produced = snapshot

    def _initial_payload(self) -> BitWriter:
        if self._initial_frame is None:
            self._initial_frame = observers.full_frame(self, self.latest_live)
        return self._initial_frame

    @property
    def initial(self) -> tuple[BitWriter, Snapshot]:
        """Encode the current live baseline only when a viewer needs its full frame."""
        return self._initial_payload(), self.latest_live

    def replicated_instances(self):
        return (instance for instance in super().replicated_instances() if instance is not self.behavior)

    def advance_live(self, frame: int, position: tuple | None = None) -> Snapshot | None:
        """Advance a live weapon body and freeze its latest observer baseline."""
        if frame <= self.frame or self.component.owner_dead:
            return None
        self.frame = frame
        if self.behavior is not None and position is not None:
            self.component.world.owner_position = expr.Vec3(*position)
        self.component.run_frame(frame)
        from ow174.game import assassin

        assassin.refresh_control(self.component.world, self.component)
        self._report_shots()
        snapshot = self._changed_snapshot()
        if snapshot is not None:
            self.latest_live = snapshot
            self._initial_frame = None
        return snapshot

    def die(self, frame: int, params: dict, sender: int) -> Snapshot | None:
        """Deliver 003E.025 and capture the change that happens immediately."""
        component = self.component
        if component.owner_dead:
            return None
        if frame < self.frame:
            raise ValueError("bot death predates its script")
        # A terminal graph may remove instances needed to encode the last live baseline.
        self._initial_payload()
        from ow174.game import assassin

        assassin.bind(component.world, component, None)
        # A bot can die in the tick that created it. Its initial frame is already
        # frozen, so run the terminal event after the bootstrap simulation frame.
        self.frame = max(frame, self.frame + 1)
        component.owner_dead = True
        if self.behavior is not None:
            component.destroy(self.behavior)
            self.behavior = None
        component.shots.clear()
        component.run_frame(self.frame)
        component.held.clear()
        component.set(OWNER_HEALTH, 0.0)
        if not runtime.send_message(component, DIED, params, sender):
            return None
        component.run_now()
        self.death_frame = frame
        return self._changed_snapshot()

    def advance_death(self, frame: int, lifetime_ticks: int) -> list[Snapshot]:
        """Run due graph events within this death's lifetime and retain each frame's change in order."""
        if self.death_frame is None:
            return []
        limit = min(frame, self.death_frame + lifetime_ticks)
        component = self.component
        payloads = []
        while (due := component.next_event_time()) is not None:
            due_frame = due // component.frame_ms
            if due_frame > limit:
                break
            self.frame = due_frame
            component.run_frame(due_frame)
            payload = self._changed_snapshot()
            if payload is not None:
                payloads.append(payload)
        return payloads

    def _changed_snapshot(self) -> Snapshot | None:
        snapshot = observers.snapshot(self)
        before = self._produced
        if (
            snapshot.instances == before.instances
            and snapshot.vars == before.vars
            and snapshot.states == before.states
        ):
            return None
        self._produced = snapshot
        return snapshot
