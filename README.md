# Azure Dreams Archipelago

An [Archipelago](https://archipelago.gg) randomizer world for the North
American PlayStation release of **Azure Dreams** (`SLUS-00614`), plus the
standalone Windows client that connects the running game to a multiworld room.

Ninety-eight checks: two locations on each tower floor from 1 to 39, ten
Equipment Shop slots, and ten Monster Shop slots. Eight progressive keycards
open the successive five-floor bands. Claiming the Ultimate Egg on floor 40
completes the goal.

Rewards arrive natively. The client hands each item to the game rather than
editing the save, so Koh plays his ordinary obtained-item animation and the
item lands in his inventory through the game's own code. A full inventory or
an unsafe moment simply defers the delivery until it is safe.

**Setup is a one-time chore. After that, playing a new seed is one
double-click.**

## Come say hello

Azure Dreams is one of those games people find on their own, love quietly for
years, and never quite get to talk to anybody about. There is a Discord for
exactly that:

### [discord.gg/GcNfZCB9TZ](https://discord.gg/GcNfZCB9TZ)

Randomizer or not, it is a room full of people who love this game. Share a
run, show off a familiar you got too attached to, argue about what to do with
the top floor, or hand the game to somebody who has never played it. New
players are genuinely welcome, and so is anybody who just wants to talk about
a twenty-five year old PlayStation roguelike with people who get it.

Screenshots and walkthroughs of the setup below get posted there too.

## Requirements

- The North American PlayStation release of Azure Dreams (`SLUS-00614`), as a
  raw BIN image. **Not included.** Supply your own copy.
- [DuckStation](https://www.duckstation.org/), with **Export Shared Memory**
  enabled.
- [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases).
- Windows, for the client. Nothing else to install: it is a single
  self-contained executable, so no Python, .NET or patching tools are needed.

## First-time setup

Do this once. Everything you point the client at is remembered.

Download the latest
[release](https://github.com/Septictank5/Azure-Dreams-Archipelago-Project/releases).
Each release is a **pair**: one `azure_dreams.apworld` and one
`AzureDreams.Archipelago.Client.exe`.

> [!IMPORTANT]
> Do not mix an apworld with a client from a different release. The two agree
> on a save-journal and mailbox layout that changes between versions, and a
> mismatched pair will not synchronize correctly.

1. **Install the apworld.** Double-click `azure_dreams.apworld`. The
   Archipelago Launcher opens and installs it into your `custom_worlds`
   folder. Restart Archipelago afterwards if it was already running.
2. **Unzip the client** somewhere sensible and keep its folder together. The
   `.dll` files beside the `.exe` are part of it. Do not put it in your
   Downloads folder, where you will lose it.
3. **Turn on DuckStation's shared memory.** Enable
   **Settings > Advanced > Show Debug Menu**, then
   **Settings > Debugging > Export Shared Memory**. The client reads the
   running game through this, and nothing works without it.
4. **Run the client once.** It asks whether to open `.adpatch` files with it.
   Say **Yes**. This is what turns every future seed into a double-click. It
   affects only your Windows account and only the `.adpatch` extension, and
   the client can undo it at any time.
5. **Point it at your two files.** Click **Original ROM...** and choose your
   clean, unmodified BIN. Click **DuckStation...** and choose your DuckStation
   executable. Both paths are saved, and you should never need to touch them
   again.
6. **Make a player options file.** Click **Create YAML**, fill in your slot
   name and options, and save it. For generating locally, put it in your
   Archipelago install's `Players` folder. For a room generated on the
   website, upload it there instead.

That is the whole chore. Step 5 is the one that makes the rest work, so do not
skip it: until the client knows both paths, a double-clicked patch can only
tell you what is missing.

## Playing a seed

Once setup is done, every future seed goes like this:

1. Generate the seed, or have the host generate it, and start the room.
2. Open the zip you get back and find your `.adpatch` file. Each Azure Dreams
   player gets their own, and it must be **yours**.
3. **Double-click it.** The client opens, builds your patched disc if it does
   not already exist, and starts DuckStation on it.
4. Enter the room's server, port, slot name and any password, then
   **Connect**. **Save Server** keeps the address filled in for next time.

That is it. No external patching tool, no separate base patch, no command
line. Your `.adpatch` already carries the common game changes together with
your slot's item names, recipient names, remote-item visuals and seed
identity.

Later launches of the same seed skip the build and start immediately, because
the patched disc is already there.

You can play disconnected. The game records pickups and purchases on its own,
and reconnecting submits what you collected while restoring what the server
already confirmed. If DuckStation closes or restarts, a connected client
reattaches on its own without leaving the room.

## Where the files go, and a word about disk space

The client writes the patched disc **next to the `.adpatch` file**, named after
it. A patch called `AP_12345_P1_Yourname.adpatch` produces
`AP_12345_P1_Yourname.bin` and a matching `.cue` in the same folder.

That BIN is a full copy of the disc, so it is **roughly 300 MB per seed**.

Two consequences worth planning for:

- **Give each seed its own folder.** Double-clicking a patch that is sitting
  loose on your Desktop will drop a 300 MB BIN and a CUE onto your Desktop.
- **Old seeds are safe to delete.** Once you are finished with a seed, its
  `.bin` and `.cue` can go. Keep the `.adpatch` if you want to rebuild it
  later, since it is only a few hundred kilobytes.

Your original BIN is never modified. It is verified against the known original
disc, copied, and the copy is patched. If the image does not match, the client
warns you and lets you decide.

## Building from source

The apworld is the `worlds/azure_dreams` package zipped up. The client needs
the .NET 8 SDK:

```bash
dotnet publish client/src/Adap.Client -c Release
```

This produces the same client the releases ship. `Directory.Build.props` at
the repository root selects the release channel, and its comment explains why
the project file on its own defaults the other way. `data.json` at the
repository root is linked into the build and holds the item names and icons,
so keep it where it is.

## Credits and legal

Azure Dreams is © Konami. This project ships no game code and no game assets.
The patch is a diff, and you supply your own disc image. It is an unaffiliated
fan project, not endorsed by Konami or by the Archipelago team.
