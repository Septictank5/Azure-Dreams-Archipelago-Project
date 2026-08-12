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

    public const uint PersistentStateAddress = 0x8001_5fc0;
    public const uint PersistentStateMagic = 0x5653_4441; // "ADSV"
    // 3 (2026-08-05): the gold-granted counter at +0x28. Eager gold granting
    // needs a durable count of packages already banked - gold is cumulative
    // and cannot be re-derived from the history the way a keycard level can.
    public const ushort PersistentStateVersion = 3;
    public const ushort PersistentStateSize = 0x2c;
    public const int PersistentLocationMaskOffset = 0x10;
    public const int ReceivedItemCountOffset = 0x1c;
    public const int KeycardLevelOffset = 0x20;
    public const int PersistentShopMaskOffset = 0x24;
    public const int GoldGrantedCountOffset = 0x28;

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
    public const int LocationCount = 78;
    public const long ShopLocationIdBase = LocationIdBase + 0x100;
    public const int ShopLocationCount = 20;
    public const int LocationMaskSize = (LocationCount + 7) / 8;
    public const int ShopLocationMaskSize = sizeof(uint);
    public const byte MaximumKeycardLevel = 8;

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

        pristine =
            BinaryPrimitives.ReadUInt32LittleEndian(
                state[ReceivedItemCountOffset..]) == 0 &&
            BinaryPrimitives.ReadUInt32LittleEndian(
                state[PersistentShopMaskOffset..]) == 0 &&
            BinaryPrimitives.ReadUInt32LittleEndian(
                state[KeycardLevelOffset..]) == 0 &&
            BinaryPrimitives.ReadUInt32LittleEndian(
                state[GoldGrantedCountOffset..]) == 0 &&
            !state.Slice(PersistentLocationMaskOffset, LocationMaskSize)
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
    /// ADSV +0x28. Compared against the count in the server history every
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

        mask[^1] &= 0x3f;
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
            long index = locationId - LocationIdBase;
            if (index < 0 || index >= LocationCount)
                continue;

            int byteIndex = (int)index >> 3;
            byte bit = (byte)(1 << ((int)index & 7));
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

        uint stateMaskAddress = PersistentStateAddress + PersistentLocationMaskOffset;
        if (!memory.TryWrite(stateMaskAddress, mask, out string? error))
        {
            message = error ?? "Could not merge server checks into persistent game state.";
            return false;
        }

        // The high mailbox is tower-only; this address is live stack in town.
        // Floor spawning reads the persistent mask directly, so the mirror is
        // optional and is refreshed only when its header proves it is loaded.
        if (!AzureDreamsMailbox.TryDetect(memory, out bool towerMailboxDetected, out message))
            return false;
        if (towerMailboxDetected &&
            !memory.TryWrite(
                AzureDreamsMailbox.Address + AzureDreamsMailbox.CollectedFloorLocationRequestsOffset,
                mask,
                out error))
        {
            message = error ?? "Persistent checks were updated, but the mailbox mirror could not be updated.";
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

    public static long[] GetCollectedLocationIds(ReadOnlySpan<byte> mask)
    {
        if (mask.Length != LocationMaskSize)
            throw new ArgumentException($"Location mask must be {LocationMaskSize} bytes.", nameof(mask));

        List<long> locations = [];
        for (int index = 0; index < LocationCount; index++)
        {
            if ((mask[index >> 3] & (1 << (index & 7))) != 0)
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
