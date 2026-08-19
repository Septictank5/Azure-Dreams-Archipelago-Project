using System.Buffers.Binary;
using Adap.Client.Emulators;

namespace Adap.Client.Games;

internal readonly record struct AzureDreamsSeedIdentity(byte[] Signature);

internal readonly record struct AzureDreamsItemDescriptor(
    byte ItemId,
    byte Category,
    sbyte Quality = 0,
    byte Flags = 0)
{
    public byte[] ToBytes() => [ItemId, Category, unchecked((byte)Quality), Flags];
}

/// <summary>
/// Durable Archipelago state embedded in Azure Dreams' ordinary save block.
/// The patched game initializes and saves this structure; the client only
/// reconciles it with the server and applies received rewards.
/// </summary>
internal static class AzureDreamsReceiveState
{
    // Stage-1 carve retraction: the seed suite lives outside the effect
    // pool now, and the memory-top marker rose to 0x001FE600. Must track
    // patch.py's SEED_BLOCK_ADDRESS.
    // Moved 2026-08-02: 0x801C8E40 was inside the floor arena's reach and a
    // high-30s floor load overwrote the whole page, which carries resident
    // code. That was the tower loading crash. See
    // docs/adap-memory-safe-regions.md.
    public const uint SeedBlockAddress = 0x801d_7f00;
    public const uint SeedMagic = 0x4453_4441; // "ADSD"
    // 3: placement text is pooled. Each distinct item and player name is stored
    // once and a placement is three bytes of references, which is what got a
    // three-player room under the seed page's message budget. The client only
    // reads this header, but the version gate above rejects a mismatch, so it
    // must track patch.py's SEED_VERSION.
    public const ushort SeedVersion = 3;

    // ADSV v4 (2026-08-15, the third floor check). Must match patch.py's
    // PERSISTENT_* constants; docs/systems/third-floor-check.md §2 owns the
    // table. The record's END is fixed at 0x8001_5FEC (the shortcut carrier
    // and the send-token pair sit above it), so growth moves the BASE down.
    //
    //   +0x00 magic           +0x04 version:size (u16 lo / u16 hi)
    //   +0x08 seed signature  +0x10 tower journal, 40 B: byte = floor-1, bit = slot
    //   +0x38 town journal, 16 B packed (the shop's 20 checks are its first word)
    //   +0x48 received count  +0x4C keycard level    +0x50 gold granted
    //   +0x54 intro-restore marker (byte)  +0x55 first-run ready (byte)
    //   +0x56 weapon temper level (byte)   +0x57 shield temper level (byte)
    //
    // 3 (2026-08-05): the gold-granted counter. Eager gold granting needs a
    // durable count of packages already banked - gold is cumulative and cannot
    // be re-derived from the history the way a keycard level can.
    // 4: the tower journal is ONE BYTE PER FLOOR, not a packed bit array. Every
    // reader that maps a location index to a mask position goes through
    // TryGetTowerMaskPosition; nothing computes `index >> 3` any more.
    public const uint PersistentStateAddress = 0x8001_5f94;
    public const uint PersistentStateMagic = 0x5653_4441; // "ADSV"
    public const ushort PersistentStateVersion = 4;
    public const ushort PersistentStateSize = 0x58;
    public const int PersistentLocationMaskOffset = 0x10;
    public const int TowerJournalSize = 40;              // 39 floors + one spare byte
    public const int PersistentShopMaskOffset = PersistentLocationMaskOffset + TowerJournalSize; // 0x38
    public const int TownJournalSize = 16;
    public const int ReceivedItemCountOffset = 0x48;
    public const int KeycardLevelOffset = 0x4c;
    public const int GoldGrantedCountOffset = 0x50;
    public const int IntroRestoreMarkerOffset = 0x54;
    public const int FirstRunReadyMarkerOffset = 0x55;
    // The blacksmith's two temper levels (docs/systems/blacksmith.md): how far
    // the equipment-shop smith may temper a weapon (swords, the Trained Wand)
    // and a shield - 0..3 -> +0/+10/+20/+40. Set by the client from the
    // received-item history like the keycard level: one Red Sand = one weapon
    // level, one Blue Sand = one shield level; the sands never enter the bag.
    public const int WeaponTemperLevelOffset = 0x56;
    public const int ShieldTemperLevelOffset = 0x57;
    // The ball charger's level (docs/systems/fortune-teller.md section 5): how
    // many charges the fortune teller's neighbour may fill a spell ball up to
    // - 0..3 -> 0/1/2/3 charges per town visit (the ceiling on one ball is ten
    // at every level). One White Sand = one level, set from the history
    // like the two temper levels. NOT inside ADSV (the record is full and
    // growing it re-initializes every save): the word just below the base, in
    // the free durable run, zeroed by both ADSV initializers. Byte 0 is the
    // level. Must match patch.BALL_CHARGE_LEVEL_ADDRESS.
    public const uint BallChargeLevelAddress = PersistentStateAddress - 4;

    // Send tokens. One tower send costs one; the game spends them, so the
    // count is a live game counter, not a client mirror.
    //
    // Deliberately NOT inside ADSV: growing the record is what burned
    // 0.9.84 (a new field landed on the shortcut carrier that a floor-ascent
    // helper zero-writes), and it would re-initialize every existing save.
    // These two live past the record in the same reserved save tail, which
    // a dataflow scan proved the game never writes.
    //
    // Both are inside the checkpoint region (0x80010000..0x80016000), which
    // is the point: a death rollback reverts the count, the bank and the
    // receive cursor as one, so a rolled-back token is re-granted rather
    // than lost or doubled. Must match patch.py.
    public const uint SendTokenCountAddress = 0x8001_5ff0;
    public const uint SendTokenMagicAddress = SendTokenCountAddress + 4;
    public const uint SendTokenMagic = 0x5453_4441; // "ADST"
    /// <summary>
    /// What a fresh run is given, so sending is never wholly gated behind
    /// the multiworld. Granted by the seed initializer, and by the tower
    /// gate itself on first touch if the tower is reached first - which is
    /// why the magic exists: "never initialized" and "spent them all" are
    /// otherwise the same zero.
    /// </summary>
    public const uint SendTokenStartingCount = 1;
    /// <summary>
    /// How many <c>Send Token</c> items this save has banked from the
    /// multiworld - the gold-granted counter's twin, and needed for the
    /// same reason: the count is cumulative and spent in game, so it cannot
    /// be re-derived from the server history.
    /// </summary>
    public const uint SendTokenBankedAddress = 0x8001_5ff8;

    public const long LocationIdBase = 0x0AD1_0000;
    // Tower checks: 39 floors x 3 slots. Location id = base + (floor-1)*3 + slot;
    // journal position = byte (floor-1), bit slot. Slots 0 and 1 lie on the
    // floor; slot 2 is carried by that floor's forced monster spawn.
    public const int TowerFloorCount = 39;
    public const int SlotsPerFloor = 3;
    public const int LocationCount = TowerFloorCount * SlotsPerFloor; // 117
    public const long ShopLocationIdBase = LocationIdBase + 0x100;
    public const int ShopLocationCount = 20;
    /// <summary>The tower journal as read from ADSV: one byte per floor.</summary>
    public const int LocationMaskSize = TowerJournalSize;
    public const int ShopLocationMaskSize = sizeof(uint);
    public const byte MaximumKeycardLevel = 8;
    public const byte MaximumTemperLevel = 3;

    /// <summary>
    /// Map a tower location index (0..LocationCount-1) to its journal byte
    /// and bit. False for anything outside the tower's range.
    /// </summary>
    public static bool TryGetTowerMaskPosition(long index, out int byteIndex, out byte bit)
    {
        byteIndex = 0;
        bit = 0;
        if (index < 0 || index >= LocationCount)
            return false;
        byteIndex = (int)(index / SlotsPerFloor);
        bit = (byte)(1 << (int)(index % SlotsPerFloor));
        return true;
    }

    /// <summary>
    /// Clear what the game never sets: bits at or above the slot count in
    /// every floor byte, and the spare byte after floor 39. Read-side hygiene
    /// only - the write side ORs into what the game wrote.
    /// </summary>
    public static void NormalizeTowerMask(Span<byte> mask)
    {
        if (mask.Length != LocationMaskSize)
            throw new ArgumentException($"Location mask must be {LocationMaskSize} bytes.", nameof(mask));
        const byte slotBits = (1 << SlotsPerFloor) - 1;
        for (int floorIndex = 0; floorIndex < TowerFloorCount; floorIndex++)
            mask[floorIndex] &= slotBits;
        for (int spare = TowerFloorCount; spare < LocationMaskSize; spare++)
            mask[spare] = 0;
    }

    public static bool TryReadIdentity(
        IEmulatorMemory memory,
        out AzureDreamsSeedIdentity identity,
        out string message)
    {
        identity = default;
        if (!TryRequireSeededBuild(memory, out message))
            return false;

        Span<byte> seedHeader = stackalloc byte[16];
        if (!memory.TryRead(SeedBlockAddress, seedHeader, out string? readError))
        {
            message = readError ?? "Could not read the generated seed block.";
            return false;
        }

        uint seedMagic = BinaryPrimitives.ReadUInt32LittleEndian(seedHeader);
        ushort seedVersion = BinaryPrimitives.ReadUInt16LittleEndian(seedHeader[4..]);
        ushort locationCount = BinaryPrimitives.ReadUInt16LittleEndian(seedHeader[6..]);
        if (seedMagic != SeedMagic || seedVersion != SeedVersion || locationCount != LocationCount)
        {
            message = "The generated seed block is not loaded.";
            return false;
        }

        if (!TryReadPersistentIdentity(
                memory,
                out AzureDreamsSeedIdentity persistentIdentity,
                out message))
        {
            return false;
        }
        if (!seedHeader[8..16].SequenceEqual(persistentIdentity.Signature))
        {
            message = "The loaded seed and persistent multiworld state have different signatures.";
            return false;
        }

        identity = new AzureDreamsSeedIdentity(seedHeader[8..16].ToArray());
        message = string.Empty;
        return true;
    }

    public static bool TryReadPersistentIdentity(
        IEmulatorMemory memory,
        out AzureDreamsSeedIdentity identity,
        out string message)
    {
        identity = default;
        if (!TryRequireSeededBuild(memory, out message))
            return false;

        Span<byte> stateHeader = stackalloc byte[16];
        if (!memory.TryRead(PersistentStateAddress, stateHeader, out string? readError))
        {
            message = readError ?? "Could not read the persistent multiworld state.";
            return false;
        }

        uint stateMagic = BinaryPrimitives.ReadUInt32LittleEndian(stateHeader);
        ushort stateVersion = BinaryPrimitives.ReadUInt16LittleEndian(stateHeader[4..]);
        ushort stateSize = BinaryPrimitives.ReadUInt16LittleEndian(stateHeader[6..]);
        if (stateMagic != PersistentStateMagic ||
            stateVersion != PersistentStateVersion ||
            stateSize != PersistentStateSize)
        {
            message = "Waiting for the patched town or tower state to finish loading.";
            return false;
        }

        identity = new AzureDreamsSeedIdentity(stateHeader[8..16].ToArray());
        message = string.Empty;
        return true;
    }

    public static bool TryReadSynchronizationIdentity(
        IEmulatorMemory memory,
        out AzureDreamsSeedIdentity identity,
        out string message)
    {
        identity = default;
        if (!TryRequireSeededBuild(memory, out message))
            return false;

        Span<byte> seedHeader = stackalloc byte[16];
        if (!memory.TryRead(SeedBlockAddress, seedHeader, out string? readError))
        {
            message = readError ?? "Could not inspect the generated seed block.";
            return false;
        }

        bool seedLoaded =
            BinaryPrimitives.ReadUInt32LittleEndian(seedHeader) == SeedMagic &&
            BinaryPrimitives.ReadUInt16LittleEndian(seedHeader[4..]) == SeedVersion &&
            BinaryPrimitives.ReadUInt16LittleEndian(seedHeader[6..]) == LocationCount;
        return seedLoaded
            ? TryReadIdentity(memory, out identity, out message)
            : TryReadPersistentIdentity(memory, out identity, out message);
    }

    /// <summary>
    /// Reports whether the save-backed block still reads as an untouched new
    /// game. Nothing has been received, no tower or shop location has been
    /// collected, and no keycard has been granted.
    /// </summary>
    /// <remarks>
    /// The intro restore handshake keeps its state byte in the town core's
    /// mailbox, which the town overlay reloads from disc on every tower
    /// return. A reloaded mailbox reads as "first run" even in the middle of a
    /// session, so the handshake needs a second opinion that survives overlay
    /// reloads. A populated save block is proof that the boot intro is long
    /// over, whereas a console reset produces a pristine block again.
    /// </remarks>
    public static bool TryReadSaveIsPristine(
        IEmulatorMemory memory,
        out bool pristine,
        out string message)
    {
        pristine = false;
        Span<byte> state = stackalloc byte[PersistentStateSize];
        if (!memory.TryRead(PersistentStateAddress, state, out string? readError))
        {
            message = readError ?? "Could not inspect the persistent Azure Dreams state.";
            return false;
        }
        if (BinaryPrimitives.ReadUInt32LittleEndian(state) != PersistentStateMagic)
        {
            // Without the extension there is nothing to have populated yet.
            pristine = true;
            message = string.Empty;
            return true;
        }

        // Everything after the signature: both journals and the three
        // counters. The intro flags at +0x54/+0x55 are deliberately NOT part
        // of this - they are the handshake this answer feeds.
        pristine =
            BinaryPrimitives.ReadUInt32LittleEndian(
                state[ReceivedItemCountOffset..]) == 0 &&
            BinaryPrimitives.ReadUInt32LittleEndian(
                state[KeycardLevelOffset..]) == 0 &&
            BinaryPrimitives.ReadUInt32LittleEndian(
                state[GoldGrantedCountOffset..]) == 0 &&
            !state.Slice(PersistentLocationMaskOffset, TowerJournalSize + TownJournalSize)
                .ContainsAnyExcept((byte)0);
        message = string.Empty;
        return true;
    }

    private static uint? _highestObservedCursor;

    /// <summary>
    /// Forget the cursor high-water mark. Called when the client loses the
    /// game, because the next attach may be a different save entirely.
    /// </summary>
    public static void ResetCursorObservation() => _highestObservedCursor = null;

    /// <summary>
    /// True when the durable receive cursor has gone DOWN since this client
    /// attached, which nothing in this client can cause.
    ///
    /// <para>Every writer here moves it forward only - the delta fold, the
    /// delivered-through watermark, the lowest-pending recovery and the
    /// mailbox ack fold all take a maximum, and the tower dispatcher's own
    /// commit is monotonic. So a decrease is external: either the save block
    /// at <c>0x80010000</c> was restored over (a town checkpoint, or something
    /// in the town-tower crossing), or a second client is writing the same
    /// slot.</para>
    ///
    /// <para>This exists because the duplication bug is invisible while it
    /// happens. Every item between the high-water mark and the new value is
    /// still owed as far as the server is concerned, so the tower will deliver
    /// them again - and by the time the player notices, the evidence is a
    /// cursor that simply reads lower than it should with nothing to say when
    /// it changed. Four fixes were aimed at who WRITES this value; none of
    /// them would have caught something else lowering it.</para>
    /// </summary>
    public static bool ObserveCursorRegression(uint cursor, out uint highWaterMark)
    {
        highWaterMark = _highestObservedCursor ?? cursor;
        if (_highestObservedCursor is uint previous && cursor < previous)
        {
            // Re-baseline so this reports the drop once rather than every
            // poll for the rest of the session.
            _highestObservedCursor = cursor;
            return true;
        }

        _highestObservedCursor = cursor;
        return false;
    }

    public static bool TryReadReceivedItemCount(
        IEmulatorMemory memory,
        out uint count,
        out string message)
    {
        count = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> bytes = stackalloc byte[4];
        if (!memory.TryRead(PersistentStateAddress + ReceivedItemCountOffset, bytes, out string? error))
        {
            message = error ?? "Could not read the received-item cursor.";
            return false;
        }

        count = BinaryPrimitives.ReadUInt32LittleEndian(bytes);
        message = string.Empty;
        return true;
    }

    public static bool TryWriteReceivedItemCount(
        IEmulatorMemory memory,
        uint count,
        out string message)
    {
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> requested = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(requested, count);
        if (!memory.TryWrite(PersistentStateAddress + ReceivedItemCountOffset, requested, out string? error))
        {
            message = error ?? "Could not update the received-item cursor.";
            return false;
        }

        Span<byte> observed = stackalloc byte[4];
        if (!memory.TryRead(PersistentStateAddress + ReceivedItemCountOffset, observed, out error) ||
            !observed.SequenceEqual(requested))
        {
            message = error ?? "The received-item cursor did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    // Koh's gold, inside the checkpoint-covered save block - a restore rolls
    // it back together with the receive cursor that granted it, which is what
    // keeps a gold grant exactly-once across death rollbacks: cursor and
    // counter revert as one and the package is simply re-offered.
    public const uint GoldAddress = 0x8001_2d5c;
    // Conservative ceiling: the counter is a 32-bit word and the town menus
    // draw up to seven digits. Clamping beats wrapping in every failure mode.
    public const uint MaximumGold = 9_999_999;

    /// <summary>
    /// The durable count of 5000-gold packages this save has banked, at
    /// ADSV GoldGrantedCountOffset. Compared against the count in the server history every
    /// poll, the same shape as the keycard level - which is what lets gold
    /// cut the delivery line. It rolls back with the gold counter and the
    /// cursor on a checkpoint restore, so the three can never disagree.
    /// </summary>
    public static bool TryReadGoldGrantedCount(
        IEmulatorMemory memory,
        out uint count,
        out string message)
    {
        count = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> bytes = stackalloc byte[4];
        if (!memory.TryRead(
                PersistentStateAddress + GoldGrantedCountOffset, bytes, out string? error))
        {
            message = error ?? "Could not read the gold-granted counter.";
            return false;
        }

        count = BinaryPrimitives.ReadUInt32LittleEndian(bytes);
        message = string.Empty;
        return true;
    }

    public static bool TryWriteGoldGrantedCount(
        IEmulatorMemory memory,
        uint count,
        out string message)
    {
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> requested = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(requested, count);
        if (!memory.TryWrite(
                PersistentStateAddress + GoldGrantedCountOffset, requested, out string? error))
        {
            message = error ?? "Could not update the gold-granted counter.";
            return false;
        }

        Span<byte> observed = stackalloc byte[4];
        if (!memory.TryRead(
                PersistentStateAddress + GoldGrantedCountOffset, observed, out error) ||
            !observed.SequenceEqual(requested))
        {
            message = error ?? "The gold-granted counter did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    public static bool TryGrantGold(
        IEmulatorMemory memory,
        uint amount,
        out uint newTotal,
        out string message)
    {
        newTotal = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> bytes = stackalloc byte[4];
        if (!memory.TryRead(GoldAddress, bytes, out string? error))
        {
            message = error ?? "Could not read the gold counter.";
            return false;
        }

        uint current = BinaryPrimitives.ReadUInt32LittleEndian(bytes);
        newTotal = current >= MaximumGold - amount
            ? MaximumGold
            : current + amount;
        BinaryPrimitives.WriteUInt32LittleEndian(bytes, newTotal);
        if (!memory.TryWrite(GoldAddress, bytes, out error))
        {
            message = error ?? "Could not update the gold counter.";
            return false;
        }

        Span<byte> observed = stackalloc byte[4];
        if (!memory.TryRead(GoldAddress, observed, out error) ||
            !observed.SequenceEqual(bytes))
        {
            message = error ?? "The gold counter did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    /// <summary>
    /// The live send-token count, as the game sees it.
    ///
    /// <para>An absent magic means neither seeder has touched the pair yet,
    /// and the honest answer is then the starting grant rather than zero:
    /// whichever of the two runs first will hand it over, so reporting zero
    /// would show the player a "no tokens" state they will never see in
    /// game. This does not write - deciding a save is new is the game's
    /// call, not the client's.</para>
    /// </summary>
    public static bool TryReadSendTokens(
        IEmulatorMemory memory,
        out uint tokens,
        out string message)
    {
        tokens = 0;
        Span<byte> pair = stackalloc byte[8];
        if (!memory.TryRead(SendTokenCountAddress, pair, out string? error))
        {
            message = error ?? "Could not read the send-token counter.";
            return false;
        }

        if (BinaryPrimitives.ReadUInt32LittleEndian(pair[4..]) != SendTokenMagic)
        {
            tokens = SendTokenStartingCount;
            message = string.Empty;
            return true;
        }

        tokens = BinaryPrimitives.ReadUInt32LittleEndian(pair);
        message = string.Empty;
        return true;
    }

    public static bool TryReadSendTokensBankedCount(
        IEmulatorMemory memory,
        out uint count,
        out string message)
    {
        count = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> bytes = stackalloc byte[4];
        if (!memory.TryRead(SendTokenBankedAddress, bytes, out string? error))
        {
            message = error ?? "Could not read the send-token banked counter.";
            return false;
        }

        count = BinaryPrimitives.ReadUInt32LittleEndian(bytes);
        message = string.Empty;
        return true;
    }

    public static bool TryWriteSendTokensBankedCount(
        IEmulatorMemory memory,
        uint count,
        out string message)
    {
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> requested = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(requested, count);
        if (!memory.TryWrite(SendTokenBankedAddress, requested, out string? error))
        {
            message = error ?? "Could not update the send-token banked counter.";
            return false;
        }

        Span<byte> observed = stackalloc byte[4];
        if (!memory.TryRead(SendTokenBankedAddress, observed, out error) ||
            !observed.SequenceEqual(requested))
        {
            message = error ?? "The send-token banked counter did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    /// <summary>
    /// Adds tokens to the game's counter, seeding the pair if the game has
    /// not yet touched it.
    ///
    /// <para>The seeding matters: a token that arrives before the player
    /// has ever opened the Send row lands on an unwitnessed pair, and
    /// writing the count alone would leave the tower gate free to decide
    /// the save was new and overwrite it with the starting one. Writing the
    /// magic alongside closes that - and the starting token is included in
    /// the seed here for the same reason the initializer grants it.</para>
    /// </summary>
    public static bool TryGrantSendTokens(
        IEmulatorMemory memory,
        uint amount,
        out uint newTotal,
        out string message)
    {
        newTotal = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> pair = stackalloc byte[8];
        if (!memory.TryRead(SendTokenCountAddress, pair, out string? error))
        {
            message = error ?? "Could not read the send-token counter.";
            return false;
        }

        uint current = BinaryPrimitives.ReadUInt32LittleEndian(pair[4..]) == SendTokenMagic
            ? BinaryPrimitives.ReadUInt32LittleEndian(pair)
            : SendTokenStartingCount;
        newTotal = current + amount;
        BinaryPrimitives.WriteUInt32LittleEndian(pair, newTotal);
        BinaryPrimitives.WriteUInt32LittleEndian(pair[4..], SendTokenMagic);
        if (!memory.TryWrite(SendTokenCountAddress, pair, out error))
        {
            message = error ?? "Could not update the send-token counter.";
            return false;
        }

        Span<byte> observed = stackalloc byte[8];
        if (!memory.TryRead(SendTokenCountAddress, observed, out error) ||
            !observed.SequenceEqual(pair))
        {
            message = error ?? "The send-token counter did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    public static bool TryReadCollectedLocationMask(
        IEmulatorMemory memory,
        Span<byte> mask,
        out string message)
    {
        if (mask.Length != LocationMaskSize)
        {
            message = $"The collected-location mask must be exactly {LocationMaskSize} bytes.";
            return false;
        }

        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        if (!memory.TryRead(PersistentStateAddress + PersistentLocationMaskOffset, mask, out string? error))
        {
            message = error ?? "Could not read the persistent collected-location mask.";
            return false;
        }

        NormalizeTowerMask(mask);
        message = string.Empty;
        return true;
    }

    public static bool TryMergeCheckedLocations(
        IEmulatorMemory memory,
        IEnumerable<long> checkedLocations,
        out int newlyRecorded,
        out string message)
    {
        newlyRecorded = 0;
        byte[] mask = new byte[LocationMaskSize];
        if (!TryReadCollectedLocationMask(memory, mask, out message))
            return false;

        foreach (long locationId in checkedLocations)
        {
            if (!TryGetTowerMaskPosition(locationId - LocationIdBase, out int byteIndex, out byte bit))
                continue;
            if ((mask[byteIndex] & bit) != 0)
                continue;

            mask[byteIndex] |= bit;
            newlyRecorded++;
        }

        if (newlyRecorded == 0)
        {
            message = string.Empty;
            return true;
        }

        // The journal is the only copy. The tower mailbox used to carry a
        // 16-byte "collected request mirror" at +0x90 that this also
        // refreshed; no game code ever read it (the spawner and the collect
        // hook read ADSV directly), and the 40-byte v4 journal would not fit
        // in it, so it is retired.
        uint stateMaskAddress = PersistentStateAddress + PersistentLocationMaskOffset;
        if (!memory.TryWrite(stateMaskAddress, mask, out string? error))
        {
            message = error ?? "Could not merge server checks into persistent game state.";
            return false;
        }

        Span<byte> observed = stackalloc byte[LocationMaskSize];
        if (!memory.TryRead(stateMaskAddress, observed, out error) || !observed.SequenceEqual(mask))
        {
            message = error ?? "The persistent collected-location mask did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    /// <summary>
    /// Merge server-confirmed checks into an ADSV record held in a buffer -
    /// both journals - and return how many bits were newly set. This is the
    /// same merge <see cref="TryMergeCheckedLocations"/> performs on live
    /// memory, applied to a checkpoint payload BEFORE it is written back:
    /// a tower resume rebuilds the restored floor the moment the angel hands
    /// over, and the spawner reads the journal at that build, so a merge that
    /// only lands on a later poll is too late - every marker collected on that
    /// floor since its entry checkpoint respawns (ride 2, 2026-08-15).
    /// </summary>
    public static int MergeCheckedLocationsIntoState(
        Span<byte> state,
        IEnumerable<long> checkedLocations)
    {
        if (state.Length < PersistentStateSize)
            throw new ArgumentException($"ADSV state must be {PersistentStateSize} bytes.", nameof(state));
        if (BinaryPrimitives.ReadUInt32LittleEndian(state) != PersistentStateMagic)
            return 0;

        Span<byte> tower = state.Slice(PersistentLocationMaskOffset, LocationMaskSize);
        Span<byte> shop = state.Slice(PersistentShopMaskOffset, ShopLocationMaskSize);
        uint shopBits = BinaryPrimitives.ReadUInt32LittleEndian(shop);
        int newlyRecorded = 0;
        foreach (long locationId in checkedLocations)
        {
            if (TryGetTowerMaskPosition(locationId - LocationIdBase, out int byteIndex, out byte bit))
            {
                if ((tower[byteIndex] & bit) == 0)
                {
                    tower[byteIndex] |= bit;
                    newlyRecorded++;
                }
                continue;
            }
            long shopIndex = locationId - ShopLocationIdBase;
            if (shopIndex >= 0 && shopIndex < ShopLocationCount)
            {
                uint shopBit = 1u << (int)shopIndex;
                if ((shopBits & shopBit) == 0)
                {
                    shopBits |= shopBit;
                    newlyRecorded++;
                }
            }
        }
        BinaryPrimitives.WriteUInt32LittleEndian(shop, shopBits);
        return newlyRecorded;
    }

    public static long[] GetCollectedLocationIds(ReadOnlySpan<byte> mask)
    {
        if (mask.Length != LocationMaskSize)
            throw new ArgumentException($"Location mask must be {LocationMaskSize} bytes.", nameof(mask));

        List<long> locations = [];
        for (int index = 0; index < LocationCount; index++)
        {
            TryGetTowerMaskPosition(index, out int byteIndex, out byte bit);
            if ((mask[byteIndex] & bit) != 0)
                locations.Add(LocationIdBase + index);
        }
        return locations.ToArray();
    }

    public static bool TryReadCollectedShopLocationMask(
        IEmulatorMemory memory,
        Span<byte> mask,
        out string message)
    {
        if (mask.Length != ShopLocationMaskSize)
        {
            message = $"The shop-location mask must be exactly {ShopLocationMaskSize} bytes.";
            return false;
        }
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        if (!memory.TryRead(
                PersistentStateAddress + PersistentShopMaskOffset,
                mask,
                out string? error))
        {
            message = error ?? "Could not read the persistent shop-location mask.";
            return false;
        }

        uint implementedBits = (1u << ShopLocationCount) - 1;
        BinaryPrimitives.WriteUInt32LittleEndian(
            mask,
            BinaryPrimitives.ReadUInt32LittleEndian(mask) & implementedBits);
        message = string.Empty;
        return true;
    }

    public static bool TryMergeCheckedShopLocations(
        IEmulatorMemory memory,
        IEnumerable<long> checkedLocations,
        out int newlyRecorded,
        out string message)
    {
        newlyRecorded = 0;
        Span<byte> mask = stackalloc byte[ShopLocationMaskSize];
        if (!TryReadCollectedShopLocationMask(memory, mask, out message))
            return false;

        uint bits = BinaryPrimitives.ReadUInt32LittleEndian(mask);
        foreach (long locationId in checkedLocations)
        {
            long index = locationId - ShopLocationIdBase;
            if (index < 0 || index >= ShopLocationCount)
                continue;

            uint bit = 1u << (int)index;
            if ((bits & bit) != 0)
                continue;

            bits |= bit;
            newlyRecorded++;
        }

        if (newlyRecorded == 0)
        {
            message = string.Empty;
            return true;
        }

        Span<byte> requested = stackalloc byte[ShopLocationMaskSize];
        BinaryPrimitives.WriteUInt32LittleEndian(requested, bits);
        uint address = PersistentStateAddress + PersistentShopMaskOffset;
        if (!memory.TryWrite(address, requested, out string? error))
        {
            message = error ?? "Could not merge server shop checks into persistent game state.";
            return false;
        }

        Span<byte> observed = stackalloc byte[ShopLocationMaskSize];
        if (!memory.TryRead(address, observed, out error) || !observed.SequenceEqual(requested))
        {
            message = error ?? "The persistent shop-location mask did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    public static long[] GetCollectedShopLocationIds(ReadOnlySpan<byte> mask)
    {
        if (mask.Length != ShopLocationMaskSize)
        {
            throw new ArgumentException(
                $"Shop-location mask must be {ShopLocationMaskSize} bytes.",
                nameof(mask));
        }

        uint bits = BinaryPrimitives.ReadUInt32LittleEndian(mask);
        List<long> locations = [];
        for (int index = 0; index < ShopLocationCount; index++)
        {
            if ((bits & (1u << index)) != 0)
                locations.Add(ShopLocationIdBase + index);
        }
        return locations.ToArray();
    }

    public static bool TryGrantProgressiveKeycard(
        IEmulatorMemory memory,
        out byte newLevel,
        out string message)
    {
        newLevel = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> levelBytes = stackalloc byte[4];
        uint address = PersistentStateAddress + KeycardLevelOffset;
        if (!memory.TryRead(address, levelBytes, out string? error))
        {
            message = error ?? "Could not read the progressive-keycard level.";
            return false;
        }

        uint currentLevel = BinaryPrimitives.ReadUInt32LittleEndian(levelBytes);
        if (currentLevel >= MaximumKeycardLevel)
        {
            message = $"The save already has the maximum keycard level ({MaximumKeycardLevel}).";
            return false;
        }

        newLevel = checked((byte)(currentLevel + 1));
        return TrySetProgressiveKeycardLevel(memory, newLevel, out message);
    }

    public static bool TrySetProgressiveKeycardLevel(
        IEmulatorMemory memory,
        byte level,
        out string message)
    {
        if (level > MaximumKeycardLevel)
        {
            message = $"Keycard level must be between 0 and {MaximumKeycardLevel}.";
            return false;
        }
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> levelBytes = stackalloc byte[4];
        uint address = PersistentStateAddress + KeycardLevelOffset;
        BinaryPrimitives.WriteUInt32LittleEndian(levelBytes, level);
        if (!memory.TryWrite(address, levelBytes, out string? error))
        {
            message = error ?? "Could not save the new progressive-keycard level.";
            return false;
        }

        if (!AzureDreamsMailbox.TryDetect(memory, out bool towerMailboxDetected, out message))
            return false;
        byte[] mailboxLevel = [level];
        if (towerMailboxDetected &&
            !memory.TryWrite(
                AzureDreamsMailbox.Address + AzureDreamsMailbox.ElevatorClearanceOffset,
                mailboxLevel,
                out error))
        {
            message = error ?? "The keycard was saved, but the live elevator clearance could not be updated.";
            return false;
        }

        Span<byte> observed = stackalloc byte[4];
        if (!memory.TryRead(address, observed, out error) || !observed.SequenceEqual(levelBytes))
        {
            message = error ?? "The keycard level did not match on read-back.";
            return false;
        }

        message = $"Progressive Keycard level is {level}.";
        return true;
    }

    /// <summary>
    /// The blacksmith's two temper levels, straight from their ADSV bytes.
    /// </summary>
    public static bool TryReadTemperLevels(
        IEmulatorMemory memory,
        out byte weaponLevel,
        out byte shieldLevel,
        out string message)
    {
        weaponLevel = 0;
        shieldLevel = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> levels = stackalloc byte[2];
        if (!memory.TryRead(PersistentStateAddress + WeaponTemperLevelOffset, levels, out string? error))
        {
            message = error ?? "Could not read the temper levels.";
            return false;
        }
        if (levels[0] > MaximumTemperLevel || levels[1] > MaximumTemperLevel)
        {
            message = $"The saved temper levels {levels[0]}/{levels[1]} exceed the maximum {MaximumTemperLevel}.";
            return false;
        }
        weaponLevel = levels[0];
        shieldLevel = levels[1];
        message = string.Empty;
        return true;
    }

    /// <summary>
    /// Writes both temper level bytes (they share one word with the intro
    /// flags, so this writes exactly the two bytes) and reads them back.
    /// </summary>
    public static bool TrySetTemperLevels(
        IEmulatorMemory memory,
        byte weaponLevel,
        byte shieldLevel,
        out string message)
    {
        if (weaponLevel > MaximumTemperLevel || shieldLevel > MaximumTemperLevel)
        {
            message = $"Temper levels must be between 0 and {MaximumTemperLevel}.";
            return false;
        }
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        byte[] levels = [weaponLevel, shieldLevel];
        uint address = PersistentStateAddress + WeaponTemperLevelOffset;
        if (!memory.TryWrite(address, levels, out string? error))
        {
            message = error ?? "Could not save the temper levels.";
            return false;
        }
        Span<byte> observed = stackalloc byte[2];
        if (!memory.TryRead(address, observed, out error) || !observed.SequenceEqual(levels))
        {
            message = error ?? "The temper levels did not match on read-back.";
            return false;
        }
        message = $"Temper levels are weapon {weaponLevel}, shield {shieldLevel}.";
        return true;
    }

    /// <summary>The ball charger's level, the byte beside ADSV.</summary>
    public static bool TryReadBallChargeLevel(
        IEmulatorMemory memory,
        out byte level,
        out string message)
    {
        level = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> value = stackalloc byte[1];
        if (!memory.TryRead(BallChargeLevelAddress, value, out string? error))
        {
            message = error ?? "Could not read the ball charge level.";
            return false;
        }
        if (value[0] > MaximumTemperLevel)
        {
            message = $"The saved ball charge level {value[0]} exceeds the maximum {MaximumTemperLevel}.";
            return false;
        }
        level = value[0];
        message = string.Empty;
        return true;
    }

    /// <summary>Writes the ball charge level byte and reads it back.</summary>
    public static bool TrySetBallChargeLevel(
        IEmulatorMemory memory,
        byte level,
        out string message)
    {
        if (level > MaximumTemperLevel)
        {
            message = $"The ball charge level must be between 0 and {MaximumTemperLevel}.";
            return false;
        }
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        byte[] value = [level];
        if (!memory.TryWrite(BallChargeLevelAddress, value, out string? error))
        {
            message = error ?? "Could not save the ball charge level.";
            return false;
        }
        Span<byte> observed = stackalloc byte[1];
        if (!memory.TryRead(BallChargeLevelAddress, observed, out error) || observed[0] != level)
        {
            message = error ?? "The ball charge level did not match on read-back.";
            return false;
        }
        message = $"Ball charge level is {level}.";
        return true;
    }

    public static bool TryReadProgressiveKeycardLevel(
        IEmulatorMemory memory,
        out byte level,
        out string message)
    {
        level = 0;
        if (!TryReadPersistentIdentity(memory, out _, out message))
            return false;

        Span<byte> levelBytes = stackalloc byte[4];
        uint address = PersistentStateAddress + KeycardLevelOffset;
        if (!memory.TryRead(address, levelBytes, out string? error))
        {
            message = error ?? "Could not read the progressive-keycard level.";
            return false;
        }

        uint value = BinaryPrimitives.ReadUInt32LittleEndian(levelBytes);
        if (value > MaximumKeycardLevel)
        {
            message = $"The saved keycard level {value} exceeds the maximum {MaximumKeycardLevel}.";
            return false;
        }
        level = (byte)value;
        message = string.Empty;
        return true;
    }

    private static bool TryRequireSeededBuild(IEmulatorMemory memory, out string message)
    {
        Span<byte> memoryTopBytes = stackalloc byte[4];
        if (!memory.TryRead(
                AzureDreamsMailbox.MemoryTopAddress,
                memoryTopBytes,
                out string? readError))
        {
            message = readError ?? "Could not read the game's memory-top value.";
            return false;
        }

        uint memoryTop = BinaryPrimitives.ReadUInt32LittleEndian(memoryTopBytes);
        if (memoryTop != AzureDreamsMailbox.ExpectedPatchedMemoryTop)
        {
            message = $"The seeded multiworld patch is not active (memory top is 0x{memoryTop:x8}).";
            return false;
        }

        message = string.Empty;
        return true;
    }
}
