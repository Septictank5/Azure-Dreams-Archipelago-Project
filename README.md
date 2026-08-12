# Azure Dreams — Archipelago

An [Archipelago](https://archipelago.gg) randomizer world for the North
American PlayStation release of **Azure Dreams** (`SLUS-00614`), plus the
standalone Windows client that connects the running game to a multiworld room.

Ninety-eight checks: two locations on each tower floor from 1 to 39, ten
Equipment Shop slots, and ten Monster Shop slots. Eight progressive keycards
open the successive five-floor bands. Claiming the Ultimate Egg on floor 40
completes the goal.

Rewards arrive natively. The client hands each item to the game rather than
editing the save: Koh plays his ordinary obtained-item animation, the item
lands in his inventory through the game's own code, and a full inventory or an
unsafe moment simply defers the delivery until it is safe.

## Requirements

- The North American PlayStation release of Azure Dreams (`SLUS-00614`), as a
  raw BIN image. **Not included** — supply your own copy.
- [DuckStation](https://www.duckstation.org/), with **Export Shared Memory**
  enabled.
- Windows, for the client. No Python, .NET or PPF tooling needed — the client
  is a single self-contained executable.

## Install

Download the latest [release](../../releases). Each release is a **pair**: one
`azure_dreams.apworld` and one `AzureDreams.Archipelago.Client.exe`.

> [!IMPORTANT]
> Do not mix an apworld with a client from a different release. The two agree
> on a save-journal and mailbox layout that changes between versions, and a
> mismatched pair will not synchronize correctly.

1. Drop `azure_dreams.apworld` into your Archipelago install's `custom_worlds`
   folder.
2. Unzip the client anywhere and keep its folder together — the `.dll` files
   beside the `.exe` are part of it.
3. In DuckStation, enable **Settings → Advanced → Show Debug Menu**, then
   **Settings → Debugging → Export Shared Memory**.

## Play

1. Generate a seed with an Azure Dreams player YAML. The client's
   **Create YAML** button will make you one.
2. The generated zip contains one `.ppf` per Azure Dreams player. In the
   client, choose that PPF with **Patch File...**, choose your untouched
   BIN with **Original ROM...**, and click **Patch ROM**. A patched BIN and
   matching CUE are written beside the PPF; your original file is not
   modified.
3. Boot the new CUE in DuckStation.
4. Enter the room's server, port, slot name and any password in the client,
   then connect.

The player PPF already contains everything — the common game changes and your
slot's item names, recipients, remote-item visuals and seed identity. There is
no separate base patch to apply first.

You can play disconnected. The game records pickups and purchases on its own;
reconnecting submits what you collected and restores what the server already
confirmed.

A fuller walkthrough lives in
[the setup guide](worlds/azure_dreams/docs/setup_en.md).

## Building from source

The apworld is the `worlds/azure_dreams` package zipped up. The client needs
the .NET 8 SDK:

```bash
dotnet publish client/src/Adap.Client -c Release
```

An ordinary build produces the **dev-channel** client, which handles
`.adpatch-dev` files and badges its window; the released client is built from
the same source with `-p:AdapChannel=stable`. `data.json` at the repository
root is linked into the build and holds the item names and icons, so keep it
where it is.

## Credits and legal

Azure Dreams is © Konami. This project ships no game code or game assets — the
patch is a diff, and you supply your own disc image. It is an unaffiliated fan
project and is not endorsed by Konami or by the Archipelago team.
