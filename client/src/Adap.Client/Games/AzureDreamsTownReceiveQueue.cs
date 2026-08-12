using System.Buffers.Binary;
using Adap.Client.Emulators;

namespace Adap.Client.Games;

internal readonly record struct AzureDreamsQueuedItem(
    AzureDreamsItemDescriptor Descriptor,
    uint Token);

/// <summary>
/// A snapshot of the town receive queue as one poll saw it.
/// </summary>
internal readonly record struct AzureDreamsTownReceiveQueueState
{
    public bool Present { get; init; }
    public bool Locked { get; init; }
    public byte Count { get; init; }
    public byte Head { get; init; }
    public byte Limit { get; init; }
    public byte Result { get; init; }

    /// <summary>Entries the game has not consumed yet, in delivery order.</summary>
    public IReadOnlyList<AzureDreamsQueuedItem> InFlight { get; init; }

    /// <summary>Free slots. Byte cursors free-run; occupancy never exceeds the slot count.</summary>
    public int Free => AzureDreamsTownReceiveQueue.Slots - InFlight.Count;
}

/// <summary>
/// The client half of the town receive queue Nada drains from inside her own
/// conversation (see the header of the generator's town_receive.py).
///
/// <para>The client is the ONLY writer of <c>count</c> and of the entries; the
/// game owns <c>lock</c>, <c>head</c>, <c>limit</c> and <c>result</c>. One
/// writer per byte, so there is no read-modify-write anywhere in the
/// protocol.</para>
///
/// <para>Appends are published by writing the entry first and <c>count</c>
/// last, and the game snapshots <c>count</c> into <c>limit</c> when the
/// conversation arms. An append that races the lock therefore lands beyond
/// the snapshot and is simply not that conversation's business - it is
/// offered at the next talk. Nothing here needs to synchronise with the game
/// thread, which is why the queue can be safely written while the player
/// walks around.</para>
///
/// <para>The queue is self-describing: the tokens of the entries still in
/// flight say exactly what the client has outstanding, so a client restart
/// needs no saved state to reconcile. Ordinary tokens are
/// <c>historyIndex + 1</c> and the game commits them to the durable receive
/// cursor as they land; gift tokens carry the sign bit and never touch that
/// cursor.</para>
/// </summary>
internal static class AzureDreamsTownReceiveQueue
{
    // Moved out of the town slab 2026-08-02, into the resident block above
    // the AP mailbox. The slab is reloaded from disc on every town entry, so
    // a queue filled in town was wiped by a tower trip - and "the record
    // vanished" was indistinguishable from "the game consumed it". Here it
    // survives mode changes, so a reset can no longer be mistaken for a
    // delivery.
    //
    // This address only became reachable with the carve retraction: the
    // mailbox it follows was at 0x801FFF00, vanilla stack territory, which
    // is exactly why town and tower had separate receive systems at all.
    public const uint Address = 0x801d_a640;
    public const uint Magic = 0x5152_4441; // "ADRQ" little-endian.
    public const ushort ProtocolVersion = 1;
    public const int Slots = 16;
    public const int EntrySize = 8;
    public const int EntriesOffset = 0x18;
    public const int Size = EntriesOffset + Slots * EntrySize;

    public const int LockOffset = 0x08;
    public const int CountOffset = 0x09;
    public const int HeadOffset = 0x0a;
    public const int LimitOffset = 0x0b;
    public const int ResultOffset = 0x0c;

    public const byte ResultIdle = 0;
    public const byte ResultDelivered = 1;
    public const byte ResultNoRoom = 2;

    /// <summary>
    /// Gift tokens live in the sign-bit range the game's <c>bltz</c> guard
    /// tests, so a gift can never be mistaken for a receive-cursor sequence.
    /// </summary>
    public const uint GiftTokenBase = 0x8000_0000;

    public static bool IsGiftToken(uint token) => token >= GiftTokenBase;

    /// <summary>
    /// Writes the record header if it is not already there.
    ///
    /// <para>The record used to be baked into the town slab on disc, which
    /// restored it on every town entry - and wiped it on every town entry
    /// too. Now that it lives in the resident block, nothing on disc covers
    /// it, so somebody has to lay it down. The client does, because the
    /// client owns the delivery state: nothing game-side needs the record
    /// before Nada is talked to, and `check` reads an all-zero record as
    /// "nothing waiting" anyway.</para>
    ///
    /// <para>Only ever writes the 8-byte header, and only when the magic is
    /// absent - an initialised queue with live entries is never disturbed.
    /// The caller must have already confirmed a seeded build; this does not
    /// re-check, and writing 8 bytes into a vanilla game's RAM would be a
    /// real bug.</para>
    /// </summary>
    public static bool TryEnsureInitialized(
        IEmulatorMemory memory,
        out bool initialized,
        out string message)
    {
        initialized = false;
        Span<byte> header = stackalloc byte[8];
        if (!memory.TryRead(Address, header, out string? readError))
        {
            message = readError ?? "Could not probe the town receive queue.";
            return false;
        }
        if (BinaryPrimitives.ReadUInt32LittleEndian(header) == Magic)
        {
            message = string.Empty;
            return true;
        }

        BinaryPrimitives.WriteUInt32LittleEndian(header, Magic);
        BinaryPrimitives.WriteUInt16LittleEndian(header[4..], ProtocolVersion);
        BinaryPrimitives.WriteUInt16LittleEndian(header[6..], Size);
        if (!memory.TryWrite(Address, header, out string? writeError))
        {
            message = writeError ?? "Could not initialize the town receive queue.";
            return false;
        }

        // Cursors and lock start at zero.
        Span<byte> cursors = stackalloc byte[8];
        cursors.Clear();
        if (!memory.TryWrite(Address + LockOffset, cursors, out writeError))
        {
            message = writeError ?? "Could not clear the town receive queue cursors.";
            return false;
        }

        // The entries go too. They used to be left alone - "never read past
        // the occupancy the cursors describe, so stale bytes cannot matter" -
        // which stopped being true when the delivered-through watermark
        // started reading the slots BEHIND head. See
        // <see cref="TryReadDeliveredThrough"/>: its whole soundness rests on
        // a non-zero entry outside the live occupancy having been delivered,
        // and a fresh record inherits whatever the resident block held.
        Span<byte> entries = stackalloc byte[Slots * EntrySize];
        entries.Clear();
        if (!memory.TryWrite(Address + EntriesOffset, entries, out writeError))
        {
            message = writeError ?? "Could not clear the town receive queue entries.";
            return false;
        }

        initialized = true;
        message = string.Empty;
        return true;
    }

    public static bool TryRead(
        IEmulatorMemory memory,
        out AzureDreamsTownReceiveQueueState state,
        out string message)
    {
        state = new AzureDreamsTownReceiveQueueState
        {
            InFlight = Array.Empty<AzureDreamsQueuedItem>(),
        };

        Span<byte> record = stackalloc byte[Size];
        if (!memory.TryRead(Address, record, out string? readError))
        {
            message = readError ?? "Could not read the town receive queue.";
            return false;
        }

        if (BinaryPrimitives.ReadUInt32LittleEndian(record) != Magic)
        {
            // Not loaded, or a disc built before the queue existed. Either
            // way there is nothing to deliver into.
            message = string.Empty;
            return true;
        }

        ushort version = BinaryPrimitives.ReadUInt16LittleEndian(record[4..]);
        ushort structureSize = BinaryPrimitives.ReadUInt16LittleEndian(record[6..]);
        if (version != ProtocolVersion || structureSize != Size)
        {
            message = $"Town receive queue protocol mismatch: expected " +
                $"v{ProtocolVersion}/0x{Size:x}, observed v{version}/0x{structureSize:x}.";
            return false;
        }

        byte count = record[CountOffset];
        byte head = record[HeadOffset];
        // Free-running byte cursors: the slot count divides 256, so this
        // subtraction stays correct across wraparound.
        int pending = (count - head) & 0xff;
        if (pending > Slots)
        {
            message = $"The town receive queue reports {pending} pending entries, " +
                $"which exceeds its {Slots} slots.";
            return false;
        }

        var inFlight = new AzureDreamsQueuedItem[pending];
        for (int index = 0; index < pending; index++)
        {
            int slot = ((head + index) & (Slots - 1)) * EntrySize + EntriesOffset;
            inFlight[index] = new AzureDreamsQueuedItem(
                new AzureDreamsItemDescriptor(
                    record[slot],
                    record[slot + 1],
                    unchecked((sbyte)record[slot + 2]),
                    record[slot + 3]),
                BinaryPrimitives.ReadUInt32LittleEndian(record[(slot + 4)..]));
        }

        state = new AzureDreamsTownReceiveQueueState
        {
            Present = true,
            Locked = record[LockOffset] != 0,
            Count = count,
            Head = head,
            Limit = record[LimitOffset],
            Result = record[ResultOffset],
            InFlight = inFlight,
        };
        message = string.Empty;
        return true;
    }

    /// <summary>
    /// Appends one entry and republishes <c>count</c>.
    ///
    /// <para>The entry is written before <c>count</c> so the game can never
    /// observe a slot the count claims is valid but which has not been filled
    /// in. This is the only ordering constraint in the whole protocol.</para>
    /// </summary>
    /// <summary>
    /// The clear the town gate applies to everything carried in from the
    /// tower, applied here because Nada's box is the one delivery path that
    /// does not pass through that gate.
    ///
    /// <para>Walking back into town unequips every item (<c>0x20</c>) and
    /// appraises every item (<c>0x80</c>). An item Nada hands over while the
    /// player is ALREADY in town skips that entirely, so it lands in the
    /// inventory still owning bits no item in town is ever supposed to have -
    /// and every town menu builds its rows by copying the descriptor word,
    /// where <c>0x20</c> reads as "this row is checked" and <c>0x80</c> as
    /// "this row cannot be selected". That is why an unidentified sword could
    /// not be sold at the Equipment shop, and it would have made the same
    /// sword unpickable in Nada's own send list.</para>
    ///
    /// <para>Cursed (<c>0x40</c>) is deliberately kept: the town gate does not
    /// lift a curse, and the shops draw a skull for it. A familiar's monster-hut
    /// index lives in the low five bits and is likewise untouched.</para>
    ///
    /// <para>Nothing is lost from the unidentified mechanic. The tower keeps
    /// its own mailbox and delivers the descriptor untouched, so an item handed
    /// over mid-climb still arrives unappraised and stays that way until the
    /// player walks back through the gate - which is exactly what the game
    /// already does with its own drops.</para>
    /// </summary>
    public static AzureDreamsItemDescriptor ApplyTownGateClear(
        AzureDreamsItemDescriptor descriptor) =>
        descriptor with
        {
            Flags = (byte)(descriptor.Flags & ~(
                AzureDreamsItemManifest.FlagUnidentified |
                AzureDreamsItemManifest.FlagEquipped)),
        };

    public static bool TryAppend(
        IEmulatorMemory memory,
        AzureDreamsTownReceiveQueueState state,
        AzureDreamsItemDescriptor descriptor,
        uint token,
        out AzureDreamsTownReceiveQueueState updated,
        out string message)
    {
        updated = state;
        // Every town delivery passes through here - ordinary items and gifts
        // alike - which is what makes this the right place to stand in for the
        // gate Nada bypasses. Applied before the write AND before the in-flight
        // record, so the client's own view of the queue matches the bytes.
        descriptor = ApplyTownGateClear(descriptor);
        if (!state.Present)
        {
            message = "The town receive queue is not loaded.";
            return false;
        }
        if (state.Free <= 0)
        {
            message = "The town receive queue is full.";
            return false;
        }
        if (descriptor.ItemId == 0 || descriptor.Category == 0)
        {
            // The game treats a zero category as "this slot is free", so
            // storing one would create an invisible item the next allocator
            // hands out twice. The delivery routine rejects it too; refusing
            // here keeps the queue honest about what it holds.
            message = "A native receive descriptor requires nonzero item and category IDs.";
            return false;
        }

        int slot = (state.Count & (Slots - 1)) * EntrySize + EntriesOffset;
        Span<byte> entry = stackalloc byte[EntrySize];
        descriptor.ToBytes().CopyTo(entry);
        BinaryPrimitives.WriteUInt32LittleEndian(entry[4..], token);
        if (!memory.TryWrite(Address + (uint)slot, entry, out string? writeError))
        {
            message = writeError ?? "Could not stage a town receive queue entry.";
            return false;
        }

        byte published = unchecked((byte)(state.Count + 1));
        Span<byte> countByte = [published];
        if (!memory.TryWrite(Address + CountOffset, countByte, out writeError))
        {
            message = writeError ?? "Could not publish the town receive queue count.";
            return false;
        }

        var appended = new List<AzureDreamsQueuedItem>(state.InFlight)
        {
            new(descriptor, token),
        };
        updated = state with { Count = published, InFlight = appended };
        message = string.Empty;
        return true;
    }

    /// <summary>
    /// The next ordinary history index to consider staging.
    ///
    /// <para>An ordinary token IS <c>historyIndex + 1</c>, so the highest one
    /// in flight names the furthest index already handed to the game, and
    /// everything at or below the durable cursor is delivered. Taking the max
    /// of the two - rather than counting entries - stays correct when the
    /// history contains indices that are never queued at all, which
    /// progressive keycards are: they cut the line ahead of the cursor and
    /// leave a gap behind them.</para>
    ///
    /// <para>Deriving this from the live queue rather than from client-side
    /// bookkeeping is what lets a client restart, or a town reload that wiped
    /// the slab, reconcile with no saved state at all.</para>
    /// </summary>
    public static uint NextOrdinaryIndex(
        AzureDreamsTownReceiveQueueState state,
        uint durableCursor)
    {
        uint next = durableCursor;
        foreach (AzureDreamsQueuedItem item in state.InFlight)
        {
            if (!IsGiftToken(item.Token) && item.Token > next)
                next = item.Token;
        }

        return next;
    }

    public static bool HasOrdinaryInFlight(AzureDreamsTownReceiveQueueState state)
    {
        foreach (AzureDreamsQueuedItem item in state.InFlight)
        {
            if (!IsGiftToken(item.Token))
                return true;
        }

        return false;
    }

    // The previous poll's view of the consume cursor and what sat in front of
    // it. Delivery is observed by watching `head` cross an entry, which is
    // the only evidence that survives without asking the game.
    private static byte? _lastHead;
    private static uint[] _lastInFlight = [];

    /// <summary>
    /// The highest ORDINARY token the game has consumed since the last call,
    /// or 0 if none.
    ///
    /// <para>The game commits each ordinary token to the durable receive
    /// cursor as the item lands, and that commit is authoritative - it
    /// survives a client restart, which this observation cannot. But it was
    /// also the ONLY thing advancing the cursor on the town path, so a commit
    /// that failed to stick left the cursor behind and the tower re-delivered
    /// everything Nada had already handed over (the 2026-08-01 duplication).
    /// Folding the observed consumption forward makes the two independent:
    /// either mechanism alone is now enough.</para>
    ///
    /// <para>Safe because it is derived from `head` CROSSING an entry, and
    /// head advances only after that entry reached storage (or was rejected
    /// as invalid, which equally must not be re-delivered). A slab reset
    /// sends head backwards instead of forwards and is excluded by the
    /// distance check, so a wiped queue is re-offered rather than falsely
    /// marked delivered.</para>
    /// </summary>
    /// <summary>
    /// The highest ordinary token the game has actually delivered, read out of
    /// the record itself rather than inferred from having watched it happen.
    ///
    /// <para><b>Why this exists.</b> The delta observer below can only report
    /// consumption it saw between two polls, and it clamps what it reports to
    /// the entries it had recorded: <c>Math.Min(advanced, _lastInFlight.Length)</c>.
    /// Entries the client appends AFTER a poll's snapshot are not in that list,
    /// so if the game consumes them before the next poll, the excess is
    /// silently discarded and the cursor folds short. Everything short-folded
    /// is still "owed", and the tower mailbox re-delivers it on entry - which
    /// is exactly the duplicated Nada receive. The window opens whenever the
    /// client is appending while she is draining, which a stale lock (timed
    /// out after thirty seconds and appended through) makes routine.</para>
    ///
    /// <para><b>Why the record can answer.</b> The delivery routine never
    /// erases an entry; it only advances <c>head</c> past one, and only after
    /// the item is safely stored. So the slots outside the live occupancy still
    /// name what was delivered, and the highest ordinary token among them is
    /// the delivered-through watermark - with no cross-poll state, which means
    /// it also survives a client restart and a DuckStation reattach.</para>
    ///
    /// <para><b>The invariant it rests on:</b> a non-zero entry outside the
    /// live occupancy has been delivered. Two places could otherwise leave a
    /// non-zero entry there that never was, and both now erase instead:
    /// <see cref="TryEnsureInitialized"/> and <see cref="TryRecallInFlight"/>.
    /// Callers bound the result as well - a token is <c>historyIndex + 1</c>,
    /// and the client can never have more than <see cref="Slots"/> entries
    /// outstanding past the cursor.</para>
    /// </summary>
    public static bool TryReadDeliveredThrough(
        IEmulatorMemory memory,
        AzureDreamsTownReceiveQueueState state,
        out uint deliveredThrough,
        out string message)
    {
        deliveredThrough = 0;
        message = string.Empty;
        if (!state.Present)
            return true;

        // The slots the live occupancy does not cover, walking back from the
        // entry the game consumed most recently.
        int free = Slots - state.InFlight.Count;
        Span<byte> entry = stackalloc byte[EntrySize];
        for (int step = 1; step <= free; step++)
        {
            int slot = unchecked((byte)(state.Head - step)) & (Slots - 1);
            if (!memory.TryRead(
                    Address + (uint)(EntriesOffset + slot * EntrySize),
                    entry,
                    out string? readError))
            {
                message = readError ?? "Could not read a delivered receive queue entry.";
                return false;
            }

            uint token = BinaryPrimitives.ReadUInt32LittleEndian(entry[4..]);
            // Zero is an erased slot; a gift never touches the receive cursor.
            if (token == 0 || IsGiftToken(token))
                continue;
            if (token > deliveredThrough)
                deliveredThrough = token;
        }

        return true;
    }

    /// <summary>
    /// Hand everything still queued back, so the tower can deliver it natively
    /// instead of it waiting for a town trip.
    ///
    /// <para>Erases the entries BEFORE pulling <c>count</c> back, which is the
    /// order that fails safely in both directions. Erase-first and then die,
    /// and the game finds invalid entries it consumes and skips - the items
    /// are still owed by the cursor, so nothing is lost. Pull <c>count</c>
    /// back first and then die, and undelivered entries are left sitting
    /// outside the occupancy where <see cref="TryReadDeliveredThrough"/> would
    /// read them as delivered - which loses items outright.</para>
    /// </summary>
    public static bool TryRecallInFlight(
        IEmulatorMemory memory,
        AzureDreamsTownReceiveQueueState state,
        out AzureDreamsTownReceiveQueueState updated,
        out string message)
    {
        updated = state;
        message = string.Empty;
        if (!state.Present)
            return true;

        // The lock and the result belong to Nada's conversation, and in the
        // tower there is no conversation - she is not loaded and cannot race
        // this, the same argument that makes the recall itself safe. Clearing
        // them here means a conversation that ended without reaching an unlock
        // cannot follow the player across a mode change, where it used to cost
        // thirty seconds of refused appends on the next town arrival and left
        // a stale `result` that made the queue hard to read in a dump. Done
        // unconditionally: a stuck lock with an empty queue is the exact case
        // an in-flight test would miss.
        Span<byte> clear = [0];
        if (!memory.TryWrite(Address + LockOffset, clear, out string? lockError))
        {
            message = lockError ?? "Could not clear the town receive lock.";
            return false;
        }
        if (!memory.TryWrite(Address + ResultOffset, clear, out string? resultError))
        {
            message = resultError ?? "Could not clear the town receive result.";
            return false;
        }
        updated = state with { Locked = false, Result = ResultIdle };

        if (state.InFlight.Count == 0)
            return true;
        state = updated;

        Span<byte> blank = stackalloc byte[EntrySize];
        blank.Clear();
        for (int index = 0; index < state.InFlight.Count; index++)
        {
            int slot = unchecked((byte)(state.Head + index)) & (Slots - 1);
            if (!memory.TryWrite(
                    Address + (uint)(EntriesOffset + slot * EntrySize),
                    blank,
                    out string? entryError))
            {
                message = entryError ?? "Could not erase a recalled receive queue entry.";
                return false;
            }
        }

        Span<byte> countByte = [state.Head];
        if (!memory.TryWrite(Address + CountOffset, countByte, out string? writeError))
        {
            message = writeError ?? "Could not recall queued items for tower delivery.";
            return false;
        }

        updated = state with { Count = state.Head, InFlight = [] };
        return true;
    }

    public static uint ObserveConsumedOrdinary(AzureDreamsTownReceiveQueueState state)
    {
        uint[] current = new uint[state.InFlight.Count];
        for (int index = 0; index < current.Length; index++)
            current[index] = state.InFlight[index].Token;

        uint highest = 0;
        if (_lastHead is byte previousHead)
        {
            int advanced = unchecked((byte)(state.Head - previousHead));
            // 0 = nothing moved. Above the slot count = the queue was reset
            // (head went back to zero), not consumed.
            if (advanced >= 1 && advanced <= Slots)
            {
                int consumed = Math.Min(advanced, _lastInFlight.Length);
                for (int index = 0; index < consumed; index++)
                {
                    uint token = _lastInFlight[index];
                    if (!IsGiftToken(token) && token > highest)
                        highest = token;
                }
            }
        }

        _lastHead = state.Head;
        _lastInFlight = current;
        return highest;
    }

    internal static void ResetObservationForTest()
    {
        _lastHead = null;
        _lastInFlight = [];
    }

    public static bool ContainsToken(
        AzureDreamsTownReceiveQueueState state,
        uint token)
    {
        foreach (AzureDreamsQueuedItem item in state.InFlight)
        {
            if (item.Token == token)
                return true;
        }

        return false;
    }
}
