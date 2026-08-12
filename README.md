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

## Requirements

- The North American PlayStation release of Azure Dreams (`SLUS-00614`), as a
  raw BIN image. **Not included.** Supply your own copy.
- [DuckStation](https://www.duckstation.org/), with **Export Shared Memory**
  enabled.
- Windows, for the client. Nothing else to install: it is a single
  self-contained executable, so no Python, .NET or patching tools are needed.

## Install

Download the latest [release](https://github.com/Septictank5/Azure-Dreams-Archipelago-Project/releases). Each release is a **pair**: one
`azure_dreams.apworld` and one `AzureDreams.Archipelago.Client.exe`.

> [!IMPORTANT]
> Do not mix an apworld with a client from a different release. The two agree
> on a save-journal and mailbox layout that changes between versions, and a
> mismatched pair will not synchronize correctly.

1. Install the apworld: double-click `azure_dreams.apworld` to let the
   Archipelago Launcher install it, or copy it into your Archipelago install's
   `custom_worlds` folder.
2. Unzip the client anywhere and keep its folder together. The `.dll` files
   beside the `.exe` are part of it.
3. In DuckStation, enable **Settings > Advanced > Show Debug Menu**, then
   **Settings > Debugging > Export Shared Memory**.

## Play

Generating a seed produces a zip holding the `.archipelago` server file and one
`.adpatch` file for each Azure Dreams player. That `.adpatch` is your patch: it
already carries the common game changes together with your slot's item names,
recipient names, remote-item visuals and seed identity. There is no separate
base patch to apply first, and no external patching tool involved.

If you need a player YAML, the client's **Create YAML** button will write one.

In the client:

1. **Patch**: choose your `.adpatch` file.
2. **Original ROM**: choose your clean, unmodified BIN. It is verified against
   the known original and is never modified.
3. **Emulator**: choose your DuckStation executable.
4. Click **Launch Game**. The first launch builds the patched BIN and a
   matching CUE beside your `.adpatch`, then boots DuckStation on it. Later
   launches reuse that disc and start immediately.
5. Enter the room's server, port, slot name and any password, then
   **Connect**.

You can play disconnected. The game records pickups and purchases on its own,
and reconnecting submits what you collected while restoring what the server
already confirmed. If DuckStation closes or restarts, a connected client
reattaches on its own without leaving the room.

A fuller walkthrough lives in
[the setup guide](https://github.com/Septictank5/Azure-Dreams-Archipelago-Project/blob/main/worlds/azure_dreams/docs/setup_en.md).

## Building from source

The apworld is the `worlds/azure_dreams` package zipped up. The client needs
the .NET 8 SDK:

```bash
dotnet publish client/src/Adap.Client -c Release
```

That produces the same client the releases ship, handling `.adpatch` files.
`Directory.Build.props` at the repository root is what selects that channel;
see the comment inside it for why the project file itself defaults the other
way. `data.json` at the repository root is linked into the build and holds the
item names and icons, so keep it where it is.

## Credits and legal

Azure Dreams is © Konami. This project ships no game code and no game assets.
The patch is a diff, and you supply your own disc image. It is an unaffiliated
fan project, not endorsed by Konami or by the Archipelago team.
