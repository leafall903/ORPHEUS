"""Partial server reconstruction of Junkrat deployables for build 104319.

096A/8 writes its projectile to5875; secondary sends003F to that entity.
0F9B's missing initial graph096B declares25 health. Placement geometry and
the engine query mapping below are reconstructed; native presentation is open.
"""

import math
from dataclasses import dataclass, replace

from ow174.game import actors, barriers, bots, immunity
from ow174.game.junkrat_charge import ChargeSystem
from ow174.game.junkrat_tire import FIRST as TIRE_FIRST
from ow174.game.junkrat_tire import Tire, TireSystem
from ow174.game.script import expr, graph, runtime

MINE = 0x0400000000000F9B
FIRST, LIMIT = 0xC0080000, 0xC0090000
DETONATE, REPLACED = 0x003F, 0x0511
MINE_HEALTH = 25.0  # 096B StateHealthPool/amount; target-build external XML
MINE_RADIUS = .2  # 0F9B/50BA775C/radius; capsule height is reconstructed
MINE_FULL_RADIUS, MINE_OUTER_RADIUS = 1.5, 3.0  # 096B setters45/46 and damage expression37
MINE_EDGE_DAMAGE = 30.0  # 096B setter44; full damage120 is supplied by096A/8
TRAP_PROJECTILE, TRAP_BODY = 0x04000000000011A9, 0x040000000000103A
TRAP_HEALTH = 100.0  # 103A initial0033 schema override199
TRAP_HIT_RADIUS = .25  # reconstructed damage capsule; native103A uses a model receiver
TRAP_RELEASE_VAR = 23154  # 09BB nodes 90/91
TRAP_EXCLUDE_VAR, TRAP_SHORT_VAR = 9588, 9161  # authored09BB filter / post-hit duration branch


@dataclass(kw_only=True)
class Mine(actors.Deployable):
    script: object
    instance: object
    output: tuple | None
    flight: object
    vitals: object
    deadline: float
    force: float
    parked: bool = False
    parked_at: float | None = None

    @property
    def creator(self):
        return self.owner

    @property
    def source(self):
        return self.source_body


@dataclass(kw_only=True)
class Trap(Mine):
    search_radius: float = 0.0
    duration: float = 0.0
    short_duration: float = 0.0
    arm_delay: float = 0.0
    damage: object = None
    armed_at: float | None = None
    victim: object = None
    victim_entity: int | None = None
    release_tick: int | None = None
    release_flag_seen: bool = False
    victim_status: runtime.Instance | None = None


class Junkrat:
    def __init__(self, combat):
        self.combat = combat
        self.mines: dict[int, Mine] = {}
        self.traps: dict[int, Trap] = {}
        self.roots: dict[int, dict[int, int]] = {}  # victim entity -> trap entity/end world tick
        self.next_entity = FIRST
        self.pending: list[tuple[int, int, int]] = []
        self.tire = TireSystem(combat)
        self.charge = ChargeSystem(combat)

    def cast(self, creator, state, origin, direction, speed, lifetime, gravity, blast, *, max_distance, launch_tick):
        from ow174.game.combat import Rocket, Vitals

        if self.next_entity >= LIMIT:
            raise RuntimeError("Junkrat deployable identity range exhausted")
        # The actual body graph sends0511 when replacing a mine. Retire here as
        # well so a lost/unsupported prior entity query cannot leave two active mines.
        for mine in list(self.mines.values()):
            if mine.creator is creator:
                self.retire(mine)
        entity, self.next_entity = self.next_entity, self.next_entity + 1
        blast_config = state.node.field("m_D2D11CE9") or {}
        flight = Rocket(
            creator, creator.team, origin, direction, speed, lifetime, None, blast,
            gravity=gravity, range_limit=max_distance, last_tick=launch_tick,
            detonate_on_expiry=expr.truthy(state.evaluate(blast_config.get("m_EEB99E41"), watch=False)),
            definition=MINE, source_body=creator.body, source_graph=state.instance.graph.index,
            source_state=state, matrix_absorbable=(MINE in self.combat.matrix_definitions
                if self.combat.matrix_definitions is not None else self.combat.projectile_tags.admitted(MINE)),
        )
        mine = Mine(
            entity=entity, definition=MINE, kind="junkrat-mine", team=creator.team,
            position=(origin[0], origin[1] - MINE_RADIUS, origin[2]), health=MINE_HEALTH,
            radius=MINE_RADIUS, height=MINE_RADIUS * 2, owner=creator, source_body=creator.body,
            created_tick=self.combat.tick, expires_tick=math.ceil((self.combat.now + lifetime) / .016),
            script=creator.body_script, instance=state.instance,
            output=expr.lvalue(state.node.field("m_505472EF")), flight=flight,
            vitals=Vitals(entity, MINE_HEALTH), deadline=self.combat.now + lifetime,
            force=float(state.evaluate(blast_config.get("m_modifyHealth", {}).get("m_E33EFEE1"), watch=False) or 0),
        )
        flight.mine = mine
        self.mines[entity] = mine
        self.combat.vitals[entity] = mine.vitals
        flight.velocity = tuple(axis * speed for axis in direction)
        self.combat.rockets.append(flight)
        if mine.output is not None:
            state.instance.write(mine.output, expr.Entity(entity))
        return mine

    def cast_trap(self, creator, state, origin, direction, speed, lifetime, gravity, *, max_distance, launch_tick):
        from ow174.game.combat import HitDef, Rocket, Vitals

        profile = graph.graph(0x09BB)
        if profile is None or self.next_entity >= LIMIT:
            raise RuntimeError("trap graph or deployable identity unavailable")
        for trap in list(self.traps.values()):
            if trap.creator is creator:
                self.retire(trap)
        entity, self.next_entity = self.next_entity, self.next_entity + 1
        modify = profile.node(4).field("m_modifyHealth")
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        flight = Rocket(
            creator, creator.team, origin, direction, speed, lifetime, None, None,
            gravity=gravity, range_limit=max_distance, last_tick=launch_tick, definition=TRAP_PROJECTILE,
            source_body=creator.body, source_graph=state.instance.graph.index, source_state=state,
            matrix_absorbable=(TRAP_PROJECTILE in self.combat.matrix_definitions
                if self.combat.matrix_definitions is not None else self.combat.projectile_tags.admitted(TRAP_PROJECTILE)),
        )
        trap = Trap(
            entity=entity, definition=TRAP_BODY, kind="junkrat-trap", team=creator.team,
            position=origin, health=TRAP_HEALTH, radius=TRAP_HIT_RADIUS,
            height=TRAP_HIT_RADIUS * 2, owner=creator, source_body=creator.body,
            created_tick=self.combat.tick, expires_tick=math.ceil((self.combat.now + lifetime) / .016),
            script=creator.body_script, instance=state.instance,
            output=expr.lvalue(state.node.field("m_505472EF")), flight=flight,
            vitals=Vitals(entity, TRAP_HEALTH), deadline=self.combat.now + lifetime, force=0.0,
            search_radius=float(state.evaluate(profile.node(2).field("m_radius"), watch=False)),
            duration=float(state.evaluate(profile.node(75).field("m_value"), watch=False)),
            short_duration=float(state.evaluate(profile.node(76).field("m_value"), watch=False)),
            arm_delay=float(state.evaluate(profile.node(3).field("m_timeout"), watch=False)),
            damage=HitDef(
                float(state.evaluate(modify.get("m_amount"), watch=False)),
                loadout.guid if isinstance(loadout, expr.Asset) else None,
            ),
        )
        flight.mine = trap  # shared sticky-flight handling; planted state is separate below
        self.traps[entity] = trap
        self.combat.vitals[entity] = trap.vitals
        flight.velocity = tuple(axis * speed for axis in direction)
        self.combat.rockets.append(flight)
        if trap.output is not None:
            state.instance.write(trap.output, expr.Entity(entity))
        return trap

    def targets(self):
        from ow174.game.combat import Target

        for mine in self.mines.values():
            x, y, z = mine.flight.position
            yield Target(mine.entity, mine.flight.team, (x, y - MINE_RADIUS, z),
                         MINE_RADIUS * 2, MINE_RADIUS, MINE_RADIUS, mine,
                         critical_allowed=False, construct=mine)
        for trap in self.traps.values():
            if trap.parked:
                x, y, z = trap.flight.position
                yield Target(trap.entity, trap.flight.team, (x, y, z),
                             TRAP_HIT_RADIUS * 2, TRAP_HIT_RADIUS, TRAP_HIT_RADIUS,
                             trap, critical_allowed=False, construct=trap)
        yield from self.tire.targets()

    def lookup(self, entity):
        return self.mines.get(entity) or self.traps.get(entity) or self.tire.actors.get(entity)

    def sync_pose(self, mine):
        if self.lookup(mine.entity) is not mine:
            return
        x, y, z = mine.flight.position
        mine.position = (x, y, z) if isinstance(mine, Trap) else (x, y - MINE_RADIUS, z)

    def flight_step(self, rocket, duration, heading, step, best, range_ends):
        from ow174.game.combat import EPSILON, _along

        limit = best[0] if best is not None else step
        absorbed = self.combat._trace_absorption(
            rocket.shooter, rocket.definition, rocket.position, heading, limit,
            admitted=rocket.matrix_absorbable, matrix_only=True,
        )
        if absorbed is not None and (best is None or absorbed[0] < best[0]):
            self.retire(rocket.mine)
            return 0.0
        if best is not None:
            distance = best[0]
            rocket.flown += duration * distance / step if step > EPSILON else 0.0
            rocket.travelled += distance
            rocket.position = _along(rocket.position, heading, max(0.0, distance - 1e-4))
            rocket.mine.parked = True
            rocket.mine.parked_at = self.combat.now
        else:
            rocket.position = _along(rocket.position, heading, step)
            rocket.flown += duration
            rocket.travelled += step
            rocket.left -= duration
            if range_ends or rocket.left <= 0:
                self.combat._expire(rocket)
        return 0.0

    def query(self, cfg, instance):
        cls = cfg.get("$")
        if cls not in ("STU_E72F6348", "STU_9B7648C3", "STU_2B2E2F6D"):
            return NotImplemented
        value = instance.evaluate(cfg.get("m_entity") or cfg.get("m_81A364DE"))
        if not isinstance(value, expr.Entity):
            return NotImplemented
        if TIRE_FIRST <= value.id < self.tire.next_entity:
            mine = self.tire.actors.get(value.id)
        elif FIRST <= value.id < self.next_entity:
            mine = self.mines.get(value.id) or self.traps.get(value.id)
        else:
            return NotImplemented
        if cls == "STU_E72F6348":
            return mine is not None
        if cls == "STU_9B7648C3":
            # Preserve the original live gate through the actual shared component51 pool.
            return mine.vitals.total if mine is not None else 0.0
        return expr.Entity(mine.source) if mine is not None else None

    def message(self, target, message, sender):
        message &= 0xFFFF  # World receives the full025 GUID; body entries use its index.
        mine = self.mines.get(target) or self.traps.get(target)
        if mine is None or message not in (DETONATE, REPLACED):
            return False
        if sender != mine.source:
            return False
        if message == DETONATE and self.combat.is_dead(mine.creator):
            return False
        # Consume outside the body VM callback. No recursive damage/death while
        # its current variable/dependency traversal is still running.
        self.pending.append((target, message, sender))
        return True

    @staticmethod
    def _source_retired(mine):
        return (mine.creator.body != mine.source or mine.creator.body_script is not mine.script
                or mine.script.retired)

    def update(self):
        for mine in [*self.mines.values(), *self.traps.values()]:
            self.sync_pose(mine)
        for mine in [*self.mines.values(), *self.traps.values()]:
            if (self._source_retired(mine)
                    or (mine.parked and not isinstance(mine, Trap) and self.combat.now >= mine.deadline)):
                self.retire(mine)
        pending, self.pending = self.pending, []
        for entity, message, sender in pending:
            mine = self.mines.get(entity) or self.traps.get(entity)
            if mine is None or sender != mine.source:
                continue
            if message == DETONATE and not self.combat.is_dead(mine.creator):
                if isinstance(mine, Trap):
                    continue  # a Steel Trap is triggered by enemies, never secondary-fire explosion
                blast = mine.flight.blast
                if blast is not None and blast.amount > 0:
                    # A remote detonation uses the 096B actor's falloff curve.
                    mine.flight.blast = replace(blast, rings=(
                        (MINE_FULL_RADIUS, 1.0), (MINE_OUTER_RADIUS, MINE_EDGE_DAMAGE / blast.amount),
                    ))
                self.detonate(mine)
            elif message == REPLACED:
                self.retire(mine)
        self.update_traps()
        self.tire.update()
        self.charge.update()

    def rooted(self, entity, tick):
        return any(end > tick for end in self.roots.get(entity, {}).values())

    @staticmethod
    def target_flag(target, identifier):
        script = getattr(target.owner, "body_script", None)
        value = script.component.entity_var(identifier, create=False) if script is not None else None
        return value is not None and expr.truthy(value.value())

    def trap_deadlines(self):
        from ow174.game.combat import TICK

        fight = self.combat
        for trap in self.traps.values():
            if not trap.parked or self._source_retired(trap):
                continue
            if trap.victim is not None:
                yield trap.release_tick, trap
            elif trap.parked_at is not None:
                armed_at = trap.armed_at if trap.armed_at is not None else trap.parked_at + trap.arm_delay
                yield fight.tick + math.ceil((armed_at - fight.now - 1e-9) / TICK), trap

    def update_traps(self, traps=None):
        from ow174.game.combat import TICK, _source_entity, segment_distance

        fight = self.combat
        if traps is None:
            traps = list(self.traps.values())
        for trap in traps:
            if not trap.parked:
                continue
            if trap.armed_at is None:
                trap.armed_at = (trap.parked_at if trap.parked_at is not None else fight.now) + trap.arm_delay
            if trap.victim is not None:
                victim = next((target for target in fight._targets()
                               if target.entity == trap.victim_entity), None)
                if (fight.tick >= trap.release_tick or _source_entity(trap.victim) != trap.victim_entity
                        or victim is None or fight.vitals_of(victim).dead):
                    self.retire(trap)
                    continue
                flag = self.target_flag(victim, TRAP_RELEASE_VAR)
                if ((trap.victim_status is not None and trap.victim_status.stopped)
                        or (trap.release_flag_seen and not flag)):
                    self.retire(trap)
                else:
                    trap.release_flag_seen = flag
                continue
            if fight.now + 1e-9 < trap.armed_at:
                continue
            candidates = []
            for target in fight._enemies(trap.creator):
                if (isinstance(target.owner, actors.Deployable) or self.target_flag(target, TRAP_EXCLUDE_VAR)
                        or immunity.phased_out(target.owner, target.entity)):
                    continue
                a, b = target.segment()
                distance = max(0.0, segment_distance(trap.flight.position, a, b) - target.radius)
                if distance <= trap.search_radius and fight._blast_visible(trap.flight.position, target):
                    candidates.append((distance, target.entity, target))
            if not candidates:
                continue
            _, _, target = min(candidates, key=lambda row: row[:2])
            # Reconstructed attached-barrier routing: consume the trigger,
            # but do not apply its body damage or root through the barrier.
            remaining = barriers.protect_body(fight, target, trap.damage.amount, attacker=trap.source)
            if remaining is not None:
                self.retire(trap)
                continue
            bubble = fight.barriers.for_target(target.owner)
            if bubble is not None:
                fight.barriers.damage(bubble, trap.damage.amount, trap.creator)
                self.retire(trap)
                continue
            trap.victim, trap.victim_entity = target.owner, target.entity
            fight._damage(
                trap.creator, target, trap.damage.amount, False, trap.damage.loadout,
                fight.tick, world_tick=fight.tick,
            )
            if fight.vitals_of(target).dead:
                self.retire(trap)
                continue
            duration = trap.short_duration if self.target_flag(target, TRAP_SHORT_VAR) else trap.duration
            trap.release_tick = fight.tick + math.ceil(duration / TICK)
            status_graph = graph.graph(0x09BC)
            script = getattr(target.owner, "body_script", None)
            if status_graph is not None and script is not None:
                component = script.component
                trap.victim_status = component.create(status_graph)
                runtime.send_instance_message(trap.victim_status, 0x024000000000000F, {}, trap.entity)
                component.run_now()
            trap.release_flag_seen = self.target_flag(target, TRAP_RELEASE_VAR)
            self.roots.setdefault(target.entity, {})[trap.entity] = trap.release_tick
            if isinstance(target.owner, bots.Bot):
                fight.knockbacks.retire(target.entity)
                target.owner.velocity = (0.0, 0.0, 0.0)
            else:
                target.owner.mover.velocity = (0.0, 0.0, 0.0)

    def detonate(self, mine):
        self.retire(mine)
        protected = set()
        self.combat._explode(mine.flight, mine.flight.position, protected_out=protected)
        self.knockback(mine, protected)

    def knockback(self, mine, protected):
        """Reconstructed radial impulse from096A's authored15m/s magnitude.

        Self-launch deals no self damage; teammate bodies and other deployables
        do not receive the offensive displacement. Native vector composition,
        resistance and disabled-movement interactions remain open.
        """
        from ow174.game.combat import segment_distance

        if mine.force <= 0 or mine.flight.blast is None:
            return
        fight = self.combat
        radius = max((ring[0] for ring in mine.flight.blast.rings), default=0)
        for target in fight._targets():
            if isinstance(target.owner, actors.Deployable) or fight.vitals_of(target).dead:
                continue
            if (target.entity in protected or self.rooted(target.entity, fight.tick)
                    or immunity.control_immune(target.owner, target.entity)
                    or immunity.phased_out(target.owner, target.entity)
                    or immunity.cryo_active(target.owner, target.entity)):
                continue  # a radial impulse does not bypass an active trap restraint
            if (target.owner is not mine.creator and not fight.match.game_map.free_for_all
                    and target.team == mine.flight.team):
                continue
            a, b = target.segment()
            point = mine.flight.position
            if (segment_distance(point, a, b) - target.radius > radius
                    or not fight._blast_visible(point, target)):
                continue
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            offset = tuple(axis - start for axis, start in zip(centre, point, strict=True))
            length = math.hypot(*offset)
            vector = (
                tuple(axis * mine.force / length for axis in offset) if length > 1e-9 else (0, mine.force, 0)
            )
            fight.knockbacks.apply(target, expr.Vec3(*vector), impact_frame=fight.tick)

    def release_trap_victim(self, entity):
        for trap in list(self.traps.values()):
            if trap.victim_entity == entity:
                self.retire(trap)

    def retire_body(self, entity):
        self.tire.retire_body(entity)
        for mine in list(self.mines.values()):
            if mine.source == entity:
                self.retire(mine)
        for trap in list(self.traps.values()):
            if entity in (trap.source, trap.victim_entity):
                self.retire(trap)

    def retire(self, mine):
        if isinstance(mine, Tire):
            self.tire.finish(mine, "destroyed")
            return
        storage = self.traps if isinstance(mine, Trap) else self.mines
        if storage.pop(mine.entity, None) is not mine:
            return
        if isinstance(mine, Trap) and mine.victim_entity is not None:
            if mine.victim_status is not None:
                mine.victim_status.component.destroy(mine.victim_status)
                mine.victim_status = None
            roots = self.roots.get(mine.victim_entity, {})
            roots.pop(mine.entity, None)
            if not roots:
                self.roots.pop(mine.victim_entity, None)
        self.combat._landed(mine.flight)
        self.combat.vitals.pop(mine.entity, None)
        self.combat.history.pop(mine.entity, None)
        self.combat.replacement_ticks.pop(mine.entity, None)
        if (mine.output is not None and not mine.instance.stopped
                and mine.instance.find_var(*mine.output).value() == expr.Entity(mine.entity)):
            mine.instance.write(mine.output, 0)
