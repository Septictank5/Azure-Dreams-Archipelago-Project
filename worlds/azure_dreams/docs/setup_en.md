# Azure Dreams Multiworld Setup Guide

## Required software

- The North American PlayStation release of Azure Dreams (`SLUS-00614`).
- DuckStation with **Export Shared Memory** enabled.
- The ADAP disc patch and standalone Azure Dreams Archipelago client.

## Generate and patch

This APWorld can generate and host seeds with 98 checks: two tower locations on
each floor from 1 through 39, ten Equipment Shop slots, and ten Monster Shop
slots. Eight progressive keycards unlock the successive five-floor bands;
Monster Shop stock requires keycard level 3. Acquiring the Ultimate Egg on
floor 40 completes the goal.

Generate normally with an Azure Dreams player YAML. The output zip contains a
`.archipelago` server file and one `.adpatch` file for each Azure Dreams player.
Open only that player's `.adpatch` in the client, together with a clean North
American raw BIN image; the client builds the patched BIN and a matching CUE
beside it, leaving the original image untouched. The patch already combines the
common game changes with the generated player's item names, recipient names,
universal gift-marker visuals, and seed identity.

## Connect

1. Enable DuckStation's **Export Shared Memory** option.
2. Start or join the Archipelago room using the generated `.archipelago` file.
3. In the client, choose the `.adpatch` file, the original BIN and your
   DuckStation executable, then click **Launch Game**. The first launch builds
   the patched disc; later launches reuse it and start immediately.
4. Enter the room's server, port, slot name and any password, then click
   **Connect**.

The client can log in while Koh is in town. A brand-new patched save initializes
its signed seed journal when Koh first enters the tower or opens a patched Buy
menu; later sessions can verify and synchronize from town. The game records
location pickups and shop purchases even with no client connected. Reconnecting
submits saved checks and restores server-confirmed checks. Progressive keycard
receives and completed shop slots persist in the game save.

The remaining 90 rewards are native inventory items drawn from one flat pool -
no item is ranked above another. Every reward rolls its own odds rather than
filling a quota, so the mix genuinely varies from seed to seed; two eggs and two
fire balls are guaranteed and nothing else is. The Monster Shop is the
exception: its ten slots are weighted toward eggs and items your familiar wants.
The client queues these rewards to the game rather
than editing inventory directly. While Koh is under normal tower control, the
game performs its standard obtained-item dialogue, held-item animation, and
inventory insertion. If Koh is in town, in an unsafe action, or has a full
twenty-slot inventory, the reward remains pending until native delivery is
safe. Holding Circle does not suppress an Archipelago receive.
