using System.Text;
using Adap.Client.Archipelago;
using Adap.Client.Emulators.DuckStation;
using Adap.Client.Games;
using Adap.Client.Patching;

namespace Adap.Client.Windows;

internal sealed class ConnectionWindow : Form
{
    private static readonly Color WindowBackground = Color.FromArgb(9, 16, 29);
    private static readonly Color PanelBackground = Color.FromArgb(15, 27, 46);
    private static readonly Color InputBackground = Color.FromArgb(10, 21, 38);
    private static readonly Color PrimaryText = Color.FromArgb(226, 232, 240);
    private static readonly Color SecondaryText = Color.FromArgb(148, 163, 184);
    private static readonly Color AccentBlue = Color.FromArgb(37, 99, 235);
    private static readonly Color AccentBlueHover = Color.FromArgb(59, 130, 246);
    private static readonly Color LocalPlayerColor = Color.FromArgb(103, 183, 255);
    private static readonly Color RemotePlayerColor = Color.FromArgb(199, 146, 234);
    private static readonly Color ItemColor = Color.FromArgb(250, 204, 21);
    private static readonly Color SuccessColor = Color.FromArgb(74, 222, 128);
    private static readonly Color WaitingColor = Color.FromArgb(251, 191, 36);
    private static readonly Color ErrorColor = Color.FromArgb(248, 113, 113);

    // A DropDown combo takes its height from the font and ignores Height, so
    // it cannot match the boxes beside it. Anchoring left and right instead of
    // filling centres it in the row, which is what actually reads as aligned.
    private readonly ComboBox _server = new()
    {
        Anchor = AnchorStyles.Left | AnchorStyles.Right,
        FlatStyle = FlatStyle.Flat,
        BackColor = InputBackground,
        ForeColor = PrimaryText,
        Name = "ServerBox",
        DropDownStyle = ComboBoxStyle.DropDown,
        Margin = new Padding(0, 4, 8, 4),
    };
    private readonly TextBox _port = CreateInput("PortBox");
    private readonly TextBox _slot = CreateInput();
    private readonly TextBox _password = CreateInput();
    private readonly TextBox _patchPath = CreateInput("PatchPath");
    private readonly TextBox _originalRomPath = CreateInput("OriginalRomPath");
    private readonly RichTextBox _activity = new()
    {
        Dock = DockStyle.Fill,
        ReadOnly = true,
        Name = "ActivityFeed",
        BorderStyle = BorderStyle.FixedSingle,
        BackColor = InputBackground,
        ForeColor = PrimaryText,
        ScrollBars = RichTextBoxScrollBars.Vertical,
        DetectUrls = false,
        Font = new Font(FontFamily.GenericMonospace, 9.5f),
    };

    /// <summary>
    /// The delivery queue as text, beside the activity feed: the same items
    /// the incoming boxes show left to right, item names only, top line
    /// always the next one to be received. Rendered from each incoming
    /// snapshot, so a delivery removes the head and the rest move up.
    /// </summary>
    private readonly ListBox _queue = new()
    {
        Dock = DockStyle.Fill,
        Name = "QueueFeed",
        BorderStyle = BorderStyle.FixedSingle,
        BackColor = InputBackground,
        ForeColor = ItemColor,
        SelectionMode = SelectionMode.None,
        IntegralHeight = false,
        Font = new Font(FontFamily.GenericMonospace, 9.5f),
        // Top/bottom/right match the activity feed's default 3px control
        // margin so the two boxes share an exact vertical extent.
        Margin = new Padding(QueueFeedGap, 3, 3, 3),
    };
    private const int QueueFeedGap = 8;

    // The feed's line prefix, as widths in its monospace font. "[HH:mm:ss] "
    // is eleven characters; the tags are padded so the message column lines up
    // whichever tag a line carries.
    private const int TimestampCharacters = 11;
    private const string SendTag = "SEND     ";
    private const string GiftTag = "GIFT     ";
    private const string CheckpointTag = "CHECKPOINT  ";

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

    private int _activityCharacterWidth;
    private readonly Button _connect = CreatePrimaryButton("Connect");
    private readonly Button _selectPatch =
        CreateSecondaryButton("Patch File...", "PatchButton");
    private readonly Button _selectOriginalRom =
        CreateSecondaryButton("Original ROM...", "OriginalRomButton");
    private readonly Button _patch = CreatePrimaryButton("Launch Game");
    private readonly Button _saveServer =
        CreateSecondaryButton("Save Server", "SaveServerButton");
    private readonly TextBox _emulatorPath = CreateInput("EmulatorPath");
    private readonly Button _selectEmulator =
        CreateSecondaryButton("DuckStation...", "EmulatorButton");
    private readonly Label _connectionStatus = CreateStatusLabel("Not connected", SecondaryText);
    private readonly Label _patchStatus = CreateStatusLabel("Choose a patch and your untouched US BIN.", SecondaryText);
    private readonly ProgressBar _patchProgress = new()
    {
        Dock = DockStyle.Fill,
        Minimum = 0,
        Maximum = 100,
        Style = ProgressBarStyle.Continuous,
        Visible = false,
    };
    private const int ShellPadding = 16;

    /// <summary>
    /// Every visible gap uses this. The left column already carries it as its
    /// own padding, so the right region adds no left margin of its own -
    /// stacking the two is what made that seam read as double.
    /// </summary>
    private const int SectionGap = ShellPadding;
    private const int HeadingSpacing = 6;
    private const int LeftColumnMinimumWidth = 720;
    /// <summary>
    /// How far the shops column sits below the top of the right region, so
    /// its heading clears the header buttons beside it rather than sitting
    /// level with the app title.
    /// </summary>
    private const int ShopsHeaderClearance = 34;
    /// <summary>
    /// The counters' left margin. The rest of their old left margin becomes
    /// their right one, so the block slides left without the header - and
    /// therefore the window, and therefore the buttons - changing width.
    /// </summary>
    private const int CounterSideGap = 10;

    /// <summary>
    /// One height for every field and its button. A single-line TextBox sizes
    /// itself from the font unless AutoSize is off, which left the boxes
    /// shorter than the buttons beside them.
    /// </summary>
    private const int FieldHeight = 26;

    /// <summary>
    /// Inter-column gap, the shops, another gap, the tower, and the window
    /// margin. The whole right side is a fixed width so the left column
    /// absorbs any resizing.
    /// </summary>
    private const int RightColumnWidth =
        ShopProgressPanel.PreferredWidth + SectionGap +
        TowerProgressPanel.PreferredWidth + ShellPadding;

    // Zero margins: both panels have a fixed content size, so the default
    // three-pixel control margin would push them past their own column.
    private readonly TowerProgressPanel _tower =
        new() { Dock = DockStyle.Fill, Margin = Padding.Empty };
    // Top-docked, not filled: the keycard readout makes that row taller than
    // the slots need, and stretching would leave them floating in dead space.
    private readonly IncomingItemsPanel _incoming =
        new() { Dock = DockStyle.Top, Margin = Padding.Empty };
    private Label? _appTitle;
    private Label? _incomingHeading;
    private TableLayoutPanel? _rightSection;
    private TableLayoutPanel? _leftColumn;

    /// <summary>
    /// Compact mode drops every control the player only needs once - the
    /// server, slot, patch and emulator fields and their buttons - and keeps
    /// what a live session actually watches. The two connection states stay,
    /// because knowing the room and the game are both up is the whole point of
    /// having the window open at all; they just become labels.
    /// </summary>
    private bool _compact;
    private TableLayoutPanel? _fullShell;
    private TableLayoutPanel? _compactShell;
    private TableLayoutPanel? _compactIncomingHost;
    private TableLayoutPanel? _compactShopsHost;
    private TableLayoutPanel? _compactTowerHost;
    private TableLayoutPanel? _compactActivityHost;
    private Control? _compactStatusStrip;
    // Headings and the status strip are text at a fixed font: they are the
    // part of the compact window that does not scale, so the scale is solved
    // against what is left after them.
    private int _compactHeadingHeight;
    private bool _applyingCompactScale;
    // The full layout's homes for the four panels the two modes share, so
    // switching back puts each one in the cell it came from.
    private TableLayoutPanel? _fullIncomingHost;
    private TableLayoutPanel? _fullShopsHost;
    private TableLayoutPanel? _fullTowerHost;
    private TableLayoutPanel? _fullActivityHost;
    // One button per layout rather than one that moves: a control has a single
    // parent, so a shared button would be torn out of whichever shell it was
    // not currently in.
    private readonly Button _compactToggle =
        CreateSecondaryButton("Compact mode", "CompactModeButton");
    // Full mode only, and deliberately not gated on being connected: making
    // an options file is what a player does BEFORE there is a room to join.
    private readonly Button _createYaml =
        CreateSecondaryButton("Create YAML", "CreateYamlButton");
    private readonly Button _fullToggle =
        CreateSecondaryButton("Full mode", "FullModeButton");
    // Separate labels rather than the full mode's own: the status text is
    // shared but the styling and position are not, and reparenting a label out
    // of a FlowLayoutPanel and back is how row order gets lost.
    private readonly Label _compactRoomStatus =
        CreateStatusLabel("Not connected", SecondaryText);
    private readonly Label _compactGameStatus =
        CreateStatusLabel("Game not running.", SecondaryText);
    private readonly Label _compactKeycard = new()
    {
        AutoSize = true,
        Name = "CompactKeycardValue",
        Text = "-/8",
        ForeColor = PrimaryText,
        Margin = new Padding(2, 7, 0, 0),
    };
    // Each mode remembers the size it was last left at, so switching back and
    // forth does not keep throwing away a window the player resized.
    private Size _fullSize;
    private Size _fullMinimumSize;
    private Size _compactSize;
    private Size _compactMinimumSize;
    private readonly ShopProgressPanel _shops =
        new() { Dock = DockStyle.Top, Margin = Padding.Empty };
    // Both readouts now live on the title's line in full mode and in the
    // status strip in compact mode, so the two modes style them identically.
    private readonly Label _keycard = new()
    {
        AutoSize = true,
        Name = "KeycardValue",
        Text = "-/8",
        ForeColor = PrimaryText,
        Margin = new Padding(2, 7, 0, 0),
    };
    // The tower spends these, so the readout follows the GAME's counter, not
    // a count of what the room has handed over.
    private readonly Label _sendToken = new()
    {
        AutoSize = true,
        Name = "SendTokenValue",
        Text = "-",
        ForeColor = PrimaryText,
        Margin = new Padding(2, 7, 0, 0),
    };
    private readonly Label _compactSendToken = new()
    {
        AutoSize = true,
        Name = "CompactSendTokenValue",
        Text = "-",
        ForeColor = PrimaryText,
        Margin = new Padding(2, 7, 0, 0),
    };
    private readonly ClientSettings _settings;

    private readonly GameLauncher _launcher = new();
    private System.Windows.Forms.Timer? _sessionWatch;
    private CancellationTokenSource? _connectionCancellation;
    private CancellationTokenSource? _patchCancellation;
    // True only while patch progress may legitimately drive the status label.
    private bool _patchProgressActive;
    private TextWriter? _originalOut;
    private TextWriter? _originalError;
    private bool _roomConnected;
    private bool _gameConnected;
    private string? _lastConnectionError;
    // The client-to-game link, tracked and displayed separately from the
    // Archipelago room. Starts once the game is launched (Launch Game or an
    // .adpatch double-click) and is independent of the Connect button.
    private bool _gameProcessLaunched;
    private bool _gameEverAttached;
    private bool _gameAttachLostAnnounced;

    private readonly string? _autoLaunchPatch;
    private bool _renderOnly;

    public ConnectionWindow() : this(null)
    {
    }

    /// <summary>
    /// <paramref name="autoLaunchPatch"/> is set when a patch was double
    /// clicked. The window opens normally and then launches that patch, so a
    /// failure is still visible rather than a window that flashes and dies.
    /// </summary>
    public ConnectionWindow(string? autoLaunchPatch)
    {
        _autoLaunchPatch = autoLaunchPatch;
        _settings = ClientSettings.Load();

#if ADAP_STABLE
        Text = "Azure Dreams Archipelago Client";
#else
        Text = "Azure Dreams Archipelago Client (DEV)";
#endif
        StartPosition = FormStartPosition.CenterScreen;
        // Sized after the layout is built, from the right column's own
        // measured height. See the ClientSize assignment below.
        MinimumSize = Size.Empty;
        BackColor = WindowBackground;
        ForeColor = PrimaryText;
        Font = new Font("Segoe UI", 9.5f);

        foreach (string server in _settings.AllServers)
            _server.Items.Add(server);
        _server.Text = _settings.Server.Length > 0
            ? _settings.Server
            : ClientSettings.DefaultServers[0];
        // Ports change per room, so this is never restored.
        _port.PlaceholderText = "Port";
        _slot.PlaceholderText = "Player Slot Name";
        _slot.Text = _settings.SlotName;
        _password.UseSystemPasswordChar = true;
        _patchPath.PlaceholderText = $"Choose a {PatchFileAssociation.Extension} file";
        _originalRomPath.Text = _settings.OriginalRomPath;
        _emulatorPath.Text = _settings.EmulatorPath.Length > 0
            ? _settings.EmulatorPath
            : DuckStationDetector.FindInstalledExecutable() ?? string.Empty;

        var root = new TableLayoutPanel
        {
            Name = "LeftColumn",
            Dock = DockStyle.Fill,
            Padding = new Padding(ShellPadding),
            BackColor = WindowBackground,
            ColumnCount = 1,
            RowCount = 4,
        };
        root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        var shell = new TableLayoutPanel
        {
            Name = "FullShell",
            Dock = DockStyle.Fill,
            BackColor = WindowBackground,
            ColumnCount = 2,
            RowCount = 1,
        };
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, RightColumnWidth));
        shell.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        shell.Controls.Add(root, 0, 0);
        shell.Controls.Add(CreateTowerSection(out int towerColumnHeight), 1, 0);
        Controls.Add(shell);
        _fullShell = shell;

        _leftColumn = root;
        root.Controls.Add(CreateHeader(), 0, 0);
        root.Controls.Add(CreateConnectionSection(), 0, 1);
        root.Controls.Add(CreatePatchingSection(), 0, 2);
        root.Controls.Add(CreateActivitySection(), 0, 3);

        ClientSize = new Size(
            LeftColumnMinimumWidth + RightColumnWidth,
            towerColumnHeight + ShellPadding);
        FitToHeaderWidth();
        AlignIncomingToTitle();
        AlignColumnSeam();
        FitTopRowToContent();
        FitToRightColumn();
        _fullSize = Size;
        _fullMinimumSize = MinimumSize;

        // Built now, populated only when it is switched to: an empty cell in a
        // hidden layout costs nothing, and building it lazily would mean the
        // first toggle re-runs every measuring pass mid-session.
        _compactShell = CreateCompactShell();
        _compactShell.Visible = false;
        Controls.Add(_compactShell);

        AcceptButton = _connect;
        _compactToggle.Click += (_, _) => SetCompactMode(true);
        _createYaml.Click += (_, _) =>
        {
            // Matched to this window rather than sized to its own content:
            // the dialog is meant to read as another screen of the app, not
            // as a popup, and it has room reserved for options still to come.
            using var dialog = new CreateYamlDialog(Size);
            dialog.ShowDialog(this);
        };
        _fullToggle.Click += (_, _) => SetCompactMode(false);
        _connect.Click += ConnectClicked;
        _selectPatch.Click += (_, _) => SelectPatchFile();
        _selectOriginalRom.Click += (_, _) => SelectOriginalRom();
        _patch.Click += LaunchClicked;
        _saveServer.Click += (_, _) => SaveServerClicked();
        _selectEmulator.Click += (_, _) => SelectEmulator();
        FormClosing += WindowClosing;
        Shown += (_, _) =>
        {
            // The render mode shows the window only to give its controls
            // handles to paint with. It must not touch the player's file
            // associations or start a game.
            if (_renderOnly)
                return;

            OfferFileAssociation();
            if (_autoLaunchPatch is null)
                return;

            SetPatchFile(_autoLaunchPatch);
            if (_originalRomPath.Text.Trim().Length == 0 ||
                _emulatorPath.Text.Trim().Length == 0)
            {
                AppendActivityLine(
                    "Choose your original BIN and DuckStation once, then this patch will " +
                    "launch straight from a double click.",
                    WaitingColor);
                return;
            }
            LaunchClicked(this, EventArgs.Empty);
        };
    }

    /// <summary>
    /// The title, the two session counters, and the mode buttons on one line.
    ///
    /// <para>The counters used to be a column of their own above the shops,
    /// which spent a whole 18pt readout and a heading on two numbers and held
    /// the shops a row lower than they needed to be. Here they ride the
    /// title's line at body size - the same shape the compact strip already
    /// used - which frees that row and lets the shops sit just under the
    /// buttons.</para>
    /// </summary>
    private Control CreateHeader()
    {
        var panel = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            AutoSize = true,
            BackColor = WindowBackground,
            ColumnCount = 3,
            RowCount = 1,
            Margin = new Padding(0, 0, 0, 12),
        };
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        _appTitle = new Label
        {
            AutoSize = true,
            Name = "AppTitle",
            Text = "Azure Dreams Archipelago",
            ForeColor = PrimaryText,
            Font = new Font(Font.FontFamily, 18, FontStyle.Bold),
            Margin = Padding.Empty,
        };
        // Anchored right and vertically centred rather than docked, with no
        // margin of its own: the 18pt title must stay the tallest thing in the
        // row, or the header grows and the Incoming heading opposite it - which
        // is aligned to the title - moves with it.
        _compactToggle.Anchor = AnchorStyles.Right;
        _compactToggle.AutoSize = true;
        _compactToggle.Margin = Padding.Empty;
        _createYaml.AutoSize = true;
        _createYaml.Margin = new Padding(0, 0, SectionGap, 0);
        // Both buttons ride one right-anchored row so the title keeps the
        // header's height (see above) and "Create YAML" sits to the left of
        // "Compact mode" without a third column shifting the layout.
        var headerButtons = new FlowLayoutPanel
        {
            Anchor = AnchorStyles.Right,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            FlowDirection = FlowDirection.LeftToRight,
            Margin = Padding.Empty,
            WrapContents = false,
        };
        headerButtons.Controls.Add(_createYaml);
        headerButtons.Controls.Add(_compactToggle);
        panel.Controls.Add(_appTitle, 0, 0);
        panel.Controls.Add(CreateHeaderCounters(), 1, 0);
        panel.Controls.Add(headerButtons, 2, 0);
        return panel;
    }

    /// <summary>
    /// Keycards and send tokens beside the title. Bottom-anchored so the two
    /// readouts sit on the title's baseline rather than floating against the
    /// top of a row whose height the 18pt title owns.
    /// </summary>
    private Control CreateHeaderCounters()
    {
        var counters = new FlowLayoutPanel
        {
            Name = "HeaderCounters",
            Anchor = AnchorStyles.Left | AnchorStyles.Bottom,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            BackColor = WindowBackground,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = false,
            // Split evenly, and the two halves must keep summing to the same
            // total: the window is grown to exactly the header's preferred
            // width, so any net change here moves the buttons and the whole
            // right region with them. Moving space from the left to the
            // right slides the counters over without touching anything else.
            Margin = new Padding(
                CounterSideGap, 0, SectionGap + SectionGap / 2 - CounterSideGap, 4),
        };
        counters.Controls.Add(CreateStatusLabel("Keycards", SecondaryText));
        counters.Controls.Add(_keycard);
        counters.Controls.Add(CreateStatusSeparator());
        counters.Controls.Add(CreateStatusLabel("Send Tokens", SecondaryText));
        counters.Controls.Add(_sendToken);

        // Reserve the width of the widest count the readout can reach, so a
        // second digit grows into space that was always there instead of
        // closing the gap to the button. A single digit then sits in a slot
        // that visibly has room for another - which is the point.
        _sendToken.MinimumSize = new Size(
            TextRenderer.MeasureText("88", _sendToken.Font ?? Font).Width, 0);
        return counters;
    }

    private Control CreateConnectionSection()
    {
        TableLayoutPanel section = CreateSection("Connection", 3);
        section.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 100));
        section.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        section.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        section.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        section.Controls.Add(CreateFieldLabel("Server"), 0, 1);
        section.Controls.Add(CreateServerRow(), 1, 1);
        section.SetColumnSpan(section.GetControlFromPosition(1, 1), 2);
        AddConnectionField(section, 2, "Slot", _slot);
        AddConnectionField(section, 3, "Password", _password);

        var actions = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            AutoSize = true,
            BackColor = PanelBackground,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = true,
            Margin = new Padding(0, 8, 0, 0),
        };
        actions.Controls.Add(_connect);
        actions.Controls.Add(_connectionStatus);
        section.Controls.Add(actions, 1, 4);
        section.SetColumnSpan(actions, 2);
        return section;
    }

    private Control CreatePatchingSection()
    {
        TableLayoutPanel section = CreateSection("Game", 3);
        section.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 100));
        section.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        section.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));

        AddPatchField(section, 1, "Patch", _patchPath, _selectPatch);
        AddPatchField(section, 2, "Original ROM", _originalRomPath, _selectOriginalRom);
        AddPatchField(section, 3, "Emulator", _emulatorPath, _selectEmulator);

        var actions = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            AutoSize = true,
            BackColor = PanelBackground,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = true,
            Margin = new Padding(0, 8, 0, 0),
        };
        actions.Controls.Add(_patch);
        actions.Controls.Add(_patchStatus);
        section.Controls.Add(actions, 1, 4);
        section.SetColumnSpan(actions, 2);

        section.Controls.Add(_patchProgress, 1, 5);
        section.SetColumnSpan(_patchProgress, 2);
        return section;
    }

    private Control CreateActivitySection()
    {
        var section = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            BackColor = WindowBackground,
            ColumnCount = 2,
            RowCount = 3,
            Margin = new Padding(0, 4, 0, 0),
        };
        // The queue feed takes a fifth of the width the activity feed used
        // to have to itself.
        section.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 80));
        section.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 20));
        section.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        section.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        section.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        section.Controls.Add(new Label
        {
            AutoSize = true,
            Text = "Activity",
            ForeColor = PrimaryText,
            Font = new Font(Font, FontStyle.Bold),
            Margin = new Padding(0, 0, 0, 2),
        }, 0, 0);
        section.Controls.Add(new Label
        {
            AutoSize = true,
            Text = "Queue",
            ForeColor = PrimaryText,
            Font = new Font(Font, FontStyle.Bold),
            Margin = new Padding(QueueFeedGap, 0, 0, 2),
        }, 1, 0);
        section.Controls.Add(new Label
        {
            AutoSize = true,
            Text = "Sends, gifts, and checkpoints across the room appear here.",
            ForeColor = SecondaryText,
            Margin = new Padding(0, 0, 0, 7),
        }, 0, 1);
        section.Controls.Add(new Label
        {
            AutoSize = true,
            Text = "Next to receive first.",
            ForeColor = SecondaryText,
            Margin = new Padding(QueueFeedGap, 0, 0, 7),
        }, 1, 1);
        section.Controls.Add(_activity, 0, 2);
        section.Controls.Add(_queue, 1, 2);
        _fullActivityHost = section;
        return section;
    }

    /// <summary>
    /// The right-hand region as a two by two grid. The shops column and the
    /// tower column each keep their own width, so the keycard readout sits
    /// above the shops and the incoming queue lines up exactly over the tower.
    /// Nothing here scrolls, so this also reports the exact height the window
    /// has to give it, measured from real font metrics.
    /// </summary>
    /// <summary>
    /// Server, port and Save Server share one line. All three are docked in a
    /// single row so they get identical heights: the button used to sit lower
    /// than the box purely because it auto-sized to its own text.
    /// </summary>
    private Control CreateServerRow()
    {
        var row = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            BackColor = PanelBackground,
            ColumnCount = 3,
            RowCount = 1,
            Margin = Padding.Empty,
        };
        row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        row.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 84));
        row.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 108));
        row.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        _server.Margin = new Padding(0, 4, 8, 4);
        _port.Margin = new Padding(0, 4, 8, 4);
        _saveServer.AutoSize = false;
        _saveServer.Dock = DockStyle.Fill;
        _saveServer.Margin = new Padding(0, 4, 8, 4);

        row.Controls.Add(_server, 0, 0);
        row.Controls.Add(_port, 1, 0);
        row.Controls.Add(_saveServer, 2, 0);
        return row;
    }

    private Control CreateTowerSection(out int requiredHeight)
    {
        var section = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            BackColor = WindowBackground,
            ColumnCount = 2,
            RowCount = 2,
            Margin = new Padding(0, 0, ShellPadding, ShellPadding),
        };
        _rightSection = section;
        section.ColumnStyles.Add(
            new ColumnStyle(SizeType.Absolute, ShopProgressPanel.PreferredWidth + SectionGap));
        section.ColumnStyles.Add(
            new ColumnStyle(SizeType.Absolute, TowerProgressPanel.PreferredWidth));

        Control incomingColumn = CreateIncomingColumn(out int incomingHeight);
        Control shopsColumn = CreateShopsColumn(out int shopsHeight);
        Control towerColumn = CreateTowerColumn(out int towerHeight);

        // The top row is the incoming queue alone now that the keycard
        // readout rides the title line, and the SHOPS SPAN BOTH ROWS so they
        // rise into the space the readout used to hold - sitting just under
        // the header buttons instead of waiting for the incoming queue's
        // row to end. The incoming queue still owns column 1's top row, so
        // it stays lined up exactly over the tower.
        //
        // An AutoSize row measured this at 100px against 91px of real
        // content, which is what pushed the shops and tower down. Size it
        // from the content that was already measured instead.
        int topRowHeight = incomingHeight;
        section.RowStyles.Add(new RowStyle(SizeType.Absolute, topRowHeight));
        section.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        // Enough to clear the header buttons beside it. The shops are their
        // own column, so this is a visual gap rather than a collision, but
        // starting them level with the title reads as a second heading row.
        shopsColumn.Margin = new Padding(
            shopsColumn.Margin.Left,
            ShopsHeaderClearance,
            shopsColumn.Margin.Right,
            shopsColumn.Margin.Bottom);
        section.Controls.Add(incomingColumn, 1, 0);
        section.Controls.Add(shopsColumn, 0, 0);
        section.SetRowSpan(shopsColumn, 2);
        section.Controls.Add(towerColumn, 1, 1);

        // The two columns no longer share a row structure, so the region is
        // as tall as the taller of them measured from its own top.
        requiredHeight = Math.Max(
            topRowHeight + towerHeight, ShopsHeaderClearance + shopsHeight);
        return section;
    }

    private Control CreateIncomingColumn(out int requiredHeight)
    {
        var column = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            BackColor = WindowBackground,
            ColumnCount = 1,
            RowCount = 2,
            Margin = Padding.Empty,
        };
        column.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        var heading = CreateSectionHeading("Incoming", topMargin: 0);
        heading.Name = "IncomingHeading";
        _incomingHeading = heading;
        column.Controls.Add(heading, 0, 0);
        column.Controls.Add(_incoming, 0, 1);
        _fullIncomingHost = column;

        using var boldFont = new Font(Font, FontStyle.Bold);
        requiredHeight =
            TextRenderer.MeasureText("Incoming", boldFont).Height + HeadingSpacing +
            IncomingItemsPanel.PreferredHeight;
        return column;
    }

    private Control CreateShopsColumn(out int requiredHeight)
    {
        var column = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            BackColor = WindowBackground,
            ColumnCount = 1,
            RowCount = 3,
            Margin = new Padding(0, SectionGap, SectionGap, 0),
        };
        column.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        column.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        using var boldFont = new Font(Font, FontStyle.Bold);
        column.Controls.Add(CreateSectionHeading("Shops", topMargin: 0), 0, 0);
        column.Controls.Add(_shops, 0, 1);
        _fullShopsHost = column;

        requiredHeight =
            SectionGap +
            TextRenderer.MeasureText("Shops", boldFont).Height + HeadingSpacing +
            ShopProgressPanel.PreferredHeight;
        return column;
    }

    private Control CreateTowerColumn(out int requiredHeight)
    {
        var column = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            BackColor = WindowBackground,
            ColumnCount = 1,
            RowCount = 2,
            Margin = new Padding(0, SectionGap, 0, 0),
        };
        column.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        column.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        using var boldFont = new Font(Font, FontStyle.Bold);
        Label towerHeading = CreateSectionHeading("Tower", topMargin: 0);
        towerHeading.Name = "TowerHeading";
        column.Controls.Add(towerHeading, 0, 0);
        column.Controls.Add(_tower, 0, 1);
        _fullTowerHost = column;

        requiredHeight =
            SectionGap +
            TextRenderer.MeasureText("Tower", boldFont).Height + HeadingSpacing +
            TowerProgressPanel.PreferredHeight;
        return column;
    }

    /// <summary>
    /// The width the compact left column starts at. The incoming queue only
    /// needs its own preferred width, but the activity feed sits under it and
    /// a feed too narrow to hold "Sandknight sent Pita Fruit to Wugga." is not
    /// worth keeping. Extra window width goes here, exactly as it does in the
    /// full layout.
    /// </summary>
    private const int CompactLeftColumnMinimumWidth = 420;

    /// <summary>
    /// Compact mode as a three by three grid.
    ///
    /// <code>
    /// | status: room - game - keycards       [Full mode] |
    /// | Incoming (named) | Shops  | Tower                |
    /// | Activity ....................       | (tower)    |
    /// </code>
    ///
    /// The shops are barely half the tower's height, so the activity feed
    /// spans the incoming and shop columns and takes back the dead space that
    /// used to sit under the shops. The tower spans both content rows because
    /// it is the tallest thing in the window and everything else is measured
    /// around it.
    /// </summary>
    private TableLayoutPanel CreateCompactShell()
    {
        var shell = new TableLayoutPanel
        {
            Name = "CompactShell",
            Dock = DockStyle.Fill,
            Padding = new Padding(ShellPadding),
            BackColor = WindowBackground,
            ColumnCount = 3,
            RowCount = 3,
        };
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        shell.ColumnStyles.Add(
            new ColumnStyle(SizeType.Absolute, ShopProgressPanel.PreferredWidth + SectionGap));
        shell.ColumnStyles.Add(
            new ColumnStyle(SizeType.Absolute, TowerProgressPanel.PreferredWidth));

        using var boldFont = new Font(Font, FontStyle.Bold);
        _compactHeadingHeight = TextRenderer.MeasureText("Incoming", boldFont).Height +
            HeadingSpacing;
        // One row height for the incoming queue and the shops beside it. They
        // are built to the same height on purpose, so this is a Math.Max only
        // to keep a future change to either from clipping the other.
        int contentRowHeight = _compactHeadingHeight + Math.Max(
            ShopProgressPanel.PreferredHeight,
            IncomingItemsPanel.VerticalPreferredHeight);

        shell.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        shell.RowStyles.Add(new RowStyle(SizeType.Absolute, contentRowHeight));
        shell.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        _compactIncomingHost = CreateCompactColumn("Incoming", rightGap: SectionGap);
        _compactShopsHost = CreateCompactColumn("Shops", rightGap: SectionGap);
        _compactTowerHost = CreateCompactColumn("Tower", rightGap: 0);
        _compactActivityHost = CreateCompactColumn("Activity", rightGap: SectionGap);
        // Only the activity feed absorbs leftover height. The other three carry
        // content sized from the scale, so height the scale did not ask for
        // would just be dead space inside their panels.
        _compactActivityHost.RowStyles[1] = new RowStyle(SizeType.Percent, 100);
        _compactActivityHost.Margin = new Padding(0, SectionGap, SectionGap, 0);

        _compactStatusStrip = CreateCompactStatusStrip();
        shell.Controls.Add(_compactStatusStrip, 0, 0);
        shell.SetColumnSpan(_compactStatusStrip, 3);
        shell.Controls.Add(_compactIncomingHost, 0, 1);
        shell.Controls.Add(_compactShopsHost, 1, 1);
        shell.Controls.Add(_compactTowerHost, 2, 1);
        shell.SetRowSpan(_compactTowerHost, 2);
        shell.Controls.Add(_compactActivityHost, 0, 2);
        shell.SetColumnSpan(_compactActivityHost, 2);
        return shell;
    }

    /// <summary>
    /// A heading over one panel, which is the shape every compact column takes.
    /// </summary>
    private TableLayoutPanel CreateCompactColumn(string heading, int rightGap)
    {
        var column = new TableLayoutPanel
        {
            Name = "Compact" + heading + "Column",
            Dock = DockStyle.Fill,
            BackColor = WindowBackground,
            ColumnCount = 1,
            RowCount = 2,
            Margin = new Padding(0, 0, rightGap, 0),
        };
        column.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        column.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Label label = CreateSectionHeading(heading, topMargin: 0);
        label.Name = "Compact" + heading + "Heading";
        column.Controls.Add(label, 0, 0);
        return column;
    }

    /// <summary>
    /// Everything the connection and game sections were reduced to: the two
    /// live states, the keycard count moved down out of the right column, and
    /// the way back out of compact mode.
    /// </summary>
    private Control CreateCompactStatusStrip()
    {
        var strip = new TableLayoutPanel
        {
            Name = "CompactStatusStrip",
            Dock = DockStyle.Fill,
            AutoSize = true,
            BackColor = PanelBackground,
            Padding = new Padding(12, 4, 12, 4),
            ColumnCount = 2,
            RowCount = 1,
            Margin = new Padding(0, 0, 0, SectionGap),
        };
        strip.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        strip.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        strip.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        var states = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            AutoSize = true,
            BackColor = PanelBackground,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = false,
            Margin = Padding.Empty,
        };
        _compactRoomStatus.Name = "CompactRoomStatus";
        _compactGameStatus.Name = "CompactGameStatus";
        states.Controls.Add(_compactRoomStatus);
        states.Controls.Add(CreateStatusSeparator());
        states.Controls.Add(_compactGameStatus);
        states.Controls.Add(CreateStatusSeparator());
        states.Controls.Add(CreateStatusLabel("Keycards", SecondaryText));
        states.Controls.Add(_compactKeycard);
        states.Controls.Add(CreateStatusSeparator());
        states.Controls.Add(CreateStatusLabel("Send Tokens", SecondaryText));
        states.Controls.Add(_compactSendToken);

        // Same two-digit reserve the full layout's readout gets. Here it also
        // holds the minimum width still, so reaching a second digit cannot
        // push the strip past the width the window is allowed to shrink to.
        _compactSendToken.MinimumSize = new Size(
            TextRenderer.MeasureText("88", _compactSendToken.Font ?? Font).Width, 0);

        _fullToggle.Anchor = AnchorStyles.Right;
        strip.Controls.Add(states, 0, 0);
        strip.Controls.Add(_fullToggle, 1, 0);
        return strip;
    }

    private static Label CreateStatusSeparator() => new()
    {
        AutoSize = true,
        Anchor = AnchorStyles.Left,
        Text = "•",
        ForeColor = Color.FromArgb(51, 78, 115),
        Margin = new Padding(10, 7, 10, 0),
    };

    /// <summary>
    /// Swaps the two layouts over. The four panels that carry live session
    /// state are moved rather than duplicated, so nothing has to be
    /// re-synchronized after a switch: whatever the tower, shops, incoming
    /// queue and activity feed were showing is what they still show.
    /// </summary>
    internal void SetCompactMode(bool compact)
    {
        if (_compact == compact ||
            _fullShell is null || _compactShell is null ||
            _compactIncomingHost is null || _compactShopsHost is null ||
            _compactTowerHost is null || _compactActivityHost is null ||
            _fullIncomingHost is null || _fullShopsHost is null ||
            _fullTowerHost is null || _fullActivityHost is null)
        {
            return;
        }

        SuspendLayout();
        try
        {
            if (compact)
            {
                _fullSize = Size;
                _incoming.LayoutMode = IncomingItemsLayout.VerticalNamed;
                // Docked to the top rather than filling: compact mode sizes the
                // tower from the scale, so a filled cell would only stretch the
                // panel's background under floor 1.
                _tower.Dock = DockStyle.Top;
                _compactIncomingHost.Controls.Add(_incoming, 0, 1);
                _compactShopsHost.Controls.Add(_shops, 0, 1);
                _compactTowerHost.Controls.Add(_tower, 0, 1);
                _compactActivityHost.Controls.Add(_activity, 0, 1);
                _fullShell.Visible = false;
                _compactShell.Visible = true;
            }
            else
            {
                _compactSize = Size;
                _incoming.LayoutMode = IncomingItemsLayout.HorizontalIcons;
                // Full mode is not scalable: its right column is a fixed width
                // that the whole measured layout is built around.
                _tower.Scale = UiScale.Natural;
                _shops.Scale = UiScale.Natural;
                _incoming.Scale = UiScale.Natural;
                _tower.Dock = DockStyle.Fill;
                _fullIncomingHost.Controls.Add(_incoming, 0, 1);
                _fullShopsHost.Controls.Add(_shops, 0, 1);
                _fullTowerHost.Controls.Add(_tower, 0, 1);
                _fullActivityHost.Controls.Add(_activity, 0, 2);
                _compactShell.Visible = false;
                _fullShell.Visible = true;
            }
            _compact = compact;
        }
        finally
        {
            ResumeLayout(true);
        }

        // Both modes pin a minimum size, so it has to be released before the
        // window can shrink into the smaller one.
        MinimumSize = Size.Empty;
        if (compact && !_compactSize.IsEmpty)
        {
            Size = _compactSize;
            MinimumSize = _compactMinimumSize;
        }
        else if (compact)
        {
            // Both dimensions are the exact natural size, so the first solved
            // scale is 1.0 and the default compact window is the unscaled one.
            // A window even slightly short of natural would scale everything
            // down to fit it, which is how the default came out at 0.91 once.
            // The chrome has to be laid out before it can be measured, hence
            // the two passes.
            ClientSize = new Size(
                ShellPadding * 2 + SectionGap + CompactScalableWidth,
                ShellPadding * 2 + TowerProgressPanel.PreferredHeight);
            PerformLayout();
            ClientSize = new Size(
                ClientSize.Width,
                CompactFixedChrome().Height + TowerProgressPanel.PreferredHeight);
            ApplyCompactScale();
            FitCompactToTower();
            _compactMinimumSize = CompactMinimumWindowSize();
            MinimumSize = _compactMinimumSize;
        }
        else
        {
            Size = _fullSize;
            MinimumSize = _fullMinimumSize;
        }
    }

    internal bool IsCompactMode => _compact;

    /// <summary>
    /// Draws the window to a PNG in either layout, with a plausible session
    /// staged, so the arrangement can be reviewed without a room or a game.
    /// Matches the tower and shop panels' own render modes.
    /// </summary>
    public static int RenderToFile(string path, bool compact, int width = 0, int height = 0)
    {
        using var window = new ConnectionWindow { _renderOnly = true };
        // DrawToBitmap paints from real control handles, and a control only
        // gets one once its window has actually been shown. So show it, well
        // off-screen and out of the taskbar, rather than accept a blank PNG.
        window.StartPosition = FormStartPosition.Manual;
        window.ShowInTaskbar = false;
        window.Location = new Point(-30_000, -30_000);
        window.Show();
        Application.DoEvents();
        window.UpdateIncoming(
        [
            new(AzureDreamsItemManifest.EncodeProtocolItemId(1, 1, 0),
                "Medicinal Herb", "Sandknight"),
            new(AzureDreamsItemManifest.EncodeProtocolItemId(4, 1, 7),
                "Fire Ball (7)", "Wugga"),
            new(AzureDreamsItemManifest.EncodeProtocolItemId(15, 3, 2),
                "Iron Sword +2", "Septic"),
        ]);
        window.UpdateTower(new AzureDreamsTowerProgress(
            new byte[AzureDreamsReceiveState.LocationMaskSize],
            3,
            7,
            0b0100_0001u,
            IsInTower: true,
            SendTokens: 2));
        // Staged before the switch on purpose: reparenting a RichTextBox
        // destroys its handle, and a feed that came back empty would be
        // invisible in every off-screen test.
        window.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent, "Pita Fruit", "Sandknight", "Wugga", false, false));
        window.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent, "Fire Ball (7)", "Septic", "Septic", true, true));
        window.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent, "Iron Sword +2", "Wugga", "Septic", false, true));
        // Long enough to wrap at either mode's feed width, so the hanging
        // indent is actually visible in the rendered layout.
        window.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent,
            "Progressive Bow of Considerable Length",
            "Sandknight",
            "SomebodyWithALongSlotName",
            false,
            false));
        if (compact)
            window.SetCompactMode(true);
        if (width > 0 && height > 0)
        {
            window.Size = new Size(
                Math.Max(width, window.MinimumSize.Width),
                Math.Max(height, window.MinimumSize.Height));
        }
        window.PerformLayout();
        window.Refresh();
        Application.DoEvents();

        using var bitmap = new Bitmap(window.Width, window.Height);
        window.DrawToBitmap(bitmap, new Rectangle(0, 0, window.Width, window.Height));
        window.Close();
        bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        Console.WriteLine(
            $"Wrote {path} ({window.Width}x{window.Height}, " +
            $"{(compact ? "compact" : "full")} mode, scale " +
            $"{window._tower.Scale:0.###}).");
        return 0;
    }

    /// <summary>
    /// The compact tower spans a fixed row and a proportional one, so the
    /// height it ends up with depends on the window. Same measured-not-derived
    /// approach as <see cref="FitToRightColumn"/>: grow until the real laid-out
    /// bottom fits.
    /// </summary>
    private void FitCompactToTower()
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
    /// The part of the compact window that never scales: the shell's own
    /// padding, the status strip, the gap under it, and one column heading.
    /// Everything else is the tower, the shops and the incoming queue, and
    /// those are what the scale is solved for.
    /// </summary>
    private Size CompactFixedChrome() => new(
        ShellPadding * 2 + SectionGap,
        ShellPadding * 2 + (_compactStatusStrip?.Height ?? 0) + SectionGap +
            _compactHeadingHeight);

    /// <summary>
    /// The natural width of everything in the compact window that scales: the
    /// left column the incoming queue and activity feed share, plus the two
    /// fixed-width panels beside it.
    /// </summary>
    private const int CompactScalableWidth =
        CompactLeftColumnMinimumWidth + ShopProgressPanel.PreferredWidth +
        TowerProgressPanel.PreferredWidth;

    /// <summary>
    /// The scale the current window size affords, taken from whichever
    /// dimension is tighter. At the default compact size this is exactly 1.0
    /// by construction, so the default layout is the unscaled one.
    /// </summary>
    private double ComputeCompactScale()
    {
        Size chrome = CompactFixedChrome();
        double byWidth =
            (ClientSize.Width - chrome.Width) / (double)CompactScalableWidth;
        double byHeight =
            (ClientSize.Height - chrome.Height) /
            (double)TowerProgressPanel.PreferredHeight;
        return UiScale.Clamp(Math.Min(byWidth, byHeight));
    }

    /// <summary>
    /// Re-solves the scale and hands it to the three drawn panels, then resizes
    /// the compact shell's fixed column and row to match. Nothing here changes
    /// the window's own size, so this cannot feed back into the resize that
    /// called it.
    /// </summary>
    private void ApplyCompactScale()
    {
        if (!_compact || _applyingCompactScale || _compactShell is null)
            return;

        _applyingCompactScale = true;
        try
        {
            double scale = ComputeCompactScale();
            _tower.Scale = scale;
            _shops.Scale = scale;
            _incoming.Scale = scale;
            _tower.Height = _tower.ScaledHeight;

            _compactShell.ColumnStyles[1] =
                new ColumnStyle(SizeType.Absolute, _shops.ScaledWidth + SectionGap);
            _compactShell.ColumnStyles[2] =
                new ColumnStyle(SizeType.Absolute, _tower.ScaledWidth);
            _compactShell.RowStyles[1] = new RowStyle(
                SizeType.Absolute,
                _compactHeadingHeight +
                    Math.Max(_shops.ScaledHeight, _incoming.ScaledHeight));
        }
        finally
        {
            _applyingCompactScale = false;
        }
    }

    /// <summary>
    /// How small compact mode is allowed to get: the fixed chrome plus every
    /// scalable panel at <see cref="UiScale.Minimum"/>.
    /// </summary>
    private Size CompactMinimumWindowSize()
    {
        Size chrome = CompactFixedChrome();
        var client = new Size(
            chrome.Width + UiScale.Round(CompactScalableWidth, UiScale.Minimum),
            chrome.Height +
                UiScale.Round(TowerProgressPanel.PreferredHeight, UiScale.Minimum));
        // The status strip is the one thing in this window that does not
        // scale, so the scaled-down floor above can leave the window narrower
        // than the strip's own line needs - which is what silently cut the
        // send-token readout off the right edge. Whichever is wider wins.
        // Measured rather than derived, for the same reason FitToHeaderWidth
        // measures: these are AutoSize labels at the player's own font.
        if (_compactStatusStrip is not null)
        {
            client.Width = Math.Max(
                client.Width,
                ShellPadding * 2 + _compactStatusStrip.PreferredSize.Width);
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
        ApplyCompactScale();
    }

    /// <summary>
    /// Grows the window until the header line fits on one row.
    ///
    /// <para>The title, two counters and two buttons are all AutoSize text at
    /// the player's own font, so their combined width is not a number this
    /// file can hold as a constant. Without this the mode button is simply
    /// clipped off the right edge at the minimum width - which is exactly
    /// what the first render of this layout showed.</para>
    /// </summary>
    private void FitToHeaderWidth()
    {
        if (_leftColumn is null || _appTitle?.Parent is not Control header)
            return;

        for (int pass = 0; pass < 4; pass++)
        {
            PerformLayout();
            int deficit = header.PreferredSize.Width - header.Width;
            if (deficit <= 0)
                break;
            ClientSize = new Size(ClientSize.Width + deficit, ClientSize.Height);
        }
    }

    /// <summary>
    /// Nudges the Incoming heading until it starts at the same height as the
    /// app title. The header panel contributes a few pixels of its own that
    /// the constants do not show, so this measures rather than assumes.
    /// </summary>
    private void AlignIncomingToTitle()
    {
        if (_appTitle is null || _incomingHeading is null || _rightSection is null)
            return;

        for (int pass = 0; pass < 4; pass++)
        {
            PerformLayout();
            int delta = TopWithin(_appTitle) - TopWithin(_incomingHeading);
            if (delta == 0)
                break;
            Padding margin = _rightSection.Margin;
            int top = Math.Max(0, margin.Top + delta);
            if (top == margin.Top)
                break;
            _rightSection.Margin =
                new Padding(margin.Left, top, margin.Right, margin.Bottom);
        }
    }

    private int TopWithin(Control child)
    {
        int top = 0;
        for (Control? current = child; current is not null && current != this;
            current = current.Parent)
        {
            top += current.Top;
        }
        return top;
    }

    /// <summary>
    /// Trims the left column's right padding until the seam between it and the
    /// shops is the same gap used everywhere else. Nested layout panels
    /// contribute a few pixels that none of the margin values account for, so
    /// this closes on the gap that is actually drawn.
    /// </summary>
    private void AlignColumnSeam()
    {
        if (_leftColumn is null)
            return;

        for (int pass = 0; pass < 4; pass++)
        {
            PerformLayout();
            int contentRight = LeftWithin(_leftColumn) + _leftColumn.Width -
                _leftColumn.Padding.Right;
            int gap = LeftWithin(_shops) - contentRight;
            int delta = gap - SectionGap;
            if (delta == 0)
                break;

            Padding padding = _leftColumn.Padding;
            int right = Math.Max(0, padding.Right - delta);
            if (right == padding.Right)
                break;
            _leftColumn.Padding = new Padding(padding.Left, padding.Top, right, padding.Bottom);
        }
    }

    private int LeftWithin(Control child)
    {
        int left = 0;
        for (Control? current = child; current is not null && current != this;
            current = current.Parent)
        {
            left += current.Left;
        }
        return left;
    }

    /// <summary>
    /// Sets the top row to exactly the height its content occupies. Font
    /// metrics give the height of the text, not of the AutoSize label drawn
    /// around it, so measuring the finished layout is the only way to make the
    /// seam below the incoming row one clean gap.
    /// </summary>
    private void FitTopRowToContent()
    {
        if (_rightSection is null || _rightSection.RowStyles.Count == 0)
            return;

        for (int pass = 0; pass < 4; pass++)
        {
            PerformLayout();
            int sectionTop = TopWithin(_rightSection);
            int contentBottom = BottomWithin(_incoming);
            int required = contentBottom - sectionTop;
            if (required <= 0)
                return;
            if (Math.Abs(_rightSection.RowStyles[0].Height - required) < 1)
                break;
            _rightSection.RowStyles[0].Height = required;
        }
    }

    /// <summary>
    /// Grows the window until the right region's real laid-out bottom fits.
    /// Heading and note heights come from font metrics, so measuring the
    /// finished layout is the only way to be certain nothing is clipped.
    /// Width needs no such loop: the right region is a fixed width, so extra
    /// width goes to the left column and never moves the tower.
    /// </summary>
    private void FitToRightColumn()
    {
        for (int pass = 0; pass < 4; pass++)
        {
            PerformLayout();
            int bottom = BottomWithin(_tower) + ShellPadding;
            if (bottom <= ClientSize.Height)
                break;
            ClientSize = new Size(ClientSize.Width, bottom);
        }
        MinimumSize = Size;
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

    private Label CreateSectionHeading(string text, int topMargin) => new()
    {
        AutoSize = true,
        Text = text,
        ForeColor = PrimaryText,
        Font = new Font(Font, FontStyle.Bold),
        Margin = new Padding(0, topMargin, 0, HeadingSpacing),
    };

    internal void UpdateIncoming(IReadOnlyList<AzureDreamsIncomingItem> items)
    {
        if (IsDisposed || !IsHandleCreated)
            return;
        if (InvokeRequired)
        {
            BeginInvoke(new Action(() => UpdateIncoming(items)));
            return;
        }
        _incoming.Update(items);
        UpdateQueueFeed(items);
    }

    private void UpdateQueueFeed(IReadOnlyList<AzureDreamsIncomingItem> items)
    {
        // Snapshot rendering keeps the contract for free: the head of the
        // pending list is the next item to be received, so a delivery
        // removes the top line and everything below moves up one.
        if (_queue.Items.Count == items.Count)
        {
            bool same = true;
            for (int index = 0; index < items.Count; index++)
            {
                if (!string.Equals(
                        _queue.Items[index] as string,
                        items[index].DisplayName,
                        StringComparison.Ordinal))
                {
                    same = false;
                    break;
                }
            }
            if (same)
                return;
        }

        _queue.BeginUpdate();
        _queue.Items.Clear();
        foreach (AzureDreamsIncomingItem item in items)
            _queue.Items.Add(item.DisplayName);
        _queue.EndUpdate();
    }

    private void UpdateTower(AzureDreamsTowerProgress progress)
    {
        if (IsDisposed || !IsHandleCreated)
            return;
        if (InvokeRequired)
        {
            BeginInvoke(new Action(() => UpdateTower(progress)));
            return;
        }
        _tower.Update(progress);
        _shops.Update(progress);
        string keycard =
            $"{progress.KeycardLevel}/{AzureDreamsReceiveState.MaximumKeycardLevel}";
        // Eight keycards is go mode.
        Color keycardColor =
            progress.KeycardLevel >= AzureDreamsReceiveState.MaximumKeycardLevel
                ? ItemColor
                : PrimaryText;
        SetKeycardReadout(keycard, keycardColor);
        // Zero tokens is a real state with a consequence - the Send row
        // refuses - so it is coloured like a warning rather than left to read
        // as an ordinary number.
        SetSendTokenReadout(
            progress.SendTokens.ToString(),
            progress.SendTokens > 0 ? PrimaryText : WaitingColor);
    }

    private void SetKeycardReadout(string text, Color color)
    {
        if (_keycard.Text != text)
            _keycard.Text = text;
        if (_keycard.ForeColor != color)
            _keycard.ForeColor = color;
        if (_compactKeycard.Text != text)
            _compactKeycard.Text = text;
        if (_compactKeycard.ForeColor != color)
            _compactKeycard.ForeColor = color;
    }

    private void SetSendTokenReadout(string text, Color color)
    {
        if (_sendToken.Text != text)
            _sendToken.Text = text;
        if (_sendToken.ForeColor != color)
            _sendToken.ForeColor = color;
        if (_compactSendToken.Text != text)
            _compactSendToken.Text = text;
        if (_compactSendToken.ForeColor != color)
            _compactSendToken.ForeColor = color;
    }

    private static TableLayoutPanel CreateSection(string title, int columnCount)
    {
        var section = new TableLayoutPanel
        {
            Dock = DockStyle.Top,
            AutoSize = true,
            BackColor = PanelBackground,
            Padding = new Padding(14, 12, 14, 12),
            ColumnCount = columnCount,
            Margin = new Padding(0, 0, 0, 12),
        };
        section.Controls.Add(new Label
        {
            AutoSize = true,
            Text = title,
            ForeColor = PrimaryText,
            Font = new Font("Segoe UI", 10.5f, FontStyle.Bold),
            Margin = new Padding(0, 0, 0, 9),
        }, 0, 0);
        section.SetColumnSpan(section.GetControlFromPosition(0, 0)!, columnCount);
        return section;
    }

    private static void AddConnectionField(
        TableLayoutPanel section,
        int row,
        string label,
        Control input)
    {
        section.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        section.Controls.Add(CreateFieldLabel(label), 0, row);
        section.Controls.Add(input, 1, row);
    }

    private static void AddPatchField(
        TableLayoutPanel section,
        int row,
        string label,
        Control input,
        Button button)
    {
        section.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        section.Controls.Add(CreateFieldLabel(label), 0, row);
        section.Controls.Add(input, 1, row);
        section.Controls.Add(button, 2, row);
    }

    private static Label CreateFieldLabel(string text) => new()
    {
        AutoSize = true,
        Anchor = AnchorStyles.Left,
        ForeColor = SecondaryText,
        Text = text,
        Margin = new Padding(0, 7, 10, 7),
    };

    private static TextBox CreateInput(string? name = null) => new()
    {
        Dock = DockStyle.Fill,
        BackColor = InputBackground,
        ForeColor = PrimaryText,
        BorderStyle = BorderStyle.FixedSingle,
        Margin = new Padding(0, 4, 8, 4),
        AutoSize = false,
        Height = FieldHeight,
        MinimumSize = new Size(0, FieldHeight),
        Name = name ?? string.Empty,
    };

    private static Button CreatePrimaryButton(string text)
    {
        var button = new Button
        {
            AutoSize = true,
            Text = text,
            BackColor = AccentBlue,
            ForeColor = Color.White,
            FlatStyle = FlatStyle.Flat,
            Cursor = Cursors.Hand,
            Margin = new Padding(0, 0, 10, 0),
            Padding = new Padding(8, 2, 8, 2),
            UseVisualStyleBackColor = false,
        };
        button.FlatAppearance.BorderSize = 0;
        button.FlatAppearance.MouseOverBackColor = AccentBlueHover;
        return button;
    }

    private static Button CreateSecondaryButton(string text, string? name = null)
    {
        var button = new Button
        {
            AutoSize = true,
            Name = name ?? string.Empty,
            Text = text,
            BackColor = Color.FromArgb(30, 58, 95),
            ForeColor = PrimaryText,
            FlatStyle = FlatStyle.Flat,
            Cursor = Cursors.Hand,
            Margin = new Padding(0, 4, 0, 4),
            Padding = new Padding(6, 1, 6, 1),
            MinimumSize = new Size(0, FieldHeight),
            UseVisualStyleBackColor = false,
        };
        button.FlatAppearance.BorderColor = Color.FromArgb(51, 78, 115);
        button.FlatAppearance.MouseOverBackColor = Color.FromArgb(39, 73, 118);
        return button;
    }

    private static Label CreateStatusLabel(string text, Color color) => new()
    {
        AutoSize = true,
        Anchor = AnchorStyles.Left,
        Text = text,
        ForeColor = color,
        Margin = new Padding(2, 7, 0, 0),
    };

    private async void ConnectClicked(object? sender, EventArgs eventArgs)
    {
        if (_connectionCancellation is not null)
        {
            SetConnectionStatus("Disconnecting...", WaitingColor);
            _connectionCancellation.Cancel();
            return;
        }

        string server = _server.Text.Trim();
        string port = _port.Text.Trim();
        // The client parser already accepts "host:" and fills the default
        // port, so an empty port field stays valid.
        string endpoint = port.Length > 0 ? $"{server}:{port}" : $"{server}:";
        string slot = _slot.Text.Trim();
        string? password = string.IsNullOrWhiteSpace(_password.Text) ? null : _password.Text;
        if (endpoint.Length == 0 || slot.Length == 0)
        {
            MessageBox.Show(
                this,
                "Server and slot are required.",
                Text,
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            return;
        }

        _settings.SlotName = slot;
        _settings.Server = server;
        SaveSettings();

        _connectionCancellation = new CancellationTokenSource();
        _lastConnectionError = null;
        SetConnectionControls(true);
        _activity.Clear();
        _queue.Items.Clear();
        _originalOut = Console.Out;
        _originalError = Console.Error;
        var writer = new ControlLogWriter(this, HandleClientOutput);
        Console.SetOut(writer);
        Console.SetError(writer);

        int exitCode = 0;
        bool cancelled = false;
        try
        {
            exitCode = await AzureDreamsArchipelagoClient.RunAsync(
                endpoint,
                slot,
                password,
                _connectionCancellation.Token,
                AppendTransfer,
                UpdateTower,
                UpdateIncoming);
        }
        catch (OperationCanceledException)
        {
            cancelled = true;
        }
        catch (Exception ex)
        {
            exitCode = -1;
            _lastConnectionError = ex.Message;
        }
        finally
        {
            Console.SetOut(_originalOut);
            Console.SetError(_originalError);
            _originalOut = null;
            _originalError = null;
            _connectionCancellation.Dispose();
            _connectionCancellation = null;
            if (!IsDisposed)
                SetConnectionControls(false);
        }

        if (!IsDisposed && !cancelled && exitCode != 0)
        {
            MessageBox.Show(
                this,
                _lastConnectionError ?? $"The client stopped with exit code {exitCode}.",
                "Connection stopped",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }

    private void SetConnectionControls(bool connected)
    {
        _server.Enabled = !connected;
        _port.Enabled = !connected;
        _saveServer.Enabled = !connected;
        _slot.Enabled = !connected;
        _password.Enabled = !connected;
        _connect.Text = connected ? "Disconnect" : "Connect";
        _roomConnected = false;
        _gameConnected = false;
        // Tower state belongs to a live session; do not leave a stale one up.
        if (!connected)
        {
            _tower.Clear();
            _incoming.Clear();
            _shops.Clear();
            SetKeycardReadout("-/8", PrimaryText);
        }
        SetConnectionStatus(
            connected ? "Connecting..." : "Not connected",
            connected ? WaitingColor : SecondaryText);
    }

    private void HandleClientOutput(string message)
    {
        if (IsDisposed)
            return;
        if (InvokeRequired)
        {
            BeginInvoke(() => HandleClientOutput(message));
            return;
        }

        if (message.StartsWith("Archipelago login succeeded", StringComparison.Ordinal))
            _roomConnected = true;
        if (message.StartsWith("Connected to DuckStation", StringComparison.Ordinal) ||
            message.StartsWith("Reconnected to DuckStation", StringComparison.Ordinal))
        {
            _gameConnected = true;
        }
        if (message.StartsWith("DuckStation PID", StringComparison.Ordinal) ||
            message.StartsWith("Game connection waiting", StringComparison.Ordinal) ||
            message.StartsWith("Game reconnection waiting", StringComparison.Ordinal))
        {
            _gameConnected = false;
        }

        if (message.Contains("failed", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("mismatch", StringComparison.OrdinalIgnoreCase) ||
            message.StartsWith("Invalid ", StringComparison.Ordinal) ||
            message.StartsWith("The Archipelago connection closed", StringComparison.Ordinal))
        {
            _lastConnectionError = message;
        }
        // The activity log carries room-wide sends (via AppendTransfer),
        // gifts, and durable checkpoint saves/restores. Queue arrivals live
        // in the queue feed beside it, receives live in the incoming boxes,
        // and waiting/settled-frame diagnostics stay on the hidden console -
        // they were noise here.
        if (message.StartsWith("Saved checkpoint", StringComparison.Ordinal) ||
            message.StartsWith("Saved town checkpoint", StringComparison.Ordinal) ||
            message.StartsWith("Restored town checkpoint", StringComparison.Ordinal))
        {
            AppendCheckpointActivity(message);
        }
        else if (message.StartsWith("Gift: ", StringComparison.Ordinal))
        {
            AppendTaggedActivity(GiftTag, ItemColor, message["Gift: ".Length..]);
        }

        // The room status label describes ONLY the Archipelago server now;
        // the game link has its own label (UpdateGameLinkStatus). The
        // session loop still reports the game attach through the console,
        // so refresh the game label from it while a session is live.
        UpdateGameLinkStatus();
        if (_connectionCancellation is null)
            return;
        if (_roomConnected)
            SetConnectionStatus("Room connected", SuccessColor);
        else
            SetConnectionStatus("Connecting to room...", WaitingColor);
    }

    internal void AppendTransfer(ClientTransfer transfer)
    {
        if (IsDisposed)
            return;
        if (InvokeRequired)
        {
            BeginInvoke(() => AppendTransfer(transfer));
            return;
        }

        // Receives no longer print here: the queue feed and the incoming
        // boxes carry the pending side, and the room-wide send line
        // already announced the item when it was found.
        if (transfer.Kind == ClientTransferKind.Received)
            return;

        BeginActivityLine(TimestampCharacters + SendTag.Length);
        AppendActivityText($"[{DateTime.Now:HH:mm:ss}] ", SecondaryText);
        AppendActivityText(
            SendTag,
            transfer.SourceIsLocal || transfer.TargetIsLocal
                ? LocalPlayerColor
                : RemotePlayerColor);
        AppendActivityText(
            transfer.SourcePlayer,
            transfer.SourceIsLocal ? LocalPlayerColor : RemotePlayerColor);
        if (transfer.SourcePlayer == transfer.TargetPlayer)
        {
            // "Septic sent Pita Fruit to Septic" reads wrong; a self-send
            // is just a find.
            AppendActivityText(" found ", PrimaryText);
            AppendActivityText(transfer.ItemName, ItemColor);
        }
        else
        {
            AppendActivityText(" sent ", PrimaryText);
            AppendActivityText(transfer.ItemName, ItemColor);
            AppendActivityText(" to ", PrimaryText);
            AppendActivityText(
                transfer.TargetPlayer,
                transfer.TargetIsLocal ? LocalPlayerColor : RemotePlayerColor);
        }

        AppendActivityText($".{Environment.NewLine}", PrimaryText);
        _activity.SelectionStart = _activity.TextLength;
        _activity.ScrollToCaret();
    }

    private void AppendCheckpointActivity(string message)
    {
        AppendTaggedActivity(CheckpointTag, SuccessColor, message);
    }

    private void AppendTaggedActivity(string tag, Color tagColor, string message)
    {
        BeginActivityLine(TimestampCharacters + tag.Length);
        AppendActivityText($"[{DateTime.Now:HH:mm:ss}] ", SecondaryText);
        AppendActivityText(tag, tagColor);
        AppendActivityText(message, PrimaryText);
        AppendActivityText(Environment.NewLine, PrimaryText);
        _activity.SelectionStart = _activity.TextLength;
        _activity.ScrollToCaret();
    }

    /// <summary>
    /// One untagged line, indented flush left. Everything that is not a
    /// timestamped SEND/GIFT/CHECKPOINT goes through here so it cannot inherit
    /// the previous paragraph's hanging indent.
    /// </summary>
    private void AppendActivityLine(string message, Color color)
    {
        BeginActivityLine(0);
        AppendActivityText(message + Environment.NewLine, color);
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
        // with the window, so a heavily shrunk compact window would otherwise
        // spend most of a line on indent and wrap every message to one word.
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

    /// <summary>
    /// The room state, written to both layouts' labels. Compact mode keeps
    /// this readout precisely because it is the one thing from the connection
    /// section a live session still needs.
    /// </summary>
    private void SetConnectionStatus(string text, Color color)
    {
        _connectionStatus.Text = text;
        _connectionStatus.ForeColor = color;
        _compactRoomStatus.Text = text;
        _compactRoomStatus.ForeColor = color;
    }

    private void SelectPatchFile()
    {
        using var dialog = new OpenFileDialog
        {
            Title = "Select the Archipelago patch file",
            Filter =
                $"Azure Dreams patches (*{PatchFileAssociation.Extension})" +
                $"|*{PatchFileAssociation.Extension}" +
                "|PPF patch files (*.ppf)|*.ppf|All files (*.*)|*.*",
            CheckFileExists = true,
            RestoreDirectory = true,
        };
        // Only the folder is remembered: the patch itself changes per seed.
        SetDialogStartingPath(
            dialog,
            _patchPath.Text.Length > 0 ? _patchPath.Text : _settings.LastPatchDirectory);
        if (dialog.ShowDialog(this) != DialogResult.OK)
            return;

        SetPatchFile(dialog.FileName);
    }

    private void SelectOriginalRom()
    {
        using var dialog = new OpenFileDialog
        {
            Title = "Select the untouched Azure Dreams (USA) BIN",
            Filter = "PlayStation BIN files (*.bin)|*.bin|Disc image files (*.bin;*.img)|*.bin;*.img|All files (*.*)|*.*",
            CheckFileExists = true,
            RestoreDirectory = true,
        };
        SetDialogStartingPath(dialog, _originalRomPath.Text);
        if (dialog.ShowDialog(this) != DialogResult.OK)
            return;

        _originalRomPath.Text = dialog.FileName;
        _settings.OriginalRomPath = dialog.FileName;
        SaveSettings();
    }

    private static void SetDialogStartingPath(FileDialog dialog, string path)
    {
        if (File.Exists(path))
        {
            dialog.FileName = path;
            return;
        }

        string? directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory) && Directory.Exists(directory))
            dialog.InitialDirectory = directory;
    }

    /// <summary>
    /// Remembers a chosen patch and the folder it came from.
    /// </summary>
    private void SetPatchFile(string path)
    {
        _patchPath.Text = path;
        try
        {
            _settings.LastPatchDirectory = Path.GetDirectoryName(path) ?? string.Empty;
        }
        catch (ArgumentException)
        {
            _settings.LastPatchDirectory = string.Empty;
        }
        _settings.PatchFilePath = string.Empty;
        SaveSettings();
    }

    private void SelectEmulator()
    {
        using var dialog = new OpenFileDialog
        {
            Title = "Select your DuckStation executable",
            Filter = "DuckStation (*.exe)|*.exe|All files (*.*)|*.*",
            CheckFileExists = true,
            RestoreDirectory = true,
        };
        SetDialogStartingPath(dialog, _emulatorPath.Text);
        if (dialog.ShowDialog(this) != DialogResult.OK)
            return;

        _emulatorPath.Text = dialog.FileName;
        _settings.EmulatorPath = dialog.FileName;
        SaveSettings();
    }

    private void SaveServerClicked()
    {
        string server = _server.Text.Trim();
        if (server.Length == 0)
            return;

        if (_settings.AddServer(server))
        {
            _server.Items.Add(server);
            SaveSettings();
            AppendActivityLine($"Saved server {server}.", SecondaryText);
        }
        _server.Text = server;
    }

    /// <summary>
    /// Offers the file association once. Consent is recorded either way, so
    /// this never nags and never registers anything unasked.
    /// </summary>
    private void OfferFileAssociation()
    {
        // Already answered. If the answer was yes, make sure the registration
        // still points here: every client version ships from its own folder,
        // so a registration made by an earlier build points at a path that no
        // longer exists and Windows falls back to the "how do you want to open
        // this" picker. Repairing it is inside the consent already given.
        if (_settings.FileAssociationAllowed == true)
        {
            if (PatchFileAssociation.IsRegistered())
                return;

            if (PatchFileAssociation.Register(out string repair))
            {
                AppendActivityLine(
                    "Patch file association now points at this client.",
                    SecondaryText);
            }
            else
            {
                AppendActivityLine(repair, ErrorColor);
            }
            return;
        }
        if (_settings.FileAssociationAllowed is not null)
            return;

        DialogResult answer = MessageBox.Show(
            this,
            $"Open {PatchFileAssociation.Extension} files with this client?" +
            Environment.NewLine + Environment.NewLine +
            "Double-clicking a patch would then build the game if needed and launch it." +
            Environment.NewLine + Environment.NewLine +
            "This only affects your Windows account, only touches the " +
            $"{PatchFileAssociation.Extension} extension this project invented, and can be " +
            "undone here at any time.",
            "Associate patch files",
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Question);

        bool allowed = answer == DialogResult.Yes;
        _settings.FileAssociationAllowed = allowed;
        SaveSettings();
        if (!allowed)
            return;

        if (PatchFileAssociation.Register(out string message))
            AppendActivityLine(message, SuccessColor);
        else
            AppendActivityLine(message, ErrorColor);
    }

    /// <summary>
    /// Asks the player whether to patch over a BIN that failed the
    /// original-disc fingerprint check. The patch pipeline may call this off
    /// the UI thread, so it marshals itself before showing the dialog.
    /// Nothing has been written when this runs - verification precedes the
    /// copy - so Cancel is entirely consequence-free.
    /// </summary>
    private Task<bool> ConfirmUnverifiedOriginalAsync(string detail)
    {
        if (InvokeRequired)
            return Invoke(() => ConfirmUnverifiedOriginalAsync(detail));

        var continueButton = new TaskDialogButton("Continue anyway");
        TaskDialogButton cancelButton = TaskDialogButton.Cancel;
        var page = new TaskDialogPage
        {
            Caption = "Unverified disc image",
            Heading = "This BIN does not match an original Azure Dreams disc.",
            Text =
                detail +
                "\n\nThe patch may not apply or run correctly on a modified, " +
                "re-encoded, or non-US image. It is recommended to patch a " +
                "clean, unmodified image of the North American release " +
                "(SLUS-00614), such as one created from your own disc.",
            Icon = TaskDialogIcon.Warning,
            Buttons = { continueButton, cancelButton },
            DefaultButton = cancelButton,
            SizeToContent = true,
        };
        if (TaskDialog.ShowDialog(this, page) != continueButton)
        {
            AppendActivityLine(
                "Patching cancelled: the selected disc image did not match the " +
                "original fingerprint.",
                SecondaryText);
            return Task.FromResult(false);
        }

        AppendActivityLine(
            "Continuing with an unverified disc image at the player's request.",
            SecondaryText);
        return Task.FromResult(true);
    }

    /// <summary>
    /// Builds the patched disc if it is not already beside the patch, then
    /// starts DuckStation on it. The client owns that process from here, which
    /// is what lets it tell a deliberate quit from a crash when it ends.
    /// </summary>
    private async void LaunchClicked(object? sender, EventArgs eventArgs)
    {
        if (_patchCancellation is not null)
            return;
        if (_launcher.IsRunning)
        {
            MessageBox.Show(
                this,
                "The game is already running.",
                "Already launched",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
            return;
        }

        string patchPath = _patchPath.Text.Trim();
        string originalPath = _originalRomPath.Text.Trim();
        string emulatorPath = _emulatorPath.Text.Trim();
        if (patchPath.Length == 0 || originalPath.Length == 0 || emulatorPath.Length == 0)
        {
            MessageBox.Show(
                this,
                "Choose a patch, the original Azure Dreams BIN, and your DuckStation executable.",
                "Files required",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            return;
        }

        _settings.OriginalRomPath = originalPath;
        _settings.EmulatorPath = emulatorPath;
        SaveSettings();

        _patchCancellation = new CancellationTokenSource();
        SetPatchingControls(false);
        _patchProgress.Value = 0;
        _patchProgress.Visible = true;
        var progress = new Progress<PatchProgress>(UpdatePatchProgress);
        _patchProgressActive = true;
        try
        {
            string disc = await GameLauncher.EnsurePatchedDiscAsync(
                patchPath,
                originalPath,
                progress,
                message => AppendActivityLine(message, SecondaryText),
                _patchCancellation.Token,
                ConfirmUnverifiedOriginalAsync);

            // Patching is done, so nothing it reported afterwards should reach
            // the status label.
            _patchProgressActive = false;

            if (_launcher.TryLaunch(emulatorPath, disc, out string launchMessage))
            {
                // A successful launch reports nothing: the game-link status
                // going Awaiting -> Connected is the signal, and the old
                // "Launched DuckStation (PID ...)" line was diagnostics.
                if (launchMessage.Length > 0)
                    AppendActivityLine(launchMessage, SuccessColor);
                // Launching the game begins the client-to-game connection:
                // the session watch tracks the process and drives the
                // game-link status (Awaiting -> Connected) without waiting
                // for the Connect button.
                StartSessionWatch();
            }
            else
            {
                _patchStatus.Text = launchMessage;
                _patchStatus.ForeColor = ErrorColor;
                AppendActivityLine(launchMessage, ErrorColor);
            }
        }
        catch (OperationCanceledException)
        {
            _patchStatus.Text = "Launch cancelled.";
            _patchStatus.ForeColor = SecondaryText;
        }
        catch (Exception ex)
        {
            _patchStatus.Text = ex.Message;
            _patchStatus.ForeColor = ErrorColor;
            AppendActivityLine(ex.Message, ErrorColor);
        }
        finally
        {
            _patchProgressActive = false;
            _patchCancellation?.Dispose();
            _patchCancellation = null;
            _patchProgress.Visible = false;
            SetPatchingControls(true);
        }
    }

    /// <summary>
    /// Watches the owned emulator process and drives the game-link status.
    /// This is the client-to-game connection lifecycle, started by Launch
    /// Game (or an .adpatch double-click) and independent of the Connect
    /// button and the Archipelago room.
    /// </summary>
    private void StartSessionWatch()
    {
        _gameProcessLaunched = true;
        _gameEverAttached = false;
        _gameAttachLostAnnounced = false;
        UpdateGameLinkStatus();
        _sessionWatch?.Dispose();
        _sessionWatch = new System.Windows.Forms.Timer { Interval = 500 };
        _sessionWatch.Tick += (_, _) =>
        {
            GameSessionResult result = _launcher.Poll();
            if (result.Outcome == GameSessionOutcome.Running)
            {
                UpdateGameLinkStatus();
                return;
            }

            _sessionWatch?.Stop();
            _sessionWatch?.Dispose();
            _sessionWatch = null;
            ReportSessionOutcome(result);
        };
        _sessionWatch.Start();
    }

    /// <summary>
    /// Refreshes the game-link status label. While a room session is live
    /// the attach state is read from its console messages (which already
    /// hold the game's DuckStation memory open); otherwise it is probed
    /// directly, so the label is accurate even before Connect is pressed.
    /// </summary>
    private void UpdateGameLinkStatus()
    {
        if (IsDisposed)
            return;
        if (InvokeRequired)
        {
            BeginInvoke(new Action(UpdateGameLinkStatus));
            return;
        }
        // Patch progress owns the label while a build is in flight.
        if (_patchProgressActive)
            return;

        bool attached;
        if (_connectionCancellation is not null)
        {
            // A room session is live and holds the game memory; trust the
            // attach state it reports rather than opening a second handle.
            attached = _gameConnected;
        }
        else if (_gameProcessLaunched)
        {
            attached = AzureDreamsArchipelagoClient
                .ProbeGameAttachment(out _) ==
                AzureDreamsArchipelagoClient.GameAttachment.Attached;
        }
        else
        {
            // Never launched here and no session probing it: nothing to show.
            SetGameLinkStatus("Game not running.", SecondaryText);
            return;
        }

        if (attached)
        {
            _gameEverAttached = true;
            _gameAttachLostAnnounced = false;
            SetGameLinkStatus("Game live. Connected.", SuccessColor);
        }
        else if (!_gameEverAttached)
        {
            SetGameLinkStatus("Game live. Awaiting connection...", WaitingColor);
        }
        else if (!_gameAttachLostAnnounced)
        {
            _gameAttachLostAnnounced = true;
            SetGameLinkStatus("Game live. Connection lost.", WaitingColor);
        }
        else
        {
            SetGameLinkStatus("Game live. Reconnecting...", WaitingColor);
        }
    }

    /// <summary>
    /// The client-to-game link, written to both layouts' labels. Only the
    /// game-link states are mirrored: patch progress and launch failures
    /// belong to controls compact mode does not have.
    /// </summary>
    private void SetGameLinkStatus(string text, Color color)
    {
        _patchStatus.Text = text;
        _patchStatus.ForeColor = color;
        _compactGameStatus.Text = text;
        _compactGameStatus.ForeColor = color;
    }

    private void ReportSessionOutcome(GameSessionResult result)
    {
        Color color = result.Outcome switch
        {
            GameSessionOutcome.Crashed => WaitingColor,
            GameSessionOutcome.ClosedByPlayer => SecondaryText,
            _ => WaitingColor,
        };
        AppendActivityLine(result.Description, color);
        _gameProcessLaunched = false;
        _gameEverAttached = false;
        _gameAttachLostAnnounced = false;
        SetGameLinkStatus("Game not running.", SecondaryText);
    }

    private void UpdatePatchProgress(PatchProgress progress)
    {
        if (IsDisposed)
            return;
        if (InvokeRequired)
        {
            BeginInvoke(new Action(() => UpdatePatchProgress(progress)));
            return;
        }

        // Progress<T> posts to the UI thread asynchronously, so the last patch
        // stage can be delivered after the launch has already set the final
        // status - which is how "Writing the CUE..." survived onto a running
        // game and dragged the colour back to grey with it. Once patching is
        // over, stale reports are dropped rather than allowed to overwrite it.
        if (!_patchProgressActive)
            return;

        _patchProgress.Value = Math.Clamp(progress.Percent, 0, 100);
        _patchStatus.Text = progress.Stage switch
        {
            PatchStage.VerifyingOriginal => "Verifying the original disc...",
            PatchStage.CopyingOriginal => "Copying the original disc...",
            PatchStage.ApplyingPatch => "Applying the patch...",
            PatchStage.WritingCue => "Writing the CUE...",
            _ => _patchStatus.Text,
        };
        _patchStatus.ForeColor = SecondaryText;
    }

    private void SetPatchingControls(bool enabled)
    {
        _patchPath.Enabled = enabled;
        _originalRomPath.Enabled = enabled;
        _selectPatch.Enabled = enabled;
        _selectOriginalRom.Enabled = enabled;
        _patch.Enabled = enabled;
    }

    private void SaveSettings()
    {
        try
        {
            _settings.Save();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _patchStatus.Text = "Selected paths could not be saved.";
            _patchStatus.ForeColor = ErrorColor;
        }
    }

    private void WindowClosing(object? sender, FormClosingEventArgs eventArgs)
    {
        _connectionCancellation?.Cancel();
        _patchCancellation?.Cancel();
    }

    private sealed class ControlLogWriter(Control owner, Action<string> handleLine) : TextWriter
    {
        public override Encoding Encoding => Encoding.UTF8;

        public override void WriteLine(string? value)
        {
            if (!owner.IsDisposed)
                handleLine(value ?? string.Empty);
        }

        public override void Write(char value)
        {
            if (value == '\n' && !owner.IsDisposed)
                handleLine(string.Empty);
        }
    }
}
