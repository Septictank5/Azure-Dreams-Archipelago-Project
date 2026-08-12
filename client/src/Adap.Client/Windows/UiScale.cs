using System.Drawing.Drawing2D;

namespace Adap.Client.Windows;

/// <summary>
/// The proportional-resize rules, in one place so the three drawn panels cannot
/// disagree about them.
///
/// <para>Scaling is done with <see cref="Graphics.ScaleTransform(float,float)"/>
/// rather than by threading a factor through every metric. Each panel keeps
/// drawing in its own natural coordinates and reports a scaled size to the
/// layout, so there is exactly one number to get wrong instead of thirty.</para>
/// </summary>
internal static class UiScale
{
    /// <summary>
    /// The window's own default. Every panel's <c>PreferredWidth</c> and
    /// <c>PreferredHeight</c> constant is its size at this scale, so the
    /// default layout is pixel-identical to the unscaled one.
    /// </summary>
    public const double Natural = 1.0;

    /// <summary>
    /// Below this the tower's floor numbers stop being readable, which is the
    /// point at which a smaller window has stopped being useful.
    /// </summary>
    public const double Minimum = 0.55;

    /// <summary>
    /// Growth is bounded only so a maximised window on a large display does not
    /// produce comically large chests.
    /// </summary>
    public const double Maximum = 2.0;

    public static double Clamp(double scale) =>
        double.IsFinite(scale) ? Math.Clamp(scale, Minimum, Maximum) : Natural;

    public static int Round(int value, double scale) =>
        Math.Max(1, (int)Math.Round(value * scale));

    /// <summary>
    /// How a sprite should be resampled at this scale.
    ///
    /// <para>These are 16-px sprites drawn at 2x. At any whole multiple,
    /// nearest-neighbour is the only correct answer - it is what keeps the
    /// pixel art crisp, and it is why the panels pinned an exact 2x in the
    /// first place. At a fractional multiple nearest-neighbour has to drop or
    /// double pixel rows unevenly, which reads as damage rather than as a
    /// smaller sprite; bicubic is softer but stays coherent. So: crisp when it
    /// can be, smooth when it cannot.</para>
    /// </summary>
    public static InterpolationMode SpriteInterpolation(double scale, int spriteScale)
    {
        double effective = scale * spriteScale;
        return effective >= 1 && Math.Abs(effective - Math.Round(effective)) < 0.02
            ? InterpolationMode.NearestNeighbor
            : InterpolationMode.HighQualityBicubic;
    }
}
