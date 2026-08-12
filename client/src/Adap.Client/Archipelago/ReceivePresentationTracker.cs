namespace Adap.Client.Archipelago;

/// <summary>
/// Decides whether a received item gets the native pickup/dialogue
/// presentation. Self-sends from the player's own locations are normally
/// silent - the game already presented the item at hand, and a second
/// pickup animation would double up - but one that has waited in the
/// delivery queue has lost that at-hand context, and delivering it
/// invisibly reads as a missing pickup. So a location-suppressed item is
/// promoted to a visible presentation once it has waited at least
/// <see cref="SelfSendPresentationDelay"/> in the queue.
///
/// The decision for an index is latched on first use: the presentation
/// flag is baked into the staged mailbox request and verified on every
/// poll, so flipping it while a request is in flight would make the
/// staged request read as corrupt and stall the queue.
/// </summary>
internal sealed class ReceivePresentationTracker
{
    internal static readonly TimeSpan SelfSendPresentationDelay =
        TimeSpan.FromSeconds(1);

    private readonly Dictionary<long, DateTime> _firstSeen = new();
    private readonly Dictionary<long, bool> _decisions = new();

    /// <summary>
    /// Records when a pending history index was first observed waiting.
    /// Later observations of the same index keep the original stamp.
    /// </summary>
    public void ObserveQueued(long historyIndex, DateTime now)
    {
        _firstSeen.TryAdd(historyIndex, now);
    }

    /// <summary>
    /// The presentation decision for one history index.
    /// <paramref name="showByLocationRule"/> is the location-based rule
    /// (<c>ShouldShowReceivePresentation</c>); a suppressed item is
    /// promoted once its queue wait reaches the delay. The first answer
    /// for an index is permanent.
    /// </summary>
    public bool DecidePresentation(long historyIndex, bool showByLocationRule, DateTime now)
    {
        if (_decisions.TryGetValue(historyIndex, out bool decided))
            return decided;

        bool show = showByLocationRule;
        if (!show &&
            _firstSeen.TryGetValue(historyIndex, out DateTime seen) &&
            now - seen >= SelfSendPresentationDelay)
        {
            show = true;
        }

        _decisions[historyIndex] = show;
        return show;
    }
}
