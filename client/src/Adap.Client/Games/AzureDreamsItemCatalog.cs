using System.Text.Json;
using Adap.Client.Windows;

namespace Adap.Client.Games;

/// <summary>
/// The embedded data.json item catalog: display names, icons, and which
/// (category, item) coordinates name a real Azure Dreams item at all.
///
/// data.json is keyed category then native item id, which is exactly what the
/// protocol item id encodes, so no separate table has to be kept in step. Each
/// entry is <c>[display name, icon file]</c>; a category also carries a
/// <c>has_quality</c> flag, which is what decides whether an item's name ends
/// in a charge count or an enchantment.
///
/// This is also the client's delivery bound, and the whole of it. It describes
/// the game rather than any policy about the game: the APWorld decides what it
/// places and the ROM decides what Nada will hand over, and a client that
/// keeps its own opinion about either drops items the moment they disagree.
/// </summary>
internal static class AzureDreamsItemCatalog
{
    private const int CategoryShift = 11;
    private const int NativeItemShift = 5;
    private const long FieldMask = 0x1f;
    private const long CatalogOffsetMask = (1L << 19) - 1;

    /// <summary>
    /// Balls spend quality as charges and the game writes it in parentheses.
    /// The other three quality-bearing categories - swords, wands, shields -
    /// spend it as an enchantment and take a signed suffix instead.
    /// </summary>
    private const int BallCategory = 4;

    private static readonly Lazy<CatalogData> Catalog = new(Build);

    /// <summary>The icon file for an item, or null when it is not mapped.</summary>
    public static string? TryGetIconFile(long protocolItemId)
    {
        if (!TryDecode(protocolItemId, out int category, out int nativeItemId))
            return null;

        return Catalog.Value.Items.TryGetValue(Key(category, nativeItemId), out CatalogEntry entry)
            ? entry.Icon
            : null;
    }

    public static Image? TryGetIcon(long protocolItemId) =>
        ClientAssets.ByFileName(TryGetIconFile(protocolItemId));

    /// <summary>Entry count, so a self-test can prove the data actually loaded.</summary>
    public static int MappedItemCount => Catalog.Value.Items.Count;

    /// <summary>
    /// Whether a coordinate names a real Azure Dreams item. This is the only
    /// question the client is entitled to ask about an incoming item: a coin
    /// and a quest item are as real as a sword, and which of them a player is
    /// allowed to be handed is the ROM's call, not this program's.
    ///
    /// A catalog that failed to load answers true for anything structurally
    /// sound. Losing data.json already costs icons and names; it must not also
    /// cost the player every item they are owed.
    /// </summary>
    public static bool IsKnownItem(int category, int nativeItemId)
    {
        if (category == 0 || nativeItemId == 0)
            return false;

        CatalogData data = Catalog.Value;
        return data.Items.Count == 0 || data.Items.ContainsKey(Key(category, nativeItemId));
    }

    /// <summary>
    /// The name the game itself would print for a descriptor, or null when the
    /// coordinate names no item. Quality is part of the name wherever the game
    /// treats it as part of the item: <c>Fire Ball (3)</c> for charges,
    /// <c>Iron Sword +2</c> for an enchantment. Eggs carry a warming percentage
    /// that no player tracks, and data.json already declines to call it a
    /// quality, so their names stop at the base.
    ///
    /// This exists because the APWorld only names the ids it places, and a gift
    /// carries whatever the sender was actually holding - any charge count, any
    /// enchantment, cursed included.
    ///
    /// <para>An item carrying FLAG_UNIDENTIFIED stops at its base name. The
    /// player has not appraised it, and quality is the whole of what appraisal
    /// reveals - <c>Vital Sword -1</c> in the queue gives away exactly what the
    /// inventory is hiding. Cursed goes with it: the only way this client ever
    /// shows cursed is the negative quality, so dropping the quality drops the
    /// curse, and that coupling is deliberate rather than incidental.</para>
    ///
    /// <para>Keyed on the flag rather than on the category, so it follows
    /// whatever carries the bit. Balls keep their charge count today because
    /// they are handed over identified; make them unidentified and the count
    /// disappears from these strings with no change here. This mirrors
    /// <c>NativeReward.display_name</c> in the APWorld's item_manifest.py and
    /// has to keep agreeing with it.</para>
    /// </summary>
    public static string? DescribeItem(
        int category,
        int nativeItemId,
        int quality,
        byte flags = 0)
    {
        CatalogData data = Catalog.Value;
        if (!data.Items.TryGetValue(Key(category, nativeItemId), out CatalogEntry entry))
            return null;
        if ((flags & AzureDreamsItemManifest.FlagUnidentified) != 0)
            return entry.Name;
        if (!data.QualityCategories.Contains(category))
            return entry.Name;
        if (category == BallCategory)
            return $"{entry.Name} ({quality})";

        return quality == 0
            ? entry.Name
            : $"{entry.Name} {(quality > 0 ? "+" : "-")}{Math.Abs(quality)}";
    }

    private static bool TryDecode(long protocolItemId, out int category, out int nativeItemId)
    {
        category = 0;
        nativeItemId = 0;
        long offset = protocolItemId - AzureDreamsItemManifest.ItemIdBase;
        // Bits 16-18 carry the quality sign and the equipment flags, so an ID
        // no longer fits a ushort. Category and item id are unaffected - they
        // sit below bit 16 - and this only needs the bound widened to match.
        if (offset <= 0 || offset > CatalogOffsetMask)
            return false;

        category = (int)((offset >> CategoryShift) & FieldMask);
        nativeItemId = (int)((offset >> NativeItemShift) & 0x3f);
        return true;
    }

    private static int Key(int category, int nativeItemId) =>
        (category << 8) | nativeItemId;

    private static CatalogData Build()
    {
        var items = new Dictionary<int, CatalogEntry>();
        var qualityCategories = new HashSet<int>();
        try
        {
            using Stream? stream = ClientAssets.OpenItemData();
            if (stream is null)
                return new CatalogData(items, qualityCategories);

            using JsonDocument document = JsonDocument.Parse(stream);
            foreach (JsonProperty categoryEntry in document.RootElement.EnumerateObject())
            {
                if (!int.TryParse(categoryEntry.Name, out int category))
                    continue;

                foreach (JsonProperty itemEntry in categoryEntry.Value.EnumerateObject())
                {
                    // Categories carry a has_quality flag alongside their items.
                    if (itemEntry.NameEquals("has_quality"))
                    {
                        if (itemEntry.Value.ValueKind == JsonValueKind.True)
                            qualityCategories.Add(category);
                        continue;
                    }

                    if (!int.TryParse(itemEntry.Name, out int nativeItemId) ||
                        itemEntry.Value.ValueKind != JsonValueKind.Array ||
                        itemEntry.Value.GetArrayLength() < 2)
                    {
                        continue;
                    }

                    string? name = itemEntry.Value[0].GetString();
                    string? icon = itemEntry.Value[1].GetString();
                    if (!string.IsNullOrWhiteSpace(name) && !string.IsNullOrWhiteSpace(icon))
                        items[Key(category, nativeItemId)] = new CatalogEntry(name, icon);
                }
            }
        }
        catch (Exception)
        {
            // A malformed or missing catalog degrades to unlabelled slots
            // rather than stopping the client from connecting.
        }
        return new CatalogData(items, qualityCategories);
    }

    private readonly record struct CatalogEntry(string Name, string Icon);

    private sealed record CatalogData(
        Dictionary<int, CatalogEntry> Items,
        HashSet<int> QualityCategories);
}

/// <summary>An item the server has sent that the game has not yet taken.</summary>
internal readonly record struct AzureDreamsIncomingItem(
    long ItemId,
    string DisplayName,
    string SenderName);
