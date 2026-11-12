"""Reconstructed Zenyatta combat services, hosted by the actual match Combat.

Authored inputs: range40 (0176/02F3), Harmony30 (02F3#420), LOS grace3
(their Waits), ultimate6 (017B#567). Cone selection, cast recovery0.75,
Discord1.25 and Transcendence300/s are explicit server reconstructions.
This is not a native engine implementation or original-client acceptance.
"""

import math
from dataclasses import dataclass, field

from ow174.game import defense_filters, stats
from ow174.game.script import expr
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E0000000000020
DISCORD, HARMONY = 0x08, 0x10
ORB_RANGE, LOS_GRACE, RECOVERY = 40.0, 3.0, 0.75
HARMONY_RATE, TRANSCENDENCE_RATE, DISCORD_SCALE = 30.0, 300.0, 1.25
TICK = 0.016


def weapon_assignment(world, instance, node, value):
    """Reconstructed charge budget for0164's literal charge-loop assignments.

    The graph spends ammunition while charging. Its generic loop currently
    continues counting after the pool reaches zero; remember the ammunition
    at its first authored increment, then cap subsequent increments before it becomes
    the released volley's count. Other graph assignments are unchanged.
    """
    if world.hero == HERO and instance.graph.index == 0x0164 and node.pos == 45:
        if expr.to_float(instance.find_var(0, 1392).value()) <= 0:
            world.zenyatta_charge_ammo = max(0, expr.to_int(instance.find_var(0, 53).value()))
        if world.zenyatta_charge_ammo is not None:
            return min(expr.to_float(value), world.zenyatta_charge_ammo)
    return value


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

    def interrupt(self, player):
        """Reconstructed control binding to0164's authored lost-priority exit.

        Stack122 under ->98 clears1392 and aborts35/visual scopes before the
        held button is released. A running35 also uses its normal end/cooldown
        plugs. No ammunition refund or generic body-control semantics implied.
        """
        script = getattr(player, 'body_script', None)
        if getattr(getattr(player, 'hero', None), 'guid', None) != HERO or script is None:
            return
        for instance in list(script.component.instances.values()):
            if instance.graph.index != 0x0164:
                continue
            node = instance.graph.node(122)
            charge = instance.states.get(node.state) if node is not None else None
            if charge is not None and charge.active:
                instance.follow(node, 'm_underSubgraphPlug', charge)
            node = instance.graph.node(35)
            volley = instance.states.get(node.state) if node is not None else None
            if volley is not None and volley.active:
                volley.end(False)
        script.component.run_now()

    def set_orb(self, owner, name, orb):
        setattr(owner, name, orb)
        script = getattr(owner.player, "body_script", None)
        if script is None or script.retired or owner.player.body != owner.body:
            return
        graph = 0x02F3 if name == "harmony" else 0x0176
        for instance in script.component.instances.values():
            if instance.graph.index == graph:
                instance.write((0, 1606), expr.Entity(orb.target) if orb is not None else 0)

    def ultimate_state(self, player):
        script = getattr(player, "body_script", None)
        if script is None or getattr(getattr(player, "hero", None), "guid", None) != HERO:
            return None
        return next((state for instance in script.component.instances.values()
                     if instance.graph.index == 0x017B and (state := instance.states.get(3)) is not None
                     and state.active), None)

    def ultimate_last_world_tick(self, player):
        state = self.ultimate_state(player)
        return defense_filters.last_countdown_world_tick(state) if state is not None else None

    def ultimate(self, player):
        end = self.ultimate_last_world_tick(player)
        return end is not None and self.combat.tick <= end

    def stale_ultimate_flag(self, player):
        end = self.ultimate_last_world_tick(player)
        return end is not None and self.combat.tick > end

    def cleanse_discord(self, body):
        for owner in self.owners.values():
            if owner.discord is not None and owner.discord.target == body:
                self.set_orb(owner, "discord", None)

    def allied(self, player, target):
        return target.owner is player or (
            not self.combat.match.game_map.free_for_all and target.team == player.team
        )

    def visible(self, player, target, *, hostile_barriers=False):
        from ow174.game.combat import _sub, ray_world

        origin = self.combat._eye(player)
        point = (target.feet[0], target.feet[1] + target.eye * 0.5, target.feet[2])
        delta = _sub(point, origin)
        distance = math.sqrt(sum(axis * axis for axis in delta))
        if distance <= 1e-9:
            return True
        direction = tuple(axis / distance for axis in delta)
        wall = ray_world(self.combat._collision(), origin, direction, distance)
        if wall is not None and wall < distance:
            return False
        return not hostile_barriers or self.combat._barrier_contact(
            player, origin, direction, distance, bubbles=True) is None

    def select(self, player, command, healing):
        from ow174.game.combat import _dot, _sub, aim

        origin, direction = self.combat._eye(player), aim(*command.aim())
        best = None
        for target in self.combat._targets():
            if target.owner is player or self.allied(player, target) != healing:
                continue
            if self.combat.vitals_of(target).dead:
                continue
            if not healing and self.combat.barriers.for_target(target.owner) is not None:
                continue
            delta = _sub((target.feet[0], target.feet[1] + target.eye, target.feet[2]), origin)
            distance = math.sqrt(_dot(delta, delta))
            if distance > ORB_RANGE or distance <= 1e-9:
                continue
            alignment = _dot(delta, direction) / distance
            # Reconstructed default acquisition policy. Retention keeps its
            # existing world-only ray. Personal barriers cleanse separately.
            if alignment < math.cos(math.radians(10)) or not self.visible(
                    player, target, hostile_barriers=not healing):
                continue
            key = (-alignment, distance, target.entity)
            if best is None or key < best[0]:
                best = key, target
        return best[1] if best is not None else None

    def command(self, player, command):
        if self.combat.is_dead(player):
            return
        owner = self.owner(player)
        if owner is None or command is None:
            return
        pressed = command.buttons & ~owner.buttons
        owner.buttons = command.buttons
        active = self.ultimate_state(player) is not None
        if active and not owner.ultimate_active:
            target = next((t for t in self.combat._targets() if t.owner is player), None)
            if target is not None:
                vitals = self.combat.vitals_of(target)
                self.combat.cleanse_ana_effects(target.entity)  #017B#8440 satisfies0C72 abort predicate
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

    def discord_deadline_in_gap(self, start, end):
        return any(start < orb.last_visible + LOS_GRACE <= end
                   for owner in self.owners.values() if (orb := owner.discord) is not None)

    def charge(self, player, amount):
        script = getattr(player, "body_script", None)
        if (getattr(getattr(player, "hero", None), "guid", None) != HERO or script is None or amount <= 0
                or self.combat.is_dead(player) or self.ultimate(player)):
            return
        cost = expr.to_float(script.component.entity_var(338).value())
        current = expr.to_float(script.component.entity_var(342).value())
        script.set_ult_charge(min(cost, current + amount))

    def damaged(self, target):
        if getattr(getattr(target.owner, "hero", None), "guid", None) == HERO:
            self.owner(target.owner).last_damage = self.combat.now

    def regenerate_before_damage(self, target, vitals):
        tick = self.combat.tick
        if self.last_tick is None or tick <= self.last_tick + 1:
            return
        owner = self.owners.get(target.entity)
        if owner is not None:
            # Keep healing from omitted updates before damage resets the deadline.
            now = self.combat.now
            self.regenerate_shields(owner, target, vitals, now-(tick-self.last_tick)*TICK, now-TICK, tick-1)

    def regenerate_shields(self, owner, target, vitals, start, end, end_tick):
        # Reconstructed shield regeneration: shield pool only, 30/s
        # after three seconds without actual damage.
        dt = end - max(start, owner.last_damage + 3.0)
        if dt <= 0 or vitals.now[SHIELDS_POOL] >= vitals.most[SHIELDS_POOL]:
            return False
        vitals.now[SHIELDS_POOL] = min(
            vitals.most[SHIELDS_POOL],
            vitals.now[SHIELDS_POOL] + 30.0*dt*self.combat.healing.factor(target, seconds=dt, end_tick=end_tick),
        )
        vitals.changed.add(SHIELDS_POOL)
        return True

    def heal(self, healer, target, amount, *, seconds=0.0, end_tick=None, award_charge=True):
        vitals = self.combat.vitals_of(target)
        if vitals.dead or not self.allied(healer, target):
            return
        given = vitals.heal(amount*self.combat.healing.factor(target, seconds=seconds, end_tick=end_tick))
        if given > 0:
            healer.stats.add(stats.HEALING, given)
            if award_charge:
                self.charge(healer, given)
            self.combat._sync_body_health(target.owner, vitals)

    def competing_heals(self, *, wounded=False):
        targets = {target.entity: target for target in self.combat._targets()}
        sources = {}
        for body, owner in self.owners.items():
            player = owner.player
            if body not in targets or player.body != body or self.combat.is_dead(player):
                continue
            sources[body] = sources.get(body, 0) + 1  # passive shield recovery
            if owner.harmony is not None:
                target = targets.get(owner.harmony.target)
                if target is not None and self.allied(player, target):
                    sources[target.entity] = sources.get(target.entity, 0) + 1
            if self.ultimate(player):
                origin = self.combat._eye(player)
                for target in targets.values():
                    point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                    if self.allied(player, target) and math.dist(origin, point) <= 10:
                        sources[target.entity] = sources.get(target.entity, 0) + 1
        for entity, count in sources.items():
            if count < 2:
                continue
            if not wounded:
                return True
            vitals = self.combat.vitals_of(targets[entity])
            if not vitals.dead and any(vitals.now[pool] < maximum for pool, maximum in vitals.most.items()):
                return True
        return False

    def update(self):
        tick = self.combat.tick
        if self.last_tick is not None and tick <= self.last_tick:
            return
        # The first admitted tick covers one interval, never an entire match uptime.
        first_tick = tick if self.last_tick is None else self.last_tick + 1
        dt = (tick - first_tick + 1) * TICK
        self.last_tick = tick
        targets = {target.entity: target for target in self.combat._targets()}
        for body, owner in list(self.owners.items()):
            player = owner.player
            if body not in targets or player.body != body or self.combat.is_dead(player):
                self.retire(body)
                continue
            target = targets[body]
            vitals = self.combat.vitals_of(target)
            now = self.combat.now
            if self.regenerate_shields(owner, target, vitals, now-dt, now, tick):
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
                elapsed = now - orb.last_visible
                if name == "harmony":
                    end_tick = tick
                    if elapsed >= LOS_GRACE:
                        # The expiry update itself never heals, even in a catch-up interval.
                        end_tick -= math.floor((elapsed-LOS_GRACE)/TICK) + 1
                    if end_tick >= first_tick:
                        seconds = (end_tick-first_tick+1)*TICK
                        self.heal(player, target, HARMONY_RATE*seconds, seconds=seconds, end_tick=end_tick)
                if elapsed >= LOS_GRACE:
                    self.set_orb(owner, name, None)
            end = self.ultimate_last_world_tick(player)
            if end is not None and end >= first_tick:
                end_tick = min(tick, end)
                seconds = (end_tick-first_tick+1)*TICK
                origin = self.combat._eye(player)
                for target in targets.values():
                    point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                    distance = math.dist(origin, point)
                    if distance <= 10 and self.visible(player, target, hostile_barriers=True):
                        self.heal(player, target, TRANSCENDENCE_RATE*seconds,
                                  seconds=seconds, end_tick=end_tick, award_charge=False)
