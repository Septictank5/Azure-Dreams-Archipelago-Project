using System.Text.Json;
using System.Text.Json.Serialization;

namespace Adap.Client.Patching;

internal sealed class ClientSettings
{
    private const string SettingsFileName = "settings.json";
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        WriteIndented = true,
    };

    /// <summary>Servers offered in the dropdown. These two are built in.</summary>
    public static readonly string[] DefaultServers = ["archipelago.gg", "localhost"];

    /// <summary>
    /// Retained only so an older settings file still round-trips. The patch is
    /// chosen per session now; only the folder it came from is remembered.
    /// </summary>
    public string PatchFilePath { get; set; } = string.Empty;

    public string OriginalRomPath { get; set; } = string.Empty;

    /// <summary>Where the file browser opens, rather than a specific patch.</summary>
    public string LastPatchDirectory { get; set; } = string.Empty;

    public string SlotName { get; set; } = string.Empty;

    public string Server { get; set; } = string.Empty;

    /// <summary>Servers the player added, on top of <see cref="DefaultServers"/>.</summary>
    public List<string> SavedServers { get; set; } = [];

    /// <summary>DuckStation's executable, so the client can own the process.</summary>
    public string EmulatorPath { get; set; } = string.Empty;

    /// <summary>
    /// Null until the player has been asked about registering .adpatch, so the
    /// question is asked once and consent is never assumed.
    /// </summary>
    public bool? FileAssociationAllowed { get; set; }

    [JsonIgnore]
    public IEnumerable<string> AllServers =>
        DefaultServers.Concat(SavedServers).Distinct(StringComparer.OrdinalIgnoreCase);

    public bool AddServer(string server)
    {
        server = server.Trim();
        if (server.Length == 0)
            return false;
        if (AllServers.Contains(server, StringComparer.OrdinalIgnoreCase))
            return false;

        SavedServers.Add(server);
        return true;
    }

    public static ClientSettings Load() => Load(GetDefaultPath());

    internal static ClientSettings Load(string path)
    {
        try
        {
            if (!File.Exists(path))
                return new ClientSettings();

            string json = File.ReadAllText(path);
            ClientSettings settings =
                JsonSerializer.Deserialize<ClientSettings>(json, SerializerOptions)
                ?? new ClientSettings();
            settings.SavedServers ??= [];

            // An older file remembered one specific patch. Keep its folder,
            // which is the part still worth having, and drop the file itself.
            if (settings.LastPatchDirectory.Length == 0 &&
                settings.PatchFilePath.Length > 0)
            {
                try
                {
                    settings.LastPatchDirectory =
                        Path.GetDirectoryName(settings.PatchFilePath) ?? string.Empty;
                }
                catch (ArgumentException)
                {
                    settings.LastPatchDirectory = string.Empty;
                }
            }
            return settings;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return new ClientSettings();
        }
    }

    public void Save() => Save(GetDefaultPath());

    internal void Save(string path)
    {
        string? directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory))
            Directory.CreateDirectory(directory);

        string temporaryPath = path + ".tmp";
        File.WriteAllText(temporaryPath, JsonSerializer.Serialize(this, SerializerOptions));
        File.Move(temporaryPath, path, true);
    }

    private static string GetDefaultPath() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "ADAP",
        "Azure Dreams Archipelago Client",
        SettingsFileName);
}
