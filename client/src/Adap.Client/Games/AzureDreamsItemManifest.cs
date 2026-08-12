namespace Adap.Client.Games;

/// <summary>
/// Decodes the descriptor-derived protocol IDs emitted by the Azure Dreams
/// APWorld.
///
/// The APWorld owns the item pool: every ID that reaches this client came out
/// of the same generation that built the player's disc, and Archipelago only
/// ever routes a player items from their own game. So this does not re-decide
/// which items a player may hold - it decodes the ID and rejects only
/// coordinates that name no Azure Dreams item at all, via
/// <see cref="AzureDreamsItemCatalog.IsKnownItem"/>.
///
/// The distinction matters because the failure modes are not symmetric. A
/// descriptor wrongly accepted is caught by the mailbox handshake and shows up
/// in the log; a descriptor wrongly rejected is a silent dead slot for the rest
/// of the run, and that is exactly what a client-side copy of the APWorld's
/// catalog produces the first time the two versions drift.
/// </summary>
internal static class AzureDreamsItemManifest
{
    public const long ItemIdBase = 0x0AD0_0000;

    private const int CategoryShift = 11;
    private const int NativeItemShift = 5;
    private const long FieldMask = 0x1f;

    // Quality is signed and equipment carries flags, so the low five bits hold
    // the quality MAGNITUDE and bits 16-18 hold the rest. They sit below the
    // 0xAD the base puts at bits 20-27, and an ID with all three clear is
    // byte-identical to what the earlier sixteen-bit layout produced.
    //
    // `archipelago/worlds/azure_dreams/item_manifest.py` encodes this and has
    // to agree bit for bit.
    private const long NegativeQualityBit = 1L << 16;
    private const long UnidentifiedBit = 1L << 17;
    private const long CursedBit = 1L << 18;
    private const long OffsetMask = (1L << 19) - 1;

    /// <summary>Handed over unidentified.</summary>
    public const byte FlagUnidentified = 0x80;

    /// <summary>Set alongside a negative quality.</summary>
    public const byte FlagCursed = 0x40;

    /// <summary>
    /// Worn. Never encoded into a protocol ID - a granted item must not arrive
    /// already equipped - but a gift carries the sender's raw inventory bytes,
    /// so it can still reach the client. Cleared by the town gate alongside
    /// <see cref="FlagUnidentified"/>.
    /// </summary>
    public const byte FlagEquipped = 0x20;

    public static bool TryGetInventoryDescriptor(
        long protocolItemId,
        out AzureDreamsItemDescriptor descriptor)
    {
        descriptor = default;
        long offset = protocolItemId - ItemIdBase;
        if (offset <= 0 || offset > OffsetMask)
            return false;

        byte category = checked((byte)((offset >> CategoryShift) & FieldMask));
        byte nativeItemId = checked((byte)((offset >> NativeItemShift) & 0x3f));
        int magnitude = (int)(offset & FieldMask);
        sbyte quality = checked(
            (sbyte)((offset & NegativeQualityBit) != 0 ? -magnitude : magnitude));
        byte flags = (byte)(
            ((offset & UnidentifiedBit) != 0 ? FlagUnidentified : 0) |
            ((offset & CursedBit) != 0 ? FlagCursed : 0));

        if (EncodeProtocolItemId(category, nativeItemId, quality, flags) != protocolItemId ||
            !AzureDreamsItemCatalog.IsKnownItem(category, nativeItemId))
        {
            return false;
        }

        descriptor = new AzureDreamsItemDescriptor(
            nativeItemId,
            category,
            quality,
            flags);
        return true;
    }

    /// <summary>
    /// The name the PLAYER may be shown for an Archipelago item. The item's
    /// Archipelago NAME is its identity in the room and is not this - the
    /// server, the spoiler and hints all print that one, and nothing here can
    /// reach them.
    ///
    /// <para>This is the client's half of the rule the APWorld applies to
    /// strings the game renders (<c>item_manifest.display_name_for</c>): an
    /// item handed over unidentified must not announce its quality. The client
    /// was showing <c>Septic found Dark Sword (+1)</c> for an item the game is
    /// deliberately presenting as unappraised, which is the client cheating for
    /// the player.</para>
    ///
    /// <para>Driven off the decoded FLAG, not off a category list, so it
    /// follows the APWorld with no further change if identification is ever
    /// randomized or other categories start carrying the bit. Items from other
    /// worlds do not decode here and pass through untouched - we know nothing
    /// about them beyond what Archipelago called them.</para>
    /// </summary>
    public static string DisplayNameFor(long protocolItemId, string archipelagoName)
    {
        if (!TryGetInventoryDescriptor(
                protocolItemId,
                out AzureDreamsItemDescriptor descriptor) ||
            (descriptor.Flags & FlagUnidentified) == 0)
        {
            return archipelagoName;
        }

        return DisplayNameFor(descriptor, archipelagoName);
    }

    /// <summary>
    /// The same rule for a descriptor the client holds directly - a gift, which
    /// carries whatever the sender was actually holding and which the APWorld
    /// never named. The catalog is preferred here precisely because the
    /// descriptor is exact, and <paramref name="fallbackName"/> is only reached
    /// when the catalog could not load at all; a name that came off the wire
    /// then gets its quality stripped rather than trusted, because an older
    /// sending client will have put the enchantment in it.
    /// </summary>
    public static string DisplayNameFor(
        AzureDreamsItemDescriptor descriptor,
        string fallbackName)
    {
        string? cataloged = AzureDreamsItemCatalog.DescribeItem(
            descriptor.Category,
            descriptor.ItemId,
            descriptor.Quality,
            descriptor.Flags);
        if (cataloged is not null)
            return cataloged;

        return (descriptor.Flags & FlagUnidentified) != 0
            ? AzureDreamsTownMailbox.StripQualitySuffix(fallbackName)
            : fallbackName;
    }

    /// <summary>
    /// A protocol ID for a descriptor the client holds, used for the icon and
    /// the catalog coordinate rather than for delivery.
    ///
    /// <para>Both fields the ID cannot always hold are dropped rather than
    /// allowed to carry: quality is five bits of MAGNITUDE, and a gift carries
    /// whatever the sender had - a sixty-charge ball or a warming egg overflows
    /// it and the spill lands in the native item id, naming a different item
    /// entirely. Category and item id sit above quality and are always exact,
    /// which is all the icon lookup reads.</para>
    /// </summary>
    public static long EncodeIconProtocolItemId(AzureDreamsItemDescriptor descriptor) =>
        EncodeProtocolItemId(
            descriptor.Category,
            descriptor.ItemId,
            Math.Abs((int)descriptor.Quality) <= FieldMask ? descriptor.Quality : 0,
            descriptor.Flags);

    public static long EncodeProtocolItemId(
        byte category,
        byte nativeItemId,
        int quality,
        byte flags = 0)
    {
        long encoded =
            ItemIdBase |
            ((long)category << CategoryShift) |
            ((long)nativeItemId << NativeItemShift) |
            (long)Math.Abs(quality);
        if (quality < 0)
            encoded |= NegativeQualityBit;
        if ((flags & FlagUnidentified) != 0)
            encoded |= UnidentifiedBit;
        if ((flags & FlagCursed) != 0)
            encoded |= CursedBit;
        return encoded;
    }
}
