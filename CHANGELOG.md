# Changelog

Every released version of the Azure Dreams Archipelago apworld and client,
newest first. **A release is a pair** — one apworld and one client — and the
version number labels the pair, not either half.

## How this file is kept

Write into **Next release** as the work lands, in the player's words rather
than the code's. Do not wait for release day and reconstruct it from memory;
that is how a release ships with notes that are half true.

`tools/Promote-AdapStable.py` refuses to promote when Next release is empty,
and on success renames it to the version being promoted, dates it, opens a
fresh empty Next release above it, and copies the section into
`releases/v<version>/RELEASE-NOTES.md` for the GitHub release body. So the
only manual step is writing the notes; the bookkeeping is not yours to do.

## What the three numbers mean

`major.minor.patch`, decided by us:

| Number | Bumped for |
| --- | --- |
| **major** | A hard stable milestone: significant feature upgrades, or a change that resets what the project is. |
| **minor** | Features. Anything a player would notice as new. |
| **patch** | Bug fixes, performance, wording, and anything invisible. |

This is the shape [Semantic Versioning](https://semver.org) defines, in the
form applications conventionally use it. Formal SemVer answers one question —
*did the public API break* — which for a library is the only question that
matters. This project's equivalent of that API is **not** the release number:
it is the slot-data gate (`apworld_version` in `world.py`,
`SupportedSlotDataVersion` in the client, currently 19), and the client refuses
a mismatched room outright. Compatibility is therefore already enforced by a
number of its own, which leaves the release label free to mean what a player
would expect it to mean.

One rule the split does impose: **a gate bump invalidates seeds already in
progress**, so it is never just a patch bump, and the entry says so on its
`Compatibility` line. Anything a seed cannot notice — client-only work, docs,
generation changes that leave ids and slot data alone — leaves the gate where
it is. `docs/RELEASING.md` owns the full version rules.

---

## Next release

_Nothing yet._

---

## 2.0.0 — 2026-08-18

Compatibility: **slot-data gate 16 → 19.** Regenerate every room and start new
saves; the client and the apworld must be updated together (the client refuses
an older room, and an older client cannot find the new save record).

### Gameplay

- **A third check on every tower floor, carried by a monster.** Each floor
  1–39 now has three Archipelago locations instead of two: the two markers on
  the ground as before, and one held by a level-1 monster that spawns on every
  floor, ignores you, heads for a room exit, and drops the marker when killed.
  Kill it, pick up the `Strange...` gift, and the check sends like any other.
  Once a floor's third check is banked the monster still spawns, but with
  nothing in its hands: it behaves like any other level-1 monster of its kind
  (so it may fight you) and drops nothing - a floor where it simply vanished
  would read as a bug, and a monster carrying a decoy turned out to be worse
  (a Picket carrier gave its decoy back on death, and throwing that item
  crashed the game). 117 tower checks in total, 39 more items in the pool.
- The carrier is drawn from the 22 wild species whose death actually drops
  what they hold. Eleven species (Unicorn, Flame, Arachne, Baloon, Kraken, Zu,
  Mandara and four that never roam wild) die without ever dropping a carried
  item - that is the game's own code, per species - and Golem has a private
  reflex that overrides the flee behaviour, so none of those can be a carrier.
- The carrier always spawns awake (the game rolls a coin-flip sleep on every
  spawn; the carrier's is cleared and it is sleep-proof against thrown sleep),
  and wears one of its species' own alternate colours, chosen per seed. A wind
  seed can revert the colour; nothing re-applies it, on purpose - the wrong
  monster on the floor is the tell, the colour is the flourish.
- **Killing the carrier no longer haunts the floor.** After the carrier died,
  the next monster to spawn on that floor could inherit the carrier's flee
  brain - and, holding nothing, half of them would instead use their species
  ability on you point-blank (a Baloon casting Fly at you, a Noise sealing
  you). The game hands a dead monster's memory to the next one spawned, and
  the carrier test was "are you at that address". It is now "are you holding
  the marker", which is true of the carrier and of nothing else.
- **A Cyclone, Volcano, Nyuel or Pulunpa carrier no longer turns and hits
  you.** Those four species (and Golem) shipped with a slightly different copy
  of the "take a step" routine than the other 39: when it already had a step
  queued and had noticed you, it re-aimed at you and threw away the direction
  its brain had just chosen - which for a fleeing carrier meant chasing you,
  or standing still and swinging when you were next to it. The disc now
  carries the other 39 species' version of that routine for all five, so
  their carriers run like everyone else's. Ordinary Cyclones and the rest
  still fight as before - they were aiming at you either way.
- Troll is no longer drawn as a carrier: it spawns with its hammer already in
  the hand the marker would go in, so a "Troll carrier" was just a Troll and
  the floor's third check never appeared.
- The floor's monster roster shifts to make room: each floor trades one of its
  native types for the carrier, chosen per seed, and the survivors take the
  vacated slots. Barong's slot-machine appearances and the water medal's Picket
  are never touched.
- **The monster-carried check is a YAML option** (`carrier_system`, on by
  default). Off: two checks a tower floor instead of three, 39 fewer items, no
  carrier ever spawns, and every floor keeps the monster roster the retail game
  gave it. Everyone in a room can choose separately - one player's tower can
  have carriers and another's not, in the same seed. The tracker keeps its
  three columns either way so nothing jumps around when you connect; a room
  without carriers just leaves the third one empty.
- The save-backed multiworld record (`ADSV`) is version 4: the tower journal is
  now one byte per floor, it moved down in RAM to make room, and version-3
  saves re-initialize on first boot. Everything the record already tracked
  (received items, keycards, gold, shop purchases, the intro handshake) is
  carried over in the new layout.
- **The tower menu no longer crashes on Line up, Fuse or Command when the stat
  panel is showing a familiar.** Adding the Send row moved the menu's row-handler
  table, and one of the two places the game rebuilds the pointer to that table
  was missed - the copy that only runs when the panel is on a familiar rather
  than on Koh. Picking any of those three rows in that state jumped into the
  middle of unrelated code. Both copies now point at the moved table. This is
  the fuse crash that could never be reproduced on demand: backing out and
  re-entering the menu put the panel back on Koh, which is why it looked random.

### Town

- **A blacksmith works in the Equipment Shop.** He tempers a sword, a shield or
  the Trained Wand in place, for gold, one level at a time - the item in your
  bag is the item that improves, keeping its name and its enchantment. The
  price is quadratic: 1 gold for the first level, 31 for the tenth, 125 for the
  twentieth, 500 for the last one. How far he will go is capped, and the cap is
  what the multiworld sells you - until the first Red Sand arrives it is +0 and
  he has nothing to offer but conversation.
- **A ball charger works beside the fortune teller.** She tops a spell ball
  back up, one charge at a time, 500 gold for the first charge on a ball and
  3000 for its tenth. What the White Sands buy is **how many charges she will
  hand out per visit to town** - one, then two, then three - and they go
  wherever you want them: three into one ball, or one into each of three. Any
  ball can reach ten charges at any level. Come back from a climb and she has
  another round in her; walk out of her building and back in and she does not.
- **Buying a temper or a charge no longer highlights the row as if you had
  selected it.** Both counters apply on the spot when you press X, so the green
  highlight and the BUY sticker the shop menus put on a checked row were saying
  something that was not true - and they turned up exactly when an item reached
  its ceiling, which is the moment you least want a row shouting at you. The
  row that is finished now goes grey, like any other row you cannot buy.
- **Three Red, three Blue and three White Sands are in the item pool.** Red is
  the smith's weapon ceiling (+10, +20, +40), Blue his shield ceiling, and
  White is how many ball charges the charger hands out per town visit (1, 2,
  3). The sands
  never enter your bag - they are levels, not items, and the client shows all
  three in the tracker. The tower floors stop dropping sands of their own,
  because a floor drop cannot raise a cap and finding one that did nothing
  would read as a bug.
- **The fortune teller reads the tower.** For 1000 gold, Mademoiselle Shiela
  looks at one of the three lowest floors that still holds an un-collected
  check and describes what KIND of thing is waiting there, in her own terms -
  a leaf, a sphere within her sphere, a thin cold card, something not of this
  world - and whether it lies on the floor or is being carried by a monster.
  She names nothing outright; that is the point of asking a fortune teller.
- **A status strip along the top of the screen in town** shows
  `WEAPON:+20  SHIELD:+40  BALL:+3` - not the number of sands, but what they
  currently buy: how far the smith will temper a weapon and a shield, and how
  many ball charges you get this visit. In the game's own banner frame. It
  steps out of the way while any menu or conversation is open and comes back
  when the last one closes. (The tracker still counts the sands themselves.)
- **Three new YAML options**, all on by default: `hint_system` (the fortune
  teller), `temper_system` (the smith, the charger and the sands) and
  `carrier_system` (the monster-carried third check). Turning one off removes
  its NPCs and its items entirely - with `temper_system` off the floors drop
  sands as they do in vanilla. The client's **Create YAML** dialog offers all
  three, with progression balancing moved to a first row of its own.
- **The pool house is open, as an easter egg.** Wotta's quest is pre-cleared,
  so the pool is open from your first visit with no Water Medal hunt, and all
  seven girls - Nico, Fur, Selfi, Cherrl, Vivian, Mia and Patty - are in there
  at once instead of one at a time, lined up by the changing rooms saying where
  they are in the row. There is nothing to collect there yet. It is groundwork
  the room is welcome to look at in the meantime: the intended shape is the
  Water Medal going into the multiworld pool and the door staying locked, with
  Wotta outside asking for it, and the girls becoming checks of their own.

### Client

- **You can stop mid-tower and pick it up later.** `Continue` on the title
  screen puts you back at the start of the floor you were on, with the level,
  the HP, the inventory and the familiar you rode the elevator up with - the
  game's own memory-card load path, pointed at a snapshot the client takes on
  every floor arrival. Dying lands in the same place, so quitting a fight you
  are losing is not a way out of it. One snapshot per seed, last arrival wins.
- **Quitting mid-tower and reloading no longer respawns the checks you already
  collected on that floor.** The floor's checkpoint is taken on arrival, and a
  tower resume rebuilt the floor before the client had re-marked the server's
  checks into it. The server's checked set is now merged into the restored save
  block before it lands.
- **Taking a shortcut to floor 10, 20 or 30 now checkpoints the levels it gave
  you.** The floor's checkpoint was being taken while the floor was still
  building, before the shortcut had handed Koh the levels the climb would have
  earned - so quitting there and resuming came back underlevelled. The
  checkpoint now waits for the grant, and stores Koh exactly as he stands
  rather than as the game last wrote him down.
- **Sending an item no longer lets you keep it.** Send something to another
  player, quit, and resume the checkpoint, and the item used to come back to
  your bag with its Send Token refunded - while the other player still had
  their copy. A send is now recorded against the saved checkpoint the moment it
  happens: the token is spent there too, and the item is taken out of it if it
  was in it. Anything you found on the floor *after* the checkpoint was never
  in it, so only the token comes off - your inventory for that floor is
  otherwise left exactly as it was, so a resume does not cost you the items you
  were part-way through beating the floor with.
- **Items gifted to you are re-delivered after a resume.** If another player
  sent you something after your floor's checkpoint, resuming that floor rolled
  the item out of your bag while the server still counted it delivered, and it
  was gone. Gift delivery is now recorded alongside the checkpoint and rewinds
  with it, so anything that arrived after it arrives again.
- The tower panel reserves a column for the bell and shortcut crystals, so
  floors 21-40's markers are no longer cut off by the window edge now that
  each floor shows three chests.
- **The window drags smoothly during a live session.** The loop that reads the
  running game ran on the same thread that draws the window, so every poll —
  ten a second, sixty during the intro — stalled the interface for as long as
  it took to read RAM, write the mailbox and commit a checkpoint. It now runs
  off that thread entirely. The 500 ms "is the game attached?" probe, which
  walked every process on the machine, moved off it too.
- **Double-clicking a patch no longer starts the game.** It opens the client
  with the patch loaded and the disc built, and stops there: `Launch Game`
  starts DuckStation when you are ready. This means you can prepare a seed
  before the room exists — while the host is still setting it up — and connect
  to the room first, which is the order the two connections actually want. The
  button itself is unchanged: it still patches if needed, then launches.
- Building a disc from a double-clicked patch no longer requires the
  DuckStation path to be filled in, since nothing is being launched. Only the
  patch and your original BIN are needed.
- The game-link status no longer reads `Game live.` before a game has been
  started. Connecting to the room before launching used to claim a live game
  the moment the room came up.
- The activity feed is capped at 2000 lines. It carries every send in the
  room, and in a long session in a full multiworld the scrollback itself
  became something the client paid for on every new line.
- **The tracker is its own window.** The client's first screen is now just the
  setup screen - server, slot, patch, ROM, emulator - and a **Tracker** button
  beside **Create YAML** pops out everything you actually watch while playing:
  the tower, the shops, the incoming queue, the counters and the activity feed,
  in the layout the old Compact mode used. Both windows stay open together, so
  reconnecting or relaunching no longer means switching away from the tracker,
  and the tracker can live on a second monitor at whatever size you drag it to.
  The old **Compact mode** / **Full mode** switch is gone with it, as is the
  separate Queue list: the tracker's incoming queue already carries the names.
- **The tracker's icons start in greyscale and gain their colour when the game
  is connected.** Every colour in it means something - a red crystal is a
  shortcut your keycards have not opened, a red shop is a shop that is shut -
  and until the client has the room, the game and your progression, all of it is
  a guess wearing the same paint. Colour returns the moment the game reports
  where you actually are, and drains again if the game link drops. The panels
  themselves keep their normal colours throughout; it is the chests, crystals,
  medal and locks inside them that wait. The incoming queue never greys: an item
  in it is something the server already told us.
- **The tracker stays up when you minimise the client.** The setup screen is
  noise once a session is running, and minimising it used to take the tracker
  with it. The tracker now has its own taskbar button, and it flashes there if
  the link to the game drops or the emulator exits - which you would otherwise
  only find out by alt-tabbing.
- **The tracker shows your three sands.** Red, blue and white pouches sit beside
  the keycard and send-token counts with the level each has reached, and hovering
  one says what that level buys - how far the smith will temper a weapon or a
  shield, and how full the charger fills a spell ball. The room and game status
  lines left the tracker at the same time; they were always the connection
  screen's to report, and the sands needed the room.
- **`Game patched. Connect, then Launch Game.` survives connecting to the
  room.** Double-clicking a patch printed that line and then connecting reset it
  to a grey `Game not running.` seconds later, while it was still perfectly
  true.
- **A trap springs even when you are holding something.** Traps waited until
  whatever you had in hand went back in the bag - so picking one up with an
  item held, which is exactly what you do when your bag is full, deferred it
  to some later moment. Incoming items still wait while you hold something;
  that part is on purpose and is unchanged.
- **A trap now springs where you picked it up, or not at all.** A trap from an
  earlier pickup could be handed to you on the first frame of a floor that had
  just loaded - by elevator or from a memory card save - and that frame is your
  turn, so the trap spent it and the whole floor moved before you did. One run
  died that way. Traps are now only sprung for a pickup the client actually
  watched happen while you were standing on that floor: checks it finds already
  in your save when it attaches (anything collected offline) are reported to the
  room without springing, and a trap that has not gone off by the time you leave
  the floor is discarded rather than carried.

### Release tooling

- This file. Promotion now refuses to build a release whose notes were never
  written, and stamps the section with the version and date itself.

---

## 1.0.1 — 2026-08-13

Compatibility: slot-data gate unchanged at 16. **Every 1.0.0 player needs
this.** Seeds and saves are unaffected.

### Generation

- **Fixed: nobody could generate a seed.** The apworld declared a minimum
  Archipelago version of 0.6.8, which had not been released — 0.6.7 was, and
  still is, what players have. A core that fails that check skips the world
  entirely and then blames the player's YAML with `No functional world found
  to handle game Azure Dreams. Did you mean 'Azure Dreams' (101% sure)?`,
  which points nowhere near the real cause. The floor is now 0.6.7.

### Release tooling

- Promotion now refuses to build when the declared minimum Archipelago version
  is newer than the newest public release tag, before anything else runs. The
  number came from the dev checkout's own version, which main bumps to the
  *next* version the moment a release branches — so the mistake was invisible
  in every local test and broken for everyone else. It cannot be made twice.

---

## 1.0.0 — 2026-08-11

**First public release.** Azure Dreams (`SLUS-00614`, North American
PlayStation) as an Archipelago world, plus the Windows client that connects the
running game to a multiworld room.

Compatibility: slot-data gate 16.

### The multiworld

- **98 locations**: two on every tower floor from 1 to 39, ten in the Equipment
  Shop, and ten in the Monster Shop.
- **Eight Progressive Keycards** open the successive five-floor bands of the
  tower, in any order they arrive.
- **The goal is floor 40.**
- **A 90-item reward pool drawn from the game's own items**, with quality,
  unidentified and cursed states carried through the protocol id, so an item
  arrives as the thing it actually is. Five 5000 Gold packages are guaranteed,
  and are kept off the shop shelves.
- **Shop prices follow Archipelago classification**, so a shelf reads as
  something before you have a server telling you what is on it.
- **Send Tokens**: a tower send row lets you hand an item to another player
  from inside the tower, at the cost of a token.
- **Trap items** plant a real native trap under Koh.
- **Items arrive natively.** A remote item comes in through the game's own
  held-item presentation or the town's `Received <item>.` dialogue; items
  returning from your own tower or shop locations are inserted silently rather
  than played back a second time.
- Placement text is composed by the game itself, per floor, so a pickup names
  the item and the player it belongs to on the floor you found it on.

### The game

- Tower floor markers spawn, are picked up without passing through your bag,
  and are recorded in the save, so a check survives death, re-entry and a
  disconnected session.
- The elevator is gated by keycard level, refuses an ascent past your clearance
  with a message rather than a silent nothing, and no longer interrupts with
  `QUIT?`.
- Save *creation* is removed; the client owns durable state instead (see
  below). The intro and the angel's welcome-back are wired into that.
- The Monster Shop door is locked below Keycard 3.
- The floor spawn pool is rebalanced, un-gating items vanilla could never drop.
- An in-game HUD panel shows keycard level and deepest floor.
- Bonus floors, reached by a Go-Up trap, drop you into a pre-baked room with
  its own loot and no multiworld checks in it.

### The client

- One Windows executable with nothing to install beside it: no Python, no .NET,
  no patching tools.
- **Patching is built in.** Pick your untouched BIN once; each seed's
  `.adpatch` is verified against the original disc's fingerprint, copied, and
  patched into a playable BIN and CUE beside the patch. Your original is never
  modified. `.adpatch` files can be associated with the client, per user and
  reversibly, so a seed is a double-click.
- **Live session panels**: the tower with your position and every check on it,
  both shops, the incoming delivery queue, keycards and send tokens, and an
  activity feed of every send in the room. Compact mode keeps the panels a
  live session is watched for and drops the controls used once.
- **Create YAML** writes a player options file without leaving the client.
- **Durable town checkpoints.** The client commits the save-backed block per
  seed and restores it at the first stable town frame, which is what makes a
  saveless game survive a crash, a reset or a closed emulator.
- **Reconnects on its own.** If DuckStation closes or restarts, the client
  releases the old mapping, waits for the new process and reconciles without
  leaving the room. You can play disconnected: pickups and purchases are
  recorded by the game and submitted when you come back.
