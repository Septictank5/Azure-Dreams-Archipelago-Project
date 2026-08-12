using System.Buffers.Binary;
using System.Text;
using Adap.Client.Emulators;

namespace Adap.Client.Games;

internal static class AzureDreamsTownMailbox
{
    public const uint Address = 0x800f_c198;
    public const int Size = 0x20;
    public const uint Magic = 0x5254_4441; // ASCII "ADTR" in little-endian RAM.
    public const ushort ProtocolVersion = 2;

    public const uint NotificationStateAddress = 0x800f_c158;
    public const uint NotificationMessageAddress = 0x800f_c160;
    public const int NotificationMessageSize = 0x38;

    public const int MagicOffset = 0x00;
    public const int ProtocolVersionOffset = 0x04;
    public const int StructureSizeOffset = 0x06;
    public const int ReceiveRequestSequenceOffset = 0x08;
    public const int ReceiveDescriptorOffset = 0x0c;
    public const int ReceiveAckSequenceOffset = 0x10;
    public const int ReceiveStatusOffset = 0x14;
    public const int ReceiveDestinationOffset = 0x18;
    public const int StableFramesOffset = 0x1c;
    public const int RequestFlagsOffset = 0x1d;
    public const int IntroRestoreStateOffset = 0x1e;
    public const int IntroRestoreProtocolOffset = 0x1f;

    public const byte RequestFlagNotify = 0x01;

    public const uint DestinationNone = 0;
    public const uint DestinationInventory = 1;
    public const uint DestinationSafe = 2;

    private static readonly Encoding ShiftJis = CreateShiftJisEncoding();

    public static bool TryDetect(
        IEmulatorMemory memory,
        out bool detected,
        out string message)
    {
        detected = false;
        Span<byte> header = stackalloc byte[8];
        if (!memory.TryRead(Address, header, out string? readError))
        {
            message = readError ?? "Could not probe the town receive mailbox.";
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
            message = $"Town mailbox protocol mismatch: expected v{ProtocolVersion}/0x{Size:x}, " +
                $"observed v{version}/0x{structureSize:x}.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    public static bool TryQueueTestReceive(
        IEmulatorMemory memory,
        out uint requestSequence,
        out string message)
    {
        requestSequence = 0;
        if (!TryRequireInitialized(memory, out message))
            return false;

        if (!TryReadReceiveStatus(
                memory,
                out uint existingRequest,
                out uint existingAck,
                out _,
                out _,
                out _,
                out message))
        {
            return false;
        }
        if (existingRequest != existingAck)
        {
            message = $"Town receive request {existingRequest} is still pending " +
                $"(acknowledged through {existingAck}).";
            return false;
        }

        requestSequence = existingAck == uint.MaxValue ? 1 : existingAck + 1;
        return TryQueueReceive(
            memory,
            AzureDreamsMailbox.TestReceiveDescriptor,
            requestSequence,
            "Acid Rain Ball",
            showNotification: true,
            out message);
    }

    public static bool TryQueueReceive(
        IEmulatorMemory memory,
        AzureDreamsItemDescriptor descriptor,
        uint requestSequence,
        string itemName,
        bool showNotification,
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
        if (!TryRequireInitialized(memory, out message))
            return false;

        byte[]? notification = null;
        if (showNotification)
        {
            if (!TryEncodeNotification(itemName, out notification, out message))
                return false;
            if (!TryRequireNotificationBufferIdle(memory, out message))
                return false;
        }

        Span<byte> current = stackalloc byte[16];
        if (!memory.TryRead(
                Address + ReceiveRequestSequenceOffset,
                current,
                out string? readError))
        {
            message = readError ?? "Could not read the town receive mailbox.";
            return false;
        }

        uint existingRequest = BinaryPrimitives.ReadUInt32LittleEndian(current);
        uint existingAck = BinaryPrimitives.ReadUInt32LittleEndian(current[8..]);
        uint existingStatus = BinaryPrimitives.ReadUInt32LittleEndian(current[12..]);
        if (existingRequest != existingAck)
        {
            message = $"Town receive request {existingRequest} is still pending " +
                $"({DescribeReceiveStatus(existingStatus)}; " +
                $"acknowledged through {existingAck}).";
            return false;
        }
        if (requestSequence == existingAck)
        {
            message = $"Town receive request sequence {requestSequence} is already acknowledged.";
            return false;
        }

        byte[] descriptorBytes = descriptor.ToBytes();
        if (!memory.TryWrite(
                Address + ReceiveDescriptorOffset,
                descriptorBytes,
                out string? writeError))
        {
            message = writeError ?? "Could not stage the town receive descriptor.";
            return false;
        }

        if (notification is not null &&
            !memory.TryWrite(
                NotificationMessageAddress,
                notification,
                out writeError))
        {
            message = writeError ?? "Could not stage the town receive notification.";
            return false;
        }

        // Preserve +0x1e/+0x1f, which belong to the independent intro-restore
        // handshake and remain useful if only the client is restarted.
        Span<byte> state = stackalloc byte[
            IntroRestoreStateOffset - ReceiveStatusOffset];
        state.Clear();
        BinaryPrimitives.WriteUInt32LittleEndian(
            state,
            AzureDreamsMailbox.ReceiveStatusPending);
        state[RequestFlagsOffset - ReceiveStatusOffset] =
            showNotification ? RequestFlagNotify : (byte)0;
        if (!memory.TryWrite(Address + ReceiveStatusOffset, state, out writeError))
        {
            message = writeError ?? "Could not mark the town receive request pending.";
            return false;
        }

        // Publish sequence last so the game cannot observe a partial command.
        Span<byte> word = stackalloc byte[sizeof(uint)];
        BinaryPrimitives.WriteUInt32LittleEndian(word, requestSequence);
        if (!memory.TryWrite(
                Address + ReceiveRequestSequenceOffset,
                word,
                out writeError))
        {
            message = writeError ?? "Could not publish the town receive sequence.";
            return false;
        }

        Span<byte> observed = stackalloc byte[Size - ReceiveRequestSequenceOffset];
        if (!memory.TryRead(
                Address + ReceiveRequestSequenceOffset,
                observed,
                out readError))
        {
            message = readError ?? "Could not read back the town receive request.";
            return false;
        }

        uint observedStatus = BinaryPrimitives.ReadUInt32LittleEndian(observed[12..]);
        if (BinaryPrimitives.ReadUInt32LittleEndian(observed) != requestSequence ||
            !observed[4..8].SequenceEqual(descriptorBytes) ||
            observed[RequestFlagsOffset - ReceiveRequestSequenceOffset] !=
                (showNotification ? RequestFlagNotify : (byte)0) ||
            observedStatus is not (
                AzureDreamsMailbox.ReceiveStatusPending or
                AzureDreamsMailbox.ReceiveStatusInventoryFull or
                AzureDreamsMailbox.ReceiveStatusDelivered))
        {
            message = "The staged town receive request did not match on read-back.";
            return false;
        }

        message = $"Queued town receive request {requestSequence}: descriptor " +
            $"[{descriptor.ItemId}, {descriptor.Category}, {descriptor.Quality}, " +
            $"0x{descriptor.Flags:x2}], notification " +
            $"{(showNotification ? "enabled" : "suppressed")}.";
        return true;
    }

    public static bool TrySynchronizeReceive(
        IEmulatorMemory memory,
        uint expectedSequence,
        AzureDreamsItemDescriptor expectedDescriptor,
        string itemName,
        bool showNotification,
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
                out _,
                out message))
        {
            return false;
        }

        if (request == expectedSequence)
        {
            if (descriptor != expectedDescriptor)
            {
                message = $"Town receive request {expectedSequence} contains descriptor " +
                    $"[{descriptor.ItemId}, {descriptor.Category}, {descriptor.Quality}, " +
                    $"0x{descriptor.Flags:x2}] instead of the descriptor expected from server history.";
                return false;
            }
            // An acknowledged request is judged on status alone: the
            // dispatcher's delivery path clears the stable/presentation
            // halfword, so the notification flag (and with it the staged
            // message's relevance) does not survive delivery. Verifying
            // flags first made every delivered gift look corrupt, kept it
            // in flight, and re-queued it into the tower mailbox for a
            // duplicate delivery on the next mailbox reset.
            if (acknowledged == expectedSequence)
            {
                if (status == AzureDreamsMailbox.ReceiveStatusDelivered)
                {
                    progress = AzureDreamsReceiveProgress.Delivered;
                    message = $"Town receive request {expectedSequence} was delivered.";
                    return true;
                }

                message = status == AzureDreamsMailbox.ReceiveStatusInvalid
                    ? $"The game rejected town receive request {expectedSequence} as an invalid descriptor."
                    : $"Town receive request {expectedSequence} was acknowledged with unexpected status " +
                        $"{DescribeReceiveStatus(status)}.";
                return false;
            }

            if (!TryReadRequestFlags(memory, out byte requestFlags, out message))
                return false;

            byte expectedFlags = showNotification ? RequestFlagNotify : (byte)0;
            if (requestFlags != expectedFlags)
            {
                message = $"Town receive request {expectedSequence} has notification flags " +
                    $"0x{requestFlags:x2}; server history expects 0x{expectedFlags:x2}.";
                return false;
            }

            if (showNotification &&
                !TryVerifyNotification(memory, itemName, out message))
            {
                return false;
            }

            if (status == AzureDreamsMailbox.ReceiveStatusPending)
            {
                progress = AzureDreamsReceiveProgress.Waiting;
                message = $"Town receive request {expectedSequence} is waiting for a safe player state.";
                return true;
            }
            if (status == AzureDreamsMailbox.ReceiveStatusInventoryFull)
            {
                progress = AzureDreamsReceiveProgress.InventoryFull;
                message = $"Town receive request {expectedSequence} is waiting for inventory or safe space.";
                return true;
            }

            message = $"Unacknowledged town receive request {expectedSequence} has unexpected status " +
                $"{DescribeReceiveStatus(status)}.";
            return false;
        }

        if (request != acknowledged)
        {
            message = $"Town mailbox request {request} is still pending while server history expects " +
                $"request {expectedSequence}.";
            return false;
        }

        if (showNotification)
        {
            if (!TryReadNotificationActive(memory, out bool active, out message))
                return false;
            if (active)
            {
                progress = AzureDreamsReceiveProgress.Waiting;
                message = "Waiting for the prior town receive notification to close.";
                return true;
            }
        }

        if (!TryQueueReceive(
                memory,
                expectedDescriptor,
                expectedSequence,
                itemName,
                showNotification,
                out message))
        {
            return false;
        }

        progress = AzureDreamsReceiveProgress.Queued;
        return true;
    }

    public static bool TryReadReceiveStatus(
        IEmulatorMemory memory,
        out uint requestSequence,
        out uint acknowledgedSequence,
        out uint status,
        out AzureDreamsItemDescriptor descriptor,
        out uint destination,
        out string message)
    {
        requestSequence = 0;
        acknowledgedSequence = 0;
        status = AzureDreamsMailbox.ReceiveStatusIdle;
        descriptor = default;
        destination = DestinationNone;
        if (!TryRequireInitialized(memory, out message))
            return false;

        Span<byte> receive = stackalloc byte[Size - ReceiveRequestSequenceOffset];
        if (!memory.TryRead(
                Address + ReceiveRequestSequenceOffset,
                receive,
                out string? readError))
        {
            message = readError ?? "Could not read the town receive mailbox.";
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
        destination = BinaryPrimitives.ReadUInt32LittleEndian(receive[16..]);
        message = $"Town receive request {requestSequence}, acknowledged " +
            $"{acknowledgedSequence}, {DescribeReceiveStatus(status)}, " +
            $"destination {DescribeDestination(destination)}, descriptor " +
            $"[{descriptor.ItemId}, {descriptor.Category}, {descriptor.Quality}, " +
            $"0x{descriptor.Flags:x2}].";
        return true;
    }

    public static string DescribeDestination(uint destination) => destination switch
    {
        DestinationNone => "none",
        DestinationInventory => "inventory",
        DestinationSafe => "safe",
        _ => $"unknown ({destination})",
    };

    public static string DescribeReceiveStatus(uint status) => status switch
    {
        AzureDreamsMailbox.ReceiveStatusIdle => "idle",
        AzureDreamsMailbox.ReceiveStatusPending => "pending",
        AzureDreamsMailbox.ReceiveStatusInventoryFull => "inventory and safe full",
        AzureDreamsMailbox.ReceiveStatusDelivered => "delivered",
        AzureDreamsMailbox.ReceiveStatusInvalid => "invalid descriptor",
        _ => $"unknown ({status})",
    };

    public static bool TryEncodeNotification(
        string itemName,
        out byte[] notification,
        out string message)
    {
        notification = [];
        if (string.IsNullOrWhiteSpace(itemName))
        {
            message = "A town receive notification requires an item name.";
            return false;
        }

        // The dialogue drops the quality decoration on purpose: the
        // inventory (and Nada's menu) show the real charges or enchantment,
        // and the 56-byte buffer is better spent on the name itself.
        string displayName = StripQualitySuffix(itemName);

        // The slab's message buffer is a hard 0x38 bytes and full-width text
        // costs two bytes per character plus the wait/end control pair, so a
        // long name overflows the pretty form. Degrade the wording rather
        // than fail the delivery: the exact same candidate is chosen on
        // re-verification because the selection is deterministic in the
        // item name.
        foreach (string text in NotificationTextCandidates(displayName))
        {
            StringBuilder fullWidth = new(text.Length);
            foreach (char character in text)
            {
                fullWidth.Append(character switch
                {
                    ' ' => '\u3000',
                    >= '!' and <= '~' => (char)(character + 0xfee0),
                    _ => character,
                });
            }

            byte[] encoded = ShiftJis.GetBytes(fullWidth.ToString());
            if (encoded.Length + 2 > NotificationMessageSize)
                continue;

            notification = new byte[NotificationMessageSize];
            encoded.CopyTo(notification, 0);
            notification[encoded.Length] = 0x11;
            notification[encoded.Length + 1] = 0x01;
            message = string.Empty;
            return true;
        }

        message = $"Town receive text for {itemName} cannot fit the " +
            $"{NotificationMessageSize}-byte notification buffer in any form.";
        return false;
    }

    private static IEnumerable<string> NotificationTextCandidates(string itemName)
    {
        yield return $"Received {itemName}.";
        yield return $"Got {itemName}.";
        yield return itemName;
        // Last resort: hard-truncate to the buffer's 27-character budget.
        const int maximumCharacters = (NotificationMessageSize - 2) / 2;
        if (itemName.Length > maximumCharacters)
            yield return itemName[..maximumCharacters];
    }

    /// <summary>
    /// Drops a trailing charge count " (N)" or enchantment " +N"/" -N"
    /// from an item name. Names without a quality decoration pass through
    /// untouched.
    /// </summary>
    internal static string StripQualitySuffix(string itemName)
    {
        System.Text.RegularExpressions.Match match =
            System.Text.RegularExpressions.Regex.Match(
                itemName, @"^(.+?)(?: \(\d+\)| [+-]\d+)$");
        return match.Success ? match.Groups[1].Value : itemName;
    }

    private static bool TryVerifyNotification(
        IEmulatorMemory memory,
        string itemName,
        out string message)
    {
        if (!TryEncodeNotification(itemName, out byte[] expected, out message))
            return false;

        Span<byte> observed = stackalloc byte[NotificationMessageSize];
        if (!memory.TryRead(NotificationMessageAddress, observed, out string? readError))
        {
            message = readError ?? "Could not read the staged town receive notification.";
            return false;
        }
        if (!observed.SequenceEqual(expected))
        {
            message = "The staged town receive notification does not match server history.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    private static bool TryReadNotificationActive(
        IEmulatorMemory memory,
        out bool active,
        out string message)
    {
        active = false;
        Span<byte> state = stackalloc byte[sizeof(uint)];
        if (!memory.TryRead(NotificationStateAddress, state, out string? readError))
        {
            message = readError ?? "Could not inspect the town notification state.";
            return false;
        }

        active = BinaryPrimitives.ReadUInt32LittleEndian(state) != 0;
        message = string.Empty;
        return true;
    }

    private static bool TryRequireNotificationBufferIdle(
        IEmulatorMemory memory,
        out string message)
    {
        if (!TryReadNotificationActive(memory, out bool active, out message))
            return false;
        if (!active)
            return true;

        message = "The prior town receive notification is still open.";
        return false;
    }

    private static bool TryReadRequestFlags(
        IEmulatorMemory memory,
        out byte requestFlags,
        out string message)
    {
        requestFlags = 0;
        Span<byte> value = stackalloc byte[1];
        if (!memory.TryRead(Address + RequestFlagsOffset, value, out string? readError))
        {
            message = readError ?? "Could not read the town receive request flags.";
            return false;
        }

        requestFlags = value[0];
        message = string.Empty;
        return true;
    }

    private static Encoding CreateShiftJisEncoding()
    {
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
        return Encoding.GetEncoding(
            932,
            new EncoderReplacementFallback("\uff1f"),
            DecoderFallback.ExceptionFallback);
    }

    private static bool TryRequireInitialized(IEmulatorMemory memory, out string message)
    {
        if (!TryDetect(memory, out bool detected, out message))
            return false;
        if (detected)
            return true;

        message = "The town receive mailbox is not loaded. Enter town with the focused test ROM.";
        return false;
    }
}
