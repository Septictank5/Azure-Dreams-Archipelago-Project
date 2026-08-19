using System.Buffers.Binary;
using Adap.Client.Emulators;

namespace Adap.Client.Games;

internal enum AzureDreamsReceiveProgress
{
    Queued,
    Waiting,
    InventoryFull,
    Delivered,
}

internal static class AzureDreamsMailbox
{
    public const uint MemoryTopAddress = 0x8008_0a70;
    // Carve retraction 2026-08-01: the mailbox moved out of what is
    // vanilla stack territory (SP 0x801FFFF0, growing down) so the
    // memory-top carve could be undone. docs/carve-retraction-plan.md.
    public const uint Address = 0x801d_a540;
    public const int Size = 0x100;
    // Vanilla again. The effect pool was filling to ceiling-8 at the
    // mix-magic crash; the carve was starving it.
    public const uint ExpectedPatchedMemoryTop = 0x0020_0000;
    public const uint LegacyPatchedMemoryTop = 0x001f_e000;

    public const uint Magic = 0x5041_4441; // ASCII "ADAP" in little-endian RAM.
    public const ushort ProtocolVersion = 3;
    // Asks the game for the pickup presentation.
    //
    // This used to be bit 7 of the staged descriptor's flags byte, which is
    // also the game's own "unidentified" bit. Every staged descriptor had that
    // bit rewritten here and then cleared again by the dispatcher's `sll 1 /
    // srl 1` before the descriptor reached the inventory, so equipment could
    // never be delivered unappraised - the protocol was consuming a field that
    // belongs to the item.
    //
    // It has its own mailbox word now. The flags byte is the item's alone.
    // `patch.MAILBOX_RECEIVE_PRESENTATION_OFFSET` and the dispatcher's load at
    // 0x801D9BEC read the same word.
    public const int ReceivePresentationOffset = 0xac;
    public const uint ReceivePresentationRequested = 1;
    public const uint ReceivePresentationSuppressed = 0;

    public const int MagicOffset = 0x00;
    public const int ProtocolVersionOffset = 0x04;
    public const int StructureSizeOffset = 0x06;
    public const int ClientHeartbeatOffset = 0x08;
    public const int GameHeartbeatOffset = 0x0c;
    public const int ClientFlagsOffset = 0x10;
    public const int GameFlagsOffset = 0x14;
    public const int ElevatorClearanceOffset = 0x18;
    public const int GameMessageOffset = 0x40;
    public const int GameMessageSize = 0x40;
    // +0x80 (16 B, "outstanding floor locations") and +0x90 (16 B, "collected
    // floor-location request mirror") are RETIRED as of ADSV v4 (2026-08-15).
    // No game code ever read either - the spawner and the collect hook read
    // the save-backed journal itself - and the 40-byte v4 journal would not
    // fit. The words are still zeroed by the mailbox finalizer and are left
    // unused; nothing may reuse them without checking that finalizer.
    public const int RetiredFloorLocationFieldsOffset = 0x80;
    public const int RetiredFloorLocationFieldsSize = 0x1c;
    public const int ReceiveRequestSequenceOffset = 0x9c;
    public const int ReceiveDescriptorOffset = 0xa0;
    public const int ReceiveAckSequenceOffset = 0xa4;
    public const int ReceiveStatusOffset = 0xa8;

    public const uint ReceiveStatusIdle = 0;
    public const uint ReceiveStatusPending = 1;
    public const uint ReceiveStatusInventoryFull = 2;
    public const uint ReceiveStatusDelivered = 3;
    public const uint ReceiveStatusInvalid = 4;

    public const int TowerFloorCount = AzureDreamsReceiveState.TowerFloorCount;

    private static readonly byte[] LockedElevatorMessageBytes =
    [
        0x51,                                           // Battle-font text marker.
        0x1c, 0x0b, 0x02,                               // The
        0x01,                                           // space
        0x02, 0x0d, 0x02, 0x17, 0x03, 0x04, 0x07, 0x09, // elevator
        0x01,                                           // space
        0x08, 0x06,                                     // is
        0x01,                                           // space
        0x0d, 0x07, 0x0c, 0x19, 0x02, 0x0a,             // locked
        0x0e,                                           // period
        0x00,                                           // terminator
    ];

    private static readonly byte[] MultiworldSendTestMessageBytes =
    [
        // "Sent Master Sword to Player" in the compact battle-font alphabet.
        0x51,
        0x35, 0x02, 0x05, 0x04,
        0x01,
        0x24, 0x03, 0x06, 0x04, 0x02, 0x09,
        0x01,
        0x35, 0x11, 0x07, 0x09, 0x0a,
        0x01,
        0x04, 0x07,
        0x01,
        0x21, 0x0d, 0x03, 0x1a, 0x02, 0x09,
        0x00,                   // Exit compact mode.
        0x82, 0x54,             // Full-width Shift-JIS 5.
        0x81, 0x44,             // Full-width Shift-JIS period.
        0x00,                   // End the raw text segment.
    ];

    public static ReadOnlySpan<byte> LockedElevatorMessage => LockedElevatorMessageBytes;
    public static ReadOnlySpan<byte> MultiworldSendTestMessage => MultiworldSendTestMessageBytes;

    public const byte MaximumElevatorClearance = 8;

    public static AzureDreamsItemDescriptor TestReceiveDescriptor => new(17, 4, 1, 0);

    public static bool TryDetect(
        IEmulatorMemory memory,
        out bool detected,
        out string message)
    {
        detected = false;
        Span<byte> header = stackalloc byte[StructureSizeOffset + sizeof(ushort)];
        if (!memory.TryRead(Address, header, out string? readError))
        {
            message = readError ?? "Could not probe the tower mailbox.";
            return false;
        }

        uint magic = BinaryPrimitives.ReadUInt32LittleEndian(header);
        if (magic != Magic)
        {
            message = string.Empty;
            return true;
        }

        detected = true;
        ushort version = BinaryPrimitives.ReadUInt16LittleEndian(
            header[ProtocolVersionOffset..]);
        ushort structureSize = BinaryPrimitives.ReadUInt16LittleEndian(
            header[StructureSizeOffset..]);
        if (version != ProtocolVersion || structureSize != Size)
        {
            message = $"Tower mailbox protocol mismatch: expected v{ProtocolVersion}/0x{Size:x}, " +
                $"observed v{version}/0x{structureSize:x}.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    public static bool TryInitialize(IEmulatorMemory memory, byte elevatorClearance, out string message)
    {
        if (elevatorClearance > MaximumElevatorClearance)
        {
            message = $"Elevator clearance must be between 0 and {MaximumElevatorClearance}.";
            return false;
        }

        if (!TryRequirePatchedBuild(memory, out message))
            return false;

        byte[] expected = CreateInitializedBytes(elevatorClearance);
        if (!memory.TryWrite(Address, expected, out string? writeError))
        {
            message = writeError ?? "Mailbox write failed.";
            return false;
        }

        byte[] observed = new byte[Size];
        if (!memory.TryRead(Address, observed, out string? readError))
        {
            message = readError ?? "Mailbox read-back failed.";
            return false;
        }

        for (int i = 0; i < expected.Length; i++)
        {
            if (observed[i] == expected[i])
                continue;

            message = $"Mailbox read-back mismatch at +0x{i:x2}: expected 0x{expected[i]:x2}, observed 0x{observed[i]:x2}.";
            return false;
        }

        message = $"Initialized protocol v{ProtocolVersion} at 0x{Address:x8} with elevator clearance {elevatorClearance}.";
        return true;
    }

    public static bool TryRequirePatchedBuild(IEmulatorMemory memory, out string message)
    {
        Span<byte> valueBytes = stackalloc byte[sizeof(uint)];
        if (!memory.TryRead(MemoryTopAddress, valueBytes, out string? error))
        {
            message = error ?? "Could not read the game's memory-top value.";
            return false;
        }

        uint memoryTop = BinaryPrimitives.ReadUInt32LittleEndian(valueBytes);
        if (memoryTop != ExpectedPatchedMemoryTop && memoryTop != LegacyPatchedMemoryTop)
        {
            message = $"Refusing mailbox access: expected patched memory top 0x{ExpectedPatchedMemoryTop:x8} " +
                $"(seeded) or 0x{LegacyPatchedMemoryTop:x8} (legacy test), observed 0x{memoryTop:x8}.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    public static bool TryReadElevatorClearance(
        IEmulatorMemory memory,
        out byte elevatorClearance,
        out string message)
    {
        elevatorClearance = 0;
        if (!TryRequirePatchedBuild(memory, out message))
            return false;

        Span<byte> header = stackalloc byte[ElevatorClearanceOffset + 1];
        if (!memory.TryRead(Address, header, out string? readError))
        {
            message = readError ?? "Could not read the AP mailbox.";
            return false;
        }

        uint magic = BinaryPrimitives.ReadUInt32LittleEndian(header[MagicOffset..]);
        ushort version = BinaryPrimitives.ReadUInt16LittleEndian(header[ProtocolVersionOffset..]);
        ushort structureSize = BinaryPrimitives.ReadUInt16LittleEndian(header[StructureSizeOffset..]);
        if (magic != Magic || version != ProtocolVersion || structureSize != Size)
        {
            message = "The AP mailbox is not initialized for the expected protocol version.";
            return false;
        }

        elevatorClearance = header[ElevatorClearanceOffset];
        if (elevatorClearance > MaximumElevatorClearance)
        {
            message = $"Mailbox elevator clearance {elevatorClearance} is outside the supported range.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    public static bool TrySetElevatorClearance(
        IEmulatorMemory memory,
        byte elevatorClearance,
        out string message)
    {
        if (elevatorClearance > MaximumElevatorClearance)
        {
            message = $"Elevator clearance must be between 0 and {MaximumElevatorClearance}.";
            return false;
        }

        Span<byte> memoryTopBytes = stackalloc byte[sizeof(uint)];
        if (!memory.TryRead(MemoryTopAddress, memoryTopBytes, out string? memoryTopError))
        {
            message = memoryTopError ?? "Could not read the game's memory-top value.";
            return false;
        }

        uint memoryTop = BinaryPrimitives.ReadUInt32LittleEndian(memoryTopBytes);
        if (memoryTop == ExpectedPatchedMemoryTop)
        {
            // Current generated builds use the save-backed 32-bit keycard level
            // as their authority. The tower mailbox is only a live mirror and
            // is ordinary town stack memory while the tower overlay is absent.
            return AzureDreamsReceiveState.TrySetProgressiveKeycardLevel(
                memory,
                elevatorClearance,
                out message);
        }

        if (memoryTop != LegacyPatchedMemoryTop)
        {
            message = $"Refusing clearance access: expected patched memory top " +
                $"0x{ExpectedPatchedMemoryTop:x8} (seeded) or 0x{LegacyPatchedMemoryTop:x8} " +
                $"(legacy test), observed 0x{memoryTop:x8}.";
            return false;
        }

        if (!TryRequireInitializedMailbox(memory, out message))
            return false;

        byte[] requested = [elevatorClearance];
        if (!memory.TryWrite(Address + ElevatorClearanceOffset, requested, out string? writeError))
        {
            message = writeError ?? "Could not update the elevator-clearance byte.";
            return false;
        }

        Span<byte> observed = stackalloc byte[1];
        if (!memory.TryRead(Address + ElevatorClearanceOffset, observed, out string? readError))
        {
            message = readError ?? "Could not read back the elevator-clearance byte.";
            return false;
        }

        if (observed[0] != elevatorClearance)
        {
            message = $"Elevator-clearance read-back mismatch: expected {elevatorClearance}, observed {observed[0]}.";
            return false;
        }

        message = $"Elevator clearance is now {elevatorClearance}.";
        return true;
    }

    public static bool TryWriteGameMessage(
        IEmulatorMemory memory,
        ReadOnlySpan<byte> encodedMessage,
        out string message)
    {
        if (encodedMessage.IsEmpty || encodedMessage.Length > GameMessageSize)
        {
            message = $"The encoded game message must contain 1-{GameMessageSize} bytes.";
            return false;
        }

        if (encodedMessage[^1] != 0)
        {
            message = "The encoded game message must end with a zero terminator.";
            return false;
        }

        if (!TryRequireInitializedMailbox(memory, out message))
            return false;

        byte[] requested = new byte[GameMessageSize];
        encodedMessage.CopyTo(requested);
        if (!memory.TryWrite(Address + GameMessageOffset, requested, out string? writeError))
        {
            message = writeError ?? "Could not write the game-message buffer.";
            return false;
        }

        Span<byte> observed = stackalloc byte[GameMessageSize];
        if (!memory.TryRead(Address + GameMessageOffset, observed, out string? readError))
        {
            message = readError ?? "Could not read back the game-message buffer.";
            return false;
        }

        if (!observed.SequenceEqual(requested))
        {
            message = "The game-message buffer did not match on read-back.";
            return false;
        }

        message = $"Loaded {encodedMessage.Length} encoded bytes into the game-message buffer.";
        return true;
    }

    public static bool TryQueueTestReceive(
        IEmulatorMemory memory,
        out uint requestSequence,
        out string message)
    {
        requestSequence = 0;
        if (!TryRequireLegacyReceiveTestBuild(memory, out message))
            return false;

        return TryQueueReceive(
            memory,
            TestReceiveDescriptor,
            showPresentation: true,
            out requestSequence,
            out message);
    }

    public static bool TryQueueReceive(
        IEmulatorMemory memory,
        AzureDreamsItemDescriptor descriptor,
        out uint requestSequence,
        out string message) => TryQueueReceive(
            memory,
            descriptor,
            showPresentation: true,
            out requestSequence,
            out message);

    public static bool TryQueueReceive(
        IEmulatorMemory memory,
        AzureDreamsItemDescriptor descriptor,
        bool showPresentation,
        out uint requestSequence,
        out string message)
    {
        requestSequence = 0;
        if (!TryReadReceiveStatus(
                memory,
                out uint existingRequest,
                out uint existingAck,
                out _,
                out _,
                out message))
        {
            return false;
        }
        if (existingRequest != existingAck)
        {
            message = $"Receive request {existingRequest} is still pending " +
                $"(acknowledged through {existingAck}).";
            return false;
        }

        requestSequence = existingAck == uint.MaxValue ? 1 : existingAck + 1;
        return TryQueueReceive(
            memory,
            descriptor,
            requestSequence,
            showPresentation,
            out message);
    }

    public static bool TryQueueReceive(
        IEmulatorMemory memory,
        AzureDreamsItemDescriptor descriptor,
        uint requestSequence,
        out string message) => TryQueueReceive(
            memory,
            descriptor,
            requestSequence,
            showPresentation: true,
            out message);

    public static bool TryQueueReceive(
        IEmulatorMemory memory,
        AzureDreamsItemDescriptor descriptor,
        uint requestSequence,
        bool showPresentation,
        out string message)
    {
        if (requestSequence == 0)
        {
            message = "Receive request sequence zero is reserved for an empty mailbox.";
            return false;
        }
        if (descriptor.ItemId == 0 || descriptor.Category == 0)
        {
            message = "A native receive descriptor requires nonzero item and category IDs.";
            return false;
        }

        if (!TryRequireInitializedMailbox(memory, out message))
            return false;

        Span<byte> current = stackalloc byte[16];
        if (!memory.TryRead(Address + ReceiveRequestSequenceOffset, current, out string? readError))
        {
            message = readError ?? "Could not read the receive mailbox.";
            return false;
        }

        uint existingRequest = BinaryPrimitives.ReadUInt32LittleEndian(current);
        uint existingAck = BinaryPrimitives.ReadUInt32LittleEndian(current[8..]);
        uint existingStatus = BinaryPrimitives.ReadUInt32LittleEndian(current[12..]);
        if (existingRequest != existingAck)
        {
            message = $"Receive request {existingRequest} is still pending " +
                $"({DescribeReceiveStatus(existingStatus)}; acknowledged through {existingAck}).";
            return false;
        }
        if (requestSequence == existingAck)
        {
            message = $"Receive request sequence {requestSequence} is already acknowledged.";
            return false;
        }

        // The descriptor is staged exactly as the item is - no flag of ours
        // rides in it. Presentation goes in its own word, written before the
        // sequence is published for the same reason the descriptor is.
        byte[] descriptorBytes = descriptor.ToBytes();
        if (!memory.TryWrite(Address + ReceiveDescriptorOffset, descriptorBytes, out string? writeError))
        {
            message = writeError ?? "Could not stage the receive descriptor.";
            return false;
        }

        Span<byte> presentation = stackalloc byte[sizeof(uint)];
        BinaryPrimitives.WriteUInt32LittleEndian(
            presentation,
            showPresentation ? ReceivePresentationRequested : ReceivePresentationSuppressed);
        if (!memory.TryWrite(Address + ReceivePresentationOffset, presentation, out writeError))
        {
            message = writeError ?? "Could not stage the receive presentation request.";
            return false;
        }

        Span<byte> word = stackalloc byte[sizeof(uint)];
        BinaryPrimitives.WriteUInt32LittleEndian(word, ReceiveStatusPending);
        if (!memory.TryWrite(Address + ReceiveStatusOffset, word, out writeError))
        {
            message = writeError ?? "Could not mark the receive request pending.";
            return false;
        }

        // Publish the sequence last. The patched game never observes a new
        // request before its descriptor and status are complete.
        BinaryPrimitives.WriteUInt32LittleEndian(word, requestSequence);
        if (!memory.TryWrite(Address + ReceiveRequestSequenceOffset, word, out writeError))
        {
            message = writeError ?? "Could not publish the receive request sequence.";
            return false;
        }

        Span<byte> observed = stackalloc byte[16];
        if (!memory.TryRead(Address + ReceiveRequestSequenceOffset, observed, out readError))
        {
            message = readError ?? "Could not read back the staged receive request.";
            return false;
        }

        uint observedStatus = BinaryPrimitives.ReadUInt32LittleEndian(observed[12..]);
        if (BinaryPrimitives.ReadUInt32LittleEndian(observed) != requestSequence ||
            !observed[4..8].SequenceEqual(descriptorBytes) ||
            observedStatus is not (ReceiveStatusPending or ReceiveStatusInventoryFull or ReceiveStatusDelivered))
        {
            message = "The staged receive request did not match on read-back.";
            return false;
        }

        message = $"Queued receive request {requestSequence}: descriptor " +
            $"[{descriptor.ItemId}, {descriptor.Category}, {descriptor.Quality}, 0x{descriptor.Flags:x2}], " +
            $"presentation {(showPresentation ? "enabled" : "suppressed")}.";
        return true;
    }

    /// <summary>
    /// Sequences below this are ordinary receive-cursor tokens; at or above
    /// it they belong to the gift stream. This is the historical gift
    /// detection floor (<c>GiftCorruptedCursorFloor</c> in the client), a
    /// deliberate superset of the live <c>0x80000000</c> gift base so that
    /// sequences staged under the old base are still treated as gifts.
    /// </summary>
    private const uint OrdinarySequenceCeiling = 0x4000_0000;

    /// <summary>
    /// Cancels an undelivered ordinary receive request left in the resident
    /// mailbox, handing the item back to the town path.
    ///
    /// <para>The mirror of <c>AzureDreamsTownReceiveQueue.TryRecallInFlight</c>:
    /// that recalls the town queue when the player enters the tower; this
    /// recalls the tower mailbox when the player is in town. Only call it
    /// from town, for the same reason the queue recall is only called from
    /// the tower - the other side's consumer is not running, so nothing can
    /// race the cancellation.</para>
    ///
    /// <para>Why it exists: a request staged in the tower can outlive the
    /// trip - never delivered because Koh never reached ordinary idle, or
    /// wedged on inventory-full - and the resident mailbox carries it across
    /// the crossing. Nada then delivers the same history index in town, and
    /// on the next tower entry the dispatcher would retry the stale request
    /// and deliver it a second time. Until 2026-08-05 the game-side
    /// cursor-commit stub then also rolled the durable cursor back to the
    /// stale sequence, re-delivering everything Nada had handed over - the
    /// Nada receive duplication. The stub is removed from the payload; this
    /// recall removes the single duplicate that remained.</para>
    ///
    /// <para>The request word is rewritten to the acknowledged sequence
    /// FIRST, then the status is returned to idle. The dispatcher acts only
    /// on <c>request != ack</c> with a pending or storage-full status - the
    /// same pair the staging order relies on - so either partial outcome is
    /// inert. The item itself is still owed by the durable cursor, which
    /// never advances past an undelivered request.</para>
    ///
    /// <para>Gift-range sequences are left alone: the gift service owns that
    /// stream and confirms deliveries against its own durable watermark, so
    /// cancelling one here would race its tracking rather than help it.</para>
    /// </summary>
    public static bool TryRecallInFlightReceive(
        IEmulatorMemory memory,
        out uint recalledSequence,
        out string message)
    {
        recalledSequence = 0;
        if (!TryDetect(memory, out bool detected, out message))
            return false;
        if (!detected)
        {
            message = string.Empty;
            return true;
        }

        if (!TryReadReceiveStatus(
                memory,
                out uint request,
                out uint acknowledged,
                out _,
                out _,
                out message))
        {
            return false;
        }
        if (request == acknowledged || request >= OrdinarySequenceCeiling)
        {
            message = string.Empty;
            return true;
        }

        Span<byte> word = stackalloc byte[sizeof(uint)];
        BinaryPrimitives.WriteUInt32LittleEndian(word, acknowledged);
        if (!memory.TryWrite(Address + ReceiveRequestSequenceOffset, word, out string? writeError))
        {
            message = writeError ?? "Could not recall the in-flight receive request.";
            return false;
        }
        BinaryPrimitives.WriteUInt32LittleEndian(word, ReceiveStatusIdle);
        if (!memory.TryWrite(Address + ReceiveStatusOffset, word, out writeError))
        {
            message = writeError ?? "Could not idle the recalled receive request.";
            return false;
        }

        Span<byte> observed = stackalloc byte[sizeof(uint)];
        if (!memory.TryRead(Address + ReceiveRequestSequenceOffset, observed, out string? readError) ||
            BinaryPrimitives.ReadUInt32LittleEndian(observed) != acknowledged)
        {
            message = readError ?? "The recalled receive request did not match on read-back.";
            return false;
        }

        recalledSequence = request;
        message = string.Empty;
        return true;
    }

    public static bool TrySynchronizeReceive(
        IEmulatorMemory memory,
        uint expectedSequence,
        AzureDreamsItemDescriptor expectedDescriptor,
        out AzureDreamsReceiveProgress progress,
        out string message) => TrySynchronizeReceive(
            memory,
            expectedSequence,
            expectedDescriptor,
            showPresentation: true,
            out progress,
            out message);

    public static bool TrySynchronizeReceive(
        IEmulatorMemory memory,
        uint expectedSequence,
        AzureDreamsItemDescriptor expectedDescriptor,
        bool showPresentation,
        out AzureDreamsReceiveProgress progress,
        out string message)
    {
        progress = AzureDreamsReceiveProgress.Waiting;
        if (expectedSequence == 0)
        {
            message = "Receive request sequence zero is reserved for an empty mailbox.";
            return false;
        }

        if (!TryReadReceiveStatus(
                memory,
                out uint request,
                out uint acknowledged,
                out uint status,
                out AzureDreamsItemDescriptor descriptor,
                out message))
        {
            return false;
        }

        if (request == expectedSequence)
        {
            // Presentation no longer rides in the descriptor, so what was
            // staged should match the item byte for byte - including flags,
            // which are the item's own.
            if (descriptor != expectedDescriptor)
            {
                message = $"Receive request {expectedSequence} contains descriptor " +
                    $"[{descriptor.ItemId}, {descriptor.Category}, {descriptor.Quality}, 0x{descriptor.Flags:x2}] " +
                    "instead of the descriptor expected from server history.";
                return false;
            }

            if (acknowledged == expectedSequence)
            {
                if (status == ReceiveStatusDelivered)
                {
                    progress = AzureDreamsReceiveProgress.Delivered;
                    message = $"Native receive request {expectedSequence} was delivered.";
                    return true;
                }

                message = status == ReceiveStatusInvalid
                    ? $"The game rejected native receive request {expectedSequence} as an invalid descriptor."
                    : $"Native receive request {expectedSequence} was acknowledged with unexpected status " +
                        $"{DescribeReceiveStatus(status)}.";
                return false;
            }

            if (status == ReceiveStatusPending)
            {
                progress = AzureDreamsReceiveProgress.Waiting;
                message = $"Native receive request {expectedSequence} is waiting for a safe player state.";
                return true;
            }
            if (status == ReceiveStatusInventoryFull)
            {
                progress = AzureDreamsReceiveProgress.InventoryFull;
                message = $"Native receive request {expectedSequence} is waiting for inventory space.";
                return true;
            }

            message = $"Unacknowledged native receive request {expectedSequence} has unexpected status " +
                $"{DescribeReceiveStatus(status)}.";
            return false;
        }

        if (request != acknowledged)
        {
            message = $"Mailbox request {request} is still pending while server history expects " +
                $"request {expectedSequence}.";
            return false;
        }

        if (!TryQueueReceive(
                memory,
                expectedDescriptor,
                expectedSequence,
                showPresentation,
                out message))
            return false;

        progress = AzureDreamsReceiveProgress.Queued;
        return true;
    }

    public static bool TryReadReceiveStatus(
        IEmulatorMemory memory,
        out uint requestSequence,
        out uint acknowledgedSequence,
        out uint status,
        out AzureDreamsItemDescriptor descriptor,
        out string message)
    {
        requestSequence = 0;
        acknowledgedSequence = 0;
        status = ReceiveStatusIdle;
        descriptor = default;
        if (!TryRequireInitializedMailbox(memory, out message))
            return false;

        Span<byte> receive = stackalloc byte[16];
        if (!memory.TryRead(Address + ReceiveRequestSequenceOffset, receive, out string? readError))
        {
            message = readError ?? "Could not read the receive mailbox.";
            return false;
        }

        requestSequence = BinaryPrimitives.ReadUInt32LittleEndian(receive);
        descriptor = new AzureDreamsItemDescriptor(
            receive[4],
            receive[5],
            unchecked((sbyte)receive[6]),
            receive[7]);
        acknowledgedSequence = BinaryPrimitives.ReadUInt32LittleEndian(receive[8..]);
        status = BinaryPrimitives.ReadUInt32LittleEndian(receive[12..]);
        message = $"Receive request {requestSequence}, acknowledged {acknowledgedSequence}, " +
            $"status {DescribeReceiveStatus(status)}, descriptor " +
            $"[{descriptor.ItemId}, {descriptor.Category}, {descriptor.Quality}, 0x{descriptor.Flags:x2}].";
        return true;
    }

    public static string DescribeReceiveStatus(uint status) => status switch
    {
        ReceiveStatusIdle => "idle",
        ReceiveStatusPending => "pending",
        ReceiveStatusInventoryFull => "inventory full",
        ReceiveStatusDelivered => "delivered",
        ReceiveStatusInvalid => "invalid descriptor",
        _ => $"unknown ({status})",
    };

    private static bool TryRequireInitializedMailbox(IEmulatorMemory memory, out string message)
    {
        if (!TryRequirePatchedBuild(memory, out message))
            return false;

        Span<byte> header = stackalloc byte[StructureSizeOffset + sizeof(ushort)];
        if (!memory.TryRead(Address, header, out string? readError))
        {
            message = readError ?? "Could not read the AP mailbox header.";
            return false;
        }

        uint magic = BinaryPrimitives.ReadUInt32LittleEndian(header[MagicOffset..]);
        ushort version = BinaryPrimitives.ReadUInt16LittleEndian(header[ProtocolVersionOffset..]);
        ushort structureSize = BinaryPrimitives.ReadUInt16LittleEndian(header[StructureSizeOffset..]);
        if (magic != Magic || version != ProtocolVersion || structureSize != Size)
        {
            message = "The AP mailbox is not initialized for the expected protocol version.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    private static bool TryRequireLegacyReceiveTestBuild(IEmulatorMemory memory, out string message)
    {
        Span<byte> valueBytes = stackalloc byte[sizeof(uint)];
        if (!memory.TryRead(MemoryTopAddress, valueBytes, out string? error))
        {
            message = error ?? "Could not read the game's memory-top value.";
            return false;
        }

        uint memoryTop = BinaryPrimitives.ReadUInt32LittleEndian(valueBytes);
        if (memoryTop != LegacyPatchedMemoryTop)
        {
            message = "The development test-item button is limited to the focused native-receive test ROM.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    private static byte[] CreateInitializedBytes(byte elevatorClearance)
    {
        byte[] bytes = new byte[Size];
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(MagicOffset), Magic);
        BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(ProtocolVersionOffset), ProtocolVersion);
        BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(StructureSizeOffset), Size);
        bytes[ElevatorClearanceOffset] = elevatorClearance;
        LockedElevatorMessage.CopyTo(bytes.AsSpan(GameMessageOffset));
        return bytes;
    }
}
