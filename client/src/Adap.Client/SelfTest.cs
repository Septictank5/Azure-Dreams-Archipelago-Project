using System.IO.MemoryMappedFiles;
using Adap.Client.Archipelago;
using Adap.Client.Emulators.DuckStation;
using Adap.Client.Games;
using Adap.Client.Patching;
using Adap.Client.Windows;
using System.Buffers.Binary;
using System.Diagnostics;

namespace Adap.Client;

internal static class SelfTest
{
    public static int Run()
    {
        using (Process currentProcess = Process.GetCurrentProcess())
        {
            Require(
                AzureDreamsArchipelagoClient.IsProcessRunning(
                    currentProcess.Id,
                    currentProcess.ProcessName),
                "A live emulator process was not recognized as running.");
            Require(
                !AzureDreamsArchipelagoClient.IsProcessRunning(
                    currentProcess.Id,
                    currentProcess.ProcessName + "-stale"),
                "A reused process ID with the wrong executable name was accepted.");
        }
        Require(
            !AzureDreamsArchipelagoClient.IsProcessRunning(int.MaxValue, "duckstation"),
            "A nonexistent emulator process was recognized as running.");
        Require(
            AzureDreamsArchipelagoClient.TryParseEndpoint(
                "archipelago.gg:",
                out string defaultHost,
                out int defaultPort,
                out string endpointError),
            endpointError);
        Require(
            defaultHost == "archipelago.gg" && defaultPort == 38_281,
            "The player-facing archipelago.gg: default did not select the standard AP port.");
        Require(
            AzureDreamsMailbox.ExpectedPatchedMemoryTop == 0x0020_0000 &&
            AzureDreamsMailbox.Address == 0x801d_a540,
            "The client memory-top marker does not match the stage-1 seeded base.");
        Require(
            AzureDreamsReceiveState.SeedBlockAddress == 0x801d_7f00 &&
            AzureDreamsReceiveState.SeedVersion == 3,
            "The client seed-page contract does not match the current APWorld.");

        Require(
            !AzureDreamsArchipelagoClient.ShouldShowReceivePresentation(
                2,
                2,
                AzureDreamsReceiveState.LocationIdBase),
            "An own-world tower check should not create duplicate receive presentation.");
        Require(
            !AzureDreamsArchipelagoClient.ShouldShowReceivePresentation(
                2,
                2,
                AzureDreamsReceiveState.ShopLocationIdBase + 19),
            "An own-world shop purchase should not create a queued town dialogue.");
        Require(
            AzureDreamsArchipelagoClient.ShouldShowReceivePresentation(
                2,
                3,
                AzureDreamsReceiveState.LocationIdBase),
            "An item received from another player should create a town dialogue.");
        Require(
            AzureDreamsArchipelagoClient.ShouldShowReceivePresentation(2, 2, 0),
            "A non-location delivery should retain its town dialogue.");

        // A location-suppressed self-send stays silent when delivered at
        // hand, is promoted to a visible pickup once it has waited a second
        // in the queue, and never changes its answer after the first
        // decision - the staged request's flags are verified every poll.
        var presentationTracker = new ReceivePresentationTracker();
        var seenAt = new DateTime(2026, 7, 29, 12, 0, 0, DateTimeKind.Utc);
        TimeSpan delay = ReceivePresentationTracker.SelfSendPresentationDelay;
        presentationTracker.ObserveQueued(0, seenAt);
        presentationTracker.ObserveQueued(1, seenAt);
        presentationTracker.ObserveQueued(2, seenAt);
        Require(
            !presentationTracker.DecidePresentation(
                0, showByLocationRule: false, seenAt + delay / 2),
            "An at-hand self-send got a pickup animation before the queue delay.");
        Require(
            !presentationTracker.DecidePresentation(
                0, showByLocationRule: false, seenAt + delay * 3),
            "A latched silent delivery changed its answer while in flight.");
        Require(
            presentationTracker.DecidePresentation(
                1, showByLocationRule: false, seenAt + delay),
            "A self-send that waited out the queue delay stayed invisible.");
        Require(
            presentationTracker.DecidePresentation(
                2, showByLocationRule: true, seenAt),
            "The location rule's visible verdict was suppressed by the tracker.");
        Require(
            !presentationTracker.DecidePresentation(
                3, showByLocationRule: false, seenAt + delay * 3),
            "An index with no queue sighting should fall back to the location rule.");

        // Signed quality and equipment flags ride in bits 16-18 of the protocol
        // ID. These literals are the same golden vector the APWorld's
        // test_native_reward_manifest pins, so the two encoders cannot drift
        // apart silently - a divergence fails one suite or the other rather
        // than handing a player the wrong item.
        Require(
            AzureDreamsItemManifest.EncodeProtocolItemId(15, 2, 0, 0x80) == 0x0AD2_7840 &&
            AzureDreamsItemManifest.EncodeProtocolItemId(15, 2, -1, 0xC0) == 0x0AD7_7841 &&
            AzureDreamsItemManifest.EncodeProtocolItemId(4, 1, 10) ==
                (AzureDreamsItemManifest.ItemIdBase | (4L << 11) | (1L << 5) | 10),
            "Protocol item ID encoding drifted from the APWorld's layout.");

        Require(
            AzureDreamsItemManifest.TryGetInventoryDescriptor(0x0AD7_7841, out var cursedSword) &&
            cursedSword.ItemId == 2 &&
            cursedSword.Category == 15 &&
            cursedSword.Quality == -1 &&
            cursedSword.Flags == 0xC0,
            "A cursed Copper Sword did not decode to (2, 15, -1, 0xC0).");

        Require(
            AzureDreamsItemManifest.TryGetInventoryDescriptor(0x0AD2_7840, out var plainSword) &&
            plainSword.Quality == 0 &&
            plainSword.Flags == 0x80 &&
            (plainSword.Flags & 0x20) == 0,
            "An unidentified Copper Sword did not decode cleanly, or arrived equipped.");

        // The incoming display queues gifts behind pending ordinary items
        // (gifts join the tail of the incoming queue - they stage only once
        // the item queue drains) and respects the panel's slot cap.
        var giftQueue = new List<AzureDreamsGiftService.IncomingGift>
        {
            new(
                "sender7:run:1",
                new AzureDreamsItemDescriptor(1, 4),
                "Acid Rain Ball",
                "Sandknight",
                AzureDreamsItemManifest.EncodeProtocolItemId(4, 1, 0)),
        };
        var ordinaryQueue = new List<AzureDreamsIncomingItem>
        {
            new(AzureDreamsItemManifest.EncodeProtocolItemId(1, 1, 0), "Pita Fruit", "Wugga"),
            new(AzureDreamsItemManifest.EncodeProtocolItemId(5, 1, 0), "Medicinal Herb", "Septic"),
        };
        var incomingDisplay = AzureDreamsArchipelagoClient.BuildIncomingDisplayList(
            giftQueue, ordinaryQueue, 3);
        Require(
            incomingDisplay.Count == 3 &&
            incomingDisplay[0].DisplayName == "Pita Fruit" &&
            incomingDisplay[1].DisplayName == "Medicinal Herb" &&
            incomingDisplay[2].DisplayName == "Acid Rain Ball" &&
            incomingDisplay[2].SenderName == "Sandknight",
            "The incoming display does not queue gifts behind pending items.");
        Require(
            AzureDreamsArchipelagoClient.BuildIncomingDisplayList(
                giftQueue, ordinaryQueue, 2).Count == 2,
            "The incoming display ignored the slot cap.");
        Require(
            AzureDreamsArchipelagoClient.BuildIncomingDisplayList(
                [], ordinaryQueue, 10).Count == 2,
            "An empty giftbox changed the ordinary incoming display.");

        uint? reportedReceiveCursor = null;
        var reportedReceiveIndices = new List<int>();
        Require(
            AzureDreamsArchipelagoClient.TryReportAcknowledgedReceiveCursorAdvance(
                0,
                2,
                ref reportedReceiveCursor,
                reportedReceiveIndices.Add,
                out string receiveActivityError),
            receiveActivityError);
        Require(
            reportedReceiveIndices.Count == 0 && reportedReceiveCursor == 0,
            "Initial receive attachment replayed historical activity.");
        Require(
            AzureDreamsArchipelagoClient.TryReportAcknowledgedReceiveCursorAdvance(
                2,
                2,
                ref reportedReceiveCursor,
                reportedReceiveIndices.Add,
                out receiveActivityError),
            receiveActivityError);
        Require(
            reportedReceiveIndices.SequenceEqual([0, 1]) && reportedReceiveCursor == 2,
            "A game-side durable cursor advance did not publish every newly acknowledged receive.");
        Require(
            AzureDreamsArchipelagoClient.TryReportAcknowledgedReceiveCursorAdvance(
                2,
                2,
                ref reportedReceiveCursor,
                reportedReceiveIndices.Add,
                out receiveActivityError) &&
            reportedReceiveIndices.SequenceEqual([0, 1]),
            "An unchanged durable receive cursor published duplicate activity.");

        int processId = Environment.ProcessId;
        string mappingName = DuckStationMemory.GetMappingName(processId);

        using MemoryMappedFile fixture = MemoryMappedFile.CreateNew(
            mappingName,
            DuckStationMemory.ExportedRamSize,
            MemoryMappedFileAccess.ReadWrite);

        using (MemoryMappedViewAccessor view = fixture.CreateViewAccessor(
                   0,
                   DuckStationMemory.ExportedRamSize,
                   MemoryMappedFileAccess.ReadWrite))
        {
            byte[] signature =
            [
                0x08, 0x80, 0x02, 0x3c,
                0x38, 0x14, 0x42, 0x24,
                0x09, 0x80, 0x03, 0x3c,
                0x60, 0x87, 0x63, 0x24,
            ];
            view.WriteArray(0x33930, signature, 0, signature.Length);
            view.Write(0x80a70, AzureDreamsMailbox.LegacyPatchedMemoryTop);
        }

        Require(
            DuckStationMemory.TryOpen(processId, "self-test", out DuckStationMemory? memory, out string? openError),
            openError ?? "Could not open the synthetic DuckStation mapping.");

        DuckStationMemory connectedMemory = memory!;
        using (connectedMemory)
        {
            Require(
                AzureDreamsUsProbe.TryIdentify(connectedMemory, out bool identified, out string? probeError),
                probeError ?? "Game probe could not read RAM.");
            Require(identified, "The Azure Dreams US signature was not recognized.");

            byte[] expected = [0xde, 0xad, 0xbe, 0xef];
            Require(connectedMemory.TryWrite(0x8001_2340, expected, out string? writeError), writeError ?? "RAM write failed.");

            Span<byte> observed = stackalloc byte[expected.Length];
            Require(connectedMemory.TryRead(0xa001_2340, observed, out string? readError), readError ?? "RAM read failed.");
            Require(observed.SequenceEqual(expected), "KSEG0/KSEG1 address translation did not alias correctly.");

            Require(
                AzureDreamsMailbox.TryInitialize(connectedMemory, 3, out string mailboxMessage),
                mailboxMessage);
            Span<byte> mailboxHeader = stackalloc byte[
                AzureDreamsMailbox.GameMessageOffset + AzureDreamsMailbox.LockedElevatorMessage.Length];
            Require(
                connectedMemory.TryRead(AzureDreamsMailbox.Address, mailboxHeader, out string? mailboxReadError),
                mailboxReadError ?? "Mailbox read failed.");
            Require(
                BinaryPrimitives.ReadUInt32LittleEndian(mailboxHeader) == AzureDreamsMailbox.Magic,
                "Mailbox magic was not initialized.");
            Require(
                mailboxHeader[AzureDreamsMailbox.ElevatorClearanceOffset] == 3,
                "Mailbox elevator clearance was not initialized.");
            Require(
                mailboxHeader[AzureDreamsMailbox.GameMessageOffset..].SequenceEqual(
                    AzureDreamsMailbox.LockedElevatorMessage),
                "Mailbox locked-elevator message was not initialized.");

            Require(
                AzureDreamsMailbox.TryQueueTestReceive(
                    connectedMemory,
                    out uint receiveRequestSequence,
                    out string receiveQueueMessage),
                receiveQueueMessage);
            Require(receiveRequestSequence == 1, "The first receive request did not use sequence 1.");
            Require(
                AzureDreamsMailbox.TryReadReceiveStatus(
                    connectedMemory,
                    out uint observedReceiveRequest,
                    out uint observedReceiveAck,
                    out uint observedReceiveStatus,
                    out AzureDreamsItemDescriptor observedReceiveDescriptor,
                    out string receiveStatusMessage),
                receiveStatusMessage);
            Require(
                observedReceiveRequest == 1 &&
                observedReceiveAck == 0 &&
                observedReceiveStatus == AzureDreamsMailbox.ReceiveStatusPending,
                "The test receive command was not published pending and unacknowledged.");
            Require(
                observedReceiveDescriptor == AzureDreamsMailbox.TestReceiveDescriptor,
                "The test receive command did not contain the Acid Rain Ball descriptor unaltered.");
            Require(
                !AzureDreamsMailbox.TryQueueTestReceive(
                    connectedMemory,
                    out _,
                    out receiveQueueMessage),
                "A second item was queued while the first receive command was still pending.");
            Require(
                AzureDreamsMailbox.TrySynchronizeReceive(
                    connectedMemory,
                    1,
                    AzureDreamsMailbox.TestReceiveDescriptor,
                    out AzureDreamsReceiveProgress receiveProgress,
                    out string receiveSyncMessage),
                receiveSyncMessage);
            Require(
                receiveProgress == AzureDreamsReceiveProgress.Waiting,
                "A published native receive request was not recognized as waiting.");

            Span<byte> receiveWord = stackalloc byte[sizeof(uint)];
            BinaryPrimitives.WriteUInt32LittleEndian(
                receiveWord,
                AzureDreamsMailbox.ReceiveStatusInventoryFull);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.ReceiveStatusOffset,
                    receiveWord,
                    out string? receiveStateWriteError),
                receiveStateWriteError ?? "Could not synthesize the inventory-full receive state.");
            Require(
                AzureDreamsMailbox.TrySynchronizeReceive(
                    connectedMemory,
                    1,
                    AzureDreamsMailbox.TestReceiveDescriptor,
                    out receiveProgress,
                    out receiveSyncMessage),
                receiveSyncMessage);
            Require(
                receiveProgress == AzureDreamsReceiveProgress.InventoryFull,
                "An inventory-full native request was not left pending for retry.");

            BinaryPrimitives.WriteUInt32LittleEndian(
                receiveWord,
                AzureDreamsMailbox.ReceiveStatusDelivered);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.ReceiveStatusOffset,
                    receiveWord,
                    out receiveStateWriteError),
                receiveStateWriteError ?? "Could not synthesize the delivered receive state.");
            BinaryPrimitives.WriteUInt32LittleEndian(receiveWord, 1);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.ReceiveAckSequenceOffset,
                    receiveWord,
                    out receiveStateWriteError),
                receiveStateWriteError ?? "Could not acknowledge the synthetic receive request.");
            Require(
                AzureDreamsMailbox.TrySynchronizeReceive(
                    connectedMemory,
                    1,
                    AzureDreamsMailbox.TestReceiveDescriptor,
                    out receiveProgress,
                    out receiveSyncMessage),
                receiveSyncMessage);
            Require(
                receiveProgress == AzureDreamsReceiveProgress.Delivered,
                "An acknowledged native receive request was not recognized as delivered.");
            Require(
                AzureDreamsMailbox.TrySynchronizeReceive(
                    connectedMemory,
                    2,
                    AzureDreamsMailbox.TestReceiveDescriptor,
                    showPresentation: false,
                    out receiveProgress,
                    out receiveSyncMessage),
                receiveSyncMessage);
            Require(
                receiveProgress == AzureDreamsReceiveProgress.Queued,
                "A silent repeated descriptor at the next server-history sequence was not queued separately.");
            Require(
                AzureDreamsMailbox.TryReadReceiveStatus(
                    connectedMemory,
                    out observedReceiveRequest,
                    out observedReceiveAck,
                    out observedReceiveStatus,
                    out observedReceiveDescriptor,
                    out receiveStatusMessage),
                receiveStatusMessage);
            Require(
                observedReceiveRequest == 2 &&
                observedReceiveAck == 1 &&
                observedReceiveStatus == AzureDreamsMailbox.ReceiveStatusPending &&
                observedReceiveDescriptor == AzureDreamsMailbox.TestReceiveDescriptor,
                "The silent tower receive did not clear the presentation transport bit.");

            byte[] townMailbox = new byte[AzureDreamsTownMailbox.Size];
            BinaryPrimitives.WriteUInt32LittleEndian(
                townMailbox.AsSpan(AzureDreamsTownMailbox.MagicOffset),
                AzureDreamsTownMailbox.Magic);
            BinaryPrimitives.WriteUInt16LittleEndian(
                townMailbox.AsSpan(AzureDreamsTownMailbox.ProtocolVersionOffset),
                AzureDreamsTownMailbox.ProtocolVersion);
            BinaryPrimitives.WriteUInt16LittleEndian(
                townMailbox.AsSpan(AzureDreamsTownMailbox.StructureSizeOffset),
                AzureDreamsTownMailbox.Size);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsTownMailbox.Address,
                    townMailbox,
                    out string? townMailboxWriteError),
                townMailboxWriteError ?? "Could not initialize the synthetic town mailbox.");
            Require(
                AzureDreamsTownMailbox.TryDetect(
                    connectedMemory,
                    out bool townMailboxDetected,
                    out string townDetectionMessage),
                townDetectionMessage);
            Require(townMailboxDetected, "The synthetic town mailbox was not detected.");
            Require(
                AzureDreamsTownMailbox.TryQueueTestReceive(
                    connectedMemory,
                    out uint townRequestSequence,
                    out string townQueueMessage),
                townQueueMessage);
            Require(townRequestSequence == 1, "The first town receive did not use sequence 1.");
            Require(
                AzureDreamsTownMailbox.TryReadReceiveStatus(
                    connectedMemory,
                    out uint townRequest,
                    out uint townAck,
                    out uint townStatus,
                    out AzureDreamsItemDescriptor townDescriptor,
                    out uint townDestination,
                    out string townStatusMessage),
                townStatusMessage);
            Require(
                townRequest == 1 &&
                townAck == 0 &&
                townStatus == AzureDreamsMailbox.ReceiveStatusPending &&
                townDescriptor == AzureDreamsMailbox.TestReceiveDescriptor &&
                townDestination == AzureDreamsTownMailbox.DestinationNone,
                "The town receive request was not staged pending and unacknowledged.");
            Span<byte> townRequestFlag = stackalloc byte[1];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsTownMailbox.Address + AzureDreamsTownMailbox.RequestFlagsOffset,
                    townRequestFlag,
                    out string? townFlagReadError),
                townFlagReadError ?? "Could not read the synthetic town request flags.");
            Require(
                townRequestFlag[0] == AzureDreamsTownMailbox.RequestFlagNotify,
                "The focused town receive did not request its native dialogue.");
            Require(
                AzureDreamsTownMailbox.TryEncodeNotification(
                    "Acid Rain Ball",
                    out byte[] expectedTownNotification,
                    out string townEncodingMessage),
                townEncodingMessage);
            byte[] expectedTownNotificationPrefix = Convert.FromHexString(
                "82718285828382858289829682858284814082608283828982848140827182818289828E814082618281828C828C81441101");
            Require(
                expectedTownNotification.AsSpan(0, expectedTownNotificationPrefix.Length)
                    .SequenceEqual(expectedTownNotificationPrefix) &&
                expectedTownNotification.AsSpan(expectedTownNotificationPrefix.Length)
                    .IndexOfAnyExcept((byte)0) < 0,
                "The C# town notification encoding does not match the game's CP932 script bytes.");
            // Quality decorations are dropped from the dialogue on purpose:
            // the inventory and Nada's menu carry the real charges, and the
            // 56-byte buffer is better spent on the name. A charged ball
            // and its plain name must encode to identical buffers.
            Require(
                AzureDreamsTownMailbox.StripQualitySuffix("Acid Rain Ball (56)") ==
                    "Acid Rain Ball" &&
                AzureDreamsTownMailbox.StripQualitySuffix("Iron Sword +5") ==
                    "Iron Sword" &&
                AzureDreamsTownMailbox.StripQualitySuffix("Iron Sword -2") ==
                    "Iron Sword" &&
                AzureDreamsTownMailbox.StripQualitySuffix("Wind Crystal") ==
                    "Wind Crystal",
                "Quality suffix stripping did not normalize names as expected.");
            Require(
                AzureDreamsTownMailbox.TryEncodeNotification(
                    "Blinder Ball (10)",
                    out byte[] chargedTownNotification,
                    out townEncodingMessage) &&
                AzureDreamsTownMailbox.TryEncodeNotification(
                    "Blinder Ball",
                    out byte[] plainTownNotification,
                    out _) &&
                chargedTownNotification.AsSpan().SequenceEqual(plainTownNotification),
                "A charged ball's dialogue did not match its plain name's dialogue.");
            // A long suffix-free name degrades to "Got ..." instead of
            // failing the delivery; an absurd one hard-truncates.
            Require(
                AzureDreamsTownMailbox.TryEncodeNotification(
                    new string('W', 20),
                    out byte[] degradedNotification,
                    out string degradedMessage),
                degradedMessage);
            Require(
                degradedNotification[0] == 0x82 && degradedNotification[1] == 0x66,
                "An over-length notification did not degrade to the Got form.");
            Require(
                AzureDreamsTownMailbox.TryEncodeNotification(
                    new string('W', 40),
                    out byte[] truncatedNotification,
                    out string truncatedMessage) &&
                truncatedNotification[^2] == 0x11 &&
                truncatedNotification[^1] == 0x01,
                $"A 40-character name did not truncate into the buffer ({truncatedMessage}).");
            Span<byte> observedTownNotification = stackalloc byte[
                AzureDreamsTownMailbox.NotificationMessageSize];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsTownMailbox.NotificationMessageAddress,
                    observedTownNotification,
                    out string? townNotificationReadError),
                townNotificationReadError ?? "Could not read the staged town notification.");
            Require(
                observedTownNotification.SequenceEqual(expectedTownNotification),
                "The staged town notification did not contain the received item name.");

            BinaryPrimitives.WriteUInt32LittleEndian(
                receiveWord,
                AzureDreamsMailbox.ReceiveStatusDelivered);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsTownMailbox.Address + AzureDreamsTownMailbox.ReceiveStatusOffset,
                    receiveWord,
                    out receiveStateWriteError),
                receiveStateWriteError ?? "Could not synthesize the delivered town receive state.");
            BinaryPrimitives.WriteUInt32LittleEndian(receiveWord, 1);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsTownMailbox.Address + AzureDreamsTownMailbox.ReceiveAckSequenceOffset,
                    receiveWord,
                    out receiveStateWriteError),
                receiveStateWriteError ?? "Could not acknowledge the synthetic town receive request.");
            Require(
                AzureDreamsTownMailbox.TrySynchronizeReceive(
                    connectedMemory,
                    1,
                    AzureDreamsMailbox.TestReceiveDescriptor,
                    "Acid Rain Ball",
                    showNotification: true,
                    out receiveProgress,
                    out receiveSyncMessage),
                receiveSyncMessage);
            Require(
                receiveProgress == AzureDreamsReceiveProgress.Delivered,
                "An acknowledged town receive was not recognized as delivered.");

            BinaryPrimitives.WriteUInt32LittleEndian(receiveWord, 1);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsTownMailbox.NotificationStateAddress,
                    receiveWord,
                    out receiveStateWriteError),
                receiveStateWriteError ?? "Could not synthesize an active town notification.");
            Require(
                AzureDreamsTownMailbox.TrySynchronizeReceive(
                    connectedMemory,
                    2,
                    AzureDreamsMailbox.TestReceiveDescriptor,
                    "Acid Rain Ball",
                    showNotification: true,
                    out receiveProgress,
                    out receiveSyncMessage),
                receiveSyncMessage);
            Require(
                receiveProgress == AzureDreamsReceiveProgress.Waiting,
                "A new town notification overwrote the active notification buffer.");

            receiveWord.Clear();
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsTownMailbox.NotificationStateAddress,
                    receiveWord,
                    out receiveStateWriteError),
                receiveStateWriteError ?? "Could not clear the synthetic town notification state.");
            Require(
                AzureDreamsTownMailbox.TrySynchronizeReceive(
                    connectedMemory,
                    2,
                    AzureDreamsMailbox.TestReceiveDescriptor,
                    "Acid Rain Ball",
                    showNotification: false,
                    out receiveProgress,
                    out receiveSyncMessage),
                receiveSyncMessage);
            Require(
                receiveProgress == AzureDreamsReceiveProgress.Queued,
                "A silent self-location town receive was not queued.");
            Require(
                connectedMemory.TryRead(
                    AzureDreamsTownMailbox.Address + AzureDreamsTownMailbox.RequestFlagsOffset,
                    townRequestFlag,
                    out townFlagReadError),
                townFlagReadError ?? "Could not read the silent town request flags.");
            Require(
                townRequestFlag[0] == 0,
                "A silent self-location receive incorrectly requested a dialogue.");

            // Raw read: these suites run before the persistent-state headers
            // are staged, so the guarded accessor would reject the build.
            _cursorBeforeGiftQueueTest = ReadRawReceiveCursor(connectedMemory);
            TestLineCuttersDoNotChopTheTownBatch();
            TestForcedTrapPump();
            TestTownReceiveQueue(connectedMemory);
            TestTowerDoesNotRouteThroughTheTownQueue(connectedMemory);
            TestStaleTowerRequestIsRecalledInTown(connectedMemory);
            TestConsumedItemsFoldIntoTheReceiveCursor(connectedMemory);
            TestGiftQueuesThroughTheReceiveQueue(connectedMemory);
            TestGiftBatchDelivery(connectedMemory);

            Require(
                AzureDreamsMailbox.TryWriteGameMessage(
                    connectedMemory,
                    AzureDreamsMailbox.MultiworldSendTestMessage,
                    out string gameMessageResult),
                gameMessageResult);
            byte[] gameMessage = new byte[AzureDreamsMailbox.GameMessageSize];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.GameMessageOffset,
                    gameMessage,
                    out string? gameMessageReadError),
                gameMessageReadError ?? "Game-message buffer read failed.");
            Require(
                gameMessage.AsSpan(0, AzureDreamsMailbox.MultiworldSendTestMessage.Length)
                    .SequenceEqual(AzureDreamsMailbox.MultiworldSendTestMessage),
                "The multiworld send test message was not written exactly.");

            Require(
                AzureDreamsMailbox.TrySetElevatorClearance(
                    connectedMemory,
                    5,
                    out string clearanceMessage),
                clearanceMessage);
            Require(
                AzureDreamsMailbox.TryReadElevatorClearance(
                    connectedMemory,
                    out byte updatedClearance,
                    out clearanceMessage),
                clearanceMessage);
            Require(updatedClearance == 5, "Mailbox clearance was not updated without reinitialization.");
            Require(
                AzureDreamsMailbox.TrySetElevatorClearance(
                    connectedMemory,
                    3,
                    out clearanceMessage),
                clearanceMessage);

            Require(
                AzureDreamsInventoryHud.TryRefreshFromMailbox(
                    connectedMemory,
                    out byte hudClearance,
                    out string hudMessage),
                hudMessage);
            Require(hudClearance == 3, "Inventory HUD did not read clearance 3 from the mailbox.");

            byte[] keycardLabel = new byte[AzureDreamsInventoryHud.LabelBufferSize];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsInventoryHud.KeycardLabelAddress,
                    keycardLabel,
                    out string? keycardReadError),
                keycardReadError ?? "Keycard HUD label read failed.");
            Require(
                keycardLabel.SequenceEqual(AzureDreamsInventoryHud.CreatePaddedLabel("Keycard Lvl: 3")),
                "Keycard HUD label did not match the native inventory-font encoding.");

            byte[] maxFloorLabel = new byte[AzureDreamsInventoryHud.LabelBufferSize];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsInventoryHud.MaxFloorLabelAddress,
                    maxFloorLabel,
                    out string? maxFloorReadError),
                maxFloorReadError ?? "Maximum-floor HUD label read failed.");
            Require(
                maxFloorLabel.SequenceEqual(AzureDreamsInventoryHud.CreatePaddedLabel("Max Floor: 19")),
                "Maximum-floor HUD label did not match clearance 3.");

            byte[] gold = AzureDreamsInventoryHud.CreateInventoryText("Gold");
            byte[] expectedGold =
            [
                0x00, 0x2c, 0x80, 0x80, 0x1f, 0x00, 0x84, 0x7c, 0x38, 0x20, 0x08, 0x10,
                0x00, 0x2c, 0x88, 0x80, 0x1f, 0x00, 0x84, 0x7c, 0x78, 0x40, 0x08, 0x10,
                0x00, 0x2c, 0x90, 0x80, 0x1f, 0x00, 0x84, 0x7c, 0x60, 0x40, 0x08, 0x10,
                0x80, 0x2c, 0x98, 0x80, 0x1f, 0x00, 0x84, 0x7c, 0x20, 0x40, 0x08, 0x10,
            ];
            Require(gold.SequenceEqual(expectedGold), "Inventory-font encoder no longer reproduces the known Gold buffer.");

            Span<byte> seededMemoryTop = stackalloc byte[sizeof(uint)];
            BinaryPrimitives.WriteUInt32LittleEndian(
                seededMemoryTop,
                AzureDreamsMailbox.ExpectedPatchedMemoryTop);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsMailbox.MemoryTopAddress,
                    seededMemoryTop,
                    out string? memoryTopWriteError),
                memoryTopWriteError ?? "Could not switch the fixture to the seeded memory-top value.");
            Require(
                AzureDreamsInventoryHud.TryWrite(
                    connectedMemory,
                    3,
                    out string seededHudMessage),
                seededHudMessage);
            Require(
                connectedMemory.TryRead(
                    AzureDreamsInventoryHud.CompactKeycardLabelAddress,
                    keycardLabel,
                    out keycardReadError),
                keycardReadError ?? "Compact Keycard HUD label read failed.");
            Require(
                keycardLabel.SequenceEqual(
                    AzureDreamsInventoryHud.CreatePaddedLabel("Keycard Lvl: 3")),
                "The compact Keycard HUD label did not use the current seeded address.");
            Require(
                connectedMemory.TryRead(
                    AzureDreamsInventoryHud.CompactMaxFloorLabelAddress,
                    maxFloorLabel,
                    out maxFloorReadError),
                maxFloorReadError ?? "Compact maximum-floor HUD label read failed.");
            Require(
                maxFloorLabel.SequenceEqual(
                    AzureDreamsInventoryHud.CreatePaddedLabel("Max Floor: 19")),
                "The compact maximum-floor HUD label did not use the current seeded address.");
            Require(
                AzureDreamsInventoryHud.TryAttachToOpenInventory(
                    connectedMemory,
                    out _,
                    out _,
                    out string compactAttachMessage) &&
                compactAttachMessage.Contains(
                    "attaches its compact inventory HUD in-game",
                    StringComparison.Ordinal),
                "The diagnostic client did not defer compact HUD attachment to the game.");

            Require(
                connectedMemory.TryWrite(
                    AzureDreamsInventoryHud.RestoredSeededHudSignatureAddress,
                    "ADAPHUD1"u8,
                    out string? restoredHudSignatureError),
                restoredHudSignatureError ?? "Could not install the restored seeded HUD signature.");
            Require(
                AzureDreamsInventoryHud.TryWrite(
                    connectedMemory,
                    4,
                    out string restoredSeededHudMessage),
                restoredSeededHudMessage);
            Require(
                connectedMemory.TryRead(
                    AzureDreamsInventoryHud.KeycardLabelAddress,
                    keycardLabel,
                    out keycardReadError),
                keycardReadError ?? "Restored seeded Keycard HUD label read failed.");
            Require(
                keycardLabel.SequenceEqual(
                    AzureDreamsInventoryHud.CreatePaddedLabel("Keycard Lvl: 4")),
                "The restored seeded HUD did not select the full legacy Keycard label address.");
            Require(
                connectedMemory.TryRead(
                    AzureDreamsInventoryHud.MaxFloorLabelAddress,
                    maxFloorLabel,
                    out maxFloorReadError),
                maxFloorReadError ?? "Restored seeded maximum-floor HUD label read failed.");
            Require(
                maxFloorLabel.SequenceEqual(
                    AzureDreamsInventoryHud.CreatePaddedLabel("Max Floor: 24")),
                "The restored seeded HUD did not select the full legacy maximum-floor label address.");

            byte[] seedHeader = new byte[16];
            BinaryPrimitives.WriteUInt32LittleEndian(seedHeader, AzureDreamsReceiveState.SeedMagic);
            BinaryPrimitives.WriteUInt16LittleEndian(seedHeader.AsSpan(4), AzureDreamsReceiveState.SeedVersion);
            BinaryPrimitives.WriteUInt16LittleEndian(seedHeader.AsSpan(6), AzureDreamsReceiveState.LocationCount);
            byte[] seedSignature = [0x41, 0x44, 0x41, 0x50, 1, 2, 3, 4];
            seedSignature.CopyTo(seedHeader, 8);
            Require(
                connectedMemory.TryWrite(AzureDreamsReceiveState.SeedBlockAddress, seedHeader, out string? seedWriteError),
                seedWriteError ?? "Synthetic seed-header write failed.");

            byte[] persistentState = new byte[AzureDreamsReceiveState.PersistentStateSize];
            BinaryPrimitives.WriteUInt32LittleEndian(persistentState, AzureDreamsReceiveState.PersistentStateMagic);
            BinaryPrimitives.WriteUInt16LittleEndian(
                persistentState.AsSpan(4), AzureDreamsReceiveState.PersistentStateVersion);
            BinaryPrimitives.WriteUInt16LittleEndian(
                persistentState.AsSpan(6), AzureDreamsReceiveState.PersistentStateSize);
            seedSignature.CopyTo(persistentState, 8);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsReceiveState.PersistentStateAddress,
                    persistentState,
                    out string? stateWriteError),
                stateWriteError ?? "Synthetic persistent-state write failed.");

            Require(
                AzureDreamsReceiveState.TryReadIdentity(
                    connectedMemory,
                    out AzureDreamsSeedIdentity seedIdentity,
                    out string seedStateMessage),
                seedStateMessage);
            Require(seedIdentity.Signature.SequenceEqual(seedSignature), "Seed identity signature did not match.");

            // The persistent state header and seeded memory top are now
            // staged, which the keycard clearance level depends on.
            TestKeycardsCutInLine(connectedMemory);
            TestTemperSandsCutInLine(connectedMemory);
            TestLiveFloorFollowsTheLoadedMode(connectedMemory);

            Require(
                connectedMemory.TryWrite(
                    AzureDreamsReceiveState.SeedBlockAddress,
                    new byte[seedHeader.Length],
                    out seedWriteError),
                seedWriteError ?? "Synthetic seed-header clear failed.");
            Require(
                AzureDreamsReceiveState.TryReadSynchronizationIdentity(
                    connectedMemory,
                    out AzureDreamsSeedIdentity townIdentity,
                    out seedStateMessage),
                seedStateMessage);
            Require(
                townIdentity.Signature.SequenceEqual(seedSignature),
                "Town synchronization did not fall back to the persistent seed identity.");
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsReceiveState.SeedBlockAddress,
                    seedHeader,
                    out seedWriteError),
                seedWriteError ?? "Synthetic seed-header restore failed.");

            Require(
                connectedMemory.TryWrite(
                    AzureDreamsMailbox.Address,
                    new byte[8],
                    out string? towerHeaderClearError),
                towerHeaderClearError ?? "Could not unload the synthetic tower mailbox.");
            // The retired +0x80..+0x9B floor-location fields: nothing may write
            // them any more, loaded mailbox or not (they were the v3 mask mirror).
            byte[] townStackMaskSentinel = Enumerable.Repeat(
                (byte)0xa5,
                AzureDreamsMailbox.RetiredFloorLocationFieldsSize).ToArray();
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.RetiredFloorLocationFieldsOffset,
                    townStackMaskSentinel,
                    out string? townStackWriteError),
                townStackWriteError ?? "Could not stage the town stack mask sentinel.");
            byte[] townStackKeycardSentinel = [0x5a];
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.ElevatorClearanceOffset,
                    townStackKeycardSentinel,
                    out townStackWriteError),
                townStackWriteError ?? "Could not stage the town stack keycard sentinel.");

            Require(
                AzureDreamsMailbox.TrySetElevatorClearance(
                    connectedMemory,
                    3,
                    out string developmentClearanceMessage),
                developmentClearanceMessage);
            Span<byte> developmentKeycardLevel = stackalloc byte[4];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsReceiveState.PersistentStateAddress + AzureDreamsReceiveState.KeycardLevelOffset,
                    developmentKeycardLevel,
                    out string? developmentKeycardReadError),
                developmentKeycardReadError ?? "Development keycard-level read-back failed.");
            Require(
                BinaryPrimitives.ReadUInt32LittleEndian(developmentKeycardLevel) == 3,
                "The development clearance control did not update the save-backed keycard level.");
            Span<byte> developmentTownStackKeycard = stackalloc byte[1];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.ElevatorClearanceOffset,
                    developmentTownStackKeycard,
                    out string? developmentTownStackReadError),
                developmentTownStackReadError ?? "Could not read the development town-stack sentinel.");
            Require(
                developmentTownStackKeycard.SequenceEqual(townStackKeycardSentinel),
                "The development clearance control wrote through the unloaded tower mailbox into town stack.");
            Require(
                AzureDreamsReceiveState.TrySetProgressiveKeycardLevel(
                    connectedMemory,
                    0,
                    out developmentClearanceMessage),
                developmentClearanceMessage);

            // Floor 1 slot 0, floor 26 slot 1 and floor 39 slot 2 (the carrier's):
            // byte = floor-1, bit = slot, in the ADSV v4 journal.
            long[] mergedLocations =
            [
                AzureDreamsReceiveState.LocationIdBase,
                AzureDreamsReceiveState.LocationIdBase + 25 * AzureDreamsReceiveState.SlotsPerFloor + 1,
                AzureDreamsReceiveState.LocationIdBase + AzureDreamsReceiveState.LocationCount - 1,
            ];
            Require(
                AzureDreamsReceiveState.TryMergeCheckedLocations(
                    connectedMemory,
                    mergedLocations,
                    out int newlyRecorded,
                    out string mergeMessage),
                mergeMessage);
            Require(newlyRecorded == 3, "Server-check merge did not record exactly three new locations.");
            Span<byte> persistentMask = stackalloc byte[AzureDreamsReceiveState.LocationMaskSize];
            Require(
                AzureDreamsReceiveState.TryReadCollectedLocationMask(
                    connectedMemory,
                    persistentMask,
                    out string persistentMaskMessage),
                persistentMaskMessage);
            Require(
                persistentMask[0] == 0x01 && persistentMask[25] == 0x02 && persistentMask[38] == 0x04 &&
                persistentMask.ToArray().Sum(b => (int)b) == 7,
                "The v4 journal did not land on byte floor-1, bit slot.");
            Require(
                AzureDreamsReceiveState.GetCollectedLocationIds(persistentMask).SequenceEqual(mergedLocations),
                "Persistent location mask did not round-trip to location IDs.");
            Require(
                AzureDreamsReceiveState.TryMergeCheckedLocations(
                    connectedMemory,
                    mergedLocations,
                    out newlyRecorded,
                    out mergeMessage) && newlyRecorded == 0,
                "A second merge of the same checks was not a no-op.");
            Span<byte> observedTownStackMask = stackalloc byte[
                AzureDreamsMailbox.RetiredFloorLocationFieldsSize];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.RetiredFloorLocationFieldsOffset,
                    observedTownStackMask,
                    out string? townStackReadError),
                townStackReadError ?? "Could not read the town stack mask sentinel.");
            Require(
                observedTownStackMask.SequenceEqual(townStackMaskSentinel),
                "Server-check reconciliation wrote into the retired mailbox mirror.");

            Require(
                AzureDreamsReceiveState.TryMergeCheckedShopLocations(
                    connectedMemory,
                    [
                        AzureDreamsReceiveState.ShopLocationIdBase,
                        AzureDreamsReceiveState.ShopLocationIdBase + 9,
                        AzureDreamsReceiveState.ShopLocationIdBase + 10,
                        AzureDreamsReceiveState.ShopLocationIdBase + 19,
                    ],
                    out int newlyRecordedShopChecks,
                    out mergeMessage),
                mergeMessage);
            Require(
                newlyRecordedShopChecks == 4,
                "Server-check merge did not record all Equipment and Monster Shop locations.");
            Span<byte> persistentShopMask = stackalloc byte[
                AzureDreamsReceiveState.ShopLocationMaskSize];
            Require(
                AzureDreamsReceiveState.TryReadCollectedShopLocationMask(
                    connectedMemory,
                    persistentShopMask,
                    out string persistentShopMaskMessage),
                persistentShopMaskMessage);
            Require(
                AzureDreamsReceiveState.GetCollectedShopLocationIds(persistentShopMask)
                    .SequenceEqual(
                        [
                            AzureDreamsReceiveState.ShopLocationIdBase,
                            AzureDreamsReceiveState.ShopLocationIdBase + 9,
                            AzureDreamsReceiveState.ShopLocationIdBase + 10,
                            AzureDreamsReceiveState.ShopLocationIdBase + 19,
                        ]),
                "Persistent town-shop mask did not round-trip to location IDs.");

            Require(
                AzureDreamsReceiveState.TryWriteReceivedItemCount(
                    connectedMemory,
                    12,
                    out string cursorMessage),
                cursorMessage);
            Require(
                AzureDreamsReceiveState.TryReadReceivedItemCount(
                    connectedMemory,
                    out uint receivedItemCount,
                    out cursorMessage),
                cursorMessage);
            Require(receivedItemCount == 12, "Received-item cursor did not persist.");

            // The gold-package grant: cumulative, clamped, verified. Its
            // exactly-once property is the durable cursor's, tested above -
            // this covers the counter arithmetic itself.
            Span<byte> goldWord = stackalloc byte[4];
            BinaryPrimitives.WriteUInt32LittleEndian(goldWord, 1_234);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsReceiveState.GoldAddress, goldWord, out string? goldSeedError),
                goldSeedError ?? "Could not seed the gold counter.");
            Require(
                AzureDreamsReceiveState.TryGrantGold(
                    connectedMemory, 5_000, out uint grantedGold, out string goldMessage),
                goldMessage);
            Require(grantedGold == 6_234, "A gold grant did not add to the counter.");
            Require(
                AzureDreamsReceiveState.TryGrantGold(
                    connectedMemory, 5_000, out grantedGold, out goldMessage),
                goldMessage);
            Require(
                grantedGold == 11_234,
                "A second gold grant was not cumulative - the delivery path " +
                "must grant once per history index, never re-derive.");
            BinaryPrimitives.WriteUInt32LittleEndian(
                goldWord, AzureDreamsReceiveState.MaximumGold - 2_000);
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsReceiveState.GoldAddress, goldWord, out goldSeedError),
                goldSeedError ?? "Could not stage a near-cap gold counter.");
            Require(
                AzureDreamsReceiveState.TryGrantGold(
                    connectedMemory, 5_000, out grantedGold, out goldMessage),
                goldMessage);
            Require(
                grantedGold == AzureDreamsReceiveState.MaximumGold,
                "A grant past the ceiling did not clamp; a wrapped counter " +
                "would erase the player's savings.");
            goldWord.Clear();
            Require(
                connectedMemory.TryWrite(
                    AzureDreamsReceiveState.GoldAddress, goldWord, out goldSeedError),
                goldSeedError ?? "Could not clear the gold counter.");

            // The durable gold-granted counter (journal v3): the eager grant's
            // exactly-once memory. Round-trips through the ADSV gold-granted word and starts
            // this suite at zero so the pristine test above stays honest.
            Require(
                AzureDreamsReceiveState.TryReadGoldGrantedCount(
                    connectedMemory, out uint grantedCount, out string grantedMessage) &&
                grantedCount == 0,
                grantedMessage.Length > 0
                    ? grantedMessage
                    : "A fresh journal reported banked gold packages.");
            Require(
                AzureDreamsReceiveState.TryWriteGoldGrantedCount(
                    connectedMemory, 4, out grantedMessage),
                grantedMessage);
            Require(
                AzureDreamsReceiveState.TryReadGoldGrantedCount(
                    connectedMemory, out grantedCount, out grantedMessage) &&
                grantedCount == 4,
                grantedMessage.Length > 0
                    ? grantedMessage
                    : "The gold-granted counter did not round-trip.");
            Require(
                AzureDreamsReceiveState.TryWriteGoldGrantedCount(
                    connectedMemory, 0, out grantedMessage),
                grantedMessage);

            TestSendTokens(connectedMemory);

            // Requires the staged persistent-state headers above, so it
            // lives here rather than beside the other gift tests.
            TestGiftCursorRepair(connectedMemory);

            Require(
                AzureDreamsReceiveState.TryGrantProgressiveKeycard(
                    connectedMemory,
                    out byte receivedKeycardLevel,
                    out string keycardMessage),
                keycardMessage);
            Require(receivedKeycardLevel == 1, "The first received keycard did not produce clearance level 1.");
            Require(
                AzureDreamsReceiveState.TrySetProgressiveKeycardLevel(
                    connectedMemory,
                    receivedKeycardLevel,
                    out keycardMessage),
                keycardMessage);
            Span<byte> idempotentKeycard = stackalloc byte[4];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsReceiveState.PersistentStateAddress + AzureDreamsReceiveState.KeycardLevelOffset,
                    idempotentKeycard,
                    out string? persistentKeycardReadError),
                persistentKeycardReadError ?? "Keycard-level read-back failed.");
            Require(
                BinaryPrimitives.ReadUInt32LittleEndian(idempotentKeycard) == 1,
                "Reapplying the receive-history keycard level was not idempotent.");
            Span<byte> observedTownStackKeycard = stackalloc byte[1];
            Require(
                connectedMemory.TryRead(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.ElevatorClearanceOffset,
                    observedTownStackKeycard,
                    out townStackReadError),
                townStackReadError ?? "Could not read the town stack keycard sentinel.");
            Require(
                observedTownStackKeycard.SequenceEqual(townStackKeycardSentinel),
                "Keycard reconciliation wrote through the unloaded tower mailbox into town stack.");

            long acidRainItemId = AzureDreamsItemManifest.EncodeProtocolItemId(4, 17, 1);
            Require(
                AzureDreamsItemManifest.TryGetInventoryDescriptor(
                    acidRainItemId,
                    out AzureDreamsItemDescriptor acidRainDescriptor),
                "Acid Rain Ball protocol ID was not recognized.");
            Require(
                acidRainDescriptor == new AzureDreamsItemDescriptor(17, 4, 1, 0),
                "Acid Rain Ball protocol ID decoded to the wrong native descriptor.");
            Require(
                AzureDreamsItemManifest.TryGetInventoryDescriptor(
                    AzureDreamsItemManifest.EncodeProtocolItemId(4, 1, 10),
                    out AzureDreamsItemDescriptor tenChargeFireBall) &&
                tenChargeFireBall == new AzureDreamsItemDescriptor(1, 4, 10, 0),
                "Ten-charge Fire Ball did not preserve its quality byte.");
            Require(
                AzureDreamsItemManifest.TryGetInventoryDescriptor(
                    AzureDreamsItemManifest.EncodeProtocolItemId(18, 3, 20),
                    out AzureDreamsItemDescriptor dragonEgg) &&
                dragonEgg == new AzureDreamsItemDescriptor(3, 18, 20, 0),
                "Dragon Egg did not preserve its warming-quality byte.");
            Require(
                !AzureDreamsItemManifest.TryGetInventoryDescriptor(
                    AzureDreamsItemManifest.ItemIdBase + 1,
                    out _),
                "The retired provisional Gold protocol ID was still accepted.");
            // A Gold Coin is an ordinary Azure Dreams item. The APWorld does not
            // put one in the pool, but that is the APWorld's choice and the
            // client does not get to enforce it a second time.
            Require(
                AzureDreamsItemManifest.TryGetInventoryDescriptor(
                    AzureDreamsItemManifest.EncodeProtocolItemId(14, 3, 0),
                    out AzureDreamsItemDescriptor goldCoin) &&
                goldCoin == new AzureDreamsItemDescriptor(3, 14, 0, 0),
                "A Gold Coin was refused because the pool does not contain one.");

            // Gift quality round trip: the raw-descriptor path carries a
            // 0-charge ball exactly, charges and status bits intact.
            long zeroChargeBall = AzureDreamsGiftService.PackDescriptor(1, 4, 0, 0);
            Require(
                AzureDreamsGiftService.TryUnpackDescriptor(
                    checked((uint)zeroChargeBall),
                    out AzureDreamsItemDescriptor zeroChargeDescriptor) &&
                zeroChargeDescriptor == new AzureDreamsItemDescriptor(1, 4, 0, 0),
                "A 0-charge ball did not survive the gift descriptor round trip.");
            // Quality is data, not identity. The manifest path takes whatever
            // charge count the id encodes instead of judging it against a
            // canonical tier list the APWorld is free to change under it.
            Require(
                AzureDreamsItemManifest.TryGetInventoryDescriptor(
                    AzureDreamsItemManifest.EncodeProtocolItemId(4, 1, 0),
                    out AzureDreamsItemDescriptor zeroChargeFromId) &&
                zeroChargeFromId == new AzureDreamsItemDescriptor(1, 4, 0, 0),
                "The manifest dropped a ball whose id encodes zero charges.");
            // A gift can only have come out of another player's Azure Dreams
            // inventory, so every real item goes through - coins included. The
            // one thing that does not is a coordinate naming no item at all.
            Require(
                AzureDreamsGiftService.TryUnpackDescriptor(
                    checked((uint)AzureDreamsGiftService.PackDescriptor(3, 14, 0, 0)),
                    out _),
                "A gifted Gold Coin was refused as an undeliverable descriptor.");
            Require(
                !AzureDreamsGiftService.TryUnpackDescriptor(
                    checked((uint)AzureDreamsGiftService.PackDescriptor(9, 14, 0, 0)),
                    out _),
                "A gift naming a coin id the game has no item for was accepted.");
            long statusCarrier = AzureDreamsGiftService.PackDescriptor(9, 1, 3, 0x80);
            Require(
                AzureDreamsGiftService.TryUnpackDescriptor(
                    checked((uint)statusCarrier),
                    out AzureDreamsItemDescriptor statusDescriptor) &&
                statusDescriptor.ToBytes().SequenceEqual(new byte[] { 9, 1, 3, 0x80 }),
                "The gift descriptor round trip dropped quality or status bytes.");
            Require(
                !AzureDreamsGiftService.TryUnpackDescriptor(0x0000_0005, out _),
                "A category-0 packed descriptor was accepted as deliverable.");

            TestTownCheckpoints(connectedMemory, seedIdentity);
        }

        TestPatchingAndSettings();
        TestPlayerYamlCreation();
        TestLaunchAndAssociation();
        TestTowerProgressView();
        TestTrackerWindow();
        TestActivityFeedIsBounded();

        Console.WriteLine(
            "PASS: emulator transport, game probe, mailbox, HUD, durable AP state, " +
            "native item delivery, town checkpoints, tower view, tracker window, " +
            "bounded activity feed, PPF patching, player settings, and player " +
            "YAML creation");
        return 0;
    }

    /// <summary>
    /// A multi-gift batch must deliver every item individually, in order,
    /// and exactly once.
    ///
    /// <para>The failure this pins is the old cursor-derived receive
    /// sequence: gifts never advance the durable receive cursor, so a whole
    /// batch shared one sequence - the first delivered and the rest wedged
    /// on a descriptor mismatch until a town reload cleared the slab, so the
    /// receiver saw one item. It now runs against the receive queue, which
    /// is the path that actually ships.</para>
    /// </summary>
    private static void TestGiftBatchDelivery(DuckStationMemory memory)
    {
        AzureDreamsTownReceiveWindow.ResetForTest();
        AzureDreamsGiftService.ResetIncomingStateForTest();
        ResetSyntheticReceiveQueue(memory);

        // Four distinct items, INCLUDING two that share id/category and
        // differ only in quality (a +10 vs a +5 sword) and a 0-charge ball
        // whose canonical form has charges - the exact-state cases.
        var descriptors = new[]
        {
            new AzureDreamsItemDescriptor(1, 4, 0, 0),    // 0-charge ball
            new AzureDreamsItemDescriptor(2, 15, 10, 0),  // +10 sword
            new AzureDreamsItemDescriptor(2, 15, 5, 0),   // +5 sword
            new AzureDreamsItemDescriptor(1, 1, 0, 0),    // medicinal herb
        };
        var batch = new List<AzureDreamsGiftService.IncomingGift>();
        for (int i = 0; i < descriptors.Length; i++)
        {
            batch.Add(new AzureDreamsGiftService.IncomingGift(
                $"sender7:run:{i + 1}",
                descriptors[i],
                $"Item {i + 1}",
                "Sandknight"));
        }

        uint cursorBeforeGifts = ReadRawReceiveCursor(memory);

        // Drive it the way the poll loop does: queue, then play the game
        // consuming exactly the one queued entry, and repeat. A regression
        // that reuses one token delivers only the first.
        var queuedTokens = new List<uint>();
        var queuedDescriptors = new List<AzureDreamsItemDescriptor>();
        for (int guard = 0; guard < 100 &&
            queuedDescriptors.Count < descriptors.Length; guard++)
        {
            AzureDreamsGiftService.DeliverPendingGifts(
                null, memory, 0, batch, "Septic",
                ordinaryQueueDrained: true, out _);
            if (!AzureDreamsTownReceiveQueue.TryRead(
                    memory,
                    out AzureDreamsTownReceiveQueueState state,
                    out string readMessage))
            {
                Require(false, readMessage);
                return;
            }
            // Nothing new queued: the batch is either done or stuck.
            if (state.InFlight.Count == 0)
                break;

            Require(
                state.InFlight.Count == 1,
                "More than one gift was in flight at once.");
            queuedTokens.Add(state.InFlight[0].Token);
            queuedDescriptors.Add(state.InFlight[0].Descriptor);
            // The game's side: consume the entry.
            AdvanceSyntheticQueueHead(memory, 1);
        }

        Require(
            queuedDescriptors.Count == descriptors.Length,
            $"A four-item gift batch delivered {queuedDescriptors.Count} items, not four.");
        Require(
            queuedDescriptors.SequenceEqual(descriptors),
            "The gift batch did not deliver every item's exact descriptor in order.");
        Require(
            queuedTokens.Distinct().Count() == descriptors.Length,
            "Two gifts in one batch shared a receive token.");
        for (int i = 1; i < queuedTokens.Count; i++)
        {
            Require(
                queuedTokens[i] > queuedTokens[i - 1],
                "Gift tokens were not strictly increasing across the batch.");
        }
        Require(
            queuedTokens.All(AzureDreamsTownReceiveQueue.IsGiftToken),
            "A gift token left the sign-bit range the game's cursor guard tests.");
        Require(
            ReadRawReceiveCursor(memory) == cursorBeforeGifts,
            $"Gift deliveries moved the durable receive cursor from {cursorBeforeGifts}.");

        // Re-running the whole batch after delivery must be idempotent.
        AzureDreamsGiftService.DeliverPendingGifts(
            null, memory, 0, batch, "Septic",
            ordinaryQueueDrained: true, out _);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(
                memory,
                out AzureDreamsTownReceiveQueueState finalState,
                out string finalMessage) &&
            finalState.InFlight.Count == 0,
            finalState.InFlight.Count == 0
                ? finalMessage
                : "A fully delivered gift batch re-queued an item on a later poll.");

        ResetSyntheticReceiveQueue(memory);
        AzureDreamsGiftService.ResetIncomingStateForTest();
        AzureDreamsTownReceiveWindow.ResetForTest();
    }

    /// <summary>
    /// A save whose durable receive cursor was overwritten with a gift
    /// sequence (a pre-guard town dispatcher committed every delivered
    /// sequence) is repaired back to the client's last observed cursor -
    /// covering both the legacy 0x40000000 gift range and the current
    /// sign-bit range - while an untrusted or missing baseline leaves the
    /// save untouched.
    /// </summary>
    private static void TestGiftCursorRepair(DuckStationMemory memory)
    {
        Require(
            AzureDreamsReceiveState.TryReadReceivedItemCount(
                memory, out uint originalCursor, out string originalMessage),
            originalMessage);

        foreach (uint corrupt in new[] { 0x4000_0001u, 0x8000_0002u })
        {
            Require(
                AzureDreamsReceiveState.TryWriteReceivedItemCount(
                    memory, corrupt, out string stageMessage),
                stageMessage);
            Require(
                AzureDreamsArchipelagoClient.TryRepairGiftCorruptedReceiveCursor(
                    memory,
                    serverHistoryCount: 20,
                    baselineCursor: 12,
                    out bool repaired,
                    out string repairMessage),
                repairMessage);
            Require(
                repaired,
                $"A gift-corrupted receive cursor (0x{corrupt:x8}) was not repaired.");
            Require(
                AzureDreamsReceiveState.TryReadReceivedItemCount(
                    memory, out uint repairedCursor, out string readMessage) &&
                repairedCursor == 12,
                $"The repaired receive cursor is not the baseline ({readMessage}).");
        }

        // No baseline (fresh attach, no checkpoint): report, do not guess.
        Require(
            AzureDreamsReceiveState.TryWriteReceivedItemCount(
                memory, 0x8000_0002u, out string restageMessage),
            restageMessage);
        Require(
            AzureDreamsArchipelagoClient.TryRepairGiftCorruptedReceiveCursor(
                memory, 20, null, out bool unrepaired, out string warning) &&
            !unrepaired &&
            warning.Length > 0,
            "A corrupt cursor without a baseline should warn without repairing.");
        Require(
            AzureDreamsReceiveState.TryReadReceivedItemCount(
                memory, out uint untouched, out _) &&
            untouched == 0x8000_0002u,
            "An unrepairable corrupt cursor was modified.");

        // A baseline that is itself in the gift range (restored from a
        // checkpoint captured after the corruption) is not trusted.
        Require(
            AzureDreamsArchipelagoClient.TryRepairGiftCorruptedReceiveCursor(
                memory, 20, 0x4000_0001u, out unrepaired, out _) &&
            !unrepaired,
            "A gift-range baseline was trusted for cursor repair.");

        // A sane cursor is left alone even when a baseline disagrees.
        Require(
            AzureDreamsReceiveState.TryWriteReceivedItemCount(
                memory, originalCursor, out string restoreMessage),
            restoreMessage);
        Require(
            AzureDreamsArchipelagoClient.TryRepairGiftCorruptedReceiveCursor(
                memory, 20, 5, out bool touched, out string saneMessage) &&
            !touched &&
            saneMessage.Length == 0,
            "A sane receive cursor was disturbed by the repair pass.");
        Require(
            AzureDreamsReceiveState.TryReadReceivedItemCount(
                memory, out uint finalCursor, out _) &&
            finalCursor == originalCursor,
            "The receive cursor did not survive the repair self-test.");
    }

    /// <summary>
    /// The send-token counter, its witness, and the banked count that makes
    /// a grant exactly-once.
    /// </summary>
    private static void TestSendTokens(DuckStationMemory memory)
    {
        Span<byte> pair = stackalloc byte[8];

        // No witness: the pair has never been touched by either seeder, and
        // the honest reading is the starting grant rather than zero - the
        // game will hand it over the moment anything asks. Reporting zero
        // would show a "no tokens" state the player never actually sees.
        pair.Clear();
        Require(
            memory.TryWrite(
                AzureDreamsReceiveState.SendTokenCountAddress, pair, out string? seedError),
            seedError ?? "Could not clear the send-token pair.");
        Require(
            AzureDreamsReceiveState.TryReadSendTokens(
                memory, out uint tokens, out string tokenMessage) &&
            tokens == AzureDreamsReceiveState.SendTokenStartingCount,
            tokenMessage.Length > 0
                ? tokenMessage
                : "An unwitnessed send-token pair did not report the starting token.");

        // Witnessed and empty is a real zero: the player spent them all, and
        // re-granting the starting token here would make sends free.
        BinaryPrimitives.WriteUInt32LittleEndian(pair, 0);
        BinaryPrimitives.WriteUInt32LittleEndian(
            pair[4..], AzureDreamsReceiveState.SendTokenMagic);
        Require(
            memory.TryWrite(
                AzureDreamsReceiveState.SendTokenCountAddress, pair, out seedError),
            seedError ?? "Could not stage a witnessed send-token pair.");
        Require(
            AzureDreamsReceiveState.TryReadSendTokens(
                memory, out tokens, out tokenMessage) && tokens == 0,
            tokenMessage.Length > 0
                ? tokenMessage
                : "A witnessed empty send-token count did not read as zero.");

        Require(
            AzureDreamsReceiveState.TryGrantSendTokens(
                memory, 2, out uint total, out tokenMessage) && total == 2,
            tokenMessage.Length > 0
                ? tokenMessage
                : "Granting two send tokens did not add them to the counter.");

        // A grant onto an UNWITNESSED pair has to write the witness too.
        // Without it the tower gate is still free to decide the save is new
        // and overwrite the delivered tokens with the starting one.
        pair.Clear();
        Require(
            memory.TryWrite(
                AzureDreamsReceiveState.SendTokenCountAddress, pair, out seedError),
            seedError ?? "Could not clear the send-token pair.");
        Require(
            AzureDreamsReceiveState.TryGrantSendTokens(
                memory, 1, out total, out tokenMessage) &&
            total == AzureDreamsReceiveState.SendTokenStartingCount + 1,
            tokenMessage.Length > 0
                ? tokenMessage
                : "A grant onto a fresh pair lost the starting token.");
        Require(
            memory.TryRead(
                AzureDreamsReceiveState.SendTokenCountAddress, pair, out seedError) &&
            BinaryPrimitives.ReadUInt32LittleEndian(pair[4..]) ==
                AzureDreamsReceiveState.SendTokenMagic,
            "A send-token grant left the pair unwitnessed; the tower gate " +
            "would re-seed it and throw the delivery away.");

        // The banked counter: the eager grant's exactly-once memory, the
        // gold-granted counter's twin.
        Require(
            AzureDreamsReceiveState.TryWriteSendTokensBankedCount(
                memory, 3, out tokenMessage),
            tokenMessage);
        Require(
            AzureDreamsReceiveState.TryReadSendTokensBankedCount(
                memory, out uint banked, out tokenMessage) && banked == 3,
            tokenMessage.Length > 0
                ? tokenMessage
                : "The send-token banked counter did not round-trip.");
        Require(
            AzureDreamsReceiveState.TryWriteSendTokensBankedCount(
                memory, 0, out tokenMessage),
            tokenMessage);

        // The pair and the bank must both sit past the ADSV record and
        // inside the checkpoint region, so a rollback reverts them with the
        // receive cursor rather than stranding a token on either side.
        Require(
            AzureDreamsReceiveState.SendTokenCountAddress >=
                AzureDreamsReceiveState.PersistentStateAddress +
                AzureDreamsReceiveState.PersistentStateSize &&
            AzureDreamsReceiveState.SendTokenBankedAddress + 4 <= 0x8001_6000,
            "The send-token counters no longer sit in the reserved save tail.");
    }

    /// <summary>
    /// Keycards apply from full received history immediately, regardless of
    /// where they sit relative to the sequential receive cursor - so a
    /// keycard behind a blocked inventory item still raises clearance now.
    /// </summary>
    private static void TestKeycardsCutInLine(DuckStationMemory memory)
    {
        Require(
            AzureDreamsReceiveState.TrySetProgressiveKeycardLevel(
                memory, 0, out string resetMessage),
            resetMessage);

        const long keycard = AzureDreamsArchipelagoClient.ProgressiveKeycardItemIdForTest;
        // A real inventory item id (medicinal herb), distinct from the
        // keycard id, so the counting is exercised against a non-keycard.
        long inventoryItem = AzureDreamsItemManifest.EncodeProtocolItemId(1, 1, 0);

        // History: a blocked inventory item, then two keycards behind it.
        // The keycards must apply even though the item ahead has not
        // "delivered" (the sequential cursor is still 0).
        long[] history = [inventoryItem, keycard, keycard];
        Require(
            AzureDreamsArchipelagoClient.SynchronizeProgressiveKeycards(
                memory, history, out string syncMessage),
            syncMessage);
        Require(
            AzureDreamsReceiveState.TryReadProgressiveKeycardLevel(
                memory, out byte level, out string readMessage) && level == 2,
            $"Two queued-behind keycards did not raise clearance to 2 ({readMessage}).");

        // Idempotent: a second pass with the same history changes nothing.
        Require(
            AzureDreamsArchipelagoClient.SynchronizeProgressiveKeycards(
                memory, history, out syncMessage),
            syncMessage);
        Require(
            AzureDreamsReceiveState.TryReadProgressiveKeycardLevel(
                memory, out level, out _) && level == 2,
            "A repeat keycard sync changed the clearance level.");

        // A third keycard arrives; clearance rises to 3 without any cursor
        // movement or inventory delivery.
        long[] grown = [inventoryItem, keycard, keycard, keycard];
        Require(
            AzureDreamsArchipelagoClient.SynchronizeProgressiveKeycards(
                memory, grown, out syncMessage),
            syncMessage);
        Require(
            AzureDreamsReceiveState.TryReadProgressiveKeycardLevel(
                memory, out level, out _) && level == 3,
            "A newly received keycard did not raise clearance while items were queued.");

        // Beyond the maximum is rejected, not silently clamped.
        long[] overflow = new long[AzureDreamsReceiveState.MaximumKeycardLevel + 1];
        Array.Fill(overflow, keycard);
        Require(
            !AzureDreamsArchipelagoClient.SynchronizeProgressiveKeycards(
                memory, overflow, out _),
            "More keycards than the maximum were accepted.");

        Require(
            AzureDreamsReceiveState.TrySetProgressiveKeycardLevel(
                memory, 0, out resetMessage),
            resetMessage);
    }

    /// <summary>
    /// The blacksmith's sands apply from the full received history to the
    /// two temper level bytes, exactly as keycards apply to clearance:
    /// eagerly, idempotently, independently of the receive cursor, and each
    /// colour to its own byte - and the ball charger's White Sand to the
    /// level byte beside ADSV the same way.
    /// </summary>
    private static void TestTemperSandsCutInLine(DuckStationMemory memory)
    {
        Require(
            AzureDreamsReceiveState.TrySetTemperLevels(memory, 0, 0, out string resetMessage),
            resetMessage);
        Require(
            AzureDreamsReceiveState.TrySetBallChargeLevel(memory, 0, out resetMessage),
            resetMessage);

        long red = AzureDreamsArchipelagoClient.RedSandItemIdForTest;
        long blue = AzureDreamsArchipelagoClient.BlueSandItemIdForTest;
        long white = AzureDreamsArchipelagoClient.WhiteSandItemIdForTest;
        long inventoryItem = AzureDreamsItemManifest.EncodeProtocolItemId(1, 1, 0);
        Require(
            red == 0x0AD0_5020 && blue == 0x0AD0_5040 && white == 0x0AD0_5060,
            "The sand protocol ids drifted from the apworld's (category 10, ids 1, 2 and 3).");
        Require(
            AzureDreamsReceiveState.BallChargeLevelAddress == 0x8001_5F90,
            "The ball charge level byte moved away from the word below ADSV.");

        long[] history = [inventoryItem, red, blue, red, white, white, white];
        Require(
            AzureDreamsArchipelagoClient.SynchronizeTemperSands(memory, history, out string syncMessage),
            syncMessage);
        Require(
            AzureDreamsReceiveState.TryReadTemperLevels(
                memory, out byte weapon, out byte shield, out string readMessage) &&
            weapon == 2 && shield == 1,
            $"Two Red and one Blue Sand did not become weapon 2 / shield 1 ({readMessage}).");
        Require(
            AzureDreamsReceiveState.TryReadBallChargeLevel(memory, out byte charge, out readMessage) &&
            charge == 3,
            $"Three White Sands did not become ball charge level 3 ({readMessage}).");

        Require(
            AzureDreamsArchipelagoClient.SynchronizeTemperSands(memory, history, out syncMessage),
            syncMessage);
        Require(
            AzureDreamsReceiveState.TryReadTemperLevels(memory, out weapon, out shield, out _) &&
            weapon == 2 && shield == 1,
            "A repeat sand sync changed the temper levels.");
        Require(
            AzureDreamsReceiveState.TryReadBallChargeLevel(memory, out charge, out _) && charge == 3,
            "A repeat sand sync changed the ball charge level.");
        Require(
            AzureDreamsArchipelagoClient.BallChargeUsesForLevel(0) == 0 &&
            AzureDreamsArchipelagoClient.BallChargeUsesForLevel(1) == 1 &&
            AzureDreamsArchipelagoClient.BallChargeUsesForLevel(3) == 3 &&
            AzureDreamsTowerProgress.BallChargeUsesPerVisit.AsSpan().SequenceEqual(
                (int[])[0, 1, 2, 3]) &&
            AzureDreamsTowerProgress.BallChargeCeiling == 10,
            "The ball charger's per-visit allowance drifted from ball_charger.USES_BY_LEVEL.");

        long[] overflow = new long[AzureDreamsReceiveState.MaximumTemperLevel + 1];
        Array.Fill(overflow, red);
        Require(
            !AzureDreamsArchipelagoClient.SynchronizeTemperSands(memory, overflow, out _),
            "More Red Sands than the maximum were accepted.");

        // The intro flags share the word: setting the levels leaves them alone.
        Require(
            AzureDreamsReceiveState.TrySetTemperLevels(memory, 0, 0, out resetMessage),
            resetMessage);
        Require(
            AzureDreamsReceiveState.TrySetBallChargeLevel(memory, 0, out resetMessage),
            resetMessage);
    }

    /// <summary>
    /// Line-cutters in the pending history must not chop the town batch.
    ///
    /// <para>Pins the 2026-08-06 one-item-per-conversation report: the append
    /// loop used to stop at the first keycard or gold package, so a history
    /// interleaved with them delivered one item per Nada talk. The planner
    /// walks past them - their grants are cursor-independent - and stages
    /// the whole remaining batch in one pass.</para>
    /// </summary>
    private static void TestLineCuttersDoNotChopTheTownBatch()
    {
        long keycard = AzureDreamsArchipelagoClient.ProgressiveKeycardItemIdForTest;
        long gold = AzureDreamsArchipelagoClient.GoldPackageItemIdForTest;
        long trap = AzureDreamsArchipelagoClient.TrapItemIdBaseForTest + 11; // poison
        const long item = 0x0AD2_7840; // any ordinary native reward id

        // Traps are line-cutters too - sprung by the location-check path,
        // never queued - and the id family test must not swallow its
        // neighbours: the base itself (below the first real trap id) and
        // the first native encoding are both ordinary.
        Require(
            AzureDreamsArchipelagoClient.IsTrapItemId(trap) &&
            AzureDreamsArchipelagoClient.IsTrapItemId(
                AzureDreamsArchipelagoClient.TrapItemIdBaseForTest + 19) &&
            !AzureDreamsArchipelagoClient.IsTrapItemId(
                AzureDreamsArchipelagoClient.TrapItemIdBaseForTest) &&
            !AzureDreamsArchipelagoClient.IsTrapItemId(
                AzureDreamsArchipelagoClient.TrapItemIdBaseForTest + 20) &&
            !AzureDreamsArchipelagoClient.IsTrapItemId(keycard) &&
            !AzureDreamsArchipelagoClient.IsTrapItemId(gold) &&
            !AzureDreamsArchipelagoClient.IsTrapItemId(item),
            "The trap item-id family test drew the wrong boundary.");

        long[] withTrap = [item, trap, item];
        Require(
            AzureDreamsArchipelagoClient.PlanTownQueueAppends(withTrap, 0, 16)
                .SequenceEqual(new uint[] { 0, 2 }),
            "A trap item was staged for town delivery; it must cut the " +
            "line like a keycard (the spring is the location-check path's).");

        // The blacksmith's sands are line-cutters too: levels, not inventory.
        long[] withSands = [
            item,
            AzureDreamsArchipelagoClient.RedSandItemIdForTest,
            AzureDreamsArchipelagoClient.BlueSandItemIdForTest,
            item,
        ];
        Require(
            AzureDreamsArchipelagoClient.PlanTownQueueAppends(withSands, 0, 16)
                .SequenceEqual(new uint[] { 0, 3 }),
            "A temper sand was staged for town delivery; it must raise the " +
            "smith's level and never enter the bag.");

        long[] interleaved = [item, keycard, item, gold, item, keycard, gold, item];
        Require(
            AzureDreamsArchipelagoClient.PlanTownQueueAppends(interleaved, 0, 16)
                .SequenceEqual(new uint[] { 0, 2, 4, 7 }),
            "An interleaved history did not stage as one batch; each " +
            "line-cutter costs the player an extra Nada conversation.");

        // The free-slot bound still applies to what is actually staged.
        Require(
            AzureDreamsArchipelagoClient.PlanTownQueueAppends(interleaved, 0, 2)
                .SequenceEqual(new uint[] { 0, 2 }),
            "The append plan overran the queue's free slots.");

        // Starting mid-history (entries already in flight) keeps walking.
        Require(
            AzureDreamsArchipelagoClient.PlanTownQueueAppends(interleaved, 3, 16)
                .SequenceEqual(new uint[] { 4, 7 }),
            "A mid-history start did not walk past line-cutters.");

        // Nothing but line-cutters stages nothing - the skip branch owns
        // advancing the cursor over them once the queue drains.
        Require(
            AzureDreamsArchipelagoClient.PlanTownQueueAppends(
                [keycard, gold, keycard], 0, 16).Count == 0,
            "A pure line-cutter run staged queue entries.");
    }

    /// <summary>
    /// A byte-backed stand-in for the emulator, for the trap pump: the pump
    /// is pure request-byte protocol and needs no live DuckStation.
    /// </summary>
    private sealed class FakeEmulatorMemory : Adap.Client.Emulators.IEmulatorMemory
    {
        private readonly Dictionary<uint, byte> _bytes = [];

        public string EmulatorName => "self-test fake";
        public int ProcessId => 0;
        public int RamSize => 0x20_0000;

        public void Dispose() { }

        public bool TryRead(uint psxAddress, Span<byte> destination, out string? error)
        {
            for (int index = 0; index < destination.Length; index++)
                destination[index] = _bytes.GetValueOrDefault((uint)(psxAddress + index));
            error = null;
            return true;
        }

        public bool TryWrite(uint psxAddress, ReadOnlySpan<byte> source, out string? error)
        {
            for (int index = 0; index < source.Length; index++)
                _bytes[(uint)(psxAddress + index)] = source[index];
            error = null;
            return true;
        }

        public byte ReadByte(uint address) => _bytes.GetValueOrDefault(address);

        public void WriteByte(uint address, byte value) => _bytes[address] = value;
    }

    /// <summary>
    /// The forced-trap request-byte protocol: one write in flight, the head
    /// popped only when the stub is seen to consume it, the next trap
    /// following in the same pump - and, since 2026-08-18, a trap that has
    /// left the floor it was picked up on being DROPPED rather than carried.
    ///
    /// <para>The failure that rule exists for: the first frame of a freshly
    /// loaded floor is Koh's turn, so a trap sprung there spends it and the
    /// floor's monsters move first, against a player who has not seen the
    /// room yet. A trap arriving late always arrives exactly there.</para>
    /// </summary>
    private static void TestForcedTrapPump()
    {
        const uint requestAddress =
            AzureDreamsArchipelagoClient.ForcedTrapRequestAddressForTest;
        // The byte lived INSIDE the ADGT tower-gift record for four world
        // versions - on its magic - and the two overwrote each other. Only
        // solo seeds were tested, and a solo room has no Send row at all.
        Require(
            requestAddress < AzureDreamsGiftService.TowerGiftMailboxAddressForTest ||
            requestAddress >= AzureDreamsGiftService.TowerGiftMailboxAddressForTest +
                AzureDreamsGiftService.TowerGiftMailboxSizeForTest,
            "The forced-trap request byte overlaps the ADGT tower-gift record.");
        var memory = new FakeEmulatorMemory();
        StandOnFloor(memory, 7);
        var traps = new AzureDreamsArchipelagoClient.ForcedTrapQueue();
        traps.Pending.Add(new AzureDreamsArchipelagoClient.PendingTrap(11, 7)); // poison
        traps.Pending.Add(new AzureDreamsArchipelagoClient.PendingTrap(7, 7));  // bomb

        AzureDreamsArchipelagoClient.PumpForcedTraps(memory, traps);
        Require(
            memory.ReadByte(requestAddress) == 11 && traps.WriteInFlight &&
            traps.Pending.Count == 2,
            "The first pump did not write the queue head into the request byte.");

        // Unconsumed: nothing changes, nothing is lost, nothing overwritten.
        AzureDreamsArchipelagoClient.PumpForcedTraps(memory, traps);
        Require(
            memory.ReadByte(requestAddress) == 11 && traps.Pending.Count == 2,
            "A pump with the write still in flight disturbed the queue.");

        // The stub consumed it: the head pops and the next trap goes out in
        // the same pump.
        memory.WriteByte(requestAddress, 0);
        AzureDreamsArchipelagoClient.PumpForcedTraps(memory, traps);
        Require(
            memory.ReadByte(requestAddress) == 7 && traps.WriteInFlight &&
            traps.Pending.Count == 1,
            "A consumed write did not advance the queue to the next trap.");

        memory.WriteByte(requestAddress, 0);
        AzureDreamsArchipelagoClient.PumpForcedTraps(memory, traps);
        Require(
            memory.ReadByte(requestAddress) == 0 && !traps.WriteInFlight &&
            traps.Pending.Count == 0,
            "Draining the queue left state behind.");

        // A manual poke (or another writer) is left alone entirely.
        memory.WriteByte(requestAddress, 4);
        AzureDreamsArchipelagoClient.PumpForcedTraps(memory, traps);
        Require(
            memory.ReadByte(requestAddress) == 4,
            "The pump disturbed a request byte it did not write.");
        memory.WriteByte(requestAddress, 0);

        // Leaving the floor strands the trap: the elevator, the walk back to
        // town, a death, a reload. All four look the same from here, and all
        // four would otherwise hand the trap to the next floor's first frame.
        traps.Pending.Add(new AzureDreamsArchipelagoClient.PendingTrap(11, 7));
        AzureDreamsArchipelagoClient.PumpForcedTraps(memory, traps);
        Require(
            memory.ReadByte(requestAddress) == 11 && traps.WriteInFlight,
            "A trap picked up on the live floor was not written out.");
        StandOnFloor(memory, 8);
        AzureDreamsArchipelagoClient.PumpForcedTraps(memory, traps);
        Require(
            traps.Pending.Count == 0 && !traps.WriteInFlight &&
            memory.ReadByte(requestAddress) == 0,
            "A trap survived the floor it was picked up on, and its request " +
            "byte was left armed for the next floor.");

        // Back in town, nothing may be armed at all.
        traps.Pending.Add(new AzureDreamsArchipelagoClient.PendingTrap(9, 8));
        LeaveTheTower(memory);
        AzureDreamsArchipelagoClient.PumpForcedTraps(memory, traps);
        Require(
            traps.Pending.Count == 0 && memory.ReadByte(requestAddress) == 0,
            "A trap was still armed after the player left the tower.");

        // The arming rule, which is the half that decides whether a trap ever
        // reaches this queue. Each refusal below is a way a trap used to
        // arrive at a moment it did not belong to.
        Require(
            AzureDreamsArchipelagoClient.IsTrapSpringable(
                armingPrimed: true,
                alreadyInSave: false,
                pickupFloor: 7,
                floorAtLastPoll: 7,
                out _),
            "A live pickup on the floor the player is standing on was refused.");
        Require(
            !AzureDreamsArchipelagoClient.IsTrapSpringable(
                armingPrimed: false,
                alreadyInSave: false,
                pickupFloor: 7,
                floorAtLastPoll: 7,
                out _),
            "The first look at an attached save armed a trap; everything in a " +
            "save the client has not seen before is history, not a pickup.");
        Require(
            !AzureDreamsArchipelagoClient.IsTrapSpringable(
                armingPrimed: true,
                alreadyInSave: true,
                pickupFloor: 7,
                floorAtLastPoll: 7,
                out _),
            "A check collected offline armed its trap when the client finally " +
            "reported it - the exact shape of the death on a loaded floor.");
        Require(
            !AzureDreamsArchipelagoClient.IsTrapSpringable(
                armingPrimed: true,
                alreadyInSave: false,
                pickupFloor: 0,
                floorAtLastPoll: 0,
                out _),
            "A trap armed from outside the tower, where there is no floor to " +
            "spring it on.");
        Require(
            !AzureDreamsArchipelagoClient.IsTrapSpringable(
                armingPrimed: true,
                alreadyInSave: false,
                pickupFloor: 8,
                floorAtLastPoll: 7,
                out _),
            "A pickup noticed only after the player had changed floors armed " +
            "its trap for the floor they had just arrived on.");

        // The reattach case, which is the one that killed a run: the queue and
        // the observed-checks baseline both belong to the attached game.
        traps.Pending.Add(new AzureDreamsArchipelagoClient.PendingTrap(7, 3));
        traps.ObservedGameChecks = [1, 2, 3];
        traps.WriteInFlight = true;
        traps.FloorAtLastPoll = 3;
        traps.ForgetGameSession();
        Require(
            traps.Pending.Count == 0 && !traps.WriteInFlight &&
            traps.ObservedGameChecks is null && traps.FloorAtLastPoll == 0,
            "Forgetting the attached game left a trap or a stale check baseline behind.");
    }

    /// <summary>Puts the fake game in the tower, on a floor.</summary>
    private static void StandOnFloor(FakeEmulatorMemory memory, int floor)
    {
        memory.WriteByte(
            AzureDreamsTownCheckpoint.LoadedModeAddress,
            AzureDreamsTownCheckpoint.TowerMode);
        memory.WriteByte(AzureDreamsTowerProgress.CurrentFloorAddress, (byte)floor);
        memory.WriteByte(AzureDreamsTowerProgress.CurrentFloorAddress + 1, 0);
    }

    private static void LeaveTheTower(FakeEmulatorMemory memory) =>
        memory.WriteByte(AzureDreamsTownCheckpoint.LoadedModeAddress, 0);

    /// <summary>
    /// The town receive queue Nada drains from inside her own conversation.
    ///
    /// <para>Passive town delivery is what raced an NPC talk for the native
    /// script queue and crashed at Nada and in the Monster hut. The client
    /// now only ever appends here, and the game owns the durable cursor, so
    /// what this pins is the append protocol and the properties that make it
    /// safe without any cross-process synchronisation.</para>
    /// </summary>
    private static void TestTownReceiveQueue(DuckStationMemory memory)
    {
        AzureDreamsTownReceiveWindow.ResetForTest();
        AzureDreamsGiftService.ResetIncomingStateForTest();
        ResetSyntheticReceiveQueue(memory);

        Require(
            AzureDreamsTownReceiveQueue.TryRead(
                memory,
                out AzureDreamsTownReceiveQueueState state,
                out string readMessage),
            readMessage);
        Require(state.Present, "The synthetic receive queue was not detected.");
        Require(
            state.InFlight.Count == 0 &&
                state.Free == AzureDreamsTownReceiveQueue.Slots,
            "A freshly reset receive queue did not read as empty.");

        // An append writes the entry first and republishes count last, so the
        // game can never see a slot the count claims is valid but which has
        // not been filled in.
        var herb = new AzureDreamsItemDescriptor(1, 1, 0, 0);
        Require(
            AzureDreamsTownReceiveQueue.TryAppend(
                memory, state, herb, 1, out state, out string appendMessage),
            appendMessage);
        Require(
            state.Count == 1 && state.InFlight.Count == 1,
            "An appended entry did not publish the count.");
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                state.InFlight.Count == 1 &&
                state.InFlight[0].Descriptor == herb &&
                state.InFlight[0].Token == 1,
            "The appended entry did not survive a re-read of the queue.");

        // A zero category is the game's own "this slot is free" marker, so
        // storing one would create an invisible item the next allocator hands
        // out twice.
        Require(
            !AzureDreamsTownReceiveQueue.TryAppend(
                memory,
                state,
                new AzureDreamsItemDescriptor(1, 0, 0, 0),
                2,
                out _,
                out _),
            "The queue accepted a descriptor with a zero category.");

        // The game consuming an entry is head advancing, nothing else.
        AdvanceSyntheticQueueHead(memory, 1);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                state.InFlight.Count == 0,
            "A consumed entry stayed in flight.");

        // Nada's box stands in for the town gate, because it is the one
        // delivery path that does not pass through it. Returning from the
        // tower unequips (0x20) and appraises (0x80) everything; an item handed
        // over while the player is ALREADY in town skips that, and every town
        // menu copies the descriptor word into its rows - where 0x20 reads as
        // "this row is checked" and 0x80 as "this row cannot be selected". An
        // unidentified sword delivered by Nada could not be sold at the
        // Equipment shop, and could not be picked in her own send list.
        Require(
            AzureDreamsTownReceiveQueue.ApplyTownGateClear(
                new AzureDreamsItemDescriptor(2, 15, -1, 0xC0)).Flags == 0x40 &&
            AzureDreamsTownReceiveQueue.ApplyTownGateClear(
                new AzureDreamsItemDescriptor(2, 15, 2, 0xA0)).Flags == 0x00,
            "The town gate clear did not strip unidentified and equipped.");
        // Cursed survives - the gate does not lift a curse, and the shops draw
        // a skull for it - and so does a familiar's monster-hut index in the
        // low five bits.
        Require(
            AzureDreamsTownReceiveQueue.ApplyTownGateClear(
                new AzureDreamsItemDescriptor(4, 0x13, 0, 0xA7)).Flags == 0x07,
            "The town gate clear ate a familiar's monster-hut index.");
        // And it is applied by the append itself, so both the bytes the game
        // reads and the client's own in-flight record agree.
        Require(
            AzureDreamsTownReceiveQueue.TryAppend(
                memory,
                state,
                new AzureDreamsItemDescriptor(2, 15, -1, 0xC0),
                2,
                out state,
                out appendMessage) &&
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
            state.InFlight.Count == 1 &&
            state.InFlight[0].Descriptor.Flags == 0x40 &&
            state.InFlight[0].Descriptor.Quality == -1,
            "A town-delivered item reached Nada's queue still carrying tower flags.");
        AdvanceSyntheticQueueHead(memory, 1);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                state.InFlight.Count == 0,
            readMessage);

        // The under-fold behind the duplicated Nada receive, reproduced exactly,
        // and the absolute watermark that recovers it.
        //
        // The delta observer clamps what it reports to the entries it had
        // recorded. Entries appended after a poll's snapshot and consumed
        // before the next one fall outside that list and are dropped, the
        // cursor folds short, and everything short-folded is re-delivered by
        // the tower on entry. Reading the record instead sees all of it.
        AzureDreamsTownReceiveQueue.ResetObservationForTest();
        ResetSyntheticReceiveQueue(memory);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        for (uint token = 1; token <= 3; token++)
        {
            Require(
                AzureDreamsTownReceiveQueue.TryAppend(
                    memory, state, herb, token, out state, out appendMessage),
                appendMessage);
        }
        // One poll sees three entries and nothing consumed yet.
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state) == 0,
            "A queue that has delivered nothing reported a consumption.");
        // The client appends two more AFTER that snapshot - which is what a
        // stale lock timing out makes routine - and Nada drains all five
        // before the next poll.
        for (uint token = 4; token <= 5; token++)
        {
            Require(
                AzureDreamsTownReceiveQueue.TryAppend(
                    memory, state, herb, token, out state, out appendMessage),
                appendMessage);
        }
        AdvanceSyntheticQueueHead(memory, 5);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        Require(
            AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state) == 3,
            "The delta observer stopped under-folding, so this no longer reproduces " +
            "the duplication and the watermark below is being tested against nothing.");
        Require(
            AzureDreamsTownReceiveQueue.TryReadDeliveredThrough(
                memory, state, out uint deliveredThrough, out string deliveredMessage) &&
                deliveredThrough == 5,
            $"The delivered-through watermark missed entries no poll observed " +
            $"({deliveredMessage}).");

        // A recalled entry was never delivered, so it must not read as though
        // it were - that would lose the item rather than duplicate it.
        ResetSyntheticReceiveQueue(memory);
        AzureDreamsTownReceiveQueue.ResetObservationForTest();
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        for (uint token = 1; token <= 2; token++)
        {
            Require(
                AzureDreamsTownReceiveQueue.TryAppend(
                    memory, state, herb, token, out state, out appendMessage),
                appendMessage);
        }
        AdvanceSyntheticQueueHead(memory, 1);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        Require(
            AzureDreamsTownReceiveQueue.TryRecallInFlight(
                memory, state, out state, out string recallMessage),
            recallMessage);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                state.InFlight.Count == 0 &&
                AzureDreamsTownReceiveQueue.TryReadDeliveredThrough(
                    memory, state, out deliveredThrough, out deliveredMessage) &&
                deliveredThrough == 1,
            "A recalled entry read as delivered; the tower would never re-deliver it.");

        // A gift never touches the receive cursor, so it cannot raise the
        // watermark either.
        ResetSyntheticReceiveQueue(memory);
        AzureDreamsTownReceiveQueue.ResetObservationForTest();
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                AzureDreamsTownReceiveQueue.TryAppend(
                    memory, state, herb, 1, out state, out appendMessage) &&
                AzureDreamsTownReceiveQueue.TryAppend(
                    memory,
                    state,
                    herb,
                    AzureDreamsTownReceiveQueue.GiftTokenBase + 7,
                    out state,
                    out appendMessage),
            appendMessage);
        AdvanceSyntheticQueueHead(memory, 2);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                AzureDreamsTownReceiveQueue.TryReadDeliveredThrough(
                    memory, state, out deliveredThrough, out deliveredMessage) &&
                deliveredThrough == 1,
            "A delivered gift raised the ordinary receive watermark.");

        AzureDreamsTownReceiveQueue.ResetObservationForTest();

        // Sixteen slots divide 256, so the free-running byte cursors may wrap
        // forever without the index arithmetic drifting. That is what removes
        // the reset step - and a reset is a second writer on a byte, which is
        // the exact race being designed out.
        ResetSyntheticReceiveQueue(memory, count: 0xFE, head: 0xFE);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        for (int index = 0; index < 4; index++)
        {
            Require(
                AzureDreamsTownReceiveQueue.TryAppend(
                    memory, state, herb, (uint)(index + 1), out state, out appendMessage),
                appendMessage);
        }
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                state.InFlight.Count == 4 &&
                state.Count == 0x02,
            $"The queue lost entries across byte-cursor wraparound ({readMessage}).");
        for (int index = 0; index < 4; index++)
        {
            Require(
                state.InFlight[index].Token == (uint)(index + 1),
                "Wraparound reordered the queue.");
        }

        // The queue is self-describing: the next ordinary index is derived
        // from the live tokens and the durable cursor, never from client-side
        // bookkeeping, which is what lets a restart reconcile with no saved
        // state. Progressive keycards leave gaps in the history, so the max
        // (not the count) is what is correct.
        ResetSyntheticReceiveQueue(memory);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        Require(
            AzureDreamsTownReceiveQueue.NextOrdinaryIndex(state, 7) == 7,
            "An empty queue did not resume from the durable cursor.");
        Require(
            AzureDreamsTownReceiveQueue.TryAppend(
                memory, state, herb, 12, out state, out appendMessage),
            appendMessage);
        Require(
            AzureDreamsTownReceiveQueue.NextOrdinaryIndex(state, 7) == 12,
            "A history gap left by keycards was re-queued instead of skipped.");
        // A gift in flight must not move the ordinary cursor derivation.
        Require(
            AzureDreamsTownReceiveQueue.TryAppend(
                memory,
                state,
                herb,
                AzureDreamsTownReceiveQueue.GiftTokenBase + 9,
                out state,
                out appendMessage),
            appendMessage);
        Require(
            AzureDreamsTownReceiveQueue.NextOrdinaryIndex(state, 7) == 12,
            "A gift token was counted as an ordinary receive.");
        Require(
            AzureDreamsTownReceiveQueue.HasOrdinaryInFlight(state),
            "An ordinary entry in flight was not reported.");

        // The lock is not a safety mechanism - the game bounds each
        // conversation by the count it snapshotted - so a lock the game never
        // dropped must go stale rather than freeze the queue forever.
        DateTime now = DateTime.UtcNow;
        var locked = state with { Locked = true };
        AzureDreamsTownReceiveWindow.ResetForTest();
        Require(
            !AzureDreamsTownReceiveWindow.AllowsAppend(locked, now),
            "The client appended while Nada's conversation held the lock.");
        Require(
            !AzureDreamsTownReceiveWindow.AllowsAppend(
                locked, now + TimeSpan.FromSeconds(5)),
            "A lock held for five seconds was already treated as stale.");
        Require(
            AzureDreamsTownReceiveWindow.AllowsAppend(
                locked,
                now + AzureDreamsTownReceiveWindow.StaleLockTimeout +
                    TimeSpan.FromSeconds(1)),
            "A lock held far past the timeout never went stale, which would " +
            "freeze the queue until the town reloaded.");
        AzureDreamsTownReceiveWindow.ResetForTest();
        Require(
            AzureDreamsTownReceiveWindow.AllowsAppend(state, now),
            "The client refused to append with the lock clear.");

        ResetSyntheticReceiveQueue(memory);
        AzureDreamsGiftService.ResetIncomingStateForTest();
        AzureDreamsTownReceiveWindow.ResetForTest();
    }

    /// <summary>
    /// The queue record is resident in BOTH modes, so its presence must
    /// never be mistaken for "this is town".
    ///
    /// <para>Pins the 2026-08-02 regression: moving the record out of the
    /// town slab into the resident block made it readable in the tower, and
    /// the delivery branch tested `queue.Present` rather than the mode.
    /// Tower receives were routed into a queue only Nada drains, so items
    /// stopped arriving in the tower at all. Nada is the town method, not
    /// the only method - the tower keeps its native pickup.</para>
    /// </summary>
    private static void TestTowerDoesNotRouteThroughTheTownQueue(DuckStationMemory memory)
    {
        AzureDreamsTownReceiveQueue.ResetObservationForTest();
        ResetSyntheticReceiveQueue(memory);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(
                memory,
                out AzureDreamsTownReceiveQueueState state,
                out string readMessage),
            readMessage);

        // The record is present. That is now true in the tower as well, so
        // presence alone must not be a routing signal.
        Require(state.Present, "the synthetic queue record was not detected");
        Require(
            AzureDreamsTownReceiveQueue.Address >= 0x801d_9700 &&
                AzureDreamsTownReceiveQueue.Address < 0x801d_a700,
            "the queue must live in the resident block, not the town slab - " +
            "that is what makes it survive a tower trip.");

        // A tower delivery recalls queued entries rather than leaving them
        // stranded: the entry was never taken, so the item is still owed and
        // the native path should hand it over.
        var herb = new AzureDreamsItemDescriptor(1, 1, 0, 0);
        Require(
            AzureDreamsTownReceiveQueue.TryAppend(
                memory, state, herb, 1, out state, out string appendMessage),
            appendMessage);
        Require(state.InFlight.Count == 1, "the entry was not queued");
        Require(
            state.Head == 0,
            "a recall must move `count` back to `head`, never move `head` - " +
            "`head` is the game's byte and moving it would forge a delivery.");

        ResetSyntheticReceiveQueue(memory);
        AzureDreamsTownReceiveQueue.ResetObservationForTest();
    }

    /// <summary>
    /// A receive request stranded in the resident mailbox across a town
    /// round-trip is cancelled by the town poll, so the dispatcher cannot
    /// retry it on the next tower entry.
    ///
    /// <para>This pins the 2026-08-05 root cause of the Nada receive
    /// duplication: a request staged in the tower survived the trip -
    /// undelivered because Koh never idled, or wedged on inventory-full - and
    /// Nada then delivered the same history index in town. On re-entry the
    /// dispatcher retried the stale request, duplicating that item, and the
    /// since-removed cursor-commit stub wrote the stale sequence over the
    /// durable cursor, re-delivering everything Nada had handed over.</para>
    /// </summary>
    private static void TestStaleTowerRequestIsRecalledInTown(DuckStationMemory memory)
    {
        // Preserve whatever the surrounding suites staged in the mailbox's
        // receive words (request, descriptor, ack, status).
        byte[] saved = new byte[16];
        Require(
            memory.TryRead(
                AzureDreamsMailbox.Address + AzureDreamsMailbox.ReceiveRequestSequenceOffset,
                saved,
                out string? savedError),
            savedError ?? "Could not save the mailbox receive words.");

        void StageStranded(uint request, uint acknowledged, uint status)
        {
            Span<byte> words = stackalloc byte[16];
            BinaryPrimitives.WriteUInt32LittleEndian(words, request);
            BinaryPrimitives.WriteUInt32LittleEndian(words[4..], 0x0000_0101);
            BinaryPrimitives.WriteUInt32LittleEndian(words[8..], acknowledged);
            BinaryPrimitives.WriteUInt32LittleEndian(words[12..], status);
            Require(
                memory.TryWrite(
                    AzureDreamsMailbox.Address + AzureDreamsMailbox.ReceiveRequestSequenceOffset,
                    words,
                    out string? stageError),
                stageError ?? "Could not stage a stranded receive request.");
        }

        (uint Request, uint Acknowledged, uint Status) ReadBack()
        {
            Require(
                AzureDreamsMailbox.TryReadReceiveStatus(
                    memory,
                    out uint request,
                    out uint acknowledged,
                    out uint status,
                    out _,
                    out string statusMessage),
                statusMessage);
            return (request, acknowledged, status);
        }

        // A pending request left over from the previous tower trip.
        StageStranded(6, 5, AzureDreamsMailbox.ReceiveStatusPending);
        Require(
            AzureDreamsMailbox.TryRecallInFlightReceive(
                memory, out uint recalled, out string recallMessage),
            recallMessage);
        Require(recalled == 6, "A stranded pending request was not recalled.");
        (uint Request, uint Acknowledged, uint Status) after = ReadBack();
        Require(
            after.Request == 5 &&
            after.Acknowledged == 5 &&
            after.Status == AzureDreamsMailbox.ReceiveStatusIdle,
            "The recalled request did not leave the mailbox idle at the ack.");

        // Idempotent: an idle mailbox has nothing to recall.
        Require(
            AzureDreamsMailbox.TryRecallInFlightReceive(
                memory, out recalled, out recallMessage),
            recallMessage);
        Require(recalled == 0, "An idle mailbox reported a recalled request.");

        // The inventory-full variant - the common stranding in practice:
        // items arrive while the inventory is full, the request wedges, and
        // the player returns to town precisely BECAUSE they are full.
        StageStranded(9, 8, AzureDreamsMailbox.ReceiveStatusInventoryFull);
        Require(
            AzureDreamsMailbox.TryRecallInFlightReceive(
                memory, out recalled, out recallMessage),
            recallMessage);
        Require(recalled == 9, "A storage-wedged request was not recalled.");

        // A gift-range request belongs to the gift service's own in-flight
        // tracking and must be left alone.
        StageStranded(0x8000_0001, 9, AzureDreamsMailbox.ReceiveStatusPending);
        Require(
            AzureDreamsMailbox.TryRecallInFlightReceive(
                memory, out recalled, out recallMessage),
            recallMessage);
        after = ReadBack();
        Require(
            recalled == 0 &&
            after.Request == 0x8000_0001 &&
            after.Status == AzureDreamsMailbox.ReceiveStatusPending,
            "A gift-range request was disturbed by the town recall.");

        Require(
            memory.TryWrite(
                AzureDreamsMailbox.Address + AzureDreamsMailbox.ReceiveRequestSequenceOffset,
                saved,
                out savedError),
            savedError ?? "Could not restore the mailbox receive words.");
    }

    /// <summary>
    /// Consumption observed by watching `head` cross an entry must fold into
    /// the durable receive cursor.
    ///
    /// <para>This pins the 2026-08-01 duplication: making the game the SOLE
    /// writer of that cursor meant a commit that failed to stick left it
    /// behind, and every item Nada handed over in town was delivered a second
    /// time on tower entry. The two mechanisms are now independent - either
    /// alone advances the cursor.</para>
    /// </summary>
    private static void TestConsumedItemsFoldIntoTheReceiveCursor(DuckStationMemory memory)
    {
        AzureDreamsTownReceiveQueue.ResetObservationForTest();
        ResetSyntheticReceiveQueue(memory);

        Require(
            AzureDreamsTownReceiveQueue.TryRead(
                memory,
                out AzureDreamsTownReceiveQueueState state,
                out string readMessage),
            readMessage);

        var herb = new AzureDreamsItemDescriptor(1, 1, 0, 0);
        for (uint token = 1; token <= 3; token++)
        {
            Require(
                AzureDreamsTownReceiveQueue.TryAppend(
                    memory, state, herb, token, out state, out string appendMessage),
                appendMessage);
        }

        // Baseline poll: nothing consumed yet.
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        Require(
            AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state) == 0,
            "An untouched queue reported consumption.");

        // The game takes two entries and - the failure being pinned - does
        // NOT advance the cursor.
        AdvanceSyntheticQueueHead(memory, 2);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        Require(
            AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state) == 2,
            "Two consumed entries did not fold to their highest token.");

        // Idempotent: observing again reports nothing new.
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        Require(
            AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state) == 0,
            "The same consumption folded twice.");

        // A gift crossing head must never fold - it would drive the cursor
        // into the sign-bit range and wedge the whole item queue.
        Require(
            AzureDreamsTownReceiveQueue.TryAppend(
                memory,
                state,
                herb,
                AzureDreamsTownReceiveQueue.GiftTokenBase + 5,
                out state,
                out string giftMessage),
            giftMessage);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state);
        AdvanceSyntheticQueueHead(memory, 2);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        uint folded = AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state);
        Require(
            folded == 3,
            $"A consumed gift folded into the ordinary cursor (got {folded}).");

        // A queue reset sends head backwards, not forwards. That must read as
        // "the town reloaded", never as consumption.
        AzureDreamsTownReceiveQueue.ResetObservationForTest();
        ResetSyntheticReceiveQueue(memory, count: 4, head: 4);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state);
        ResetSyntheticReceiveQueue(memory);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage),
            readMessage);
        Require(
            AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(state) == 0,
            "A queue reset was mistaken for the game consuming entries.");

        AzureDreamsTownReceiveQueue.ResetObservationForTest();
        ResetSyntheticReceiveQueue(memory);
    }

    /// <summary>
    /// A gift travels through the same queue as ordinary items but carries a
    /// sign-bit token, which is what makes it structurally impossible for it
    /// to move the durable receive cursor - the wedge fixed on 2026-07-30,
    /// now enforced by the game's own `bltz` guard rather than by a client
    /// rule that could be forgotten.
    /// </summary>
    private static void TestGiftQueuesThroughTheReceiveQueue(DuckStationMemory memory)
    {
        AzureDreamsTownReceiveWindow.ResetForTest();
        AzureDreamsGiftService.ResetIncomingStateForTest();
        ResetSyntheticReceiveQueue(memory);

        var gifts = new List<AzureDreamsGiftService.IncomingGift>
        {
            new(
                "queue-sender:run:1",
                new AzureDreamsItemDescriptor(1, 4, 0, 0),
                "Acid Rain Ball",
                "Sandknight"),
        };

        AzureDreamsGiftService.DeliverPendingGifts(
            null, memory, 0, gifts, "Septic",
            ordinaryQueueDrained: true, out bool deliveredInTown);
        Require(
            !deliveredInTown,
            "A gift reported delivery before the game had consumed it.");
        Require(
            AzureDreamsTownReceiveQueue.TryRead(
                memory,
                out AzureDreamsTownReceiveQueueState state,
                out string readMessage),
            readMessage);
        Require(state.InFlight.Count == 1, "The gift was not queued.");
        uint token = state.InFlight[0].Token;
        Require(
            AzureDreamsTownReceiveQueue.IsGiftToken(token),
            "A gift was queued with an ordinary token, which the game would " +
            "commit to the durable receive cursor.");
        Require(
            !AzureDreamsTownReceiveQueue.HasOrdinaryInFlight(state),
            "A gift token was mistaken for an ordinary receive.");

        // Re-polling must not stage a second copy.
        AzureDreamsGiftService.DeliverPendingGifts(
            null, memory, 0, gifts, "Septic", ordinaryQueueDrained: true, out _);
        Require(
            AzureDreamsTownReceiveQueue.TryRead(memory, out state, out readMessage) &&
                state.InFlight.Count == 1,
            "A second poll queued the same gift twice.");

        // The town reloading resets the queue. That is indistinguishable from
        // a consumed entry by absence alone, so it must NOT confirm: a
        // re-offer is recoverable, a false confirm loses the gift.
        ResetSyntheticReceiveQueue(memory);
        AzureDreamsGiftService.DeliverPendingGifts(
            null, memory, 0, gifts, "Septic", ordinaryQueueDrained: true, out deliveredInTown);
        Require(
            !deliveredInTown,
            "A town reload that wiped the queue was mistaken for a delivery.");

        // Now the real thing: queued, then consumed by the game.
        ResetSyntheticReceiveQueue(memory);
        AzureDreamsGiftService.ResetIncomingStateForTest();
        AzureDreamsGiftService.DeliverPendingGifts(
            null, memory, 0, gifts, "Septic", ordinaryQueueDrained: true, out _);
        AdvanceSyntheticQueueHead(memory, 1);
        AzureDreamsGiftService.DeliverPendingGifts(
            null, memory, 0, gifts, "Septic", ordinaryQueueDrained: true, out deliveredInTown);
        Require(
            deliveredInTown,
            "A gift the game consumed was never confirmed.");

        // And the durable receive cursor never moved for any of it.
        uint cursor = ReadRawReceiveCursor(memory);
        Require(
            cursor == _cursorBeforeGiftQueueTest,
            $"Gift queueing moved the durable receive cursor to {cursor}.");

        ResetSyntheticReceiveQueue(memory);
        AzureDreamsGiftService.ResetIncomingStateForTest();
        AzureDreamsTownReceiveWindow.ResetForTest();
    }

    private static uint _cursorBeforeGiftQueueTest;

    private static uint ReadRawReceiveCursor(DuckStationMemory memory)
    {
        Span<byte> word = stackalloc byte[sizeof(uint)];
        Require(
            memory.TryRead(
                AzureDreamsReceiveState.PersistentStateAddress +
                    AzureDreamsReceiveState.ReceivedItemCountOffset,
                word,
                out string? error),
            error ?? "Could not read the durable receive cursor.");
        return BinaryPrimitives.ReadUInt32LittleEndian(word);
    }

    private static void ResetSyntheticReceiveQueue(
        DuckStationMemory memory,
        byte count = 0,
        byte head = 0)
    {
        Span<byte> record = stackalloc byte[AzureDreamsTownReceiveQueue.Size];
        record.Clear();
        BinaryPrimitives.WriteUInt32LittleEndian(record, AzureDreamsTownReceiveQueue.Magic);
        BinaryPrimitives.WriteUInt16LittleEndian(
            record[4..], AzureDreamsTownReceiveQueue.ProtocolVersion);
        BinaryPrimitives.WriteUInt16LittleEndian(
            record[6..], AzureDreamsTownReceiveQueue.Size);
        record[AzureDreamsTownReceiveQueue.CountOffset] = count;
        record[AzureDreamsTownReceiveQueue.HeadOffset] = head;
        Require(
            memory.TryWrite(AzureDreamsTownReceiveQueue.Address, record, out string? error),
            error ?? "Could not initialize the synthetic receive queue.");
    }

    /// <summary>Plays the game's side: consuming entries is head advancing.</summary>
    private static void AdvanceSyntheticQueueHead(DuckStationMemory memory, int entries)
    {
        Span<byte> current = stackalloc byte[1];
        Require(
            memory.TryRead(
                AzureDreamsTownReceiveQueue.Address + AzureDreamsTownReceiveQueue.HeadOffset,
                current,
                out string? readError),
            readError ?? "Could not read the synthetic queue head.");
        Span<byte> advanced = [unchecked((byte)(current[0] + entries))];
        Require(
            memory.TryWrite(
                AzureDreamsTownReceiveQueue.Address + AzureDreamsTownReceiveQueue.HeadOffset,
                advanced,
                out string? writeError),
            writeError ?? "Could not advance the synthetic queue head.");
    }

    private static void AcknowledgeTownReceive(DuckStationMemory memory, uint request)
    {
        // Faithful to the dispatcher's delivered path, in its order: commit
        // the sequence as the durable cursor ONLY for ordinary
        // (sign-bit-clear) requests, clear the stable/presentation
        // halfword (which erases the notification flag), set the status,
        // and publish the ack last. The old helper skipped the halfword
        // clear and the cursor commit, which is exactly how the
        // flags-after-delivery wedge and the gift cursor clobber escaped
        // this suite.
        Span<byte> word = stackalloc byte[sizeof(uint)];
        if (request < AzureDreamsGiftService.GiftReceiveSequenceBase)
        {
            BinaryPrimitives.WriteUInt32LittleEndian(word, request);
            Require(
                memory.TryWrite(
                    AzureDreamsReceiveState.PersistentStateAddress +
                        AzureDreamsReceiveState.ReceivedItemCountOffset,
                    word,
                    out string? cursorError),
                cursorError ?? "Could not commit the synthetic durable receive cursor.");
        }
        Span<byte> half = stackalloc byte[2];
        half.Clear();
        Require(
            memory.TryWrite(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.StableFramesOffset,
                half,
                out string? halfError),
            halfError ?? "Could not clear the synthetic stable/presentation halfword.");
        BinaryPrimitives.WriteUInt32LittleEndian(
            word, AzureDreamsMailbox.ReceiveStatusDelivered);
        Require(
            memory.TryWrite(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.ReceiveStatusOffset,
                word,
                out string? statusError),
            statusError ?? "Could not mark the synthetic gift receive delivered.");
        BinaryPrimitives.WriteUInt32LittleEndian(word, request);
        Require(
            memory.TryWrite(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.ReceiveAckSequenceOffset,
                word,
                out string? ackError),
            ackError ?? "Could not acknowledge the synthetic gift receive.");
        // Clear the notification-active gate so the next gift can stage.
        word.Clear();
        Require(
            memory.TryWrite(
                AzureDreamsTownMailbox.NotificationStateAddress,
                word,
                out string? notifyError),
            notifyError ?? "Could not clear the synthetic gift notification.");
    }

    private static void TestTownCheckpoints(
        DuckStationMemory memory,
        AzureDreamsSeedIdentity identity)
    {
        string root = Path.Combine(
            Path.GetTempPath(),
            "ADAP-checkpoint-self-test-" + Guid.NewGuid().ToString("N"));
        string directDirectory = Path.Combine(root, "direct");
        string coordinatorDirectory = Path.Combine(root, "coordinator");
        try
        {
            Span<byte> money = stackalloc byte[4];
            BinaryPrimitives.WriteUInt32LittleEndian(money, 0x1234_5678);
            Require(
                memory.TryWrite(0x8001_2d5c, money, out string? memoryError),
                memoryError ?? "Could not stage checkpoint money.");

            Require(
                AzureDreamsTownCheckpoint.TryCapture(
                    memory,
                    identity,
                    AzureDreamsCheckpointReason.InitialTown,
                    out AzureDreamsCheckpointMetadata directMetadata,
                    out string checkpointMessage,
                    directDirectory),
                checkpointMessage);
            Require(
                File.Exists(directMetadata.Path),
                "The atomic town checkpoint file was not created.");
            Require(
                AzureDreamsTownCheckpoint.TryRead(
                    directMetadata.Path,
                    identity,
                    out byte[] directPayload,
                    out AzureDreamsCheckpointMetadata readMetadata,
                    out checkpointMessage),
                checkpointMessage);
            int moneyOffset = checked((int)(0x8001_2d5c - AzureDreamsTownCheckpoint.SaveBlockAddress));
            Require(
                directPayload.Length == AzureDreamsTownCheckpoint.SaveBlockSize &&
                BinaryPrimitives.ReadUInt32LittleEndian(directPayload.AsSpan(moneyOffset)) ==
                    0x1234_5678,
                "The complete save-backed RAM block did not round-trip through a checkpoint.");
            Require(
                readMetadata.Reason == AzureDreamsCheckpointReason.InitialTown &&
                readMetadata.ReceiveCursor == 12 &&
                readMetadata.SeedSignature.SequenceEqual(identity.Signature),
                "Town checkpoint metadata did not preserve its commit identity and cursor.");

            BinaryPrimitives.WriteUInt32LittleEndian(money, 0xdead_beef);
            Require(
                memory.TryWrite(0x8001_2d5c, money, out memoryError),
                memoryError ?? "Could not mutate RAM before direct checkpoint restoration.");
            Require(
                AzureDreamsTownCheckpoint.TryRestore(
                    memory,
                    identity,
                    null,
                    out AzureDreamsCheckpointMetadata restoredDirectMetadata,
                    out _,
                    out checkpointMessage,
                    directDirectory),
                checkpointMessage);
            Require(
                memory.TryRead(0x8001_2d5c, money, out memoryError),
                memoryError ?? "Could not verify directly restored checkpoint money.");
            Require(
                BinaryPrimitives.ReadUInt32LittleEndian(money) == 0x1234_5678 &&
                restoredDirectMetadata.CreatedUnixMilliseconds ==
                    directMetadata.CreatedUnixMilliseconds,
                "The validated town checkpoint did not restore the save-backed RAM block.");

            // Server-confirmed checks land IN the restored block, before the
            // game can rebuild a floor from it. Restore the checkpoint with
            // three known checks the capture did not have (two tower slots -
            // one of them a carrier's - and one shop row): the mask reads
            // merged the moment the write lands, the count comes back, and the
            // checkpoint FILE itself is untouched.
            {
                int journalOffset = checked((int)(
                    AzureDreamsReceiveState.PersistentStateAddress +
                    AzureDreamsReceiveState.PersistentLocationMaskOffset -
                    AzureDreamsTownCheckpoint.SaveBlockAddress));
                int shopOffset = checked((int)(
                    AzureDreamsReceiveState.PersistentStateAddress +
                    AzureDreamsReceiveState.PersistentShopMaskOffset -
                    AzureDreamsTownCheckpoint.SaveBlockAddress));
                byte[] capturedJournal = directPayload
                    .AsSpan(journalOffset, AzureDreamsReceiveState.LocationMaskSize).ToArray();
                uint capturedShop = BinaryPrimitives.ReadUInt32LittleEndian(directPayload.AsSpan(shopOffset));
                Require(
                    (capturedJournal[6] & 0x04) == 0 && (capturedJournal[19] & 0x01) == 0 &&
                    (capturedShop & (1u << 4)) == 0,
                    "The checkpoint fixture already held the bits this test merges.");
                long[] knownChecks =
                [
                    AzureDreamsReceiveState.LocationIdBase + 6 * AzureDreamsReceiveState.SlotsPerFloor + 2,
                    AzureDreamsReceiveState.LocationIdBase + 19 * AzureDreamsReceiveState.SlotsPerFloor,
                    AzureDreamsReceiveState.ShopLocationIdBase + 4,
                    AzureDreamsReceiveState.LocationIdBase + 6 * AzureDreamsReceiveState.SlotsPerFloor + 2, // repeat
                    0x1234_5678, // somebody else's location
                ];
                Require(
                    AzureDreamsTownCheckpoint.TryRestore(
                        memory,
                        identity,
                        knownChecks,
                        out _,
                        out int mergedAtRestore,
                        out checkpointMessage,
                        directDirectory),
                    checkpointMessage);
                Require(mergedAtRestore == 3, $"Merged {mergedAtRestore} checks at restore; expected 3.");
                byte[] journalAfter = new byte[AzureDreamsReceiveState.LocationMaskSize];
                Span<byte> shopAfter = stackalloc byte[AzureDreamsReceiveState.ShopLocationMaskSize];
                Require(
                    memory.TryRead(
                        AzureDreamsReceiveState.PersistentStateAddress +
                            AzureDreamsReceiveState.PersistentLocationMaskOffset,
                        journalAfter,
                        out memoryError) &&
                    memory.TryRead(
                        AzureDreamsReceiveState.PersistentStateAddress +
                            AzureDreamsReceiveState.PersistentShopMaskOffset,
                        shopAfter,
                        out memoryError),
                    memoryError ?? "Could not read the journal after the merged restore.");
                byte[] expectedJournal = (byte[])capturedJournal.Clone();
                expectedJournal[6] |= 0x04;
                expectedJournal[19] |= 0x01;
                Require(
                    journalAfter.AsSpan().SequenceEqual(expectedJournal) &&
                    BinaryPrimitives.ReadUInt32LittleEndian(shopAfter) == (capturedShop | (1u << 4)),
                    "The restore did not merge the server checks into the restored block.");
                Require(
                    AzureDreamsTownCheckpoint.TryRestore(
                        memory, identity, null, out _, out int mergedNone, out checkpointMessage, directDirectory) &&
                    mergedNone == 0 &&
                    memory.TryRead(
                        AzureDreamsReceiveState.PersistentStateAddress +
                            AzureDreamsReceiveState.PersistentLocationMaskOffset,
                        journalAfter,
                        out memoryError) &&
                    journalAfter.AsSpan().SequenceEqual(capturedJournal),
                    checkpointMessage.Length > 0 ? checkpointMessage : "A restore without a server view did not start from the captured journal.");
            }

            // A TOWER checkpoint restores AND arms the native resume. Both
            // halves matter: restoring a tower block without the arm walks the
            // player into town, and arming without a tower block is what
            // crashed on 2026-08-11 - a town block has a zeroed stats mirror
            // at 0x80012194 and an inventory pointer table aimed at
            // descriptors that are not there.
            string towerDirectory = Path.Combine(
                Path.GetTempPath(), "adap-selftest-tower-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(towerDirectory);
            try
            {
                Span<byte> clear = stackalloc byte[4];
                Require(
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.ResumeTriggerAddress, clear, out memoryError),
                    memoryError ?? "Could not clear the resume trigger.");
                Require(
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.SavedInTowerFlagAddress,
                        clear[..2],
                        out memoryError),
                    memoryError ?? "Could not clear the saved-in-tower flag.");
                Require(
                    AzureDreamsTownCheckpoint.TryCapture(
                        memory,
                        identity,
                        AzureDreamsCheckpointReason.TowerFloorEntry,
                        out AzureDreamsCheckpointMetadata towerMetadata,
                        out checkpointMessage,
                        towerDirectory),
                    checkpointMessage);
                Require(
                    towerMetadata.Reason == AzureDreamsCheckpointReason.TowerFloorEntry,
                    "A tower-floor checkpoint did not record its reason.");
                Require(
                    AzureDreamsTownCheckpoint.TryRestore(
                        memory,
                        identity,
                        null,
                        out _,
                        out _,
                        out checkpointMessage,
                        towerDirectory),
                    checkpointMessage);

                Span<byte> armed = stackalloc byte[4];
                Require(
                    memory.TryRead(
                        AzureDreamsTownCheckpoint.ResumeTriggerAddress, armed, out memoryError),
                    memoryError ?? "Could not read the resume trigger.");
                Require(
                    BinaryPrimitives.ReadUInt32LittleEndian(armed) ==
                        AzureDreamsTownCheckpoint.ResumeTriggerValue,
                    "Restoring a tower checkpoint did not arm the resume trigger; the "
                    + "player would walk into town with a tower save.");
                Span<byte> towerFlag = stackalloc byte[2];
                Require(
                    memory.TryRead(
                        AzureDreamsTownCheckpoint.SavedInTowerFlagAddress,
                        towerFlag,
                        out memoryError),
                    memoryError ?? "Could not read the saved-in-tower flag.");
                Require(
                    BinaryPrimitives.ReadUInt16LittleEndian(towerFlag) != 0,
                    "Restoring a tower checkpoint left the saved-in-tower flag clear; the "
                    + "load path would choose game mode 6 (town) over 5 (tower).");

                // And a TOWN checkpoint must not arm it.
                Require(
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.ResumeTriggerAddress, clear, out memoryError),
                    memoryError ?? "Could not clear the resume trigger.");
                Require(
                    AzureDreamsTownCheckpoint.TryRestore(
                        memory, identity, null, out _, out _, out checkpointMessage, directDirectory),
                    checkpointMessage);
                Require(
                    memory.TryRead(
                        AzureDreamsTownCheckpoint.ResumeTriggerAddress, armed, out memoryError),
                    memoryError ?? "Could not re-read the resume trigger.");
                Require(
                    BinaryPrimitives.ReadUInt32LittleEndian(armed) == 0,
                    "A TOWN checkpoint armed the tower resume; that walks a town save into "
                    + "a tower entry, which crashes.");
            }
            finally
            {
                try { Directory.Delete(towerDirectory, recursive: true); } catch { }
            }

            // A tower-floor capture waits for the shortcut's level grant, and
            // stores Koh as he LIVES rather than as the game last mirrored him.
            //
            // The bug this pins: Uncle's warp to floor 10/20/30 levels Koh from
            // a wrapper on the floor's monster-levelling loop, near the END of
            // the build, while the floor number, the CD queue and the mode-load
            // flag all read ready near the START of it. A capture in between
            // stored the level the player left town with, and the resume handed
            // it back.
            string shortcutDirectory = Path.Combine(
                Path.GetTempPath(), "adap-selftest-shortcut-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(shortcutDirectory);
            try
            {
                byte[] liveStats = new byte[AzureDreamsTownCheckpoint.UnitStatsRecordSize];
                liveStats[AzureDreamsTownCheckpoint.UnitStatsLevelOffset] = 12;
                liveStats[AzureDreamsTownCheckpoint.UnitStatsMaximumHpOffset] = 60;
                liveStats[0] = 24;                                   // ATK
                byte[] staleMirror = new byte[AzureDreamsTownCheckpoint.UnitStatsRecordSize];
                staleMirror[AzureDreamsTownCheckpoint.UnitStatsLevelOffset] = 1;
                staleMirror[AzureDreamsTownCheckpoint.UnitStatsMaximumHpOffset] = 16;
                Span<byte> floorBytes = stackalloc byte[2];
                BinaryPrimitives.WriteUInt16LittleEndian(floorBytes, 10);
                Require(
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.LoadedModeAddress,
                        [AzureDreamsTownCheckpoint.TowerMode],
                        out memoryError) &&
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.CurrentFloorAddress, floorBytes, out memoryError) &&
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.ModeLoadPendingAddress, [0], out memoryError) &&
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.CdQueueHeadAddress, [0x21, 0x21], out memoryError) &&
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.LiveUnitStatsAddress, liveStats, out memoryError) &&
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.SavedUnitStatsAddress, staleMirror, out memoryError) &&
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.ShortcutPendingLevelAddress, [10], out memoryError),
                    memoryError ?? "Could not stage a shortcut arrival on floor 10.");

                var shortcutCoordinator =
                    new AzureDreamsTownCheckpointCoordinator(shortcutDirectory);
                Require(
                    shortcutCoordinator.TryObserveAndCommitBoundary(
                        memory,
                        identity,
                        out _,
                        out AzureDreamsCheckpointMetadata? tooEarly,
                        out checkpointMessage) &&
                    tooEarly is null &&
                    !File.Exists(AzureDreamsTownCheckpoint.GetSnapshotPath(identity, shortcutDirectory)),
                    "A tower-floor checkpoint was taken while the shortcut's level grant was "
                    + "still pending; it would store the level the player left town with.");

                Require(
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.ShortcutPendingLevelAddress, [0], out memoryError),
                    memoryError ?? "Could not clear the shortcut pending level.");
                Require(
                    shortcutCoordinator.TryObserveAndCommitBoundary(
                        memory,
                        identity,
                        out _,
                        out AzureDreamsCheckpointMetadata? granted,
                        out checkpointMessage) &&
                    granted?.Reason == AzureDreamsCheckpointReason.TowerFloorEntry,
                    checkpointMessage.Length > 0
                        ? checkpointMessage
                        : "No tower-floor checkpoint was taken once the level grant had run.");
                Require(
                    AzureDreamsTownCheckpoint.TryRead(
                        AzureDreamsTownCheckpoint.GetSnapshotPath(identity, shortcutDirectory),
                        identity,
                        out byte[] shortcutPayload,
                        out _,
                        out checkpointMessage),
                    checkpointMessage);
                int mirrorOffset = checked((int)(
                    AzureDreamsTownCheckpoint.SavedUnitStatsAddress -
                    AzureDreamsTownCheckpoint.SaveBlockAddress));
                Require(
                    shortcutPayload.AsSpan(
                        mirrorOffset, AzureDreamsTownCheckpoint.UnitStatsRecordSize)
                        .SequenceEqual(liveStats),
                    "A tower-floor checkpoint stored the game's stale stats mirror instead of "
                    + "Koh as he lives; a resume would come back at the wrong level.");

                // Only one capture per floor, and a live record that is not a
                // plausible Koh leaves the game's own mirror alone.
                Require(
                    shortcutCoordinator.TryObserveAndCommitBoundary(
                        memory, identity, out _, out AzureDreamsCheckpointMetadata? again, out _) &&
                    again is null,
                    "The same tower floor was captured twice.");
                Require(
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.LiveUnitStatsAddress,
                        new byte[AzureDreamsTownCheckpoint.UnitStatsRecordSize],
                        out memoryError),
                    memoryError ?? "Could not blank the live stats.");
                Require(
                    AzureDreamsTownCheckpoint.TryCapture(
                        memory,
                        identity,
                        AzureDreamsCheckpointReason.TowerFloorEntry,
                        out _,
                        out checkpointMessage,
                        shortcutDirectory) &&
                    AzureDreamsTownCheckpoint.TryRead(
                        AzureDreamsTownCheckpoint.GetSnapshotPath(identity, shortcutDirectory),
                        identity,
                        out byte[] blankedPayload,
                        out _,
                        out checkpointMessage) &&
                    blankedPayload.AsSpan(
                        mirrorOffset, AzureDreamsTownCheckpoint.UnitStatsRecordSize)
                        .SequenceEqual(staleMirror),
                    "A live record that is not a plausible Koh overwrote the saved mirror.");

                // --- what escapes the world must not roll back --------------
                //
                // A send puts an item in another player's world and spends a
                // token. Restoring the block as captured hands both back, and
                // the recipient keeps their copy - so the SAVED checkpoint is
                // edited instead of being retaken, which would cost the player
                // the floor they are part-way through.
                byte[] sent = [0x06, 0x04, 0x09, 0x00];              // a 9-charge Water Ball
                byte[] neverHeld = [0x2a, 0x0f, 0x03, 0x00];
                uint firstSlot = AzureDreamsTownCheckpoint.InventoryDescriptorAddress;
                Span<byte> order = stackalloc byte[12];
                BinaryPrimitives.WriteUInt32LittleEndian(order, firstSlot);
                BinaryPrimitives.WriteUInt32LittleEndian(order[4..], firstSlot + 4);
                BinaryPrimitives.WriteUInt32LittleEndian(order[8..], firstSlot + 8);
                Span<byte> tokens = stackalloc byte[8];
                BinaryPrimitives.WriteUInt32LittleEndian(tokens, 3);
                BinaryPrimitives.WriteUInt32LittleEndian(
                    tokens[4..], AzureDreamsReceiveState.SendTokenMagic);
                Require(
                    memory.TryWrite(firstSlot, [0x01, 0x04, 0x04, 0x00], out memoryError) &&
                    memory.TryWrite(firstSlot + 4, sent, out memoryError) &&
                    memory.TryWrite(firstSlot + 8, [0x03, 0x04, 0x00, 0x00], out memoryError) &&
                    memory.TryWrite(
                        AzureDreamsTownCheckpoint.InventoryOrderAddress, order, out memoryError) &&
                    memory.TryWrite(
                        AzureDreamsReceiveState.SendTokenCountAddress, tokens, out memoryError),
                    memoryError ?? "Could not stage a bag and a token count.");
                Require(
                    AzureDreamsTownCheckpoint.TryCapture(
                        memory,
                        identity,
                        AzureDreamsCheckpointReason.TowerFloorEntry,
                        out _,
                        out checkpointMessage,
                        shortcutDirectory),
                    checkpointMessage);

                // Sent with four charges left of the nine it had at floor entry:
                // identity is the id and the category, the rest is floor state.
                Require(
                    AzureDreamsTownCheckpoint.TryAmendForSentItem(
                        identity, [sent[0], sent[1], 0x04, 0x00],
                        out bool editedInventory, out checkpointMessage,
                        shortcutDirectory) &&
                    editedInventory,
                    checkpointMessage.Length > 0
                        ? checkpointMessage
                        : "Sending an item the checkpoint was carrying did not edit it out.");
                Require(
                    AzureDreamsTownCheckpoint.TryRead(
                        AzureDreamsTownCheckpoint.GetSnapshotPath(identity, shortcutDirectory),
                        identity,
                        out byte[] amended,
                        out AzureDreamsCheckpointMetadata amendedMetadata,
                        out checkpointMessage),
                    checkpointMessage);
                int descriptorBase = checked((int)(
                    firstSlot - AzureDreamsTownCheckpoint.SaveBlockAddress));
                int orderBase = checked((int)(
                    AzureDreamsTownCheckpoint.InventoryOrderAddress -
                    AzureDreamsTownCheckpoint.SaveBlockAddress));
                int tokenBase = checked((int)(
                    AzureDreamsReceiveState.SendTokenCountAddress -
                    AzureDreamsTownCheckpoint.SaveBlockAddress));
                Require(
                    amended.AsSpan(descriptorBase + 4, 4).SequenceEqual(new byte[4]) &&
                    amended.AsSpan(descriptorBase, 4).SequenceEqual((byte[])[0x01, 0x04, 0x04, 0x00]) &&
                    amended.AsSpan(descriptorBase + 8, 4).SequenceEqual((byte[])[0x03, 0x04, 0x00, 0x00]),
                    "The amendment removed the wrong descriptor.");
                Require(
                    BinaryPrimitives.ReadUInt32LittleEndian(amended.AsSpan(orderBase)) == firstSlot &&
                    BinaryPrimitives.ReadUInt32LittleEndian(amended.AsSpan(orderBase + 4)) == firstSlot + 8 &&
                    BinaryPrimitives.ReadUInt32LittleEndian(amended.AsSpan(orderBase + 8)) == 0,
                    "The amendment did not close the display order over the sent item.");
                Require(
                    BinaryPrimitives.ReadUInt32LittleEndian(amended.AsSpan(tokenBase)) == 2,
                    "The amendment did not spend the send token in the stored checkpoint.");
                Require(
                    amendedMetadata.Reason == AzureDreamsCheckpointReason.TowerFloorEntry,
                    "The amendment rewrote the checkpoint's reason.");

                // An item found on this floor and sent from it was never in the
                // checkpoint, so the token is the whole correction.
                Require(
                    AzureDreamsTownCheckpoint.TryAmendForSentItem(
                        identity, neverHeld, out bool editedNothing, out checkpointMessage,
                        shortcutDirectory) &&
                    !editedNothing &&
                    AzureDreamsTownCheckpoint.TryRead(
                        AzureDreamsTownCheckpoint.GetSnapshotPath(identity, shortcutDirectory),
                        identity,
                        out byte[] tokenOnly,
                        out _,
                        out checkpointMessage) &&
                    BinaryPrimitives.ReadUInt32LittleEndian(tokenOnly.AsSpan(tokenBase)) == 1 &&
                    BinaryPrimitives.ReadUInt32LittleEndian(tokenOnly.AsSpan(orderBase + 4)) == firstSlot + 8,
                    checkpointMessage.Length > 0
                        ? checkpointMessage
                        : "Sending an item the checkpoint never held still edited the bag.");

                // --- what enters the world is offered again -----------------
                //
                // Gift delivery is recorded on the Archipelago server, which a
                // memory restore cannot reach, so the watermark rides beside
                // the snapshot and is handed back on a restore.
                const string watermark = "{\"senders\":{\"2:abcd\":7},\"ack\":2147483652}";
                Require(
                    AzureDreamsTownCheckpoint.TryCapture(
                        memory,
                        identity,
                        AzureDreamsCheckpointReason.TowerFloorEntry,
                        out _,
                        out checkpointMessage,
                        shortcutDirectory,
                        watermark),
                    checkpointMessage);
                string? handedBack = "not called";
                Require(
                    AzureDreamsTownCheckpoint.TryRestore(
                        memory,
                        identity,
                        null,
                        out _,
                        out _,
                        out checkpointMessage,
                        shortcutDirectory,
                        value => handedBack = value) &&
                    handedBack == watermark,
                    checkpointMessage.Length > 0
                        ? checkpointMessage
                        : "Restoring a checkpoint did not hand back its gift watermark; gifts "
                          + "delivered after it would be lost rather than re-offered.");
                // A capture with no watermark must not leave the previous one
                // behind - it belongs to a block that no longer exists.
                Require(
                    AzureDreamsTownCheckpoint.TryCapture(
                        memory,
                        identity,
                        AzureDreamsCheckpointReason.TowerFloorEntry,
                        out _,
                        out checkpointMessage,
                        shortcutDirectory),
                    checkpointMessage);
                handedBack = "not called";
                Require(
                    AzureDreamsTownCheckpoint.TryRestore(
                        memory, identity, null, out _, out _, out checkpointMessage,
                        shortcutDirectory, value => handedBack = value) &&
                    handedBack is null,
                    "A stale gift watermark outlived the checkpoint it was captured with.");
            }
            finally
            {
                try { Directory.Delete(shortcutDirectory, recursive: true); } catch { }
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.LoadedModeAddress,
                    [AzureDreamsTownCheckpoint.TownMode],
                    out _);
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.CurrentFloorAddress, new byte[2], out _);
            }

            byte[] validCheckpoint = File.ReadAllBytes(directMetadata.Path);
            byte[] inconsistentCheckpoint = validCheckpoint.ToArray();
            BinaryPrimitives.WriteUInt32LittleEndian(
                inconsistentCheckpoint.AsSpan(24),
                readMetadata.ReceiveCursor + 1);
            File.WriteAllBytes(directMetadata.Path, inconsistentCheckpoint);
            Require(
                !AzureDreamsTownCheckpoint.TryRead(
                    directMetadata.Path,
                    identity,
                    out _,
                    out _,
                    out checkpointMessage) &&
                checkpointMessage.Contains("does not match", StringComparison.OrdinalIgnoreCase),
                "Checkpoint header metadata inconsistent with its payload was accepted.");

            byte[] corruptCheckpoint = validCheckpoint.ToArray();
            corruptCheckpoint[^1] ^= 0xff;
            File.WriteAllBytes(directMetadata.Path, corruptCheckpoint);
            Require(
                !AzureDreamsTownCheckpoint.TryRead(
                    directMetadata.Path,
                    identity,
                    out _,
                    out _,
                    out checkpointMessage) &&
                checkpointMessage.Contains("checksum", StringComparison.OrdinalIgnoreCase),
                "A corrupted town checkpoint passed payload integrity validation.");

            byte[] townHeader = new byte[8];
            BinaryPrimitives.WriteUInt32LittleEndian(townHeader, AzureDreamsTownMailbox.Magic);
            BinaryPrimitives.WriteUInt16LittleEndian(
                townHeader.AsSpan(4), AzureDreamsTownMailbox.ProtocolVersion);
            BinaryPrimitives.WriteUInt16LittleEndian(
                townHeader.AsSpan(6), AzureDreamsTownMailbox.Size);
            Require(
                memory.TryWrite(AzureDreamsTownMailbox.Address, townHeader, out memoryError) &&
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.LoadedModeAddress,
                    [AzureDreamsTownCheckpoint.TownMode],
                    out memoryError) &&
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.ModeLoadPendingAddress,
                    [0],
                    out memoryError) &&
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.TownModalStateAddress,
                    new byte[4],
                    out memoryError) &&
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.TownModalRootAddress,
                    new byte[4],
                    out memoryError) &&
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.CdQueueHeadAddress,
                    [0x17, 0x17],
                    out memoryError),
                memoryError ?? "Could not stage stable town checkpoint guards.");

            var coordinator = new AzureDreamsTownCheckpointCoordinator(coordinatorDirectory);
            Require(
                coordinator.TryObserveAndCommitBoundary(
                    memory,
                    identity,
                    out AzureDreamsTownObservation initialObservation,
                    out AzureDreamsCheckpointMetadata? initialCheckpoint,
                    out checkpointMessage),
                checkpointMessage);
            Require(
                initialObservation.IsStableTown &&
                initialCheckpoint?.Reason == AzureDreamsCheckpointReason.InitialTown,
                "The first stable town state did not create an initial checkpoint.");
            Require(
                coordinator.TryObserveAndCommitBoundary(
                    memory,
                    identity,
                    out _,
                    out AzureDreamsCheckpointMetadata? duplicateCheckpoint,
                    out checkpointMessage) &&
                duplicateCheckpoint is null,
                "An unchanged stable town state created a duplicate boundary checkpoint.");

            Require(
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.LoadedModeAddress,
                    [AzureDreamsTownCheckpoint.TowerMode],
                    out memoryError),
                memoryError ?? "Could not stage tower mode.");
            Require(
                coordinator.TryObserveAndCommitBoundary(
                    memory,
                    identity,
                    out AzureDreamsTownObservation towerObservation,
                    out _,
                    out checkpointMessage) &&
                towerObservation.IsTower,
                checkpointMessage);
            BinaryPrimitives.WriteUInt32LittleEndian(money, 100);
            Require(
                memory.TryWrite(0x8001_2d5c, money, out memoryError) &&
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.LoadedModeAddress,
                    [AzureDreamsTownCheckpoint.TownMode],
                    out memoryError),
                memoryError ?? "Could not stage a settled tower return.");
            Require(
                coordinator.TryObserveAndCommitBoundary(
                    memory,
                    identity,
                    out AzureDreamsTownObservation returnObservation,
                    out AzureDreamsCheckpointMetadata? returnCheckpoint,
                    out checkpointMessage),
                checkpointMessage);
            Require(
                returnObservation.IsStableTown &&
                returnCheckpoint?.Reason == AzureDreamsCheckpointReason.TowerReturn,
                "A tower-to-stable-town transition did not create a return checkpoint.");

            // AcceptIntroCheckpoint exists for the intro's own capture and
            // restore, and deliberately drops the boundary coordinator's
            // pending state. CheckpointLifecycleComplete stays true for the
            // rest of the session, so calling this once per poll erases the
            // tower observation and both pending requests before any of them
            // can commit, and forces _snapshotExists true so the very first
            // town checkpoint is never taken. Pin that hazard here.
            Require(
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.LoadedModeAddress,
                    [AzureDreamsTownCheckpoint.TowerMode],
                    out memoryError) &&
                coordinator.TryObserveAndCommitBoundary(
                    memory,
                    identity,
                    out _,
                    out _,
                    out checkpointMessage),
                memoryError ?? checkpointMessage);
            coordinator.AcceptIntroCheckpoint(identity);
            AzureDreamsCheckpointMetadata? erasedReturn = null;
            Require(
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.LoadedModeAddress,
                    [AzureDreamsTownCheckpoint.TownMode],
                    out memoryError) &&
                coordinator.TryObserveAndCommitBoundary(
                    memory,
                    identity,
                    out _,
                    out erasedReturn,
                    out checkpointMessage),
                memoryError ?? checkpointMessage);
            Require(
                erasedReturn is null,
                "AcceptIntroCheckpoint no longer clears a pending tower return; " +
                "revisit whether the main loop may call it once per poll.");

            coordinator.RequestShopPurchaseCheckpoint();
            coordinator.RequestTownReceiveCheckpoint();
            Require(
                coordinator.TryCommitPending(
                    memory,
                    identity,
                    out AzureDreamsCheckpointMetadata? receiveCheckpoint,
                    out checkpointMessage),
                checkpointMessage);
            Require(
                receiveCheckpoint?.Reason ==
                    AzureDreamsCheckpointReason.TownReceiveAcknowledged,
                "A town receive acknowledgement did not supersede its overlapping shop checkpoint.");
            coordinator.RequestShopPurchaseCheckpoint();
            Require(
                coordinator.TryCommitPending(
                    memory,
                    identity,
                    out AzureDreamsCheckpointMetadata? shopCheckpoint,
                    out checkpointMessage),
                checkpointMessage);
            Require(
                shopCheckpoint?.Reason == AzureDreamsCheckpointReason.ShopPurchase,
                "A standalone shop purchase did not create a town checkpoint.");
            Require(
                coordinator.TryCommitPending(
                    memory,
                    identity,
                    out AzureDreamsCheckpointMetadata? noCheckpoint,
                    out checkpointMessage) &&
                noCheckpoint is null,
                "Cleared checkpoint requests committed more than once.");

            // A pending capture waits for a settled frame. A checkpoint taken
            // mid-modal can snapshot half-written state - Nada's delivery loop
            // partway through the inventory - and restoring it would resurrect
            // that state; added 2026-08-05 alongside the restore gate.
            byte[] busyModalRoot = [1, 0, 0, 0];
            Require(
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.TownModalRootAddress,
                    busyModalRoot,
                    out memoryError),
                memoryError ?? "Could not stage an open town modal.");
            coordinator.RequestShopPurchaseCheckpoint();
            Require(
                coordinator.TryCommitPending(
                    memory,
                    identity,
                    out AzureDreamsCheckpointMetadata? unstableCheckpoint,
                    out checkpointMessage) &&
                unstableCheckpoint is null,
                "A pending capture committed on an unsettled town frame.");
            Require(
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.TownModalRootAddress,
                    new byte[4],
                    out memoryError),
                memoryError ?? "Could not close the staged town modal.");
            Require(
                coordinator.TryCommitPending(
                    memory,
                    identity,
                    out AzureDreamsCheckpointMetadata? settledCheckpoint,
                    out checkpointMessage) &&
                settledCheckpoint?.Reason == AzureDreamsCheckpointReason.ShopPurchase,
                "A capture deferred by an unsettled frame did not commit once the " +
                "frame settled.");

            BinaryPrimitives.WriteUInt32LittleEndian(money, 999);
            Require(
                memory.TryWrite(0x8001_2d5c, money, out memoryError),
                memoryError ?? "Could not mutate RAM before startup restoration.");
            Require(
                AzureDreamsTownCheckpoint.TryObserve(
                    memory,
                    out AzureDreamsTownObservation restoreObservation,
                    out checkpointMessage),
                checkpointMessage);
            var restoringCoordinator =
                new AzureDreamsTownCheckpointCoordinator(coordinatorDirectory);
            Require(
                restoringCoordinator.TryRestoreAtStartup(
                    memory,
                    identity,
                    new AzureDreamsTownObservation(6, false),
                    saveIsPristine: true,
                    null,
                    out bool restorePending,
                    out AzureDreamsCheckpointMetadata? restoredCheckpoint,
                    out _,
                    out _,
                    out checkpointMessage) &&
                restorePending &&
                restoredCheckpoint is null &&
                memory.TryRead(0x8001_2d5c, money, out memoryError) &&
                BinaryPrimitives.ReadUInt32LittleEndian(money) == 999,
                memoryError ??
                "Startup restoration did not wait for a stable town frame.");
            Require(
                restoringCoordinator.TryRestoreAtStartup(
                    memory,
                    identity,
                    restoreObservation,
                    saveIsPristine: true,
                    null,
                    out restorePending,
                    out restoredCheckpoint,
                    out _,
                    out _,
                    out checkpointMessage),
                checkpointMessage);
            Require(
                !restorePending &&
                restoredCheckpoint?.Reason == AzureDreamsCheckpointReason.ShopPurchase &&
                memory.TryRead(0x8001_2d5c, money, out memoryError) &&
                BinaryPrimitives.ReadUInt32LittleEndian(money) == 100,
                memoryError ??
                "The startup coordinator did not restore the latest town checkpoint.");
            Require(
                restoringCoordinator.TryRestoreAtStartup(
                    memory,
                    identity,
                    restoreObservation,
                    saveIsPristine: true,
                    null,
                    out restorePending,
                    out restoredCheckpoint,
                    out _,
                    out _,
                    out checkpointMessage) &&
                !restorePending &&
                restoredCheckpoint is null,
                "A startup checkpoint restored more than once without a reset.");

            // Losing sight of a LIVE game - a transient identity read
            // failure, a client restart against a running session - re-arms
            // the pending restore, but the save block is populated, so the
            // restore must be dropped rather than roll live progress back.
            // This was the mid-session restore hazard fixed 2026-08-05: only
            // the main-menu continue path (which reaches town with a pristine
            // block, or hands off through the intro handshake) may restore.
            restoringCoordinator.ResetGameObservation();
            BinaryPrimitives.WriteUInt32LittleEndian(money, 777);
            bool staleRestoreDropped = false;
            Require(
                memory.TryWrite(0x8001_2d5c, money, out memoryError) &&
                restoringCoordinator.TryRestoreAtStartup(
                    memory,
                    identity,
                    restoreObservation,
                    saveIsPristine: false,
                    null,
                    out restorePending,
                    out restoredCheckpoint,
                    out _,
                    out staleRestoreDropped,
                    out checkpointMessage),
                memoryError ?? checkpointMessage);
            Require(
                !restorePending &&
                restoredCheckpoint is null &&
                staleRestoreDropped &&
                memory.TryRead(0x8001_2d5c, money, out memoryError) &&
                BinaryPrimitives.ReadUInt32LittleEndian(money) == 777,
                memoryError ??
                "A reconnection to a live (non-pristine) game restored a checkpoint " +
                "over its progress.");
            // The drop is one-shot: the armed restore is consumed, so the
            // next poll reports nothing rather than dropping again forever.
            Require(
                restoringCoordinator.TryRestoreAtStartup(
                    memory,
                    identity,
                    restoreObservation,
                    saveIsPristine: false,
                    null,
                    out restorePending,
                    out restoredCheckpoint,
                    out _,
                    out staleRestoreDropped,
                    out checkpointMessage) &&
                !restorePending &&
                restoredCheckpoint is null &&
                !staleRestoreDropped,
                "A dropped stale restore was reported more than once.");

            // A genuine reboot re-initializes the save block, so identity
            // re-establishment over a PRISTINE block still restores - that is
            // the main-menu continue path's fallback when the intro handshake
            // has not already consumed the checkpoint.
            restoringCoordinator.ResetGameObservation();
            Require(
                restoringCoordinator.TryRestoreAtStartup(
                    memory,
                    identity,
                    restoreObservation,
                    saveIsPristine: true,
                    null,
                    out restorePending,
                    out restoredCheckpoint,
                    out _,
                    out _,
                    out checkpointMessage),
                checkpointMessage);
            Require(
                !restorePending &&
                restoredCheckpoint is not null &&
                memory.TryRead(0x8001_2d5c, money, out memoryError) &&
                BinaryPrimitives.ReadUInt32LittleEndian(money) == 100,
                memoryError ??
                "A pristine-block reboot did not re-arm one-time restoration.");

            BinaryPrimitives.WriteUInt32LittleEndian(money, 888);
            Require(
                memory.TryWrite(0x8001_2d5c, money, out memoryError) &&
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.LoadedModeAddress,
                    [AzureDreamsTownCheckpoint.TowerMode],
                    out memoryError),
                memoryError ?? "Could not stage an attachment during tower mode.");
            Require(
                AzureDreamsTownCheckpoint.TryObserve(
                    memory,
                    out AzureDreamsTownObservation attachInTowerObservation,
                    out checkpointMessage),
                checkpointMessage);
            var towerAttachCoordinator =
                new AzureDreamsTownCheckpointCoordinator(coordinatorDirectory);
            Require(
                towerAttachCoordinator.TryRestoreAtStartup(
                    memory,
                    identity,
                    attachInTowerObservation,
                    saveIsPristine: false,
                    null,
                    out restorePending,
                    out restoredCheckpoint,
                    out _,
                    out _,
                    out checkpointMessage) &&
                !restorePending &&
                restoredCheckpoint is null,
                "Attaching during a tower trip attempted to restore a town checkpoint.");
            Require(
                memory.TryWrite(
                    AzureDreamsTownCheckpoint.LoadedModeAddress,
                    [AzureDreamsTownCheckpoint.TownMode],
                    out memoryError),
                memoryError ?? "Could not return the tower-attachment test to town.");
            Require(
                AzureDreamsTownCheckpoint.TryObserve(
                    memory,
                    out AzureDreamsTownObservation afterTowerAttachObservation,
                    out checkpointMessage),
                checkpointMessage);
            Require(
                towerAttachCoordinator.TryRestoreAtStartup(
                    memory,
                    identity,
                    afterTowerAttachObservation,
                    saveIsPristine: true,
                    null,
                    out restorePending,
                    out restoredCheckpoint,
                    out _,
                    out _,
                    out checkpointMessage) &&
                !restorePending &&
                restoredCheckpoint is null &&
                memory.TryRead(0x8001_2d5c, money, out memoryError) &&
                BinaryPrimitives.ReadUInt32LittleEndian(money) == 888,
                memoryError ??
                "A checkpoint suppressed at tower attachment restored later in town.");

            TestIntroRestoreHandshake(memory, identity, root);
        }
        finally
        {
            if (Directory.Exists(root))
                Directory.Delete(root, recursive: true);
        }
    }

    private static void TestIntroRestoreHandshake(
        DuckStationMemory memory,
        AzureDreamsSeedIdentity identity,
        string root)
    {
        string returningDirectory = Path.Combine(root, "intro-returning");
        string initialDirectory = Path.Combine(root, "intro-initial");

        // The greeting must end by calling the resource's scene-transition
        // subroutine. Without it the dialogue closes and the returning player
        // is stranded on the angel screen, which is exactly how V45 and V46
        // failed. The countdown must also loop back to its own yield.
        ReadOnlySpan<byte> returningScript =
            AzureDreamsIntroRestore.ReturningAngelScript;
        bool introWindowClosed = false;
        byte[] expectedTransition = new byte[7];
        expectedTransition[0] = 0x08;
        expectedTransition[1] = 0x15;
        BinaryPrimitives.WriteUInt32LittleEndian(
            expectedTransition.AsSpan(2),
            AzureDreamsIntroRestore.ScenePatchCallAddress);
        expectedTransition[6] = 0x01;
        Require(
            returningScript.Length > expectedTransition.Length &&
            returningScript[^expectedTransition.Length..]
                .SequenceEqual(expectedTransition),
            "The returning angel script does not end by starting the scene transition.");
        Require(
            returningScript[AzureDreamsIntroRestore.TransitionDelayLoopOffset] == 0x30 &&
            BinaryPrimitives.ReadUInt32LittleEndian(
                returningScript[
                    (AzureDreamsIntroRestore.TransitionDelayLoopOffset + 0xc)..]) ==
                AzureDreamsIntroRestore.TransitionDelayLoopAddress,
            "The returning angel script's transition countdown does not loop back to its own yield.");
        Require(
            AzureDreamsIntroRestore.ReturningAngelScriptAddress +
                returningScript.Length < 0x8001_7afb,
            "The returning angel script overruns the original closing sequence it replays.");
        Require(
            returningScript.IndexOf([(byte)0xfe, (byte)0x00, (byte)0x81, (byte)0x49, (byte)0x11]) >= 0,
            "The returning greeting does not end the player name with the angel's exclamation.");

        byte[] coreHeader = new byte[0x28];
        "ADAPSHOP"u8.CopyTo(coreHeader);
        BinaryPrimitives.WriteUInt16LittleEndian(
            coreHeader.AsSpan(8),
            AzureDreamsIntroRestore.TownCoreVersion);
        identity.Signature.CopyTo(coreHeader, 0x20);

        byte[] mailbox = new byte[AzureDreamsTownMailbox.Size];
        BinaryPrimitives.WriteUInt32LittleEndian(
            mailbox,
            AzureDreamsTownMailbox.Magic);
        BinaryPrimitives.WriteUInt16LittleEndian(
            mailbox.AsSpan(AzureDreamsTownMailbox.ProtocolVersionOffset),
            AzureDreamsTownMailbox.ProtocolVersion);
        BinaryPrimitives.WriteUInt16LittleEndian(
            mailbox.AsSpan(AzureDreamsTownMailbox.StructureSizeOffset),
            AzureDreamsTownMailbox.Size);
        mailbox[AzureDreamsTownMailbox.IntroRestoreProtocolOffset] =
            AzureDreamsIntroRestore.ProtocolVersion;
        mailbox[AzureDreamsTownMailbox.IntroRestoreStateOffset] =
            AzureDreamsIntroRestore.StateProbeRequest;
        byte[] originalAngel =
            new byte[AzureDreamsIntroRestore.ReturningAngelScript.Length];
        Array.Fill(originalAngel, (byte)0x5a);

        Require(
            memory.TryWrite(
                AzureDreamsTownCheckpoint.LoadedModeAddress,
                [AzureDreamsTownCheckpoint.TownMode],
                out string? memoryError) &&
            memory.TryWrite(
                AzureDreamsIntroRestore.TownCoreAddress,
                coreHeader,
                out memoryError) &&
            memory.TryWrite(
                AzureDreamsTownMailbox.Address,
                mailbox,
                out memoryError) &&
            memory.TryWrite(
                AzureDreamsIntroRestore.ReturningAngelScriptAddress,
                originalAngel,
                out memoryError),
            memoryError ?? "Could not stage the intro restore protocol.");

        // A genuine boot reaches the angel with an untouched save block. The
        // handshake only waits for the angel resource in that state, so the
        // fixture has to represent it rather than the populated save left
        // behind by the preceding checkpoint tests.
        Require(
            AzureDreamsReceiveState.TryWriteReceivedItemCount(memory, 0, out string pristineMessage) &&
            AzureDreamsReceiveState.TrySetProgressiveKeycardLevel(memory, 0, out pristineMessage) &&
            memory.TryWrite(
                AzureDreamsReceiveState.PersistentStateAddress +
                    AzureDreamsReceiveState.PersistentLocationMaskOffset,
                new byte[AzureDreamsReceiveState.LocationMaskSize],
                out memoryError) &&
            memory.TryWrite(
                AzureDreamsReceiveState.PersistentStateAddress +
                    AzureDreamsReceiveState.PersistentShopMaskOffset,
                new byte[AzureDreamsReceiveState.ShopLocationMaskSize],
                out memoryError),
            memoryError ?? pristineMessage);

        Require(
            AzureDreamsIntroRestore.TrySynchronize(
                memory,
                identity,
                ref introWindowClosed,
                out AzureDreamsIntroRestoreResult firstRunWaiting,
                out string introMessage,
                returningDirectory),
            introMessage);
        Span<byte> state = stackalloc byte[1];
        Span<byte> ready = stackalloc byte[1];
        byte[] observedAngel =
            new byte[AzureDreamsIntroRestore.ReturningAngelScript.Length];
        Require(
            firstRunWaiting.Event == AzureDreamsIntroRestoreEvent.None &&
            firstRunWaiting.BlocksNormalSynchronization &&
            memory.TryRead(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.IntroRestoreStateOffset,
                state,
                out memoryError) &&
            state[0] == AzureDreamsIntroRestore.StateFirstRun &&
            memory.TryRead(
                AzureDreamsIntroRestore.ReturningAngelScriptAddress,
                observedAngel,
                out memoryError) &&
            observedAngel.SequenceEqual(originalAngel),
            memoryError ??
            "A missing checkpoint changed or prematurely released the original angel sequence.");

        ready[0] = AzureDreamsIntroRestore.FirstRunReadyMarkerValue;
        Require(
            memory.TryWrite(
                AzureDreamsIntroRestore.FirstRunReadyMarkerAddress,
                ready,
                out memoryError) &&
            AzureDreamsIntroRestore.TrySynchronize(
                memory,
                identity,
                ref introWindowClosed,
                out AzureDreamsIntroRestoreResult firstRunReleased,
                out introMessage,
                returningDirectory) &&
            firstRunReleased.Event ==
                AzureDreamsIntroRestoreEvent.FirstRunReleased &&
            !firstRunReleased.BlocksNormalSynchronization &&
            memory.TryRead(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.IntroRestoreStateOffset,
                state,
                out memoryError) &&
            state[0] == AzureDreamsIntroRestore.StateFirstRunReady &&
            memory.TryRead(
                AzureDreamsIntroRestore.FirstRunReadyMarkerAddress,
                ready,
                out memoryError) &&
            ready[0] == 0,
            memoryError ?? introMessage);

        byte[] savedName =
        [
            0x82, 0x71, 0x82, 0x85, 0x82, 0x94, 0x82, 0x95,
            0x82, 0x92, 0x82, 0x8e, 0x00, 0x00, 0x00, 0x00,
        ];
        Span<byte> money = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(money, 0x1020_3040);
        Require(
            memory.TryWrite(
                AzureDreamsIntroRestore.PlayerNameAddress,
                savedName,
                out memoryError) &&
            memory.TryWrite(0x8001_2d5c, money, out memoryError) &&
            AzureDreamsTownCheckpoint.TryCapture(
                memory,
                identity,
                AzureDreamsCheckpointReason.ShopPurchase,
                out _,
                out introMessage,
                returningDirectory),
            memoryError ?? introMessage);

        byte[] blankName = new byte[AzureDreamsIntroRestore.PlayerNameSize];
        byte[] loadedOriginalAngel =
            new byte[AzureDreamsIntroRestore.ReturningAngelScript.Length];
        AzureDreamsIntroRestore.ReturningAngelScript[..5].CopyTo(
            loadedOriginalAngel);
        BinaryPrimitives.WriteUInt32LittleEndian(money, 0x9999_9999);
        // A current game begins at state zero. The client must discover and
        // stage a compatible checkpoint before the angel asks for it.
        state[0] = AzureDreamsIntroRestore.StateFirstRun;
        // A fresh console start also means a fresh client session latch.
        introWindowClosed = false;
        AzureDreamsIntroRestoreResult proactiveRestore = default;
        Require(
            memory.TryWrite(
                AzureDreamsIntroRestore.PlayerNameAddress,
                blankName,
                out memoryError) &&
            memory.TryWrite(
                AzureDreamsIntroRestore.ReturningAngelScriptAddress,
                loadedOriginalAngel,
                out memoryError) &&
            memory.TryWrite(0x8001_2d5c, money, out memoryError) &&
            memory.TryWrite(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.IntroRestoreStateOffset,
                state,
                out memoryError) &&
            AzureDreamsIntroRestore.TrySynchronize(
                memory,
                identity,
                ref introWindowClosed,
                out proactiveRestore,
                out introMessage,
                returningDirectory),
            memoryError ?? introMessage);

        byte[] observedName = new byte[AzureDreamsIntroRestore.PlayerNameSize];
        Span<byte> marker = stackalloc byte[1];
        Require(
            proactiveRestore.Event ==
                AzureDreamsIntroRestoreEvent.CheckpointRestored &&
            proactiveRestore.CheckpointLifecycleComplete &&
            !proactiveRestore.BlocksNormalSynchronization &&
            proactiveRestore.Checkpoint?.Reason ==
                AzureDreamsCheckpointReason.ShopPurchase &&
            memory.TryRead(
                AzureDreamsIntroRestore.PlayerNameAddress,
                observedName,
                out memoryError) &&
            observedName.SequenceEqual(savedName) &&
            memory.TryRead(
                AzureDreamsIntroRestore.ReturningAngelScriptAddress,
                observedAngel,
                out memoryError) &&
            observedAngel.AsSpan().SequenceEqual(
                AzureDreamsIntroRestore.ReturningAngelScript) &&
            memory.TryRead(0x8001_2d5c, money, out memoryError) &&
            BinaryPrimitives.ReadUInt32LittleEndian(money) == 0x1020_3040 &&
            memory.TryRead(
                AzureDreamsIntroRestore.ReturningPitaSkipMarkerAddress,
                marker,
                out memoryError) &&
            marker[0] ==
                AzureDreamsIntroRestore.ReturningPitaSkipMarkerValue &&
            memory.TryRead(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.IntroRestoreStateOffset,
                state,
                out memoryError) &&
            state[0] == AzureDreamsIntroRestore.StateApplyComplete,
            memoryError ??
            "Startup discovery did not restore, stage the welcome, and arm the Pita guard.");

        BinaryPrimitives.WriteUInt32LittleEndian(money, 0x9999_9999);
        state[0] = AzureDreamsIntroRestore.StateApplyRequest;
        AzureDreamsIntroRestoreResult restored = default;
        Require(
            memory.TryWrite(0x8001_2d5c, money, out memoryError) &&
            memory.TryWrite(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.IntroRestoreStateOffset,
                state,
                out memoryError) &&
            AzureDreamsIntroRestore.TrySynchronize(
                memory,
                identity,
                ref introWindowClosed,
                out restored,
                out introMessage,
                returningDirectory),
            memoryError ?? introMessage);
        Require(
            restored.Event == AzureDreamsIntroRestoreEvent.CheckpointRestored &&
            restored.CheckpointLifecycleComplete &&
            restored.Checkpoint?.Reason == AzureDreamsCheckpointReason.ShopPurchase &&
            memory.TryRead(0x8001_2d5c, money, out memoryError) &&
            BinaryPrimitives.ReadUInt32LittleEndian(money) == 0x1020_3040 &&
            memory.TryRead(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.IntroRestoreStateOffset,
                state,
                out memoryError) &&
            state[0] == AzureDreamsIntroRestore.StateApplyComplete,
            memoryError ??
            "Angel confirmation did not apply and acknowledge the full checkpoint.");

        BinaryPrimitives.WriteUInt32LittleEndian(money, 0x5566_7788);
        state[0] = AzureDreamsIntroRestore.StateCaptureRequest;
        AzureDreamsIntroRestoreResult captured = default;
        Require(
            memory.TryWrite(0x8001_2d5c, money, out memoryError) &&
            memory.TryWrite(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.IntroRestoreStateOffset,
                state,
                out memoryError) &&
            AzureDreamsIntroRestore.TrySynchronize(
                memory,
                identity,
                ref introWindowClosed,
                out captured,
                out introMessage,
                initialDirectory),
            memoryError ?? introMessage);
        Require(
            captured.Event ==
                AzureDreamsIntroRestoreEvent.InitialCheckpointCaptured &&
            captured.CheckpointLifecycleComplete &&
            captured.Checkpoint?.Reason == AzureDreamsCheckpointReason.InitialTown &&
            File.Exists(AzureDreamsTownCheckpoint.GetSnapshotPath(
                identity,
                initialDirectory)) &&
            memory.TryRead(
                AzureDreamsTownMailbox.Address +
                    AzureDreamsTownMailbox.IntroRestoreStateOffset,
                state,
                out memoryError) &&
            state[0] == AzureDreamsIntroRestore.StateCaptureComplete,
            memoryError ??
            "The first-run wake-up boundary did not capture and acknowledge a checkpoint.");

        // Returning from the tower reloads the town overlay, which restores the
        // mailbox's as-patched contents and drops the handshake state back to
        // StateFirstRun in the middle of a session. The client must not re-arm
        // and block every location check and delivery waiting for an angel
        // resource that cannot load again.
        mailbox[AzureDreamsTownMailbox.IntroRestoreStateOffset] =
            AzureDreamsIntroRestore.StateFirstRun;
        byte[] livePlayAngel =
            new byte[AzureDreamsIntroRestore.ReturningAngelScript.Length];
        Array.Fill(livePlayAngel, (byte)0x5a);
        Require(
            memory.TryWrite(
                AzureDreamsTownMailbox.Address,
                mailbox,
                out memoryError) &&
            memory.TryWrite(
                AzureDreamsIntroRestore.ReturningAngelScriptAddress,
                livePlayAngel,
                out memoryError),
            memoryError ?? "Could not stage a reloaded town-overlay mailbox.");

        // Live play: something has been received, so the save block is proof
        // that the boot intro finished however the mailbox now reads.
        introWindowClosed = false;
        Require(
            AzureDreamsReceiveState.TryWriteReceivedItemCount(memory, 3, out introMessage),
            introMessage);
        Require(
            AzureDreamsReceiveState.TryReadSaveIsPristine(
                memory,
                out bool livePlayPristine,
                out introMessage) &&
            !livePlayPristine,
            introMessage.Length > 0
                ? introMessage
                : "A populated save block still reported as an untouched new game.");
        Require(
            AzureDreamsIntroRestore.TrySynchronize(
                memory,
                identity,
                ref introWindowClosed,
                out AzureDreamsIntroRestoreResult afterTowerReturn,
                out introMessage,
                returningDirectory) &&
            !afterTowerReturn.BlocksNormalSynchronization &&
            afterTowerReturn.CheckpointLifecycleComplete &&
            introWindowClosed,
            introMessage.Length > 0
                ? introMessage
                : "A reloaded town mailbox re-armed the intro handshake and blocked synchronization.");

        // The same reloaded mailbox on a genuinely pristine save is a real
        // first run and must still be serviced.
        Require(
            AzureDreamsReceiveState.TryWriteReceivedItemCount(memory, 0, out introMessage) &&
            AzureDreamsReceiveState.TrySetProgressiveKeycardLevel(memory, 0, out introMessage) &&
            memory.TryWrite(
                AzureDreamsReceiveState.PersistentStateAddress +
                    AzureDreamsReceiveState.PersistentLocationMaskOffset,
                new byte[AzureDreamsReceiveState.LocationMaskSize],
                out memoryError) &&
            memory.TryWrite(
                AzureDreamsReceiveState.PersistentStateAddress +
                    AzureDreamsReceiveState.PersistentShopMaskOffset,
                new byte[AzureDreamsReceiveState.ShopLocationMaskSize],
                out memoryError),
            memoryError ?? introMessage);
        introWindowClosed = false;
        Require(
            AzureDreamsReceiveState.TryReadSaveIsPristine(
                memory,
                out bool clearedPristine,
                out introMessage) &&
            clearedPristine,
            introMessage.Length > 0
                ? introMessage
                : "An untouched save block did not report as a new game.");
        Require(
            AzureDreamsIntroRestore.TrySynchronize(
                memory,
                identity,
                ref introWindowClosed,
                out AzureDreamsIntroRestoreResult pristineBoot,
                out introMessage,
                returningDirectory) &&
            pristineBoot.BlocksNormalSynchronization &&
            !introWindowClosed,
            introMessage.Length > 0
                ? introMessage
                : "A pristine save no longer waits for the angel resource to load.");
    }

    private static void TestLaunchAndAssociation()
    {
        // Exit-code classification is the whole basis for telling a deliberate
        // quit from a crash, so pin the three cases.
        Require(
            GameLauncher.Classify(0).Outcome == GameSessionOutcome.ClosedByPlayer,
            "A clean exit was not treated as the player closing the game.");
        Require(
            GameLauncher.Classify(unchecked((int)0xC0000005)).Outcome ==
                GameSessionOutcome.Crashed,
            "An access violation was not treated as a crash.");
        Require(
            GameLauncher.Classify(1).Outcome == GameSessionOutcome.Terminated,
            "An outside kill was not reported as indeterminate.");

        // The patched disc must land beside the patch so a second launch can
        // reuse it instead of rebuilding.
        string patch = Path.Combine(Path.GetTempPath(), "ADAP-selftest", "room_P1_Slot.adpatch");
        string original = Path.Combine(Path.GetTempPath(), "ADAP-selftest", "original.bin");
        (string binPath, string cuePath) = PpfPatchService.GetOutputPaths(patch, original);
        Require(
            Path.GetDirectoryName(binPath) == Path.GetDirectoryName(Path.GetFullPath(patch)),
            "The patched disc is not written beside its patch.");
        Require(
            Path.GetFileNameWithoutExtension(binPath) == "room_P1_Slot" &&
            Path.GetExtension(binPath) == ".bin" &&
            Path.GetExtension(cuePath) == ".cue",
            $"The patched disc is named {Path.GetFileName(binPath)} rather than after its patch.");

        // The original-disc fingerprint check. Verification runs before
        // anything is opened or written, so a dummy patch file and a
        // wrong-size "original" exercise both refusal shapes end to end
        // without building a disc.
        string fingerprintRoot = Path.Combine(
            Path.GetTempPath(), "ADAP-selftest-fingerprint");
        Directory.CreateDirectory(fingerprintRoot);
        try
        {
            string dummyPatch = Path.Combine(fingerprintRoot, "room.adpatch");
            string wrongOriginal = Path.Combine(fingerprintRoot, "not-azure.bin");
            File.WriteAllBytes(dummyPatch, new byte[PpfPatchService.PpfHeaderSize]);
            File.WriteAllBytes(wrongOriginal, new byte[64]);

            // No confirm callback (the CLI path): a mismatch is a hard fail.
            bool hardFailed = false;
            try
            {
                PpfPatchService.ApplyAsync(
                        dummyPatch, wrongOriginal, overwrite: true)
                    .GetAwaiter()
                    .GetResult();
            }
            catch (InvalidDataException)
            {
                hardFailed = true;
            }
            Require(
                hardFailed,
                "A wrong-size original was patched without a confirm callback.");

            // The windowed path: the callback sees the mismatch detail and its
            // refusal cancels the operation before anything is written.
            string? observedDetail = null;
            bool cancelled = false;
            try
            {
                PpfPatchService.ApplyAsync(
                        dummyPatch,
                        wrongOriginal,
                        overwrite: true,
                        progress: null,
                        cancellationToken: default,
                        confirmUnverifiedOriginal: detail =>
                        {
                            observedDetail = detail;
                            return Task.FromResult(false);
                        })
                    .GetAwaiter()
                    .GetResult();
            }
            catch (OperationCanceledException)
            {
                cancelled = true;
            }
            Require(
                cancelled,
                "Declining the unverified-original warning did not cancel patching.");
            Require(
                observedDetail is not null &&
                observedDetail.Contains("bytes", StringComparison.Ordinal),
                "The unverified-original warning did not describe the size mismatch.");
            Require(
                !File.Exists(Path.ChangeExtension(dummyPatch, ".bin")),
                "A refused patch still produced an output disc.");
        }
        finally
        {
            if (Directory.Exists(fingerprintRoot))
                Directory.Delete(fingerprintRoot, recursive: true);
        }

        // A registration made by an earlier build points at a folder that no
        // longer exists, which is what left Windows showing its "how do you
        // want to open this" picker. The client repairs that on launch, so the
        // staleness check has to be right.
        const string current = @"C:\App64\AzureDreams.Archipelago.Client.exe";
        const string stale = @"C:\App61\AzureDreams.Archipelago.Client.exe";
        Require(
            PatchFileAssociation.CommandTargets($"\"{current}\" \"%1\"", current),
            "A registration pointing at this build was not recognised as current.");
        Require(
            !PatchFileAssociation.CommandTargets($"\"{stale}\" \"%1\"", current),
            "A registration left behind by an earlier build was treated as current.");
        Require(
            !PatchFileAssociation.CommandTargets(null, current) &&
            !PatchFileAssociation.CommandTargets($"\"{current}\"", string.Empty),
            "A missing command or executable path was treated as a match.");

#if ADAP_STABLE
        Require(
            PatchFileAssociation.Extension == ".adpatch",
            "The stable patch extension changed; the association and the promoted APWorld must agree.");
        Require(
            PatchFileAssociation.ProgId == "ADAP.PatchFile.1",
            "The stable ProgID changed; existing player associations would silently break.");
#else
        Require(
            PatchFileAssociation.Extension == ".adpatch-dev",
            "The dev patch extension changed; the association and the dev APWorld must agree.");
        Require(
            PatchFileAssociation.ProgId == "ADAP.DevPatchFile.1",
            "The dev ProgID collides with or drifted from the stable one.");
#endif

        // Windows records an Open With choice as Applications\<exe name>, never
        // as our own ProgID. If nothing backs that name the choice resolves to
        // nothing and the "how do you want to open this" picker returns on every
        // double-click, which is exactly what a stale per-package client path
        // caused. The name has to be the bare executable, no directory.
        Require(
            PatchFileAssociation.ApplicationProgId(current) ==
                @"Applications\AzureDreams.Archipelago.Client.exe",
            "The Applications ProgID no longer matches what the Open With dialog records.");
        Require(
            PatchFileAssociation.ApplicationProgId(stale) ==
                PatchFileAssociation.ApplicationProgId(current),
            "The Applications ProgID depends on the folder, so a moved client would orphan it.");
        Require(
            PatchFileAssociation.ApplicationProgId(string.Empty).Length == 0,
            "An empty executable path produced an Applications ProgID.");

        // Server list and the settings that survive a restart.
        var settings = new ClientSettings();
        Require(
            settings.AllServers.Contains("archipelago.gg") &&
            settings.AllServers.Contains("localhost"),
            "The built-in servers are missing from the dropdown.");
        Require(
            !settings.AddServer("archipelago.gg") && !settings.AddServer("  "),
            "A duplicate or empty server was accepted.");
        Require(
            settings.AddServer("ap.example.net:1234") &&
            settings.AllServers.Contains("ap.example.net:1234"),
            "A new server was not saved.");
        Require(
            settings.FileAssociationAllowed is null,
            "File association consent must start unanswered rather than assumed.");
    }

    /// <summary>
    /// The reader against the real addresses: the same stale floor halfword
    /// reads as a live position in the tower and as no position in town.
    ///
    /// <para>Asserting this on the record alone would not catch the failure
    /// that actually happened - the floor was read and the loaded-mode byte
    /// beside it was not - so this drives the whole reader through synthetic
    /// RAM instead.</para>
    /// </summary>
    private static void TestLiveFloorFollowsTheLoadedMode(DuckStationMemory memory)
    {
        Span<byte> floor = stackalloc byte[2];
        BinaryPrimitives.WriteUInt16LittleEndian(floor, 7);
        Require(
            memory.TryWrite(
                AzureDreamsTowerProgress.CurrentFloorAddress,
                floor,
                out string? floorWriteError),
            floorWriteError ?? "Could not stage the synthetic tower floor.");

        Require(
            memory.TryWrite(
                AzureDreamsTownCheckpoint.LoadedModeAddress,
                [AzureDreamsTownCheckpoint.TowerMode],
                out string? modeWriteError),
            modeWriteError ?? "Could not stage the synthetic tower mode.");
        Require(
            AzureDreamsTowerProgressReader.TryRead(
                memory,
                out AzureDreamsTowerProgress towerSide,
                out string towerReadMessage),
            towerReadMessage);
        Require(
            towerSide.CurrentFloor == 7 && towerSide.IsInTower &&
            towerSide.IsOnLiveFloor,
            $"Floor 7 in tower mode did not read as a live position " +
            $"(floor {towerSide.CurrentFloor}, inTower {towerSide.IsInTower}).");

        // The halfword is deliberately left alone: this is exactly the state
        // the game leaves behind when the player walks back into town.
        Require(
            memory.TryWrite(
                AzureDreamsTownCheckpoint.LoadedModeAddress,
                [AzureDreamsTownCheckpoint.TownMode],
                out modeWriteError),
            modeWriteError ?? "Could not stage the synthetic town mode.");
        Require(
            AzureDreamsTowerProgressReader.TryRead(
                memory,
                out AzureDreamsTowerProgress townSide,
                out string townReadMessage),
            townReadMessage);
        Require(
            townSide.CurrentFloor == 7 && !townSide.IsInTower &&
            !townSide.IsOnLiveFloor,
            $"The stale floor still read as a live position in town " +
            $"(floor {townSide.CurrentFloor}, inTower {townSide.IsInTower}).");
        Require(
            townSide.HasCurrentFloor,
            "Returning to town discarded the floor value goal recognition reads.");
    }

    private static void TestTowerProgressView()
    {
        // The sprites are embedded resources. A rename or a dropped csproj glob
        // would leave the tower view silently blank, so prove they resolve.
        Require(ClientAssets.Chest is not null, "The chest sprite is not embedded in the client.");
        Require(ClientAssets.Crystal is not null, "The crystal sprite is not embedded in the client.");
        Require(ClientAssets.KohHead is not null, "The Koh sprite is not embedded in the client.");
        Require(ClientAssets.Medal is not null, "The medal sprite is not embedded in the client.");
        Require(
            ClientAssets.LockedCrystal is not null,
            "The locked-shortcut crystal tint could not be produced.");

        // The journal is one byte per floor from floor 1, bit = slot (ADSV
        // v4), three slots per floor.
        byte[] mask = new byte[AzureDreamsReceiveState.LocationMaskSize];
        mask[0] = 0b0000_0010;   // floor 1 slot 1
        mask[4] = 0b0000_0101;   // floor 5 slots 0 and 2 (the carrier's)
        var progress = new AzureDreamsTowerProgress(mask, 2, 7, 0);
        Require(
            !progress.IsCollected(1, 0) && progress.IsCollected(1, 1) && !progress.IsCollected(1, 2),
            "The tower view mapped floor 1's slots to the wrong mask bits.");
        Require(
            progress.IsCollected(5, 0) && !progress.IsCollected(5, 1) && progress.IsCollected(5, 2),
            "The tower view mapped floor 5's slots to the wrong mask bits.");
        Require(
            !progress.IsCollected(2, 0) && !progress.IsCollected(4, 0) && !progress.IsCollected(6, 0),
            "A floor byte leaked into a neighbouring floor.");
        Require(
            AzureDreamsTowerProgress.SlotsPerFloor == 3 &&
            !progress.IsCollected(1, AzureDreamsTowerProgress.SlotsPerFloor),
            "The tower view accepted a slot past the per-floor count.");
        Require(
            !progress.IsCollected(0, 0) &&
            !progress.IsCollected(AzureDreamsTowerProgress.TopFloor, 0),
            "The tower view accepted a floor outside the item range.");

        // A room with the carrier system off has two checks a floor. The
        // journal keeps its three bits per floor - that is the save's layout,
        // not the room's - so only what the VIEW draws changes, and the third
        // column stays where it is rather than resizing the whole tower.
        Require(
            progress.CarrierChecks &&
            progress.SlotsWithChecks == AzureDreamsTowerProgress.SlotsPerFloor,
            "A room defaults to something other than three checks a floor.");
        var withoutCarrier = progress with { CarrierChecks = false };
        Require(
            withoutCarrier.SlotsWithChecks == 2 &&
            AzureDreamsTowerProgress.CarrierSlot == 2 &&
            AzureDreamsReceiveState.SlotsPerFloor == 3,
            "Switching the carrier off did not drop exactly the third slot.");
        Require(
            !progress.Equivalent(withoutCarrier),
            "The carrier flag is outside the equivalence test, so the third column "
            + "would keep its chests until something else in the record moved.");
        Require(
            withoutCarrier.IsCollected(5, 0) && !withoutCarrier.IsCollected(5, 1),
            "Switching the carrier off changed how the journal is read.");

        // Reachability must match the formula the in-game HUD prints.
        Require(
            progress.MaxReachableFloor ==
                Math.Min(AzureDreamsTowerProgress.TopFloor, 2 * 5 + 4),
            "The tower view disagrees with the HUD about the deepest reachable floor.");
        Require(
            new AzureDreamsTowerProgress(mask, 8, 0, 0).MaxReachableFloor ==
                AzureDreamsTowerProgress.TopFloor,
            "A maximum keycard level did not reach the top floor.");

        // Floor 10 opens at level 2; the deeper shortcuts stay locked.
        Require(
            progress.IsShortcutUnlocked(2) &&
            !progress.IsShortcutUnlocked(4) &&
            !progress.IsShortcutUnlocked(6),
            "The tower view unlocked the wrong shortcuts for keycard level 2.");
        // Koh is only drawn while the floor reads as a real tower floor. The
        // warp helper briefly stages a marked request in the same halfword.
        Require(
            progress.HasCurrentFloor && progress.CurrentFloor == 7,
            "The tower view did not accept a live tower floor.");
        Require(
            !new AzureDreamsTowerProgress(mask, 2, 0, 0).HasCurrentFloor &&
            !new AzureDreamsTowerProgress(mask, 2, 41, 0).HasCurrentFloor &&
            !new AzureDreamsTowerProgress(mask, 2, 0x8000 | 9, 0).HasCurrentFloor,
            "The tower view accepted a floor value that is not a live tower floor.");

        // The floor halfword is save-backed and is not cleared on the way back
        // to town, so it keeps reading the last trip's floor. Two rules come
        // apart at exactly that point, and both matter:
        //
        //   the view must stop drawing Koh and the live-floor highlight,
        //   goal recognition must keep trusting the stale value.
        //
        // A stale unmarked 40 can only exist if floor 40 was genuinely reached,
        // so a client attaching after the fact still sees the goal - which is
        // why this is two predicates rather than one tightened one.
        var inTown = new AzureDreamsTowerProgress(mask, 2, 7, 0, IsInTower: false);
        var inTower = new AzureDreamsTowerProgress(mask, 2, 7, 0, IsInTower: true);
        Require(
            !inTown.IsOnLiveFloor && inTower.IsOnLiveFloor,
            "The live-floor marker does not follow the loaded mode.");
        Require(
            inTown.HasCurrentFloor && inTower.HasCurrentFloor,
            "Leaving the tower discarded the floor goal recognition reads.");
        Require(
            !new AzureDreamsTowerProgress(mask, 2, 0, 0, IsInTower: true).IsOnLiveFloor,
            "A tower mode with no real floor still claimed a live position.");
        // Returning to town changes nothing else about the state, so a panel
        // that ignored the flag here would leave the marker up until something
        // unrelated moved.
        Require(
            !inTown.Equivalent(inTower),
            "Leaving the tower reads as an unchanged state; the marker would " +
            "never be repainted away.");
        Require(ClientAssets.Bell is not null, "The bell sprite is not embedded in the client.");

        // Shops open at keycard 0 and 3; slot bits run ten per shop.
        var shopProgress = new AzureDreamsTowerProgress(mask, 3, 0, 0b0000_0000_0000_0100_0001u);
        Require(
            shopProgress.IsShopUnlocked(0) && shopProgress.IsShopUnlocked(1),
            "Keycard level 3 did not unlock both shops.");
        Require(
            !new AzureDreamsTowerProgress(mask, 2, 0, 0).IsShopUnlocked(1) &&
            new AzureDreamsTowerProgress(mask, 2, 0, 0).IsShopUnlocked(0),
            "Keycard level 2 unlocked the wrong shops.");
        Require(
            AzureDreamsTowerProgress.RequiredKeycardLevel(0) == 0 &&
            AzureDreamsTowerProgress.RequiredKeycardLevel(1) == 3,
            "The shop keycard thresholds no longer match the Monster Shop door gate.");
        // The locked visual must exist even while lock.png is missing: the
        // red-tinted chest fallback is the only cue that a shop is out of
        // reach, and losing it silently is exactly what happened once.
        Require(
            ClientAssets.LockedSlot is not null,
            "A locked shop has no visual: both lock.png and the tinted-chest " +
            "fallback failed to produce an image.");
        Require(
            shopProgress.IsShopSlotPurchased(0, 0) &&
            !shopProgress.IsShopSlotPurchased(0, 1) &&
            shopProgress.IsShopSlotPurchased(0, 6),
            "The shop view mapped Equipment slots to the wrong mask bits.");
        Require(
            !shopProgress.IsShopSlotPurchased(1, 0) &&
            !shopProgress.IsShopSlotPurchased(0, 10) &&
            !shopProgress.IsShopSlotPurchased(2, 0),
            "The shop view accepted a slot outside its range.");
        Require(
            ClientAssets.LockedSlot is not null,
            "No locked-slot image is available for a shop that is not open yet.");

        // data.json is linked into the build, so a broken path or a rename
        // would leave every incoming slot blank.
        Require(
            AzureDreamsItemCatalog.MappedItemCount > 100,
            $"The embedded item catalog only mapped {AzureDreamsItemCatalog.MappedItemCount} items.");
        long medicinalHerb = AzureDreamsItemManifest.EncodeProtocolItemId(1, 1, 0);
        Require(
            AzureDreamsItemCatalog.TryGetIconFile(medicinalHerb) == "herb.png",
            "The item catalog did not resolve a known category 1 item to its icon.");
        long fireBall = AzureDreamsItemManifest.EncodeProtocolItemId(4, 1, 7);
        Require(
            AzureDreamsItemCatalog.TryGetIconFile(fireBall) == "ball.png",
            "A quality-bearing item did not resolve to its base icon.");
        Require(
            AzureDreamsItemCatalog.TryGetIconFile(AzureDreamsItemManifest.ItemIdBase) is null,
            "The item catalog resolved an id outside the encodable range.");

        // Delivery is bounded by what the game has an item for, and nothing
        // else. The Oleem is the case that first needed it.
        Require(
            AzureDreamsItemManifest.TryGetInventoryDescriptor(
                AzureDreamsItemManifest.EncodeProtocolItemId(12, 9, 0),
                out AzureDreamsItemDescriptor oleem) &&
            oleem == new AzureDreamsItemDescriptor(9, 12, 0, 0),
            "The special-category Oleem was not accepted as an inventory receive.");
        Require(
            AzureDreamsItemCatalog.TryGetIconFile(
                AzureDreamsItemManifest.EncodeProtocolItemId(12, 9, 0)) == "oleem.png",
            "The Oleem did not resolve to its icon.");
        // Items this APWorld version no longer places still decode: the client
        // must not be a second gatekeeper, or every pool change strands the
        // players who have not updated in lockstep.
        Require(
            AzureDreamsItemManifest.TryGetInventoryDescriptor(
                AzureDreamsItemManifest.EncodeProtocolItemId(1, 8, 0), out _) &&
            AzureDreamsItemManifest.TryGetInventoryDescriptor(
                AzureDreamsItemManifest.EncodeProtocolItemId(9, 3, 0), out _),
            "An item the game knows was refused because the pool dropped it.");
        // Gifts and quest items are real too - what the ROM will actually hand
        // over is the ROM's business, and gating them here only hides that.
        Require(
            AzureDreamsItemManifest.TryGetInventoryDescriptor(
                AzureDreamsItemManifest.EncodeProtocolItemId(11, 1, 0), out _) &&
            AzureDreamsItemManifest.TryGetInventoryDescriptor(
                AzureDreamsItemManifest.EncodeProtocolItemId(13, 2, 0), out _),
            "A real gift or quest item was refused as an inventory receive.");
        // The one bound that does hold: a coordinate naming no item at all.
        Require(
            !AzureDreamsItemManifest.TryGetInventoryDescriptor(
                AzureDreamsItemManifest.EncodeProtocolItemId(1, 40, 0), out _) &&
            !AzureDreamsItemManifest.TryGetInventoryDescriptor(
                AzureDreamsItemManifest.EncodeProtocolItemId(6, 9, 0), out _),
            "An item id the game has no entry for was accepted.");

        // Gift naming: the APWorld names only the ids it places, so a gift of
        // any other charge count or enchantment falls to the catalog. These are
        // the four shapes the game itself uses.
        Require(
            AzureDreamsItemCatalog.DescribeItem(4, 1, 3) == "Fire Ball (3)",
            "A ball's charge count was dropped from its gifted name.");
        Require(
            AzureDreamsItemCatalog.DescribeItem(15, 3, 2) == "Iron Sword +2" &&
            AzureDreamsItemCatalog.DescribeItem(17, 5, 1) == "Iron Shield +1" &&
            AzureDreamsItemCatalog.DescribeItem(16, 3, 4) == "Life Wand +4",
            "An enchantment was dropped from a gifted weapon, shield, or wand.");
        Require(
            AzureDreamsItemCatalog.DescribeItem(15, 3, -2) == "Iron Sword -2",
            "A cursed weapon's negative enchantment did not survive naming.");
        Require(
            AzureDreamsItemCatalog.DescribeItem(15, 3, 0) == "Iron Sword",
            "An unenchanted weapon picked up a pointless +0 suffix.");
        Require(
            AzureDreamsItemCatalog.DescribeItem(18, 3, 40) == "Dragon Egg" &&
            AzureDreamsItemCatalog.DescribeItem(1, 5, 0) == "Cure-All Herb",
            "An item with no meaningful quality had one appended anyway.");
        Require(
            AzureDreamsItemCatalog.DescribeItem(6, 9, 0) is null,
            "The catalog named an item the game does not have.");

        // An unidentified item must not announce its quality ANYWHERE in the
        // app - feed, queue, incoming panel, or console. These are the same
        // shapes the APWorld's test_native_reward_manifest pins for
        // display_name_for, so the two rules cannot drift apart silently.
        //
        // Cursed goes with the quality by construction: a negative enchantment
        // is the only way this client ever renders cursed, so hiding one hides
        // the other. That is intended, not incidental.
        Require(
            AzureDreamsItemCatalog.DescribeItem(15, 3, -2, 0xC0) == "Iron Sword" &&
            AzureDreamsItemCatalog.DescribeItem(15, 3, 2, 0x80) == "Iron Sword",
            "An unidentified weapon's enchantment survived into its name.");
        // Keyed on the flag, not the category: an unidentified ball would lose
        // its charge count too, with no change to the rule.
        Require(
            AzureDreamsItemCatalog.DescribeItem(4, 1, 3, 0x80) == "Fire Ball" &&
            AzureDreamsItemCatalog.DescribeItem(4, 1, 3) == "Fire Ball (3)",
            "The unidentified rule did not follow the flag across categories.");

        // The golden-vector IDs, carrying the Archipelago names the room would
        // print for them. The AP name is the item's identity and is untouched;
        // only what this app shows the player changes.
        Require(
            AzureDreamsItemManifest.DisplayNameFor(0x0AD2_7840, "Copper Sword") ==
                "Copper Sword" &&
            AzureDreamsItemManifest.DisplayNameFor(0x0AD7_7841, "Copper Sword (-1)") ==
                "Copper Sword",
            "An unidentified item was displayed with the quality the game is hiding.");
        // Identified items keep their Archipelago name exactly - including the
        // APWorld's own parenthesised charge count, which is not this client's
        // formatting to second-guess.
        Require(
            AzureDreamsItemManifest.DisplayNameFor(
                AzureDreamsItemManifest.EncodeProtocolItemId(4, 1, 10),
                "Fire Ball (10)") == "Fire Ball (10)",
            "An identified item's name was rewritten.");
        // Another world's item does not decode here and tells us nothing.
        Require(
            AzureDreamsItemManifest.DisplayNameFor(84, "Master Sword") == "Master Sword" &&
            AzureDreamsItemManifest.DisplayNameFor(0, "Progressive Sword") ==
                "Progressive Sword",
            "A remote world's item name was rewritten by the Azure Dreams rule.");
        // Gifts arrive as raw descriptors the APWorld never named, and an older
        // sending client puts the enchantment in the transmitted name.
        Require(
            AzureDreamsItemManifest.DisplayNameFor(
                new AzureDreamsItemDescriptor(3, 15, -2, 0xC0), "Iron Sword -2") ==
                "Iron Sword",
            "A gifted unidentified item was named from the sender's wire text.");

        // A gift carries whatever the sender held, and quality is five bits of
        // magnitude: a sixty-charge ball overflows into the native item id and
        // names a different item. The icon id drops an unrepresentable quality
        // instead, so the coordinate stays exact.
        Require(
            AzureDreamsItemManifest.TryGetInventoryDescriptor(
                AzureDreamsItemManifest.EncodeIconProtocolItemId(
                    new AzureDreamsItemDescriptor(1, 4, 60)),
                out var overflowingBall) &&
            overflowingBall.Category == 4 &&
            overflowingBall.ItemId == 1,
            "An out-of-range gift quality corrupted the item's protocol coordinate.");
        Require(
            AzureDreamsItemManifest.EncodeIconProtocolItemId(
                new AzureDreamsItemDescriptor(2, 15, -1, 0xC0)) == 0x0AD7_7841,
            "A cursed gift's icon id drifted from the golden vector.");

        // Build the real connection screen off-screen. It is a SETUP screen
        // now - the tower, the shops, the incoming queue and the activity feed
        // all live in the tracker - so a panel left parented here would be one
        // nothing feeds and nobody watches.
        using (var window = new ConnectionWindow())
        {
            window.CreateControl();
            window.PerformLayout();
            Require(
                FindControl<TowerProgressPanel>(window) is null &&
                FindControl<ShopProgressPanel>(window) is null &&
                FindControl<IncomingItemsPanel>(window) is null &&
                FindNamed(window, "ActivityFeed") is null,
                "A tracker panel is still parented to the connection screen.");

            Control? title = FindNamed(window, "AppTitle");
            Require(title is not null, "The window does not contain the app title.");
            Rectangle titleBounds = OffsetWithin(title!, window);
            foreach ((string name, Control? control) in new[]
                     {
                         ("Create YAML", FindNamed(window, "CreateYamlButton")),
                         ("Tracker", FindNamed(window, "TrackerButton")),
                     })
            {
                Require(control is not null, $"The window has no {name} button.");
                Rectangle bounds = OffsetWithin(control!, window);
                Require(
                    bounds.Right <= window.ClientSize.Width,
                    $"The {name} button ends at x={bounds.Right}, past the window's " +
                    $"{window.ClientSize.Width}px client width; the header must fit its own row.");
                Require(
                    bounds.Left >= titleBounds.Right,
                    $"The {name} button starts at x={bounds.Left}, overlapping the title " +
                    $"that ends at x={titleBounds.Right}.");
            }

            // Every field row must line up: same centre.
            foreach (string[] group in new[]
            {
                new[] { "PatchPath", "PatchButton" },
                new[] { "OriginalRomPath", "OriginalRomButton" },
                new[] { "EmulatorPath", "EmulatorButton" },
                new[] { "ServerBox", "PortBox", "SaveServerButton" },
            })
            {
                var found = group
                    .Select(n => FindNamed(window, n))
                    .Where(c => c is not null)
                    .Select(c => c!)
                    .ToArray();
                Require(
                    found.Length == group.Length,
                    $"The connection screen is missing one of {string.Join(", ", group)}.");
                string report = string.Join(
                    ", ",
                    found.Select(c => $"{c.Name} y={OffsetWithin(c, window).Top} h={c.Height}"));
                Console.WriteLine($"  Row: {report}");
                // Centres, not tops: a DropDown combo is font-locked to a
                // shorter height than the boxes beside it, so centring is
                // the alignment that can actually be achieved.
                Rectangle first = OffsetWithin(found[0], window);
                int centre = first.Top + first.Height / 2;
                foreach (Control c in found)
                {
                    Rectangle bounds = OffsetWithin(c, window);
                    Require(
                        Math.Abs(bounds.Top + bounds.Height / 2 - centre) <= 1,
                        $"Field row misaligned: {report}");
                }
            }

            // The window is sized to its own content, and the progress bar
            // that appears mid-patch is part of that content even while it is
            // hidden - a window measured without it would clip the Game panel
            // the moment a build started.
            Control? gameSection = FindNamed(window, "GameSection");
            Require(gameSection is not null, "The window does not contain the Game section.");
            Rectangle gameBounds = OffsetWithin(gameSection!, window);
            Require(
                gameBounds.Bottom <= window.ClientSize.Height,
                $"The Game section ends at y={gameBounds.Bottom} inside a " +
                $"{window.ClientSize.Height}px client area; it is clipped.");
            // The bottom margin is the side margin. It used to be three times
            // it, because a row was reserved for a progress bar that is
            // visible only during a build.
            int bottomMargin = window.ClientSize.Height - gameBounds.Bottom;
            Require(
                bottomMargin == ShellPaddingForTest,
                $"The window leaves {bottomMargin}px under the Game section " +
                $"against a {ShellPaddingForTest}px margin everywhere else.");
            // That bar now floats in the margin, positioned by hand - so it is
            // the one control here that can be laid out off the window.
            ProgressBar? patchBar = FindControl<ProgressBar>(window);
            Require(patchBar is not null, "The window has no patch progress bar.");
            Rectangle bar = OffsetWithin(patchBar!, window);
            Require(
                bar.Top >= gameBounds.Bottom && bar.Bottom <= window.ClientSize.Height &&
                bar.Left >= 0 && bar.Right <= window.ClientSize.Width && bar.Width > 0,
                $"The patch progress bar sits at {bar} against a " +
                $"{window.ClientSize.Width}x{window.ClientSize.Height} client area with the " +
                $"Game section ending at y={gameBounds.Bottom}; it belongs in the margin " +
                "under it.");
            Require(
                !patchBar!.Visible,
                "The patch progress bar is showing with no patch in flight.");
            Console.WriteLine(
                $"  Connection screen: window {window.Size.Width}x{window.Size.Height}, " +
                $"Game section bottom={gameBounds.Bottom}.");
        }

        Require(
            AzureDreamsTowerProgress.Shortcuts.Length == 3 &&
            AzureDreamsTowerProgress.Shortcuts[0] == (10, 2) &&
            AzureDreamsTowerProgress.Shortcuts[1] == (20, 4) &&
            AzureDreamsTowerProgress.Shortcuts[2] == (30, 6),
            "The marked shortcut floors no longer match the warp implementation.");
    }

    /// <summary>
    /// Compact mode, asserted against the real window the same way the full
    /// layout is. The failure this guards is a silent one: the panels are
    /// moved between two layouts rather than duplicated, so a switch that
    /// dropped one, clipped the tower, or forgot to put a panel back would
    /// still run - it would just quietly stop showing the session.
    /// </summary>
    /// <summary>
    /// The feed carries every room-wide send, so in a full multiworld it is
    /// the one control that grows without limit for as long as the session
    /// lasts - and a RichTextBox charges for its whole document on every
    /// append. Prove the bound holds and that it drops the oldest lines
    /// rather than the newest.
    /// </summary>
    private static void TestActivityFeedIsBounded()
    {
        using var window = new ConnectionWindow();
        window.CreateControl();
        // The feed moved to the tracker, and the tracker is fed whether it is
        // on screen or not - which is exactly the case this bound has to hold
        // in, since a player may never open it at all.
        TrackerWindow tracker = window.Tracker;
        tracker.CreateControl();
        var feed = (RichTextBox)FindNamed(tracker, "ActivityFeed")!;
        _ = feed.Handle;

        Color feedBackground = feed.BackColor;
        const int overflow = 120;
        for (int line = 0; line < ConnectionWindow.ActivityLineLimit + overflow; line++)
        {
            window.AppendTransfer(new ClientTransfer(
                ClientTransferKind.Sent,
                $"Item {line}",
                "Sandknight",
                "Wugga",
                false,
                false));
        }

        // Lines counts the document's own paragraphs; a trailing newline adds
        // one empty last entry, hence the slack.
        int lines = feed.Lines.Length;
        Require(
            lines <= ConnectionWindow.ActivityLineLimit + 1,
            $"The activity feed grew to {lines} lines, past its {ConnectionWindow.ActivityLineLimit} limit.");
        Require(
            lines >= ConnectionWindow.ActivityLinesKept,
            $"Trimming the activity feed left only {lines} lines of scrollback.");
        Require(
            feed.Text.Contains(
                $"Item {ConnectionWindow.ActivityLineLimit + overflow - 1}",
                StringComparison.Ordinal),
            "Trimming the activity feed dropped the newest line.");
        Require(
            !feed.Text.Contains("Item 0 ", StringComparison.Ordinal),
            "Trimming the activity feed kept the oldest line.");
        // The trim lifts ReadOnly to delete. A text box repaints itself when
        // that flag moves, so prove the feed came back the colour it was.
        Require(
            feed.ReadOnly && feed.BackColor == feedBackground,
            "Trimming the activity feed left it editable or recoloured.");
    }

    /// <summary>
    /// The tracker, asserted against the real window. Three separate failures
    /// are silent without this: a panel that never made it across, a window
    /// whose scale solver disagrees with the size it opens at, and - the new
    /// one - a tracker painting its default colours as though they were
    /// readings of a live game.
    /// </summary>
    private static void TestTrackerWindow()
    {
        using var window = new ConnectionWindow();
        window.CreateControl();
        window.PerformLayout();
        _ = window.Handle;

        TrackerWindow tracker = window.Tracker;
        tracker.CreateControl();
        tracker.PerformLayout();
        _ = tracker.Handle;
        Require(
            !window.IsTrackerOpen,
            "The tracker was already on screen before anything asked for it.");

        // The setup controls stayed behind. The split is the point of the
        // window: one screen is touched once, the other is watched for hours.
        foreach (string name in new[]
        {
            "ServerBox", "PortBox", "SaveServerButton", "PatchPath", "PatchButton",
            "OriginalRomPath", "EmulatorPath", "EmulatorButton", "CreateYamlButton",
            "TrackerButton",
        })
        {
            Require(
                FindNamed(tracker, name) is null,
                $"The tracker carries the {name} control, which belongs to the setup screen.");
        }

        IncomingItemsPanel? incoming = FindControl<IncomingItemsPanel>(tracker);
        TowerProgressPanel? tower = FindControl<TowerProgressPanel>(tracker);
        ShopProgressPanel? shops = FindControl<ShopProgressPanel>(tracker);
        Control? activity = FindNamed(tracker, "ActivityFeed");
        Require(
            incoming is not null && tower is not null && shops is not null &&
            activity is not null,
            "The tracker is missing one of its four panels.");
        Require(
            incoming!.LayoutMode == IncomingItemsLayout.VerticalNamed,
            "The tracker incoming queue is drawing the horizontal icon row.");

        // The strip is what the SESSION is worth: the two connection states
        // went back to the setup screen, which is the only window that can act
        // on either, and the three sands took the room they freed.
        Require(
            FindNamed(tracker, "TrackerRoomStatus") is null &&
            FindNamed(tracker, "TrackerGameStatus") is null,
            "The tracker still duplicates the connection screen's status lines.");
        Control? keycards = FindNamed(tracker, "TrackerKeycardValue");
        Control? sendTokens = FindNamed(tracker, "TrackerSendTokenValue");
        Control? redSand = FindNamed(tracker, "TrackerRedSandValue");
        Control? blueSand = FindNamed(tracker, "TrackerBlueSandValue");
        Control? whiteSand = FindNamed(tracker, "TrackerWhiteSandValue");
        Require(
            keycards is not null && sendTokens is not null && redSand is not null &&
            blueSand is not null && whiteSand is not null,
            "The tracker is missing the keycard, send-token or sand readouts.");
        Require(
            redSand!.Text == "-" && blueSand!.Text == "-" && whiteSand!.Text == "-",
            "The sand readouts claim a level before any save has been read.");
        // Three sands, three sprites, three DIFFERENT sprites: the colour is
        // the only thing telling them apart, so a failed tint would leave two
        // of them identical and the strip unreadable.
        Require(
            ClientAssets.RedSand is not null && ClientAssets.BlueSand is not null &&
            ClientAssets.WhiteSand is not null,
            "A sand sprite is missing: sand.png or one of its tints failed to load.");
        foreach (string name in new[]
        {
            "TrackerRedSandIcon", "TrackerBlueSandIcon", "TrackerWhiteSandIcon",
        })
        {
            Control? icon = FindNamed(tracker, name);
            Require(
                icon is PictureBox box && box.Image is not null,
                $"The tracker's {name} has no sprite in it.");
        }

        // Drained until all three of room, game and progression are in hand.
        // Every colour in here means something specific - a red crystal is a
        // locked shortcut, a red shop is a shut shop - and before the game has
        // answered, those are DEFAULTS wearing the same paint.
        Require(
            !tracker.IsLive && !tower!.Live && !shops!.Live,
            "The tracker opened in colour, before it had a room, a game or a " +
            "progression snapshot to colour anything from.");
        window.HandleClientOutput("Archipelago login succeeded for Septic.");
        Require(
            !tracker.IsLive,
            "A room alone turned the tracker colours on; the game had said nothing yet.");
        window.HandleClientOutput("Connected to DuckStation (PID 1234).");
        Require(
            !tracker.IsLive,
            "An attached game alone turned the colours on; no progression had been read.");
        byte[] liveMask = new byte[AzureDreamsReceiveState.LocationMaskSize];
        window.UpdateTower(new AzureDreamsTowerProgress(
            liveMask,
            3,
            7,
            0b0100_0001u,
            IsInTower: true,
            SendTokens: 2,
            WeaponTemperLevel: 2,
            ShieldTemperLevel: 1,
            BallChargeLevel: 3));
        Require(
            tracker.IsLive && tower!.Live && shops!.Live,
            "Room, game and a progression snapshot were all in and the tracker " +
            "stayed drained.");
        Require(
            keycards!.Text == "3/8" && sendTokens!.Text == "2",
            $"The tracker counters read \"{keycards!.Text}\" and \"{sendTokens!.Text}\" " +
            "against a snapshot of 3 keycards and 2 send tokens.");
        Require(
            redSand.Text == "2/3" && blueSand.Text == "1/3" && whiteSand.Text == "3/3",
            $"The sand readouts read \"{redSand.Text}\", \"{blueSand.Text}\" and " +
            $"\"{whiteSand.Text}\" against a save at weapon 2, shield 1, ball 3.");
        // A sand's level is a fact about the SAVE, and nothing else in the
        // record moves when one lands - so a comparison that ignored them
        // would freeze the readout until something unrelated changed.
        var beforeSand = new AzureDreamsTowerProgress(liveMask, 3, 7, 0u);
        Require(
            !beforeSand.Equivalent(beforeSand with { WeaponTemperLevel = 1 }) &&
            !beforeSand.Equivalent(beforeSand with { ShieldTemperLevel = 1 }) &&
            !beforeSand.Equivalent(beforeSand with { BallChargeLevel = 1 }),
            "A sand level changed and the progress record read as unchanged; " +
            "the readout would never be repainted.");
        // Losing the game link is losing the readings, not just the link.
        window.HandleClientOutput("Game connection waiting for DuckStation.");
        Require(
            !tracker.IsLive && !tower.Live,
            "The game link dropped and the tracker kept painting its last state " +
            "as though it were current.");
        window.HandleClientOutput("Reconnected to DuckStation (PID 1234).");
        Require(tracker.IsLive, "Reattaching to the game did not restore the colours.");

        Rectangle incomingBounds = OffsetWithin(incoming, tracker);
        Rectangle shopBounds = OffsetWithin(shops!, tracker);
        Rectangle towerBounds = OffsetWithin(tower!, tracker);
        Rectangle activityBounds = OffsetWithin(activity!, tracker);

        // The arrangement the window exists for: incoming above the activity
        // feed, shops beside it, and the feed reaching back under the shops.
        Require(
            incomingBounds.Bottom <= activityBounds.Top,
            $"The incoming queue ends at y={incomingBounds.Bottom} but the activity feed " +
            $"starts at y={activityBounds.Top}; incoming must sit above it.");
        Require(
            incomingBounds.Right <= shopBounds.Left &&
            shopBounds.Right <= towerBounds.Left,
            $"Tracker columns are out of order: incoming ends x={incomingBounds.Right}, " +
            $"shops x={shopBounds.Left}-{shopBounds.Right}, tower starts x={towerBounds.Left}.");
        Require(
            activityBounds.Right > shopBounds.Left &&
            activityBounds.Top >= shopBounds.Bottom,
            $"The activity feed spans x to {activityBounds.Right} from y={activityBounds.Top}; " +
            $"it should reach under the shops, which end at x={shopBounds.Right}, " +
            $"y={shopBounds.Bottom}.");
        Require(
            activityBounds.Right <= towerBounds.Left,
            $"The activity feed ends at x={activityBounds.Right}, past the tower left edge " +
            $"x={towerBounds.Left}.");

        // Nothing scrolls, so a short window clips silently.
        Require(
            towerBounds.Bottom <= tracker.ClientSize.Height &&
            towerBounds.Right <= tracker.ClientSize.Width &&
            activityBounds.Bottom <= tracker.ClientSize.Height,
            $"The tracker clips its content: tower ends {towerBounds.Right},"
            + $"{towerBounds.Bottom} and the feed ends y={activityBounds.Bottom} inside a "
            + $"{tracker.ClientSize.Width}x{tracker.ClientSize.Height} client area.");
        Require(
            tower.Height >= tower.ScaledHeight && shops.Height >= shops.ScaledHeight,
            $"The tracker squeezed the tower to {tower.Height}px or the shops to " +
            $"{shops.Height}px, below their {tower.ScaledHeight}/" +
            $"{shops.ScaledHeight}px content.");
        // The default tracker is the unscaled one. Anything else means the
        // size it opens at disagrees with the size its own scale solver thinks
        // that layout needs.
        Require(
            incoming.Scale == UiScale.Natural &&
            tower.Scale == UiScale.Natural &&
            shops.Scale == UiScale.Natural,
            $"The tracker opened scaled (incoming {incoming.Scale:0.###}, tower " +
            $"{tower.Scale:0.###}, shops {shops.Scale:0.###}); its default size must " +
            "be exactly what the natural layout needs.");
        // The gap this closed: ten rows that stopped short of the shops beside
        // them left an obvious dead strip under the queue.
        Require(
            IncomingItemsPanel.VerticalPreferredHeight ==
                ShopProgressPanel.PreferredHeight,
            $"The vertical incoming queue is {IncomingItemsPanel.VerticalPreferredHeight}px " +
            $"against the shops' {ShopProgressPanel.PreferredHeight}px; the two sit side by " +
            "side and must be the same height.");
        Console.WriteLine(
            $"  Tracker: window {tracker.Size.Width}x{tracker.Size.Height}, incoming " +
            $"{incomingBounds.Width}x{incomingBounds.Height}, feed " +
            $"{activityBounds.Width}x{activityBounds.Height}.");

        // The tracker has to be shrinkable, and everything drawn in it has to
        // come down together. The tower is the binding constraint - forty
        // floors is what makes the window tall - so proving it scales is
        // proving the window can actually get smaller.
        Size openedAt = tracker.Size;
        Require(
            tracker.MinimumSize.Width < openedAt.Width &&
            tracker.MinimumSize.Height < openedAt.Height,
            $"The tracker pinned its minimum at {tracker.MinimumSize.Width}x" +
            $"{tracker.MinimumSize.Height}, so the {openedAt.Width}x{openedAt.Height} " +
            "default cannot be shrunk at all.");
        Size shrunk = tracker.MinimumSize;
        tracker.Size = shrunk;
        tracker.PerformLayout();
        Require(
            tracker.Size == shrunk,
            $"The tracker refused to shrink to its own minimum {shrunk.Width}x{shrunk.Height}.");
        double small = tower.Scale;
        Require(
            small < UiScale.Natural && small >= UiScale.Minimum,
            $"Shrinking the tracker left the tower at scale {small:0.###}; it should have " +
            $"solved between {UiScale.Minimum} and {UiScale.Natural}.");
        Require(
            Math.Abs(shops.Scale - small) < 0.001 &&
            Math.Abs(incoming.Scale - small) < 0.001,
            $"The panels scaled apart (tower {small:0.###}, shops {shops.Scale:0.###}, " +
            $"incoming {incoming.Scale:0.###}); they have to move together.");
        Rectangle shrunkTower = OffsetWithin(tower, tracker);
        Require(
            tower.Height < TowerProgressPanel.PreferredHeight &&
            shops.Height < ShopProgressPanel.PreferredHeight,
            $"The tracker shrank but the tower stayed {tower.Height}px and the shops " +
            $"{shops.Height}px; the panels have to come down with it.");
        Require(
            shrunkTower.Bottom <= tracker.ClientSize.Height &&
            shrunkTower.Right <= tracker.ClientSize.Width,
            $"At minimum size the tower ends at {shrunkTower.Right},{shrunkTower.Bottom} " +
            $"outside the {tracker.ClientSize.Width}x{tracker.ClientSize.Height} client area.");

        // The status strip does not scale with the rest, so the minimum width
        // has to clear the strip's own line as well as the shrunken panels.
        // Before it did, the send-token readout was simply cut off the right
        // edge at the smallest size - silently, because nothing here scrolls
        // or wraps.
        Control? strip = FindNamed(tracker, "TrackerStatusStrip");
        Require(strip is not null, "The tracker does not contain its status strip.");
        Require(
            strip!.PreferredSize.Width <= strip.Width,
            $"At minimum size the status strip needs {strip.PreferredSize.Width}px but was " +
            $"given {strip.Width}px, so its right-hand items are clipped.");
        Rectangle tokenBounds = OffsetWithin(sendTokens!, tracker);
        Require(
            tokenBounds.Right <= tracker.ClientSize.Width,
            $"At minimum size the send-token readout ends at x={tokenBounds.Right}, " +
            $"outside the {tracker.ClientSize.Width}px client area.");
        Console.WriteLine(
            $"  Tracker minimum: window {shrunk.Width}x{shrunk.Height} at scale " +
            $"{small:0.###}, tower {tower.Width}x{tower.Height}.");

        tracker.Size = openedAt;
        tracker.PerformLayout();

        // Closing the tracker hides it. Everything in here is the run's state
        // and it keeps arriving whether the window is up or not, so a close
        // that disposed it would quietly reset the tracker mid-session.
        var feed = (RichTextBox)activity!;
        _ = feed.Handle;
        window.UpdateIncoming(
        [
            new(0x0AD2_0101, "Pita Fruit", "Sandknight"),
            new(0x0AD2_0102, "Medicinal Herb", "Wugga"),
        ]);
        window.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent, "Pita Fruit", "Sandknight", "Wugga", false, false));
        string staged = feed.Text;
        Require(
            staged.Contains("Sandknight sent Pita Fruit to Wugga.", StringComparison.Ordinal),
            "The activity feed did not accept a line staged while the tracker was closed.");

        // Shown well off-screen: this is a diagnostic run, not a reason to
        // throw a window at whoever is watching the console.
        tracker.StartPosition = FormStartPosition.Manual;
        tracker.ShowInTaskbar = false;
        tracker.Location = new Point(-30_000, -30_000);
        tracker.Show();
        Require(window.IsTrackerOpen, "A shown tracker did not report itself as open.");
        tracker.Hide();
        Require(
            !window.IsTrackerOpen && !tracker.IsDisposed,
            "Hiding the tracker disposed it, taking the session state with it.");
        tracker.Show();
        Require(
            feed.Text == staged && tracker.IsLive,
            "Reopening the tracker lost the session scrollback or its live state.");
        tracker.Hide();
    }

    private static Rectangle OffsetWithin(Control child, Control ancestor)
    {
        int x = 0;
        int y = 0;
        for (Control? current = child; current is not null && current != ancestor;
            current = current.Parent)
        {
            x += current.Left;
            y += current.Top;
        }
        return new Rectangle(x, y, child.Width, child.Height);
    }

    private const int ShellPaddingForTest = 16;

    private static Control? FindNamed(Control parent, string name)
    {
        foreach (Control child in parent.Controls)
        {
            if (child.Name == name)
                return child;
            Control? nested = FindNamed(child, name);
            if (nested is not null)
                return nested;
        }
        return null;
    }

    private static T? FindControl<T>(Control parent) where T : Control
    {
        foreach (Control child in parent.Controls)
        {
            if (child is T match)
                return match;
            T? nested = FindControl<T>(child);
            if (nested is not null)
                return nested;
        }
        return null;
    }

    private static void TestPatchingAndSettings()
    {
        byte[] patchBytes = new byte[PpfPatchService.PpfHeaderSize + 5 + 3 + 5 + 2];
        "PPF10\0"u8.CopyTo(patchBytes);
        int cursor = PpfPatchService.PpfHeaderSize;
        BinaryPrimitives.WriteUInt32LittleEndian(patchBytes.AsSpan(cursor, 4), 4);
        patchBytes[cursor + 4] = 3;
        patchBytes[cursor + 5] = 0xAA;
        patchBytes[cursor + 6] = 0xBB;
        patchBytes[cursor + 7] = 0xCC;
        cursor += 8;
        BinaryPrimitives.WriteUInt32LittleEndian(patchBytes.AsSpan(cursor, 4), 14);
        patchBytes[cursor + 4] = 2;
        patchBytes[cursor + 5] = 0x12;
        patchBytes[cursor + 6] = 0x34;

        using var patch = new MemoryStream(patchBytes, writable: false);
        using var output = new MemoryStream(Enumerable.Range(0, 24).Select(value => (byte)value).ToArray());
        int recordCount = PpfPatchService.ApplyRecordsAsync(patch, output)
            .GetAwaiter()
            .GetResult();
        Require(recordCount == 2, "The PPF1 parser did not apply both fixture records.");
        byte[] patched = output.ToArray();
        Require(
            patched.AsSpan(4, 3).SequenceEqual(new byte[] { 0xAA, 0xBB, 0xCC }) &&
            patched.AsSpan(14, 2).SequenceEqual(new byte[] { 0x12, 0x34 }),
            "The PPF1 parser wrote fixture records at the wrong offsets.");
        Require(
            patched[3] == 3 && patched[7] == 7 && patched[16] == 16,
            "The PPF1 parser changed bytes outside its records.");

        string settingsDirectory = Path.Combine(
            Path.GetTempPath(),
            "ADAP-client-self-test-" + Guid.NewGuid().ToString("N"));
        string settingsPath = Path.Combine(settingsDirectory, "settings.json");
        try
        {
            var expected = new ClientSettings
            {
                PatchFilePath = @"C:\Seeds\Player1.ppf",
                OriginalRomPath = @"D:\Games\Azure Dreams (USA).bin",
            };
            expected.Save(settingsPath);
            ClientSettings observed = ClientSettings.Load(settingsPath);
            Require(
                observed.PatchFilePath == expected.PatchFilePath &&
                observed.OriginalRomPath == expected.OriginalRomPath,
                "The saved patch and original-ROM paths did not round-trip.");
        }
        finally
        {
            if (Directory.Exists(settingsDirectory))
                Directory.Delete(settingsDirectory, true);
        }
    }

    /// <summary>
    /// The Create YAML dialog's output: an Archipelago player options file
    /// carrying the two options the APWorld defines.
    ///
    /// <para>What this pins is that the generated text round-trips through
    /// the same option names and value shapes generation reads
    /// (`traps: true|false`, `trap_chance: 0..100`) - a typo here produces a
    /// file that generates a trapless seed and says nothing about why.</para>
    /// </summary>
    private static void TestPlayerYamlCreation()
    {
        Require(
            AzureDreamsPlayerYaml.MaxSlotNameLength == 16,
            "The slot-name cap no longer matches Archipelago's sixteen characters.");

        string yaml = AzureDreamsPlayerYaml.Build(
            "Septic", traps: true, trapChance: 25, progressionBalancing: 50);
        Require(
            yaml.Contains("name: \"Septic\"") &&
            yaml.Contains("game: Azure Dreams") &&
            yaml.Contains("Azure Dreams:"),
            "The generated YAML is missing its identity block.");
        Require(
            yaml.Contains("  traps: true") &&
            yaml.Contains("  trap_chance: 25") &&
            yaml.Contains("  progression_balancing: 50"),
            "The generated YAML does not carry the options as the APWorld " +
            "and Archipelago spell them.");
        Require(
            AzureDreamsPlayerYaml.Build("Koh", false, 3, 50).Contains("  traps: false"),
            "Traps-off did not survive into the generated YAML.");
        // The two toggles: on by default, spelled as the APWorld reads them.
        Require(
            yaml.Contains("  hint_system: true") && yaml.Contains("  temper_system: true") &&
            yaml.Contains("  carrier_system: true"),
            "The generated YAML does not carry hint_system / temper_system / carrier_system "
            + "on by default.");
        string toggledOff = AzureDreamsPlayerYaml.Build(
            "Koh", false, 3, 50, hintSystem: false, temperSystem: false, carrierSystem: false);
        Require(
            toggledOff.Contains("  hint_system: false") &&
            toggledOff.Contains("  temper_system: false") &&
            toggledOff.Contains("  carrier_system: false"),
            "A toggled-off system did not survive into the generated YAML.");
        Require(
            AzureDreamsPlayerYaml.DefaultHintSystem && AzureDreamsPlayerYaml.DefaultTemperSystem,
            "The toggle defaults drifted from the APWorld's (both on).");
        // Accessibility is deliberately absent: every keycard must be
        // obtained, so there is nothing for it to change yet.
        Require(
            !yaml.Contains("accessibility"),
            "The generated YAML pins accessibility, which this world does " +
            "not need and did not ask the player about.");

        // The defaults the dialog opens on.
        Require(
            AzureDreamsPlayerYaml.DefaultTrapChance == 3 &&
            AzureDreamsPlayerYaml.DefaultProgressionBalancing == 50,
            "The option defaults drifted from what the dialog promises.");
        // Archipelago's ProgressionBalancing is a NamedRange 0-99, NOT
        // 0-100; writing 100 would be refused at generation time.
        Require(
            AzureDreamsPlayerYaml.MaxProgressionBalancing == 99,
            "Progression balancing's ceiling no longer matches Archipelago's.");

        // Out-of-range values are clamped rather than written through.
        Require(
            AzureDreamsPlayerYaml.Build("Koh", true, 250, 50).Contains("  trap_chance: 100") &&
            AzureDreamsPlayerYaml.Build("Koh", true, -5, 50).Contains("  trap_chance: 0"),
            "A trap chance outside 0-100 was not clamped.");
        Require(
            AzureDreamsPlayerYaml.Build("Koh", true, 3, 150)
                .Contains("  progression_balancing: 99") &&
            AzureDreamsPlayerYaml.Build("Koh", true, 3, -5)
                .Contains("  progression_balancing: 0"),
            "A progression balancing outside 0-99 was not clamped.");

        // Slot names: trimmed, bounded, and letters/digits/spaces only.
        Require(
            AzureDreamsPlayerYaml.TryValidateSlotName("  Septic  ", out string trimmed, out _) &&
            trimmed == "Septic",
            "A pasted slot name was not trimmed.");
        Require(
            AzureDreamsPlayerYaml.TryValidateSlotName("Sand Knight 2", out _, out _),
            "A plain letters/digits/spaces slot name was refused.");
        Require(
            !AzureDreamsPlayerYaml.TryValidateSlotName("", out _, out _) &&
            !AzureDreamsPlayerYaml.TryValidateSlotName("   ", out _, out _),
            "An empty slot name was accepted.");
        Require(
            AzureDreamsPlayerYaml.TryValidateSlotName(new string('A', 16), out _, out _) &&
            !AzureDreamsPlayerYaml.TryValidateSlotName(new string('A', 17), out _, out _),
            "The sixteen-character slot-name boundary is wrong.");
        foreach (string rejected in new[]
                 {
                     "Se{p}tic",     // Archipelago's templating braces
                     "Septic!",      // punctuation
                     "Sep-tic",      // punctuation that looks harmless
                     "a: b",         // would need YAML quoting
                     "100%",         // Archipelago's %number% templating
                     "Septéc",       // the game's CP932 encoder cannot render it
                     "Koh　Ku",  // a full-width space is not a space
                 })
        {
            Require(
                !AzureDreamsPlayerYaml.TryValidateSlotName(rejected, out _, out _),
                $"The slot name \"{rejected}\" was accepted; only letters, " +
                "numbers and spaces are allowed.");
        }
        // Archipelago refuses this name outright (Generate.handle_name), so
        // refusing it here is the difference between a clear message now and
        // a failed generation later.
        Require(
            !AzureDreamsPlayerYaml.TryValidateSlotName("Archipelago", out _, out _) &&
            !AzureDreamsPlayerYaml.TryValidateSlotName("archipelago", out _, out _),
            "The reserved name \"Archipelago\" was accepted.");

        Require(
            AzureDreamsPlayerYaml.SuggestedFileName("Sand Knight") == "Sand Knight.yaml",
            "The suggested file name is wrong.");

        // The dialog itself: it must build, lay out and take the size it was
        // matched to. Constructing it here is what catches a layout that
        // re-enters itself - the centring in the development panel is done
        // with cell anchoring precisely so nothing has to.
        var matched = new Size(1231, 966);
        using (var dialog = new CreateYamlDialog(matched))
        {
            dialog.PerformLayout();
            Screen? screen = Screen.PrimaryScreen;
            var expected = new Size(
                Math.Min(matched.Width, screen?.WorkingArea.Width ?? matched.Width),
                Math.Min(matched.Height, screen?.WorkingArea.Height ?? matched.Height));
            Require(
                dialog.Size == expected,
                $"The Create YAML dialog is {dialog.Size.Width}x{dialog.Size.Height} " +
                $"instead of the matched {expected.Width}x{expected.Height}.");

            var chance = (NumericUpDown)dialog.Controls.Find("YamlTrapChance", true).Single();
            var traps = (CheckBox)dialog.Controls.Find("YamlEnableTraps", true).Single();
            var balancing =
                (NumericUpDown)dialog.Controls.Find("YamlProgressionBalancing", true).Single();
            var slot = (TextBox)dialog.Controls.Find("YamlSlotName", true).Single();
            var hints = (CheckBox)dialog.Controls.Find("YamlHintSystem", true).Single();
            var temper = (CheckBox)dialog.Controls.Find("YamlTemperSystem", true).Single();
            Require(
                hints.Checked && temper.Checked,
                "The hint / temper toggles do not open enabled, unlike the APWorld defaults.");
            Require(
                slot.Text.Length == 0,
                "The slot name field is not empty when no saved slot is given.");
            Require(
                dialog.Controls.Find("YamlDevelopmentBanner", true).Length == 1,
                "The development banner is missing from the Create YAML dialog.");
            Require(
                slot.MaxLength == AzureDreamsPlayerYaml.MaxSlotNameLength,
                "The slot-name field does not enforce the sixteen-character cap.");
            Require(
                !traps.Checked && !chance.Enabled,
                "The trap chance is editable while traps are off, so a player " +
                "can set a number the file will ignore.");
            traps.Checked = true;
            Require(
                chance.Enabled,
                "Turning traps on did not enable the trap chance.");
            Require(
                chance.Value == AzureDreamsPlayerYaml.DefaultTrapChance &&
                balancing.Value == AzureDreamsPlayerYaml.DefaultProgressionBalancing,
                "The dialog does not open on the documented defaults.");
            Require(
                balancing.Maximum == AzureDreamsPlayerYaml.MaxProgressionBalancing,
                "The progression balancing spinner would let a player pick a " +
                "value Archipelago refuses.");

            // Every option explains itself through a hover bubble instead of
            // a line of description under it. An empty one would look
            // identical in a screenshot, so assert the help is really there.
            foreach (string bubbleName in new[]
                     {
                         "YamlHelpTraps",
                         "YamlHelpTrapChance",
                         "YamlHelpProgressionBalancing",
                         "YamlHelpHintSystem",
                         "YamlHelpTemperSystem",
                     })
            {
                Control[] found = dialog.Controls.Find(bubbleName, true);
                Require(
                    found.Length == 1,
                    $"The help bubble {bubbleName} is missing from the dialog.");
                Require(
                    found[0].Tag is string help && help.Trim().Length > 0,
                    $"The help bubble {bubbleName} carries no hover text.");
            }
        }

        // The saved slot name is loaded into the dialog - read, never written
        // back: the dialog has no settings to write to. Trimmed to the cap.
        using (var prefilled = new CreateYamlDialog(matched, "  Septic  "))
        {
            var slot = (TextBox)prefilled.Controls.Find("YamlSlotName", true).Single();
            Require(slot.Text == "Septic", "The saved slot name was not prefilled (or not trimmed).");
        }
        using (var prefilled = new CreateYamlDialog(matched, new string('A', 20)))
        {
            var slot = (TextBox)prefilled.Controls.Find("YamlSlotName", true).Single();
            Require(
                slot.Text.Length == AzureDreamsPlayerYaml.MaxSlotNameLength,
                "A saved slot name longer than the cap was not trimmed on prefill.");
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}
