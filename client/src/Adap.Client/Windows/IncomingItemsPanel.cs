using System.Drawing.Drawing2D;
using Adap.Client.Games;

namespace Adap.Client.Windows;

/// <summary>
/// How the incoming queue draws itself. The horizontal row of bare icons is the
/// full window's shape, where it has to line up with the tower above the
/// forty-floor grid. Compact mode has no room beside the tower and no separate
/// Queue list, so the same queue turns on its side and carries the item names
/// the list used to hold.
/// </summary>
internal enum IncomingItemsLayout
{
    HorizontalIcons,
    VerticalNamed,
}

/// <summary>
/// The queue of items the server has already sent that the game has not taken
/// yet. Delivery is one at a time through the town mailbox, so the leftmost
/// slot is the item currently going in and the rest are waiting behind it.
/// </summary>
internal sealed class IncomingItemsPanel : Panel
{
    public const int SlotCount = 10;

    private const int SpriteSize = 16;
    private const int SpriteScale = 2;
    private const int SlotSize = SpriteSize * SpriteScale;
    // Ten slots must fit the tower's width so the two line up exactly:
    // 10*32 + 9*5 + 2*6 = 377, inside TowerProgressPanel.PreferredWidth.
    private const int SlotGap = 5;
    private const int ContentPadding = 6;

    public const int PreferredWidth =
        ContentPadding * 2 + SlotCount * SlotSize + (SlotCount - 1) * SlotGap;

    public const int PreferredHeight = ContentPadding * 2 + SlotSize;

    // Vertical metrics. Ten rows and their gaps come to exactly
    // ShopProgressPanel.PreferredHeight, because the two sit side by side in
    // compact mode and a queue that stopped short of the shops left an obvious
    // dead strip under it. 16 + 10*39 + 9*4 = 442. The self-test pins the
    // equality so a change to either panel's rows fails the build rather than
    // quietly reopening that gap.
    private const int VerticalContentPadding = 8;
    private const int NameGap = 10;
    private const int NameWidth = 150;
    private const int VerticalRowHeight = 39;
    private const int VerticalRowGap = 4;

    public const int VerticalPreferredWidth =
        VerticalContentPadding * 2 + SlotSize + NameGap + NameWidth;

    public const int VerticalPreferredHeight =
        VerticalContentPadding * 2 + SlotCount * VerticalRowHeight +
        (SlotCount - 1) * VerticalRowGap;

    private static readonly Color SlotBackground = Color.FromArgb(10, 18, 32);
    private static readonly Color SlotBorder = Color.FromArgb(24, 39, 63);
    private static readonly Color NextSlotBorder = Color.FromArgb(59, 130, 246);
    // The same colour every item name uses everywhere else in the window.
    private static readonly Color ItemNameText = Color.FromArgb(250, 204, 21);

    // Larger than the window's body text: this is the readout a player glances
    // at mid-run, and it now has a 39-px row to sit in.
    private readonly Font _nameFont = new("Segoe UI", 11f);
    private readonly ToolTip _tips = new() { InitialDelay = 250, ReshowDelay = 100 };
    private IReadOnlyList<AzureDreamsIncomingItem> _items = [];
    private IncomingItemsLayout _layout = IncomingItemsLayout.HorizontalIcons;
    private double _scale = UiScale.Natural;

    public IncomingItemsPanel()
    {
        DoubleBuffered = true;
        // The vertical form measures its name column from the panel's own
        // width, so a resize has to repaint rather than stretch stale pixels.
        ResizeRedraw = true;
        BackColor = Color.FromArgb(15, 27, 46);
        ApplyLayoutSize();
    }

    /// <summary>
    /// Switching shape resizes the panel as well as repainting it: both forms
    /// are fixed-size content that the window's own layout is measured from, so
    /// a stale MinimumSize would let the new shape be clipped.
    /// </summary>
    public IncomingItemsLayout LayoutMode
    {
        get => _layout;
        set
        {
            if (_layout == value)
                return;

            _layout = value;
            ApplyLayoutSize();
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
            ApplyLayoutSize();
            Invalidate();
        }
    }

    private int NaturalWidth => _layout == IncomingItemsLayout.VerticalNamed
        ? VerticalPreferredWidth
        : PreferredWidth;

    private int NaturalHeight => _layout == IncomingItemsLayout.VerticalNamed
        ? VerticalPreferredHeight
        : PreferredHeight;

    public int ScaledWidth => UiScale.Round(NaturalWidth, _scale);

    public int ScaledHeight => UiScale.Round(NaturalHeight, _scale);

    private void ApplyLayoutSize()
    {
        var preferred = new Size(ScaledWidth, ScaledHeight);
        // Order matters: shrinking Height below the old MinimumSize is ignored.
        MinimumSize = Size.Empty;
        Height = preferred.Height;
        MinimumSize = preferred;
    }

    public void Update(IReadOnlyList<AzureDreamsIncomingItem> items)
    {
        if (_items.Count == items.Count)
        {
            bool same = true;
            for (int index = 0; index < items.Count; index++)
            {
                if (_items[index] != items[index])
                {
                    same = false;
                    break;
                }
            }
            if (same)
                return;
        }

        _items = items;
        _tips.SetToolTip(this, BuildTip(items));
        Invalidate();
    }

    public void Clear() => Update([]);

    private static string BuildTip(IReadOnlyList<AzureDreamsIncomingItem> items)
    {
        if (items.Count == 0)
            return "Nothing waiting to be delivered.";

        return string.Join(
            Environment.NewLine,
            items.Select((item, index) =>
                index == 0
                    ? $"Delivering: {item.DisplayName} (from {item.SenderName})"
                    : $"{index + 1}. {item.DisplayName} (from {item.SenderName})"));
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        Graphics g = e.Graphics;
        g.InterpolationMode = UiScale.SpriteInterpolation(_scale, SpriteScale);
        g.PixelOffsetMode = PixelOffsetMode.Half;
        g.SmoothingMode = SmoothingMode.None;
        // Everything below draws in natural coordinates; the transform is the
        // only place the scale appears.
        g.ScaleTransform((float)_scale, (float)_scale);

        if (_layout == IncomingItemsLayout.VerticalNamed)
            DrawVertical(g);
        else
            DrawHorizontal(g);
    }

    private void DrawHorizontal(Graphics g)
    {
        int left = ContentPadding;
        int top = ContentPadding;
        for (int slot = 0; slot < SlotCount; slot++)
        {
            DrawSlot(g, new Rectangle(left, top, SlotSize, SlotSize), slot);
            left += SlotSize + SlotGap;
        }
    }

    /// <summary>
    /// The same ten slots stacked, each with the item's name beside it. This
    /// replaces the Queue list in compact mode, so it has to carry the same
    /// contract: top row is the next item to be received, and a delivery
    /// removes the head and moves everything up one.
    /// </summary>
    private void DrawVertical(Graphics g)
    {
        int nameLeft = VerticalContentPadding + SlotSize + NameGap;
        // The names run to the panel's own edge rather than the nominal name
        // width, so a widened window spends the extra pixels on longer names.
        // Width is in device pixels and everything here is in natural ones, so
        // it has to come back through the scale.
        int naturalWidth = (int)Math.Round(Width / _scale);
        int nameWidth = Math.Max(0, naturalWidth - VerticalContentPadding - nameLeft);

        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        using var text = new SolidBrush(ItemNameText);
        using var format = new StringFormat(StringFormatFlags.NoWrap)
        {
            LineAlignment = StringAlignment.Center,
            Trimming = StringTrimming.EllipsisCharacter,
        };

        int top = VerticalContentPadding;
        for (int slot = 0; slot < SlotCount; slot++)
        {
            // The sprite is centred in the row box rather than filling it: the
            // row is taller than the sprite so the ten of them reach the shops'
            // full height.
            DrawSlot(
                g,
                new Rectangle(
                    VerticalContentPadding,
                    top + (VerticalRowHeight - SlotSize) / 2,
                    SlotSize,
                    SlotSize),
                slot);
            if (slot < _items.Count && nameWidth > 0)
            {
                g.DrawString(
                    _items[slot].DisplayName,
                    _nameFont,
                    text,
                    new RectangleF(nameLeft, top, nameWidth, VerticalRowHeight),
                    format);
            }
            top += VerticalRowHeight + VerticalRowGap;
        }
    }

    private void DrawSlot(Graphics g, Rectangle cell, int slot)
    {
        using (var fill = new SolidBrush(SlotBackground))
            g.FillRectangle(fill, cell);

        // The head of the queue is the one actually being delivered.
        bool occupied = slot < _items.Count;
        using (var pen = new Pen(occupied && slot == 0 ? NextSlotBorder : SlotBorder))
            g.DrawRectangle(pen, cell);

        if (!occupied)
            return;

        Image? icon = AzureDreamsItemCatalog.TryGetIcon(_items[slot].ItemId);
        if (icon is not null)
            g.DrawImage(icon, cell);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _nameFont.Dispose();
            _tips.Dispose();
        }
        base.Dispose(disposing);
    }
}
