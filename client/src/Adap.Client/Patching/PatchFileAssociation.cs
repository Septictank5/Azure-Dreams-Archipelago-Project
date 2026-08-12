using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace Adap.Client.Patching;

/// <summary>
/// Registers the client as the handler for its own <c>.adpatch</c> files.
///
/// This is written to be the well-behaved version of a thing that is often
/// done badly, so the rules it follows are worth stating:
///
/// * Per-user only. Everything goes under <c>HKEY_CURRENT_USER\Software\
///   Classes</c>, which needs no administrator rights and cannot affect
///   another account on the machine.
/// * Only an extension we invented. <c>.adpatch</c> belongs to this project,
///   so nothing is taken away from another program. Claiming <c>.ppf</c> would
///   have meant hijacking a format shared with every other PSX patching tool.
/// * Never without being asked. The caller must have explicit consent before
///   calling <see cref="Register"/>; consent is stored so the question is
///   asked once.
/// * Always reversible, from inside the app, via <see cref="Unregister"/>.
/// * <c>UserChoice</c> is left alone. That key records the player's own
///   explicit default-app decision and is hash-protected by Windows precisely
///   because programs used to forge it. Registering a ProgID is an offer;
///   writing UserChoice would be impersonating a choice the player never made.
///
/// Two ProgIDs are registered, not one, and the second is the reason the
/// "How do you want to open this file?" picker used to reappear on every
/// double-click even after the player had chosen this client.
///
/// When the player picks an application by browsing to its executable, Windows
/// records their choice as <c>UserChoice\ProgId =
/// Applications\&lt;exe name&gt;</c> - never as our <c>ADAP.PatchFile.1</c>. If
/// nothing backs that <c>Applications\</c> ProgID with a working
/// <c>shell\open\command</c>, the choice resolves to nothing and Windows falls
/// back to the picker. That is exactly what happened here: an older build had
/// left one pointing into a per-package <c>client\</c> folder that the
/// packaging change deleted.
///
/// So this also registers <c>Applications\&lt;exe name&gt;</c>, which is our own
/// executable's name and our own application to describe. It makes a choice the
/// player already made actually work; it does not make one on their behalf.
/// </summary>
internal static class PatchFileAssociation
{
    // Channel split (2026-08-09): the stable client owns .adpatch, the dev
    // client owns .adpatch-dev, and the two register DIFFERENT ProgIDs so both
    // associations coexist per-user. The dev world only emits .adpatch-dev and
    // the promoted apworld only emits .adpatch, so a seed can never open in
    // the wrong channel's client by double-click.
#if ADAP_STABLE
    public const string Extension = ".adpatch";
    public const string ProgId = "ADAP.PatchFile.1";
    public const string FriendlyName = "Azure Dreams Archipelago patch";
#else
    public const string Extension = ".adpatch-dev";
    public const string ProgId = "ADAP.DevPatchFile.1";
    public const string FriendlyName = "Azure Dreams Archipelago dev patch";
#endif

    private const string ClassesKey = @"Software\Classes";

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern void SHChangeNotify(
        int eventId,
        uint flags,
        IntPtr item1,
        IntPtr item2);

    private const int SHCNE_ASSOCCHANGED = 0x08000000;
    private const uint SHCNF_IDLIST = 0x0000;

    /// <summary>
    /// True when the registered command points at the executable given.
    /// Separated out so the staleness check can be exercised directly: the
    /// client ships from a new folder each version, so a registration made by
    /// an earlier build always points somewhere that no longer exists.
    /// </summary>
    public static bool CommandTargets(string? command, string executablePath) =>
        !string.IsNullOrWhiteSpace(command) &&
        !string.IsNullOrWhiteSpace(executablePath) &&
        command.Contains(executablePath, StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// The <c>Applications\</c> ProgID Windows uses when the player picks this
    /// executable from the Open With dialog. Pure so the self-test can check it
    /// without touching the registry.
    /// </summary>
    public static string ApplicationProgId(string executablePath) =>
        string.IsNullOrWhiteSpace(executablePath)
            ? string.Empty
            : $@"Applications\{Path.GetFileName(executablePath)}";

    /// <summary>The command currently registered, or null when there is none.</summary>
    public static string? GetRegisteredCommand() => ReadCommand(ProgId);

    /// <summary>
    /// The command behind the <c>Applications\</c> ProgID, or null when there is
    /// none. This is the one a player's own Open With choice points at.
    /// </summary>
    public static string? GetApplicationCommand()
    {
        string application = ApplicationProgId(ExecutablePath());
        return application.Length == 0 ? null : ReadCommand(application);
    }

    private static string? ReadCommand(string progId)
    {
        try
        {
            using RegistryKey? command = Registry.CurrentUser.OpenSubKey(
                $@"{ClassesKey}\{progId}\shell\open\command");
            return command?.GetValue(null) as string;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.Security.SecurityException)
        {
            return null;
        }
    }

    /// <summary>
    /// True when this executable is the registered handler. Both ProgIDs must
    /// point here: a stale Applications command is invisible to the player right
    /// up until the picker appears, so it has to count as "not registered" and
    /// let the caller repair it.
    /// </summary>
    public static bool IsRegistered()
    {
        try
        {
            string executable = ExecutablePath();
            return CommandTargets(GetRegisteredCommand(), executable) &&
                CommandTargets(GetApplicationCommand(), executable);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.Security.SecurityException)
        {
            return false;
        }
    }

    /// <summary>
    /// Registers the handler. Only call this after the player has agreed.
    /// </summary>
    public static bool Register(out string message)
    {
        try
        {
            string executable = ExecutablePath();
            using (RegistryKey extension = Registry.CurrentUser.CreateSubKey(
                $@"{ClassesKey}\{Extension}"))
            {
                extension.SetValue(null, ProgId);
            }

            using (RegistryKey progId = Registry.CurrentUser.CreateSubKey(
                $@"{ClassesKey}\{ProgId}"))
            {
                progId.SetValue(null, FriendlyName);
            }
            using (RegistryKey icon = Registry.CurrentUser.CreateSubKey(
                $@"{ClassesKey}\{ProgId}\DefaultIcon"))
            {
                icon.SetValue(null, $"\"{executable}\",0");
            }
            using (RegistryKey command = Registry.CurrentUser.CreateSubKey(
                $@"{ClassesKey}\{ProgId}\shell\open\command"))
            {
                command.SetValue(null, $"\"{executable}\" \"%1\"");
            }

            // Offer the ProgID in Open With lists rather than only as the
            // extension's default.
            using (RegistryKey openWith = Registry.CurrentUser.CreateSubKey(
                $@"{ClassesKey}\{Extension}\OpenWithProgids"))
            {
                openWith.SetValue(ProgId, Array.Empty<byte>(), RegistryValueKind.None);
            }

            // Back the ProgID Windows records when the player picks this
            // executable in the Open With dialog. Without this their choice
            // resolves to nothing and the picker returns on every double-click.
            string application = ApplicationProgId(executable);
            if (application.Length != 0)
            {
                using (RegistryKey applicationKey = Registry.CurrentUser.CreateSubKey(
                    $@"{ClassesKey}\{application}"))
                {
                    applicationKey.SetValue("FriendlyAppName", FriendlyName);
                }
                using (RegistryKey applicationIcon = Registry.CurrentUser.CreateSubKey(
                    $@"{ClassesKey}\{application}\DefaultIcon"))
                {
                    applicationIcon.SetValue(null, $"\"{executable}\",0");
                }
                using (RegistryKey applicationCommand = Registry.CurrentUser.CreateSubKey(
                    $@"{ClassesKey}\{application}\shell\open\command"))
                {
                    applicationCommand.SetValue(null, $"\"{executable}\" \"%1\"");
                }
                using (RegistryKey supported = Registry.CurrentUser.CreateSubKey(
                    $@"{ClassesKey}\{application}\SupportedTypes"))
                {
                    supported.SetValue(Extension, string.Empty);
                }
            }

            NotifyShell();
            message = $"{Extension} files will now open with this client.";
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.Security.SecurityException)
        {
            message = $"Could not register {Extension}: {ex.Message}";
            return false;
        }
    }

    /// <summary>Removes everything <see cref="Register"/> created.</summary>
    public static bool Unregister(out string message)
    {
        try
        {
            using (RegistryKey? classes = Registry.CurrentUser.OpenSubKey(ClassesKey, writable: true))
            {
                classes?.DeleteSubKeyTree(ProgId, throwOnMissingSubKey: false);

                // Only our own executable's Applications entry, and only when it
                // still points here.
                string application = ApplicationProgId(ExecutablePath());
                if (application.Length != 0 &&
                    CommandTargets(ReadCommand(application), ExecutablePath()))
                {
                    classes?.DeleteSubKeyTree(application, throwOnMissingSubKey: false);
                }

                // Only drop the extension if it still points at us; a later
                // program may legitimately have taken it over.
                using RegistryKey? extension = classes?.OpenSubKey(Extension, writable: true);
                if (extension?.GetValue(null) as string == ProgId)
                {
                    extension.Dispose();
                    classes?.DeleteSubKeyTree(Extension, throwOnMissingSubKey: false);
                }
            }

            NotifyShell();
            message = $"{Extension} is no longer associated with this client.";
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.Security.SecurityException)
        {
            message = $"Could not remove the {Extension} association: {ex.Message}";
            return false;
        }
    }

    private static void NotifyShell()
    {
        try
        {
            SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, IntPtr.Zero, IntPtr.Zero);
        }
        catch (EntryPointNotFoundException)
        {
            // The association still works; Explorer just refreshes later.
        }
        catch (DllNotFoundException)
        {
        }
    }

    private static string ExecutablePath() =>
        Environment.ProcessPath ?? System.Reflection.Assembly.GetEntryAssembly()?.Location ?? string.Empty;
}
