using Adap.Client.Emulators;

namespace Adap.Client.Games;

/// <summary>
/// Tells the title screen whether to label its first row <c>NEW GAME</c> or
/// <c>CONTINUE</c>.
/// </summary>
/// <remarks>
/// <para>
/// The row dispatches to New Game either way - New Game <em>is</em> ADAP's load
/// path, because the checkpoint is restored at the angel. Only the label
/// changes, so the player is told whether there is anything to come back to.
/// </para>
/// <para>
/// The console cannot work this out for itself. The checkpoint is a file on
/// this machine, the memory card is gone and the disc is read-only, so the
/// client is the only thing that knows.
/// </para>
/// <para>
/// It is deliberately <em>one byte, and not the label pointer</em>. Writing the
/// pointer directly would mean landing inside the title module between its CD
/// read and the menu constructor consuming the descriptors, and a live trace
/// (<c>tools/pcsx-redux/trace-title-module.lua</c>) found nothing at all in
/// that gap - no other read, no mode load - so a poll cannot be relied on to
/// hit it. This byte instead lives in the retired memory-card driver, which is
/// resident in <c>SLUS_006.14</c> from boot and is never covered by an overlay
/// load, so it can be written at any time and as often as we like.
/// </para>
/// <para>
/// Publishing is idempotent and self-healing: the value is read back first and
/// only written when it differs, so a console reset - which reloads
/// <c>SLUS_006.14</c> and zeroes the flag - is repaired on the next poll
/// without a write on every other one.
/// </para>
/// </remarks>
internal static class AzureDreamsTitleContinueFlag
{
    /// <summary>
    /// Inside the retired card driver, whose live span is
    /// 0x8004eb3c-0x8004ee4f. The generated patch zeroes this word, so a disc
    /// running without a client shows <c>NEW GAME</c>.
    /// </summary>
    public const uint FlagAddress = 0x8004_eb3c;

    public const byte CheckpointPresent = 1;
    public const byte NoCheckpoint = 0;

    /// <summary>
    /// Publishes <paramref name="checkpointExists"/> to the game, writing only
    /// when the flag does not already say so.
    /// </summary>
    public static bool TryPublish(
        IEmulatorMemory memory,
        bool checkpointExists,
        out string? error)
    {
        byte desired = checkpointExists ? CheckpointPresent : NoCheckpoint;
        Span<byte> value = stackalloc byte[1];
        if (!memory.TryRead(FlagAddress, value, out error))
            return false;
        if (value[0] == desired)
        {
            error = null;
            return true;
        }

        value[0] = desired;
        if (!memory.TryWrite(FlagAddress, value, out error))
            return false;

        value[0] = (byte)~desired;
        if (!memory.TryRead(FlagAddress, value, out error))
            return false;
        if (value[0] != desired)
        {
            error = "The title Continue flag did not match on read-back.";
            return false;
        }

        error = null;
        return true;
    }

    /// <summary>
    /// True when a committed checkpoint exists for this seed. A pure host-file
    /// question - it needs neither the game nor the emulator.
    /// </summary>
    public static bool CheckpointExists(
        AzureDreamsSeedIdentity identity,
        string? snapshotDirectory = null)
    {
        try
        {
            return File.Exists(
                AzureDreamsTownCheckpoint.GetSnapshotPath(identity, snapshotDirectory));
        }
        catch (Exception)
        {
            // An unreadable checkpoint directory is not worth failing a poll
            // over. NEW GAME is the honest answer when we cannot tell.
            return false;
        }
    }
}
