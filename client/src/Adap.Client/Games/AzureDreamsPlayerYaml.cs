using System.Text;

namespace Adap.Client.Games;

/// <summary>
/// Builds an Archipelago player options file for Azure Dreams.
///
/// <para>Kept out of the dialog so the text and the slot-name rules are
/// testable without a window. Only the options a player actually chooses
/// are written; anything else is left to Archipelago's defaults rather
/// than pinned to a value the player did not pick and would have to know
/// to edit back. Accessibility is deliberately absent - every keycard must
/// be obtained and everything else follows from that, so there is nothing
/// for the setting to change yet.</para>
/// </summary>
internal static class AzureDreamsPlayerYaml
{
    public const string GameName = "Azure Dreams";

    /// <summary>
    /// Archipelago truncates slot names to sixteen characters
    /// (`Generate.handle_name`), and so does the seed page: a recipient
    /// name is encoded into a 0x24-byte slot as sixteen full-width
    /// characters plus a terminator.
    /// </summary>
    public const int MaxSlotNameLength = 16;

    public const int MinTrapChance = 0;
    public const int MaxTrapChance = 100;
    /// <summary>
    /// Matches the APWorld's `TrapChance` default. Low on purpose: a trap
    /// is a whole tower check spent on a setback, and they compound.
    /// </summary>
    public const int DefaultTrapChance = 3;

    /// <summary>
    /// Archipelago's own `ProgressionBalancing` is a NamedRange 0-99
    /// defaulting to 50 - NOT 0-100. Writing 100 would be refused at
    /// generation time.
    /// </summary>
    public const int MinProgressionBalancing = 0;
    public const int MaxProgressionBalancing = 99;
    public const int DefaultProgressionBalancing = 50;

    /// <summary>The one name Archipelago refuses outright.</summary>
    private const string ReservedSlotName = "Archipelago";

    /// <summary>
    /// Validates and normalizes a slot name. Surrounding whitespace is
    /// trimmed rather than rejected (it is almost always a paste artifact).
    ///
    /// <para>Letters, digits and spaces only. Archipelago itself is far more
    /// permissive - it truncates, strips, and refuses only the literal name
    /// "Archipelago" - but its slot names also carry `%number%`/`%player%`
    /// templating and brace substitution, and the same name is rendered by
    /// the GAME through a CP932 encoder. Restricting to the intersection is
    /// this client's policy, and it is one a player can be told in one
    /// line.</para>
    /// </summary>
    public static bool TryValidateSlotName(
        string? candidate,
        out string slotName,
        out string message)
    {
        slotName = (candidate ?? string.Empty).Trim();
        if (slotName.Length == 0)
        {
            message = "Enter a slot name - it is how the room identifies you.";
            return false;
        }
        if (slotName.Length > MaxSlotNameLength)
        {
            message =
                $"Slot names are limited to {MaxSlotNameLength} characters " +
                $"({slotName.Length} entered).";
            return false;
        }
        foreach (char character in slotName)
        {
            bool allowed =
                (character >= 'a' && character <= 'z') ||
                (character >= 'A' && character <= 'Z') ||
                (character >= '0' && character <= '9') ||
                character == ' ';
            if (!allowed)
            {
                message =
                    "Slot names may only use letters, numbers and spaces.";
                return false;
            }
        }
        if (string.Equals(slotName, ReservedSlotName, StringComparison.OrdinalIgnoreCase))
        {
            message = $"\"{ReservedSlotName}\" is reserved; pick another name.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    public static int ClampTrapChance(int trapChance) =>
        Math.Clamp(trapChance, MinTrapChance, MaxTrapChance);

    public static int ClampProgressionBalancing(int balancing) =>
        Math.Clamp(balancing, MinProgressionBalancing, MaxProgressionBalancing);

    /// <summary>The file's default name, for the save dialog.</summary>
    public static string SuggestedFileName(string slotName)
    {
        var safe = new StringBuilder(slotName.Length);
        foreach (char character in slotName)
        {
            safe.Append(
                Path.GetInvalidFileNameChars().Contains(character) ? '_' : character);
        }
        return safe.Length == 0 ? "player.yaml" : safe + ".yaml";
    }

    /// <summary>
    /// The options file itself. The slot name is quoted so a name that is
    /// all digits stays a string rather than parsing as a number.
    /// </summary>
    public static string Build(
        string slotName,
        bool traps,
        int trapChance,
        int progressionBalancing)
    {
        if (!TryValidateSlotName(slotName, out string validated, out string message))
            throw new ArgumentException(message, nameof(slotName));

        var text = new StringBuilder();
        text.Append("# Azure Dreams player options.\n");
        text.Append("# Created by the Azure Dreams Archipelago client.\n");
        text.Append("#\n");
        text.Append("# Drop this file into your Archipelago Players folder, or hand it\n");
        text.Append("# to whoever is generating the room.\n");
        text.Append('\n');
        text.Append($"name: \"{validated}\"\n");
        text.Append($"game: {GameName}\n");
        text.Append('\n');
        text.Append($"{GameName}:\n");
        text.Append("  # Moves progression earlier to reduce early dead time.\n");
        text.Append("  # 0 disables it, 50 is normal, 99 is extreme.\n");
        text.Append(
            $"  progression_balancing: {ClampProgressionBalancing(progressionBalancing)}\n");
        text.Append("  # Tower traps disguised as Progressive Keycards. They are only\n");
        text.Append("  # ever placed in your own tower - the machinery that springs one\n");
        text.Append("  # exists nowhere else.\n");
        text.Append($"  traps: {(traps ? "true" : "false")}\n");
        text.Append("  # Percent chance for each ordinary item to be a trap instead.\n");
        text.Append($"  trap_chance: {ClampTrapChance(trapChance)}\n");
        return text.ToString();
    }
}
