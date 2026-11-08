"""Reconstructed Zenyatta combat services, hosted by the actual match Combat.

Authored inputs: range40 (0176/02F3), Harmony30 (02F3#420), LOS grace3
(their Waits), ultimate6 (017B#567). Cone selection, cast recovery0.75,
Discord1.25 and Transcendence300/s are explicit server reconstructions.
This is not a native engine implementation or original-client acceptance.
"""

import math
from dataclasses import dataclass, field

from ow174.game import stats
from ow174.game.script import expr
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E0000000000020
DISCORD, HARMONY = 0x08, 0x10
ORB_RANGE, LOS_GRACE, RECOVERY = 40.0, 3.0, 0.75
HARMONY_RATE, TRANSCENDENCE_RATE, DISCORD_SCALE = 30.0, 300.0, 1.25


@dataclass
class Orb:
    target: int
    last_visible: float


@dataclass
class Owner:
    player: object
    body: int
    buttons: int = 0
    ready: dict = field(default_factory=dict)
    harmony: Orb | None = None
    discord: Orb | None = None
    ultimate_active: bool = False
    last_damage: float = 0.0


class Zenyatta:
    def __init__(self, combat):
        self.combat = combat
        self.owners: dict[int, Owner] = {}
        self.last_tick: int | None = None

    def owner(self, player):
        if player.hero.guid != HERO:
            return None
        found = self.owners.get(player.body)
        if found is None:
            found = Owner(player, player.body, last_damage=self.combat.now)
            self.owners[player.body] = found
        return found

    def retire(self, body):
        self.owners.pop(body, None)
        for owner in self.owners.values():
            for name in ("harmony", "discord"):
                orb = getattr(owner, name)
                if orb is not None and orb.target == body:
                    self.set_orb(owner, name, None)

    def set_orb(self, owner, name, orb):
        setattr(owner, name, orb)
        script = getattr(owner.player, "body_script", None)
        if script is None or script.retired or owner.player.body != owner.body:
            return
        graph = 0x02F3 if name == "harmony" else 0x0176
        for instance in script.component.instances.values():
            if instance.graph.index == graph:
                instance.write((0, 1606), expr.Entity(orb.target) if orb is not None else 0)

    def ultimate(self, player):
        script = getattr(player, "body_script", None)
        if script is None or getattr(player.hero, "guid", None) != HERO:
            return False
        return any(
            instance.graph.index == 0x017B and (state := instance.states.get(3)) is not None
            and state.active for instance in script.component.instances.values()
        )

    def allied(self, player, target):
        return target.owner is player or (
            not self.combat.match.game_map.free_for_all and target.team == player.team
        )

    def visible(self, player, target):
        from ow174.game.combat import _sub, ray_world

        origin = self.combat._eye(player)
        point = (target.feet[0], target.feet[1] + target.eye * 0.5, target.feet[2])
        delta = _sub(point, origin)
        distance = math.sqrt(sum(axis * axis for axis in delta))
        if distance <= 1e-9:
            return True
        wall = ray_world(self.combat._collision(), origin, tuple(axis / distance for axis in delta), distance)
        return wall is None or wall >= distance

    def select(self, player, command, healing):
        from ow174.game.combat import _dot, _sub, aim

        origin, direction = self.combat._eye(player), aim(*command.aim())
        best = None
        for target in self.combat._targets():
            if target.owner is player or self.allied(player, target) != healing:
                continue
            if self.combat.vitals_of(target).dead:
                continue
            delta = _sub((target.feet[0], target.feet[1] + target.eye, target.feet[2]), origin)
            distance = math.sqrt(_dot(delta, delta))
            if distance > ORB_RANGE or distance <= 1e-9:
                continue
            alignment = _dot(delta, direction) / distance
            if alignment < math.cos(math.radians(10)) or not self.visible(player, target):
                continue
            key = (-alignment, distance, target.entity)
            if best is None or key < best[0]:
                best = key, target
        return best[1] if best is not None else None

    def command(self, player, command):
        owner = self.owner(player)
        if owner is None or command is None:
            return
        pressed = command.buttons & ~owner.buttons
        owner.buttons = command.buttons
        active = self.ultimate(player)
        if active and not owner.ultimate_active:
            target = next((t for t in self.combat._targets() if t.owner is player), None)
            if target is not None:
                vitals = self.combat.vitals_of(target)
                vitals.heal(sum(vitals.most.values()))  # authored017B/ModifyHealth self restoration
                self.combat._sync_body_health(player, vitals)
        owner.ultimate_active = active
        if self.combat.is_dead(player) or active:
            return
        for bit, name, healing in (
            (HARMONY, "harmony", True), (DISCORD, "discord", False),
        ):
            if not pressed & bit or self.combat.now < owner.ready.get(name, 0):
                continue
            target = self.select(player, command, healing)
            if target is None:
                continue
            self.set_orb(owner, name, Orb(target.entity, self.combat.now))
            owner.ready[name] = self.combat.now + RECOVERY
            break

    def boost(self, target):
        for owner in self.owners.values():
            orb = owner.discord
            if (orb is not None and orb.target == target.entity and not self.allied(owner.player, target)
                    and not self.combat.is_dead(owner.player)
                    and self.combat.now - orb.last_visible < LOS_GRACE):
                return DISCORD_SCALE  # multiple Zenyattas do not multiply Discord
        return 1.0

    def charge(self, player, amount):
        script = getattr(player, "body_script", None)
        if (getattr(player.hero, "guid", None) != HERO or script is None or amount <= 0
                or self.combat.is_dead(player) or self.ultimate(player)):
            return
        cost = expr.to_float(script.component.entity_var(338).value())
        current = expr.to_float(script.component.entity_var(342).value())
        script.set_ult_charge(min(cost, current + amount))

    def damaged(self, target):
        if getattr(target.owner.hero, "guid", None) == HERO:
            self.owner(target.owner).last_damage = self.combat.now

    def heal(self, healer, target, amount):
        vitals = self.combat.vitals_of(target)
        if vitals.dead or not self.allied(healer, target):
            return
        given = vitals.heal(amount)
        if given > 0:
            healer.stats.add(stats.HEALING, given)
            self.charge(healer, given)
            self.combat._sync_body_health(target.owner, vitals)

    def update(self):
        tick = self.combat.tick
        if self.last_tick is not None and tick <= self.last_tick:
            return
        # The first admitted tick covers one interval, never an entire match uptime.
        dt = 0.016 if self.last_tick is None else (tick - self.last_tick) * 0.016
        self.last_tick = tick
        targets = {target.entity: target for target in self.combat._targets()}
        for body, owner in list(self.owners.items()):
            player = owner.player
            if body not in targets or player.body != body or self.combat.is_dead(player):
                self.retire(body)
                continue
            vitals = self.combat.vitals_of(targets[body])
            # Reconstructed OW1 shield regeneration: shield pool only, 30/s
            # after three seconds without actual damage. Boundary overlap is
            # integrated, so a skipped update does not heal its pre-delay time.
            regen_dt = max(0.0, self.combat.now - max(self.combat.now - dt, owner.last_damage + 3.0))
            if regen_dt and vitals.now[SHIELDS_POOL] < vitals.most[SHIELDS_POOL]:
                vitals.now[SHIELDS_POOL] = min(
                    vitals.most[SHIELDS_POOL], vitals.now[SHIELDS_POOL] + 30.0 * regen_dt,
                )
                vitals.changed.add(SHIELDS_POOL)
                self.combat._sync_body_health(player, vitals)
            for name in ("harmony", "discord"):
                orb = getattr(owner, name)
                if orb is None:
                    continue
                target = targets.get(orb.target)
                if (target is None or self.combat.vitals_of(target).dead
                        or self.allied(player, target) != (name == "harmony")):
                    self.set_orb(owner, name, None)
                    continue
                point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                if math.dist(self.combat._eye(player), point) <= ORB_RANGE and self.visible(player, target):
                    orb.last_visible = self.combat.now
                if self.combat.now - orb.last_visible >= LOS_GRACE:
                    self.set_orb(owner, name, None)
                elif name == "harmony":
                    self.heal(player, target, HARMONY_RATE * dt)
            if self.ultimate(player):
                origin = self.combat._eye(player)
                for target in targets.values():
                    point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                    distance = math.dist(origin, point)
                    if distance <= 10 and self.visible(player, target):
                        self.heal(player, target, TRANSCENDENCE_RATE * dt)
