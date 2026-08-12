using Adap.Client.Emulators;

namespace Adap.Client.Games;

/// <summary>
/// Whether the client may append to the town receive queue right now.
///
/// <para>Passive town delivery is retired (2026-08-01). The town dispatcher
/// used to publish its notification through the same native new-script queue
/// an NPC talk uses, and its four safety guards - modal state, modal root, CD
/// queue, pending transition - ALL read idle in the frames between the talk
/// button going down and the NPC's modal actually existing. A receive that
/// landed in that gap owned the script queue the NPC then staged over: the
/// Nada crash and the Monster hut crash, one fault. No frame-level guard
/// closes it, because the race is against a modal that does not exist yet.
/// (The 2026-07-29 movement stop closed the neighbouring
/// walk-into-an-NPC-while-the-box-is-up variant, not this one.)</para>
///
/// <para>The game now never opens anything on its own. Nada drains the queue
/// from inside her own conversation, so the only modal that can exist across
/// a delivery is one the player deliberately opened.</para>
///
/// <para><b>This gate is not a safety mechanism.</b> The queue is
/// append-only and the game bounds each conversation by the <c>count</c> it
/// snapshotted when it armed, so an append that races the lock lands beyond
/// that snapshot and is simply delivered at the next talk. The lock exists
/// only so <c>Items received!</c> is honest about what was waiting when the
/// player asked. That is why a lock the game never dropped - a conversation
/// that exited by some path we did not think of - is treated as stale and
/// ignored rather than stalling the queue forever. Nothing can be corrupted
/// by being wrong about it in either direction.</para>
/// </summary>
internal static class AzureDreamsTownReceiveWindow
{
    /// <summary>
    /// How long a held lock is believed. A conversation is a few seconds of
    /// human input; well past that, the lock is the residue of an exit path
    /// that missed its unlock and the queue must not stay frozen behind it.
    /// </summary>
    internal static readonly TimeSpan StaleLockTimeout = TimeSpan.FromSeconds(30);

    private static DateTime? _lockedSince;
    private static string? _lastHoldMessage;

    /// <summary>
    /// True when the client may add to the queue: the game is not inside
    /// Nada's conversation, or it has been "inside" it for implausibly long.
    /// </summary>
    internal static bool AllowsAppend(
        AzureDreamsTownReceiveQueueState state,
        DateTime now)
    {
        if (!state.Locked)
        {
            _lockedSince = null;
            return true;
        }

        _lockedSince ??= now;
        if (now - _lockedSince.Value < StaleLockTimeout)
            return false;

        // Believed stale. Appending anyway is safe by construction - see the
        // class remarks - and the alternative is a queue that never fills
        // again until the town reloads.
        AnnounceHold(
            "The receive queue has been held by a conversation for over " +
            $"{StaleLockTimeout.TotalSeconds:0} seconds; treating the lock as " +
            "stale and queueing items anyway.");
        return true;
    }

    /// <summary>
    /// Reports a held or blocked delivery once per distinct reason. The poll
    /// loop runs ten times a second and a hold can last a whole town visit,
    /// so the message must not repeat.
    ///
    /// <para>This latch belongs to the ordinary queue and the lock. The gift
    /// loop uses the gift service's own <c>DeferOnce</c>: a shared latch
    /// would let the two overwrite each other's last message and print a line
    /// each on every poll while both were held.</para>
    /// </summary>
    internal static void AnnounceHold(string message)
    {
        if (message == _lastHoldMessage)
            return;
        _lastHoldMessage = message;
        Console.WriteLine(message);
    }

    internal static void AnnounceQueueFull(int waiting) =>
        AnnounceHold(
            $"{waiting} item{(waiting == 1 ? string.Empty : "s")} waiting behind a " +
            $"full receive queue. Talk to Nada at the tower entrance to collect " +
            "the ones already queued.");

    internal static void AnnounceQueued(int queued, int waiting)
    {
        string tail = waiting > 0
            ? $" ({waiting} more behind {(waiting == 1 ? "it" : "them")})"
            : string.Empty;
        AnnounceHold(
            $"{queued} item{(queued == 1 ? string.Empty : "s")} waiting with Nada " +
            $"at the tower entrance{tail}.");
    }

    /// <summary>
    /// Clears the announcement latch so the next hold speaks up again.
    /// Called once the queue has drained.
    /// </summary>
    internal static void ResetHoldAnnouncement() => _lastHoldMessage = null;

    internal const string GiftHold =
        "Gift receive waiting: gifts join the tail of the incoming queue " +
        "behind items you are already owed.";

    internal static void ResetForTest()
    {
        _lockedSince = null;
        _lastHoldMessage = null;
    }
}
