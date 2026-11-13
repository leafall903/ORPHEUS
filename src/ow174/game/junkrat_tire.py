"""Partial resource-backed RIP-Tire actor reconstruction, not native possession transport.

The creator's0232 supplies readiness/cost and recovery.039C's prepared external
mover supplies physical dimensions/speeds.0233 supplies arming and explosion.
Wall-climb contact is reconstructed from0234. The same039C actor now publishes
its identity, pose and pools; full actor VM/possession and client acceptance remain open.
"""

import json
import math
import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from ow174.game import actors, sombra_replay, world
from ow174.game.collision import NOT_GROUND
from ow174.game.commands import JUMP, Command
from ow174.game.mover import REPEAT, WORLD_GRAVITY, Mover, MoverData, statescript_mods
from ow174.game.script import expr, graph, nodes, runtime
from ow174.paths import DATA_DIR

HERO, BODY = 0x02E0000000000065, 0x040000000000039C
FIRST, LIMIT = 0xC0090000, 0xC00A0000
DETONATE_BUTTONS = 0x23  # 0233 listens to primary20, secondary29 and ultimate37


class PlacementContext(expr.Context):
    """Body inputs for the authored placement expressions in the static adapter."""

    def __init__(self, position, forward, scale):
        super().__init__()
        self.values = {}
        self.inputs = {
            "STU_71A87E92": expr.Vec3(*position),
            "STU_021D01CD": expr.Vec3(*forward),
            "STU_FDBB0B67": scale,
        }

    def var(self, scope, var):
        return self.values.get((scope, var))

    def native(self, cfg):
        return self.inputs.get(cfg["$"], expr.EVALUATION_FAILED)


def load_profile(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (data.get("format_version") != 1 or data.get("build") != 104319
            or data.get("body") != f"0x{BODY:016X}"):
        raise ValueError("RIP-Tire profile must describe build104319/body039C")
    health, mover = data.get("health"), data.get("mover")
    if type(health) not in (int, float) or not math.isfinite(health) or health <= 0:
        raise ValueError("invalid RIP-Tire health")
    if (not isinstance(mover, dict) or set(mover) - {item.name for item in fields(MoverData)}
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in mover.values())):
        raise ValueError("invalid RIP-Tire mover data")
    required = {"stand_height", "stand_radius", "stand_pogo", "run_forward", "run_backward",
                "run_strafe", "jump_speed", "gravity_scale"}
    if not required <= mover.keys() or any(mover[key] <= 0 for key in required):
        raise ValueError("incomplete RIP-Tire dimensions or movement")
    for key in ("ground_circle_rays", "character_collision"):
        if key in mover:
            mover[key] = int(mover[key])
    return float(health), MoverData(**mover)


@dataclass(frozen=True)
class WallClimb:
    speed: float
    duration: float
    margin: float
    opposition: float


def wall_climb_profile():
    path = os.environ.get("ORPHEUS_JUNKRAT_TIRE_CONTROLLER")
    if path:
        try:
            controller = graph.Graph(json.loads(Path(path).read_text(encoding="utf-8")))
        except (KeyError, TypeError) as error:
            raise ValueError("invalid RIP-Tire 0234 controller record") from error
    else:
        controller = graph.graph(0x234)
    if controller is None:
        return None
    node = controller.node(23)
    if controller.index != 0x234 or node is None or node.cls != "STU_92ED01D0":
        raise ValueError("expected RIP-Tire 0234 wall-climb state")
    values = tuple(node.field(f"{key}.m_value") for key in (
        "m_4E3BB2CE", "m_E7146E0B", "m_70E47D79", "m_94554C04",
    ))
    if (any(type(value) not in (float, int) or not math.isfinite(value) for value in values)
            or min(values[:3]) <= 0 or not -1 <= values[3] < 0):
        raise ValueError("invalid RIP-Tire 0234 wall-climb fields")
    return WallClimb(*values)


@dataclass(kw_only=True)
class Tire(actors.Deployable):
    script: object
    instance: object
    ability: object
    mover: object
    vitals: object
    drive_tick: int
    arm_tick: int
    end_tick: int
    blast: object
    recovery: float
    controls: Command
    drive: runtime.Instance
    last_move_tick: int | None = None
    jump_pending: bool = False
    climb: WallClimb | None = None
    climb_ticks: int = 0
    climbing: bool = False
    retreat: tuple[tuple[float, float, float], float, int] | None = None
    last_retreat_tick: int = 0
    viewers: dict = field(default_factory=dict)

    @property
    def creator(self):
        return self.owner

    @property
    def source(self):
        return self.source_body


class TireSystem:
    def __init__(self, combat):
        self.combat = combat
        self.actors: dict[int, Tire] = {}
        self.recovery: dict[int, tuple] = {}
        self.next_entity = FIRST
        self.last_error = None
        self.last_result = None

    def for_player(self, player):
        return next(
            (t for t in self.actors.values() if t.creator is player and t.source == player.body), None,
        )

    def busy(self, entity):
        return entity in self.recovery or any(t.source == entity for t in self.actors.values())

    def begin(self, player, command):
        if (player.hero.guid != HERO or self.busy(player.body) or self.combat.is_dead(player)
                or self.combat.is_stunned(player)):
            return
        script = player.body_script
        born = sombra_replay.world_frame(script, command.frame)
        if born is None:
            born = self.combat.tick
        ability = next((a for a in script.ultimates if a.flags & nodes.Ability.RUNNING), None)
        if ability is None:
            return
        instance = ability.instance
        placement = instance.states.get(instance.graph.node(37).state)
        if placement is None or not placement.active:
            return
        # 0232 checks the grounded input after either placement result. Use the
        # posed Match mover, then let 58 -> 63 -> 59 retry only while Q is held.
        retry = instance.states.get(instance.graph.node(63).state)
        if retry is not None and retry.active:
            retry.request(runtime.ABORT)
        if player.mover.airborne:
            placement.request(runtime.ABORT)
            instance.run_node(instance.graph.node(58), placement)
            script.component.run_now()
            return
        from ow174.game.combat import TICK, HitDef, Vitals, aim

        path = Path(os.environ.get("ORPHEUS_JUNKRAT_TIRE_PROFILE", DATA_DIR / "junkrat_tire_174.json"))
        try:
            health, data = load_profile(path)
            climb = wall_climb_profile()
        except (OSError, ValueError, TypeError) as error:
            self.last_error = str(error)
            ability.stop()
            return
        if self.next_entity >= LIMIT:
            raise RuntimeError("RIP-Tire identity range exhausted")
        profile = graph.graph(0x0233)
        if profile is None:
            raise RuntimeError("RIP-Tire followup graph0233 is unavailable")
        forward = aim(command.yaw, 0)
        position = player.mover.position
        cast = instance.graph.node(37)
        cast_length = cast.field("m_75198C98.m_castLength")
        scale = ability.evaluate(cast_length["m_configVars"][0], watch=False)
        context = PlacementContext(position, forward, scale)
        origin = expr.evaluate(cast.field("m_75198C98.m_11EFE48E"), context)
        length = expr.evaluate(cast_length, context)
        fallback = expr.evaluate(instance.graph.node(54).field("m_value"), context)
        if (not isinstance(origin, expr.Vec3) or not isinstance(fallback, expr.Vec3)
                or not isinstance(length, (float, int)) or not math.isfinite(length) or length <= 0):
            self.last_error = "RIP-Tire placement expressions did not resolve"
            ability.stop()
            return
        ground = self.combat._collision().ground(origin.x, origin.z, origin.y, origin.y - length)
        candidates = []
        if ground is not None and not ground.surface & NOT_GROUND:
            candidates.append(((origin.x, ground.y, origin.z), False))
        candidates.append(((fallback.x, fallback.y, fallback.z), True))
        mover = None
        for feet, near_body in candidates:
            candidate = Mover(feet, command.yaw, self.combat._collision(), data)
            radius, segment = candidate._capsule()
            if candidate._engine_body().fits(radius, segment + radius):
                mover = candidate
                break
        if mover is None:
            self.last_error = "RIP-Tire placement capsule does not fit"
            ability.stop()
            return

        def value(position, field):
            return float(ability.evaluate(profile.node(position).field(field), watch=False))

        windup, arm_delay = value(73, "m_timeout"), value(25, "m_timeout")
        duration = float(ability.evaluate(instance.graph.node(86).field("m_value"), watch=False))
        maximum, minimum = value(3, "m_value"), value(26, "m_value")
        inner, outer = value(158, "m_value"), value(159, "m_value")
        loadout = ability.evaluate(profile.node(56).field("m_modifyHealth.m_loadout"), watch=False)
        modify = profile.node(56).field("m_modifyHealth")
        force = float(ability.evaluate(modify["m_E33EFEE1"], watch=False))
        floor = float(ability.evaluate(modify["m_583C208B"], watch=False))
        impulse_rings = tuple(
            (radius, *(float(ability.evaluate(row[key], watch=False))
                       for key in ("m_579D7CFF", "m_10399AE7")))
            for radius, row in zip((inner, outer), modify["m_knockbackRings"], strict=True)
        )
        # Original0232/30 passes the body's component43 owner to the factory.
        # Keep this separate from the retained combat source body.
        pet_source = ability.evaluate(instance.graph.node(30).field("m_D749D7D8"), watch=False)
        if not isinstance(pet_source, expr.Entity) or not expr.valid_entity(pet_source.id):
            self.last_error = "RIP-Tire authored Pet source is unavailable"
            ability.stop()
            return
        drive_tick = born + math.ceil(windup / TICK)
        arm_tick = born + math.ceil((windup + arm_delay) / TICK)
        entity, self.next_entity = self.next_entity, self.next_entity + 1
        # Evaluate the authored selector and host only its movement modifiers.
        drive_component = runtime.Component(entity)
        drive_component.frame = born
        drive_component.now = born * drive_component.frame_ms
        drive_instance = drive_component.create(profile, init=False)
        drive_instance.node_state(profile.node(32))
        drive_instance.node_state(profile.node(23)).begin()
        mover.track_mods(statescript_mods(drive_component), born - 1)
        tire = Tire(
            entity=entity, definition=BODY, kind="junkrat-tire", team=player.team,
            position=tuple(mover.position), health=health, radius=data.stand_radius,
            height=data.stand_height, owner=player, source_body=player.body,
            pet_owner=pet_source.id, filter_bits=player.team_bit,
            created_tick=born, expires_tick=arm_tick + math.ceil(duration / TICK),
            script=script, instance=instance, ability=ability, mover=mover, vitals=Vitals(entity, health),
            drive_tick=drive_tick, arm_tick=arm_tick, end_tick=arm_tick + math.ceil(duration / TICK),
            blast=HitDef(maximum, loadout.guid if isinstance(loadout, expr.Asset) else None,
                         rings=((inner, 1.0), (outer, minimum / maximum)),
                         horizontal_impulse=force, vertical_impulse_minimum=floor,
                         impulse_rings=impulse_rings),
            recovery=float(ability.evaluate(instance.graph.node(19).field("m_timeout"), watch=False)),
            controls=replace(command, frame=born, right=0, forward=0),
            drive=drive_instance, climb=climb,
        )
        if near_body:
            # The static actor has no native associated companion for the 0232/98 check.
            node = instance.graph.node(55)
            context.values[0, 956] = expr.evaluate(instance.graph.node(53).field("m_value"), context)
            target = expr.evaluate(node.field("m_865F3D01"), context)
            duration = expr.evaluate(node.field("m_5BEFF040"), context)
            window = expr.evaluate(instance.graph.node(61).field("m_timeout"), context)
            if isinstance(target, expr.Vec3) and duration > 0:
                destination = (target.x, target.y, target.z)
                speed = math.dist(position, destination) / duration
                if speed > 0:
                    tire.retreat = destination, speed, born + math.ceil(window / TICK)
                    tire.last_retreat_tick = born
        self.actors[entity] = tire
        self.combat.vitals[entity] = tire.vitals
        self.publish()
        player.mover.velocity = (0.0, 0.0, 0.0)
        player.mover.throttles = (0, 0)
        script.set_ult_charge(0.0)
        instance.write((0, 38), expr.Entity(entity))
        instance.write((0, 625), expr.Vec3(*feet))
        instance.write((0, 1735), 1)
        # Explicit adapter for this placement result only; no generic native
        # ShapeCast/CreateEntity implementation is claimed by creating this actor.
        placement.request(runtime.ABORT)
        script.component.run_now()
        self.last_error = None

    def create_updates(self, viewer):
        """Publish each existing039C identity once per viewer connection, including late joins."""
        if viewer.client is None or viewer.gone:
            return []
        updates = []
        for tire in self.actors.values():
            if tire.viewers.get(viewer.slot) is viewer.client:
                continue
            tire.viewers[viewer.slot] = viewer.client
            updates.append(self.combat.deployable_create(tire))
        return updates

    def publish(self, *, movement=False):
        for viewer in self.combat.match.players:
            if viewer.client is None or not viewer.spawned or viewer.gone:
                continue
            viewer.client.queue_entities(self.create_updates(viewer))
            if movement:
                for tire in self.actors.values():
                    viewer.client.queue_entities([world.EntityUpdate(
                        tire.entity, movement=world.Movement(
                            tire.mover.position, yaw=tire.mover.yaw, velocity=tire.mover.velocity,
                            frame=self.combat.tick,
                        ),
                    )])

    def command(self, player, command, tick):
        tire = self.for_player(player)
        if tire is None:
            return player.body in self.recovery
        event = sombra_replay.world_frame(player.body_script, command.frame)
        if event is not None:
            tick = event
        pressed = command.buttons & ~tire.controls.buttons
        tire.controls = replace(command, frame=tick)
        if tick >= tire.arm_tick and pressed & DETONATE_BUTTONS:
            from ow174.game.combat import TICK

            endpoint, now = self.combat.tick, self.combat.now
            pose = next((sample for sample in reversed(tire.mover.history) if sample.frame == tick), None)
            if pose is not None:
                tire.mover.position[:] = pose.position
                tire.position = tuple(pose.position)
            try:
                self.combat.tick = tick
                self.combat.now = now - (endpoint - tick) * TICK
                self.finish(tire, "detonated", explode=True)
            finally:
                self.combat.tick, self.combat.now = endpoint, now
        elif tick >= tire.drive_tick and pressed & JUMP:
            tire.jump_pending = True
        return True

    def targets(self):
        from ow174.game.combat import Target

        for tire in self.actors.values():
            data = tire.mover.data
            yield Target(tire.entity, tire.creator.team, tuple(tire.mover.position), data.stand_height,
                         data.stand_radius, 1.0, tire, critical_allowed=False, construct=tire)

    @staticmethod
    def set_drive_input(tire, slot, tick, *, entity=False):
        instance = tire.drive
        component = instance.component
        component.frame = tick
        component.now = tick * component.frame_ms
        if entity:
            component.set(slot, True)
        else:
            instance.write((0, slot), True)
        selector = instance.states[12]
        selected = expr.truthy(selector.evaluate(selector.node.field("m_condition"), watch=False))
        if selected != selector.current:
            selector.current = selected
            instance.states[7 if selected else 2].end(True)
            instance.node_state(instance.graph.node(14 if selected else 23)).begin()
            tire.mover.track_mods(statescript_mods(component), tick - 1)

    @staticmethod
    def step_climb(tire, command, seconds):
        climb = tire.climb
        if climb is None:
            return
        mover = tire.mover
        if not mover.airborne:
            tire.climb_ticks = 0
            tire.climbing = False
            return
        limit = math.ceil(climb.duration / seconds)
        clears_edge = False
        if command.buttons & JUMP and tire.climb_ticks < limit:
            angle = command.yaw * math.tau / 65536
            direction = (math.sin(angle), 0.0, math.cos(angle))
            x, y, z = mover.position
            radius = mover.data.stand_radius
            reach = radius + climb.margin

            def has_face(height):
                hit = mover.world.cast_ray((x, y + height, z), direction, reach, mover.category)
                return (hit is not None and abs(hit.normal[1]) <= .25
                        and sum(a * b for a, b in zip(hit.normal, direction, strict=True)) <= climb.opposition)

            if has_face(mover.data.stand_height * .5) or (tire.climbing and has_face(.01)):
                vx, _, vz = mover.velocity
                mover.velocity = (vx, climb.speed + WORLD_GRAVITY * mover.values.gravity_scale * seconds, vz)
                mover.fall = 0.0
                tire.climb_ticks += 1
                tire.climbing = True
                return
            if tire.climbing:
                ahead = reach + radius
                top = mover.world.ground(x + direction[0] * ahead, z + direction[2] * ahead,
                                         y + .01, y - climb.margin, mover.category)
                clears_edge = top is not None and top.normal[1] >= mover.slope_cos32
        if tire.climbing:
            if not clears_edge:
                vx, vy, vz = mover.velocity
                mover.velocity = (vx, min(vy, 0.0), vz)
            tire.climb_ticks = limit
            tire.climbing = False

    def step_retreat(self, tire, tick, seconds):
        if tire.retreat is None or tick <= tire.last_retreat_tick:
            return
        tire.last_retreat_tick = tick
        player = tire.creator
        mover = player.mover
        target, speed, end = tire.retreat
        if (tick >= end or self.combat.is_dead(player) or self.combat.is_stunned(player, tick=tick)
                or self.combat.junkrat.rooted(tire.source, tick)):
            tire.retreat = None
            mover.velocity = (0.0, 0.0, 0.0)
            return
        command = Command(tick, yaw=mover.yaw, pitch=mover.pitch)
        if mover.step_pose(command, seconds, tick, move_to=(target, speed)) is not None:
            tire.retreat = None

    def deadlines(self):
        yield from (tire.end_tick for tire in self.actors.values())
        yield from (recovery[-1] for recovery in self.recovery.values())

    def update(self, *, deadlines_only=False):
        from ow174.game.combat import TICK

        tick = self.combat.tick
        for tire in list(self.actors.values()):
            if (tire.creator.gone or tire.creator.body != tire.source or tire.script.retired
                    or tire.creator.body_script is not tire.script):
                self.finish(tire, "owner-retired", recover=False)
                continue
            elif tick >= tire.end_tick:
                self.finish(tire, "timeout", explode=True)
                continue
            if deadlines_only:
                continue
            self.step_retreat(tire, tick, TICK)
            if tick >= tire.drive_tick and (tire.last_move_tick is None or tick > tire.last_move_tick):
                if tire.last_move_tick is None:
                    self.set_drive_input(tire, 699, tick)
                command = tire.controls
                if tick - command.frame > REPEAT:
                    command = Command(tick, yaw=command.yaw, pitch=command.pitch)
                buttons = command.buttons | JUMP if tire.jump_pending else command.buttons
                self.step_climb(tire, command, TICK)
                tire.mover.step_pose(replace(command, frame=tick, buttons=buttons), TICK, tick)
                tire.jump_pending = False
                tire.last_move_tick = tick
                tire.position = tuple(tire.mover.position)
                tire.instance.write((0, 1735), 2)
        if not deadlines_only:
            self.publish(movement=True)
        for entity, (player, script, _, _, end) in list(self.recovery.items()):
            if self.combat.tick < end and player.body == entity and not script.retired:
                continue
            self._end_recovery(entity)

    def retire_body(self, entity):
        for tire in list(self.actors.values()):
            if tire.source == entity:
                self.finish(tire, "owner-retired", recover=False)
        self._end_recovery(entity)

    def _end_recovery(self, entity):
        recovery = self.recovery.pop(entity, None)
        if recovery is not None:
            _, script, instance, ability, _ = recovery
            if not instance.stopped:
                ability.stop()
                instance.write((0, 1735), 0)
                # Reconstructed control handoff. Tire's equal-valued stack
                # binding can leave the parked-mine priority gate stale.
                for mine_graph in script.component.instances.values():
                    if mine_graph.graph.index != 0x096A:
                        continue
                    gate = mine_graph.graph.node(31)
                    state = mine_graph.states.get(gate.state) if gate is not None else None
                    if state is not None and state.active and not state.ending:
                        state.dependency_changed()

    def finish(self, tire, reason, *, explode=False, recover=True):
        if self.actors.pop(tire.entity, None) is not tire:
            return
        for viewer in self.combat.match.players:
            if viewer.client is not None and tire.viewers.get(viewer.slot) is viewer.client:
                viewer.client.queue_entities([world.EntityUpdate(tire.entity, world.OP_DESTROY)])
        tire.viewers.clear()
        from ow174.game.combat import TICK, Rocket

        self.last_result = reason
        self.combat.vitals.pop(tire.entity, None)
        self.combat.history.pop(tire.entity, None)
        self.combat.replacement_ticks.pop(tire.entity, None)
        if tire.retreat is not None:
            tire.retreat = None
            if tire.creator.body == tire.source and tire.creator.body_script is tire.script:
                tire.creator.mover.velocity = (0.0, 0.0, 0.0)
        if explode:
            self.set_drive_input(tire, 2016, self.combat.tick)
        else:
            # This static actor's terminal marker supplies the selector's entity-side gate.
            self.set_drive_input(tire, 478, self.combat.tick, entity=True)
        tire.mover.velocity = (0.0, 0.0, 0.0)
        # Keep charge and new casts gated across the synchronous blast callbacks.
        if recover and not tire.script.retired and tire.creator.body == tire.source:
            self.recovery[tire.source] = (
                tire.creator, tire.script, tire.instance, tire.ability,
                self.combat.tick + math.ceil(tire.recovery / TICK),
            )
        if explode:
            x, y, z = tire.mover.position
            rocket = Rocket(
                tire.creator, tire.creator.team, (x, y + .6, z), (0, 0, 1), 0, 0, None, tire.blast,
                source_body=tire.source, source_graph=0x0233,
            )
            self.combat._explode(rocket, rocket.position)
        if not tire.instance.stopped:
            tire.instance.write((0, 38), 0)
        if tire.source not in self.recovery and not tire.instance.stopped:
            tire.ability.stop()
            tire.instance.write((0, 1735), 0)
