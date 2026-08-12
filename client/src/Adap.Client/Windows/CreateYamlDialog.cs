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
    private readonly Label _validation;

    public CreateYamlDialog(Size? matchSize = null)
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
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
            _tips.Dispose();
        base.Dispose(disposing);
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
            RowCount = 2,
            Margin = new Padding(0, 4, 0, 0),
        };
        for (int column = 0; column < 4; column++)
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        grid.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        grid.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        grid.Controls.Add(FieldLabel("Traps"), 0, 0);
        grid.Controls.Add(
            WithBubble(
                _traps,
                "YamlHelpTraps",
                "Adds tower traps to your item pool.\n\n" +
                "A trap looks like a Progressive Keycard - on the floor, in the\n" +
                "at-feet menu and in its description - until you pick it up.\n" +
                "Traps only ever appear in your own tower."),
            1, 0);

        grid.Controls.Add(FieldLabel("Trap chance"), 2, 0);
        grid.Controls.Add(
            WithBubble(
                Suffixed(_trapChance, "%"),
                "YamlHelpTrapChance",
                "Percent chance for each ordinary item to be a trap instead.\n\n" +
                "A trap spends a whole tower check, so a little goes a long way.\n" +
                "Monster dens stay very rare whatever you set here."),
            3, 0);

        grid.Controls.Add(FieldLabel("Progression balancing"), 0, 1);
        grid.Controls.Add(
            WithBubble(
                _progressionBalancing,
                "YamlHelpProgressionBalancing",
                "Moves progression earlier so the opening drags less.\n\n" +
                "0 disables it, 50 is normal, 99 is extreme."),
            1, 1);
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
            (int)_progressionBalancing.Value);

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
