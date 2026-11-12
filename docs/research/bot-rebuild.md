# Training Bot death and rebuild

Build 1.74.0.0.104319. Seen live on 2026-11-08 in a separate offline service; see the
[index](README.md) for the evidence labels.

## Summary

A killed Training Bot falls apart by itself: the client does it when a body it still has reaches
zero health. Putting it together again is a statescript Anim of the bot's own body graph `0BA8`,
started by game message `0013.025`. A server has to deliver that message to the new body, or the
bot simply appears whole.

## Death

Seen live: with the body kept and its health at zero, the bot bursts and its parts lie on the
floor. The model carries a ragdoll configuration. Destroying the entity at once skips this.

## Rebuild

Read from data, graph `0BA8`, an initial graph of the Training Bot's body `1403.003`:

1. A game-message entry waits for `0013.025`.
2. It sets variable 1226 to an animation category and branches on entity variable 7143: true
   starts state 0, false state 1. Both are `STUStatescriptStateAnim` states, remote slots 0 and 1,
   and both play alias `0451.0C8`.
3. The bot's blend-tree set resolves that alias to a random choice between two clips, `39D7` and
   `39D8`. Each lasts 160 frames at 60 fps. Their first frame has the parts up to 33 m away and
   their last frame is the idle pose.
4. Each Anim is authored 2.5 s long with its automatic finish off (`m_154DBA91`), so the state
   stays on after the clip.

Seen live in the other service, which kept the body and revived it 3 s after the death with remote
slot 1 on: the parts on the floor go, the torso drops in from above, the arms fly in from the
sides, and the bot settles into its idle pose about 1.7 s later. Slots 0 and 1 looked the same on
four kills.

Not decoded: what sets entity variable 7143, and who posts `0013.025` in retail. The message is
also the placement's eligibility reset that the firing behaviour waits for.

## Compared with ORPHEUS

- ORPHEUS runs the bot's body graphs on the server, `0BA8` among them, and keeps a killed bot for
  `BOT_RESPAWN` before it makes a new one under a new entity id. Death therefore shows as above.
- `0013.025` went only to the firing behaviour's instance. Nothing delivered it to `0BA8`, so
  neither Anim was ever on and a new bot appeared whole.
- Changed 2026-11-11: a bot made again at its spawner gets `0013.025` on its `0BA8` instance
  before its first frame is built. With variable 7143 unset the graph takes state 1, the slot seen
  live. A bot's first spawn is left as it was. This is a reconstruction: retail's sender of the
  message is not known.

## Seen live against ORPHEUS

Original client, Practice Range, 2026-11-12, commit `a667ef2`'s change in place. Two Training Bots
17 to 19 m away were killed together by D.Va's Self-Destruct and watched with a capture every
0.3 s.

- **Death.** Both burst at the blast: a red flash, parts flying, then smoke. Their parts lie on
  the floor until the new bots come. The server log has both kills and, 3 s later, both respawns
  under new entity ids.
- **Rebuild.** 3.6 s after the death the parts on the floor are gone and a new bot is at each
  place, as a bunched cluster of parts a little above the floor. 0.3 s later it is tilted with
  its parts apart, 0.3 s after that low and wide with dust around it, and 0.9 s after it appeared
  it stands whole with its jets on. It does not appear whole.
- **Not resolved.** The first 0.3 s of the new bot fell between two captures, so parts flying in
  from far off were not seen, and at that distance the torso and arms cannot be told apart. The
  kill was by a blast, not by held fire.

So the rebuild also plays on a new entity, as it did on the revived body in the other service.

## Open

- A closer and faster look at the rebuild's first third of a second.
- Whether retail also rebuilds a bot on its first spawn.
- Whether a rebuilding bot can be hit.
