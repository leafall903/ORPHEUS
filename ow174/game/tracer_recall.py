"""Selected Tracer Recall history and playback for the authored 001A graph."""

from collections import deque
from dataclasses import dataclass

from ow174.game.mover import turned_pitch
from ow174.game.script import expr, nodes, runtime
from ow174.game.world import HEALTH_POOL

HERO = 0x02E0000000000003


@dataclass(frozen=True)
class Sample:
    time: int
    position: tuple
    health: float


@dataclass
class Recall:
    activation: tuple[int, int]
    sample: Sample
    end_ms: int
    playback: bool = False
    finished: bool = False


class Recalls:
    def __init__(self, combat):
        self.combat = combat
        self.history: dict[int, deque] = {}
        self.active: dict[int, Recall] = {}

    @staticmethod
    def instance(player):
        script = getattr(player, "body_script", None)
        if (script is None or script.retired or script.component.owner_dead
                or script.hero != HERO or script.component.strict):
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x001A), None)

    def protected(self, player, *, frame=None):
        body = getattr(player, "body", None)
        cast = self.active.get(body)
        if cast is None or cast.finished:
            return False
        instance = self.instance(player)
        if instance is None or not instance.state(16).flags & nodes.Ability.RUNNING:
            return False
        now = (self.combat.tick if frame is None else frame) * instance.component.frame_ms
        return cast.activation[1] <= now < cast.end_ms

    def retire(self, body):
        self.history.pop(body, None)
        self.active.pop(body, None)

    def remember(self, player, frame):
        instance = self.instance(player)
        if instance is None or self.protected(player, frame=frame):
            return
        if not player.mover.history or player.mover.history[-1].frame != frame:
            return
        target = next((t for t in self.combat._targets() if t.owner is player), None)
        if target is None:
            return
        history = self.history.setdefault(player.body, deque())
        now = frame * instance.component.frame_ms
        if history and history[-1].time >= now:
            return
        health = self.combat.vitals_of(target).now[HEALTH_POOL]
        history.append(Sample(now, tuple(player.mover.position), health))
        cutoff = now - self._lookback(instance)
        while len(history) > 1 and history[1].time <= cutoff:
            history.popleft()

    @staticmethod
    def _lookback(instance):
        return max(0.0, expr.to_float(instance.find_var(expr.INSTANCE, 7125).value())) * 1000

    @staticmethod
    def hold_pose(player, command):
        mover = player.mover
        mover.yaw = command.yaw
        mover.pitch = turned_pitch(command.pitch)[0]
        mover.history.append(mover.snapshot(command.frame))

    def command(self, player, command):
        instance = self.instance(player)
        if instance is None:
            return
        cast = self.active.get(player.body)
        warmup, playback = instance.states.get(3), instance.states.get(0)
        if (cast is not None and not cast.finished and cast.playback and playback is not None
                and not playback.active and playback.ended_at >= cast.end_ms):
            self._restore(player, instance, cast)
        if not instance.state(16).flags & nodes.Ability.RUNNING:
            if cast is not None:
                cast.finished = True
            return
        if command is not None and warmup is not None and warmup.active:
            activation = (warmup.counter, warmup.start)
            if cast is None or cast.activation != activation:
                history = self.history.get(player.body)
                if not history:
                    return
                cutoff = warmup.start - self._lookback(instance)
                sample = next((s for s in reversed(history) if s.time <= cutoff), history[0])
                warm = instance.graph.node(16).field("m_5BEFF040")
                duration = instance.graph.node(1).field("m_FAF3B2D3")
                warm_ms = round(expr.to_float(instance.evaluate(warm)) * 1000)
                play_ms = round(expr.to_float(instance.evaluate(duration)) * 1000)
                cast = Recall(activation, sample, warmup.start + warm_ms + play_ms)
                self.active[player.body] = cast
                wait = warmup.start + warm_ms - instance.component.now
                warmup.request(runtime.FINISH, max(0, wait))
        if cast is None or playback is None or not playback.active or cast.playback:
            return
        cast.playback = True
        playback.request(runtime.FINISH, max(0, cast.end_ms - instance.component.now))

    def update(self):
        if not self.active:
            return
        for player in self.combat.match.players:
            cast = self.active.get(player.body)
            if cast is None or cast.finished:
                continue
            instance = self.instance(player)
            if instance is None or not instance.state(16).flags & nodes.Ability.RUNNING:
                cast.finished = True
            elif self.combat.tick * instance.component.frame_ms >= cast.end_ms:
                self._restore(player, instance, cast)

    def _restore(self, player, instance, cast):
        cast.finished = True
        target = next((t for t in self.combat._targets() if t.owner is player), None)
        if target is None:
            return
        vitals = self.combat.vitals_of(target)
        if vitals.dead:
            return
        restored = min(cast.sample.health, vitals.most[HEALTH_POOL])
        vitals.heal(max(0.0, restored - vitals.now[HEALTH_POOL]))
        self.combat._sync_body_health(player, vitals)
        mover = player.mover
        x, y, z = cast.sample.position
        mover._place(mover._engine_body(), (x, y + mover._lift(), z), False)
        mover.velocity = (0.0, 0.0, 0.0)
        mover.fall = 0.0
        if mover.history and mover.history[-1].frame == instance.component.frame:
            mover.history[-1] = mover.snapshot(instance.component.frame)
        self.combat.record_discontinuity(player)
        for weapon in instance.component.instances.values():
            if weapon.graph.index == 0x0016:
                weapon.write((expr.INSTANCE, 53), weapon.find_var(expr.INSTANCE, 198).value())
