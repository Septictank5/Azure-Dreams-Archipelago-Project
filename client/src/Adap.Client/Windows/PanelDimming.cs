using System.Drawing.Imaging;

namespace Adap.Client.Windows;

/// <summary>
/// The "we do not know yet" state for the tracker's icons.
///
/// <para>Every icon in the tracker is colour-coded, and the colour is the
/// reading: a red crystal is a shortcut the keycards have not opened, a red
/// shop slot is a shop that is shut, a chest is a check still out there. None
/// of those readings are true before the client has a room, a game, and a
/// progression snapshot from that game - the panels are drawing their DEFAULTS,
/// which look exactly like real state and say the opposite of what they mean.
/// Draining the colour out of the sprites is how the window says "this is not
/// live yet" without inventing a second set of placeholder art.</para>
///
/// <para><b>Icons only.</b> The first cut drained whole panels through an
/// offscreen buffer, which took the backgrounds, borders and floor numbers with
/// it and read as a disabled control rather than as unknown contents. The grid
/// is furniture and is always true; it is what sits IN the grid that is not
/// known yet.</para>
/// </summary>
internal static class PanelDimming
{
    /// <summary>
    /// How much of the original brightness a drained sprite keeps. Pure
    /// greyscale at full brightness still reads as a live icon against these
    /// dark panels; a little darker reads as waiting.
    /// </summary>
    private const float DrainedBrightness = 0.8f;

    private static readonly ImageAttributes Drained = BuildDrainedAttributes();

    /// <summary>
    /// Draws a sprite in colour when the panel is live, and in grey when it is
    /// not. Every icon in the three tracker panels goes through here.
    /// </summary>
    public static void DrawIcon(Graphics g, Image image, Rectangle cell, bool live)
    {
        if (live)
        {
            g.DrawImage(image, cell);
            return;
        }

        g.DrawImage(
            image,
            cell,
            0,
            0,
            image.Width,
            image.Height,
            GraphicsUnit.Pixel,
            Drained);
    }

    private static ImageAttributes BuildDrainedAttributes()
    {
        // The usual luminance weights, with every output channel taking the
        // same grey, then scaled down together. Alpha is untouched: these are
        // transparent sprites and the panel behind them keeps its own colour.
        const float r = 0.299f * DrainedBrightness;
        const float g = 0.587f * DrainedBrightness;
        const float b = 0.114f * DrainedBrightness;
        var matrix = new ColorMatrix(
        [
            [r, r, r, 0f, 0f],
            [g, g, g, 0f, 0f],
            [b, b, b, 0f, 0f],
            [0f, 0f, 0f, 1f, 0f],
            [0f, 0f, 0f, 0f, 1f],
        ]);
        var attributes = new ImageAttributes();
        attributes.SetColorMatrix(matrix);
        return attributes;
    }
}
