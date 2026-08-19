using Archipelago.MultiClient.Net;
using Archipelago.MultiClient.Net.Enums;
using Archipelago.MultiClient.Net.MessageLog.Messages;
using Archipelago.MultiClient.Net.Models;
using Adap.Client.Emulators.DuckStation;
using Adap.Client.Games;
using Adap.Client.Windows;
using System.Diagnostics;

namespace Adap.Client.Archipelago;

internal static class AzureDreamsArchipelagoClient
{
    private const string GameName = "Azure Dreams";
    private const int DefaultPort = 38_281;
    // 13: the 5000-gold packages exist. A client below 13 would wedge its
    // receive queue on the first package (an undecodable item id blocks the
    // cursor), so the version gate refuses the room up front instead.
    // 14: the ADSV journal grew to v3/0x2C for the gold-granted counter; a
    // version-2 client cannot read the journal at all.
    // 15: trap items exist. A pre-trap client would deliver a trap item
    // into inventory as a Strange... gift instead of springing it.
    // 16: Send Token items exist.
    // 17: three tower checks per floor (117) and ADSV v4 - moved base, a
    // one-byte-per-floor journal, the town half of one unified mask, the
    // intro flags in a field of their own. A v3 client cannot find the
    // record at all.
    private const int SupportedSlotDataVersion = 19;
    private const long ProgressiveKeycardItemId = 0x0AD0_0000;
    internal const long ProgressiveKeycardItemIdForTest = ProgressiveKeycardItemId;
    // Granted straight into the gold counter, no inventory slot and no
    // native descriptor - and since 2026-08-05 (journal v3) it cuts the
    // delivery line the way keycards do: the queue exists because inventory
    // space is finite, and gold consumes none. Eager granting is made
    // exactly-once by the durable gold-granted counter (ADSV GoldGrantedCountOffset), which
    // is reconciled against the history count every poll and rolls back
    // with the gold counter and the cursor on a checkpoint restore.
    private const long GoldPackageItemId = 0x0AD0_0001;
    internal const long GoldPackageItemIdForTest = GoldPackageItemId;
    private const uint GoldPackageAmount = 5_000;
    // One tower send costs one token. Gold's twin in every respect that
    // matters here: granted straight into a game counter, no inventory slot
    // and no native descriptor, so it cuts the delivery line - the queue
    // exists because inventory space is finite and a token uses none.
    //
    // Exactly-once comes from the durable banked counter beside the game's
    // count, reconciled against the history every poll. It has to be a
    // counter and not a re-derivation because the GAME SPENDS TOKENS: the
    // live count says nothing about how many ever arrived.
    private const long SendTokenItemId = 0x0AD0_0002;
    internal const long SendTokenItemIdForTest = SendTokenItemId;
    // The blacksmith's progressive unlocks (docs/systems/blacksmith.md): a Red
    // Sand is one WEAPON temper level, a Blue Sand one SHIELD temper level,
    // three of each in the pool. They keep the native item names and the
    // native protocol ids (category 10, ids 1 and 2, quality 0, no flags) so
    // every client already decodes and displays them - but they never enter
    // the bag: like keycards they are applied eagerly from the full history
    // to the two ADSV level bytes and cut the delivery line.
    private static readonly long RedSandItemId = AzureDreamsItemManifest.EncodeProtocolItemId(10, 1, 0);
    private static readonly long BlueSandItemId = AzureDreamsItemManifest.EncodeProtocolItemId(10, 2, 0);
    // The ball charger's unlock (docs/systems/fortune-teller.md section 5): a
    // White Sand is one charge level (1/2/3 charges per town visit), three in
    // the pool, applied
    // to the byte beside ADSV the same way.
    private static readonly long WhiteSandItemId = AzureDreamsItemManifest.EncodeProtocolItemId(10, 3, 0);
    internal static long RedSandItemIdForTest => RedSandItemId;
    internal static long BlueSandItemIdForTest => BlueSandItemId;
    internal static long WhiteSandItemIdForTest => WhiteSandItemId;
    // Trap items: own-world only, disguised in the tower as Progressive
    // Keycards (docs/systems/forced-trap.md). The protocol id carries the
    // GAME trap id in the low byte - TrapItemIdBase + 1..19 - which is the
    // exact value the forced-trap stub wants in its mailbox request byte.
    //
    // A trap is SPRUNG when its location's check is first reported to the
    // server (SynchronizeLocationChecks + the slot-data trap_locations map):
    // the server's checked-location set is the durable exactly-once anchor,
    // so no journal field was needed. The received trap item itself is a
    // pure line-cutter - never queued, never delivered to inventory; the
    // cursor folds past it like a keycard's.
    private const long TrapItemIdBase = 0x0AD0_0040;
    internal const long TrapItemIdBaseForTest = TrapItemIdBase;
    private const int TrapGameIdCount = 19;
    // The forced-trap request byte. Nonzero = trap id the seed-page stub
    // will plant and spring at Koh's next idle tower frame.
    //
    // **Not the AP mailbox's +0xB0**, which is where it started: that is the
    // ADGT tower-gift record's magic, and the two overwrote each other (a
    // queued trap id blanked the magic; a committed send put 'A' in the
    // request byte, which the stub cleared as an out-of-range id). Only
    // solo seeds were ever tested, and the Send row does not exist in one.
    // Must match `patch.FORCED_TRAP_REQUEST_ADDRESS`.
    private const uint ForcedTrapRequestAddress = 0x801D_A6D8;
    internal const uint ForcedTrapRequestAddressForTest = ForcedTrapRequestAddress;

    internal static bool IsTrapItemId(long itemId) =>
        itemId > TrapItemIdBase && itemId <= TrapItemIdBase + TrapGameIdCount;

    private static bool IsTemperSandItemId(long itemId) =>
        itemId == RedSandItemId || itemId == BlueSandItemId || itemId == WhiteSandItemId;

    private static bool IsLineCutterItemId(long itemId) =>
        itemId == ProgressiveKeycardItemId ||
        itemId == GoldPackageItemId ||
        itemId == SendTokenItemId ||
        IsTemperSandItemId(itemId) ||
        IsTrapItemId(itemId);

    /// <summary>
    /// A trap waiting for the request byte, and the floor its pickup happened
    /// on. The floor is half the exactly-once story now: see
    /// <see cref="ForcedTrapQueue"/>.
    /// </summary>
    internal readonly record struct PendingTrap(byte TrapId, int Floor);

    /// <summary>
    /// Traps waiting for the request byte. One byte is one pending trap, so
    /// the queue holds the rest; the head is only removed when the stub is
    /// seen to consume its write.
    ///
    /// <para><b>A trap springs at its pickup or not at all (2026-08-18).</b>
    /// It used to spring at the next opportunity, wherever and whenever that
    /// turned out to be, and two things could hand it one long after the
    /// moment it belonged to:</para>
    ///
    /// <list type="bullet">
    /// <item>a client attaching to a save holding checks the server had never
    /// heard of - collected offline - reported them all at once, and every
    /// trap among them queued;</item>
    /// <item>a pending trap outliving the trip it was picked up in, so a
    /// reload or an elevator ride carried it to the next floor.</item>
    /// </list>
    ///
    /// <para>Both land the same way, and it is the worst possible way: the
    /// first frame of a freshly loaded floor is Koh's turn, and springing a
    /// trap there spends it - the floor's monsters move first, against a
    /// player who has not seen the room yet. That is how a Bomb Trap killed a
    /// run on arrival. So: <see cref="ObservedGameChecks"/> makes a trap arm
    /// only for a pickup this client WATCHED happen, and the recorded floor
    /// makes it spring only while the player is still standing there.</para>
    /// </summary>
    internal sealed class ForcedTrapQueue
    {
        public readonly List<PendingTrap> Pending = [];
        public bool WriteInFlight;

        /// <summary>
        /// Locations already collected in the GAME the first time this client
        /// looked at the save. Null until that first look. Everything in here
        /// is history: reporting it to the server is bookkeeping, not a
        /// pickup, and history does not spring traps.
        /// </summary>
        public HashSet<long>? ObservedGameChecks;

        /// <summary>
        /// The floor the player was standing on at the last pump, which is one
        /// poll ago. A pickup reported on a DIFFERENT floor than that is a
        /// pickup we only heard about after the player had already left it -
        /// the 100 ms race between the collect hook writing the journal bit
        /// and this client reading it. Rare, and rare is exactly what this
        /// whole rule is about.
        /// </summary>
        public int FloorAtLastPoll;

        /// <summary>
        /// Forgets the attached game: a queued trap belongs to one floor of
        /// one trip, and the save in front of us after a reattach or a
        /// checkpoint restore is not that one. Called wherever the location
        /// bookkeeping is reset for the same reason.
        /// </summary>
        public void ForgetGameSession()
        {
            Pending.Clear();
            WriteInFlight = false;
            ObservedGameChecks = null;
            FloorAtLastPoll = 0;
        }
    }

    private static readonly IReadOnlyDictionary<byte, string> TrapNameByGameId =
        new Dictionary<byte, string>
        {
            [1] = "Reversal Trap",
            [2] = "Slow Trap",
            [3] = "Warp Trap",
            [5] = "Chaos Trap",
            [7] = "Bomb Trap",
            [8] = "Slam Trap",
            [9] = "Sleep Trap",
            [10] = "Blinder Trap",
            [11] = "Poison Trap",
            [12] = "Prison Trap",
            [13] = "Frog Trap",
            [14] = "Bump Trap",
            [17] = "Seal Trap",
            [18] = "Rust Trap",
            [19] = "Monster Den Trap",
        };

    /// <summary>
    /// Whether a trap-holding location's first report to the server is a
    /// PICKUP this client watched happen, on a floor it can still spring on.
    ///
    /// <para>Four ways it is not, and every one of them ends with the trap
    /// going off on the first frame of a floor - the frame that is Koh's turn,
    /// so springing there spends it and hands the floor's monsters the first
    /// move against a player who has not seen the room yet.</para>
    /// </summary>
    /// <param name="armingPrimed">
    /// False on the first look at an attached save: everything in it is
    /// history until we have seen it once.
    /// </param>
    /// <param name="alreadyInSave">
    /// The location was already collected when this client first looked -
    /// picked up offline, or before this session. Reporting it is bookkeeping.
    /// </param>
    /// <param name="pickupFloor">Where the player is standing now, 0 for anywhere but a tower floor.</param>
    /// <param name="floorAtLastPoll">Where they were a poll ago.</param>
    internal static bool IsTrapSpringable(
        bool armingPrimed,
        bool alreadyInSave,
        int pickupFloor,
        int floorAtLastPoll,
        out string refusal)
    {
        if (!armingPrimed || alreadyInSave)
        {
            refusal = "it was already collected in this save; reporting it, not springing it";
            return false;
        }
        if (pickupFloor == 0)
        {
            refusal = "it was reported from outside the tower, so there is no floor to spring it on";
            return false;
        }
        if (pickupFloor != floorAtLastPoll)
        {
            refusal =
                $"the player reached floor {pickupFloor} between polls, so the pickup " +
                "cannot be placed on the floor it happened on";
            return false;
        }

        refusal = string.Empty;
        return true;
    }

    /// <summary>
    /// Drives the forced-trap request byte from the pending queue. Written
    /// any time the byte is clear (the seed-page stub itself defers until
    /// Koh is idle on an ordinary tower floor); observed-consumed pops the
    /// head and announces the trap by its real name - the one moment the
    /// disguise is allowed to drop, because it just went off.
    ///
    /// <para>A pending trap that has left its floor is DROPPED, not carried:
    /// see <see cref="ForcedTrapQueue"/>. Springing late is worse than not
    /// springing, because late means "on the first frame of a floor", which
    /// is the frame that costs the player their turn.</para>
    /// </summary>
    internal static void PumpForcedTraps(
        Adap.Client.Emulators.IEmulatorMemory memory,
        ForcedTrapQueue traps)
    {
        // Read every pump, queue or no queue: arming needs to know the player
        // was ALREADY standing here one poll ago, which is what tells a pickup
        // apart from a journal bit noticed just after a floor change.
        int liveFloor = AzureDreamsTowerProgressReader.ReadLiveTowerFloor(memory);
        traps.FloorAtLastPoll = liveFloor;

        if (traps.Pending.Count == 0 && !traps.WriteInFlight)
            return;

        Span<byte> current = stackalloc byte[1];
        if (!memory.TryRead(ForcedTrapRequestAddress, current, out _))
            return;

        if (current[0] == 0 && traps.WriteInFlight && traps.Pending.Count > 0)
        {
            // The write this client made has been consumed: the trap fired.
            PendingTrap sprung = traps.Pending[0];
            traps.Pending.RemoveAt(0);
            traps.WriteInFlight = false;
            Console.WriteLine($"That was no keycard - a {TrapName(sprung.TrapId)} sprang!");
        }

        // Anything whose moment has passed goes now, before a fresh write can
        // hand it to a floor it was never picked up on.
        DropStrandedTraps(memory, traps, liveFloor, current[0]);

        if (traps.Pending.Count == 0 || traps.WriteInFlight)
            return;
        if (current[0] != 0)
        {
            // Someone else's byte (a manual poke); the stub owns it from here.
            return;
        }

        Span<byte> request = [traps.Pending[0].TrapId];
        if (memory.TryWrite(ForcedTrapRequestAddress, request, out _))
            traps.WriteInFlight = true;
    }

    /// <summary>
    /// Drops every pending trap that is no longer on the floor it was picked
    /// up on - the player took the elevator, walked back to town, died, or
    /// loaded a save. If one of them owned the request byte, the byte is
    /// cleared so the stub cannot spring it on the way out.
    /// </summary>
    private static void DropStrandedTraps(
        Adap.Client.Emulators.IEmulatorMemory memory,
        ForcedTrapQueue traps,
        int liveFloor,
        byte requestByte)
    {
        for (int index = traps.Pending.Count - 1; index >= 0; index--)
        {
            if (traps.Pending[index].Floor == liveFloor && liveFloor != 0)
                continue;

            PendingTrap stranded = traps.Pending[index];
            traps.Pending.RemoveAt(index);
            // Only the head is ever written, so only the head can be holding
            // the byte. Clearing it is safe either way: if the stub has
            // already taken it the byte reads 0 and this writes 0 again.
            if (index == 0 && traps.WriteInFlight)
            {
                traps.WriteInFlight = false;
                if (requestByte == stranded.TrapId)
                {
                    Span<byte> clear = [0];
                    memory.TryWrite(ForcedTrapRequestAddress, clear, out _);
                }
            }
            Console.WriteLine(
                $"A {TrapName(stranded.TrapId)} picked up on floor {stranded.Floor} " +
                "was not sprung there; it is discarded rather than carried " +
                "onto another floor.");
        }
    }

    private static string TrapName(byte gameTrapId) =>
        TrapNameByGameId.TryGetValue(gameTrapId, out string? known)
            ? known
            : $"trap {gameTrapId}";

    /// <summary>
    /// The slot-data map of this world's own tower locations that hold its
    /// trap items: location id to GAME trap id. Absent or empty means the
    /// yaml generated no traps.
    /// </summary>
    private static Dictionary<long, byte> ReadTrapLocations(LoginSuccessful login)
    {
        var map = new Dictionary<long, byte>();
        if (!login.SlotData.TryGetValue("trap_locations", out object? raw) || raw is null)
            return map;
        if (raw is not System.Collections.IDictionary entries)
        {
            // Newtonsoft JObject implements IDictionary; anything else is a
            // malformed room and the traps simply stay dormant.
            if (raw is Newtonsoft.Json.Linq.JObject jObject)
            {
                foreach (var property in jObject.Properties())
                {
                    if (long.TryParse(property.Name, out long locationId) &&
                        byte.TryParse(property.Value.ToString(), out byte trapId))
                    {
                        map[locationId] = trapId;
                    }
                }
            }
            return map;
        }
        foreach (System.Collections.DictionaryEntry entry in entries)
        {
            if (long.TryParse(entry.Key?.ToString(), out long locationId) &&
                byte.TryParse(entry.Value?.ToString(), out byte trapId))
            {
                map[locationId] = trapId;
            }
        }
        return map;
    }

    /// <summary>
    /// The gold twin of <see cref="SynchronizeProgressiveKeycards"/>: count
    /// the packages in the history, compare against the durable granted
    /// counter, bank the difference. Gold is granted BEFORE the counter is
    /// written, so a crash between the two re-offers one grant rather than
    /// losing one - the same failure direction every delivery path here
    /// chooses.
    /// </summary>
    internal static bool SynchronizeGoldPackages(
        IArchipelagoSession session,
        Adap.Client.Emulators.IEmulatorMemory memory,
        out string message)
    {
        uint inHistory = 0;
        foreach (ItemInfo item in session.Items.AllItemsReceived)
        {
            if (item.ItemId == GoldPackageItemId)
                inHistory++;
        }

        if (!AzureDreamsReceiveState.TryReadGoldGrantedCount(
                memory, out uint granted, out message))
        {
            return false;
        }
        if (granted >= inHistory)
        {
            // Equal is settled. Greater means the journal outran this
            // session's history view (a reconnect mid-download); the
            // history catches up on its own and a grant now would double.
            message = string.Empty;
            return true;
        }

        uint owed = inHistory - granted;
        if (!AzureDreamsReceiveState.TryGrantGold(
                memory, owed * GoldPackageAmount, out uint total, out message))
        {
            return false;
        }
        if (!AzureDreamsReceiveState.TryWriteGoldGrantedCount(
                memory, inHistory, out message))
        {
            return false;
        }

        Console.WriteLine(
            owed == 1
                ? $"Received 5000 Gold; gold is now {total}."
                : $"Received {owed} x 5000 Gold; gold is now {total}.");
        message = string.Empty;
        return true;
    }

    /// <summary>
    /// The send-token twin of <see cref="SynchronizeGoldPackages"/>: count
    /// the tokens in the history, compare against the durable banked
    /// counter, add the difference to the game's counter. Tokens are added
    /// BEFORE the counter is written, so a crash between the two re-offers
    /// a grant rather than losing one - the failure direction every
    /// delivery path here chooses.
    /// </summary>
    internal static bool SynchronizeSendTokens(
        IArchipelagoSession session,
        Adap.Client.Emulators.IEmulatorMemory memory,
        out string message)
    {
        uint inHistory = 0;
        foreach (ItemInfo item in session.Items.AllItemsReceived)
        {
            if (item.ItemId == SendTokenItemId)
                inHistory++;
        }

        if (!AzureDreamsReceiveState.TryReadSendTokensBankedCount(
                memory, out uint banked, out message))
        {
            return false;
        }
        if (banked >= inHistory)
        {
            // Equal is settled. Greater means the journal outran this
            // session's history view (a reconnect mid-download); the
            // history catches up on its own and a grant now would double.
            message = string.Empty;
            return true;
        }

        uint owed = inHistory - banked;
        if (!AzureDreamsReceiveState.TryGrantSendTokens(
                memory, owed, out uint total, out message))
        {
            return false;
        }
        if (!AzureDreamsReceiveState.TryWriteSendTokensBankedCount(
                memory, inHistory, out message))
        {
            return false;
        }

        Console.WriteLine(
            owed == 1
                ? $"Received a Send Token; you now have {total}."
                : $"Received {owed} Send Tokens; you now have {total}.");
        message = string.Empty;
        return true;
    }

    public static async Task<int> RunAsync(
        string endpoint,
        string slotName,
        string? password,
        CancellationToken cancellationToken,
        Action<ClientTransfer>? transferSink = null,
        Action<AzureDreamsTowerProgress>? towerSink = null,
        Action<IReadOnlyList<AzureDreamsIncomingItem>>? incomingSink = null)
    {
        if (!TryParseEndpoint(endpoint, out string host, out int port, out string endpointError))
        {
            Console.Error.WriteLine(endpointError);
            return 1;
        }

        DuckStationCandidate? selected = TryFindAzureDreams(out string gameConnectionError);
        if (selected?.Memory is null)
        {
            selected?.Dispose();
            selected = null;
            Console.WriteLine($"Game connection waiting: {gameConnectionError}");
        }
        try
        {
            if (selected is not null)
                Console.WriteLine($"Connected to DuckStation PID {selected.ProcessId}; Azure Dreams (USA) is loaded.");

            IArchipelagoSession session = ArchipelagoSessionFactory.CreateSession(host, port);
            try
            {
                Console.WriteLine($"Connecting to Archipelago at {host}:{port} as {slotName}...");
                await session.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(15), cancellationToken);
                LoginResult loginResult = await session.LoginAsync(
                    GameName,
                    slotName,
                    ItemsHandlingFlags.AllItems,
                    new Version(0, 6, 3),
                    password: password,
                    requestSlotData: true);

                if (loginResult is not LoginSuccessful login)
                {
                    LoginFailure failure = (LoginFailure)loginResult;
                    Console.Error.WriteLine("Archipelago login failed: " + string.Join("; ", failure.Errors));
                    return 3;
                }

                if (!TryValidateSlotDataVersion(login, out string slotVersionError))
                {
                    Console.Error.WriteLine(slotVersionError);
                    return 3;
                }
                if (!TryReadSlotSeedIdentity(
                        login,
                        out AzureDreamsSeedIdentity expectedSeedIdentity,
                        out string seedIdentityError))
                {
                    Console.Error.WriteLine(seedIdentityError);
                    return 3;
                }
                Dictionary<long, byte> trapLocations = ReadTrapLocations(login);
                var forcedTraps = new ForcedTrapQueue();
                int localSlot = session.ConnectionInfo.Slot;
                string localPlayerName = session.Players.GetPlayerAlias(localSlot) ?? slotName;
                // Room configuration, read once. Absent reads as ON, which is
                // both the default and the only value a room generated before
                // the option existed could have had.
                bool roomHasCarrierChecks = ReadSlotDataFlag(login, "carrier_system", true);
                if (!roomHasCarrierChecks)
                {
                    Console.WriteLine(
                        "This room has monster-carried checks switched off: two checks a " +
                        "tower floor, and no carrier spawns.");
                }
                // Every send in the room is registered, not only the ones
                // this slot is part of: the server broadcasts an ItemSend
                // for each transfer, and that single stream covers local
                // finds, remote-to-remote trades, and everything the old
                // scout-based reporting reconstructed locally. Hints share
                // the message shape but move nothing, so they are skipped.
                session.MessageLog.OnMessageReceived += logMessage =>
                {
                    if (logMessage is not ItemSendLogMessage itemSend ||
                        itemSend is HintItemSendLogMessage)
                        return;
                    string senderName = string.IsNullOrEmpty(itemSend.Sender.Alias)
                        ? $"Player {itemSend.Sender.Slot}"
                        : itemSend.Sender.Alias;
                    string receiverName = string.IsNullOrEmpty(itemSend.Receiver.Alias)
                        ? $"Player {itemSend.Receiver.Slot}"
                        : itemSend.Receiver.Alias;
                    // The room-wide feed names every transfer, including the
                    // ones this player is deliberately not being told about:
                    // an unidentified item's own game shows it unappraised, so
                    // the client must not print the enchantment beside it.
                    string sentItemName = AzureDreamsItemManifest.DisplayNameFor(
                        itemSend.Item.ItemId, itemSend.Item.ItemDisplayName);
                    Console.WriteLine(itemSend.Sender.Slot == itemSend.Receiver.Slot
                        ? $"Send: {senderName} found {sentItemName}."
                        : $"Send: {senderName} sent {sentItemName} to {receiverName}.");
                    transferSink?.Invoke(new ClientTransfer(
                        ClientTransferKind.Sent,
                        sentItemName,
                        senderName,
                        receiverName,
                        itemSend.Sender.Slot == localSlot,
                        itemSend.Receiver.Slot == localSlot));
                };
                Console.WriteLine("Archipelago login succeeded. Press Ctrl+C to disconnect.");
                bool gameSynchronizationAnnounced = false;
                HashSet<long> submittedChecks = [];
                // Queue arrivals already announced, by absolute received-item
                // index, so a pending item is named once even though the queue
                // panel itself only draws icons.
                HashSet<long> announcedQueuedItems = [];
                var presentationTracker = new ReceivePresentationTracker();
                uint? reportedReceiveCursor = null;
                var checkpointCoordinator = new AzureDreamsTownCheckpointCoordinator();
                // What escapes the world must not roll back, and what enters
                // it must be offered again after one. Sends are handled where
                // they are observed (NoteItemSent, below); gifts are handled
                // here, because "delivered" lives on the Archipelago server
                // rather than in the block a restore rewrites.
                checkpointCoordinator.GiftWatermarkProvider =
                    AzureDreamsGiftService.CaptureConsumedWatermark;
                checkpointCoordinator.GiftWatermarkRestorer = watermark =>
                    AzureDreamsGiftService.RewindConsumedWatermark(session, localSlot, watermark);
                bool gameIdentityWasAvailable = false;
                byte[]? lastSeedSignature = null;
                bool restoreWaitingAnnounced = false;
                string? lastGameStateMessage = null;
                string? lastLocationError = null;
                string? lastReceiveError = null;
                string? lastCheckpointError = null;
                string? lastIntroRestoreError = null;
                string? lastContinueFlagError = null;
                string? lastCursorRepairError = null;
                // Sticky for the game session: the boot intro can only run
                // once per console start, but the town core's mailbox that
                // records it is reloaded from disc on every tower return.
                bool introWindowClosed = false;
                // Announced at most once per connection; the server treats a
                // repeated goal status as a no-op, so a reconnect re-sending
                // it is harmless.
                bool goalAnnounced = false;
                AzureDreamsTownStabilityGuard? lastStabilityGuards = null;
                bool? lastObservedIsTown = null;
                while (!cancellationToken.IsCancellationRequested && session.Socket.Connected)
                {
                    if (selected is not null &&
                        !IsProcessRunning(selected.ProcessId, selected.ProcessName))
                    {
                        int disconnectedProcessId = selected.ProcessId;
                        selected.Dispose();
                        selected = null;
                        gameSynchronizationAnnounced = false;
                        submittedChecks.Clear();
                        // The request byte died with the emulator, and so did
                        // the trip every queued trap belonged to. A trap that
                        // did not spring where it was picked up is gone - the
                        // alternative is springing it on the first frame of
                        // whatever floor the player loads next.
                        forcedTraps.ForgetGameSession();
                        reportedReceiveCursor = null;
                        checkpointCoordinator.ResetGameObservation();
                        AzureDreamsReceiveState.ResetCursorObservation();
                        gameIdentityWasAvailable = false;
                        // A fresh DuckStation may legitimately boot a different
                        // seed; only a change WITHIN one attachment is an alarm.
                        lastSeedSignature = null;
                        restoreWaitingAnnounced = false;
                        introWindowClosed = false;
                        lastGameStateMessage = null;
                        lastLocationError = null;
                        lastReceiveError = null;
                        lastCheckpointError = null;
                        lastIntroRestoreError = null;
                        lastContinueFlagError = null;
                        lastCursorRepairError = null;
                        Console.WriteLine(
                            $"DuckStation PID {disconnectedProcessId} closed; " +
                            "waiting for the patched game to restart.");
                    }

                    if (selected is null)
                    {
                        selected = TryFindAzureDreams(out string reconnectMessage);
                        if (selected?.Memory is null)
                        {
                            selected?.Dispose();
                            selected = null;
                            if (reconnectMessage != lastGameStateMessage)
                                Console.WriteLine($"Game reconnection waiting: {reconnectMessage}");
                            lastGameStateMessage = reconnectMessage;
                            await Task.Delay(250, cancellationToken);
                            continue;
                        }

                        Console.WriteLine(
                            $"Reconnected to DuckStation PID {selected.ProcessId}; " +
                            "saved checks and received items will be reconciled.");
                        lastGameStateMessage = null;
                    }

                    DuckStationMemory activeMemory = selected.Memory!;

                    // Before anything that needs the game to be in a known
                    // state: the title screen asks whether to label its first
                    // row CONTINUE, and it asks long before the town core
                    // exists. The seed identity here comes from slot data, not
                    // from the game, so this works from the moment DuckStation
                    // is attached.
                    if (!AzureDreamsTitleContinueFlag.TryPublish(
                            activeMemory,
                            AzureDreamsTitleContinueFlag.CheckpointExists(
                                expectedSeedIdentity),
                            out string? continueFlagError))
                    {
                        if (continueFlagError != lastContinueFlagError)
                            Console.Error.WriteLine(
                                $"Title Continue label paused: {continueFlagError}");
                        lastContinueFlagError = continueFlagError;
                    }
                    else
                    {
                        lastContinueFlagError = null;
                    }

                    if (!AzureDreamsIntroRestore.TrySynchronize(
                            activeMemory,
                            expectedSeedIdentity,
                            ref introWindowClosed,
                            out AzureDreamsIntroRestoreResult introRestore,
                            out string introRestoreMessage,
                            serverCheckedLocations: session.Locations.AllLocationsChecked))
                    {
                        if (introRestoreMessage != lastIntroRestoreError)
                        {
                            Console.Error.WriteLine(
                                $"Intro checkpoint synchronization paused: {introRestoreMessage}");
                        }
                        lastIntroRestoreError = introRestoreMessage;
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    lastIntroRestoreError = null;
                    // Only accept when the intro path actually produced or
                    // consumed a checkpoint. CheckpointLifecycleComplete stays
                    // true for the rest of the session, and accepting on every
                    // poll cleared _towerObserved, _shopPurchasePending and
                    // _townReceivePending faster than they could ever commit,
                    // and forced _snapshotExists true so the very first town
                    // checkpoint was never taken either.
                    if (introRestore.Event ==
                        AzureDreamsIntroRestoreEvent.FirstRunReleased)
                    {
                        Console.WriteLine(
                            "First-run intro complete; the initial checkpoint will be captured at stable town.");
                    }
                    else if (introRestore.Event ==
                        AzureDreamsIntroRestoreEvent.ReturningNameStaged)
                    {
                        Console.WriteLine(
                            "Saved player name staged; press X on the angel's welcome-back message to restore.");
                    }
                    else if (introRestore.Event ==
                            AzureDreamsIntroRestoreEvent.CheckpointRestored &&
                        introRestore.Checkpoint is AzureDreamsCheckpointMetadata introRestored)
                    {
                        checkpointCoordinator.AcceptIntroCheckpoint(expectedSeedIdentity);
                        reportedReceiveCursor = introRestored.ReceiveCursor;
                        submittedChecks.Clear();
                        forcedTraps.ForgetGameSession();
                        Console.WriteLine(
                            $"Restored town checkpoint from {FormatCheckpointReason(introRestored.Reason)} " +
                            $"at the angel confirmation (receive cursor {introRestored.ReceiveCursor}, " +
                            $"shop mask 0x{introRestored.ShopMask:x8}" +
                            (introRestore.MergedChecks > 0
                                ? $", {introRestore.MergedChecks} server-confirmed check{(introRestore.MergedChecks == 1 ? string.Empty : "s")} merged into the restored save"
                                : string.Empty) +
                            ").");
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    else if (introRestore.Event ==
                            AzureDreamsIntroRestoreEvent.InitialCheckpointCaptured &&
                        introRestore.Checkpoint is AzureDreamsCheckpointMetadata introCaptured)
                    {
                        checkpointCoordinator.AcceptIntroCheckpoint(expectedSeedIdentity);
                        AnnounceCheckpoint(introCaptured);
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    if (introRestore.BlocksNormalSynchronization)
                    {
                        // Intro requests are published between emulated frames.
                        // Poll at frame cadence so restore/capture completes
                        // while the cutscene still owns player input.
                        await Task.Delay(16, cancellationToken);
                        continue;
                    }

                    if (!AzureDreamsReceiveState.TryReadSynchronizationIdentity(
                            activeMemory,
                            out AzureDreamsSeedIdentity seedIdentity,
                            out string identityMessage))
                    {
                        if (gameIdentityWasAvailable)
                        {
                            checkpointCoordinator.ResetGameObservation();
                            AzureDreamsReceiveState.ResetCursorObservation();
                            submittedChecks.Clear();
                            forcedTraps.ForgetGameSession();
                            reportedReceiveCursor = null;
                            gameSynchronizationAnnounced = false;
                            gameIdentityWasAvailable = false;
                            restoreWaitingAnnounced = false;
                        }
                        if (identityMessage != lastGameStateMessage)
                            Console.WriteLine($"Game synchronization waiting: {identityMessage}");
                        lastGameStateMessage = identityMessage;
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }

                    gameIdentityWasAvailable = true;
                    lastGameStateMessage = null;

                    // The seed signature naming this save changes mid-session
                    // only when the game-side state initializer re-created the
                    // ADSV journal as a DIFFERENT seed's save - which resets
                    // the receive cursor, keycard level and location masks.
                    // That is a disc swap at best and memory corruption at
                    // worst (the 2026-08-05 send-menu overflow clobbered the
                    // slab's signature copy and factory-reset a live session,
                    // silently). Either way the player must hear about it the
                    // moment it happens, not after items re-deliver.
                    if (lastSeedSignature is not null &&
                        !lastSeedSignature.AsSpan().SequenceEqual(
                            seedIdentity.Signature))
                    {
                        Console.Error.WriteLine(
                            "THE SAVE'S SEED SIGNATURE CHANGED MID-SESSION: " +
                            $"{Convert.ToHexString(lastSeedSignature)} -> " +
                            $"{Convert.ToHexString(seedIdentity.Signature)}. " +
                            "The game re-initialized its multiworld journal as a " +
                            "different seed's save, so the receive cursor, keycard " +
                            "level and location masks were reset. If you did not " +
                            "change discs, this is memory corruption - capture a " +
                            "save state now and report it.");
                    }
                    lastSeedSignature = seedIdentity.Signature.ToArray();

                    if (!AzureDreamsTownCheckpoint.TryObserve(
                            activeMemory,
                            out AzureDreamsTownObservation startupObservation,
                            out string checkpointMessage) ||
                        !AzureDreamsReceiveState.TryReadSaveIsPristine(
                            activeMemory,
                            out bool saveIsPristine,
                            out checkpointMessage) ||
                        !checkpointCoordinator.TryRestoreAtStartup(
                            activeMemory,
                            seedIdentity,
                            startupObservation,
                            saveIsPristine,
                            session.Locations.AllLocationsChecked,
                            out bool restorePending,
                            out AzureDreamsCheckpointMetadata? restoredCheckpoint,
                            out int restoreMergedChecks,
                            out bool staleRestoreDropped,
                            out checkpointMessage))
                    {
                        if (checkpointMessage != lastCheckpointError)
                            Console.Error.WriteLine($"Town restore paused: {checkpointMessage}");
                        lastCheckpointError = checkpointMessage;
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    if (staleRestoreDropped)
                    {
                        Console.WriteLine(
                            "Startup checkpoint restore skipped: the running game already " +
                            "carries live progress, so this was a reconnection, not a game " +
                            "load. Restores only follow the main-menu continue path.");
                    }
                    if (restorePending)
                    {
                        if (!restoreWaitingAnnounced)
                        {
                            Console.WriteLine(
                                "Game synchronization waiting: " +
                                "the saved town checkpoint will restore at the first stable town frame.");
                            restoreWaitingAnnounced = true;
                        }
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    if (restoredCheckpoint is AzureDreamsCheckpointMetadata restored)
                    {
                        restoreWaitingAnnounced = false;
                        reportedReceiveCursor = restored.ReceiveCursor;
                        submittedChecks.Clear();
                        forcedTraps.ForgetGameSession();
                        Console.WriteLine(
                            $"Restored town checkpoint from {FormatCheckpointReason(restored.Reason)} " +
                            $"(receive cursor {restored.ReceiveCursor}, " +
                            $"shop mask 0x{restored.ShopMask:x8}" +
                            (restoreMergedChecks > 0
                                ? $", {restoreMergedChecks} server-confirmed check{(restoreMergedChecks == 1 ? string.Empty : "s")} merged into the restored save"
                                : string.Empty) +
                            ").");
                        lastCheckpointError = null;
                        lastGameStateMessage = null;
                        // Allow the town game thread to observe the complete
                        // one-write restore before any AP reconciliation.
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    restoreWaitingAnnounced = false;
                    if (!gameSynchronizationAnnounced)
                    {
                        Console.WriteLine("Game synchronization active.");
                        gameSynchronizationAnnounced = true;
                    }

                    // Before the checkpoint machinery, so a gift-corrupted
                    // cursor is repaired rather than captured into a snapshot.
                    if (!TryRepairGiftCorruptedReceiveCursor(
                            activeMemory,
                            session.Items.AllItemsReceived.Count,
                            reportedReceiveCursor,
                            out bool cursorRepaired,
                            out string cursorRepairMessage))
                    {
                        if (cursorRepairMessage != lastCursorRepairError)
                            Console.Error.WriteLine($"Receive cursor check paused: {cursorRepairMessage}");
                        lastCursorRepairError = cursorRepairMessage;
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    if (cursorRepaired)
                    {
                        // reportedReceiveCursor already equals the restored
                        // value; the reporter continues without a rebase.
                        Console.WriteLine(cursorRepairMessage);
                        lastCursorRepairError = null;
                    }
                    else if (cursorRepairMessage.Length > 0)
                    {
                        if (cursorRepairMessage != lastCursorRepairError)
                            Console.Error.WriteLine(cursorRepairMessage);
                        lastCursorRepairError = cursorRepairMessage;
                    }
                    else
                    {
                        lastCursorRepairError = null;
                    }

                    if (!checkpointCoordinator.TryObserveAndCommitBoundary(
                            activeMemory,
                            seedIdentity,
                            out AzureDreamsTownObservation observation,
                            out AzureDreamsCheckpointMetadata? boundaryCheckpoint,
                            out checkpointMessage))
                    {
                        if (checkpointMessage != lastCheckpointError)
                            Console.Error.WriteLine($"Town checkpoint paused: {checkpointMessage}");
                        lastCheckpointError = checkpointMessage;
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    AnnounceCheckpoint(boundaryCheckpoint);

                    // A town checkpoint can only be taken on a settled frame.
                    // Report which guard is holding it up so a checkpoint that
                    // never arrives is diagnosable from the console alone.
                    //
                    // Reporting every *change* was not enough. CdQueueBusy
                    // toggles as the CD queue drains and refills, so the guard
                    // set oscillates between ModalRoot, CdQueueBusy and both,
                    // and each flip counted as news - several lines a second for
                    // as long as the wait lasted.
                    //
                    // Instead, accumulate the guards already reported for this
                    // wait and speak up only when one appears that has not been
                    // named yet. An oscillating set converges to a single line;
                    // a genuinely new blocker still gets reported.
                    if (observation.IsTown &&
                        !observation.IsStableTown &&
                        checkpointCoordinator.CapturePending)
                    {
                        AzureDreamsTownStabilityGuard reported =
                            lastStabilityGuards ?? AzureDreamsTownStabilityGuard.None;
                        if ((observation.BlockingGuards & ~reported) !=
                            AzureDreamsTownStabilityGuard.None)
                        {
                            Console.WriteLine(
                                "Town checkpoint waiting for a settled frame: " +
                                $"{observation.BlockingGuards}.");
                            lastStabilityGuards = reported | observation.BlockingGuards;
                        }
                    }
                    else if (lastStabilityGuards is not null &&
                        lastStabilityGuards != AzureDreamsTownStabilityGuard.None)
                    {
                        Console.WriteLine("Town frame settled.");
                        lastStabilityGuards = AzureDreamsTownStabilityGuard.None;
                    }

                    if (!checkpointCoordinator.TryCommitPending(
                            activeMemory,
                            seedIdentity,
                            out AzureDreamsCheckpointMetadata? retriedCheckpoint,
                            out checkpointMessage))
                    {
                        if (checkpointMessage != lastCheckpointError)
                            Console.Error.WriteLine($"Town checkpoint paused: {checkpointMessage}");
                        lastCheckpointError = checkpointMessage;
                        await Task.Delay(100, cancellationToken);
                        continue;
                    }
                    AnnounceCheckpoint(retriedCheckpoint);
                    lastCheckpointError = null;

                    bool locationSynchronized = SynchronizeLocationChecks(
                            activeMemory,
                            session,
                            submittedChecks,
                            trapLocations,
                            forcedTraps,
                            out bool shopPurchaseObserved,
                            out string locationMessage);
                    if (shopPurchaseObserved)
                        checkpointCoordinator.RequestShopPurchaseCheckpoint();
                    if (!locationSynchronized)
                    {
                        if (locationMessage != lastLocationError)
                            Console.Error.WriteLine($"Location synchronization paused: {locationMessage}");
                        lastLocationError = locationMessage;
                    }
                    else
                    {
                        lastLocationError = null;
                    }

                    if (AzureDreamsTowerProgressReader.TryRead(
                            activeMemory,
                            out AzureDreamsTowerProgress towerProgress,
                            out _,
                            roomHasCarrierChecks))
                    {
                        towerSink?.Invoke(towerProgress);

                        // Goal recognition: the save-backed floor halfword at
                        // 0x80010234 reads 0 in town and the live floor in the
                        // tower (HasCurrentFloor filters the warp helper's
                        // transient marked values). Standing on floor 40 IS
                        // the goal; announce it so the server can release
                        // this slot's remaining items.
                        if (!goalAnnounced &&
                            towerProgress.HasCurrentFloor &&
                            towerProgress.CurrentFloor >=
                                AzureDreamsTowerProgress.TopFloor)
                        {
                            try
                            {
                                session.SetGoalAchieved();
                                goalAnnounced = true;
                                Console.WriteLine(
                                    $"Floor {AzureDreamsTowerProgress.TopFloor} reached - " +
                                    "goal complete! The server has been told; remaining " +
                                    "items in this world will be released.");
                            }
                            catch (Exception exception)
                            {
                                // Retry next poll; the floor value is durable.
                                Console.Error.WriteLine(
                                    $"Goal announcement failed: {exception.Message}");
                            }
                        }
                    }

                    // A delivery the game acknowledged between polls may not
                    // be recorded anywhere but the mailbox ack itself (the
                    // tower dispatcher historically never committed the
                    // durable cursor). Fold that ack into its owner BEFORE
                    // anything can stage a new request over it and destroy
                    // the only evidence the delivery happened - the exact
                    // mechanism behind the duplicated Nada receives.
                    FoldUnrecordedDeliveries(
                        activeMemory, session, localSlot, localPlayerName);

                    // Keycards cut in line: they set a durable clearance
                    // level, not inventory, so nothing about the queue's
                    // pacing applies to them. Apply every keycard in the
                    // received history immediately, independent of the
                    // sequential receive cursor, so one waiting behind a
                    // blocked inventory item never stalls.
                    if (!SynchronizeProgressiveKeycards(
                            session, activeMemory, out string keycardMessage))
                    {
                        if (keycardMessage != lastReceiveError)
                            Console.Error.WriteLine($"Keycard sync paused: {keycardMessage}");
                        lastReceiveError = keycardMessage;
                    }

                    // Gold cuts the line for the same reason keycards do: the
                    // delivery queue paces inventory space, and gold uses
                    // none. Banked here, eagerly, against the durable
                    // gold-granted counter.
                    if (!SynchronizeGoldPackages(
                            session, activeMemory, out string goldMessage))
                    {
                        if (goldMessage != lastReceiveError)
                            Console.Error.WriteLine($"Gold sync paused: {goldMessage}");
                        lastReceiveError = goldMessage;
                    }

                    // Send tokens, same rule again: a token is a counter,
                    // not an inventory item, so it never queues.
                    if (!SynchronizeSendTokens(
                            session, activeMemory, out string tokenMessage))
                    {
                        if (tokenMessage != lastReceiveError)
                            Console.Error.WriteLine($"Send token sync paused: {tokenMessage}");
                        lastReceiveError = tokenMessage;
                    }

                    // The blacksmith's sands: levels, not inventory. Same
                    // rule as keycards - the full history's count, eagerly.
                    if (!SynchronizeTemperSands(
                            session, activeMemory, out string sandMessage))
                    {
                        if (sandMessage != lastReceiveError)
                            Console.Error.WriteLine($"Temper sand sync paused: {sandMessage}");
                        lastReceiveError = sandMessage;
                    }

                    // Traps queued by the location sync above: keep the
                    // request byte fed. The seed-page stub does the actual
                    // planting and springing at Koh's next idle tower frame.
                    PumpForcedTraps(activeMemory, forcedTraps);

                    // Mode-transition cursor trace. Always on, and silent
                    // except on an actual town<->tower crossing.
                    //
                    // This is the measurement the 2026-08-01 duplication
                    // needs and did not have. Both mechanisms that advance
                    // the durable cursor - the game's own commit inside
                    // Nada's delivery, and the client's fold - write the SAME
                    // word at 0x80015FDC. If that word does not survive the
                    // crossing, neither one can help, and the symptom is
                    // exactly what was reported: everything Nada handed over
                    // arrives again in the tower. Printing the cursor on each
                    // side of the crossing separates "nothing wrote it" from
                    // "something reverted it", which no amount of reading the
                    // delivery code can.
                    if (lastObservedIsTown != observation.IsTown)
                    {
                        string where = observation.IsTown ? "town" : "tower";
                        string cursorText =
                            AzureDreamsReceiveState.TryReadReceivedItemCount(
                                activeMemory, out uint crossingCursor, out _)
                                ? crossingCursor.ToString()
                                : "unreadable";
                        Console.WriteLine(
                            $"Entered {where}: durable receive cursor = {cursorText}, " +
                            $"server history = {session.Items.AllItemsReceived.Count}.");
                        lastObservedIsTown = observation.IsTown;
                    }

                    bool receivesProcessed = ProcessReceivedItems(
                            activeMemory,
                            session,
                            localSlot,
                            localPlayerName,
                            transferSink,
                            presentationTracker,
                            observation.IsTown,
                            ref reportedReceiveCursor,
                            out bool townReceiveAcknowledged,
                            out string receiveMessage);
                    if (townReceiveAcknowledged)
                        checkpointCoordinator.RequestTownReceiveCheckpoint();
                    if (!receivesProcessed)
                    {
                        if (receiveMessage != lastReceiveError)
                            Console.Error.WriteLine($"Receive queue paused: {receiveMessage}");
                        lastReceiveError = receiveMessage;
                    }
                    else
                    {
                        lastReceiveError = null;
                    }

                    // Player-to-player gifting over data storage, processed
                    // AFTER the ordinary queue: a new gift stages only once
                    // the queue is drained, so gifts join the tail of the
                    // incoming list instead of cutting ahead of items the
                    // player is already owed.
                    AzureDreamsGiftService.ProcessOutgoing(
                        session,
                        activeMemory,
                        localSlot,
                        localPlayerName,
                        // A sent item and its token have left this world for
                        // good, so they come off the saved checkpoint too - a
                        // restore that gave them back would be a duplication
                        // the recipient keeps their copy of.
                        descriptor => checkpointCoordinator.NoteItemSent(
                            seedIdentity, descriptor));
                    bool ordinaryQueueDrained =
                        AzureDreamsReceiveState.TryReadReceivedItemCount(
                            activeMemory, out uint drainedCursor, out _) &&
                        drainedCursor >= session.Items.AllItemsReceived.Count;
                    AzureDreamsGiftService.ProcessIncoming(
                        session,
                        activeMemory,
                        localSlot,
                        localPlayerName,
                        ordinaryQueueDrained,
                        out IReadOnlyList<AzureDreamsGiftService.IncomingGift> pendingGifts,
                        out bool giftDeliveredInTown);
                    // A gifted item is in inventory but not behind the
                    // receive cursor; only a fresh checkpoint protects it
                    // across a restore.
                    if (giftDeliveredInTown)
                        checkpointCoordinator.RequestTownReceiveCheckpoint();

                    // Everything past the save's durable cursor is still owed
                    // to the game. Delivery is one at a time, so the head of
                    // this list is the item currently going in.
                    if (incomingSink is not null &&
                        AzureDreamsReceiveState.TryReadReceivedItemCount(
                            activeMemory,
                            out uint deliveredCount,
                            out _))
                    {
                        ItemInfo[] allReceived = session.Items.AllItemsReceived.ToArray();
                        var pending = new List<AzureDreamsIncomingItem>();
                        for (long index = deliveredCount;
                            index < allReceived.Length &&
                                pending.Count < IncomingItemsPanel.SlotCount;
                            index++)
                        {
                            ItemInfo pendingItem = allReceived[index];
                            // Keycards, gold and traps apply immediately;
                            // they are not part of the delivery queue the
                            // panel represents.
                            if (IsLineCutterItemId(pendingItem.ItemId))
                                continue;
                            pending.Add(new AzureDreamsIncomingItem(
                                pendingItem.ItemId,
                                AzureDreamsItemManifest.DisplayNameFor(
                                    pendingItem.ItemId,
                                    pendingItem.ItemDisplayName),
                                pendingItem.Player.Alias));
                        }
                        incomingSink(BuildIncomingDisplayList(
                            pendingGifts, pending, IncomingItemsPanel.SlotCount));
                        // Name every new queue arrival: the panel draws only
                        // item icons, and a Roche Fruit is worth making room
                        // for where most fruit is not.
                        for (long index = deliveredCount;
                            index < allReceived.Length;
                            index++)
                        {
                            if (!announcedQueuedItems.Add(index))
                                continue;
                            ItemInfo queued = allReceived[index];
                            // Keycards, gold and traps cut the line, so
                            // they are never "waiting to deliver" - don't
                            // announce them here (a trap announcement would
                            // also break its disguise before the spring).
                            if (IsLineCutterItemId(queued.ItemId))
                                continue;
                            Console.WriteLine(
                                "Queued: " +
                                AzureDreamsItemManifest.DisplayNameFor(
                                    queued.ItemId, queued.ItemDisplayName) +
                                $" from {queued.Player.Alias} - waiting to deliver.");
                        }
                    }

                    if (!checkpointCoordinator.TryCommitPending(
                            activeMemory,
                            seedIdentity,
                            out AzureDreamsCheckpointMetadata? eventCheckpoint,
                            out checkpointMessage))
                    {
                        if (checkpointMessage != lastCheckpointError)
                            Console.Error.WriteLine($"Town checkpoint paused: {checkpointMessage}");
                        lastCheckpointError = checkpointMessage;
                    }
                    else
                    {
                        AnnounceCheckpoint(eventCheckpoint);
                        lastCheckpointError = null;
                    }

                    await Task.Delay(100, cancellationToken);
                }

                if (!session.Socket.Connected && !cancellationToken.IsCancellationRequested)
                {
                    Console.Error.WriteLine(
                        "The Archipelago connection closed. Game-side checks and receive progress remain saved; " +
                        "run the same command again to reconcile them.");
                    return 5;
                }

                return 0;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Archipelago synchronization failed: {ex.Message}");
                return 5;
            }
            finally
            {
                if (session.Socket.Connected)
                    await session.Socket.DisconnectAsync();
            }
        }
        finally
        {
            selected?.Dispose();
        }
    }

    internal static bool IsProcessRunning(int processId, string expectedProcessName)
    {
        try
        {
            using Process process = Process.GetProcessById(processId);
            return !process.HasExited &&
                process.ProcessName.Equals(expectedProcessName, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
        {
            return false;
        }
    }

    /// <summary>
    /// The client-to-game (DuckStation shared memory) attachment state,
    /// independent of the Archipelago room connection. The connection
    /// window polls this so the game-process/attach status and the room
    /// status are shown separately.
    /// </summary>
    public enum GameAttachment
    {
        NotRunning,
        Attached,
    }

    /// <summary>
    /// One-shot probe: is a DuckStation with Azure Dreams (USA) loaded and
    /// its shared memory readable right now? Opens and immediately releases
    /// the mapping, so it is safe to call alongside a live session.
    /// </summary>
    public static GameAttachment ProbeGameAttachment(out string message)
    {
        DuckStationCandidate? candidate = TryFindAzureDreams(out message);
        if (candidate is null)
            return GameAttachment.NotRunning;
        candidate.Dispose();
        message = string.Empty;
        return GameAttachment.Attached;
    }

    private static DuckStationCandidate? TryFindAzureDreams(out string message)
    {
        IReadOnlyList<DuckStationCandidate> candidates = DuckStationDetector.FindCandidates();
        if (candidates.Count == 0)
        {
            message = "DuckStation was not detected. Start the patched game and enable Export Shared Memory.";
            return null;
        }

        DuckStationCandidate? selected = null;
        var failures = new List<string>();
        foreach (DuckStationCandidate candidate in candidates)
        {
            if (candidate.Memory is null)
            {
                failures.Add($"PID {candidate.ProcessId}: {candidate.Error}");
                continue;
            }
            if (!AzureDreamsUsProbe.TryIdentify(candidate.Memory, out bool identified, out string? probeError))
            {
                failures.Add($"PID {candidate.ProcessId}: {probeError}");
                continue;
            }
            if (!identified)
            {
                failures.Add($"PID {candidate.ProcessId}: Azure Dreams (USA) is not loaded");
                continue;
            }

            selected = candidate;
            break;
        }

        foreach (DuckStationCandidate candidate in candidates)
        {
            if (!ReferenceEquals(candidate, selected))
                candidate.Dispose();
        }

        if (selected is null)
        {
            message = failures.Count == 0
                ? "No DuckStation instance has Azure Dreams (USA) loaded."
                : string.Join("; ", failures);
            return null;
        }

        message = string.Empty;
        return selected;
    }

    private static bool SynchronizeLocationChecks(
        Adap.Client.Emulators.IEmulatorMemory memory,
        IArchipelagoSession session,
        HashSet<long> submittedChecks,
        IReadOnlyDictionary<long, byte> trapLocations,
        ForcedTrapQueue forcedTraps,
        out bool shopPurchaseObserved,
        out string message)
    {
        shopPurchaseObserved = false;
        byte[] gameMask = new byte[AzureDreamsReceiveState.LocationMaskSize];
        if (!AzureDreamsReceiveState.TryReadCollectedLocationMask(memory, gameMask, out message))
            return false;
        byte[] shopMask = new byte[AzureDreamsReceiveState.ShopLocationMaskSize];
        if (!AzureDreamsReceiveState.TryReadCollectedShopLocationMask(memory, shopMask, out message))
            return false;

        long[] gameChecks = [
            .. AzureDreamsReceiveState.GetCollectedLocationIds(gameMask),
            .. AzureDreamsReceiveState.GetCollectedShopLocationIds(shopMask),
        ];
        // What the save already held the first time this client looked at it.
        // A check that was in there before we arrived is history - it may
        // still need reporting, but nobody just picked it up.
        bool trapArmingPrimed = forcedTraps.ObservedGameChecks is not null;
        HashSet<long> observedGameChecks = forcedTraps.ObservedGameChecks ??= [];
        HashSet<long> serverChecks = session.Locations.AllLocationsChecked.ToHashSet();
        submittedChecks.RemoveWhere(serverChecks.Contains);
        long[] unsent = gameChecks
            .Where(location => !serverChecks.Contains(location) && !submittedChecks.Contains(location))
            .ToArray();
        if (unsent.Length > 0)
        {
            session.Locations.CompleteLocationChecks(unsent);
            submittedChecks.UnionWith(unsent);
            shopPurchaseObserved = unsent.Any(IsShopLocation);
            Console.WriteLine($"Sent {unsent.Length} saved location check{(unsent.Length == 1 ? string.Empty : "s")}.");
            // The transfer itself is reported by the server's ItemSend
            // broadcast, which names this send the same way it names every
            // other player's.

            // A first-time check of a trap-holding location IS the trap's
            // delivery: queue its spring. Locations the server already knew
            // about never reach `unsent`, which is what makes this
            // exactly-once across sessions with no journal field.
            //
            // But `unsent` is "the server has not heard this yet", which is
            // NOT the same as "this just happened": a client attaching to a
            // save with offline pickups in it reports the lot in one pass. So
            // a trap arms only when this client watched the check appear
            // (trapArmingPrimed and not in the baseline) AND the player is
            // standing on a tower floor to spring it on. Anything else is a
            // trap arriving at a moment it does not belong to - which is how
            // one went off on the first frame of a loaded floor and spent the
            // turn the player needed.
            int pickupFloor = -1;
            foreach (long location in unsent)
            {
                if (!trapLocations.TryGetValue(location, out byte trapId))
                    continue;
                if (pickupFloor < 0)
                    pickupFloor = AzureDreamsTowerProgressReader.ReadLiveTowerFloor(memory);
                if (!IsTrapSpringable(
                        trapArmingPrimed,
                        observedGameChecks.Contains(location),
                        pickupFloor,
                        forcedTraps.FloorAtLastPoll,
                        out string refusal))
                {
                    Console.WriteLine($"Trap location {location}: {refusal}.");
                    continue;
                }
                forcedTraps.Pending.Add(new PendingTrap(trapId, pickupFloor));
            }
        }

        observedGameChecks.UnionWith(gameChecks);

        if (!AzureDreamsReceiveState.TryMergeCheckedLocations(
                memory,
                session.Locations.AllLocationsChecked,
                out int mergedTower,
                out message))
        {
            return false;
        }

        if (!AzureDreamsReceiveState.TryMergeCheckedShopLocations(
                memory,
                session.Locations.AllLocationsChecked,
                out int mergedShop,
                out message))
        {
            return false;
        }

        int merged = mergedTower + mergedShop;
        if (merged > 0)
            Console.WriteLine($"Restored {merged} server-confirmed check{(merged == 1 ? string.Empty : "s")} into the game save.");
        return true;
    }

    /// <summary>
    /// Applies every progressive keycard in the received history to the
    /// durable clearance level immediately, independent of the sequential
    /// receive cursor. Keycards do not enter inventory and are not paced by
    /// the game's delivery rhythm, so they never wait behind a blocked
    /// inventory item. Idempotent: the level is set to the full-history
    /// count, and an increase is announced once.
    /// </summary>
    internal static bool SynchronizeProgressiveKeycards(
        IArchipelagoSession session,
        Adap.Client.Emulators.IEmulatorMemory memory,
        out string message) =>
        SynchronizeProgressiveKeycards(
            memory,
            session.Items.AllItemsReceived.Select(item => item.ItemId).ToArray(),
            out message);

    internal static bool SynchronizeProgressiveKeycards(
        Adap.Client.Emulators.IEmulatorMemory memory,
        IReadOnlyList<long> receivedItemIds,
        out string message)
    {
        int keycardCount = receivedItemIds.Count(id => id == ProgressiveKeycardItemId);
        if (keycardCount > AzureDreamsReceiveState.MaximumKeycardLevel)
        {
            message = $"The server history contains {keycardCount} progressive keycards; only " +
                $"{AzureDreamsReceiveState.MaximumKeycardLevel} are valid.";
            return false;
        }

        if (!AzureDreamsReceiveState.TryReadProgressiveKeycardLevel(
                memory, out byte currentLevel, out message))
        {
            return false;
        }
        if (currentLevel == keycardCount)
        {
            message = string.Empty;
            return true;
        }

        if (!AzureDreamsReceiveState.TrySetProgressiveKeycardLevel(
                memory, (byte)keycardCount, out message))
        {
            return false;
        }
        if (keycardCount > currentLevel)
        {
            Console.WriteLine(
                $"Received Progressive Keycard; clearance is now {keycardCount}.");
        }
        message = string.Empty;
        return true;
    }

    /// <summary>
    /// The sands' twin of <see cref="SynchronizeProgressiveKeycards"/>: the
    /// count of Red Sands in the received history is the weapon temper level,
    /// the count of Blue Sands the shield temper level, the count of White
    /// Sands the ball charge level. Applied eagerly and idempotently to the
    /// three level bytes; a rise is announced once. More than the maximum in
    /// the history is rejected, not clamped.
    /// </summary>
    internal static bool SynchronizeTemperSands(
        IArchipelagoSession session,
        Adap.Client.Emulators.IEmulatorMemory memory,
        out string message) =>
        SynchronizeTemperSands(
            memory,
            session.Items.AllItemsReceived.Select(item => item.ItemId).ToArray(),
            out message);

    internal static bool SynchronizeTemperSands(
        Adap.Client.Emulators.IEmulatorMemory memory,
        IReadOnlyList<long> receivedItemIds,
        out string message)
    {
        int redCount = receivedItemIds.Count(id => id == RedSandItemId);
        int blueCount = receivedItemIds.Count(id => id == BlueSandItemId);
        int whiteCount = receivedItemIds.Count(id => id == WhiteSandItemId);
        if (redCount > AzureDreamsReceiveState.MaximumTemperLevel ||
            blueCount > AzureDreamsReceiveState.MaximumTemperLevel ||
            whiteCount > AzureDreamsReceiveState.MaximumTemperLevel)
        {
            message = $"The server history contains {redCount} Red, {blueCount} Blue and {whiteCount} White Sands; only " +
                $"{AzureDreamsReceiveState.MaximumTemperLevel} of each are valid.";
            return false;
        }

        if (!AzureDreamsReceiveState.TryReadTemperLevels(
                memory, out byte weaponLevel, out byte shieldLevel, out message))
        {
            return false;
        }
        if (!AzureDreamsReceiveState.TryReadBallChargeLevel(memory, out byte chargeLevel, out message))
            return false;
        if (weaponLevel == redCount && shieldLevel == blueCount && chargeLevel == whiteCount)
        {
            message = string.Empty;
            return true;
        }

        if (weaponLevel != redCount || shieldLevel != blueCount)
        {
            if (!AzureDreamsReceiveState.TrySetTemperLevels(
                    memory, (byte)redCount, (byte)blueCount, out message))
            {
                return false;
            }
        }
        if (chargeLevel != whiteCount &&
            !AzureDreamsReceiveState.TrySetBallChargeLevel(memory, (byte)whiteCount, out message))
        {
            return false;
        }
        if (redCount > weaponLevel)
            Console.WriteLine($"Received Red Sand; the smith tempers weapons to level {redCount} now.");
        if (blueCount > shieldLevel)
            Console.WriteLine($"Received Blue Sand; the smith tempers shields to level {blueCount} now.");
        if (whiteCount > chargeLevel)
        {
            int perVisit = BallChargeUsesForLevel(whiteCount);
            Console.WriteLine(
                "Received White Sand; the ball charger adds " +
                (perVisit == 1 ? "1 charge" : $"{perVisit} charges") +
                " per town visit now.");
        }
        message = string.Empty;
        return true;
    }

    /// <summary>
    /// ball_charger.USES_BY_LEVEL: how many charges the charger hands out per
    /// town visit at this level. Not a per-ball cap - the ceiling on one ball
    /// is ten at every level.
    /// </summary>
    internal static int BallChargeUsesForLevel(int level) =>
        level switch { 0 => 0, 1 => 1, 2 => 2, _ => 3 };

    // Any durable cursor at or above this floor is a gift sequence, not a
    // receive count: the current gift range starts at the sign bit
    // (AzureDreamsGiftService.GiftReceiveSequenceBase), and saves corrupted
    // before the range moved hold values from the old 0x40000000 base.
    internal const uint GiftCorruptedCursorFloor = 0x4000_0000;

    /// <summary>
    /// A town dispatcher without the gift-range guard commits a gift
    /// request's sequence into the durable receive cursor, wedging the
    /// ordinary item queue behind an impossible cursor - permanently, since
    /// the count lives in the save. Detect that signature and restore the
    /// last cursor this client observed or wrote. That baseline is exact:
    /// gifts never legitimately advance the cursor, so the pre-corruption
    /// value is the last sane one, and at most one mailbox delivery can
    /// complete between client polls.
    /// </summary>
    internal static bool TryRepairGiftCorruptedReceiveCursor(
        Adap.Client.Emulators.IEmulatorMemory memory,
        int serverHistoryCount,
        uint? baselineCursor,
        out bool repaired,
        out string message)
    {
        repaired = false;
        if (!AzureDreamsReceiveState.TryReadReceivedItemCount(memory, out uint cursor, out message))
            return false;
        if (cursor < GiftCorruptedCursorFloor)
        {
            message = string.Empty;
            return true;
        }

        if (baselineCursor is not uint baseline ||
            baseline >= GiftCorruptedCursorFloor ||
            baseline > (uint)serverHistoryCount)
        {
            message = $"The save's receive cursor (0x{cursor:x8}) was overwritten by a gift " +
                "delivery and no trusted baseline is available; receiving stays paused. " +
                "Load a save or town checkpoint from before the gift arrived and reconnect.";
            return true;
        }

        if (!AzureDreamsReceiveState.TryWriteReceivedItemCount(memory, baseline, out message))
            return false;

        repaired = true;
        message = $"Repaired the receive cursor: a gift delivery overwrote it with its gift " +
            $"sequence (0x{cursor:x8}); restored {baseline}.";
        return true;
    }

    /// <summary>
    /// Folds a delivery the game has acknowledged but nobody has recorded
    /// into its owner's durable state. The mailbox ack is the only evidence
    /// of a delivery the moment it happens; if a new request is staged over
    /// it before its owner observes it, the item is re-requested and the
    /// game delivers it twice. An ordinary ack one past the durable cursor
    /// becomes a cursor write; a gift-range ack is handed to the gift
    /// service's ledger. Best-effort: an absent or in-flight mailbox, or a
    /// non-delivered status (the owner handles invalid/full explicitly), is
    /// simply left alone.
    /// </summary>
    private static void FoldUnrecordedDeliveries(
        Adap.Client.Emulators.IEmulatorMemory memory,
        IArchipelagoSession session,
        int localSlot,
        string localPlayerName)
    {
        uint request = 0;
        uint acknowledged = 0;
        uint status = AzureDreamsMailbox.ReceiveStatusIdle;
        bool read;
        if (AzureDreamsMailbox.TryDetect(memory, out bool towerMailbox, out _) &&
            towerMailbox)
        {
            // The resident mailbox is readable in both modes since the carve
            // retraction, and its ack can be the ONLY record of a delivery
            // that landed in the frames before a town crossing: with the
            // game-side cursor commit removed (2026-08-05), a delivered ack
            // the tower polls never observed must be folded here, in town,
            // before the town queue re-offers the same index through Nada.
            read = AzureDreamsMailbox.TryReadReceiveStatus(
                memory, out request, out acknowledged, out status, out _, out _);
        }
        else
        {
            // Old-disc fallback: no resident mailbox, so the town beacon's
            // receive fields are the only possible carrier.
            if (!AzureDreamsTownMailbox.TryDetect(memory, out bool townMailbox, out _) ||
                !townMailbox)
            {
                return;
            }
            read = AzureDreamsTownMailbox.TryReadReceiveStatus(
                memory, out request, out acknowledged, out status, out _, out _, out _);
        }
        if (!read || acknowledged == 0 || request != acknowledged)
            return;
        if (status != AzureDreamsMailbox.ReceiveStatusDelivered)
            return;

        if (acknowledged >= GiftCorruptedCursorFloor)
        {
            AzureDreamsGiftService.NotifyGiftAcknowledged(
                session, localSlot, localPlayerName, acknowledged);
            return;
        }

        if (!AzureDreamsReceiveState.TryReadReceivedItemCount(memory, out uint cursor, out _))
            return;
        if (acknowledged != cursor + 1)
            return; // Already recorded, or not this save's request stream.
        if (AzureDreamsReceiveState.TryWriteReceivedItemCount(memory, acknowledged, out _))
        {
            Console.WriteLine(
                $"Folded acknowledged delivery {acknowledged} into the receive " +
                "cursor (the ack outlived the poll that staged it).");
        }
    }

    /// <summary>
    /// The history indices the town queue should stage next, walking PAST
    /// line-cutters instead of stopping at them.
    ///
    /// <para>The append loop used to break at the first keycard or gold
    /// package in the pending run, which chopped the batch: Nada delivered
    /// only what was staged before the cutter, the cursor caught up over it
    /// after the queue drained, and the remainder staged a poll later - one
    /// extra conversation per cutter. A history interleaved
    /// item/keycard/item/gold degenerated to one item per talk (reported
    /// 2026-08-06, made common by the gold packages).</para>
    ///
    /// <para>Skipping is safe for exactly the reason stopping is required
    /// for ordinary items: a line-cutter's grant does not depend on the
    /// cursor (keycard level and gold-granted counter are their own durable
    /// state), so a cursor fold crossing its unqueued index loses nothing -
    /// the gap-tolerance the token scheme already guarantees for keycard
    /// holes. An ordinary item, by contrast, is delivered BY cursor
    /// position, which is why the caller still stops the batch at an
    /// undeliverable one.</para>
    /// </summary>
    internal static List<uint> PlanTownQueueAppends(
        IReadOnlyList<long> pendingItemIds,
        uint nextOrdinaryIndex,
        int freeSlots)
    {
        var planned = new List<uint>();
        for (uint index = nextOrdinaryIndex;
             index < pendingItemIds.Count && planned.Count < freeSlots;
             index++)
        {
            long itemId = pendingItemIds[(int)index];
            if (IsLineCutterItemId(itemId))
                continue;
            planned.Add(index);
        }

        return planned;
    }

    /// <summary>
    /// The town path: fill the queue Nada drains, and nothing else.
    ///
    /// <para>The client never delivers in town, and the game never writes the
    /// durable receive cursor at all (town write removed 2026-08-02, tower
    /// stub removed 2026-08-05) - delivery is observed from the queue record
    /// and folded forward by the client, the sole cursor authority. A partial
    /// delivery (inventory and safe both full) stops `head` at the first
    /// undelivered entry, so the fold stops with it. Everything this method
    /// knows it derives from the live queue, so a client restart or a town
    /// reload that wiped the slab reconciles with no saved state.</para>
    /// </summary>
    private static bool SynchronizeTownReceiveQueue(
        Adap.Client.Emulators.IEmulatorMemory memory,
        ItemInfo[] received,
        uint cursor,
        AzureDreamsTownReceiveQueueState queue,
        out string message)
    {
        // Fold anything the game has consumed since the last poll into the
        // durable cursor. Since 2026-08-05 the game writes that cursor
        // nowhere - the client's folds are its only writers.
        //
        // It exists because making the game the SOLE writer is what caused
        // the 2026-08-01 duplication: everything Nada handed over in town was
        // delivered a second time on tower entry, which is precisely what a
        // cursor that never advanced looks like. The old mailbox path had the
        // client write the cursor too, and that redundancy was masking
        // whichever side drops it. Monotonic by construction - it only ever
        // moves the cursor forward - so it cannot cause the opposite fault.
        // Absolute reading of the queue, independent of having watched the
        // transition. Entries in [head, count) are what the game has NOT
        // taken, so the lowest ordinary token still pending proves everything
        // below it was delivered.
        //
        // The delta-based observation below cannot survive a client restart -
        // it compares against the previous poll, and a restart has no
        // previous poll. That was covered by the game committing the cursor
        // itself; with the game out of that business (2026-08-02) the client
        // has to be able to recover the truth from the record alone, and this
        // is how. Both paths only ever move the cursor forward.
        uint lowestPending = 0;
        foreach (AzureDreamsQueuedItem item in queue.InFlight)
        {
            if (AzureDreamsTownReceiveQueue.IsGiftToken(item.Token))
                continue;
            if (lowestPending == 0 || item.Token < lowestPending)
                lowestPending = item.Token;
        }
        if (lowestPending > 1 && lowestPending - 1 > cursor)
        {
            uint recovered = lowestPending - 1;
            if (!AzureDreamsReceiveState.TryWriteReceivedItemCount(
                    memory, recovered, out message))
            {
                return false;
            }
            Console.WriteLine(
                $"Receive cursor recovered {cursor} -> {recovered} from the queue " +
                $"(token {lowestPending} is still pending, so everything below it landed).");
            cursor = recovered;
        }

        // Absolute delivered-through, read out of the record rather than
        // inferred from having watched the transition. The delta observer
        // below clamps what it reports to the entries it had recorded, so an
        // entry appended after its last snapshot and consumed before the next
        // one is dropped - and everything dropped is re-delivered by the tower
        // on entry, which is the duplicated Nada receive. This sees it whether
        // or not any poll caught it happening.
        if (!AzureDreamsTownReceiveQueue.TryReadDeliveredThrough(
                memory, queue, out uint deliveredThrough, out message))
        {
            return false;
        }
        // Two bounds, both structural. An ordinary token is historyIndex + 1,
        // so it can never exceed the history; and the client can never have
        // more than the queue's slot count outstanding past the cursor, so a
        // record left over from another save cannot fold this one forward.
        if (deliveredThrough > (uint)received.Length ||
            deliveredThrough > cursor + AzureDreamsTownReceiveQueue.Slots)
        {
            deliveredThrough = 0;
        }

        uint consumed = AzureDreamsTownReceiveQueue.ObserveConsumedOrdinary(queue);
        if (consumed > 0)
        {
            // Report every observed consumption, not only the ones that move
            // the cursor. Silence has to mean "the game consumed nothing",
            // never "the fold ran and decided it had nothing to do" - those
            // were indistinguishable in the first attempt, which is why a
            // whole play session could not say whether it fired.
            Console.WriteLine(
                consumed > cursor
                    ? $"Nada delivered through token {consumed}; folding the receive " +
                      $"cursor {cursor} -> {consumed} (the game's own commit had not)."
                    : $"Nada delivered through token {consumed}; the game's own commit " +
                      $"already advanced the receive cursor to {cursor}.");
        }
        // This line firing is the under-fold caught in the act: the record says
        // the game delivered further than any poll observed. It cannot repeat
        // for the same token, because the fold below closes the gap.
        if (deliveredThrough > consumed && deliveredThrough > cursor)
        {
            Console.WriteLine(
                $"Receive cursor recovered {cursor} -> {deliveredThrough} from the " +
                $"delivered queue entries; no poll observed token {deliveredThrough} " +
                "being consumed.");
        }
        if (deliveredThrough > consumed)
            consumed = deliveredThrough;

        if (consumed > cursor)
        {
            if (!AzureDreamsReceiveState.TryWriteReceivedItemCount(
                    memory, consumed, out message))
            {
                return false;
            }
            cursor = consumed;
        }

        // A run of client-granted items at the cursor would otherwise stall
        // it: keycards are applied eagerly by SynchronizeProgressiveKeycards,
        // gold by SynchronizeGoldPackages and send tokens by
        // SynchronizeSendTokens, so none of them is ever queued and nothing
        // the game delivers will ever carry their token.
        // Stepping over them is a cursor write, so it is only safe while the
        // game has no ordinary entry to commit - and with none in flight,
        // nothing else writes that cursor at all.
        if (!AzureDreamsTownReceiveQueue.HasOrdinaryInFlight(queue) &&
            cursor < received.Length &&
            IsLineCutterItemId(received[cursor].ItemId))
        {
            uint skipped = cursor;
            while (skipped < received.Length &&
                   IsLineCutterItemId(received[skipped].ItemId))
            {
                skipped++;
            }

            if (!AzureDreamsReceiveState.TryWriteReceivedItemCount(
                    memory, skipped, out message))
            {
                return false;
            }

            // Re-read next poll rather than reasoning about the queue across
            // a write we just made.
            message = string.Empty;
            return true;
        }

        if (!AzureDreamsTownReceiveWindow.AllowsAppend(queue, DateTime.UtcNow))
        {
            message = string.Empty;
            return true;
        }

        int queued = 0;
        string? blocked = null;
        long[] pendingItemIds = new long[received.Length];
        for (int index = 0; index < received.Length; index++)
            pendingItemIds[index] = received[index].ItemId;
        foreach (uint next in PlanTownQueueAppends(
                     pendingItemIds,
                     AzureDreamsTownReceiveQueue.NextOrdinaryIndex(queue, cursor),
                     queue.Free))
        {
            ItemInfo item = received[next];
            if (!AzureDreamsItemManifest.TryGetInventoryDescriptor(
                    item.ItemId,
                    out AzureDreamsItemDescriptor descriptor))
            {
                // An undeliverable ORDINARY item must stop the batch: later
                // deliveries would fold the cursor past it and it would never
                // arrive. (Line-cutters are different - the planner skips
                // them because their grants do not depend on the cursor.)
                blocked =
                    $"Unsupported Azure Dreams item {item.ItemDisplayName} " +
                    $"(0x{item.ItemId:x}). It remains pending.";
                break;
            }

            uint token;
            try
            {
                token = checked(next + 1);
            }
            catch (OverflowException)
            {
                message = "The received-item cursor exhausted the token range.";
                return false;
            }
            if (token >= AzureDreamsTownReceiveQueue.GiftTokenBase)
            {
                message = "The received-item cursor reached the gift token range.";
                return false;
            }

            if (!AzureDreamsTownReceiveQueue.TryAppend(
                    memory, queue, descriptor, token, out queue, out message))
            {
                return false;
            }
            queued++;
        }

        int waiting = Math.Max(0, received.Length - (int)cursor - queue.InFlight.Count);
        if (queue.InFlight.Count == 0)
        {
            AzureDreamsTownReceiveWindow.ResetHoldAnnouncement();
        }
        else if (queue.Free == 0 && waiting > 0)
        {
            AzureDreamsTownReceiveWindow.AnnounceQueueFull(waiting);
        }
        else if (queued > 0)
        {
            AzureDreamsTownReceiveWindow.AnnounceQueued(queue.InFlight.Count, waiting);
        }

        message = blocked ?? string.Empty;
        return blocked is null;
    }

    private static bool ProcessReceivedItems(
        Adap.Client.Emulators.IEmulatorMemory memory,
        IArchipelagoSession session,
        int localSlot,
        string localPlayerName,
        Action<ClientTransfer>? transferSink,
        ReceivePresentationTracker presentationTracker,
        bool isTownMode,
        ref uint? reportedReceiveCursor,
        out bool townReceiveAcknowledged,
        out string message)
    {
        townReceiveAcknowledged = false;
        if (!AzureDreamsReceiveState.TryReadReceivedItemCount(memory, out uint cursor, out message))
            return false;

        // The duplication detector. Nothing in this client lowers the cursor,
        // so if it drops, something outside did - and everything between the
        // two values is about to be delivered a second time. Printing it at
        // the moment it happens is the difference between a bug report saying
        // "items came twice" and one that names the crossing.
        if (AzureDreamsReceiveState.ObserveCursorRegression(cursor, out uint cursorHighWater))
        {
            Console.Error.WriteLine(
                $"RECEIVE CURSOR WENT BACKWARDS: {cursorHighWater} -> {cursor}. " +
                $"Nothing in this client lowers it, so the save block was reverted " +
                $"underneath us - a checkpoint restore, the town/tower crossing, or a " +
                $"second client on this slot. The {cursorHighWater - cursor} item(s) " +
                "in between are still owed to the game and will arrive again.");
        }

        ItemInfo[] received = session.Items.AllItemsReceived.ToArray();
        // Stamp every pending item's first sighting so the presentation
        // decision below can tell an at-hand delivery from one that has
        // been waiting behind a backed-up queue.
        DateTime observedAt = DateTime.UtcNow;
        for (long index = cursor; index < received.Length; index++)
            presentationTracker.ObserveQueued(index, observedAt);
        uint? previouslyReportedCursor = reportedReceiveCursor;
        if (!TryReportAcknowledgedReceiveCursorAdvance(
                cursor,
                received.Length,
                ref reportedReceiveCursor,
                index =>
                {
                    ItemInfo acknowledgedItem = received[index];
                    Console.WriteLine(
                        "Received " +
                        AzureDreamsItemManifest.DisplayNameFor(
                            acknowledgedItem.ItemId,
                            acknowledgedItem.ItemDisplayName) +
                        $"; native request {index + 1} acknowledged.");
                    PublishReceiveTransfer(
                        acknowledgedItem,
                        localSlot,
                        localPlayerName,
                        transferSink);
                },
                out message))
        {
            return false;
        }
        if (isTownMode &&
            previouslyReportedCursor is not null &&
            previouslyReportedCursor < cursor)
        {
            townReceiveAcknowledged = true;
        }

        // The record lives in the resident block now, outside anything the
        // disc restores, so the client lays down its header. Safe here: the
        // cursor read above already proved a seeded build with a valid
        // persistent identity.
        if (!AzureDreamsTownReceiveQueue.TryEnsureInitialized(
                memory, out bool queueInitialized, out string initMessage))
        {
            message = initMessage;
            return false;
        }
        if (queueInitialized)
            Console.WriteLine("Initialized the receive queue record.");

        // In town the client does not deliver at all: it fills a queue that
        // Nada drains from inside her own conversation. The client owns the
        // delivered-through count; the game only reports `head`.
        if (!AzureDreamsTownReceiveQueue.TryRead(
                memory,
                out AzureDreamsTownReceiveQueueState queue,
                out string queueMessage))
        {
            message = queueMessage;
            return false;
        }
        // Nada is the TOWN delivery method, not the only one. The tower keeps
        // its native pickup presentation - that is the point of having a
        // tower path at all.
        //
        // This must branch on the MODE, not on the record being present. The
        // queue used to live in the town slab, so "present" and "in town"
        // were the same thing; moving it into the resident block on
        // 2026-08-02 made it visible in the tower too, and a bare
        // `queue.Present` test then routed tower receives into a queue that
        // nothing drains. Items stopped arriving in the tower entirely.
        if (isTownMode && queue.Present)
        {
            // The mirror of the tower-entry queue recall below: a receive
            // request staged in the tower can outlive the trip - undelivered
            // because Koh never idled, or wedged on inventory-full - and the
            // resident mailbox carries it across the crossing. Nada is about
            // to deliver that same history index through the queue, and a
            // stale request left armed would make the dispatcher deliver it
            // a second time on the next tower entry. Cancel it here, where
            // the dispatcher is not running and nothing can race the write.
            if (!AzureDreamsMailbox.TryRecallInFlightReceive(
                    memory, out uint recalledRequest, out string mailboxRecallError))
            {
                message = mailboxRecallError;
                return false;
            }
            if (recalledRequest != 0)
            {
                Console.WriteLine(
                    $"Recalled undelivered tower receive request {recalledRequest}; " +
                    "Nada delivers it in town instead.");
            }

            return SynchronizeTownReceiveQueue(
                memory,
                received,
                cursor,
                queue,
                out message);
        }

        // In the tower, recall anything still sitting in the queue so it gets
        // delivered natively instead of waiting for a town trip. Safe by
        // construction: a queued entry is one the game has NOT taken - `head`
        // never passed it, so the cursor never advanced and the item is still
        // owed. Writing `count` back to `head` is the client editing the one
        // byte it owns, and it cannot race Nada because she is not running.
        if (queue.Present)
        {
            int recalledCount = queue.InFlight.Count;
            bool wasLocked = queue.Locked;
            // Erases the entries as well as pulling `count` back, and clears
            // the conversation's lock and result. A recalled entry was never
            // delivered, and leaving it outside the live occupancy would let
            // the delivered-through watermark read it as though it had been -
            // which loses the item instead of duplicating it. See
            // AzureDreamsTownReceiveQueue.TryReadDeliveredThrough.
            if (!AzureDreamsTownReceiveQueue.TryRecallInFlight(
                    memory, queue, out queue, out string recallError))
            {
                message = recallError;
                return false;
            }
            if (recalledCount > 0)
            {
                Console.WriteLine(
                    $"Recalled {recalledCount} queued item(s); the tower " +
                    "delivers them natively.");
            }
            if (wasLocked)
            {
                Console.WriteLine(
                    "Cleared a receive lock that outlived its conversation.");
            }
        }

        while (cursor < received.Length)
        {
            ItemInfo item = received[cursor];
            // Every string below is shown to the player, and the town
            // notification one is rendered by the GAME itself.
            string itemName = AzureDreamsItemManifest.DisplayNameFor(
                item.ItemId, item.ItemDisplayName);
            if (item.ItemId == ProgressiveKeycardItemId)
            {
                // The clearance level was already applied eagerly by
                // SynchronizeProgressiveKeycards (keycards cut the line).
                // Here the cursor merely catches up to it - record the
                // transfer for the activity feed and advance.
                PublishReceiveTransfer(
                    item,
                    localSlot,
                    localPlayerName,
                    transferSink);
            }
            else if (item.ItemId == GoldPackageItemId)
            {
                // Already banked eagerly by SynchronizeGoldPackages (gold
                // cuts the line like keycards - it consumes no inventory
                // space, which is the only thing the queue paces). Here the
                // cursor merely catches up; record the transfer and advance.
                PublishReceiveTransfer(
                    item,
                    localSlot,
                    localPlayerName,
                    transferSink);
            }
            else if (item.ItemId == SendTokenItemId)
            {
                // Already banked eagerly by SynchronizeSendTokens, for the
                // same reason gold is: it is a counter, not an inventory
                // item. The cursor merely catches up over it.
                PublishReceiveTransfer(
                    item,
                    localSlot,
                    localPlayerName,
                    transferSink);
            }
            else if (IsTemperSandItemId(item.ItemId))
            {
                // Already applied eagerly to the smith's level bytes by
                // SynchronizeTemperSands (the sands cut the line like
                // keycards and never enter the bag). The cursor merely
                // catches up over it.
                PublishReceiveTransfer(
                    item,
                    localSlot,
                    localPlayerName,
                    transferSink);
            }
            else if (IsTrapItemId(item.ItemId))
            {
                // Sprung (or queued to spring) by the location-check path
                // the moment its own tower check was first reported - the
                // item's appearance in the history is an echo of that same
                // pickup. Never queued, never delivered to inventory; the
                // cursor merely catches up over it.
                PublishReceiveTransfer(
                    item,
                    localSlot,
                    localPlayerName,
                    transferSink);
            }
            else if (AzureDreamsItemManifest.TryGetInventoryDescriptor(
                         item.ItemId,
                         out AzureDreamsItemDescriptor descriptor))
            {
                uint requestSequence;
                try
                {
                    requestSequence = checked(cursor + 1);
                }
                catch (OverflowException)
                {
                    message = "The received-item cursor exhausted the mailbox sequence range.";
                    return false;
                }

                if (!AzureDreamsTownMailbox.TryDetect(
                        memory,
                        out bool townMailboxDetected,
                        out string townDetectionMessage))
                {
                    message = $"Could not receive {itemName}: {townDetectionMessage}";
                    return false;
                }

                // Only reachable on a disc built before the receive queue
                // existed: a current disc always carries the queue wherever
                // it carries the town core, and that path returned above.
                // Passive town delivery is what raced NPC talks for the
                // native script queue, so it is never re-enabled - the items
                // wait for the tower.
                if (townMailboxDetected)
                {
                    AzureDreamsTownReceiveWindow.AnnounceHold(
                        $"{received.Length - (int)cursor} item(s) waiting: this disc " +
                        "predates the receive queue, so town delivery is off. They " +
                        "will be delivered on your next tower entry.");
                    message = string.Empty;
                    return true;
                }

                // Latched on first attempt: the flag is baked into the
                // staged request and re-verified every poll, so it must
                // not change while the request is in flight.
                bool showReceivePresentation = presentationTracker.DecidePresentation(
                    cursor,
                    ShouldShowReceivePresentation(
                        session.ConnectionInfo.Slot,
                        item.Player.Slot,
                        item.LocationId),
                    DateTime.UtcNow);
                bool synchronized = townMailboxDetected
                    ? AzureDreamsTownMailbox.TrySynchronizeReceive(
                        memory,
                        requestSequence,
                        descriptor,
                        itemName,
                        showReceivePresentation,
                        out AzureDreamsReceiveProgress progress,
                        out string receiveMessage)
                    : AzureDreamsMailbox.TrySynchronizeReceive(
                        memory,
                        requestSequence,
                        descriptor,
                        showReceivePresentation,
                        out progress,
                        out receiveMessage);
                if (!synchronized)
                {
                    message = $"Could not receive {itemName}: {receiveMessage}";
                    return false;
                }

                if (progress == AzureDreamsReceiveProgress.Queued)
                {
                    string presentation = townMailboxDetected
                        ? showReceivePresentation
                            ? "town dialogue"
                            : "silent self-location delivery"
                        : showReceivePresentation
                            ? "tower pickup"
                            : "silent self-location delivery";
                    Console.WriteLine(
                        $"Queued {itemName} for native game delivery " +
                        $"({presentation}; request {requestSequence}).");
                    message = string.Empty;
                    return true;
                }
                if (progress == AzureDreamsReceiveProgress.Waiting)
                {
                    message = string.Empty;
                    return true;
                }
                if (progress == AzureDreamsReceiveProgress.InventoryFull)
                {
                    message = townMailboxDetected
                        ? $"Could not receive {itemName}: Koh's inventory and safe are full; " +
                            "the game will retry after a slot is freed."
                        : $"Could not receive {itemName}: Koh's inventory is full; " +
                            "the game will retry after a slot is freed.";
                    return false;
                }

                Console.WriteLine($"Received {itemName}; native request {requestSequence} acknowledged.");
                PublishReceiveTransfer(
                    item,
                    localSlot,
                    localPlayerName,
                    transferSink);
            }
            else
            {
                message = $"Unsupported Azure Dreams item {itemName} (0x{item.ItemId:x}). It remains pending.";
                return false;
            }

            cursor++;
            if (!AzureDreamsReceiveState.TryWriteReceivedItemCount(memory, cursor, out message))
                return false;
            reportedReceiveCursor = cursor;
            if (isTownMode)
                townReceiveAcknowledged = true;
        }

        // The queue is drained. Re-arm the hold notice so the next batch that
        // arrives in town explains itself again instead of being silent.
        AzureDreamsTownReceiveWindow.ResetHoldAnnouncement();
        message = string.Empty;
        return true;
    }

    internal static bool TryReportAcknowledgedReceiveCursorAdvance(
        uint durableCursor,
        int receivedItemCount,
        ref uint? reportedCursor,
        Action<int> publishIndex,
        out string message)
    {
        if (durableCursor > receivedItemCount)
        {
            message =
                $"The save's receive cursor ({durableCursor}) is ahead of the server history " +
                $"({receivedItemCount}).";
            return false;
        }

        // On initial attachment, existing progress is historical and should
        // not flood the activity pane. Thereafter the game can advance its
        // durable cursor and acknowledgement atomically between client polls;
        // report every newly crossed history index before processing the next
        // pending item. Loading an older save rebases the in-memory reporter.
        if (reportedCursor is null || reportedCursor > durableCursor)
        {
            reportedCursor = durableCursor;
            message = string.Empty;
            return true;
        }

        while (reportedCursor < durableCursor)
        {
            int index = checked((int)reportedCursor.Value);
            publishIndex(index);
            reportedCursor++;
        }

        message = string.Empty;
        return true;
    }

    private static void PublishReceiveTransfer(
        ItemInfo item,
        int localSlot,
        string localPlayerName,
        Action<ClientTransfer>? transferSink)
    {
        transferSink?.Invoke(new ClientTransfer(
            ClientTransferKind.Received,
            AzureDreamsItemManifest.DisplayNameFor(item.ItemId, item.ItemDisplayName),
            item.Player.Alias,
            localPlayerName,
            item.Player.Slot == localSlot,
            true));
    }

    private static bool IsShopLocation(long locationId) =>
        locationId is >= AzureDreamsReceiveState.ShopLocationIdBase and
        < AzureDreamsReceiveState.ShopLocationIdBase +
        AzureDreamsReceiveState.ShopLocationCount;

    private static void AnnounceCheckpoint(AzureDreamsCheckpointMetadata? checkpoint)
    {
        if (checkpoint is not AzureDreamsCheckpointMetadata saved)
            return;

        // Player-facing, so it says the one thing a player can act on: where
        // this checkpoint puts them if they reload. The cursor and shop mask
        // were diagnostics for the receive work and are still in the .adcp
        // header and the metadata if a future investigation wants them.
        Console.WriteLine($"Saved checkpoint. {FormatCheckpointReason(saved.Reason)}");
    }

    private static string FormatCheckpointReason(AzureDreamsCheckpointReason reason) =>
        reason switch
        {
            AzureDreamsCheckpointReason.TowerFloorEntry => "Tower.",
            _ => "Town.",
        };

    /// <summary>
    /// The delivery queue as the player will experience it: pending gifts
    /// first - the gift service polls ahead of the ordinary stream every
    /// pass, so waiting gifts drain before the next history item - then the
    /// ordinary queue, capped to the panel's slots. Gifts were invisible
    /// here before, which made a Nada send look like nothing happened until
    /// the delivery animation fired.
    /// </summary>
    internal static List<AzureDreamsIncomingItem> BuildIncomingDisplayList(
        IReadOnlyList<AzureDreamsGiftService.IncomingGift> pendingGifts,
        IReadOnlyList<AzureDreamsIncomingItem> pendingItems,
        int slotCount)
    {
        // Ordinary items lead: gifts join the tail of the incoming queue
        // (they only stage once the item queue drains), and the display
        // mirrors the actual delivery order.
        var display = new List<AzureDreamsIncomingItem>(slotCount);
        foreach (AzureDreamsIncomingItem item in pendingItems)
        {
            if (display.Count == slotCount)
                return display;
            display.Add(item);
        }
        foreach (AzureDreamsGiftService.IncomingGift gift in pendingGifts)
        {
            if (display.Count == slotCount)
                return display;
            display.Add(new AzureDreamsIncomingItem(
                gift.ProtocolItemId, gift.ItemName, gift.FromName));
        }
        return display;
    }

    internal static bool ShouldShowReceivePresentation(
        int localSlot,
        int sourceSlot,
        long locationId)
    {
        if (sourceSlot != localSlot)
            return true;

        bool ownTowerLocation = locationId is >= AzureDreamsReceiveState.LocationIdBase and
            < AzureDreamsReceiveState.LocationIdBase + AzureDreamsReceiveState.LocationCount;
        bool ownShopLocation = locationId is >= AzureDreamsReceiveState.ShopLocationIdBase and
            < AzureDreamsReceiveState.ShopLocationIdBase + AzureDreamsReceiveState.ShopLocationCount;
        return !ownTowerLocation && !ownShopLocation;
    }

    /// <summary>
    /// A boolean the room may or may not carry. Slot data arrives as loosely
    /// typed JSON, so anything that is not recognisably false is taken as the
    /// default rather than as a reason to refuse the room - the version gate
    /// is what refuses rooms.
    /// </summary>
    internal static bool ReadSlotDataFlag(LoginSuccessful login, string key, bool fallback)
    {
        if (!login.SlotData.TryGetValue(key, out object? raw) || raw is null)
            return fallback;
        if (raw is bool value)
            return value;
        string text = raw.ToString()?.Trim() ?? string.Empty;
        if (bool.TryParse(text, out bool parsed))
            return parsed;
        if (int.TryParse(text, out int number))
            return number != 0;
        return fallback;
    }

    private static bool TryValidateSlotDataVersion(LoginSuccessful login, out string message)
    {
        if (!login.SlotData.TryGetValue("apworld_version", out object? rawVersion) ||
            !int.TryParse(rawVersion?.ToString(), out int version))
        {
            message = "The server slot data does not contain a valid Azure Dreams APWorld version.";
            return false;
        }
        if (version != SupportedSlotDataVersion)
        {
            message = $"This client requires Azure Dreams slot-data version {SupportedSlotDataVersion}; " +
                $"the room supplies version {version}. Regenerate the room with the matching APWorld.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    private static bool TryReadSlotSeedIdentity(
        LoginSuccessful login,
        out AzureDreamsSeedIdentity identity,
        out string message)
    {
        identity = default;
        if (!login.SlotData.TryGetValue("seed_signature", out object? rawSignature))
        {
            message = "The server slot data does not contain an Azure Dreams seed signature.";
            return false;
        }

        string text = rawSignature?.ToString()?.Trim() ?? string.Empty;
        byte[] signature;
        try
        {
            signature = Convert.FromHexString(text);
        }
        catch (FormatException)
        {
            message = "The server slot data contains an invalid Azure Dreams seed signature.";
            return false;
        }
        if (signature.Length != 8)
        {
            message =
                "The server slot data seed signature must contain exactly eight bytes.";
            return false;
        }

        identity = new AzureDreamsSeedIdentity(signature);
        message = string.Empty;
        return true;
    }

    internal static bool TryParseEndpoint(
        string endpoint,
        out string host,
        out int port,
        out string message)
    {
        host = string.Empty;
        port = DefaultPort;
        string normalized = endpoint.Contains("://", StringComparison.Ordinal)
            ? endpoint
            : $"ws://{endpoint}";
        if (!Uri.TryCreate(normalized, UriKind.Absolute, out Uri? uri) || string.IsNullOrWhiteSpace(uri.Host))
        {
            message = $"Invalid Archipelago endpoint: {endpoint}";
            return false;
        }

        host = uri.Host;
        if (!uri.IsDefaultPort)
            port = uri.Port;
        message = string.Empty;
        return true;
    }
}
