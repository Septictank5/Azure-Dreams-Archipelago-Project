using System.Drawing.Drawing2D;
using Adap.Client.Games;

namespace Adap.Client.Windows;

/// <summary>
/// Both town shops, each as five rows of two slots. A chest marks a slot the
/// player has not bought yet and an emptied slot marks one they have. A shop
/// whose keycard requirement is unmet shows locks instead, because none of its
/// slots can be reached regardless of what is in them.
/// </summary>
internal sealed class ShopProgressPanel : Panel
{
    private const int SpriteSize = 16;
    private const int SpriteScale = 2;
    private const int SlotSize = SpriteSize * SpriteScale;
    private const int SlotGap = 3;
    private const int RowGap = 2;
    private const int RowHeight = SlotSize + 4;
    private const int ColumnsPerShop = 2;
    private const int RowsPerShop =
        AzureDreamsTowerProgress.SlotsPerShop / ColumnsPerShop;
    private const int ContentPadding = 8;
    private const int HeadingHeight = 18;
    private const int ShopGap = 10;

    private const int GridWidth =
        ColumnsPerShop * SlotSize + (ColumnsPerShop - 1) * SlotGap;
    private const int ShopHeight =
        HeadingHeight + RowsPerShop * (RowHeight + RowGap);

    public const int PreferredWidth = ContentPadding * 2 + GridWidth;
    public const int PreferredHeight =
        ContentPadding * 2 +
        AzureDreamsTowerProgress.ShopCount * ShopHeight +
        (AzureDreamsTowerProgress.ShopCount - 1) * ShopGap;

    private static readonly Color SlotBackground = Color.FromArgb(10, 18, 32);
    private static readonly Color SlotBorder = Color.FromArgb(24, 39, 63);
    private static readonly Color LockedSlotBorder = Color.FromArgb(74, 34, 42);
    private static readonly Color HeadingText = Color.FromArgb(226, 232, 240);
    private static readonly Color LockedHeadingText = Color.FromArgb(148, 163, 184);

    private readonly Font _headingFont = new("Segoe UI", 8.5f, FontStyle.Bold);
    private readonly ToolTip _tips = new() { InitialDelay = 250 };
    private AzureDreamsTowerProgress _progress = new(new byte[10], 0, 0, 0);
    private bool _hasProgress;
    private double _scale = UiScale.Natural;
    private bool _live = true;

    public ShopProgressPanel()
    {
        DoubleBuffered = true;
        ResizeRedraw = true;
        BackColor = Color.FromArgb(15, 27, 46);
        MinimumSize = new Size(PreferredWidth, PreferredHeight);
    }

    /// <summary>See <see cref="PanelDimming"/>.</summary>
    public bool Live
    {
        get => _live;
        set
        {
            if (_live == value)
                return;

            _live = value;
            Invalidate();
        }
    }

    /// <summary>See <see cref="UiScale"/>.</summary>
    public double Scale
    {
        get => _scale;
        set
        {
            double clamped = UiScale.Clamp(value);
            if (Math.Abs(_scale - clamped) < 0.001)
                return;

            _scale = clamped;
            MinimumSize = Size.Empty;
            Height = ScaledHeight;
            MinimumSize = new Size(ScaledWidth, ScaledHeight);
            Invalidate();
        }
    }

    public int ScaledWidth => UiScale.Round(PreferredWidth, _scale);

    public int ScaledHeight => UiScale.Round(PreferredHeight, _scale);

    public void Update(AzureDreamsTowerProgress progress)
    {
        if (_hasProgress &&
            _progress.ShopMask == progress.ShopMask &&
            _progress.KeycardLevel == progress.KeycardLevel)
        {
            return;
        }

        _progress = progress;
        _hasProgress = true;
        _tips.SetToolTip(this, BuildTip(progress));
        Invalidate();
    }

    public void Clear()
    {
        _hasProgress = false;
        _progress = new AzureDreamsTowerProgress(new byte[10], 0, 0, 0);
        _tips.SetToolTip(this, string.Empty);
        Invalidate();
    }

    private static string BuildTip(AzureDreamsTowerProgress progress)
    {
        var lines = new List<string>();
        for (int shop = 0; shop < AzureDreamsTowerProgress.ShopCount; shop++)
        {
            if (!progress.IsShopUnlocked(shop))
            {
                lines.Add(
                    $"{AzureDreamsTowerProgress.ShopNames[shop]}: locked until keycard " +
                    $"level {AzureDreamsTowerProgress.RequiredKeycardLevel(shop)}.");
                continue;
            }

            int bought = 0;
            for (int slot = 0; slot < AzureDreamsTowerProgress.SlotsPerShop; slot++)
            {
                if (progress.IsShopSlotPurchased(shop, slot))
                    bought++;
            }
            lines.Add(
                $"{AzureDreamsTowerProgress.ShopNames[shop]}: {bought} of " +
                $"{AzureDreamsTowerProgress.SlotsPerShop} bought.");
        }
        return string.Join(Environment.NewLine, lines);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        Graphics g = e.Graphics;
        g.InterpolationMode = UiScale.SpriteInterpolation(_scale, SpriteScale);
        g.PixelOffsetMode = PixelOffsetMode.Half;
        g.SmoothingMode = SmoothingMode.None;
        g.ScaleTransform((float)_scale, (float)_scale);
        DrawShops(g);
    }

    private void DrawShops(Graphics g)
    {
        int top = ContentPadding;
        for (int shop = 0; shop < AzureDreamsTowerProgress.ShopCount; shop++)
        {
            DrawShop(g, shop, top);
            top += ShopHeight + ShopGap;
        }
    }

    private void DrawShop(Graphics g, int shop, int top)
    {
        bool unlocked = !_hasProgress || _progress.IsShopUnlocked(shop);
        using (var text = new SolidBrush(unlocked ? HeadingText : LockedHeadingText))
        {
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            g.DrawString(
                AzureDreamsTowerProgress.ShopNames[shop],
                _headingFont,
                text,
                new PointF(ContentPadding, top));
        }

        int rowTop = top + HeadingHeight;
        for (int row = 0; row < RowsPerShop; row++)
        {
            for (int column = 0; column < ColumnsPerShop; column++)
            {
                int slot = row * ColumnsPerShop + column;
                var cell = new Rectangle(
                    ContentPadding + column * (SlotSize + SlotGap),
                    rowTop,
                    SlotSize,
                    SlotSize);

                Image? image;
                Color border = SlotBorder;
                if (!unlocked)
                {
                    // The whole shop is out of reach; what is inside does not
                    // matter yet.
                    image = ClientAssets.LockedSlot;
                    border = LockedSlotBorder;
                }
                else
                {
                    bool purchased = _hasProgress && _progress.IsShopSlotPurchased(shop, slot);
                    image = purchased ? null : ClientAssets.Chest;
                }

                using (var fill = new SolidBrush(SlotBackground))
                    g.FillRectangle(fill, cell);
                using (var pen = new Pen(border))
                    g.DrawRectangle(pen, cell);
                if (image is not null)
                    PanelDimming.DrawIcon(g, image, cell, _live);
            }
            rowTop += RowHeight + RowGap;
        }
    }

    /// <summary>Renders both shops to a PNG for layout review.</summary>
    public static int RenderToFile(string path, int keycardLevel, uint shopMask)
    {
        using var panel = new ShopProgressPanel
        {
            _progress = new AzureDreamsTowerProgress(new byte[10], keycardLevel, 0, shopMask),
            _hasProgress = true,
        };
        using var bitmap = new Bitmap(PreferredWidth, PreferredHeight);
        using (Graphics g = Graphics.FromImage(bitmap))
        {
            g.Clear(Color.FromArgb(15, 27, 46));
            g.InterpolationMode = InterpolationMode.NearestNeighbor;
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.SmoothingMode = SmoothingMode.None;
            panel.DrawShops(g);
        }
        bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        Console.WriteLine(
            $"Wrote {path} ({PreferredWidth}x{PreferredHeight}, keycard {keycardLevel}, " +
            $"shop mask 0x{shopMask:x8}).");
        return 0;
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _headingFont.Dispose();
            _tips.Dispose();
        }
        base.Dispose(disposing);
    }
}
