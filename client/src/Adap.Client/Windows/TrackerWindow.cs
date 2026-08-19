using Adap.Client.Archipelago;
using Adap.Client.Games;

namespace Adap.Client.Windows;

/// <summary>
/// Everything a live session is actually watched for, in a window of its own.
///
/// <para>This is the old compact mode, popped out. The connection screen is a
/// setup screen - server, slot, patch, emulator - and a player touches it once
/// per session; the tracker is what stays on a second monitor for the next
/// three hours. Splitting them means neither has to compromise: the setup
/// screen stopped carrying a forty-floor tower it had no room for, and the
/// tracker stopped carrying eight fields nobody reads mid-run.</para>
///
/// <para>Closing the tracker HIDES it rather than disposing it. Every panel in
/// here holds live session state that arrives whether the window is open or
/// not, so a close that threw the state away would silently reset the run's
/// tracker; reopening is a <see cref="Form.Show()"/> and shows exactly what it
/// was showing.</para>
/// </summary>
internal sealed class TrackerWindow : Form
{
    private const int ShellPadding = 16;
    private const int SectionGap = ShellPadding;

    /// <summary>
    /// The width the left column starts at. The incoming queue only needs its
    /// own preferred width, but the activity feed sits under it and a feed too
    /// narrow to hold "Sandknight sent Pita Fruit to Wugga." is not worth
    /// keeping. Extra window width goes here.
    /// </summary>
    private const int LeftColumnMinimumWidth = 420;

    /// <summary>
    /// The natural width of everything that scales: the left column the
    /// incoming queue and activity feed share, plus the two fixed-width panels
    /// beside it.
    /// </summary>
    private const int ScalableWidth =
        LeftColumnMinimumWidth + ShopProgressPanel.PreferredWidth +
        TowerProgressPanel.PreferredWidth;

    // The feed's line prefix, as widths in its monospace font. "[HH:mm:ss] "
    // is eleven characters; the tags are padded so the message column lines up
    // whichever tag a line carries.
    private const int TimestampCharacters = 11;
    private const string SendTag = "SEND     ";
    private const string GiftTag = "GIFT     ";
    private const string CheckpointTag = "CHECKPOINT  ";

    /// <summary>The gap between two readouts in the status strip.</summary>
    private const int GroupGap = 22;

    /// <summary>
    /// The gap between the sands, which are one readout in three parts and so
    /// sit closer together than they do to the counters beside them.
    /// </summary>
    private const int SandGap = 12;

    internal const int ActivityLineLimit = 2000;
    internal const int ActivityLinesKept = 1500;

    private readonly TowerProgressPanel _tower =
        new() { Dock = DockStyle.Top, Margin = Padding.Empty };
    private readonly ShopProgressPanel _shops =
        new() { Dock = DockStyle.Top, Margin = Padding.Empty };
    private readonly IncomingItemsPanel _incoming = new()
    {
        Dock = DockStyle.Top,
        Margin = Padding.Empty,
        LayoutMode = IncomingItemsLayout.VerticalNamed,
    };
    private readonly RichTextBox _activity = new()
    {
        Dock = DockStyle.Fill,
        ReadOnly = true,
        Name = "ActivityFeed",
        BorderStyle = BorderStyle.FixedSingle,
        BackColor = ClientPalette.InputBackground,
        ForeColor = ClientPalette.PrimaryText,
        ScrollBars = RichTextBoxScrollBars.Vertical,
        DetectUrls = false,
        Font = new Font(FontFamily.GenericMonospace, 9.5f),
    };
    private readonly Label _keycard = new()
    {
        AutoSize = true,
        Name = "TrackerKeycardValue",
        Text = "-/8",
        ForeColor = ClientPalette.PrimaryText,
        Margin = new Padding(2, 7, 0, 0),
    };
    private readonly Label _sendToken = new()
    {
        AutoSize = true,
        Name = "TrackerSendTokenValue",
        Text = "-",
        ForeColor = ClientPalette.PrimaryText,
        Margin = new Padding(2, 7, 0, 0),
    };
    // The three sands: one sprite in three colours, each with the level it
    // has bought. They are levels the smith and the charger read out of the
    // save, so they belong beside the keycards rather than in a panel.
    private readonly SandReadout _redSand = new("Red", ClientAssets.RedSand);
    private readonly SandReadout _blueSand = new("Blue", ClientAssets.BlueSand);
    private readonly SandReadout _whiteSand = new("White", ClientAssets.WhiteSand);

    private TableLayoutPanel? _shell;
    private Control? _statusStrip;
    // Headings and the status strip are text at a fixed font: they are the
    // part of the window that does not scale, so the scale is solved against
    // what is left after them.
    private int _headingHeight;
    private bool _applyingScale;
    private int _activityCharacterWidth;
    private int _activityLineCount;
    private bool _placed;
    // Nothing in here means what it looks like until the client has a room, a
    // game, and a progression snapshot out of that game. See PanelDimming.
    private bool _live;

    public TrackerWindow()
    {
#if ADAP_STABLE
        Text = "Azure Dreams Tracker";
#else
        Text = "Azure Dreams Tracker (DEV)";
#endif
        StartPosition = FormStartPosition.Manual;
        BackColor = ClientPalette.WindowBackground;
        ForeColor = ClientPalette.PrimaryText;
        Font = new Font("Segoe UI", 9.5f);
        MinimumSize = Size.Empty;

        _shell = BuildShell();
        Controls.Add(_shell);
        ApplyLiveState();

        // Both dimensions start at the exact natural size, so the first solved
        // scale is 1.0 and the default window is the unscaled one. A window
        // even slightly short of natural would scale everything down to fit
        // it, which is how the default came out at 0.91 once. The chrome has
        // to be laid out before it can be measured, hence the two passes.
        ClientSize = new Size(
            ShellPadding * 2 + SectionGap + ScalableWidth,
            ShellPadding * 2 + TowerProgressPanel.PreferredHeight);
        PerformLayout();
        ClientSize = new Size(
            ClientSize.Width,
            FixedChrome().Height + TowerProgressPanel.PreferredHeight);
        ApplyScale();
        FitToTower();
        MinimumSize = MinimumWindowSize();
    }

    /// <summary>
    /// True while the window is up. The client keeps feeding a hidden tracker,
    /// so this says whether the player can see it, not whether it is current.
    /// </summary>
    public bool IsOpen => Visible;

    /// <summary>
    /// Shows the tracker, or brings an already-open one forward. First open
    /// places it beside its owner rather than on top of it: the two windows
    /// are meant to be used together.
    /// </summary>
    public void Present(Form owner)
    {
        if (!Visible)
        {
            PlaceBeside(owner);
            // Show(), NOT Show(owner). An owned form minimises with its owner,
            // and the setup screen is exactly what a player minimises once the
            // session is up - taking the tracker down with it was the opposite
            // of the point. Ownerless also means its own taskbar button, which
            // is what a player restores it from.
            Show();
        }
        else if (WindowState == FormWindowState.Minimized)
        {
            WindowState = FormWindowState.Normal;
        }

        Activate();
    }

    private void PlaceBeside(Form owner)
    {
        if (_placed)
            return;

        _placed = true;
        Rectangle screen = Screen.FromControl(owner).WorkingArea;
        // To the right of the connection screen if it fits there, otherwise
        // pinned inside the working area.
        int left = owner.Right + 8;
        if (left + Width > screen.Right)
            left = Math.Max(screen.Left, screen.Right - Width);
        int top = Math.Max(screen.Top, Math.Min(owner.Top, screen.Bottom - Height));
        Location = new Point(left, top);
    }

    /// <summary>
    /// A closed tracker is a hidden tracker: the session keeps updating it and
    /// reopening has to show the run, not a blank window. The owner disposes
    /// it when the app itself closes.
    /// </summary>
    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            Hide();
            return;
        }

        base.OnFormClosing(e);
    }

    /// <summary>
    /// Three columns and a feed:
    ///
    /// <code>
    /// | status: room - game - keycards - send tokens      |
    /// | Incoming (named) | Shops  | Tower                 |
    /// | Activity ....................     | (tower)       |
    /// </code>
    ///
    /// The shops are barely half the tower's height, so the activity feed
    /// spans the incoming and shop columns and takes back the dead space that
    /// would otherwise sit under the shops. The tower spans both content rows
    /// because it is the tallest thing in the window and everything else is
    /// measured around it.
    /// </summary>
    private TableLayoutPanel BuildShell()
    {
        var shell = new TableLayoutPanel
        {
            Name = "TrackerShell",
            Dock = DockStyle.Fill,
            Padding = new Padding(ShellPadding),
            BackColor = ClientPalette.WindowBackground,
            ColumnCount = 3,
            RowCount = 3,
        };
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        shell.ColumnStyles.Add(
            new ColumnStyle(SizeType.Absolute, ShopProgressPanel.PreferredWidth + SectionGap));
        shell.ColumnStyles.Add(
            new ColumnStyle(SizeType.Absolute, TowerProgressPanel.PreferredWidth));

        using var boldFont = new Font(Font, FontStyle.Bold);
        _headingHeight = TextRenderer.MeasureText("Incoming", boldFont).Height +
            ClientControls.HeadingSpacing;
        // One row height for the incoming queue and the shops beside it. They
        // are built to the same height on purpose, so this is a Math.Max only
        // to keep a future change to either from clipping the other.
        int contentRowHeight = _headingHeight + Math.Max(
            ShopProgressPanel.PreferredHeight,
            IncomingItemsPanel.VerticalPreferredHeight);

        shell.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        shell.RowStyles.Add(new RowStyle(SizeType.Absolute, contentRowHeight));
        shell.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        TableLayoutPanel incomingHost = CreateColumn("Incoming", rightGap: SectionGap);
        TableLayoutPanel shopsHost = CreateColumn("Shops", rightGap: SectionGap);
        TableLayoutPanel towerHost = CreateColumn("Tower", rightGap: 0);
        TableLayoutPanel activityHost = CreateColumn("Activity", rightGap: SectionGap);
        // Only the activity feed absorbs leftover height. The other three carry
        // content sized from the scale, so height the scale did not ask for
        // would just be dead space inside their panels.
        activityHost.RowStyles[1] = new RowStyle(SizeType.Percent, 100);
        activityHost.Margin = new Padding(0, SectionGap, SectionGap, 0);

        incomingHost.Controls.Add(_incoming, 0, 1);
        shopsHost.Controls.Add(_shops, 0, 1);
        towerHost.Controls.Add(_tower, 0, 1);
        activityHost.Controls.Add(_activity, 0, 1);

        _statusStrip = CreateStatusStrip();
        shell.Controls.Add(_statusStrip, 0, 0);
        shell.SetColumnSpan(_statusStrip, 3);
        shell.Controls.Add(incomingHost, 0, 1);
        shell.Controls.Add(shopsHost, 1, 1);
        shell.Controls.Add(towerHost, 2, 1);
        shell.SetRowSpan(towerHost, 2);
        shell.Controls.Add(activityHost, 0, 2);
        shell.SetColumnSpan(activityHost, 2);
        return shell;
    }

    /// <summary>A heading over one panel, which is the shape every column takes.</summary>
    private TableLayoutPanel CreateColumn(string heading, int rightGap)
    {
        var column = new TableLayoutPanel
        {
            Name = "Tracker" + heading + "Column",
            Dock = DockStyle.Fill,
            BackColor = ClientPalette.WindowBackground,
            ColumnCount = 1,
            RowCount = 2,
            Margin = new Padding(0, 0, rightGap, 0),
        };
        column.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Label label = ClientControls.CreateSectionHeading(Font, heading, topMargin: 0);
        label.Name = "Tracker" + heading + "Heading";
        column.Controls.Add(label, 0, 0);
        return column;
    }

    /// <summary>
    /// The two live states and the two session counters. These are the only
    /// things the tracker carries that are not drawn panels, and they are here
    /// because a player watching the tracker still needs to know the room and
    /// the game are both up.
    /// </summary>
    private Control CreateStatusStrip()
    {
        var strip = new TableLayoutPanel
        {
            Name = "TrackerStatusStrip",
            Dock = DockStyle.Fill,
            AutoSize = true,
            BackColor = ClientPalette.PanelBackground,
            Padding = new Padding(12, 4, 12, 4),
            ColumnCount = 1,
            RowCount = 1,
            Margin = new Padding(0, 0, 0, SectionGap),
        };
        strip.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        strip.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        var states = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            AutoSize = true,
            BackColor = ClientPalette.PanelBackground,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = false,
            Margin = Padding.Empty,
        };
        // No separators between the readouts. There were dots between the two
        // counters and none between the sands, which read as an accident, and
        // the strip is where the next counter will have to go - the space is
        // worth more than the punctuation. A wider gap does the same job.
        states.Controls.Add(
            ClientControls.CreateStatusLabel("Keycards", ClientPalette.SecondaryText));
        states.Controls.Add(_keycard);
        Label tokenLabel =
            ClientControls.CreateStatusLabel("Send Tokens", ClientPalette.SecondaryText);
        tokenLabel.Margin = new Padding(GroupGap, tokenLabel.Margin.Top, 0, 0);
        states.Controls.Add(tokenLabel);
        states.Controls.Add(_sendToken);
        // No labels on the sands either: three of them with names would be
        // most of the strip, and the colour IS the name. What each one buys is
        // in its tooltip.
        bool firstSand = true;
        foreach (SandReadout sand in new[] { _redSand, _blueSand, _whiteSand })
        {
            sand.Icon.Margin = new Padding(
                firstSand ? GroupGap : SandGap, sand.Icon.Margin.Top, 0, 0);
            firstSand = false;
            states.Controls.Add(sand.Icon);
            states.Controls.Add(sand.Value);
        }

        // Reserve the width of the widest count the readout can reach, so a
        // second digit grows into space that was always there. It also holds
        // the minimum-width floor, so reaching a second digit cannot push the
        // strip past the width the window is allowed to shrink to.
        _sendToken.MinimumSize = new Size(
            TextRenderer.MeasureText("88", _sendToken.Font ?? Font).Width, 0);

        strip.Controls.Add(states, 0, 0);
        return strip;
    }

    // ---------------------------------------------------------------- state

    /// <summary>
    /// Whether the drawn panels are showing a real session. Everything in them
    /// is colour-coded, and before the client knows the player's progression
    /// those colours are the DEFAULTS rather than the truth, so they are
    /// drained until room, game and a progression snapshot are all in hand.
    /// See <see cref="PanelDimming"/>.
    /// </summary>
    public void SetLive(bool live)
    {
        if (_live == live)
            return;

        _live = live;
        ApplyLiveState();
    }

    public bool IsLive => _live;

    private void ApplyLiveState()
    {
        _tower.Live = _live;
        _shops.Live = _live;
    }

    public void UpdateIncoming(IReadOnlyList<AzureDreamsIncomingItem> items) =>
        _incoming.Update(items);

    public void UpdateTower(AzureDreamsTowerProgress progress)
    {
        _tower.Update(progress);
        _shops.Update(progress);
    }

    public void Clear()
    {
        _tower.Clear();
        _shops.Clear();
        _incoming.Clear();
    }

    public void SetKeycardReadout(string text, Color color)
    {
        if (_keycard.Text != text)
            _keycard.Text = text;
        if (_keycard.ForeColor != color)
            _keycard.ForeColor = color;
    }

    public void SetSendTokenReadout(string text, Color color)
    {
        if (_sendToken.Text != text)
            _sendToken.Text = text;
        if (_sendToken.ForeColor != color)
            _sendToken.ForeColor = color;
    }

    /// <summary>
    /// The three sand levels, each as `level/3` with what it buys in the
    /// tooltip. A maxed sand is gold for the same reason eight keycards are:
    /// there is nothing left to want from it.
    /// </summary>
    public void SetSandReadouts(AzureDreamsTowerProgress progress)
    {
        _redSand.Set(
            progress.WeaponTemperLevel,
            $"Red Sand: the smith tempers weapons to " +
            $"+{AzureDreamsTowerProgress.TemperCaps[
                Math.Clamp(progress.WeaponTemperLevel, 0, AzureDreamsTowerProgress.MaximumSandLevel)]}.");
        _blueSand.Set(
            progress.ShieldTemperLevel,
            $"Blue Sand: the smith tempers shields to " +
            $"+{AzureDreamsTowerProgress.TemperCaps[
                Math.Clamp(progress.ShieldTemperLevel, 0, AzureDreamsTowerProgress.MaximumSandLevel)]}.");
        int charges = AzureDreamsTowerProgress.BallChargeUsesPerVisit[
            Math.Clamp(progress.BallChargeLevel, 0, AzureDreamsTowerProgress.MaximumSandLevel)];
        _whiteSand.Set(
            progress.BallChargeLevel,
            "White Sand: the charger adds " +
            (charges == 1 ? "1 charge" : $"{charges} charges") +
            $" per town visit, to a ceiling of {AzureDreamsTowerProgress.BallChargeCeiling} on any one ball.");
    }

    /// <summary>Back to the dashes a session that is not running shows.</summary>
    public void ClearSandReadouts()
    {
        _redSand.Clear();
        _blueSand.Clear();
        _whiteSand.Clear();
    }

    /// <summary>
    /// One sand: its sprite and the level beside it. A little class rather
    /// than six fields, because there are three of them and they differ only
    /// by colour.
    /// </summary>
    private sealed class SandReadout
    {
        private readonly ToolTip _tip = new() { InitialDelay = 250 };

        public SandReadout(string name, Image? sprite)
        {
            Icon = new PictureBox
            {
                Name = "Tracker" + name + "SandIcon",
                Image = sprite,
                // The sprite is 16px art; drawn at its own size it stays
                // crisp, which is the whole reason the panels pin 2x.
                Size = new Size(16, 16),
                SizeMode = PictureBoxSizeMode.CenterImage,
                BackColor = Color.Transparent,
                Margin = new Padding(8, 6, 0, 0),
            };
            Value = new Label
            {
                AutoSize = true,
                Name = "Tracker" + name + "SandValue",
                Text = Dash,
                ForeColor = ClientPalette.PrimaryText,
                Margin = new Padding(3, 7, 0, 0),
            };
            // Reserve the widest reading, so reaching a level does not shove
            // the next sand along the strip.
            Value.MinimumSize = new Size(
                TextRenderer.MeasureText("0/0", Value.Font ?? SystemFonts.DefaultFont).Width, 0);
        }

        private const string Dash = "-";

        public PictureBox Icon { get; }

        public Label Value { get; }

        public void Set(int level, string tip)
        {
            int clamped = Math.Clamp(level, 0, AzureDreamsTowerProgress.MaximumSandLevel);
            string text = $"{clamped}/{AzureDreamsTowerProgress.MaximumSandLevel}";
            if (Value.Text != text)
                Value.Text = text;
            Color colour = clamped >= AzureDreamsTowerProgress.MaximumSandLevel
                ? ClientPalette.ItemColor
                : ClientPalette.PrimaryText;
            if (Value.ForeColor != colour)
                Value.ForeColor = colour;
            _tip.SetToolTip(Icon, tip);
            _tip.SetToolTip(Value, tip);
        }

        public void Clear()
        {
            Value.Text = Dash;
            Value.ForeColor = ClientPalette.PrimaryText;
            _tip.SetToolTip(Icon, string.Empty);
            _tip.SetToolTip(Value, string.Empty);
        }
    }

    // --------------------------------------------------------------- layout

    /// <summary>
    /// The part of the window that never scales: the shell's own padding, the
    /// status strip, the gap under it, and one column heading. Everything else
    /// is the tower, the shops and the incoming queue, and those are what the
    /// scale is solved for.
    /// </summary>
    private Size FixedChrome() => new(
        ShellPadding * 2 + SectionGap,
        ShellPadding * 2 + (_statusStrip?.Height ?? 0) + SectionGap + _headingHeight);

    /// <summary>
    /// The scale the current window size affords, taken from whichever
    /// dimension is tighter. At the default size this is exactly 1.0 by
    /// construction, so the default layout is the unscaled one.
    /// </summary>
    private double ComputeScale()
    {
        Size chrome = FixedChrome();
        double byWidth = (ClientSize.Width - chrome.Width) / (double)ScalableWidth;
        double byHeight =
            (ClientSize.Height - chrome.Height) / (double)TowerProgressPanel.PreferredHeight;
        return UiScale.Clamp(Math.Min(byWidth, byHeight));
    }

    /// <summary>
    /// Re-solves the scale and hands it to the three drawn panels, then resizes
    /// the shell's fixed column and row to match. Nothing here changes the
    /// window's own size, so this cannot feed back into the resize that called
    /// it.
    /// </summary>
    private void ApplyScale()
    {
        if (_applyingScale || _shell is null)
            return;

        _applyingScale = true;
        try
        {
            double scale = ComputeScale();
            _tower.Scale = scale;
            _shops.Scale = scale;
            _incoming.Scale = scale;
            _tower.Height = _tower.ScaledHeight;

            _shell.ColumnStyles[1] =
                new ColumnStyle(SizeType.Absolute, _shops.ScaledWidth + SectionGap);
            _shell.ColumnStyles[2] =
                new ColumnStyle(SizeType.Absolute, _tower.ScaledWidth);
            _shell.RowStyles[1] = new RowStyle(
                SizeType.Absolute,
                _headingHeight + Math.Max(_shops.ScaledHeight, _incoming.ScaledHeight));
        }
        finally
        {
            _applyingScale = false;
        }
    }

    /// <summary>
    /// The tower spans a fixed row and a proportional one, so the height it
    /// ends up with depends on the window. Measured, not derived: grow until
    /// the real laid-out bottom fits.
    /// </summary>
    private void FitToTower()
    {
        for (int pass = 0; pass < 4; pass++)
        {
            PerformLayout();
            int bottom = BottomWithin(_tower) + ShellPadding;
            if (bottom <= ClientSize.Height)
                break;
            ClientSize = new Size(ClientSize.Width, bottom);
        }
    }

    /// <summary>
    /// How small the tracker is allowed to get: the fixed chrome plus every
    /// scalable panel at <see cref="UiScale.Minimum"/>.
    /// </summary>
    private Size MinimumWindowSize()
    {
        Size chrome = FixedChrome();
        var client = new Size(
            chrome.Width + UiScale.Round(ScalableWidth, UiScale.Minimum),
            chrome.Height +
                UiScale.Round(TowerProgressPanel.PreferredHeight, UiScale.Minimum));
        // The status strip is the one thing in this window that does not
        // scale, so the scaled-down floor above can leave the window narrower
        // than the strip's own line needs - which is what silently cut the
        // send-token readout off the right edge. Whichever is wider wins.
        if (_statusStrip is not null)
        {
            client.Width = Math.Max(
                client.Width, ShellPadding * 2 + _statusStrip.PreferredSize.Width);
        }
        // MinimumSize is the outer size; the border and caption are the
        // difference between the two and are not part of any of this.
        return new Size(
            client.Width + (Width - ClientSize.Width),
            client.Height + (Height - ClientSize.Height));
    }

    protected override void OnClientSizeChanged(EventArgs e)
    {
        base.OnClientSizeChanged(e);
        ApplyScale();
    }

    private int BottomWithin(Control child)
    {
        int top = 0;
        for (Control? current = child; current is not null && current != this;
            current = current.Parent)
        {
            top += current.Top;
        }
        return top + child.Height;
    }

    // ------------------------------------------------------------- activity

    /// <summary>
    /// One character of the activity feed's font. Measured from the real font
    /// rather than assumed, and only once - it is the unit every hanging indent
    /// in the feed is expressed in.
    /// </summary>
    private int ActivityCharacterWidth =>
        _activityCharacterWidth != 0
            ? _activityCharacterWidth
            : _activityCharacterWidth = Math.Max(
                1,
                TextRenderer.MeasureText(
                    "0000000000",
                    _activity.Font,
                    Size.Empty,
                    TextFormatFlags.NoPadding).Width / 10);

    public void ClearActivity()
    {
        _activity.Clear();
        _activityLineCount = 0;
    }

    public void AppendTransfer(ClientTransfer transfer)
    {
        // Receives no longer print here: the incoming queue carries the
        // pending side, and the room-wide send line already announced the item
        // when it was found.
        if (transfer.Kind == ClientTransferKind.Received)
            return;

        BeginActivityLine(TimestampCharacters + SendTag.Length);
        AppendActivityText($"[{DateTime.Now:HH:mm:ss}] ", ClientPalette.SecondaryText);
        AppendActivityText(
            SendTag,
            transfer.SourceIsLocal || transfer.TargetIsLocal
                ? ClientPalette.LocalPlayerColor
                : ClientPalette.RemotePlayerColor);
        AppendActivityText(
            transfer.SourcePlayer,
            transfer.SourceIsLocal
                ? ClientPalette.LocalPlayerColor
                : ClientPalette.RemotePlayerColor);
        if (transfer.SourcePlayer == transfer.TargetPlayer)
        {
            // "Septic sent Pita Fruit to Septic" reads wrong; a self-send
            // is just a find.
            AppendActivityText(" found ", ClientPalette.PrimaryText);
            AppendActivityText(transfer.ItemName, ClientPalette.ItemColor);
        }
        else
        {
            AppendActivityText(" sent ", ClientPalette.PrimaryText);
            AppendActivityText(transfer.ItemName, ClientPalette.ItemColor);
            AppendActivityText(" to ", ClientPalette.PrimaryText);
            AppendActivityText(
                transfer.TargetPlayer,
                transfer.TargetIsLocal
                    ? ClientPalette.LocalPlayerColor
                    : ClientPalette.RemotePlayerColor);
        }

        AppendActivityText($".{Environment.NewLine}", ClientPalette.PrimaryText);
        ScrollActivityToEnd();
    }

    public void AppendCheckpointActivity(string message) =>
        AppendTaggedActivity(CheckpointTag, ClientPalette.SuccessColor, message);

    public void AppendGiftActivity(string message) =>
        AppendTaggedActivity(GiftTag, ClientPalette.ItemColor, message);

    private void AppendTaggedActivity(string tag, Color tagColor, string message)
    {
        BeginActivityLine(TimestampCharacters + tag.Length);
        AppendActivityText($"[{DateTime.Now:HH:mm:ss}] ", ClientPalette.SecondaryText);
        AppendActivityText(tag, tagColor);
        AppendActivityText(message, ClientPalette.PrimaryText);
        AppendActivityText(Environment.NewLine, ClientPalette.PrimaryText);
        ScrollActivityToEnd();
    }

    /// <summary>
    /// One untagged line, indented flush left. Everything that is not a
    /// timestamped SEND/GIFT/CHECKPOINT goes through here so it cannot inherit
    /// the previous paragraph's hanging indent.
    /// </summary>
    public void AppendActivityLine(string message, Color color)
    {
        BeginActivityLine(0);
        AppendActivityText(message + Environment.NewLine, color);
        _activityLineCount++;
        TrimActivityFeed();
    }

    private void ScrollActivityToEnd()
    {
        _activity.SelectionStart = _activity.TextLength;
        _activity.ScrollToCaret();
        _activityLineCount++;
        TrimActivityFeed();
    }

    /// <summary>
    /// Drops the oldest lines once the feed passes its limit.
    ///
    /// <para>The feed carries every room-wide send, so a long session in a
    /// full multiworld is thousands of lines - and a RichTextBox charges for
    /// all of them on every append and every repaint. The limit is set well
    /// above what a normal session reaches, so what it actually bounds is the
    /// runaway case.</para>
    ///
    /// <para>The line count is tracked rather than read back: asking the
    /// control for <c>Lines</c> rebuilds the whole document as an array, which
    /// would cost more per line than the trim saves.</para>
    /// </summary>
    private void TrimActivityFeed()
    {
        if (_activityLineCount <= ActivityLineLimit)
            return;

        // Character offsets, not line indices: with wrapping on, the
        // control's line numbers count displayed rows rather than messages.
        string text = _activity.Text;
        int cut = 0;
        for (int dropped = _activityLineCount - ActivityLinesKept; dropped > 0; dropped--)
        {
            int lineEnd = text.IndexOf('\n', cut);
            if (lineEnd < 0)
                return;
            cut = lineEnd + 1;
        }
        if (cut <= 0)
            return;

        // The feed is read-only, and a read-only rich text box silently
        // refuses an edit - including this one. Lifted for the deletion and
        // put straight back; the explicit BackColor survives the round trip,
        // which is the part a read-only text box would otherwise repaint.
        _activity.ReadOnly = false;
        _activity.Select(0, cut);
        _activity.SelectedText = string.Empty;
        _activity.ReadOnly = true;
        _activityLineCount = ActivityLinesKept;
        _activity.SelectionStart = _activity.TextLength;
        _activity.ScrollToCaret();
    }

    /// <summary>
    /// Starts a paragraph and sets the indent its wrapped lines hang at, in
    /// characters of the feed's monospace font.
    ///
    /// <para>Without this a long send wraps back to the left margin and runs
    /// underneath the timestamp column, which is what made a busy room's feed
    /// hard to skim. With it, a wrapped line resumes exactly where the message
    /// itself began - the space under the timestamp and the tag stays empty on
    /// purpose.</para>
    ///
    /// <para>Paragraph formatting in a RichTextBox carries forward to whatever
    /// is typed next, so every line has to declare its own.</para>
    /// </summary>
    private void BeginActivityLine(int indentCharacters)
    {
        _activity.SelectionStart = _activity.TextLength;
        _activity.SelectionLength = 0;
        // Capped against the feed's own width: the feed font does not scale
        // with the window, so a heavily shrunk window would otherwise spend
        // most of a line on indent and wrap every message to one word.
        int indent = indentCharacters * ActivityCharacterWidth;
        int limit = Math.Max(0, _activity.ClientSize.Width * 2 / 5);
        _activity.SelectionHangingIndent = Math.Min(indent, limit);
    }

    private void AppendActivityText(string text, Color color)
    {
        _activity.SelectionStart = _activity.TextLength;
        _activity.SelectionLength = 0;
        _activity.SelectionColor = color;
        _activity.AppendText(text);
    }
}
