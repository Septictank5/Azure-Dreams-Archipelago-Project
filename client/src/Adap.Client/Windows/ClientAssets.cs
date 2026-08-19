using System.Drawing.Imaging;
using System.Reflection;

namespace Adap.Client.Windows;

/// <summary>
/// Loads the embedded UI sprites once. The published client is a single file,
/// so every image ships inside the executable rather than beside it.
/// </summary>
internal static class ClientAssets
{
    private const string ResourcePrefix = "Adap.Client.Assets.";

    private static readonly Lazy<Image?> ChestImage = new(() => Load("chest.png"));
    private static readonly Lazy<Image?> CrystalImage = new(() => Load("crystal.png"));
    private static readonly Lazy<Image?> KohHeadImage = new(() => Load("kohhead.png"));
    private static readonly Lazy<Image?> MedalImage = new(() => Load("medal.png"));
    private static readonly Lazy<Image?> BellImage = new(() => Load("bell.png"));
    private static readonly Lazy<Image?> LockedCrystalImage =
        new(() => Tint(CrystalImage.Value, 0.95f, 0.16f, 0.18f));
    private static readonly Lazy<Image?> LockImage = new(() => Load("lock.png"));
    // The three sands are one sprite in three colours. The art is the red
    // pouch, which is why Red is the untinted one.
    private static readonly Lazy<Image?> RedSandImage = new(() => Load("sand.png"));
    // Tinting collapses the sprite to luminance first, and the red pouch's
    // luminance is low - so a factor of 1 comes out nearly black. Both of
    // these are scaled well past 1 to land at the brightness the red art
    // already has; the matrix clamps whatever overshoots.
    private static readonly Lazy<Image?> BlueSandImage =
        new(() => Tint(RedSandImage.Value, 0.55f, 1.05f, 2.10f));
    private static readonly Lazy<Image?> WhiteSandImage =
        new(() => Tint(RedSandImage.Value, 1.95f, 1.95f, 1.95f));
    // Until a lock sprite is supplied, a red chest still reads as "not yet
    // available" and is clearly distinct from an ordinary or emptied slot.
    private static readonly Lazy<Image?> LockedSlotImage =
        new(() => LockImage.Value ?? Tint(ChestImage.Value, 0.95f, 0.16f, 0.18f));

    /// <summary>An uncollected tower slot.</summary>
    public static Image? Chest => ChestImage.Value;

    /// <summary>A shortcut whose keycard requirement is already met.</summary>
    public static Image? Crystal => CrystalImage.Value;

    /// <summary>A shortcut still short of its keycard requirement.</summary>
    public static Image? LockedCrystal => LockedCrystalImage.Value;

    /// <summary>Marks the floor the player is standing on right now.</summary>
    public static Image? KohHead => KohHeadImage.Value;

    /// <summary>The deepest floor the current keycard level allows.</summary>
    public static Image? Bell => BellImage.Value;

    /// <summary>The floor 40 goal marker.</summary>
    public static Image? Medal => MedalImage.Value;

    /// <summary>Red Sand: how far the smith may temper a weapon.</summary>
    public static Image? RedSand => RedSandImage.Value;

    /// <summary>Blue Sand: how far the smith may temper a shield.</summary>
    public static Image? BlueSand => BlueSandImage.Value;

    /// <summary>White Sand: how full the charger fills a spell ball.</summary>
    public static Image? WhiteSand => WhiteSandImage.Value;

    /// <summary>A slot in a shop the player cannot reach yet.</summary>
    public static Image? LockedSlot => LockedSlotImage.Value;

    /// <summary>True while the dedicated lock sprite is still missing.</summary>
    public static bool UsingLockFallback => LockImage.Value is null;

    private static readonly Dictionary<string, Image?> NamedImages = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>
    /// Resolves an icon named by data.json. Results are cached, including the
    /// misses, so an unmapped item does not retry the lookup every repaint.
    /// </summary>
    public static Image? ByFileName(string? fileName)
    {
        if (string.IsNullOrWhiteSpace(fileName))
            return null;

        lock (NamedImages)
        {
            if (NamedImages.TryGetValue(fileName, out Image? cached))
                return cached;

            Image? loaded = Load(fileName);
            NamedImages[fileName] = loaded;
            return loaded;
        }
    }

    /// <summary>Reads the embedded data.json that names every item's icon.</summary>
    public static Stream? OpenItemData() =>
        typeof(ClientAssets).Assembly.GetManifestResourceStream(ResourcePrefix + "data.json");

    private static Image? Load(string fileName)
    {
        try
        {
            Assembly assembly = typeof(ClientAssets).Assembly;
            using Stream? stream =
                assembly.GetManifestResourceStream(ResourcePrefix + fileName);
            if (stream is null)
                return null;

            // Image.FromStream keeps the stream alive for the image's lifetime,
            // so copy into memory the bitmap owns outright.
            using var buffer = new MemoryStream();
            stream.CopyTo(buffer);
            buffer.Position = 0;
            using var loaded = Image.FromStream(buffer);
            return new Bitmap(loaded);
        }
        catch (Exception)
        {
            // Missing or unreadable art must never stop the client from
            // connecting. Callers treat null as "draw the placeholder".
            return null;
        }
    }

    /// <summary>
    /// Recolours a sprite by luminance so it reads as a single hue while
    /// keeping its shading. Used to show a locked shortcut in red.
    /// </summary>
    private static Image? Tint(Image? source, float red, float green, float blue)
    {
        if (source is null)
            return null;

        try
        {
            var tinted = new Bitmap(source.Width, source.Height, PixelFormat.Format32bppArgb);
            tinted.SetResolution(source.HorizontalResolution, source.VerticalResolution);

            // Collapse to luminance, then scale that grey into the target hue.
            var matrix = new ColorMatrix(
            [
                [0.299f * red, 0.299f * green, 0.299f * blue, 0f, 0f],
                [0.587f * red, 0.587f * green, 0.587f * blue, 0f, 0f],
                [0.114f * red, 0.114f * green, 0.114f * blue, 0f, 0f],
                [0f, 0f, 0f, 1f, 0f],
                [0f, 0f, 0f, 0f, 1f],
            ]);
            using var attributes = new ImageAttributes();
            attributes.SetColorMatrix(matrix);

            using Graphics graphics = Graphics.FromImage(tinted);
            graphics.Clear(Color.Transparent);
            graphics.DrawImage(
                source,
                new Rectangle(0, 0, source.Width, source.Height),
                0,
                0,
                source.Width,
                source.Height,
                GraphicsUnit.Pixel,
                attributes);
            return tinted;
        }
        catch (Exception)
        {
            return null;
        }
    }
}
