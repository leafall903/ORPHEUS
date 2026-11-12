"""Explicit static consumer for Zarya0244/025C's authored barrier pets.

DATA: pets13F4/03DA, model10D7 sphere1.5,0256 pool default200/ruleset1D43 and native
StatePet lifetime/counter. RECONSTRUCTED: capsule-centre attachment, private
server identity, whole-hit overkill consumption and break -> StatePet completion.
Native pet/PlayScript hosting, bone pose, energy/credit/filter policy and wire
replication remain open. This is a finite collision target, not body invulnerability.
"""
import math
from dataclasses import dataclass, replace

from ow174.game import barrier_geometry, bots, contacts, reinhardt_barrier, world_bubbles
from ow174.game.reinhardt_barrier import Geometry, configured_geometry, frame, intersect, load_geometry, mesh
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, play, play_queue, runtime

RADIUS = 1.5  # 10D7.00C MSTU/m_CB4D298D/STU_4614D0E5.m_radius
FIRST, SPAN = 0xA0100000, 0x00040000  # private static identities; no native wire acceptance


@dataclass
class Bubble:
    entity: int
    owner: object
    body: int
    phase: object
    counter: int
    definition: int
    maximum: float
    health: float
    caster: object
    caster_body: int

    def centre(self):
        if isinstance(self.owner, bots.Bot):
            data = mover_data(self.owner.body)
            height, position = data.stand_height, self.owner.position
        else:
            data = mover_data(self.owner.hero.body)
            height = data.crouch_height if self.owner.mover.crouched else data.stand_height
            position = self.owner.mover.position
        x, y, z = position
        return x, y + height * .5, z


def _source_entity(shooter):
    from ow174.game.combat import _source_entity

    return _source_entity(shooter)


def sphere(origin, direction, centre, radius, length):
    delta = tuple(p-c for p, c in zip(origin, centre, strict=True))
    c = sum(p*p for p in delta) - radius*radius
    if c <= 0:
        return 0.0
    b = sum(p*d for p, d in zip(delta, direction, strict=True))
    discriminant = b*b-c
    if discriminant < 0:
        return None
    distance = -b-math.sqrt(discriminant)
    return distance if 0 <= distance <= length else None


@dataclass
class BarrierExpiry:
    owner: object
    duration: object
    generation: int
    frame: int
    tick: int
    started: int
    ended_ahead: bool = False


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.live = {}
        self.scripted: dict[int, Barrier] = {}
        self.expiries: dict[tuple[int, bool], BarrierExpiry] = {}
        self.cooldown_deadlines: dict[tuple[int, bool], int] = {}
        self.seen = {}
        self.next_entity = 0
        self.world = world_bubbles.WorldBubbles(fight)
        self.reinhardt = reinhardt_barrier.ReinhardtBarriers(fight)

    def retire(self, body):
        for projected in (False, True):
            self.expiries.pop((body, projected), None)
            self.cooldown_deadlines.pop((body, projected), None)
        self.world.retire(body)
        self.reinhardt.actors.pop(body, None)
        self.live = {k: b for k, b in self.live.items() if b.body != body and b.caster_body != body}
        self.seen = {k: v for k, v in self.seen.items() if k[0] != body}

    def ability_start_allowed(self, state):
        index = state.instance.graph.index
        if (index, state.node.pos) not in ((0x0244, 3), (0x025C, 21)):
            return True
        key = state.instance.component.entity, index == 0x025C
        return key not in self.expiries and self.fight.match.tick >= self.cooldown_deadlines.get(key, 0)

    def _schedule_expiry(self, caster, duration, projected):
        from ow174.game.combat import TICK
        from ow174.game.projectiles import MAX_LEASE_FRAME

        from ow174.game.script import nodes

        if not isinstance(duration, nodes.ChaseVar) or not duration.active:
            return
        script = caster.body_script
        remaining = duration.remaining
        if remaining is None:
            rate = expr.to_float(duration.evaluate(duration.node.field("m_9B0385B4"), watch=False))
            if rate <= 0:
                return
            remaining = round(abs(expr.to_float(duration.cur) - expr.to_float(duration.destination())) * 1000
                              / rate)
        frame = (duration.last + remaining - 1) // script.frame_ms
        anchor = duration.instance.component.frame  # actual ctor callback; BodyScript.frame commits after command
        if anchor < frame < MAX_LEASE_FRAME:
            elapsed = (frame - anchor) * script.frame_ms
            started = caster.match.tick
            tick = started + math.ceil(elapsed / (TICK * 1000))
            expiry = BarrierExpiry(caster, duration, duration.counter, frame, tick, started)
            self.expiries[caster.body, projected] = expiry

    def clock_edges(self, tick):
        return {edge for expiry in self.expiries.values()
                for edge in (expiry.tick, expiry.tick + 1, expiry.tick + 2)
                if self.fight.tick < edge <= tick}

    @staticmethod
    def _duration_ended_on_deadline(expiry, frame_ms):
        # An accepted input frame can finish the duration before the world replays
        # an earlier contact. Keep that activation until its mapped expiry tick.
        duration = expiry.duration
        return (not duration.active and duration.counter == expiry.generation
                and duration.ended_at // frame_ms == expiry.frame)

    def advance_expiries(self):
        for (body, projected), expiry in tuple(self.expiries.items()):
            owner = expiry.owner
            script = owner.body_script
            if (not self.fight.match.ended and not owner.gone and owner.has_body and owner.body == body
                    and script is not None and not script.retired
                    and script.component is expiry.duration.instance.component
                    and expiry.duration.counter == expiry.generation
                    and not self.fight.is_dead(owner)):
                if expiry.ended_ahead and self.fight.tick <= expiry.tick:
                    continue
                if (self.fight.tick > expiry.tick and (expiry.duration.active
                        or (not expiry.duration.reached and script.frame < expiry.frame))):
                    # Leave the expiry/contact frame available for real input before its neutral gap.
                    frame = expiry.frame + min(1, self.fight.tick - expiry.tick - 1)
                    owner._advance_body_gap(frame + 1)
                if expiry.duration.active:
                    continue
                if not expiry.duration.reached and self.fight.tick > expiry.tick:
                    # Native child cleanup may queue the authored cooldown write
                    # at the idle owner's old cursor. Settle that same value only
                    # after its original contact-frame neutral gap has run.
                    ability = self._ability(expiry.duration.instance, projected)
                    boundary = expiry.frame * script.frame_ms
                    if ability is not None and ability.cur > 0 and ability.last < boundary:
                        ability.set_cooldown_start(boundary + ability.last % script.frame_ms)
                self._record_cooldown(body, projected, expiry, script.frame_ms)
            self.expiries.pop((body, projected), None)

    @staticmethod
    def _ability(instance, projected):
        return instance.states.get(instance.graph.node(21 if projected else 3).state)

    def _record_cooldown(self, body, projected, expiry, frame_ms):
        instance = expiry.duration.instance
        ability = self._ability(instance, projected)
        if ability is None or ability.cur <= 0 or ability.cooldown_rate <= 0:
            return
        self.cooldown_deadlines[body, projected] = self._mapped_cooldown_ready_tick(ability, expiry, frame_ms)

    @staticmethod
    def _mapped_cooldown_ready_tick(ability, expiry, frame_ms):
        from ow174.game.combat import TICK

        # The authored exit writes the cooldown. Map its ready frame back to the
        # world clock even if a command already advanced the linked variable to zero.
        ready_frame = (ability.last + expr.round_half_away(expr.f32(expr.f32(ability.cur * 1000.0) / ability.cooldown_rate))) // frame_ms
        elapsed = (ready_frame - expiry.frame) * frame_ms
        return expiry.tick + math.ceil(elapsed / (TICK * 1000))

    def cooldown_ready_tick(self, ability):
        index = ability.instance.graph.index
        if (index, ability.node.pos) not in ((0x0244, 3), (0x025C, 21)):
            return 0
        key = ability.instance.component.entity, index == 0x025C
        expiry = self.expiries.get(key)
        if (expiry is not None and expiry.duration.instance is ability.instance
                and not expiry.duration.active and expiry.duration.counter == expiry.generation):
            script = expiry.owner.body_script
            if script is not None and script.component is ability.instance.component:
                return self._mapped_cooldown_ready_tick(ability, expiry, script.frame_ms)
        return self.cooldown_deadlines.get(key, 0)

    def start_edges(self, tick):
        return {edge for barrier in self.scripted.values()
                if barrier.component.world.hero == ZARYA
                for edge in (barrier.application_tick - 1, barrier.application_tick)
                if self.fight.tick < edge <= tick}

    def live_source(self, bubble):
        actor = self.authored_for(bubble.caster_body, bubble.phase)
        if actor is not None:
            return actor.vitals.total > 0
        if bubble.phase.instance.graph.index in (PERSONAL, PROJECTED):
            return False  # the existing scripted actor alone owns protection
        script = getattr(bubble.caster, 'body_script', None)
        return (script is not None and not script.retired
                and script.component is bubble.phase.instance.component
                and bubble.phase.active and bubble.health > 0)

    def query_targets(self, state):
        """025C's two native searches only; centre range/cone/ranking are reconstructed."""
        if (state.instance.graph.index, state.index) not in ((0x25C, 4), (0x25C, 7)):
            return NotImplemented
        from ow174.game.combat import aim, capsule_visible

        component = state.instance.component
        caster = next((p for p in self.fight.match.players if p.body == component.entity), None)
        if caster is None or component.owner_dead or self.fight.match.game_map.free_for_all:
            return ()
        origin = self.fight._eye(caster)
        forward = aim(caster.mover.yaw, caster.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field('m_radius'), watch=False))
        factor = component.entity_var(9595, create=False)
        factor = factor.value() if factor is not None else None
        # Native angle input is10*entity9595. Its absent engine default1 is explicit reconstruction.
        angle = 10.0 if factor is None else expr.to_float(state.evaluate(
            state.node.field('m_A30B383E'), watch=False))
        candidates = []
        protected = {b.body for b in self.live.values() if self.live_source(b)}
        for target in self.fight._targets():
            if (target.owner is caster or target.team != caster.team or target.entity in protected
                    or self.fight.vitals_of(target).dead):
                continue
            a, b = target.segment()
            centre = tuple((x+y)*.5 for x, y in zip(a, b, strict=True))
            distance = math.dist(origin, centre)
            if not 0 < distance <= reach:
                continue
            direction = tuple((x-y)/distance for x, y in zip(centre, origin, strict=True))
            alignment = sum(x*y for x, y in zip(direction, forward, strict=True))
            if alignment < math.cos(math.radians(angle)):
                continue
            if not capsule_visible(self.fight._collision(), origin, a, b, target.radius):
                continue
            candidates.append(((-alignment, distance, target.entity), target.entity))
        if not candidates:
            return ()
        return (expr.Entity(min(candidates)[1]),)

    def sync(self, actor):
        self.reinhardt.sync(actor)
        script = getattr(actor, 'body_script', None)
        if script is None or getattr(script, 'retired', False) or not hasattr(actor, 'mover'):
            # Retired scripts keep historical active-state bits; they no longer own live pets.
            self.live = {k: b for k, b in self.live.items() if b.caster is not actor}
            body = getattr(actor, 'body', None)
            self.seen = {k: stamp for k, stamp in self.seen.items() if k[0] != body}
            return None
        body = script.component.entity
        if not actor.has_body or not actor.spawned:
            self.retire(body)
            return None
        dead_caster = script.component.owner_dead or self.fight.is_dead(actor)
        targets = {t.entity: t for t in self.fight._targets()}
        personal = None
        for index, state_id, definition in ((0x244, 2, 0x13F4), (0x25C, 10, 0x3DA)):
            key = body, index
            instance = next((i for i in script.component.instances.values() if i.graph.index == index), None)
            phase = instance.states.get(state_id) if instance is not None else None
            # Death prevents new casts, but a live script's already-running025C
            # pet timer is independent of Ability.stop. Retire only with its phase.
            expiry = self.expiries.get((body, index == 0x25C))
            historical = (phase is not None and expiry is not None and expiry.ended_ahead
                          and phase.ended_at == expiry.duration.ended_at
                          and self.fight.tick <= expiry.tick)
            if phase is None or (not phase.active and not historical) or (dead_caster and index == 0x244):
                self.live.pop(key, None)
                continue
            stamp = instance.id, phase.counter, phase.start
            if self.seen.get(key) != stamp:
                self.seen[key] = stamp
                target_id = body if index == 0x244 else expr.to_entity(instance.vars[536].value())
                target = targets.get(target_id)
                if target is None or self.fight.vitals_of(target).dead:
                    continue
                pool = graph.graph(0x256).states[0].field('m_amount')
                default = pool['m_configVars'][1]['m_A53100D5']['m_value']
                configured = script.world.ruleset(0x1D43)
                maximum = expr.f32(default if configured is None else configured)
                self.live[key] = Bubble(FIRST + self.next_entity % SPAN, target.owner, target_id,
                                         phase, phase.counter, 0x0400000000000000 | definition,
                                         maximum, maximum, actor, body)
                self.next_entity += 1
            bubble = self.live.get(key)
            target = targets.get(bubble.body) if bubble is not None else None
            if (target is None or target.owner is not bubble.owner or self.fight.vitals_of(target).dead):
                self.live.pop(key, None)
                phase.end(True)
                script.component.run_now()
            elif index == 0x244:
                personal = bubble
        return personal

    def for_target(self, actor):
        for caster in self.fight.match.players:
            self.sync(caster)
        entity = actor.entity if isinstance(actor, bots.Bot) else actor.body
        return next((b for b in self.live.values()
                     if b.owner is actor and b.body == entity and self.live_source(b)), None)

    def enemies(self, shooter, *, team=None):
        team = shooter.team if team is None else team
        found = []
        for caster in self.fight.match.players:
            self.sync(caster)
        for bubble in self.live.values():
            if (self.live_source(bubble) and bubble.owner is not shooter
                    and (self.fight.match.game_map.free_for_all or bubble.owner.team != team)):
                found.append(bubble)
        roster = {id(player): index for index, player in enumerate(self.fight.match.players)}
        found.sort(key=lambda bubble: (bubble.phase.instance.graph.index == PROJECTED,
                                       roster.get(id(bubble.caster), len(roster)), bubble.entity))
        return found + self.world.enemies(shooter, team=team) + self.reinhardt.enemies(shooter, team=team)

    def authored_for(self, body, phase=None):
        return next((actor for actor in self.scripted.values()
                     if actor.parent_body == body and actor.vitals is not None
                     and _live(self.fight, actor)
                     and (phase is None or (actor.instance, actor.state, actor.activation)
                          == (phase.instance.id, phase.index, phase.counter))), None)

    def intercept(self, shooter, origin, direction, length, *, radius=0.0, include_authored_mesh=True, team=None):
        best = None
        for bubble in self.enemies(shooter, team=team):
            if isinstance(bubble, reinhardt_barrier.RaisedBarrier):
                actor = self.authored_for(bubble.body)
                if actor is not None and actor.mesh and not include_authored_mesh:
                    continue
                distance = bubble.trace(origin, direction, length, radius)
            else:
                fixed = isinstance(bubble, world_bubbles.FixedBubble)
                trace = world_bubbles.shell if fixed else sphere
                extent = bubble.radius if fixed else RADIUS
                distance = trace(origin, direction, bubble.centre(), extent + radius, length)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, bubble
        return best

    def attached(self, entity):
        if entity in self.world.actors:
            actor = self.world.actors[entity]
            return actor if self.world.live_source(actor) else None
        for actor in tuple(self.reinhardt.actors.values()):
            if actor.entity == entity:
                self.reinhardt.sync(actor.owner)
                return actor if actor.active and self.reinhardt.actors.get(actor.body) is actor else None
        for b in tuple(self.live.values()):
            self.sync(b.caster)
        return next((b for b in self.live.values() if b.entity == entity and self.live_source(b)), None)

    def damage(self, bubble, amount, shooter=None):
        if isinstance(bubble, reinhardt_barrier.RaisedBarrier):
            return self.reinhardt.damage(bubble, amount)
        if isinstance(bubble,world_bubbles.FixedBubble):
            return self.world.damage(bubble,amount)
        actor = self.authored_for(bubble.caster_body, bubble.phase)
        if actor is not None:
            attacker = _source_entity(shooter) if shooter is not None else 0
            dealt = damage(self.fight, actor, amount, attacker=attacker)
            bubble.health = actor.vitals.total
            return dealt
        if bubble.phase.instance.graph.index in (PERSONAL, PROJECTED):
            bubble.health = 0.0
            return 0.0  # the missing/exhausted native actor cannot recreate a proxy pool
        dealt = min(bubble.health, max(0.0, amount))
        bubble.health = max(0.0, bubble.health-dealt)
        if dealt and shooter is not None:
            #0256/n10 forwards actual pool loss to0242 using0146. Hosting0256 and
            #the upstream0011 filter remain open; retain the authored energy math.
            source = shooter.entity if hasattr(shooter, 'position') else shooter.body
            component = bubble.caster.body_script.component
            runtime.send_message(component, 0x0240000000000146,
                                 {0x2A: expr.f32(dealt), 0x12: expr.Entity(source)},
                                 bubble.entity)
            component.run_now()
        if bubble.health <= 0 and dealt:
            bubble.phase.end(True)
            bubble.phase.instance.component.run_now()
            self.live.pop((bubble.caster_body, bubble.phase.instance.graph.index), None)
        return dealt

    def area(self, shooter, centre, radius, amount, *, contacted=None, include_raised=True):
        """One pool hit per area event; no same-hit overflow onto its protected body."""
        from ow174.game.combat import capsule_visible

        protected = set()
        for bubble in self.enemies(shooter):
            if isinstance(bubble, Bubble) and bubble.body in protected:
                continue  # source0068 consumes only one pool for this recipient/event
            if contacted is not None and bubble.entity in contacted:
                if isinstance(bubble, Bubble):
                    protected.add(bubble.body)
                continue
            if isinstance(bubble, reinhardt_barrier.RaisedBarrier):
                if not include_raised:
                    continue
                origin = self.fight._eye(shooter)
                def visible(point, origin=origin):
                    return capsule_visible(self.fight._collision(), origin, point, point, .001)
                if bubble.near(centre, radius, visible):
                    if contacted is not None:
                        contacted.add(bubble.entity)
                    self.damage(bubble, amount, shooter)
                continue
            if isinstance(bubble,world_bubbles.FixedBubble):
                continue  # declared world-barrier melee bypass; native filters still open
            point = bubble.centre()
            distance = math.dist(centre, point)
            if distance > radius + RADIUS:
                continue
            if not capsule_visible(self.fight._collision(), self.fight._eye(shooter), point, point, RADIUS):
                continue
            protected.add(bubble.body)
            if contacted is not None:
                contacted.add(bubble.entity)
            self.damage(bubble, amount, shooter)
        return protected


REINHARDT = 0x02E0000000000007
ZARYA = 0x02E0000000000068
WINSTON = 0x02E0000000000009
PERSONAL, PROJECTED = 0x0244, 0x025C
MEI = 0x02E00000000000DD
ZENYATTA = 0x02E0000000000020
CRYO_RADIUS = 0.5  # definition 090B, STU_BE9A0B3E
CRYO_HEIGHT = 2.0
FIRST_ENTITY = 0xC1000001  # reconstructed server proxy namespace; collision checked on allocation
ANCHOR_FORWARD = 1.0  # reconstructed raised pose, not the native bone008C transform
ANCHOR_HEIGHT = 1.8  # actual body0024 FirstPerson height, used as a reconstructed anchor
ANCHOR_CROUCHED_HEIGHT = 1.26


@dataclass
class Barrier:
    entity: int
    parent: object
    parent_body: int
    source: runtime.Component
    instance: int
    state: int
    activation: int
    component: runtime.Component
    mesh: tuple = ()
    geometry_digest: str | None = None
    vitals: object = None
    retired: bool = False
    recipient: object = None
    recipient_body: int = 0
    recipient_component: runtime.Component | None = None
    application_tick: int = 0
    cleanse_applied: bool = False


def track_projected_target(world, state):
    """Feed 025C's cast transition with a visible allied body under the crosshair."""
    if (world.hero, state.instance.graph.index, state.index) != (ZARYA, 0x025C, 7):
        return
    if world.match is None:
        return
    from ow174.game.combat import aim, ray_capsule, ray_world

    fight = world.match.combat
    source = next((p for p in world.match.players if p.body == world.body and p.has_body), None)
    output = expr.lvalue(state.node.field("m_8A2905A0"))
    found = ()
    if (output is not None and source is not None and source.body_script is not None
            and source.body_script.component is state.instance.component
            and world.match.collision_world is not None and not world.match.game_map.free_for_all):
        origin = fight._eye(source)
        direction = aim(source.mover.yaw, source.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        wall = ray_world(world.match.collision_world, origin, direction, reach)
        closest = math.inf if wall is None else wall
        for target in fight._targets():
            ally = target.owner
            if (ally is source or target.team != source.team
                    or not _projection_recipient_ready(fight, ally)):
                continue
            a, b = target.segment()
            distance = ray_capsule(origin, direction, a, b, target.radius)
            if distance is not None and distance <= reach and distance < closest:
                closest, found = distance, (expr.Entity(target.entity),)
    if output is not None:
        state.instance.write(output, found)


def _recipient_ready(fight, player):
    script = player.body_script
    return (player.has_body and player.spawned and not player.gone and player.client is not None
            and script is not None and not script.retired and not fight.is_dead(player))


def _projection_recipient_ready(fight, actor):
    if not isinstance(actor, bots.Bot):
        return actor in fight.match.players and _recipient_ready(fight, actor)
    from ow174.game.symmetra_portals import BODIES as TELEPORTER_BODIES

    script = actor.body_script
    return (actor in fight.match.bots and actor.body not in TELEPORTER_BODIES
            and not fight.is_dead(actor) and script is not None
            and script.component.entity == actor.entity
            and not script.component.owner_dead and script.component.fault is None)


def cryo_heal(world, state, elapsed_ms):
    """Apply 05FE's self-heal budget over its evaluated ModifyHealth duration.

    The authored duration is 567 minus 0.1 seconds. Distributing the
    budget over elapsed VM time is a reconstructed server policy.
    """
    instance = state.instance
    if (world.hero, instance.graph.index, state.node.pos) != (MEI, 0x05FE, 27) or world.match is None:
        return NotImplemented
    if elapsed_ms <= 0 or instance.teardown:
        return True
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == world.body), None)
    if (target is None or not _recipient_ready(fight, target.owner)
            or target.owner.body_script.component is not instance.component
            or expr.to_entity(state.evaluate(state.node.field("m_FA3ECC43"), watch=False)) != world.body):
        return True
    # The original sampled consumer settles this activation after body VM advance.
    # Claim the authored native report here so it cannot heal a second time.
    return True


def _transcendence_owner(world, instance):
    fight = world.match.combat
    owner = next((p for p in world.match.players if p.body == world.body), None)
    if (owner is not None and _recipient_ready(fight, owner)
            and owner.body_script.component is instance.component):
        return owner
    return None


def track_transcendence(world, state):
    """Feed 017B's live ally tuple with a reconstructed capsule range and LOS rule."""
    instance = state.instance
    if (world.hero, instance.graph.index, state.node.pos) != (ZENYATTA, 0x017B, 5) or world.match is None:
        return False
    from ow174.game.combat import capsule_visible, segment_distance

    fight = world.match.combat
    owner = _transcendence_owner(world, instance)
    output = expr.lvalue(state.node.field("m_8A2905A0"))
    selected = []
    collision = world.match.collision_world
    if owner is not None and not world.match.game_map.free_for_all and collision is not None:
        centre = fight._eye(owner)
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        for target in fight._targets():
            if target.owner is owner or target.team != owner.team or fight.vitals_of(target).dead:
                continue
            a, b = target.segment()
            if (segment_distance(centre, a, b) - target.radius <= radius
                    and capsule_visible(collision, centre, a, b, target.radius)):
                selected.append(expr.Entity(target.entity))
    targets = tuple(selected)
    if output is not None and instance.find_var(*output).value() != targets:
        instance.write(output, targets)
    return True


def transcendence_heal(world, state, elapsed_ms):
    """Apply the authored 017B healing budget over VM time to the tracked allies."""
    instance = state.instance
    if (world.hero, instance.graph.index, state.node.pos) != (ZENYATTA, 0x017B, 12) or world.match is None:
        return NotImplemented
    phase = instance.states.get(31)
    if elapsed_ms <= 0 or instance.teardown or phase is None or not phase.active:
        return True
    owner = _transcendence_owner(world, instance)
    targets = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if owner is None or world.match.game_map.free_for_all or not isinstance(targets, tuple):
        return True
    duration = expr.to_float(state.evaluate(state.node.field("m_95F1FB0B"), watch=False))
    budget = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    if duration <= 0 or budget <= 0:
        return True
    selected = {expr.to_entity(target) for target in targets}
    fight = world.match.combat
    for target in fight._targets():
        if target.entity in selected and target.owner is not owner and target.team == owner.team:
            vitals = fight.vitals_of(target)
            if not vitals.dead:
                fight._heal_target(owner, target, budget * elapsed_ms / (duration * 1000))
    return True


def create(world, state):
    signature = world.hero, state.instance.graph.index, state.index
    if signature == (REINHARDT, 0x0095, 17):
        entity_definition, startup, pool_graph = 0x040000000000011A, 0x25B4, 0x96
    elif signature == (ZARYA, 0x0244, 2):
        entity_definition, startup, pool_graph = 0x04000000000013F4, None, 0x256
    elif signature == (ZARYA, 0x025C, 10):
        entity_definition, startup, pool_graph = 0x04000000000003DA, None, 0x256
    elif signature == (MEI, 0x05FE, 11):
        entity_definition, startup, pool_graph = 0x040000000000090B, None, 0x623
    else:
        return 0
    if (world.match is None
            or expr.guid_of(state.node.field("m_entityDefinition.m_entityDef")) != entity_definition):
        return 0
    fight = world.match.combat
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (owner is None or owner.gone or owner.body_script is None or owner.body_script.retired
            or owner.body_script.component is not state.instance.component or fight.is_dead(owner)):
        return 0
    recipient = None
    if signature == (ZARYA, 0x025C, 10):
        variable = state.instance.vars.get(536)
        target = expr.to_entity(variable.value()) if variable is not None else 0
        recipient = next((row.owner for row in fight._targets()
                          if row.entity == target and row.owner is not owner), None)
        if (recipient is None or world.match.game_map.free_for_all or recipient.team != owner.team
                or not _projection_recipient_ready(fight, recipient)):
            return 0
    from ow174.game.script.driver import BodyWorld

    definition = graph.graph(startup) if startup is not None else None
    if (startup is not None and definition is None) or graph.graph(pool_graph) is None:
        return 0
    occupied = ({p.body for p in world.match.players}
                | {b.entity for b in world.match.bots} | set(fight.barriers.scripted))
    while fight.next_barrier in occupied:
        fight.next_barrier += 1
    entity, fight.next_barrier = fight.next_barrier, fight.next_barrier + 1
    child_world = BodyWorld(world.hero, world.mode, entity, entity_definition)
    child_world.match = world.match
    component = runtime.Component(entity, state.instance.component.frame_ms, child_world)
    component.frame, component.now = state.instance.component.frame, state.now
    barrier = Barrier(entity, owner, owner.body, state.instance.component,
                      state.instance.id, state.index, state.counter, component,
                      application_tick=world.match.tick)
    if recipient is not None:
        barrier.recipient = recipient
        barrier.recipient_body = recipient.entity if isinstance(recipient, bots.Bot) else recipient.body
        barrier.recipient_component = recipient.body_script.component
    if world.hero == REINHARDT:
        barrier.mesh, barrier.geometry_digest = barrier_geometry.load()
    fight.barriers.scripted[entity] = barrier
    if world.hero == ZARYA:
        projected = signature[1] == PROJECTED
        duration = state.instance.states.get(11 if projected else 1)
        fight.barriers._schedule_expiry(owner, duration, projected)
    #011A's actual initial script, recovered from the external entity XML.
    if definition is not None:
        initial = component.create(definition, init=False)
        initial.set_source_entity(owner.body)
        initial.init()
        component.run_now()
    return entity


def retire(fight, entity):
    barrier = fight.barriers.scripted.get(entity)
    expiry = _expiry(fight, barrier) if barrier is not None else None
    ending_on_deadline = (expiry is not None and expiry.duration.ending and expiry.duration.reached
                          and expiry.duration.counter == expiry.generation
                          and expiry.duration.now // barrier.source.frame_ms == expiry.frame)
    if (expiry is not None and fight.tick <= expiry.tick
            and (barrier.vitals is None or barrier.vitals.total > 0)
            and (fight.barriers._duration_ended_on_deadline(expiry, barrier.source.frame_ms)
                 or ending_on_deadline)
            and not fight.is_dead(barrier.parent)
            and barrier.parent.body_script is not None
            and barrier.parent.body_script.component is barrier.source
            and _recipient_live(fight, barrier)):
        expiry.ended_ahead = True
        return  # Preserve this same actual actor for earlier world contacts.
    barrier = fight.barriers.scripted.pop(entity, None)
    if barrier is None:
        return
    barrier.retired = True
    for instance in list(barrier.component.instances.values()):
        barrier.component.destroy(instance)


def retire_owner(fight, owner):
    body = owner.entity if isinstance(owner, bots.Bot) else owner.body
    for barrier in list(fight.barriers.scripted.values()):
        if barrier.parent is owner and barrier.parent_body == body:
            retire(fight, barrier.entity)
        elif barrier.recipient is owner and barrier.recipient_body == body:
            _end_bubble(fight, barrier)


def _end_bubble(fight, barrier):
    instance = barrier.source.instances.get(barrier.instance)
    if instance is not None:
        state = instance.states.get(barrier.state)
        if state is not None and state.active and state.counter == barrier.activation:
            state.request(runtime.ABORT)
            barrier.source.run_now()
    retire(fight, barrier.entity)


def play_script(world, instance, node):
    signature = world.hero, instance.graph.index, node.pos
    if signature == (REINHARDT, 0x95, 6):
        pool_graph = 0x96
    elif signature in ((ZARYA, 0x244, 9), (ZARYA, 0x25C, 27)):
        pool_graph = 0x256
    elif signature == (MEI, 0x05FE, 22):
        pool_graph = 0x623
    else:
        return
    if world.match is None:
        return
    resource = play.resolve_resource(instance, node)
    definition = graph.graph(pool_graph)
    if resource is None or resource.guid != (0x0580000000000000 | pool_graph) or definition is None:
        return
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if request is None or len(request.targets) != 1:
        return
    barrier = world.match.combat.barriers.scripted.get(request.targets[0])
    if (barrier is None or barrier.retired or barrier.parent_body != world.body
            or barrier.source is not instance.component):
        return
    component = barrier.component
    component.frame, component.now = instance.component.frame, instance.component.now
    # Native parameter arrays carry Entity values into the separate component,
    # rather than sharing the source variable's Python value-record storage.
    request = replace(request, params=tuple(
        (key, tuple(expr.Entity(value.id) if type(value) is expr.Entity else value
                    for value in values)) for key, values in request.params))
    queue = play_queue.Queue(component, lambda guid: play_queue.Load("ready", definition))
    queue.submit(request)
    if queue.pending:
        raise ValueError("ready barrier call left pending work")
    component.run_now()
    _init_health(world.match.combat, barrier)


def health_query(world, instance, cfg):
    if (world.match is None or world.hero != REINHARDT or instance.graph.index != 0x95
            or (cfg.get("m_7E8B666C") or {}).get("m_7E8B666C") != 0):
        return NotImplemented
    target = instance.evaluate(cfg.get("m_entity"))
    barrier = world.match.combat.barriers.scripted.get(expr.to_entity(target))
    if barrier is None or barrier.parent_body != world.body:
        return NotImplemented
    return barrier.vitals.total if barrier.vitals is not None else 0.0


def advance(fight, tick):
    for barrier in list(fight.barriers.scripted.values()):
        if not _valid(fight, barrier):
            if barrier.recipient is not None and not _recipient_live(fight, barrier):
                _end_bubble(fight, barrier)
            else:
                retire(fight, barrier.entity)
            continue
        component = barrier.component
        if tick > component.frame:
            component.run_frame(tick)
        else:
            component.run_now()
        _init_health(fight, barrier)
        _apply_bubble_cleanse(fight, barrier)

    from ow174.game import reinhardt_presentation

    reinhardt_presentation.sync(fight)


def _init_health(fight, barrier):
    from ow174.game.combat import Vitals

    if barrier.vitals is not None or barrier.component.world.hero == MEI:
        return
    component = barrier.component
    bubble = component.world.hero == ZARYA
    pool_graph, index = (0x256, 0) if bubble else (0x96, 1)
    pool = next((i.state(index) for i in component.instances.values() if i.graph.index == pool_graph), None)
    if pool is not None and pool.active:
        protected_body = barrier.recipient_body or barrier.parent_body
        if bubble and expr.to_entity(pool.instance.vars[2390].value()) != protected_body:
            return
        maximum = expr.to_float(pool.evaluate(pool.node.field("m_amount"), watch=False))
        current = maximum
        if not bubble:
            current = expr.to_float(pool.evaluate(pool.node.field("m_BCFDD2E9"), watch=False))
        vitals = barrier.vitals = Vitals(barrier.entity, maximum)
        vitals.now[0] = max(0.0, min(maximum, current))
        component.set(253, vitals.total)
        _apply_bubble_cleanse(fight, barrier)


def _apply_bubble_cleanse(fight, barrier):
    """Apply once at the native Pet's actual accepted world application."""
    if (barrier.component.world.hero != ZARYA or barrier.cleanse_applied
            or fight.tick < barrier.application_tick or barrier.vitals is None
            or barrier.vitals.total <= 0 or not _live(fight, barrier)):
        return
    protected_body = barrier.recipient_body or barrier.parent_body
    recipient = fight.vitals.get(protected_body)
    if recipient is None or recipient.dead:
        return
    barrier.cleanse_applied = True
    fight.cleanse_ana_effects(protected_body)
    # Retain the real block interval for prior-time healing integration.
    if recipient.healing_block[1] > fight.now:
        recipient.healing_block = recipient.healing_block[0], fight.now
    fight.hooks.release_target(protected_body)
    if barrier.recipient is not None:
        recipient.clear_sleep()
        from ow174.game.combat import SLEEP_DART_EFFECT

        fight.close_input_lock(protected_body, SLEEP_DART_EFFECT)


def _recipient_live(fight, barrier):
    actor = barrier.recipient
    if actor is None:
        return True
    body = actor.entity if isinstance(actor, bots.Bot) else actor.body
    return (body == barrier.recipient_body and actor.team == barrier.parent.team
            and not fight.match.game_map.free_for_all
            and _projection_recipient_ready(fight, actor)
            and actor.body_script.component is barrier.recipient_component)


def _expiry(fight, barrier):
    if barrier.component.world.hero != ZARYA:
        return None
    expiry = fight.barriers.expiries.get((barrier.parent_body, barrier.recipient is not None))
    return expiry if (expiry is not None and expiry.duration.instance.id == barrier.instance
                      and expiry.duration.instance.component is barrier.source) else None


def _live(fight, barrier):
    if not _valid(fight, barrier):
        return False
    if barrier.component.world.hero == ZARYA:
        expiry = _expiry(fight, barrier)
        return (barrier.application_tick <= fight.tick
                and (expiry is None or fight.tick <= expiry.tick))
    return True


def _valid(fight, barrier):
    owner = barrier.parent
    if barrier.component.world.hero == MEI:
        from ow174.game import mei

        if mei.cryo_world_expired(owner):
            return False
    instance = barrier.source.instances.get(barrier.instance)
    state = instance.states.get(barrier.state) if instance is not None else None
    expiry = _expiry(fight, barrier)
    historical = (expiry is not None and expiry.ended_ahead
                  and state is not None and state.ended_at == expiry.duration.ended_at
                  and fight.tick <= expiry.tick)
    return (not barrier.retired and not owner.gone and owner.has_body
            and owner.body == barrier.parent_body and owner.body_script is not None
            and not owner.body_script.retired and owner.body_script.component is barrier.source
            and not fight.is_dead(owner) and state is not None and (state.active or historical)
            and state.counter == barrier.activation and _recipient_live(fight, barrier))


def mesh_trace(barrier, origin, direction, length, *, padding=0.0):
    """Read the actual native shield mesh in its existing body transform."""
    owner = barrier.parent
    mover = owner.mover
    yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    basis = ((cy, 0, -sy), (sy*sp, cp, cy*sp), (sy*cp, -sp, cy*cp))
    height = ANCHOR_CROUCHED_HEIGHT if mover.crouched else ANCHOR_HEIGHT
    anchor = tuple(mover.position[k] + (height if k == 1 else 0)
                   + basis[2][k] * ANCHOR_FORWARD for k in range(3))
    delta = tuple(origin[k] - anchor[k] for k in range(3))
    local_origin = tuple(barrier_geometry._dot(delta, axis) for axis in basis)
    local_direction = tuple(barrier_geometry._dot(direction, axis) for axis in basis)
    return barrier_geometry.ray_mesh(barrier.mesh, local_origin, local_direction, length,
                                     padding=padding)


def _particle_priority(fight, actor):
    roster = next((index for index, player in enumerate(fight.match.players)
                   if player is actor.parent), len(fight.match.players))
    return actor.recipient is not None, roster, actor.entity


def ray_hit(fight, shooter, origin, direction, length, *, padding=0.0):
    """Closest live enemy defensive Pet using its reconstructed collision.

    Current owner pose is used for both hitscan and projectiles; native shield
    history/attachment, scale, beam and splash receiver filters remain open.
    """
    best = None
    for barrier in fight.barriers.scripted.values():
        owner = barrier.recipient if barrier.recipient is not None else barrier.parent
        if (owner is shooter or not _live(fight, barrier)
                or (not fight.match.game_map.free_for_all and owner.team == shooter.team)):
            continue
        if barrier.component.world.hero == MEI:
            from ow174.game.combat import ray_capsule

            x, y, z = owner.mover.position
            a, b = (x, y + CRYO_RADIUS, z), (x, y + CRYO_HEIGHT - CRYO_RADIUS, z)
            distance = ray_capsule(origin, direction, a, b, CRYO_RADIUS + padding)
            if distance is not None and distance <= length and (best is None or distance < best[0]):
                best = distance, barrier
            continue
        if barrier.vitals is None or barrier.vitals.total <= 0:
            continue
        if barrier.component.world.hero == ZARYA:
            from ow174.game.combat import ray_capsule

            target = next((t for t in fight._targets() if t.owner is owner), None)
            if target is not None:
                a, b = target.segment()
                distance = ray_capsule(origin, direction, a, b, target.radius + padding)
                same_recipient_tie = (best is not None and distance == best[0]
                                      and best[1].component.world.hero == ZARYA
                                      and (best[1].recipient_body or best[1].parent_body)
                                      == (barrier.recipient_body or barrier.parent_body)
                                      and _particle_priority(fight, barrier) < _particle_priority(fight, best[1]))
                if distance is not None and distance <= length and (best is None or distance < best[0]
                                                                  or same_recipient_tie):
                    best = distance, barrier
            continue
        if not barrier.mesh:
            continue
        distance = mesh_trace(barrier, origin, direction, length, padding=padding)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, barrier
    return best


def protect_body(fight, target, amount, *, attacker):
    """Route body-directed damage to a live bubble, consuming the impact."""
    actors = sorted((actor for actor in fight.barriers.scripted.values()
                     if actor.component.world.hero == ZARYA), key=lambda actor: _particle_priority(fight, actor))
    for barrier in actors:
        protected = barrier.recipient if barrier.recipient is not None else barrier.parent
        if (protected is target.owner and (barrier.recipient_body or barrier.parent_body) == target.entity
                and barrier.component.world.hero == ZARYA and barrier.vitals is not None
                and barrier.vitals.total > 0 and _live(fight, barrier)):
            damage(fight, barrier, amount, attacker=attacker)
            return barrier.vitals.total
    return None


def relay_bubble_energy(world, target, message, params, sender):
    """Route the child 0256 energy message to the live caster's 0242 entry."""
    if world.match is None or world.hero != ZARYA or sender != world.body or message != 0x0240000000000146:
        return False
    fight = world.match.combat
    bubble = fight.barriers.scripted.get(sender)
    if (bubble is None or bubble.component.world is not world or bubble.parent_body != target
            or not _live(fight, bubble)):
        return False
    receiver = next((i for i in bubble.source.instances.values() if i.graph.index == 0x0242), None)
    if receiver is not None:
        runtime.send_instance_message(receiver, message, params, sender)
    return True


def damage(fight, barrier, amount, *, attacker=0):
    """Consume shield health and retire the Pet on its scoped break path.

    Reinhardt receives the saved meter and 0053 entry. Zarya's child receives
    the death flag, followed by an explicit Pet abort on the source graph.
    The host reports effective bubble loss to 0256 before retiring it.
    Native damage categories and cross-component message ordering remain open.
    """
    if not _live(fight, barrier) or barrier.vitals is None or amount <= 0:
        return 0.0
    dealt = barrier.vitals.damage(amount)
    remaining = barrier.vitals.total
    barrier.component.set(253, remaining)
    owner_script = barrier.source.instances[barrier.instance]
    if barrier.component.world.hero == ZARYA:
        child = next((i for i in barrier.component.instances.values() if i.graph.index == 0x0256), None)
        if child is not None and dealt > 0 and attacker:
            params = {0x13: expr.f32(dealt), 0x12: expr.Entity(attacker)}
            runtime.send_instance_message(child, 0x0240000000000011, params, attacker)
            barrier.component.run_now()
            barrier.source.run_now()
        if remaining <= 0:
            expiry = _expiry(fight, barrier)
            deferred = False
            projected = barrier.recipient is not None
            if expiry is not None:
                duration = expiry.duration
                hit_frame = expiry.frame + fight.tick - expiry.tick
                if expiry.ended_ahead and not duration.active:
                    ability = fight.barriers._ability(duration.instance, projected)
                    if ability is not None and ability.last >= duration.ended_at:
                        at = hit_frame * barrier.source.frame_ms + ability.last - duration.ended_at
                        ability.set_cooldown_start(at)
                elif duration.active:
                    # Original0068 finishes the same duration at the contact's
                    # mapped frame; the next world edge supplies its neutral gap.
                    at = max(barrier.source.now, hit_frame * barrier.source.frame_ms)
                    duration.request_finish(at - barrier.source.now)
                    frame = min(expiry.frame, max(barrier.parent.body_script.frame + 1, hit_frame))
                    expiry.tick += frame - expiry.frame
                    expiry.frame = frame
                    deferred = True
            if projected:
                old = fight.projected_barriers.get(barrier.recipient_body)
                if old is not None and old.source == barrier.parent_body:
                    fight.projected_barriers.pop(barrier.recipient_body, None)
            else:
                fight.personal_barriers.pop(barrier.parent_body, None)
            fight.barriers.live.pop((barrier.parent_body, PROJECTED if projected else PERSONAL), None)
            barrier.component.set(478, True)
            barrier.component.run_now()
            if deferred:
                retire(fight, barrier.entity)
            else:
                _end_bubble(fight, barrier)
        return dealt
    legacy = fight.reinhardt_barriers.get(barrier.parent_body)
    if legacy is not None:
        geometry = fight.reinhardt_geometry
        legacy.current = remaining
        legacy.regen_after = fight.now + geometry.regen_delay
        if remaining <= 0:
            legacy.active = False
            legacy.broken_until = fight.now + geometry.break_delay
    owner_script.write((runtime.INSTANCE_SCOPE, 586), expr.f32(remaining))
    if remaining <= 0:
        runtime.send_instance_message(owner_script, 0x0240000000000053, {}, barrier.entity)
    barrier.source.run_now()
    if not _live(fight, barrier):
        retire(fight, barrier.entity)
    return dealt


def overlap_points(geometry: Geometry, feet, yaw, pitch, a, b, radius, owner_radius, owner_height):
    """World-space triangle witnesses touched by a melee query capsule."""
    position, axes = frame(geometry, feet, yaw, pitch, owner_radius, owner_height)
    points = tuple(tuple(sum((p[k] - position[k]) * axis[k] for k in range(3)) for axis in axes)
                   for p in (a, b))
    low = tuple(min(points[0][k], points[1][k]) - radius for k in range(3))
    high = tuple(max(points[0][k], points[1][k]) + radius for k in range(3))
    for triangle in geometry.triangles:
        corners = tuple(tuple(triangle[n:n + 3]) for n in (0, 3, 6))
        if any(max(v[k] for v in corners) < low[k] or min(v[k] for v in corners) > high[k]
               for k in range(3)):
            continue
        distance = contacts.gjk(corners, points, reach=radius)
        if distance.distance <= radius:
            yield tuple(position[k] + sum(axis[k] * distance.point_a[n]
                                           for n, axis in enumerate(axes)) for k in range(3))

def overlaps(geometry: Geometry, feet, yaw, pitch, a, b, radius, owner_radius, owner_height) -> bool:
    """A melee query capsule against the actual model's triangle mesh."""
    return next(overlap_points(geometry, feet, yaw, pitch, a, b, radius, owner_radius, owner_height),
                None) is not None
