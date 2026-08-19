using System.Text;
using Adap.Client.Archipelago;
using Adap.Client.Emulators.DuckStation;
using Adap.Client.Games;
using Adap.Client.Patching;

namespace Adap.Client.Windows;

/// <summary>
/// The setup screen: the room, the slot, the files, and the two buttons that
/// start things. Everything a live session is watched for lives in the
/// <see cref="TrackerWindow"/> this pops out, which is where the tower, the
/// shops, the incoming queue and the activity feed went.
///
/// <para>The two used to be one window with a mode switch. The setup half is
/// touched once per session and the tracking half is watched for hours, so
/// stacking them meant a window that was simultaneously too wide for the
/// fields and too short for the tower. Two windows costs nothing: the tracker
/// is fed whether it is on screen or not.</para>
/// </summary>
internal sealed class ConnectionWindow : Form
{
    private static readonly Color WindowBackground = ClientPalette.WindowBackground;
    private static readonly Color PanelBackground = ClientPalette.PanelBackground;
    private static readonly Color InputBackground = ClientPalette.InputBackground;
    private static readonly Color PrimaryText = ClientPalette.PrimaryText;
    private static readonly Color SecondaryText = ClientPalette.SecondaryText;
    private static readonly Color ItemColor = ClientPalette.ItemColor;
    private static readonly Color SuccessColor = ClientPalette.SuccessColor;
    private static readonly Color WaitingColor = ClientPalette.WaitingColor;
    private static readonly Color ErrorColor = ClientPalette.ErrorColor;

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
    /// <summary>
    /// A thin bar laid into the window's bottom margin, positioned by hand
    /// rather than placed in a row of its own.
    ///
    /// <para>It is visible only during a build, and a row reserved for it is
    /// dead space for the whole rest of the session - which is what left this
    /// window with a bottom margin three times its sides. Floating it costs
    /// the layout nothing and takes the margin that is already there.</para>
    /// </summary>
    private readonly ProgressBar _patchProgress = new()
    {
        Minimum = 0,
        Maximum = 100,
        Style = ProgressBarStyle.Continuous,
        Visible = false,
    };
    /// <summary>The floating bar's height, inside the window's bottom margin.</summary>
    private const int PatchProgressHeight = 6;
    // Not gated on being connected: making an options file is what a player
    // does BEFORE there is a room to join.
    private readonly Button _createYaml =
        CreateSecondaryButton("Create YAML", "CreateYamlButton");
    /// <summary>
    /// Pops the tracker out. It is a button rather than a mode switch because
    /// the two windows are meant to be up at once - the setup screen is where
    /// a reconnect or a relaunch happens mid-session.
    /// </summary>
    private readonly Button _showTracker =
        CreateSecondaryButton("Tracker", "TrackerButton");

    private const int ShellPadding = 16;
    private const int SectionGap = ShellPadding;
    /// <summary>
    /// Wide enough for a real patch path in the file fields. Everything in
    /// this window is a labelled row, so this is the whole width story.
    /// </summary>
    private const int ContentMinimumWidth = 720;

    /// <summary>
    /// One height for every field and its button. A single-line TextBox sizes
    /// itself from the font unless AutoSize is off, which left the boxes
    /// shorter than the buttons beside them.
    /// </summary>
    private const int FieldHeight = ClientControls.FieldHeight;

    private Label? _appTitle;
    private TableLayoutPanel? _root;
    private Control? _gameSection;

    /// <summary>
    /// Built with the window, shown on demand, and hidden rather than disposed
    /// when the player closes it. The session feeds it either way, so the state
    /// it shows is never a function of whether it happened to be open.
    /// </summary>
    private readonly TrackerWindow _tracker = new();

    private readonly ClientSettings _settings;

    private readonly GameLauncher _launcher = new();
    private System.Windows.Forms.Timer? _sessionWatch;
    private CancellationTokenSource? _connectionCancellation;
    private CancellationTokenSource? _patchCancellation;
    // True only while patch progress may legitimately drive the status label.
    private bool _patchProgressActive;
    /// <summary>
    /// A disc was built and no game has been started from it yet. This is the
    /// state a double-clicked patch leaves the window in, and it is a real
    /// answer to "what is the game doing" - so it survives connecting to the
    /// room, which used to blank it back to "Game not running.".
    /// </summary>
    private bool _patchReady;
    private TextWriter? _originalOut;
    private TextWriter? _originalError;
    private bool _roomConnected;
    private bool _gameConnected;
    // Whether the game has handed over a progression snapshot yet. Until it
    // has, every colour in the tracker is a default rather than a reading.
    private bool _hasProgress;
    private string? _lastConnectionError;
    // The client-to-game link, tracked and displayed separately from the
    // Archipelago room. Starts once the game is launched with Launch Game,
    // and is independent of the Connect button.
    private bool _gameProcessLaunched;
    private bool _gameEverAttached;
    private bool _gameAttachLostAnnounced;
    // Last answer from the off-thread attach probe, and the guard that keeps
    // one probe in flight at a time. See StartGameAttachProbe.
    private bool _gameAttached;
    private bool _gameAttachProbeInFlight;

    private readonly string? _startupPatch;
    private bool _renderOnly;

    internal const int ActivityLineLimit = TrackerWindow.ActivityLineLimit;
    internal const int ActivityLinesKept = TrackerWindow.ActivityLinesKept;

    public ConnectionWindow() : this(null)
    {
    }

    /// <summary>
    /// <paramref name="startupPatch"/> is set when a patch was double clicked.
    /// The window opens normally with that patch loaded and builds its disc,
    /// but deliberately does NOT start the game: see the Shown handler.
    /// </summary>
    public ConnectionWindow(string? startupPatch)
    {
        _startupPatch = startupPatch;
        _settings = ClientSettings.Load();

#if ADAP_STABLE
        Text = "Azure Dreams Archipelago Client";
#else
        Text = "Azure Dreams Archipelago Client (DEV)";
#endif
        StartPosition = FormStartPosition.CenterScreen;
        // Sized after the layout is built, from its own measured content. See
        // FitToContent below.
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
            Name = "ContentColumn",
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
        Controls.Add(root);
        _root = root;

        root.Controls.Add(CreateHeader(), 0, 0);
        root.Controls.Add(CreateConnectionSection(), 0, 1);
        _gameSection = CreatePatchingSection();
        // Last section in the column: it drops the gap every section keeps
        // under itself, so what follows it is the window's own margin and
        // nothing else.
        _gameSection.Margin = new Padding(0, 0, 0, 0);
        root.Controls.Add(_gameSection, 0, 2);
        // Outside the layout entirely - see PatchProgressHeight.
        Controls.Add(_patchProgress);
        _patchProgress.BringToFront();

        ClientSize = new Size(ContentMinimumWidth + ShellPadding * 2, ContentMinimumWidth);
        FitToHeaderWidth();
        FitToContent();
        LayoutPatchProgress();

        AcceptButton = _connect;
        _showTracker.Click += (_, _) => ShowTracker();
        _createYaml.Click += (_, _) =>
        {
            // Matched to this window rather than sized to its own content:
            // the dialog is meant to read as another screen of the app, not
            // as a popup, and it has room reserved for options still to come.
            // The slot field opens on the saved slot name and the player may
            // have retyped it since; whichever is current is what a fresh
            // yaml should carry. The dialog only reads it - the settings are
            // written by Connect, never by Create YAML.
            string slotName = _slot.Text.Trim().Length > 0 ? _slot.Text.Trim() : _settings.SlotName;
            using var dialog = new CreateYamlDialog(Size, slotName);
            dialog.ShowDialog(this);
        };
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
            if (_startupPatch is null)
                return;

            // A double-clicked patch PREPARES the game; it does not start it.
            // Building the disc is the half that takes time and needs nothing
            // but the player's own files, so it happens here. Launching is the
            // half that wants a room to already exist, so it stays behind the
            // Launch Game button: a player can double-click a seed while the
            // host is still setting the room up, and the natural order -
            // connect first, then launch - is what the window then asks for.
            SetPatchFile(_startupPatch);
            if (_originalRomPath.Text.Trim().Length == 0)
            {
                AppendActivityLine(
                    "Choose your original Azure Dreams BIN once, then a double-clicked " +
                    "patch will be built for you on the spot.",
                    WaitingColor);
                return;
            }
            _ = BuildPatchedDiscAsync(launchAfterBuild: false);
        };
    }

    /// <summary>The title and the two buttons that open other windows.</summary>
    private Control CreateHeader()
    {
        var panel = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            AutoSize = true,
            BackColor = WindowBackground,
            ColumnCount = 2,
            RowCount = 1,
            Margin = new Padding(0, 0, 0, 12),
        };
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
        // row, or the header grows for no reason.
        _createYaml.AutoSize = true;
        _createYaml.Margin = new Padding(0, 0, SectionGap, 0);
        _showTracker.Anchor = AnchorStyles.Right;
        _showTracker.AutoSize = true;
        _showTracker.Margin = Padding.Empty;
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
        headerButtons.Controls.Add(_showTracker);
        panel.Controls.Add(_appTitle, 0, 0);
        panel.Controls.Add(headerButtons, 1, 0);
        return panel;
    }

    private Control CreateConnectionSection()
    {
        TableLayoutPanel section = CreateSection("Connection", 3);
        section.Name = "ConnectionSection";
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
        section.Name = "GameSection";
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
        return section;
    }

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

    /// <summary>
    /// Grows the window until the header line fits on one row.
    ///
    /// <para>The title and its buttons are all AutoSize text at the player's
    /// own font, so their combined width is not a number this file can hold as
    /// a constant.</para>
    /// </summary>
    private void FitToHeaderWidth()
    {
        if (_root is null || _appTitle?.Parent is not Control header)
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
    /// Shrinks the window onto its own content, measured rather than derived:
    /// every section is AutoSize text at the player's font, so the height is
    /// whatever the laid-out sections came to. What is left under the Game
    /// section is one <see cref="ShellPadding"/>, the same margin the sides
    /// carry.
    /// </summary>
    private void FitToContent()
    {
        if (_gameSection is null)
            return;

        for (int pass = 0; pass < 4; pass++)
        {
            PerformLayout();
            int required = BottomWithin(_gameSection) + ShellPadding;
            if (required == ClientSize.Height)
                break;
            ClientSize = new Size(ClientSize.Width, required);
        }
        MinimumSize = Size;
    }

    /// <summary>
    /// Lays the patch bar across the bottom margin, centred in it. Called on
    /// every resize because the bar takes no part in the layout that would
    /// otherwise do this for it.
    /// </summary>
    private void LayoutPatchProgress()
    {
        int inset = ShellPadding + (_gameSection?.Margin.Left ?? 0);
        _patchProgress.Bounds = new Rectangle(
            inset,
            ClientSize.Height - ShellPadding / 2 - PatchProgressHeight / 2,
            Math.Max(0, ClientSize.Width - inset * 2),
            PatchProgressHeight);
    }

    protected override void OnClientSizeChanged(EventArgs e)
    {
        base.OnClientSizeChanged(e);
        LayoutPatchProgress();
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

    // --------------------------------------------------------------- tracker

    /// <summary>The tracker this window feeds, open or not.</summary>
    internal TrackerWindow Tracker => _tracker;

    internal bool IsTrackerOpen => _tracker.IsOpen;

    /// <summary>
    /// Pops the tracker out, or brings it forward if it is already up.
    /// </summary>
    internal void ShowTracker()
    {
        if (_tracker.IsDisposed)
            return;

        _tracker.Present(this);
    }

    /// <summary>
    /// The tracker's colours are only meaningful once all three of the room,
    /// the game and a progression snapshot are in hand: before that the panels
    /// draw their defaults, which look exactly like a real state and say the
    /// opposite of what they mean. See <see cref="PanelDimming"/>.
    /// </summary>
    private void RefreshTrackerLiveState() =>
        _tracker.SetLive(_roomConnected && _gameConnected && _hasProgress);

    internal void UpdateIncoming(IReadOnlyList<AzureDreamsIncomingItem> items)
    {
        if (IsDisposed)
            return;
        if (InvokeRequired)
        {
            BeginInvoke(new Action(() => UpdateIncoming(items)));
            return;
        }
        _tracker.UpdateIncoming(items);
    }

    internal void UpdateTower(AzureDreamsTowerProgress progress)
    {
        if (IsDisposed)
            return;
        if (InvokeRequired)
        {
            BeginInvoke(new Action(() => UpdateTower(progress)));
            return;
        }
        _tracker.UpdateTower(progress);
        _hasProgress = true;
        RefreshTrackerLiveState();

        string keycard =
            $"{progress.KeycardLevel}/{AzureDreamsReceiveState.MaximumKeycardLevel}";
        // Eight keycards is go mode.
        Color keycardColor =
            progress.KeycardLevel >= AzureDreamsReceiveState.MaximumKeycardLevel
                ? ItemColor
                : PrimaryText;
        _tracker.SetKeycardReadout(keycard, keycardColor);
        // Zero tokens is a real state with a consequence - the Send row
        // refuses - so it is coloured like a warning rather than left to read
        // as an ordinary number.
        _tracker.SetSendTokenReadout(
            progress.SendTokens.ToString(),
            progress.SendTokens > 0 ? PrimaryText : WaitingColor);
        _tracker.SetSandReadouts(progress);
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
        _tracker.AppendTransfer(transfer);
    }

    /// <summary>
    /// One untagged line into the tracker's feed. Everything this window has
    /// to say that is not a status label goes through here.
    /// </summary>
    private void AppendActivityLine(string message, Color color) =>
        _tracker.AppendActivityLine(message, color);

    // ------------------------------------------------------------ rendering

    /// <summary>
    /// Draws the connection window to a PNG so the layout can be reviewed
    /// without a room or a game. See <see cref="RenderTrackerToFile"/> for the
    /// other half.
    /// </summary>
    public static int RenderToFile(string path, int width = 0, int height = 0)
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
        window._patchStatus.Text = "Game patched. Connect, then Launch Game.";
        window._patchStatus.ForeColor = SuccessColor;
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
        Console.WriteLine($"Wrote {path} ({window.Width}x{window.Height}, connection screen).");
        return 0;
    }

    /// <summary>
    /// Draws the tracker to a PNG with a plausible session staged, in either
    /// the drained or the live state, so the arrangement can be reviewed
    /// without a room or a game.
    /// </summary>
    public static int RenderTrackerToFile(
        string path,
        bool live,
        int width = 0,
        int height = 0)
    {
        using var window = new ConnectionWindow { _renderOnly = true };
        TrackerWindow tracker = window.Tracker;
        tracker.StartPosition = FormStartPosition.Manual;
        tracker.ShowInTaskbar = false;
        tracker.Location = new Point(-30_000, -30_000);
        tracker.Show();
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
            SendTokens: 2,
            WeaponTemperLevel: 2,
            ShieldTemperLevel: 1,
            BallChargeLevel: 3));
        tracker.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent, "Pita Fruit", "Sandknight", "Wugga", false, false));
        tracker.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent, "Fire Ball (7)", "Septic", "Septic", true, true));
        tracker.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent, "Iron Sword +2", "Wugga", "Septic", false, true));
        // Long enough to wrap at the feed's width, so the hanging indent is
        // actually visible in the rendered layout.
        tracker.AppendTransfer(new ClientTransfer(
            ClientTransferKind.Sent,
            "Progressive Bow of Considerable Length",
            "Sandknight",
            "SomebodyWithALongSlotName",
            false,
            false));
        tracker.SetLive(live);
        if (width > 0 && height > 0)
        {
            tracker.Size = new Size(
                Math.Max(width, tracker.MinimumSize.Width),
                Math.Max(height, tracker.MinimumSize.Height));
        }
        tracker.PerformLayout();
        tracker.Refresh();
        Application.DoEvents();

        using var bitmap = new Bitmap(tracker.Width, tracker.Height);
        tracker.DrawToBitmap(bitmap, new Rectangle(0, 0, tracker.Width, tracker.Height));
        int renderedWidth = tracker.Width;
        int renderedHeight = tracker.Height;
        window.Close();
        bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        Console.WriteLine(
            $"Wrote {path} ({renderedWidth}x{renderedHeight}, tracker, " +
            $"{(live ? "live" : "drained")}).");
        return 0;
    }

    // -------------------------------------------------------------- controls

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

    private static Button CreatePrimaryButton(string text) =>
        ClientControls.CreatePrimaryButton(text);

    private static Button CreateSecondaryButton(string text, string? name = null) =>
        ClientControls.CreateSecondaryButton(text, name);

    private static Label CreateStatusLabel(string text, Color color) =>
        ClientControls.CreateStatusLabel(text, color);

    // --------------------------------------------------------------- session

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
        CancellationToken sessionToken = _connectionCancellation.Token;
        _lastConnectionError = null;
        SetConnectionControls(true);
        _tracker.ClearActivity();
        _originalOut = Console.Out;
        _originalError = Console.Error;
        var writer = new ControlLogWriter(this, HandleClientOutput);
        Console.SetOut(writer);
        Console.SetError(writer);

        int exitCode = 0;
        bool cancelled = false;
        try
        {
            // Task.Run, not a bare await: RunAsync is a poll loop that reads
            // emulated RAM, writes mailbox commands and commits 24 KiB
            // checkpoints between its awaits, and awaiting it from this
            // handler put every one of those continuations back on the UI
            // thread through the WinForms synchronization context - ten a
            // second normally and sixty during the intro. The window was then
            // only ever a poll apart from its next stall, which is what made
            // dragging it during a session choppy. Off the pool it has no
            // context to capture, so the loop stays off the message pump
            // entirely; every callback below already marshals itself back.
            exitCode = await Task.Run(
                () => AzureDreamsArchipelagoClient.RunAsync(
                    endpoint,
                    slot,
                    password,
                    sessionToken,
                    AppendTransfer,
                    UpdateTower,
                    UpdateIncoming));
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
        _hasProgress = false;
        // Tower state belongs to a live session; do not leave a stale one up.
        if (!connected)
        {
            _tracker.Clear();
            _tracker.SetKeycardReadout("-/8", PrimaryText);
            _tracker.SetSendTokenReadout("-", PrimaryText);
            _tracker.ClearSandReadouts();
        }
        RefreshTrackerLiveState();
        SetConnectionStatus(
            connected ? "Connecting..." : "Not connected",
            connected ? WaitingColor : SecondaryText);
    }

    internal void HandleClientOutput(string message)
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
        // gifts, and durable checkpoint saves/restores. Receives live in the
        // incoming queue, and waiting/settled-frame diagnostics stay on the
        // hidden console - they were noise here.
        if (message.StartsWith("Saved checkpoint", StringComparison.Ordinal) ||
            message.StartsWith("Saved town checkpoint", StringComparison.Ordinal) ||
            message.StartsWith("Restored town checkpoint", StringComparison.Ordinal))
        {
            _tracker.AppendCheckpointActivity(message);
        }
        else if (message.StartsWith("Gift: ", StringComparison.Ordinal))
        {
            _tracker.AppendGiftActivity(message["Gift: ".Length..]);
        }

        // The room status label describes ONLY the Archipelago server now;
        // the game link has its own label (UpdateGameLinkStatus). The
        // session loop still reports the game attach through the console,
        // so refresh the game label from it while a session is live.
        UpdateGameLinkStatus();
        RefreshTrackerLiveState();
        if (_connectionCancellation is null)
            return;
        if (_roomConnected)
            SetConnectionStatus("Room connected", SuccessColor);
        else
            SetConnectionStatus("Connecting to room...", WaitingColor);
    }

    /// <summary>
    /// The room state. It lives on this screen only: the tracker's strip is
    /// for what the SESSION is worth, and duplicating two connection lines
    /// there just to have them twice cost the sands their room.
    /// </summary>
    private void SetConnectionStatus(string text, Color color)
    {
        _connectionStatus.Text = text;
        _connectionStatus.ForeColor = color;
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
        // A different seed is a different disc, so whatever was built before
        // is no longer what Launch Game would start.
        _patchReady = false;
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
            "Double-clicking a patch would then open this window with that patch " +
            "loaded and the game built, ready for Launch Game." +
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

    private async void LaunchClicked(object? sender, EventArgs eventArgs)
    {
        await BuildPatchedDiscAsync(launchAfterBuild: true);
    }

    /// <summary>
    /// Builds the patched disc if it is not already beside the patch and,
    /// when <paramref name="launchAfterBuild"/> is set, starts DuckStation on
    /// it. The client owns that process from there, which is what lets it tell
    /// a deliberate quit from a crash when it ends.
    ///
    /// <para>Both halves run for the Launch Game button. A double-clicked
    /// patch runs the build half only, so opening a seed never starts a game
    /// before the player has a room to connect it to.</para>
    /// </summary>
    private async Task BuildPatchedDiscAsync(bool launchAfterBuild)
    {
        if (_patchCancellation is not null)
            return;
        if (launchAfterBuild && _launcher.IsRunning)
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
        // The emulator is only needed by the half that starts it. Building a
        // disc asks for the patch and the player's own BIN, nothing else.
        if (patchPath.Length == 0 || originalPath.Length == 0 ||
            (launchAfterBuild && emulatorPath.Length == 0))
        {
            MessageBox.Show(
                this,
                launchAfterBuild
                    ? "Choose a patch, the original Azure Dreams BIN, and your DuckStation executable."
                    : "Choose a patch and the original Azure Dreams BIN.",
                "Files required",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            return;
        }

        _settings.OriginalRomPath = originalPath;
        // Never blank a remembered emulator: the build half can legitimately
        // run before one has been chosen.
        if (emulatorPath.Length > 0)
            _settings.EmulatorPath = emulatorPath;
        SaveSettings();

        _patchCancellation = new CancellationTokenSource();
        SetPatchingControls(false);
        _patchReady = false;
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

            if (!launchAfterBuild)
            {
                // Remembered, not just printed: this is the window's real
                // answer to "what is the game doing" until something actually
                // starts one, and connecting to the room used to wipe it.
                _patchReady = true;
                ShowIdleGameStatus();
                AppendActivityLine(
                    $"{Path.GetFileName(disc)} is ready. Connect to the Archipelago room, " +
                    "then click Launch Game.",
                    SuccessColor);
                return;
            }

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
            _patchStatus.Text = launchAfterBuild ? "Launch cancelled." : "Patching cancelled.";
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
    /// Game and independent of the Connect button and the Archipelago room.
    /// </summary>
    private void StartSessionWatch()
    {
        _gameProcessLaunched = true;
        _gameEverAttached = false;
        _gameAttachLostAnnounced = false;
        _gameAttached = false;
        // A game is running now, so the "built, not started" state is over.
        _patchReady = false;
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
            // Paint what the last probe found and start the next one off the
            // UI thread. The probe walks every process on the machine and
            // opens DuckStation's shared memory - tens of milliseconds, spent
            // twice a second, which on the message pump is felt as a window
            // that will not drag smoothly.
            attached = _gameAttached;
            StartGameAttachProbe();
        }
        else
        {
            // Never launched here and no session probing it: nothing to show
            // about the game beyond whether a disc is sitting ready.
            ShowIdleGameStatus();
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
            // "Game live" is only honest once this client has actually started
            // one. Connecting to the room before launching - the ordinary
            // order now that a double-clicked patch no longer launches - would
            // otherwise claim a live game the moment the room came up.
            if (_gameProcessLaunched)
                SetGameLinkStatus("Game live. Awaiting connection...", WaitingColor);
            else
                ShowIdleGameStatus();
        }
        else if (!_gameAttachLostAnnounced)
        {
            _gameAttachLostAnnounced = true;
            SetGameLinkStatus("Game live. Connection lost.", WaitingColor);
            // Once per loss, not once per poll: this is the transition, and
            // the flag above is what makes it one.
            FlashForAttention();
        }
        else
        {
            SetGameLinkStatus("Game live. Reconnecting...", WaitingColor);
        }
    }

    /// <summary>
    /// What the game line says when no game is running: that a disc is built
    /// and waiting, if one is, and otherwise nothing at all.
    ///
    /// <para>The green "Game patched" line used to be written straight to the
    /// label, so the next thing to touch the label - which was connecting to
    /// the room - reset it to a grey "Game not running." The message had not
    /// stopped being true; it just had nowhere to be remembered.</para>
    /// </summary>
    private void ShowIdleGameStatus()
    {
        if (_patchReady)
            SetGameLinkStatus("Game patched. Connect, then Launch Game.", SuccessColor);
        else
            SetGameLinkStatus("Game not running.", SecondaryText);
    }

    /// <summary>
    /// Probes for an attached patched game on the thread pool and refreshes
    /// the label if the answer moved. Runs on the UI thread either side of its
    /// await, so the state it touches stays single-threaded.
    /// </summary>
    private async void StartGameAttachProbe()
    {
        if (_gameAttachProbeInFlight)
            return;

        _gameAttachProbeInFlight = true;
        try
        {
            bool attached = await Task.Run(() =>
                AzureDreamsArchipelagoClient.ProbeGameAttachment(out _) ==
                    AzureDreamsArchipelagoClient.GameAttachment.Attached);
            if (IsDisposed || !IsHandleCreated || attached == _gameAttached)
                return;

            _gameAttached = attached;
            // Refreshed while the in-flight guard still holds, so the repaint
            // cannot turn around and start a second probe of its own.
            UpdateGameLinkStatus();
        }
        catch (Exception)
        {
            // A probe that could not run is not news: the next tick tries
            // again and the label keeps the state it last painted.
        }
        finally
        {
            _gameAttachProbeInFlight = false;
        }
    }

    /// <summary>
    /// The client-to-game link. Same as the room state: this screen owns it.
    /// </summary>
    private void SetGameLinkStatus(string text, Color color)
    {
        _patchStatus.Text = text;
        _patchStatus.ForeColor = color;
    }

    /// <summary>
    /// Asks for attention on the taskbar. The player is expected to be in the
    /// game with these windows behind it, so a link that dropped mid-run has
    /// no other way to say so. The tracker is the window that gets it when it
    /// is up - it is the one they left visible.
    /// </summary>
    private void FlashForAttention()
    {
        if (_tracker is { IsDisposed: false, IsOpen: true })
        {
            TaskbarAlert.Flash(_tracker);
            return;
        }
        TaskbarAlert.Flash(this);
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
        // The game itself ended - a quit or a crash. Same reasoning as a lost
        // link: whatever the player is looking at, it is not this window.
        FlashForAttention();
        _gameProcessLaunched = false;
        _gameEverAttached = false;
        _gameAttachLostAnnounced = false;
        _gameAttached = false;
        _gameConnected = false;
        RefreshTrackerLiveState();
        ShowIdleGameStatus();
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

    /// <summary>
    /// The tracker refuses a user close so a run's state survives it, so the
    /// app closing has to dispose it outright - otherwise the message loop has
    /// a live window left and the process never exits.
    /// </summary>
    protected override void Dispose(bool disposing)
    {
        if (disposing && !_tracker.IsDisposed)
            _tracker.Dispose();
        base.Dispose(disposing);
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
