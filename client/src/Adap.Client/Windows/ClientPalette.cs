namespace Adap.Client.Windows;

/// <summary>
/// The window palette, in one place because there are now two windows wearing
/// it: the connection screen and the tracker it pops out.
/// </summary>
internal static class ClientPalette
{
    public static readonly Color WindowBackground = Color.FromArgb(9, 16, 29);
    public static readonly Color PanelBackground = Color.FromArgb(15, 27, 46);
    public static readonly Color InputBackground = Color.FromArgb(10, 21, 38);
    public static readonly Color PrimaryText = Color.FromArgb(226, 232, 240);
    public static readonly Color SecondaryText = Color.FromArgb(148, 163, 184);
    public static readonly Color AccentBlue = Color.FromArgb(37, 99, 235);
    public static readonly Color AccentBlueHover = Color.FromArgb(59, 130, 246);
    public static readonly Color LocalPlayerColor = Color.FromArgb(103, 183, 255);
    public static readonly Color RemotePlayerColor = Color.FromArgb(199, 146, 234);
    public static readonly Color ItemColor = Color.FromArgb(250, 204, 21);
    public static readonly Color SuccessColor = Color.FromArgb(74, 222, 128);
    public static readonly Color WaitingColor = Color.FromArgb(251, 191, 36);
    public static readonly Color ErrorColor = Color.FromArgb(248, 113, 113);
    public static readonly Color SeparatorColor = Color.FromArgb(51, 78, 115);
    public static readonly Color ButtonBackground = Color.FromArgb(30, 58, 95);
    public static readonly Color ButtonBorder = Color.FromArgb(51, 78, 115);
    public static readonly Color ButtonHover = Color.FromArgb(39, 73, 118);
}

/// <summary>
/// The control shapes both windows build. Same reason as the palette: a status
/// label on the tracker and a status label on the connection screen have to be
/// the same control, or the two windows drift apart one margin at a time.
/// </summary>
internal static class ClientControls
{
    /// <summary>
    /// One height for every field and its button. A single-line TextBox sizes
    /// itself from the font unless AutoSize is off, which left the boxes
    /// shorter than the buttons beside them.
    /// </summary>
    public const int FieldHeight = 26;

    /// <summary>The gap under a section heading.</summary>
    public const int HeadingSpacing = 6;

    public static Label CreateStatusLabel(string text, Color color) => new()
    {
        AutoSize = true,
        Anchor = AnchorStyles.Left,
        Text = text,
        ForeColor = color,
        Margin = new Padding(2, 7, 0, 0),
    };

    public static Label CreateStatusSeparator() => new()
    {
        AutoSize = true,
        Anchor = AnchorStyles.Left,
        Text = "•",
        ForeColor = ClientPalette.SeparatorColor,
        Margin = new Padding(10, 7, 10, 0),
    };

    public static Label CreateSectionHeading(Font baseFont, string text, int topMargin) => new()
    {
        AutoSize = true,
        Text = text,
        ForeColor = ClientPalette.PrimaryText,
        Font = new Font(baseFont, FontStyle.Bold),
        Margin = new Padding(0, topMargin, 0, HeadingSpacing),
    };

    public static Button CreatePrimaryButton(string text, string? name = null)
    {
        var button = new Button
        {
            AutoSize = true,
            Name = name ?? string.Empty,
            Text = text,
            BackColor = ClientPalette.AccentBlue,
            ForeColor = Color.White,
            FlatStyle = FlatStyle.Flat,
            Cursor = Cursors.Hand,
            Margin = new Padding(0, 0, 10, 0),
            Padding = new Padding(8, 2, 8, 2),
            UseVisualStyleBackColor = false,
        };
        button.FlatAppearance.BorderSize = 0;
        button.FlatAppearance.MouseOverBackColor = ClientPalette.AccentBlueHover;
        return button;
    }

    public static Button CreateSecondaryButton(string text, string? name = null)
    {
        var button = new Button
        {
            AutoSize = true,
            Name = name ?? string.Empty,
            Text = text,
            BackColor = ClientPalette.ButtonBackground,
            ForeColor = ClientPalette.PrimaryText,
            FlatStyle = FlatStyle.Flat,
            Cursor = Cursors.Hand,
            Margin = new Padding(0, 4, 0, 4),
            Padding = new Padding(6, 1, 6, 1),
            MinimumSize = new Size(0, FieldHeight),
            UseVisualStyleBackColor = false,
        };
        button.FlatAppearance.BorderColor = ClientPalette.ButtonBorder;
        button.FlatAppearance.MouseOverBackColor = ClientPalette.ButtonHover;
        return button;
    }
}
