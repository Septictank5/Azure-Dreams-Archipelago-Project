using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

using Adap.Client.Games;

namespace Adap.Client.Windows;

/// <summary>
/// The "Create YAML" dialog: a slot name and the Azure Dreams options,
/// written out as an Archipelago player options file.
///
/// <para>One panel with etched dividers rather than separate cards - three
/// stacked cards spent a lot of height on a single short text field. The
/// options sit in a two-column grid, and each one carries a hover bubble
/// instead of a line of description under it, so adding the fourth and
/// fifth options does not push the layout around.</para>
///
/// <para>Sized to the main window on purpose: this world is still gaining
/// options, and the reserved space is what keeps that from becoming a
/// redesign.</para>
/// </summary>
internal sealed class CreateYamlDialog : Form
{
    private static readonly Color WindowBackground = Color.FromArgb(9, 16, 29);
    private static readonly Color PanelBackground = Color.FromArgb(15, 27, 46);
    private static readonly Color PrimaryText = Color.FromArgb(226, 232, 240);
    private static readonly Color SecondaryText = Color.FromArgb(148, 163, 184);
    private static readonly Color AccentText = Color.FromArgb(125, 176, 240);
    private static readonly Color WarningText = Color.FromArgb(248, 180, 108);
    private static readonly Color ButtonFace = Color.FromArgb(30, 58, 95);
    private static readonly Color ButtonBorder = Color.FromArgb(51, 78, 115);
    private static readonly Color TooltipBackground = Color.FromArgb(24, 40, 66);
    /// <summary>The two lines an etched groove is made of.</summary>
    private static readonly Color DividerShadow = Color.FromArgb(9, 16, 29);
    private static readonly Color DividerHighlight = Color.FromArgb(38, 58, 88);

    private static readonly Size FallbackSize = new(980, 820);

    private readonly ToolTip _tips = new()
    {
        AutoPopDelay = 20_000,
        InitialDelay = 200,
        ReshowDelay = 100,
        ShowAlways = true,
        OwnerDraw = true,
    };

    private readonly TextBox _slotName;
    private readonly CheckBox _traps;
    private readonly NumericUpDown _trapChance;
    private readonly NumericUpDown _progressionBalancing;
    private readonly CheckBox _hintSystem;
    private readonly CheckBox _temperSystem;
    private readonly CheckBox _carrierSystem;
    private readonly Label _validation;

    /// <param name="matchSize">The main window's size, so the dialog reads as another screen of it.</param>
    /// <param name="initialSlotName">
    /// The slot name to open on - the connection screen's saved slot, so a
    /// player updating their yaml finds their name already filled in. Read
    /// only: this dialog never writes the client settings.
    /// </param>
    public CreateYamlDialog(Size? matchSize = null, string? initialSlotName = null)
    {
        Text = "Create YAML";
        Name = "CreateYamlDialog";
        BackColor = WindowBackground;
        ForeColor = PrimaryText;
        Font = new Font("Segoe UI", 11f);
        StartPosition = FormStartPosition.CenterParent;
        FormBorderStyle = FormBorderStyle.Sizable;
        MinimizeBox = false;
        MaximizeBox = false;
        AutoScaleMode = AutoScaleMode.Dpi;
        MinimumSize = new Size(760, 600);
        Size = ChooseSize(matchSize);
        Padding = new Padding(28, 22, 28, 22);

        _tips.Draw += DrawTooltip;
        _tips.Popup += (_, e) =>
            e.ToolTipSize = new Size(e.ToolTipSize.Width + 18, e.ToolTipSize.Height + 14);

        _slotName = new TextBox
        {
            Name = "YamlSlotName",
            MaxLength = AzureDreamsPlayerYaml.MaxSlotNameLength,
            BackColor = WindowBackground,
            ForeColor = PrimaryText,
            BorderStyle = BorderStyle.FixedSingle,
            Font = new Font("Segoe UI", 15f),
            Width = 320,
            Margin = new Padding(0, 2, 0, 0),
        };
        _traps = new CheckBox
        {
            Name = "YamlEnableTraps",
            // The field label beside it already says "Traps".
            Text = "Enabled",
            AutoSize = true,
            ForeColor = PrimaryText,
            Margin = new Padding(0, 3, 0, 0),
        };
        _trapChance = CreateSpinner(
            "YamlTrapChance",
            AzureDreamsPlayerYaml.MinTrapChance,
            AzureDreamsPlayerYaml.MaxTrapChance,
            AzureDreamsPlayerYaml.DefaultTrapChance);
        _progressionBalancing = CreateSpinner(
            "YamlProgressionBalancing",
            AzureDreamsPlayerYaml.MinProgressionBalancing,
            AzureDreamsPlayerYaml.MaxProgressionBalancing,
            AzureDreamsPlayerYaml.DefaultProgressionBalancing);
        _hintSystem = CreateToggle("YamlHintSystem", AzureDreamsPlayerYaml.DefaultHintSystem);
        _temperSystem = CreateToggle("YamlTemperSystem", AzureDreamsPlayerYaml.DefaultTemperSystem);
        _carrierSystem = CreateToggle("YamlCarrierSystem", AzureDreamsPlayerYaml.DefaultCarrierSystem);
        _validation = new Label
        {
            Name = "YamlValidation",
            AutoSize = true,
            ForeColor = WarningText,
            Text = string.Empty,
            Margin = new Padding(0, 8, 0, 0),
        };

        var root = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 1,
            RowCount = 3,
            BackColor = WindowBackground,
        };
        root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.Controls.Add(CreateHeading(), 0, 0);
        root.Controls.Add(CreateBody(), 0, 1);
        root.Controls.Add(CreateButtonRow(out Button save, out Button cancel), 0, 2);
        Controls.Add(root);

        AcceptButton = save;
        CancelButton = cancel;
        save.Click += (_, _) => Save();

        _traps.CheckedChanged += (_, _) => _trapChance.Enabled = _traps.Checked;
        _trapChance.Enabled = _traps.Checked;

        // Prefill only; the settings are never written from here. Trimmed
        // to the cap so a stale long name does not slip past MaxLength (which
        // only bounds typing), and validated on Save like anything typed.
        string prefill = (initialSlotName ?? string.Empty).Trim();
        if (prefill.Length > AzureDreamsPlayerYaml.MaxSlotNameLength)
            prefill = prefill[..AzureDreamsPlayerYaml.MaxSlotNameLength];
        _slotName.Text = prefill;
        if (prefill.Length > 0)
        {
            _slotName.SelectionStart = prefill.Length;
            _slotName.SelectionLength = 0;
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
            _tips.Dispose();
        base.Dispose(disposing);
    }

    /// <summary>
    /// Draws the dialog to a PNG (`--render-yaml`), prefilled the way the
    /// connection screen would open it, so a layout change can be LOOKED at.
    /// </summary>
    public static int RenderToFile(string path, string slotName, int width = 0, int height = 0)
    {
        Size? matched = width > 0 && height > 0 ? new Size(width, height) : null;
        using var dialog = new CreateYamlDialog(matched, slotName);
        dialog.StartPosition = FormStartPosition.Manual;
        dialog.ShowInTaskbar = false;
        dialog.Location = new Point(-30_000, -30_000);
        dialog.Show();
        Application.DoEvents();
        dialog.PerformLayout();
        dialog.Refresh();
        Application.DoEvents();
        using var bitmap = new Bitmap(dialog.Width, dialog.Height);
        dialog.DrawToBitmap(bitmap, new Rectangle(0, 0, dialog.Width, dialog.Height));
        dialog.Close();
        bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        Console.WriteLine($"Wrote {path} ({dialog.Width}x{dialog.Height}).");
        return 0;
    }

    private static Size ChooseSize(Size? matchSize)
    {
        if (matchSize is not { Width: > 0, Height: > 0 } requested)
            return FallbackSize;
        Rectangle work = Screen.PrimaryScreen?.WorkingArea
            ?? new Rectangle(Point.Empty, requested);
        return new Size(
            Math.Min(requested.Width, work.Width),
            Math.Min(requested.Height, work.Height));
    }

    private static Control CreateHeading()
    {
        var stack = new FlowLayoutPanel
        {
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.TopDown,
            Margin = new Padding(0, 0, 0, 14),
            WrapContents = false,
        };
        stack.Controls.Add(new Label
        {
            AutoSize = true,
            Text = "Create player options",
            ForeColor = PrimaryText,
            Font = new Font("Segoe UI", 20f, FontStyle.Bold),
            Margin = new Padding(0, 0, 0, 4),
        });
        stack.Controls.Add(new Label
        {
            AutoSize = true,
            Text = "Save a YAML file for your Archipelago room. " +
                   "Hand it to whoever is generating, or drop it in their Players folder.",
            ForeColor = SecondaryText,
            Margin = Padding.Empty,
        });
        return stack;
    }

    /// <summary>
    /// Everything on one background, separated by etched grooves instead of
    /// gaps between cards.
    /// </summary>
    private Control CreateBody()
    {
        var body = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            BackColor = PanelBackground,
            ColumnCount = 1,
            RowCount = 8,
            Padding = new Padding(24, 20, 24, 20),
            Margin = Padding.Empty,
        };
        body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (int row = 0; row < 7; row++)
            body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        body.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        body.Controls.Add(SectionHeading("Your slot name"), 0, 0);
        body.Controls.Add(_slotName, 0, 1);
        body.Controls.Add(new Label
        {
            AutoSize = true,
            Text = "Letters, numbers and spaces only, up to " +
                   $"{AzureDreamsPlayerYaml.MaxSlotNameLength} characters. " +
                   "This is the name the room and the game both call you.",
            ForeColor = SecondaryText,
            Margin = new Padding(0, 8, 0, 0),
        }, 0, 2);
        body.Controls.Add(_validation, 0, 3);
        body.Controls.Add(Divider(), 0, 4);
        body.Controls.Add(SectionHeading("Options"), 0, 5);
        body.Controls.Add(CreateOptionGrid(), 0, 6);
        // No divider above the banner: it simply owns whatever space the
        // options have not claimed yet, so nothing has to be removed when
        // they grow into it (or when the banner itself retires).
        body.Controls.Add(CreateDevelopmentPanel(), 0, 7);
        return body;
    }

    /// <summary>
    /// Two columns of options. Each cell is a label, its control and a hover
    /// bubble - the explanations live in the bubbles so a new option costs a
    /// cell rather than another paragraph of height.
    /// </summary>
    private Control CreateOptionGrid()
    {
        // Columns size to their content and sit left, so the second pair
        // follows the first at a readable distance instead of being flung
        // to the far edge of a percentage-split panel.
        var grid = new TableLayoutPanel
        {
            Anchor = AnchorStyles.Left | AnchorStyles.Top,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            ColumnCount = 4,
            RowCount = 4,
            Margin = new Padding(0, 4, 0, 0),
        };
        for (int column = 0; column < 4; column++)
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        for (int row = 0; row < 4; row++)
            grid.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        // Progression balancing leads, on a row of its own: it is the one
        // option here that is not about Azure Dreams at all, and every room
        // has an opinion about it.
        grid.Controls.Add(FieldLabel("Progression balancing"), 0, 0);
        grid.Controls.Add(
            WithBubble(
                _progressionBalancing,
                "YamlHelpProgressionBalancing",
                "Moves progression earlier so the opening drags less.\n\n" +
                "0 disables it, 50 is normal, 99 is extreme."),
            1, 0);

        grid.Controls.Add(FieldLabel("Traps"), 0, 1);
        grid.Controls.Add(
            WithBubble(
                _traps,
                "YamlHelpTraps",
                "Adds tower traps to your item pool.\n\n" +
                "A trap looks like a Progressive Keycard - on the floor, in the\n" +
                "at-feet menu and in its description - until you pick it up.\n" +
                "Traps only ever appear in your own tower."),
            1, 1);

        grid.Controls.Add(FieldLabel("Trap chance"), 2, 1);
        grid.Controls.Add(
            WithBubble(
                Suffixed(_trapChance, "%"),
                "YamlHelpTrapChance",
                "Percent chance for each ordinary item to be a trap instead.\n\n" +
                "A trap spends a whole tower check, so a little goes a long way.\n" +
                "Monster dens stay very rare whatever you set here."),
            3, 1);

        grid.Controls.Add(FieldLabel("Fortune teller hints"), 0, 2);
        grid.Controls.Add(
            WithBubble(
                _hintSystem,
                "YamlHelpHintSystem",
                "Mademoiselle Shiela reads a tower floor for 1000 gold.\n\n" +
                "She looks at one of the lowest three floors that still holds\n" +
                "un-collected checks and describes, in her own vague terms, what\n" +
                "KIND of thing waits there and whether a monster carries it.\n" +
                "Off: she sees nothing in the crystal."),
            1, 2);

        grid.Controls.Add(FieldLabel("Blacksmith and ball charger"), 2, 2);
        grid.Controls.Add(
            WithBubble(
                _temperSystem,
                "YamlHelpTemperSystem",
                "Three Red, three Blue and three White Sands join the pool.\n\n" +
                "The blacksmith tempers weapons (Red) and shields (Blue) up to\n" +
                "+10/+20/+40; the ball charger beside the fortune teller adds\n" +
                "1/2/3 ball charges per town visit (White). Sands never enter\n" +
                "the bag. Off: neither NPC, no sands, floors drop them as usual."),
            3, 2);

        grid.Controls.Add(FieldLabel("Monster-carried checks"), 0, 3);
        grid.Controls.Add(
            WithBubble(
                _carrierSystem,
                "YamlHelpCarrierSystem",
                "A third check on every tower floor, carried by a monster.\n\n" +
                "A level-1 monster of a species that does not belong on the floor\n" +
                "spawns, ignores you, heads for an exit, and drops the check when\n" +
                "killed. Each floor trades one native type for it.\n" +
                "Off: two checks a floor, 39 fewer items, vanilla rosters."),
            1, 3);
        return grid;
    }

    private static Control CreateDevelopmentPanel()
    {
        var panel = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 1,
            RowCount = 1,
            Margin = new Padding(0, 10, 0, 0),
        };
        panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        panel.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        panel.Controls.Add(new Label
        {
            Anchor = AnchorStyles.None,
            AutoSize = true,
            Name = "YamlDevelopmentBanner",
            Text = "IN ACTIVE DEVELOPMENT",
            ForeColor = AccentText,
            Font = new Font("Segoe UI", 26f, FontStyle.Bold),
            Margin = Padding.Empty,
        }, 0, 0);
        return panel;
    }

    private Control CreateButtonRow(out Button save, out Button cancel)
    {
        save = SecondaryButton("Save YAML...", "YamlSave");
        cancel = SecondaryButton("Cancel", "YamlCancel");
        cancel.DialogResult = DialogResult.Cancel;

        var row = new FlowLayoutPanel
        {
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            Dock = DockStyle.Fill,
            FlowDirection = FlowDirection.RightToLeft,
            Margin = new Padding(0, 16, 0, 0),
            WrapContents = false,
        };
        row.Controls.Add(save);
        row.Controls.Add(cancel);
        return row;
    }

    private void Save()
    {
        if (!AzureDreamsPlayerYaml.TryValidateSlotName(
                _slotName.Text, out string slotName, out string message))
        {
            _validation.Text = message;
            _slotName.Focus();
            return;
        }
        _validation.Text = string.Empty;

        string contents = AzureDreamsPlayerYaml.Build(
            slotName,
            _traps.Checked,
            (int)_trapChance.Value,
            (int)_progressionBalancing.Value,
            _hintSystem.Checked,
            _temperSystem.Checked,
            _carrierSystem.Checked);

        using var dialog = new SaveFileDialog
        {
            Title = "Save Azure Dreams player options",
            Filter = "Archipelago player options (*.yaml)|*.yaml|All files (*.*)|*.*",
            FileName = AzureDreamsPlayerYaml.SuggestedFileName(slotName),
            OverwritePrompt = true,
            AddExtension = true,
            DefaultExt = "yaml",
        };
        if (dialog.ShowDialog(this) != DialogResult.OK)
            return;

        try
        {
            File.WriteAllText(dialog.FileName, contents);
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                this,
                $"The options file could not be written:{Environment.NewLine}{exception.Message}",
                "Create YAML",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            return;
        }

        DialogResult = DialogResult.OK;
        Close();
    }

    private void DrawTooltip(object? sender, DrawToolTipEventArgs e)
    {
        using var background = new SolidBrush(TooltipBackground);
        e.Graphics.FillRectangle(background, e.Bounds);
        using var border = new Pen(ButtonBorder);
        e.Graphics.DrawRectangle(
            border, 0, 0, e.Bounds.Width - 1, e.Bounds.Height - 1);
        TextRenderer.DrawText(
            e.Graphics,
            e.ToolTipText,
            e.Font,
            Rectangle.Inflate(e.Bounds, -9, -7),
            PrimaryText,
            TextFormatFlags.Left | TextFormatFlags.VerticalCenter);
    }

    /// <summary>
    /// A control paired with a round `?` whose hover text explains it. The
    /// explanation lives here rather than under the control so the grid
    /// keeps its shape as options are added.
    /// </summary>
    private Control WithBubble(Control control, string name, string help)
    {
        var row = new FlowLayoutPanel
        {
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            FlowDirection = FlowDirection.LeftToRight,
            // The right margin is the gap between the two option columns.
            Margin = new Padding(0, 0, 64, 16),
            WrapContents = false,
        };
        // Drawn rather than clipped with a Region: a Region is not honoured
        // by every rendering path (it comes out square under WM_PRINT), and
        // painting it gives anti-aliased edges for free.
        var bubble = new Label
        {
            AutoSize = false,
            Name = name,
            Size = new Size(20, 20),
            Text = string.Empty,
            BackColor = PanelBackground,
            Cursor = Cursors.Help,
            Margin = new Padding(10, 5, 0, 0),
            // Mirrored onto the control so the self-test can prove every
            // bubble actually carries help - a silent empty tooltip is
            // exactly the failure a screenshot would not show.
            Tag = help,
        };
        bubble.Paint += (_, e) =>
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            var face = new Rectangle(0, 0, bubble.Width - 1, bubble.Height - 1);
            using (var fill = new SolidBrush(ButtonFace))
                e.Graphics.FillEllipse(fill, face);
            using (var edge = new Pen(ButtonBorder))
                e.Graphics.DrawEllipse(edge, face);
            e.Graphics.SmoothingMode = SmoothingMode.Default;
            TextRenderer.DrawText(
                e.Graphics,
                "?",
                new Font("Segoe UI", 9f, FontStyle.Bold),
                bubble.ClientRectangle,
                AccentText,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        };
        _tips.SetToolTip(bubble, help);
        row.Controls.Add(control);
        row.Controls.Add(bubble);
        return row;
    }

    private static Control Suffixed(Control control, string suffix)
    {
        var row = new FlowLayoutPanel
        {
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            FlowDirection = FlowDirection.LeftToRight,
            Margin = Padding.Empty,
            WrapContents = false,
        };
        row.Controls.Add(control);
        row.Controls.Add(new Label
        {
            AutoSize = true,
            Text = suffix,
            ForeColor = PrimaryText,
            Margin = new Padding(6, 6, 0, 0),
        });
        return row;
    }

    /// <summary>A two-line etched groove, the full width of the panel.</summary>
    private static Control Divider()
    {
        var divider = new Panel
        {
            Dock = DockStyle.Fill,
            Height = 2,
            Margin = new Padding(0, 20, 0, 18),
        };
        divider.Paint += (_, e) =>
        {
            using var shadow = new Pen(DividerShadow);
            using var highlight = new Pen(DividerHighlight);
            e.Graphics.DrawLine(shadow, 0, 0, divider.Width, 0);
            e.Graphics.DrawLine(highlight, 0, 1, divider.Width, 1);
        };
        return divider;
    }

    private static Label SectionHeading(string text) => new()
    {
        AutoSize = true,
        Text = text,
        ForeColor = PrimaryText,
        Font = new Font("Segoe UI", 13f, FontStyle.Bold),
        Margin = new Padding(0, 0, 0, 10),
    };

    private static Label FieldLabel(string text) => new()
    {
        AutoSize = true,
        Text = text,
        ForeColor = PrimaryText,
        Margin = new Padding(0, 6, 16, 16),
    };

    private static CheckBox CreateToggle(string name, bool value) => new()
    {
        Name = name,
        Text = "Enabled",
        Checked = value,
        AutoSize = true,
        ForeColor = PrimaryText,
        Margin = new Padding(0, 3, 0, 0),
    };

    private static NumericUpDown CreateSpinner(
        string name, int minimum, int maximum, int value) => new()
    {
        Name = name,
        Minimum = minimum,
        Maximum = maximum,
        Value = value,
        BackColor = WindowBackground,
        ForeColor = PrimaryText,
        BorderStyle = BorderStyle.FixedSingle,
        Font = new Font("Segoe UI", 12f),
        Width = 84,
        Margin = Padding.Empty,
    };

    private static Button SecondaryButton(string text, string name)
    {
        var button = new Button
        {
            AutoSize = true,
            Name = name,
            Text = text,
            BackColor = ButtonFace,
            ForeColor = PrimaryText,
            FlatStyle = FlatStyle.Flat,
            Cursor = Cursors.Hand,
            Margin = new Padding(10, 0, 0, 0),
            Padding = new Padding(16, 6, 16, 6),
            UseVisualStyleBackColor = false,
        };
        button.FlatAppearance.BorderColor = ButtonBorder;
        button.FlatAppearance.MouseOverBackColor = Color.FromArgb(39, 73, 118);
        return button;
    }
}
