using Adap.Client.Emulators;
using Archipelago.MultiClient.Net;
using Archipelago.MultiClient.Net.Enums;
using Archipelago.MultiClient.Net.Models;
using Newtonsoft.Json.Linq;

namespace Adap.Client.Games;

/// <summary>
/// Player-to-player item gifting over Archipelago data storage - the
/// transport half of the tower Send row (docs/tower-send-design.md). A gift is
/// a small JSON object in the target's <c>Giftbox;{team};{slot}</c> key.
///
/// The trigger is the in-game `ADGT` outgoing mailbox that the tower
/// players-menu commit publishes: count, target menu index, and the removed
/// items' full descriptors, guarded by a sequence/ack pair the same way the
/// town-receive `ADTR` record is. The game removes the items and compacts the
/// order table itself; this service only ships what the mailbox says was sent,
/// then acks.
///
/// Nada's town `ADGS` mailbox was the original trigger and is GONE (world
/// 0.9.109): sending is priced in send tokens, one item per token, and a
/// second unbounded send UI in town could not be reconciled with that. Her
/// receive half is untouched - incoming gifts still land in her queue in town
/// (see DeliverPendingGifts), which is why this file still talks about her.
/// </summary>
internal static class AzureDreamsGiftService
{
    public const string GameName = "Azure Dreams";

    // The tower record (generation: patch.TOWER_GIFT_MAILBOX_*), published by
    // the players-menu Send row's commit routine. It lives in the unused
    // tail of the ADAP protocol mailbox structure, so it survives floors
    // and is never disc-restored.
    //
    // It used to have a town twin, `ADGS` at 0x800FBF88, published by Nada's
    // send commit. That record no longer exists on the game side and this
    // client no longer reads the address: an unpolled stale record cannot
    // ship a phantom gift, and the slab span is free for whatever claims it
    // next.
    private const uint TowerGiftMailboxAddress = 0x801D_A5F0;
    internal const uint TowerGiftMailboxAddressForTest = TowerGiftMailboxAddress;
    /// <summary>0x14 header + 15 descriptor words.</summary>
    internal const int TowerGiftMailboxSizeForTest =
        GiftMailboxItemsOffset + 4 * TowerGiftMailboxMaxItems;
    private const uint TowerGiftMailboxMagic = 0x5447_4441; // 'ADGT'
    private const int GiftMailboxSequenceOffset = 0x04;
    private const int GiftMailboxAckOffset = 0x08;
    private const int GiftMailboxTargetOffset = 0x0C;
    private const int GiftMailboxCountOffset = 0x10;
    private const int GiftMailboxItemsOffset = 0x14;
    // The read buffer's bound, not any one mailbox's: kept at twenty so the
    // stack span still covers the widest record this protocol ever carried.
    private const int GiftMailboxMaxItems = 20;
    // 80 bytes of structure tail: 0x14 header + 15 descriptor words. The
    // commit only ever writes count = 1, but the bound is what stops a
    // garbage count from walking the read into the town receive queue.
    private const int TowerGiftMailboxMaxItems = 15;

    // Coins (category 0x0E) deliver as GOLD, not as items: the descriptor's
    // quality byte IS the amount - unsigned, 255 max - the same rule the
    // game applies when a coin is walked over or used. Banked exactly like
    // the multiworld's gold packages, so a friend a few gold short of a
    // purchase can be topped up cooperatively.
    private const byte CoinCategory = 0x0E;

    // Gift JSON fields.
    private const string ItemField = "item";      // protocol item id (long)
    private const string FromField = "from";      // sender slot (int)
    private const string NameField = "name";      // display name (string)
    private const string SequenceField = "seq";   // per-sender monotonic (long)
    // Raw 4-byte inventory descriptor, LE-packed into an int:
    // itemId | category<<8 | quality<<16 | flags<<24. The protocol item id
    // cannot carry a non-canonical quality (the manifest validates against
    // catalog charge tiers), so a 0-charge ball would either arrive with
    // canonical charges or be dropped; the raw bytes are authoritative.
    private const string DescriptorField = "desc";

    // Sender session field: a per-client-run nonce, so the per-sender
    // sequence can safely restart from 1 after a client restart without
    // the receiver mistaking the new gifts for already-delivered ones.
    private const string SessionField = "sid";

    // Gift receive requests use their own sequence range so they can never
    // collide with the ordinary item stream's cursor+1 requests. The old
    // cursor-derived sequence was the multi-send bug: gifts never advance
    // the durable receive cursor, so every gift in a batch got the SAME
    // sequence - the first occupied the mailbox and the rest wedged on a
    // descriptor mismatch until a town reload cleared the slab.
    //
    // The range starts at the sign bit on purpose: the town dispatcher
    // commits an ordinary request's sequence as the durable receive cursor
    // (sequence == cursor+1 by construction), and it recognizes and skips
    // gift sequences with a single bltz. Keep this in step with
    // town_receive.py's delivered-path range test.
    internal const uint GiftReceiveSequenceBase = 0x8000_0000;

    // In-memory dedupe, seeded from the durable consumed watermark below so
    // a client restart cannot replay the giftbox (which is append-only and
    // never pruned - rewriting it would race a sender's concurrent append).
    private static readonly HashSet<string> DeliveredGiftKeys = new();
    private static readonly Dictionary<int, long> OutgoingSequenceBySender = new();
    private static readonly string OutgoingSessionNonce =
        Guid.NewGuid().ToString("N")[..8];

    // Exactly one gift is in flight at a time; it advances only on a
    // confirmed Delivered, which is what makes a batch deliver each item
    // individually, in order, exactly once.
    private static string? _inFlightGiftKey;
    private static IncomingGift? _inFlightGift;
    private static uint _inFlightSequence;
    private static uint _nextGiftSequence = GiftReceiveSequenceBase + 1;
    private static string? _lastDeferredMessage;
    // The queue head as it stood when the in-flight gift was queued. The
    // game consuming that entry advances head by a small amount; the town
    // reloading resets head to zero. Both make the token disappear, so head
    // is what tells them apart - and it must not be `count`, which an
    // ordinary re-append in the same poll can restore to its old value.
    private static byte? _queuedAtHead;

    // The durable consumed watermark, stored in this receiver's own
    // data-storage key (single writer): the highest delivered sender-side
    // sequence per sender session, plus the mailbox (client-side) sequence of
    // the last delivery this client confirmed. The second field is what
    // disambiguates a leftover delivered mailbox ack after a client restart:
    // if it matches, the delivery was already recorded before the restart;
    // if not, the head undelivered gift was delivered and never recorded.
    private static readonly Dictionary<string, long> ConsumedBySender = new();
    private static long _confirmedMailboxAck;
    private static uint? _unattributedAck;
    private static object? _watermarkSession;
    private const string ConsumedSendersField = "senders";
    private const string ConsumedAckField = "ack";

    internal static void ResetIncomingStateForTest()
    {
        DeliveredGiftKeys.Clear();
        _inFlightGiftKey = null;
        _inFlightGift = null;
        _inFlightSequence = 0;
        _nextGiftSequence = GiftReceiveSequenceBase + 1;
        _lastDeferredMessage = null;
        _queuedAtHead = null;
        ConsumedBySender.Clear();
        _confirmedMailboxAck = 0;
        _unattributedAck = null;
        _watermarkSession = null;
    }

    private static string BoxKey(int team, int slot) => $"Giftbox;{team};{slot}";

    private static string ConsumedKey(int team, int slot) =>
        $"GiftboxConsumed;{team};{slot}";

    /// <summary>
    /// Polls the in-game outgoing mailbox - the tower players-menu `ADGT`
    /// record - and ships any newly published send to its target's giftbox,
    /// then acks the record. Nada's town `ADGS` record was polled here too
    /// until her send was removed in world 0.9.109.
    /// </summary>
    /// <param name="onItemLeftTheWorld">
    /// Called once per shipped item, after it is in the target's giftbox.
    /// A send is the one player action a checkpoint restore must not undo -
    /// the item is in someone else's world by then - so the client uses this
    /// to subtract it from the stored checkpoint.
    /// </param>
    public static void ProcessOutgoing(
        IArchipelagoSession session,
        IEmulatorMemory memory,
        int localSlot,
        string localPlayerName,
        Action<AzureDreamsItemDescriptor>? onItemLeftTheWorld = null)
    {
        ProcessOutgoingMailbox(
            session, memory, localSlot, localPlayerName,
            TowerGiftMailboxAddress, TowerGiftMailboxMagic,
            TowerGiftMailboxMaxItems, onItemLeftTheWorld);
    }

    private static void ProcessOutgoingMailbox(
        IArchipelagoSession session,
        IEmulatorMemory memory,
        int localSlot,
        string localPlayerName,
        uint mailboxAddress,
        uint mailboxMagic,
        int mailboxMaxItems,
        Action<AzureDreamsItemDescriptor>? onItemLeftTheWorld)
    {
        Span<byte> header = stackalloc byte[GiftMailboxItemsOffset];
        if (!memory.TryRead(mailboxAddress, header, out _))
            return;
        uint magic = BitConverter.ToUInt32(header[..4]);
        uint sequence = BitConverter.ToUInt32(header[GiftMailboxSequenceOffset..]);
        uint ack = BitConverter.ToUInt32(header[GiftMailboxAckOffset..]);
        if (magic != mailboxMagic || sequence == ack)
            return;

        int targetIndex = BitConverter.ToInt32(header[GiftMailboxTargetOffset..]);
        int count = BitConverter.ToInt32(header[GiftMailboxCountOffset..]);
        if (count < 1 || count > mailboxMaxItems)
        {
            AcknowledgeSend(memory, mailboxAddress, sequence);
            Console.Error.WriteLine($"Gift send: implausible item count {count}; dropped.");
            return;
        }

        Span<byte> items = stackalloc byte[GiftMailboxMaxItems * 4];
        if (!memory.TryRead(
                mailboxAddress + GiftMailboxItemsOffset,
                items[..(count * 4)],
                out _))
            return;
        // The commit routine publishes the sequence bump last; if the
        // record changed underneath this read, catch it on the next poll.
        Span<byte> confirm = stackalloc byte[4];
        if (!memory.TryRead(
                mailboxAddress + GiftMailboxSequenceOffset, confirm, out _) ||
            BitConverter.ToUInt32(confirm) != sequence)
            return;

        if (!TryResolveTargetSlot(session, localSlot, targetIndex, out int targetSlot))
        {
            AcknowledgeSend(memory, mailboxAddress, sequence);
            Console.Error.WriteLine(
                $"Gift send: menu target {targetIndex} has no matching player; dropped.");
            return;
        }

        int team = session.ConnectionInfo.Team;
        string targetName =
            session.Players.GetPlayerAlias(targetSlot) ?? $"Player {targetSlot}";
        string key = BoxKey(team, targetSlot);
        var gifts = new JArray();
        var shippedNames = new List<string>();
        var shipped = new List<AzureDreamsItemDescriptor>();
        for (int index = 0; index < count; index++)
        {
            byte itemId = items[index * 4];
            byte category = items[index * 4 + 1];
            byte quality = items[index * 4 + 2];
            byte flags = items[index * 4 + 3];
            if (category == 0)
                continue;
            // Quality is SIGNED and the flags belong in the id. Passing the raw
            // byte made a cursed -1 arrive as 255, whose top bits spill out of
            // the five-bit magnitude field and into the native item id, so a
            // cursed sword shipped an id naming a different item; dropping the
            // flags then lost unidentified from the id as well.
            var descriptor = new AzureDreamsItemDescriptor(
                itemId, category, unchecked((sbyte)quality), flags);
            long protocolItemId =
                AzureDreamsItemManifest.EncodeIconProtocolItemId(descriptor);
            string itemName = ResolveItemName(session, protocolItemId, descriptor);
            gifts.Add(new JObject
            {
                [ItemField] = protocolItemId,
                [FromField] = localSlot,
                [NameField] = itemName,
                [SequenceField] = NextOutgoingSequence(localSlot),
                [SessionField] = OutgoingSessionNonce,
                [DescriptorField] = PackDescriptor(itemId, category, quality, flags),
            });
            shippedNames.Add(itemName);
            shipped.Add(descriptor);
        }

        if (gifts.Count > 0)
        {
            // "add" of an array appends its elements to the stored list;
            // Initialize seeds an empty list for the first gift.
            session.DataStorage[Scope.Global, key].Initialize(new JArray());
            session.DataStorage[Scope.Global, key] += gifts;
            // Only once the giftbox has them: an item reported gone before it
            // has actually left would be taken off a checkpoint that is still
            // the only copy of it.
            foreach (AzureDreamsItemDescriptor descriptor in shipped)
                onItemLeftTheWorld?.Invoke(descriptor);
        }
        AcknowledgeSend(memory, mailboxAddress, sequence);
        foreach (string itemName in shippedNames)
        {
            Console.WriteLine(
                $"Gift: {localPlayerName} gifted {itemName} to {targetName}.");
        }
    }

    private static void AcknowledgeSend(
        IEmulatorMemory memory, uint mailboxAddress, uint sequence)
    {
        Span<byte> ack = stackalloc byte[4];
        BitConverter.TryWriteBytes(ack, sequence);
        memory.TryWrite(mailboxAddress + GiftMailboxAckOffset, ack, out _);
    }

    /// <summary>
    /// Nada's menu lists the room's other Azure Dreams players in slot
    /// order - the same order generation bakes her choice rows in - so the
    /// mailbox's target index resolves positionally.
    /// </summary>
    private static bool TryResolveTargetSlot(
        IArchipelagoSession session,
        int localSlot,
        int targetIndex,
        out int targetSlot)
    {
        int team = session.ConnectionInfo.Team;
        var candidates = session.Players.AllPlayers
            .Where(player =>
                player.Team == team &&
                player.Slot != localSlot &&
                string.Equals(player.Game, GameName, StringComparison.Ordinal))
            .OrderBy(player => player.Slot)
            .Select(player => player.Slot)
            .ToList();
        if (targetIndex >= 0 && targetIndex < candidates.Count)
        {
            targetSlot = candidates[targetIndex];
            return true;
        }
        targetSlot = -1;
        return false;
    }

    internal readonly record struct IncomingGift(
        string Key,
        AzureDreamsItemDescriptor Descriptor,
        string ItemName,
        string FromName,
        long ProtocolItemId = -1,
        string SenderKey = "",
        long SenderSequence = -1);

    /// <summary>
    /// Polls this player's giftbox and delivers new gifts through the same
    /// dual mailbox path ordinary received items use - town dialogue in
    /// town, native tower pickup in the tower - with the sender's raw
    /// descriptor bytes preserved exactly. Gifts join the tail of the
    /// incoming queue: a new gift stages only while the ordinary item queue
    /// is drained (<paramref name="ordinaryQueueDrained"/>), so it can never
    /// cut ahead of items the player is already owed.
    /// <paramref name="undelivered"/> is the giftbox-ordered list of gifts
    /// not yet confirmed delivered, for the client's queue displays;
    /// <paramref name="deliveredInTown"/> reports a delivery confirmed
    /// through the town mailbox this pass, so the caller can request a town
    /// checkpoint that captures the gifted item.
    /// </summary>
    public static void ProcessIncoming(
        IArchipelagoSession session,
        IEmulatorMemory memory,
        int localSlot,
        string localPlayerName,
        bool ordinaryQueueDrained,
        out IReadOnlyList<IncomingGift> undelivered,
        out bool deliveredInTown)
    {
        undelivered = [];
        deliveredInTown = false;
        int team = session.ConnectionInfo.Team;
        string key = BoxKey(team, localSlot);
        EnsureWatermarkLoaded(session, localSlot);

        JArray box;
        try
        {
            box = session.DataStorage[Scope.Global, key].To<JArray>() ?? new JArray();
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"Gift receive read failed: {exception.Message}");
            return;
        }

        var pending = new List<IncomingGift>();
        foreach (JToken token in box)
        {
            if (token is not JObject gift)
                continue;
            long fromSlot = gift.Value<long?>(FromField) ?? -1;
            long sequence = gift.Value<long?>(SequenceField) ?? -1;
            if (fromSlot < 0 || sequence < 0)
                continue;
            string? sessionNonce = gift.Value<string>(SessionField);
            string giftKey = sessionNonce is null
                ? $"{fromSlot}:{sequence}"
                : $"{fromSlot}:{sessionNonce}:{sequence}";
            string senderKey = sessionNonce is null
                ? $"{fromSlot}"
                : $"{fromSlot}:{sessionNonce}";
            if (ConsumedBySender.TryGetValue(senderKey, out long consumedSequence) &&
                sequence <= consumedSequence)
            {
                // Consumed under a previous client run; the durable watermark
                // is what makes the append-only giftbox safe to re-read.
                DeliveredGiftKeys.Add(giftKey);
                continue;
            }

            long protocolItemId = gift.Value<long?>(ItemField) ?? -1;
            AzureDreamsItemDescriptor descriptor;
            long packed = gift.Value<long?>(DescriptorField) ?? -1;
            if (packed is >= 0 and <= uint.MaxValue &&
                TryUnpackDescriptor((uint)packed, out descriptor))
            {
                // Raw bytes from the sender's inventory slot: quality
                // (charges) and flags survive exactly as removed. Never
                // reconstruct from the base item id.
            }
            else if (!AzureDreamsItemManifest.TryGetInventoryDescriptor(
                    protocolItemId,
                    out descriptor))
            {
                if (DeliveredGiftKeys.Add(giftKey))
                {
                    Console.Error.WriteLine(
                        $"Gift receive: item id 0x{protocolItemId:x} is not a deliverable descriptor.");
                }
                continue;
            }

            // Rebuilt from the descriptor rather than trusted from the wire:
            // the descriptor is the sender's exact inventory bytes, flags
            // included, while NameField was written by whatever client the
            // sender is running - an older one puts an unidentified item's
            // enchantment straight into it.
            string itemName = AzureDreamsItemManifest.DisplayNameFor(
                descriptor, gift.Value<string>(NameField) ?? "an item");
            string fromName =
                session.Players.GetPlayerAlias((int)fromSlot) ?? $"Player {fromSlot}";
            // The protocol id is only needed for the client's icon lookup;
            // when the sender's entry lacks one (or carries a raw-descriptor
            // exact-state item the manifest can't name), rebuild it from the
            // descriptor so the queue still shows the right sprite.
            if (protocolItemId < 0)
            {
                protocolItemId =
                    AzureDreamsItemManifest.EncodeIconProtocolItemId(descriptor);
            }
            pending.Add(new IncomingGift(
                giftKey, descriptor, itemName, fromName, protocolItemId,
                senderKey, sequence));
        }

        // A delivered gift-range ack observed with no in-flight gift belongs
        // to a previous client run. The head undelivered gift is, by
        // construction, the gift that run had staged: delivery is strictly
        // giftbox-ordered and the watermark only advances on a confirmed
        // delivery. Record it now so it is not delivered a second time.
        if (_unattributedAck is uint leftoverAck)
        {
            _unattributedAck = null;
            IncomingGift? head = null;
            foreach (IncomingGift candidate in pending)
            {
                if (!DeliveredGiftKeys.Contains(candidate.Key))
                {
                    head = candidate;
                    break;
                }
            }
            if (head is IncomingGift adopted)
            {
                Console.WriteLine(
                    $"Gift: {adopted.ItemName} was delivered by a previous client " +
                    "run; recording it instead of delivering it again.");
                ConfirmDelivered(session, localSlot, adopted, leftoverAck, localPlayerName);
            }
            else
            {
                // Nothing is pending, so the ack's gift is already consumed;
                // just record the ack as handled.
                PersistConsumed(session, localSlot, leftoverAck);
            }
        }

        DeliverPendingGifts(
            session, memory, localSlot, pending, localPlayerName,
            ordinaryQueueDrained, out deliveredInTown);
        // Report after delivery so a gift confirmed this very pass does not
        // linger in the queue display for an extra poll.
        undelivered = pending
            .Where(gift => !DeliveredGiftKeys.Contains(gift.Key))
            .ToList();
    }

    /// <summary>
    /// Delivers the pending gifts one at a time, in giftbox order. A gift
    /// advances only on a confirmed Delivered from the game, so every item
    /// in a batch is individually queued, acknowledged, and delivered
    /// exactly once - and each request carries its own sequence from the
    /// gift range, never colliding with the ordinary item stream. A NEW
    /// request stages only while the ordinary item queue is drained, so
    /// gifts join the tail of the incoming queue instead of cutting ahead
    /// of items the player is already owed; a request that is already in
    /// the game's hands always completes.
    /// <paramref name="session"/> may be null (the self-test path); the
    /// durable watermark is then not persisted.
    /// </summary>
    internal static void DeliverPendingGifts(
        IArchipelagoSession? session,
        IEmulatorMemory memory,
        int localSlot,
        IReadOnlyList<IncomingGift> gifts,
        string localPlayerName,
        bool ordinaryQueueDrained,
        out bool deliveredInTown)
    {
        deliveredInTown = false;
        foreach (IncomingGift gift in gifts)
        {
            if (DeliveredGiftKeys.Contains(gift.Key))
                continue;

            // A coin banks as gold immediately - no mailbox request, no
            // in-game queue, works in both modes. Confirmed with the stored
            // ack watermark left where it was, since no sequence was staged.
            // (A coin staged as an ITEM by a pre-coin-aware client run and
            // resumed here would double-deliver; both testers run current
            // clients, and the window closes entirely once no old client
            // exists.)
            if (gift.Descriptor.Category == CoinCategory)
            {
                uint amount = unchecked((byte)gift.Descriptor.Quality);
                if (!AzureDreamsReceiveState.TryGrantGold(
                        memory, amount, out uint goldTotal, out string goldMessage))
                {
                    DeferOnce($"Gift receive deferred: {goldMessage}");
                    return;
                }
                Console.WriteLine(
                    $"Gift: {gift.ItemName} from {gift.FromName} banked as " +
                    $"{amount} gold; gold is now {goldTotal}.");
                ConfirmDelivered(
                    session, localSlot, gift, (uint)_confirmedMailboxAck,
                    localPlayerName);
                continue;
            }

            // The same branch ordinary received items take: town mailbox
            // when the town core is resident, native tower pickup
            // otherwise.
            if (!AzureDreamsTownMailbox.TryDetect(
                    memory, out bool townMailbox, out string detectMessage))
            {
                DeferOnce($"Gift receive deferred: {detectMessage}");
                return;
            }

            // In town, gifts join the same append-only queue Nada drains as
            // ordinary items - with a sign-bit token, so the game's `bltz`
            // guard skips the durable receive cursor for them. That is what
            // makes it structurally impossible for a gift to clobber the
            // cursor, the wedge fixed on 2026-07-30.
            if (!AzureDreamsTownReceiveQueue.TryRead(
                    memory,
                    out AzureDreamsTownReceiveQueueState queue,
                    out string queueMessage))
            {
                DeferOnce($"Gift receive deferred: {queueMessage}");
                return;
            }
            // Only in TOWN. The queue record is resident in both modes since
            // 2026-08-02, so its presence no longer implies the town - and
            // routing a tower gift into a queue Nada is not there to drain
            // makes it never arrive. `townMailbox` is the ADTR beacon, which
            // lives in the town slab and really is town-only.
            if (townMailbox && queue.Present)
            {
                if (!TryQueueGiftInTown(
                        session, memory, localSlot, gift, localPlayerName,
                        ordinaryQueueDrained, queue, out deliveredInTown))
                {
                    return;
                }
                continue;
            }
            if (townMailbox)
            {
                // A disc built before the receive queue existed. Passive town
                // delivery is never re-enabled; the gift waits for the tower.
                DeferOnce(
                    "Gift receive waiting: this disc predates the receive queue, " +
                    "so town delivery is off. It arrives on your next tower entry.");
                return;
            }

            if (_inFlightGiftKey is null)
            {
                // A pending gift-range request left over from a previous
                // client run: the head undelivered gift is by construction
                // the gift it staged, so adopt its sequence rather than
                // staging a second request for the same gift.
                uint leftoverRequest = 0;
                uint leftoverAck = 0;
                bool read = townMailbox
                    ? AzureDreamsTownMailbox.TryReadReceiveStatus(
                        memory, out leftoverRequest, out leftoverAck,
                        out _, out _, out _, out _)
                    : AzureDreamsMailbox.TryReadReceiveStatus(
                        memory, out leftoverRequest, out leftoverAck,
                        out _, out _, out _);
                if (read &&
                    leftoverRequest >= GiftReceiveSequenceBase &&
                    leftoverRequest != leftoverAck)
                {
                    _inFlightGiftKey = gift.Key;
                    _inFlightGift = gift;
                    _inFlightSequence = leftoverRequest;
                    if (_nextGiftSequence <= leftoverRequest)
                        _nextGiftSequence = leftoverRequest + 1;
                    Console.WriteLine(
                        $"Gift: adopted in-flight request {leftoverRequest} " +
                        "from a previous client run.");
                }
            }

            if (_inFlightGiftKey != gift.Key)
            {
                if (!ordinaryQueueDrained)
                {
                    DeferOnce(
                        "Gift receive waiting: gifts join the tail of the " +
                        "incoming queue behind pending items.");
                    return;
                }
                _inFlightGiftKey = gift.Key;
                _inFlightGift = gift;
                _inFlightSequence = _nextGiftSequence++;
            }

            bool synchronized = townMailbox
                ? AzureDreamsTownMailbox.TrySynchronizeReceive(
                    memory,
                    _inFlightSequence,
                    gift.Descriptor,
                    gift.ItemName,
                    showNotification: true,
                    out AzureDreamsReceiveProgress progress,
                    out string receiveMessage)
                : AzureDreamsMailbox.TrySynchronizeReceive(
                    memory,
                    _inFlightSequence,
                    gift.Descriptor,
                    showPresentation: true,
                    out progress,
                    out receiveMessage);
            if (!synchronized)
            {
                DeferOnce($"Gift receive deferred: {receiveMessage}");
                return;
            }

            if (progress == AzureDreamsReceiveProgress.Delivered)
            {
                ConfirmDelivered(
                    session, localSlot, gift, _inFlightSequence, localPlayerName);
                if (townMailbox)
                    deliveredInTown = true;
                continue;
            }

            // Queued, Waiting, or InventoryFull: the request is in the
            // game's hands; keep this gift in flight and come back.
            _lastDeferredMessage = null;
            return;
        }
    }

    /// <summary>
    /// The town path for one gift: confirm it if the game has consumed it,
    /// otherwise append it to the receive queue.
    ///
    /// <para>Still one gift in flight at a time, and still only staged once
    /// the ordinary queue is drained, so gifts join the tail of the incoming
    /// list instead of cutting ahead of items the player is already owed.
    /// What changed is the transport: an append to the queue rather than a
    /// mailbox request, with a sign-bit token that the game's cursor guard
    /// skips.</para>
    ///
    /// <para>Delivery is observed, not assumed: the token disappearing from
    /// the in-flight set means the game consumed that entry. A slab reset
    /// (the town reloading) also empties the queue, which is why a shrinking
    /// <c>count</c> drops the in-flight tracking WITHOUT confirming - the
    /// gift is then re-offered rather than silently marked consumed.</para>
    ///
    /// <returns>False when the caller should stop for this poll.</returns>
    /// </summary>
    private static bool TryQueueGiftInTown(
        IArchipelagoSession? session,
        IEmulatorMemory memory,
        int localSlot,
        IncomingGift gift,
        string localPlayerName,
        bool ordinaryQueueDrained,
        AzureDreamsTownReceiveQueueState queue,
        out bool deliveredInTown)
    {
        deliveredInTown = false;

        if (_inFlightGiftKey == gift.Key)
        {
            if (AzureDreamsTownReceiveQueue.ContainsToken(queue, _inFlightSequence))
            {
                // Still waiting for the player to talk to Nada.
                _lastDeferredMessage = null;
                return false;
            }

            // The entry is gone. It was consumed only if head advanced over
            // it by a plausible amount; a reset sends head back to zero,
            // which shows up here as a distance outside the queue's range.
            int advanced = _queuedAtHead is byte queuedAt
                ? unchecked((byte)(queue.Head - queuedAt))
                : 0;
            if (advanced is < 1 or > AzureDreamsTownReceiveQueue.Slots)
            {
                // The town reloaded and took the queue with it. Whether the
                // gift was delivered first is unknowable from here, so drop
                // the tracking and let the adoption path re-stage it: a
                // re-offer is recoverable, a false confirm is not.
                _inFlightGiftKey = null;
                _inFlightGift = null;
                DeferOnce(
                    "Gift receive: the town reloaded while a gift was queued; " +
                    "re-offering it.");
                return false;
            }

            ConfirmDelivered(
                session, localSlot, gift, _inFlightSequence, localPlayerName);
            deliveredInTown = true;
            return true;
        }

        if (_inFlightGiftKey is not null)
            return false;

        // Adopt a gift-range token left in the queue by a previous client
        // run: the head undelivered gift is by construction the one it
        // staged, so tracking it beats staging a second copy.
        foreach (AzureDreamsQueuedItem queued in queue.InFlight)
        {
            if (!AzureDreamsTownReceiveQueue.IsGiftToken(queued.Token))
                continue;
            _inFlightGiftKey = gift.Key;
            _inFlightGift = gift;
            _inFlightSequence = queued.Token;
            _queuedAtHead = queue.Head;
            if (_nextGiftSequence <= queued.Token)
                _nextGiftSequence = queued.Token + 1;
            Console.WriteLine(
                $"Gift: adopted queued token {queued.Token} from a previous client run.");
            return false;
        }

        if (!ordinaryQueueDrained)
        {
            DeferOnce(AzureDreamsTownReceiveWindow.GiftHold);
            return false;
        }
        if (queue.Free <= 0)
        {
            DeferOnce("Gift receive waiting: the receive queue is full.");
            return false;
        }
        if (!AzureDreamsTownReceiveWindow.AllowsAppend(queue, DateTime.UtcNow))
            return false;

        uint token = _nextGiftSequence++;
        if (!AzureDreamsTownReceiveQueue.TryAppend(
                memory, queue, gift.Descriptor, token, out _, out string appendMessage))
        {
            DeferOnce($"Gift receive deferred: {appendMessage}");
            return false;
        }

        _inFlightGiftKey = gift.Key;
        _inFlightGift = gift;
        _inFlightSequence = token;
        _queuedAtHead = queue.Head;
        _lastDeferredMessage = null;
        Console.WriteLine(
            $"Gift queued for Nada: {gift.ItemName} from {gift.FromName}.");
        return false;
    }

    /// <summary>
    /// Records a confirmed delivery everywhere it must be recorded: the
    /// in-memory dedupe, the per-sender consumed watermark, and (when a
    /// session is available) the durable data-storage copy of both. The
    /// watermark write happens before the delivery is announced so a crash
    /// between the game's ack and the persist cannot double-deliver more
    /// than the single gift the leftover-ack adoption already covers.
    /// </summary>
    private static void ConfirmDelivered(
        IArchipelagoSession? session,
        int localSlot,
        IncomingGift gift,
        uint mailboxSequence,
        string localPlayerName)
    {
        DeliveredGiftKeys.Add(gift.Key);
        if (gift.SenderKey.Length > 0 &&
            (!ConsumedBySender.TryGetValue(gift.SenderKey, out long consumed) ||
                gift.SenderSequence > consumed))
        {
            ConsumedBySender[gift.SenderKey] = gift.SenderSequence;
        }
        PersistConsumed(session, localSlot, mailboxSequence);
        _inFlightGiftKey = null;
        _inFlightGift = null;
        _lastDeferredMessage = null;
        Console.WriteLine(
            $"Gift: {gift.FromName} gifted {gift.ItemName} to {localPlayerName}.");
    }

    /// <summary>
    /// Called by the client's ack-folding pass when the mailbox holds a
    /// delivered gift-range acknowledgement. Confirms the in-flight gift if
    /// the sequence matches; otherwise flags the ack for head-gift adoption
    /// on the next giftbox poll (a delivery confirmed by a previous client
    /// run, or one whose confirmation this run has not observed yet). Also
    /// keeps the sequence counter ahead of every acknowledged sequence so a
    /// restarted client can never stage a request that collides with the
    /// mailbox's leftover state.
    /// </summary>
    internal static void NotifyGiftAcknowledged(
        IArchipelagoSession session,
        int localSlot,
        string localPlayerName,
        uint acknowledgedSequence)
    {
        EnsureWatermarkLoaded(session, localSlot);
        if (_nextGiftSequence <= acknowledgedSequence)
            _nextGiftSequence = acknowledgedSequence + 1;
        if (_inFlightGift is IncomingGift gift &&
            _inFlightSequence == acknowledgedSequence)
        {
            ConfirmDelivered(
                session, localSlot, gift, acknowledgedSequence, localPlayerName);
            return;
        }
        if (_inFlightGiftKey is null &&
            _confirmedMailboxAck != acknowledgedSequence)
        {
            _unattributedAck = acknowledgedSequence;
        }
    }

    private static void EnsureWatermarkLoaded(IArchipelagoSession session, int localSlot)
    {
        if (ReferenceEquals(_watermarkSession, session))
            return;
        ConsumedBySender.Clear();
        _confirmedMailboxAck = 0;
        try
        {
            JObject? stored = session.DataStorage[
                Scope.Global,
                ConsumedKey(session.ConnectionInfo.Team, localSlot)].To<JObject>();
            if (stored?[ConsumedSendersField] is JObject senders)
            {
                foreach (JProperty property in senders.Properties())
                    ConsumedBySender[property.Name] = property.Value.Value<long>();
            }
            _confirmedMailboxAck = stored?.Value<long?>(ConsumedAckField) ?? 0;
        }
        catch (Exception exception)
        {
            // In-memory dedupe still covers this run; the watermark repairs
            // itself on the next confirmed delivery.
            Console.Error.WriteLine($"Gift watermark read failed: {exception.Message}");
        }
        _watermarkSession = session;
    }

    private static void PersistConsumed(
        IArchipelagoSession? session, int localSlot, uint mailboxSequence)
    {
        _confirmedMailboxAck = mailboxSequence;
        if (session is null)
            return;
        var senders = new JObject();
        foreach ((string sender, long sequence) in ConsumedBySender)
            senders[sender] = sequence;
        var stored = new JObject
        {
            [ConsumedSendersField] = senders,
            [ConsumedAckField] = (long)mailboxSequence,
        };
        try
        {
            session.DataStorage[
                Scope.Global,
                ConsumedKey(session.ConnectionInfo.Team, localSlot)] = stored;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"Gift watermark write failed: {exception.Message}");
        }
    }

    /// <summary>
    /// The gift-delivery watermark as it stands now, as JSON, for storing
    /// beside a checkpoint. Null before any session has loaded one, which
    /// simply means the checkpoint gets no companion and a restore leaves
    /// gift delivery where it is.
    /// </summary>
    public static string? CaptureConsumedWatermark()
    {
        if (_watermarkSession is null)
            return null;
        var senders = new JObject();
        foreach ((string sender, long sequence) in ConsumedBySender)
            senders[sender] = sequence;
        return new JObject
        {
            [ConsumedSendersField] = senders,
            [ConsumedAckField] = _confirmedMailboxAck,
        }.ToString(Newtonsoft.Json.Formatting.None);
    }

    /// <summary>
    /// Put gift delivery back where it stood when a checkpoint was captured,
    /// so every gift that arrived after it is offered again.
    ///
    /// <para>This is the receiving half of the rule a send obeys on the other
    /// side. A checkpoint rolls the game's memory back, and the gifted item
    /// goes back with it - but "delivered" is recorded in Archipelago's data
    /// storage, which no memory restore can reach. Left alone, the item is
    /// gone from the world and marked delivered forever.</para>
    ///
    /// <para>The giftbox is append-only and never pruned, which is what makes
    /// the replay possible at all: the entries are still there to re-read.
    /// The in-memory dedupe is cleared with the watermark, since it is only
    /// ever a cache of it.</para>
    /// </summary>
    public static void RewindConsumedWatermark(
        IArchipelagoSession session, int localSlot, string? snapshot)
    {
        if (string.IsNullOrEmpty(snapshot))
            return;

        JObject stored;
        try
        {
            stored = JObject.Parse(snapshot);
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(
                $"The checkpoint's gift watermark could not be read ({exception.Message}); " +
                "gifts delivered after it will not be re-offered.");
            return;
        }

        ConsumedBySender.Clear();
        if (stored[ConsumedSendersField] is JObject senders)
        {
            foreach (JProperty property in senders.Properties())
                ConsumedBySender[property.Name] = property.Value.Value<long>();
        }
        _confirmedMailboxAck = stored.Value<long?>(ConsumedAckField) ?? 0;
        // Everything downstream of the watermark is a cache of it, and a
        // restore invalidates all of it: the dedupe set, the gift the previous
        // run had in flight, and any ack it had not attributed yet.
        DeliveredGiftKeys.Clear();
        _inFlightGiftKey = null;
        _inFlightGift = null;
        _inFlightSequence = 0;
        _unattributedAck = null;
        _queuedAtHead = null;
        _lastDeferredMessage = null;
        // Claim the session so the next poll does not read the server copy
        // back over this, then write the rewound watermark out as the truth.
        _watermarkSession = session;
        PersistConsumed(session, localSlot, (uint)_confirmedMailboxAck);
        Console.WriteLine(
            "Gift delivery rewound to the restored checkpoint; anything gifted to you after " +
            "it will be offered again.");
    }

    private static void DeferOnce(string message)
    {
        if (message == _lastDeferredMessage)
            return;
        _lastDeferredMessage = message;
        Console.Error.WriteLine(message);
    }

    internal static long PackDescriptor(
        byte itemId, byte category, byte quality, byte flags) =>
        itemId | ((long)category << 8) | ((long)quality << 16) | ((long)flags << 24);

    /// <summary>
    /// Unpacks a giftbox descriptor. A gift can only have come out of some
    /// other player's Azure Dreams inventory, so anything sendable is by
    /// construction a real item and the client has no business ruling on which
    /// ones a player may be handed - only on whether the coordinate names an
    /// item at all. Quality and flags stay free-form on purpose: they carry the
    /// sender's exact charge count and status bits, which no canonical item id
    /// could reconstruct.
    /// </summary>
    internal static bool TryUnpackDescriptor(
        uint packed, out AzureDreamsItemDescriptor descriptor)
    {
        descriptor = new AzureDreamsItemDescriptor(
            (byte)(packed & 0xFF),
            (byte)((packed >> 8) & 0xFF),
            unchecked((sbyte)((packed >> 16) & 0xFF)),
            (byte)((packed >> 24) & 0xFF));
        return AzureDreamsItemCatalog.IsKnownItem(
            descriptor.Category, descriptor.ItemId);
    }

    private static long NextOutgoingSequence(int senderSlot)
    {
        long next = OutgoingSequenceBySender.TryGetValue(senderSlot, out long current)
            ? current + 1
            : 1;
        OutgoingSequenceBySender[senderSlot] = next;
        return next;
    }

    private static string ResolveItemName(
        IArchipelagoSession session,
        long protocolItemId,
        AzureDreamsItemDescriptor descriptor)
    {
        // An unidentified item is never named from Archipelago: that name is
        // the item's room identity and carries the enchantment the sender has
        // not appraised. The catalog answers from the flag instead.
        if ((descriptor.Flags & AzureDreamsItemManifest.FlagUnidentified) == 0)
        {
            try
            {
                string? name = session.Items.GetItemName(protocolItemId, GameName);
                if (!string.IsNullOrEmpty(name))
                    return name;
            }
            catch
            {
                // Fall through to the catalog.
            }
        }

        // The APWorld names only the ids it places, so a gift of anything else
        // - a Fire Ball at 3 charges, an enchanted sword - misses. The game's
        // own naming rules over data.json cover every descriptor a player can
        // actually be holding.
        return AzureDreamsItemCatalog.DescribeItem(
                   descriptor.Category,
                   descriptor.ItemId,
                   descriptor.Quality,
                   descriptor.Flags)
            ?? $"item {descriptor.Category:X2}:{descriptor.ItemId:X2}";
    }
}
