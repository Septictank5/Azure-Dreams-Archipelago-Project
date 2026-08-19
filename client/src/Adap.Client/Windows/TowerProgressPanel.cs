using System.Drawing.Drawing2D;
using Adap.Client.Games;

namespace Adap.Client.Windows;

/// <summary>
/// The tower drawn as forty floors in two columns, floors 1-20 on the left and
/// 21-40 on the right, each column reading bottom to top. Every floor carries
/// its two item slots; the deepest reachable floor carries the bell, the marked
/// shortcut floors carry a crystal, and floor 40 carries the medal. Koh sits in
/// a gutter to the left of whichever floor the player is standing on.
///
/// The whole tower is always fully visible. The client is meant to be set up
/// once and then left alone, so nothing here scrolls.
/// </summary>
internal sealed class TowerProgressPanel : Panel
{
    private const int SpriteSize = 16;
    private const int SpriteScale = 2;

    // Sprites stay at an exact 2x. Pixel art at a fractional scale samples
    // unevenly and looks ragged, so the size reduction comes out of the row
    // box and the gaps instead.
    private const int SlotSize = SpriteSize * SpriteScale;
    private const int SlotGap = 3;
    private const int RowGap = 2;
    private const int RowHeight = SlotSize + 4;
    private const int FloorLabelWidth = 34;
    private const int MarkerGutter = SlotSize + 3;
    private const int ContentPadding = 8;
    private const int ColumnGap = 10;
    private const int MaxSlotsPerFloor = 3;
    private const int FloorsPerColumn = AzureDreamsTowerProgress.TopFloor / 2;

    private const int RowWidth =
        FloorLabelWidth + SlotGap + MaxSlotsPerFloor * (SlotSize + SlotGap);
    // The bell (deepest reachable floor) and the shortcut crystals are drawn
    // AFTER the row's chests, in a gutter to the row's right. While rows held
    // two chests they sat in the third chest's reserved space; with three
    // chests per floor (2026-08-15) they need a gutter of their own, or the
    // right column's markers fall off the panel edge. One slot is enough:
    // reachable floors are 4/9/14/../39 and shortcut floors 10/20/30, so the
    // two never share a row.
    private const int TrailingMarkerGutter = SlotSize + SlotGap;
    private const int ColumnWidth = MarkerGutter + RowWidth + TrailingMarkerGutter;

    public const int PreferredWidth =
        ContentPadding * 2 + ColumnWidth * 2 + ColumnGap;

    public const int PreferredHeight =
        ContentPadding * 2 + FloorsPerColumn * (RowHeight + RowGap);

    private static readonly Color RowBackground = Color.FromArgb(13, 24, 41);
    private static readonly Color RowBorder = Color.FromArgb(28, 45, 72);
    private static readonly Color ReachableRowBackground = Color.FromArgb(20, 38, 62);
    private static readonly Color ReachableRowBorder = Color.FromArgb(59, 130, 246);
    private static readonly Color GoalRowBorder = Color.FromArgb(250, 204, 21);
    private static readonly Color CurrentRowBorder = Color.FromArgb(74, 222, 128);
    private static readonly Color SlotBackground = Color.FromArgb(10, 18, 32);
    private static readonly Color SlotBorder = Color.FromArgb(24, 39, 63);
    private static readonly Color FloorText = Color.FromArgb(148, 163, 184);
    private static readonly Color ReachableFloorText = Color.FromArgb(226, 232, 240);

    private readonly Font _floorFont = new("Segoe UI", 15f, FontStyle.Regular);
    private AzureDreamsTowerProgress _progress = new(new byte[10], 0, 0, 0);
    private bool _hasProgress;
    private double _scale = UiScale.Natural;
    private bool _live = true;

    public TowerProgressPanel()
    {
        AutoScroll = false;
        DoubleBuffered = true;
        ResizeRedraw = true;
        BackColor = Color.FromArgb(15, 27, 46);
        Padding = new Padding(ContentPadding);
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
            // Released before it is re-pinned: a stale floor would stop the
            // panel shrinking at all.
            MinimumSize = Size.Empty;
            MinimumSize = new Size(ScaledWidth, ScaledHeight);
            Invalidate();
        }
    }

    public int ScaledWidth => UiScale.Round(PreferredWidth, _scale);

    public int ScaledHeight => UiScale.Round(PreferredHeight, _scale);

    /// <summary>Applies fresh state and repaints only when something moved.</summary>
    public void Update(AzureDreamsTowerProgress progress)
    {
        if (_hasProgress && _progress.Equivalent(progress))
            return;

        _progress = progress;
        _hasProgress = true;
        Invalidate();
    }

    public void Clear()
    {
        _hasProgress = false;
        _progress = new AzureDreamsTowerProgress(new byte[10], 0, 0, 0);
        Invalidate();
    }

    /// <summary>
    /// Floors 1-20 fill the left column and 21-40 the right, each counting up
    /// from the bottom of its own column.
    /// </summary>
    private static Rectangle RowBounds(int floor)
    {
        int column = floor <= FloorsPerColumn ? 0 : 1;
        int positionInColumn = floor - column * FloorsPerColumn;
        int top =
            ContentPadding +
            (FloorsPerColumn - positionInColumn) * (RowHeight + RowGap);
        int left =
            ContentPadding + column * (ColumnWidth + ColumnGap) + MarkerGutter;
        return new Rectangle(left, top, RowWidth, RowHeight);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        Graphics g = e.Graphics;
        g.InterpolationMode = UiScale.SpriteInterpolation(_scale, SpriteScale);
        g.PixelOffsetMode = PixelOffsetMode.Half;
        g.SmoothingMode = SmoothingMode.None;
        // Forty floors always fit, at any scale: DrawTower keeps working in
        // natural coordinates and the transform is the only thing that moves.
        g.ScaleTransform((float)_scale, (float)_scale);
        DrawTower(g);
    }

    private void DrawTower(Graphics g)
    {
        for (int floor = AzureDreamsTowerProgress.TopFloor; floor >= 1; floor--)
            DrawFloor(g, floor, RowBounds(floor));
    }

    private void DrawFloor(Graphics g, int floor, Rectangle bounds)
    {
        bool isGoal = floor == AzureDreamsTowerProgress.TopFloor;
        bool isReachable = _hasProgress && floor == _progress.MaxReachableFloor;
        // IsOnLiveFloor, not HasCurrentFloor: in town the floor halfword still
        // reads the last trip's floor, and Koh standing on a floor the player
        // walked out of hours ago is a lie the view used to tell.
        bool isCurrent =
            _hasProgress && _progress.IsOnLiveFloor && floor == _progress.CurrentFloor;

        Color background = isReachable ? ReachableRowBackground : RowBackground;
        Color border = isCurrent
            ? CurrentRowBorder
            : isGoal
                ? GoalRowBorder
                : isReachable
                    ? ReachableRowBorder
                    : RowBorder;

        using (var fill = new SolidBrush(background))
            g.FillRectangle(fill, bounds);
        using (var pen = new Pen(border))
            g.DrawRectangle(pen, bounds);

        // Koh marks the live floor from the gutter, so the player can read
        // where they are and what is left there without opening the game HUD.
        if (isCurrent && ClientAssets.KohHead is not null)
        {
            PanelDimming.DrawIcon(
                g,
                ClientAssets.KohHead,
                new Rectangle(
                    bounds.Left - MarkerGutter,
                    bounds.Top + (bounds.Height - SlotSize) / 2,
                    SlotSize,
                    SlotSize),
                _live);
        }

        using (var text = new SolidBrush(isReachable ? ReachableFloorText : FloorText))
        using (var format = new StringFormat
        {
            Alignment = StringAlignment.Far,
            LineAlignment = StringAlignment.Center,
        })
        {
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            g.DrawString(
                floor.ToString(),
                _floorFont,
                text,
                new Rectangle(bounds.Left + 2, bounds.Top, FloorLabelWidth, bounds.Height),
                format);
        }

        int slotLeft = bounds.Left + FloorLabelWidth + SlotGap;
        int slotTop = bounds.Top + (bounds.Height - SlotSize) / 2;

        if (isGoal)
        {
            // Floor 40 has no items. The medal marks the goal unconditionally.
            DrawSlot(g, new Rectangle(slotLeft, slotTop, SlotSize, SlotSize), ClientAssets.Medal, _live);
            slotLeft += SlotSize + SlotGap;
        }
        else
        {
            // Always three columns wide, whatever the room placed. A room
            // without the carrier system draws nothing in the third one, but
            // it keeps its space: rebuilding the whole tower view's width the
            // moment a room connects is a worse thing to look at than an empty
            // column, and the layout stays the same across rooms.
            int placed = _hasProgress
                ? _progress.SlotsWithChecks
                : AzureDreamsTowerProgress.SlotsPerFloor;
            for (int slot = 0; slot < AzureDreamsTowerProgress.SlotsPerFloor; slot++)
            {
                // A collected slot is left empty; only what remains is drawn.
                bool collected = slot >= placed ||
                    (_hasProgress && _progress.IsCollected(floor, slot));
                DrawSlot(
                    g,
                    new Rectangle(slotLeft, slotTop, SlotSize, SlotSize),
                    collected ? null : ClientAssets.Chest,
                    _live);
                slotLeft += SlotSize + SlotGap;
            }
        }

        if (isReachable)
        {
            DrawSlot(
                g,
                new Rectangle(slotLeft, slotTop, SlotSize, SlotSize),
                ClientAssets.Bell,
                _live);
            slotLeft += SlotSize + SlotGap;
        }

        foreach ((int shortcutFloor, int requiredLevel) in AzureDreamsTowerProgress.Shortcuts)
        {
            if (shortcutFloor != floor)
                continue;

            // Always shown, red until its keycard requirement is met.
            Image? crystal = _hasProgress && _progress.IsShortcutUnlocked(requiredLevel)
                ? ClientAssets.Crystal
                : ClientAssets.LockedCrystal;
            DrawSlot(g, new Rectangle(slotLeft, slotTop, SlotSize, SlotSize), crystal, _live);
        }
    }

    /// <summary>
    /// One slot box and, if it holds anything, its sprite. The box is
    /// furniture and is always drawn in its own colours; only the sprite
    /// answers to <paramref name="live"/>. See <see cref="PanelDimming"/>.
    /// </summary>
    private static void DrawSlot(Graphics g, Rectangle cell, Image? image, bool live)
    {
        using (var fill = new SolidBrush(SlotBackground))
            g.FillRectangle(fill, cell);
        using (var pen = new Pen(SlotBorder))
            g.DrawRectangle(pen, cell);

        if (image is not null)
            PanelDimming.DrawIcon(g, image, cell, live);
    }

    /// <summary>
    /// Draws the whole tower to a PNG so the layout can be reviewed without a
    /// running game, matching the client's other diagnostic commands.
    /// </summary>
    public static int RenderToFile(
        string path,
        int keycardLevel,
        int collectedSlots,
        int currentFloor,
        bool inTower = true)
    {
        byte[] mask = new byte[AzureDreamsReceiveState.LocationMaskSize];
        for (int index = 0; index < collectedSlots; index++)
        {
            if (AzureDreamsReceiveState.TryGetTowerMaskPosition(index, out int byteIndex, out byte bit))
                mask[byteIndex] |= bit;
        }

        using var panel = new TowerProgressPanel
        {
            _progress = new AzureDreamsTowerProgress(
                mask, keycardLevel, currentFloor, 0, inTower && currentFloor >= 1),
            _hasProgress = true,
        };

        using var bitmap = new Bitmap(PreferredWidth, PreferredHeight);
        using (Graphics g = Graphics.FromImage(bitmap))
        {
            g.Clear(Color.FromArgb(15, 27, 46));
            g.InterpolationMode = InterpolationMode.NearestNeighbor;
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.SmoothingMode = SmoothingMode.None;
            panel.DrawTower(g);
        }

        bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        Console.WriteLine(
            $"Wrote {path} ({PreferredWidth}x{PreferredHeight}, keycard {keycardLevel}, " +
            $"{collectedSlots} slots collected, floor {currentFloor}, " +
            $"{(inTower ? "in tower" : "in town - no live position")}).");
        return 0;
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
            _floorFont.Dispose();
        base.Dispose(disposing);
    }
}
