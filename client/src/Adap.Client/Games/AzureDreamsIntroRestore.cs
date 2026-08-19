using System.Buffers.Binary;
using Adap.Client.Emulators;

namespace Adap.Client.Games;

internal enum AzureDreamsIntroRestoreEvent
{
    None,
    FirstRunReleased,
    ReturningNameStaged,
    CheckpointRestored,
    InitialCheckpointCaptured,
}

internal readonly record struct AzureDreamsIntroRestoreResult(
    bool ProtocolDetected,
    bool BlocksNormalSynchronization,
    bool CheckpointLifecycleComplete,
    AzureDreamsIntroRestoreEvent Event,
    AzureDreamsCheckpointMetadata? Checkpoint,
    // Server-confirmed checks merged into the restored save block before it
    // landed (see AzureDreamsTownCheckpoint.TryRestore). Zero unless a
    // checkpoint was restored this call.
    int MergedChecks = 0);

/// <summary>
/// Services the explicit game/client handshake embedded in the angel and
/// wake-up scripts. This runs before ADSV/seed-page discovery, so a returning
/// checkpoint can stage its saved player name and restore before ordinary
/// town synchronization becomes available.
/// </summary>
internal static class AzureDreamsIntroRestore
{
    public const uint TownCoreAddress = 0x800f_b418;
    public const ushort TownCoreVersion = 5;
    public const uint TownCoreSeedSignatureAddress = TownCoreAddress + 0x20;
    public const uint PlayerNameAddress = 0x8001_020c;
    public const int PlayerNameSize = 16;
    public const uint ReturningAngelScriptAddress = 0x8001_770a;

    // The angel resource's scene-transition subroutine. ProGrammar's intro
    // skip rewrites its destination byte, so the returning script calls it
    // instead of naming a scene of its own.
    public const uint ScenePatchCallAddress = 0x8001_76fa;

    // The returning script's countdown loops back to its own yield.
    public const int TransitionDelayLoopOffset = 0x2f;
    public const uint TransitionDelayLoopAddress =
        ReturningAngelScriptAddress + TransitionDelayLoopOffset;
    // Both markers are ADSV fields (v4 gave them a word of their own; they
    // used to squat in the old 10-byte location mask's slack at +0x1A/+0x1B).
    public const uint ReturningPitaSkipMarkerAddress =
        AzureDreamsReceiveState.PersistentStateAddress +
        AzureDreamsReceiveState.IntroRestoreMarkerOffset;
    public const byte ReturningPitaSkipMarkerValue = 1;
    public const uint FirstRunReadyMarkerAddress =
        AzureDreamsReceiveState.PersistentStateAddress +
        AzureDreamsReceiveState.FirstRunReadyMarkerOffset;
    public const byte FirstRunReadyMarkerValue = 1;

    // Original angel portrait/dialogue prefix, full-width "Welcome back, ",
    // dynamic player-name token, X acknowledgement, and then the original
    // dialogue's own closing sequence. The checkpoint is already restored
    // before this is staged; a true first run retains the original resource
    // bytes.
    //
    // Script slot 0 is the first argument of the next FNO call rather than a
    // persistent variable, so setting it alone never moved anyone. After the
    // acknowledgement this replays vanilla's ending: the closing angel pose,
    // a thirty-one frame countdown that loops back to its own yield at
    // TransitionDelayLoopAddress, a text clear, and finally script
    // call 0x15 into the resource's scene-transition subroutine at
    // ScenePatchCallAddress. That subroutine holds ProGrammar's patched
    // destination byte and calls FNO 0x80, which reaches
    // begin_scene_transition_from_descriptor.
    //
    // Keep this byte-identical to the APWorld's
    // intro_skip.build_returning_angel_script.
    public static ReadOnlySpan<byte> ReturningAngelScript =>
    [
        0x57, 0x1d, 0x0f, 0x1d, 0x03,
        0x82, 0x76, 0x82, 0x85, 0x82, 0x8c, 0x82, 0x83, 0x82, 0x8f,
        0x82, 0x8d, 0x82, 0x85, 0x81, 0x40, 0x82, 0x82, 0x82, 0x81,
        0x82, 0x83, 0x82, 0x8b, 0x81, 0x43, 0x81, 0x40,
        0xfe, 0x00, 0x81, 0x49, 0x11,
        0x0f, 0x1d, 0x19,
        0x34, 0x00, 0x1e, 0x00, 0x00, 0x00,
        0x30,
        0x34, 0x01, 0x01, 0x00, 0x00, 0x00,
        0x3c, 0x00, 0x01,
        0x42, 0x00, 0x39, 0x77, 0x01, 0x80,
        0x08,
        0x15, 0xfa, 0x76, 0x01, 0x80,
        0x01,
    ];

    private static ReadOnlySpan<byte> OriginalAngelScriptPrefix =>
    [
        0x57, 0x1d, 0x0f, 0x1d, 0x03,
    ];

    public const byte ProtocolVersion = 1;
    public const byte StateFirstRun = 0;
    public const byte StateNameReady = 1;
    public const byte StateProbeRequest = 2;
    public const byte StateFirstRunReady = 3;
    public const byte StateApplyRequest = 4;
    public const byte StateApplyComplete = 5;
    public const byte StateCaptureRequest = 6;
    public const byte StateCaptureComplete = 7;

    private static readonly byte[] TownCoreMagic = "ADAPSHOP"u8.ToArray();

    /// <summary>
    /// Services the intro handshake. <paramref name="introWindowClosed"/> is a
    /// sticky per-session latch: once the boot intro is provably over, the
    /// handshake must never re-arm, because the town core's mailbox - which
    /// holds the handshake's own state byte - is reloaded from disc and reads
    /// as "first run" again after every tower return.
    /// </summary>
    public static bool TrySynchronize(
        IEmulatorMemory memory,
        AzureDreamsSeedIdentity expectedIdentity,
        ref bool introWindowClosed,
        out AzureDreamsIntroRestoreResult result,
        out string message,
        string? snapshotDirectory = null,
        IEnumerable<long>? serverCheckedLocations = null)
    {
        result = default;
        if (expectedIdentity.Signature is null ||
            expectedIdentity.Signature.Length != 8)
        {
            message = "The intro restore handshake requires an eight-byte server seed signature.";
            return false;
        }

        Span<byte> loadedMode = stackalloc byte[1];
        if (!memory.TryRead(
                AzureDreamsTownCheckpoint.LoadedModeAddress,
                loadedMode,
                out string? readError))
        {
            message = readError ?? "Could not inspect the loaded Azure Dreams mode.";
            return false;
        }
        if (loadedMode[0] != AzureDreamsTownCheckpoint.TownMode)
        {
            // Reaching the tower proves the boot intro finished. The town
            // overlay that returns afterwards carries a freshly zeroed
            // mailbox, so this latch is the only surviving evidence.
            if (loadedMode[0] == AzureDreamsTownCheckpoint.TowerMode)
                introWindowClosed = true;
            message = string.Empty;
            return true;
        }

        Span<byte> coreHeader = stackalloc byte[0x28];
        if (!memory.TryRead(TownCoreAddress, coreHeader, out readError))
        {
            message = readError ?? "Could not inspect the generated town core.";
            return false;
        }
        if (!coreHeader[..TownCoreMagic.Length].SequenceEqual(TownCoreMagic))
        {
            message = string.Empty;
            return true;
        }
        ushort coreVersion = BinaryPrimitives.ReadUInt16LittleEndian(coreHeader[8..]);
        if (coreVersion != TownCoreVersion)
        {
            message =
                $"Town core version mismatch: expected {TownCoreVersion}, observed {coreVersion}.";
            return false;
        }
        if (!coreHeader[0x20..0x28].SequenceEqual(expectedIdentity.Signature))
        {
            message =
                "The loaded town belongs to a different generated seed than the connected slot.";
            return false;
        }

        Span<byte> mailbox = stackalloc byte[AzureDreamsTownMailbox.Size];
        if (!memory.TryRead(AzureDreamsTownMailbox.Address, mailbox, out readError))
        {
            message = readError ?? "Could not inspect the intro restore mailbox.";
            return false;
        }
        bool mailboxMatches =
            BinaryPrimitives.ReadUInt32LittleEndian(mailbox) ==
                AzureDreamsTownMailbox.Magic &&
            BinaryPrimitives.ReadUInt16LittleEndian(
                mailbox[AzureDreamsTownMailbox.ProtocolVersionOffset..]) ==
                AzureDreamsTownMailbox.ProtocolVersion &&
            BinaryPrimitives.ReadUInt16LittleEndian(
                mailbox[AzureDreamsTownMailbox.StructureSizeOffset..]) ==
                AzureDreamsTownMailbox.Size;
        if (!mailboxMatches)
        {
            message = "The generated town core does not contain the expected receive mailbox.";
            return false;
        }

        byte protocol = mailbox[AzureDreamsTownMailbox.IntroRestoreProtocolOffset];
        if (protocol != ProtocolVersion)
        {
            // A pre-handshake ROM can still use the stable-town fallback.
            result = new AzureDreamsIntroRestoreResult(
                false,
                false,
                false,
                AzureDreamsIntroRestoreEvent.None,
                null);
            message = string.Empty;
            return true;
        }

        byte state = mailbox[AzureDreamsTownMailbox.IntroRestoreStateOffset];
        if (state is StateFirstRun or StateProbeRequest)
        {
            // A tower return reloads the town overlay, which restores the
            // mailbox's as-patched contents and drops this byte back to
            // StateFirstRun mid-session. Without a second opinion the client
            // would wait forever for an angel resource that cannot load again,
            // blocking every location check and item delivery until the game
            // is restarted. A populated save block is that second opinion.
            if (!introWindowClosed)
            {
                if (!AzureDreamsReceiveState.TryReadSaveIsPristine(
                        memory,
                        out bool pristine,
                        out message))
                {
                    return false;
                }
                if (!pristine)
                    introWindowClosed = true;
            }
            if (introWindowClosed)
            {
                result = new AzureDreamsIntroRestoreResult(
                    true,
                    false,
                    true,
                    AzureDreamsIntroRestoreEvent.None,
                    null);
                message = string.Empty;
                return true;
            }
        }
        if (state is StateApplyComplete or StateCaptureComplete)
        {
            introWindowClosed = true;
            result = new AzureDreamsIntroRestoreResult(
                true,
                false,
                true,
                AzureDreamsIntroRestoreEvent.None,
                null);
            message = string.Empty;
            return true;
        }
        if (state == StateFirstRunReady)
        {
            introWindowClosed = true;
            result = new AzureDreamsIntroRestoreResult(
                true,
                false,
                false,
                AzureDreamsIntroRestoreEvent.None,
                null);
            message = string.Empty;
            return true;
        }
        if (state == StateNameReady)
        {
            result = new AzureDreamsIntroRestoreResult(
                true,
                true,
                false,
                AzureDreamsIntroRestoreEvent.None,
                null);
            message = string.Empty;
            return true;
        }
        if (state is StateFirstRun or StateProbeRequest)
        {
            string path = AzureDreamsTownCheckpoint.GetSnapshotPath(
                expectedIdentity,
                snapshotDirectory);
            if (!File.Exists(path))
            {
                if (state == StateProbeRequest)
                {
                    if (!TryWriteState(memory, StateFirstRun, out message))
                        return false;
                }

                Span<byte> ready = stackalloc byte[1];
                if (!memory.TryRead(
                        FirstRunReadyMarkerAddress,
                        ready,
                        out readError))
                {
                    message =
                        readError ?? "Could not inspect the first-run checkpoint marker.";
                    return false;
                }
                if (ready[0] != FirstRunReadyMarkerValue)
                {
                    result = new AzureDreamsIntroRestoreResult(
                        true,
                        true,
                        false,
                        AzureDreamsIntroRestoreEvent.None,
                        null);
                    message = string.Empty;
                    return true;
                }

                ready[0] = 0;
                if (!memory.TryWrite(
                        FirstRunReadyMarkerAddress,
                        ready,
                        out string? readyWriteError))
                {
                    message =
                        readyWriteError ??
                        "Could not clear the first-run checkpoint marker.";
                    return false;
                }
                if (!TryWriteState(memory, StateFirstRunReady, out message))
                    return false;
                introWindowClosed = true;
                result = new AzureDreamsIntroRestoreResult(
                    true,
                    false,
                    false,
                    AzureDreamsIntroRestoreEvent.FirstRunReleased,
                    null);
                message = string.Empty;
                return true;
            }

            // The town core is resident before this dialogue resource. Wait
            // until its original router bytes are actually present so an
            // ensuing CD load cannot overwrite the returning script.
            Span<byte> originalPrefix =
                stackalloc byte[OriginalAngelScriptPrefix.Length];
            if (!memory.TryRead(
                    ReturningAngelScriptAddress,
                    originalPrefix,
                    out readError))
            {
                message =
                    readError ?? "Could not inspect the live angel dialogue resource.";
                return false;
            }
            if (!originalPrefix.SequenceEqual(OriginalAngelScriptPrefix))
            {
                result = new AzureDreamsIntroRestoreResult(
                    true,
                    true,
                    false,
                    AzureDreamsIntroRestoreEvent.None,
                    null);
                message = string.Empty;
                return true;
            }

            if (!AzureDreamsTownCheckpoint.TryRestore(
                    memory,
                    expectedIdentity,
                    serverCheckedLocations,
                    out AzureDreamsCheckpointMetadata metadata,
                    out int mergedChecks,
                    out message,
                    snapshotDirectory))
            {
                return false;
            }

            Span<byte> marker = stackalloc byte[1];
            marker[0] = ReturningPitaSkipMarkerValue;
            if (!memory.TryWrite(
                    ReturningPitaSkipMarkerAddress,
                    marker,
                    out string? writeError) ||
                !memory.TryRead(
                    ReturningPitaSkipMarkerAddress,
                    marker,
                    out readError) ||
                marker[0] != ReturningPitaSkipMarkerValue)
            {
                message =
                    writeError ?? readError ??
                    "Could not arm the returning-player Pita guard.";
                return false;
            }
            if (!memory.TryWrite(
                    ReturningAngelScriptAddress,
                    ReturningAngelScript,
                    out writeError))
            {
                message = writeError ?? "Could not stage the returning angel dialogue.";
                return false;
            }
            byte[] observedScript = new byte[ReturningAngelScript.Length];
            if (!memory.TryRead(
                    ReturningAngelScriptAddress,
                    observedScript,
                    out readError))
            {
                message = readError ?? "Could not verify the returning angel dialogue.";
                return false;
            }
            if (!observedScript.AsSpan().SequenceEqual(ReturningAngelScript))
            {
                message = "The game changed the returning angel dialogue before it could be verified.";
                return false;
            }
            if (!TryWriteState(memory, StateApplyComplete, out message))
                return false;
            introWindowClosed = true;

            result = new AzureDreamsIntroRestoreResult(
                true,
                false,
                true,
                AzureDreamsIntroRestoreEvent.CheckpointRestored,
                metadata,
                mergedChecks);
            return true;
        }

        if (state == StateApplyRequest)
        {
            if (!AzureDreamsTownCheckpoint.TryRestore(
                    memory,
                    expectedIdentity,
                    serverCheckedLocations,
                    out AzureDreamsCheckpointMetadata metadata,
                    out int mergedChecks,
                    out message,
                    snapshotDirectory))
            {
                return false;
            }
            if (!TryWriteState(memory, StateApplyComplete, out message))
                return false;
            introWindowClosed = true;

            result = new AzureDreamsIntroRestoreResult(
                true,
                false,
                true,
                AzureDreamsIntroRestoreEvent.CheckpointRestored,
                metadata,
                mergedChecks);
            return true;
        }

        if (state == StateCaptureRequest)
        {
            if (!AzureDreamsTownCheckpoint.TryCapture(
                    memory,
                    expectedIdentity,
                    AzureDreamsCheckpointReason.InitialTown,
                    out AzureDreamsCheckpointMetadata metadata,
                    out message,
                    snapshotDirectory))
            {
                return false;
            }
            if (!TryWriteState(memory, StateCaptureComplete, out message))
                return false;
            introWindowClosed = true;

            result = new AzureDreamsIntroRestoreResult(
                true,
                false,
                true,
                AzureDreamsIntroRestoreEvent.InitialCheckpointCaptured,
                metadata);
            return true;
        }

        message = $"The game published unknown intro restore state {state}.";
        return false;
    }

    private static bool TryWriteState(
        IEmulatorMemory memory,
        byte state,
        out string message)
    {
        Span<byte> value = stackalloc byte[1];
        value[0] = state;
        uint address =
            AzureDreamsTownMailbox.Address +
            AzureDreamsTownMailbox.IntroRestoreStateOffset;
        if (!memory.TryWrite(address, value, out string? writeError))
        {
            message = writeError ?? "Could not publish the intro restore response.";
            return false;
        }
        value[0] = 0xff;
        if (!memory.TryRead(address, value, out string? readError))
        {
            message = readError ?? "Could not verify the intro restore response.";
            return false;
        }
        if (value[0] != state)
        {
            message = "The intro restore response did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }
}
