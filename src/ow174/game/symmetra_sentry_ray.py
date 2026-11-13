"""Scoped1590 ray output links; current poses are explicit external host inputs.

This host uses one completed combat-world cast for the authority and presentation
point. Native client prediction/correction is not reproduced here.
"""
from ow174.game.script import expr, runtime


class SentryRay(runtime.State):
    def __init__(self, *args):
        super().__init__(*args)
        self.result = None
        self.outputs = []

    def on_begin(self):
        self.result = None
        self.outputs = []
        self.withdraw_contacts()
        for field, slot in (('m_5ED803CF', 1), ('m_F2F8606D', 5)):
            target = expr.lvalue(self.node.field('m_target.' + field))
            if target is not None:
                self.outputs.append(self.link(target, slot))

    def on_enter(self):
        self.tick()
        # A current pose may become available after original target acquisition.
        self.ticks()

    def tick(self):
        result = self.instance.component.world.tracked_ray(self)
        if not self.active or self.instance.stopped or self.instance.teardown:
            return
        if result is NotImplemented:
            if self.result is None:
                return
            # Unavailable input is not a completed miss. Withdraw the preceding
            # result so a stale hit cannot keep the authored contact gate open.
            result = None
        elif (type(result) is not runtime.RaycastResult or type(result.hit) is not bool
              or (result.point is not None and not isinstance(result.point, expr.Vec3))
              or (result.entity is not None and not isinstance(result.entity, expr.Entity))):
            raise ValueError('Sentry TrackRay needs a typed completed cast')
        before = [(var, var.value()) for var in self.outputs]
        self.result = result
        for var, previous in before:
            self.instance.changed(var, previous)
        outputs = self.node.field('m_target')
        entity = (result.entity or expr.Entity(0)) if result is not None else None
        for field, value in (('m_692DFF72', entity),
                             ('m_7BA44A69', result.hit if result is not None else False)):
            target = expr.lvalue(outputs.get(field))
            if target is not None:
                self.instance.write(target, value)

    def withdraw_contacts(self):
        """Invalidate host contact eligibility without fabricating a completed miss."""
        for field, value in (('m_692DFF72', None), ('m_7BA44A69', False)):
            target = expr.lvalue(self.node.field('m_target.' + field))
            if target is not None and self.instance.find_var(*target).value() is not None:
                self.instance.write(target, value)

    def output(self, slot):
        return self.result.point if self.result is not None and slot in (1, 5) else None

    def on_cleanup(self):
        for var in self.outputs:
            self.unlink(var)
        self.outputs.clear()
        self.result = None
        self.withdraw_contacts()
