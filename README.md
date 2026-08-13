# Azure Dreams Archipelago

An [Archipelago](https://archipelago.gg) randomizer world for the North
American PlayStation release of **Azure Dreams** (`SLUS-00614`), plus the
standalone Windows client that connects the running game to a multiworld room.

Ninety-eight checks: two locations on each tower floor from 1 to 39, ten
Equipment Shop slots, and ten Monster Shop slots. Eight progressive keycards
open the successive five-floor bands. Arriving on floor 40
completes the goal.

**Setup is a one-time chore. After that, playing a new seed is one
double-click.**

## Come say hello

Azure Dreams is one of those games people find on their own, love quietly for
years, and never quite get to talk to anybody about. There is a Discord for
exactly that:

### [discord.gg/GcNfZCB9TZ](https://discord.gg/GcNfZCB9TZ)

Randomizer or not, it is a room full of people who love this game. New
players are genuinely welcome.

Screenshots and walkthroughs of the setup below get posted there too.

## Requirements

- The North American PlayStation release of Azure Dreams (`SLUS-00614`), as a
  raw BIN image. **Not included.** Supply your own copy.
- [DuckStation](https://www.duckstation.org/), with **Export Shared Memory**
  enabled.
- [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases), 0.6.7
  or newer. Older versions may not work.
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

1. **Install Archipelago.** Get the latest release from
   [ArchipelagoMW/Archipelago/releases](https://github.com/ArchipelagoMW/Archipelago/releases).
   At the time of writing that is **0.6.7**; older versions may not work, so
   install the newest one. Since this project's client is Windows-only, use the
   Windows installer, `Setup.Archipelago.0.6.7.exe` (matching whatever the
   current version number is), rather than the portable archive. The installer
   is also what registers the `.apworld` file type, which the next step relies
   on.
2. **Install the apworld.** Double-click `azure_dreams.apworld`. The
   Archipelago Launcher opens and installs it into your `custom_worlds`
   folder. Restart Archipelago afterwards if it was already running.
3. **Put the client somewhere sensible.** It is a single executable with
   nothing beside it, so anywhere works, but do not leave it in your Downloads
   folder where you will lose it.
4. **Turn on DuckStation's shared memory.** Enable
   **Settings > Advanced > Show Debug Menu**, then
   **Settings > Debugging > Export Shared Memory**. The client reads the
   running game through this, and nothing works without it.
5. **Run the client once.** It asks whether to open `.adpatch` files with it.
   Say **Yes**. This is what turns every future seed into a double-click. It
   affects only your Windows account and only the `.adpatch` extension, and
   the client can undo it at any time.
6. **Make a player options file.** Click **Create YAML**, fill in your slot
   name and options, and save it into your Archipelago install's `Players`
   folder. Remember the slot name you chose, because you type it again when
   you connect.
7. **Point it at your two files.** Click **Original ROM...** and choose your
   clean, unmodified BIN. Click **DuckStation...** and choose your DuckStation
   executable. Both paths are saved, and you should never need to touch them
   again.  Add your Slot name from the yaml creation to the slot field, then
   close the client.

That is the whole chore. Step 7 is the one that makes the rest work, so do not
skip it: until the client knows both paths, a double-clicked patch can only
tell you what is missing.

## Playing a seed

Azure Dreams is not part of official Archipelago yet, so **archipelago.gg
cannot generate it**. Generation happens on your own machine. The website can
still host the room once the seed exists.

### Generate the seed

1. Make sure `azure_dreams.apworld` is installed in your Archipelago install's
   `custom_worlds` folder. Double-clicking the apworld does this for you.
2. Put every player's YAML into the `Players` folder. That means everyone in
   the multiworld, not just the Azure Dreams players.
3. Run `ArchipelagoGenerate.exe`.
4. Look in the `output` folder for the zip it produced, and extract it. Inside
   are the `.archipelago` server file and one `.adpatch` file for each Azure
   Dreams player.

### Host the room

1. Go to [archipelago.gg](https://archipelago.gg), then
   **Start Playing > Host a pre-generated game**.
2. Choose the `.archipelago` file you just extracted.
3. Click **Create Room**. The room page shows the port your players connect
   on.

### Connect

1. **Double-click your own `.adpatch` file.** Each Azure Dreams player has
   their own and they are not interchangeable. The client opens with it
   loaded and starts DuckStation on your patched disc.
2. In the client, set the **Server** dropdown to `archipelago.gg`.
3. Enter the **Port** from the room page.
4. Enter your **Slot** if you haven't already, spelled exactly as in your YAML.
5. Enter the room's password if it has one, then click **Connect**.

> [!NOTE]
> Double-clicking the patch starts DuckStation right away, so the game will be
> booting while you fill in the room details. If you would rather not be
> rushed, open the client on its own instead. On first run of a patch you will
> need to browse for the patch before clicking **Launch Game**.

The first time you use a given patch, the client builds the disc before
launching, which takes a moment. Every later launch of that seed starts
immediately.

None of this is as short as it looks written down, and the first run through
is genuinely fiddly if Archipelago is new to you. That is normal, it is not
you, and the Discord above is a reasonable place to get unstuck.

You can play disconnected. The game records pickups and purchases on its own,
and reconnecting submits what you collected while restoring what the server
already confirmed. If DuckStation ever closes, use the clients **Launch game**
to bring it back, and it will reattach on its own without leaving the server.

## Where the files go, and a word about disk space

The client writes the patched disc **next to the `.adpatch` file**, named after
it. A patch called `AP_12345_P1_Yourname.adpatch` produces
`AP_12345_P1_Yourname.bin` and a matching `.cue` in the same folder.

That BIN is a full copy of the disc, so it is **roughly 290 MB per seed**.

Two consequences worth planning for:

- **Give each seed its own folder.** Double-clicking a patch that is sitting
  loose on your Desktop will drop a 300 MB BIN and a CUE onto your Desktop.
- **Old seeds are safe to delete.** Once you are finished with a seed, all files
  created can be deleted.

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

The code in this repository - the apworld and the client - is released under
the [MIT License](LICENSE). Use it, change it, fork it, build on it,
commercially or not. Keep the copyright notice with it, and understand it
comes with no warranty of any kind.

That license covers this project's own code and nothing else. It grants no
rights to Konami's game data: the original disc, its assets and its text
remain (c) Konami, are not distributed here, and are not mine to license.
