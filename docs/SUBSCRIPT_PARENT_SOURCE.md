SubScript child instigator (104319)
==================================

Root source initialization does not initialize its SubScript children. The
normal SubScript adapter previously called Component.create with init enabled
and supplied no source. Actual Reaper004B descendants, including01CF's nested
children, therefore retained source0 after the body roots already used SELF.

Original37D754C8 uses table265B9C8. Its allocation slot1B8 isD53D00; the source
selection there has no hero predicate. D53DA3/D53DD6 call the parent instance's
1B92E50 cached/effective source getter. The two allocation routes are:

- Facade98=64DDB0: sourceR9d is saved at entryRSP20, read at64DE1E fromRSP68,
  and forwarded at64DE22 as GameVM argument6/RSP28. Slot28 is97AA20.
- Facade90=64C880: source argument6 is read at64C8CC fromRSP78 and forwarded at
  64C8E0 as GameVM argument8/RSP38. Slot20 is977F20.

Both allocators pass their source to1B94B10 before initialization. Exactly0
falls back throughD0D9B0 to the actual VM owner; nonzero parent instigators are
retained. This is independent of the separate allocation role/parent flags.
1BA1F30 is cleanup, correcting the adapter's former misleading OnBegin comment.

For normal non-strict SubScripts, read the parent source before allocation,
allocate without init, seed typed001B/cache, then init once. Keep authored
parameter values and the parent link. The current composed static inventory of
535 SubScript nodes contains no instance001B parameter targets. Descriptor
sources remain implicit; neither parent instigator nor component owner is
rewritten. The strict explicit host/resource initialization contract keeps its
existing path and remains a separate boundary; there is no hero allowlist.

Three focused methods use actual Reaper idle child/grandchild graphs plus one
real twenty-HIT shotgun shell, another actual Soldier body parent/child chain,
and a synthetic Reaper NPC body with explicitly supplied non-SELF instigator.
All live descendants carry the expected typed001B/cache. Correct non-SELF input
changes actual authored admission branches (the NPC has fewer active children),
so the regression does not require the old source0 graph count. The same final
assertions against formal44's old adapter fail51 source assertions, no errors.

This repairs the server's child source contract. Native complete allocator
execution, original-client opening/fire animation, Shadow Step begin/cancel/
confirm presentation, actual actor model and faction palette remain unverified.
No client/backend, pressure matrix, actor lifetime, shared root or transport
optimization is changed. Native receipts and audit logs remain external lane0002
artifacts; this is not rendered-color acceptance.
