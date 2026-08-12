using System.Buffers.Binary;
using System.Security.Cryptography;
using Adap.Client.Emulators;

namespace Adap.Client.Games;

internal enum AzureDreamsCheckpointReason : uint
{
    InitialTown = 1,
    TowerReturn = 2,
    ShopPurchase = 3,
    TownReceiveAcknowledged = 4,
    /// <summary>
    /// Taken on arrival at a tower floor, which is the only moment a tower
    /// snapshot is internally consistent: the game refreshes Koh's stats
    /// mirror at `0x80012194` on a floor transition and not continuously, and
    /// the familiar's live record at `0x80012260` is written back at the same
    /// time. It is also the state a resume wants - the inventory you rode the
    /// elevator up with.
    ///
    /// Restoring one of these arms a native resume: the game's own card-load
    /// path takes it from there. A TOWN checkpoint restored into a tower entry
    /// is not the same thing and crashes - measured 2026-08-11, the stats
    /// mirror read all zeros and the inventory pointer table pointed at
    /// descriptors that were not there.
    /// </summary>
    TowerFloorEntry = 5,
}

[Flags]
internal enum AzureDreamsTownStabilityGuard
{
    None = 0,
    ModeLoadPending = 1 << 0,
    ModalRoot = 1 << 2,
    CdQueueBusy = 1 << 3,
    MailboxMissing = 1 << 4,
}

internal readonly record struct AzureDreamsTownObservation(
    byte LoadedMode,
    bool IsStableTown,
    AzureDreamsTownStabilityGuard BlockingGuards = AzureDreamsTownStabilityGuard.None)
{
    public bool IsTown => LoadedMode == AzureDreamsTownCheckpoint.TownMode;

    public bool IsTower => LoadedMode == AzureDreamsTownCheckpoint.TowerMode;
}

internal readonly record struct AzureDreamsCheckpointMetadata(
    string Path,
    AzureDreamsCheckpointReason Reason,
    long CreatedUnixMilliseconds,
    uint ReceiveCursor,
    uint ShopMask,
    byte[] SeedSignature);

/// <summary>
/// Detects the live town/tower lifecycle and stores an atomic, client-owned
/// copy of Azure Dreams' complete save-backed RAM block. A validated copy can
/// be restored once at a stable-town startup boundary.
/// </summary>
internal static class AzureDreamsTownCheckpoint
{
    public const uint SaveBlockAddress = 0x8001_0000;
    public const int SaveBlockSize = 0x6000;

    public const uint LoadedModeAddress = 0x8008_2e6a;
    public const byte TownMode = 1;
    public const byte TowerMode = 2;
    public const uint ModeLoadPendingAddress = 0x8008_2e6e;
    public const uint TownModalStateAddress = 0x8008_1eb0;
    public const uint TownModalRootAddress = 0x8008_2bc0;
    public const uint CdQueueHeadAddress = 0x8008_14d0;
    public const uint CdQueueTailAddress = 0x8008_14d1;

    /// <summary>The save-backed floor halfword; 1-40 means a real floor.</summary>
    public const uint CurrentFloorAddress = 0x8001_0234;
    public const int TopFloor = 40;

    // --- resuming a tower checkpoint -------------------------------------
    //
    // Restoring a tower checkpoint is only half the job: the game has to be
    // told to resume rather than walk into town. Its own memory-card load does
    // that with four resident operations, and the seed page carries a stub on
    // the angel's scene-transition handler that runs them when this trigger is
    // armed (`town_warp._build_resume_warp_stub`).
    //
    // The trigger lives inside the 24 KiB this class restores, which is why it
    // must be written AFTER the payload - and why a hand-staged resume had to
    // wait for the restore. Here that ordering is free.
    public const uint ResumeTriggerAddress = 0x8001_5850;
    public const uint ResumeTriggerValue = 1;

    /// <summary>
    /// The saved-in-tower flag the load path branches on to choose game mode 5
    /// (tower) over 6 (town). A live tower run does not set it - only the
    /// vanilla elevator save did - so a restore has to.
    /// </summary>
    public const uint SavedInTowerFlagAddress = 0x8001_0208;

    private const uint SnapshotMagic = 0x5043_4441; // "ADCP"
    private const ushort SnapshotVersion = 1;
    private const ushort SnapshotHeaderSize = 0x50;
    private const int SnapshotHashOffset = 0x30;
    private const int SnapshotHashSize = 32;
    private const string SnapshotExtension = ".adcp";

    public static bool TryObserve(
        IEmulatorMemory memory,
        out AzureDreamsTownObservation observation,
        out string message)
    {
        observation = default;
        Span<byte> mode = stackalloc byte[1];
        if (!memory.TryRead(LoadedModeAddress, mode, out string? modeError))
        {
            message = modeError ?? "Could not read the loaded Azure Dreams mode.";
            return false;
        }

        if (mode[0] != TownMode)
        {
            observation = new AzureDreamsTownObservation(mode[0], false);
            message = string.Empty;
            return true;
        }

        if (!AzureDreamsTownMailbox.TryDetect(memory, out bool mailboxDetected, out message))
            return false;
        if (!mailboxDetected)
        {
            observation = new AzureDreamsTownObservation(
                TownMode,
                false,
                AzureDreamsTownStabilityGuard.MailboxMissing);
            message = string.Empty;
            return true;
        }

        Span<byte> loadPending = stackalloc byte[1];
        Span<byte> modalState = stackalloc byte[4];
        Span<byte> modalRoot = stackalloc byte[4];
        Span<byte> cdQueue = stackalloc byte[2];
        if (!memory.TryRead(ModeLoadPendingAddress, loadPending, out string? readError) ||
            !memory.TryRead(TownModalStateAddress, modalState, out readError) ||
            !memory.TryRead(TownModalRootAddress, modalRoot, out readError) ||
            !memory.TryRead(CdQueueHeadAddress, cdQueue, out readError))
        {
            message = readError ?? "Could not inspect the stable-town checkpoint guards.";
            return false;
        }

        // The town window context at TownModalStateAddress is deliberately not
        // a stability guard. Its first word is set to 1 by the initializer at
        // 0x80033C1C when the town's first window is created and never returns
        // to 0, so requiring it to be zero only ever passed immediately after
        // a town overlay reload. That is why tower returns checkpointed while
        // the first town entry after the angel never did.
        //
        // The live signal is the window list head at TownModalRootAddress,
        // which 0x8003FF2C inserts into and which returns to 0 once the last
        // window closes.
        AzureDreamsTownStabilityGuard blocking = AzureDreamsTownStabilityGuard.None;
        if (loadPending[0] != 0)
            blocking |= AzureDreamsTownStabilityGuard.ModeLoadPending;
        if (BinaryPrimitives.ReadUInt32LittleEndian(modalRoot) != 0)
            blocking |= AzureDreamsTownStabilityGuard.ModalRoot;
        if (cdQueue[0] != cdQueue[1])
            blocking |= AzureDreamsTownStabilityGuard.CdQueueBusy;

        bool stable = blocking == AzureDreamsTownStabilityGuard.None;
        observation = new AzureDreamsTownObservation(TownMode, stable, blocking);
        message = string.Empty;
        return true;
    }

    public static string GetDefaultSnapshotDirectory() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "ADAP",
        "Azure Dreams Archipelago Client",
        "checkpoints");

    public static string GetSnapshotPath(
        AzureDreamsSeedIdentity identity,
        string? snapshotDirectory = null)
    {
        string directory = snapshotDirectory ?? GetDefaultSnapshotDirectory();
        return Path.Combine(
            directory,
            Convert.ToHexString(identity.Signature) + SnapshotExtension);
    }

    public static bool TryCapture(
        IEmulatorMemory memory,
        AzureDreamsSeedIdentity identity,
        AzureDreamsCheckpointReason reason,
        out AzureDreamsCheckpointMetadata metadata,
        out string message,
        string? snapshotDirectory = null)
    {
        metadata = default;
        if (identity.Signature is null || identity.Signature.Length != 8)
        {
            message = "A town checkpoint requires an eight-byte seed signature.";
            return false;
        }

        byte[] payload = new byte[SaveBlockSize];
        if (!memory.TryRead(SaveBlockAddress, payload, out string? readError))
        {
            message = readError ?? "Could not read the Azure Dreams save-backed RAM block.";
            return false;
        }
        if (!TryValidatePayloadIdentity(payload, identity, out message))
            return false;

        uint receiveCursor = BinaryPrimitives.ReadUInt32LittleEndian(
            payload.AsSpan(
                checked((int)(AzureDreamsReceiveState.PersistentStateAddress - SaveBlockAddress)) +
                AzureDreamsReceiveState.ReceivedItemCountOffset));
        uint shopMask = BinaryPrimitives.ReadUInt32LittleEndian(
            payload.AsSpan(
                checked((int)(AzureDreamsReceiveState.PersistentStateAddress - SaveBlockAddress)) +
                AzureDreamsReceiveState.PersistentShopMaskOffset));
        long created = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        byte[] hash = SHA256.HashData(payload);

        byte[] header = new byte[SnapshotHeaderSize];
        BinaryPrimitives.WriteUInt32LittleEndian(header, SnapshotMagic);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(4), SnapshotVersion);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(6), SnapshotHeaderSize);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(8), SaveBlockAddress);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(12), (uint)SaveBlockSize);
        identity.Signature.CopyTo(header, 16);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(24), receiveCursor);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(28), shopMask);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(32), (uint)reason);
        BinaryPrimitives.WriteInt64LittleEndian(header.AsSpan(40), created);
        hash.CopyTo(header, SnapshotHashOffset);

        string path = GetSnapshotPath(identity, snapshotDirectory);
        string? directory = Path.GetDirectoryName(path);
        string temporaryPath = path + ".tmp";
        try
        {
            if (!string.IsNullOrEmpty(directory))
                Directory.CreateDirectory(directory);

            using (FileStream stream = new(
                       temporaryPath,
                       FileMode.Create,
                       FileAccess.Write,
                       FileShare.None,
                       16 * 1024,
                       FileOptions.WriteThrough))
            {
                stream.Write(header);
                stream.Write(payload);
                stream.Flush(flushToDisk: true);
            }
            File.Move(temporaryPath, path, overwrite: true);
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or
            System.Security.SecurityException)
        {
            TryDeleteTemporaryFile(temporaryPath);
            message = $"Could not save the town checkpoint: {ex.Message}";
            return false;
        }

        metadata = new AzureDreamsCheckpointMetadata(
            path,
            reason,
            created,
            receiveCursor,
            shopMask,
            identity.Signature.ToArray());
        message = string.Empty;
        return true;
    }

    public static bool TryRead(
        string path,
        AzureDreamsSeedIdentity expectedIdentity,
        out byte[] payload,
        out AzureDreamsCheckpointMetadata metadata,
        out string message)
    {
        payload = [];
        metadata = default;
        try
        {
            byte[] file = File.ReadAllBytes(path);
            if (file.Length != SnapshotHeaderSize + SaveBlockSize)
            {
                message =
                    $"Town checkpoint size mismatch: expected {SnapshotHeaderSize + SaveBlockSize}, " +
                    $"observed {file.Length}.";
                return false;
            }

            ReadOnlySpan<byte> header = file.AsSpan(0, SnapshotHeaderSize);
            if (BinaryPrimitives.ReadUInt32LittleEndian(header) != SnapshotMagic ||
                BinaryPrimitives.ReadUInt16LittleEndian(header[4..]) != SnapshotVersion ||
                BinaryPrimitives.ReadUInt16LittleEndian(header[6..]) != SnapshotHeaderSize ||
                BinaryPrimitives.ReadUInt32LittleEndian(header[8..]) != SaveBlockAddress ||
                BinaryPrimitives.ReadUInt32LittleEndian(header[12..]) != (uint)SaveBlockSize)
            {
                message = "The town checkpoint header is not supported by this client.";
                return false;
            }
            if (expectedIdentity.Signature is null ||
                expectedIdentity.Signature.Length != 8 ||
                !header[16..24].SequenceEqual(expectedIdentity.Signature))
            {
                message = "The town checkpoint belongs to a different generated seed.";
                return false;
            }

            payload = file.AsSpan(SnapshotHeaderSize, SaveBlockSize).ToArray();
            byte[] actualHash = SHA256.HashData(payload);
            if (!CryptographicOperations.FixedTimeEquals(
                    actualHash,
                    header.Slice(SnapshotHashOffset, SnapshotHashSize)))
            {
                payload = [];
                message = "The town checkpoint payload checksum is invalid.";
                return false;
            }
            if (!TryValidatePayloadIdentity(payload, expectedIdentity, out message))
            {
                payload = [];
                return false;
            }

            int persistentOffset = checked(
                (int)(AzureDreamsReceiveState.PersistentStateAddress - SaveBlockAddress));
            uint receiveCursor = BinaryPrimitives.ReadUInt32LittleEndian(header[24..]);
            uint shopMask = BinaryPrimitives.ReadUInt32LittleEndian(header[28..]);
            uint payloadReceiveCursor = BinaryPrimitives.ReadUInt32LittleEndian(
                payload.AsSpan(
                    persistentOffset +
                    AzureDreamsReceiveState.ReceivedItemCountOffset));
            uint payloadShopMask = BinaryPrimitives.ReadUInt32LittleEndian(
                payload.AsSpan(
                    persistentOffset +
                    AzureDreamsReceiveState.PersistentShopMaskOffset));
            if (receiveCursor != payloadReceiveCursor || shopMask != payloadShopMask)
            {
                payload = [];
                message =
                    "The town checkpoint header does not match its saved receive/shop state.";
                return false;
            }

            AzureDreamsCheckpointReason reason =
                (AzureDreamsCheckpointReason)BinaryPrimitives.ReadUInt32LittleEndian(header[32..]);
            if (!Enum.IsDefined(reason))
            {
                payload = [];
                message = $"The town checkpoint has unknown reason value {(uint)reason}.";
                return false;
            }

            metadata = new AzureDreamsCheckpointMetadata(
                path,
                reason,
                BinaryPrimitives.ReadInt64LittleEndian(header[40..]),
                receiveCursor,
                shopMask,
                header[16..24].ToArray());
            message = string.Empty;
            return true;
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or
            System.Security.SecurityException)
        {
            payload = [];
            message = $"Could not read the town checkpoint: {ex.Message}";
            return false;
        }
    }

    public static bool TryRestore(
        IEmulatorMemory memory,
        AzureDreamsSeedIdentity identity,
        out AzureDreamsCheckpointMetadata metadata,
        out string message,
        string? snapshotDirectory = null)
    {
        metadata = default;
        string path = GetSnapshotPath(identity, snapshotDirectory);
        if (!TryRead(path, identity, out byte[] payload, out metadata, out message))
            return false;

        if (!memory.TryWrite(SaveBlockAddress, payload, out string? writeError))
        {
            message = writeError ?? "Could not restore the Azure Dreams save-backed RAM block.";
            return false;
        }

        int persistentOffset = checked(
            (int)(AzureDreamsReceiveState.PersistentStateAddress - SaveBlockAddress));
        Span<byte> restoredState = stackalloc byte[AzureDreamsReceiveState.PersistentStateSize];
        if (!memory.TryRead(
                AzureDreamsReceiveState.PersistentStateAddress,
                restoredState,
                out string? readError))
        {
            message = readError ?? "Could not verify the restored ADSV state.";
            return false;
        }
        if (!restoredState.SequenceEqual(
                payload.AsSpan(
                    persistentOffset,
                    AzureDreamsReceiveState.PersistentStateSize)))
        {
            message = "The game changed the restored ADSV state before it could be verified.";
            return false;
        }

        if (metadata.Reason == AzureDreamsCheckpointReason.TowerFloorEntry &&
            !TryArmTowerResume(memory, out message))
        {
            return false;
        }

        message = string.Empty;
        return true;
    }

    /// <summary>
    /// Tells the game to resume the restored tower run instead of walking into
    /// town: set the saved-in-tower flag the load path branches on, then arm
    /// the trigger the seed-page stub tests when the angel hands over.
    ///
    /// <para>Flag first, trigger last. The stub only reads the trigger, so a
    /// half-written pair reads as "nothing pending" and the player lands in
    /// town with an intact save rather than in a tower entry missing the flag
    /// that chooses tower mode.</para>
    /// </summary>
    private static bool TryArmTowerResume(IEmulatorMemory memory, out string message)
    {
        Span<byte> flag = stackalloc byte[2];
        BinaryPrimitives.WriteUInt16LittleEndian(flag, 1);
        if (!memory.TryWrite(SavedInTowerFlagAddress, flag, out string? flagError))
        {
            message = flagError ?? "Could not set the saved-in-tower flag.";
            return false;
        }

        Span<byte> trigger = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(trigger, ResumeTriggerValue);
        if (!memory.TryWrite(ResumeTriggerAddress, trigger, out string? triggerError))
        {
            message = triggerError ?? "Could not arm the tower-resume trigger.";
            return false;
        }

        Span<byte> observed = stackalloc byte[4];
        if (!memory.TryRead(ResumeTriggerAddress, observed, out string? readBack) ||
            !observed.SequenceEqual(trigger))
        {
            message = readBack ?? "The tower-resume trigger did not match on read-back.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    private static bool TryValidatePayloadIdentity(
        ReadOnlySpan<byte> payload,
        AzureDreamsSeedIdentity identity,
        out string message)
    {
        int persistentOffset = checked(
            (int)(AzureDreamsReceiveState.PersistentStateAddress - SaveBlockAddress));
        ReadOnlySpan<byte> state = payload[persistentOffset..];
        if (BinaryPrimitives.ReadUInt32LittleEndian(state) !=
                AzureDreamsReceiveState.PersistentStateMagic ||
            BinaryPrimitives.ReadUInt16LittleEndian(state[4..]) !=
                AzureDreamsReceiveState.PersistentStateVersion ||
            BinaryPrimitives.ReadUInt16LittleEndian(state[6..]) !=
                AzureDreamsReceiveState.PersistentStateSize)
        {
            message = "The save-backed RAM block does not contain initialized ADSV state.";
            return false;
        }
        if (!state[8..16].SequenceEqual(identity.Signature))
        {
            message = "The save-backed RAM block belongs to a different generated seed.";
            return false;
        }

        message = string.Empty;
        return true;
    }

    private static void TryDeleteTemporaryFile(string path)
    {
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or
            System.Security.SecurityException)
        {
            // The original save failure is more useful than cleanup failure.
        }
    }
}

internal sealed class AzureDreamsTownCheckpointCoordinator
{
    private readonly string? _snapshotDirectory;
    private byte[]? _seedSignature;
    private bool _snapshotExists;
    private bool _restorePending;
    private bool _towerObserved;
    private bool _shopPurchasePending;
    private bool _townReceivePending;
    // The floor whose arrival is already snapshotted, so the per-frame poll
    // captures once per floor instead of once per frame.
    private int _lastTowerFloorCaptured;

    public AzureDreamsTownCheckpointCoordinator(string? snapshotDirectory = null)
    {
        _snapshotDirectory = snapshotDirectory;
    }

    public void ResetGameObservation()
    {
        _seedSignature = null;
        _snapshotExists = false;
        _restorePending = false;
        _towerObserved = false;
        _shopPurchasePending = false;
        _townReceivePending = false;
    }

    /// <summary>
    /// True while some capture is actually waiting on a settled town frame.
    /// Town is unsettled constantly as the CD queue streams, so reporting a
    /// blocked guard without this would claim a checkpoint is stuck whenever
    /// none is wanted.
    /// </summary>
    public bool CapturePending =>
        !_snapshotExists ||
        _towerObserved ||
        _shopPurchasePending ||
        _townReceivePending;

    public void RequestShopPurchaseCheckpoint() => _shopPurchasePending = true;

    public void RequestTownReceiveCheckpoint() => _townReceivePending = true;

    public void AcceptIntroCheckpoint(AzureDreamsSeedIdentity identity)
    {
        EnsureIdentity(identity);
        _snapshotExists = true;
        _restorePending = false;
        _towerObserved = false;
        _shopPurchasePending = false;
        _townReceivePending = false;
    }

    public bool TryRestoreAtStartup(
        IEmulatorMemory memory,
        AzureDreamsSeedIdentity identity,
        AzureDreamsTownObservation observation,
        bool saveIsPristine,
        out bool restorePending,
        out AzureDreamsCheckpointMetadata? restoredCheckpoint,
        out bool staleRestoreDropped,
        out string message)
    {
        restorePending = false;
        restoredCheckpoint = null;
        staleRestoreDropped = false;
        EnsureIdentity(identity);
        if (!_restorePending)
        {
            message = string.Empty;
            return true;
        }

        // Attaching while a tower trip is already active must not roll it back.
        // Observe it normally and commit only after its legitimate town return.
        if (observation.IsTower)
        {
            _restorePending = false;
            message = string.Empty;
            return true;
        }
        // A populated save block means the running game already carries live
        // progress, so the pending restore is the residue of losing sight of
        // the game - a transient identity read failure, a client restart
        // against a live session - not of a game load. Restoring over it
        // would BE a mid-session rollback: inventory and the durable receive
        // cursor revert, and everything past the snapshot re-delivers.
        //
        // Every legitimate load reaches town with a pristine block: a console
        // reset re-initializes it, and the main-menu continue path populates
        // it through the intro handshake, whose AcceptIntroCheckpoint clears
        // the pending flag before this can fire. This fallback therefore only
        // restores into a block that provably holds no progress to lose.
        if (!saveIsPristine)
        {
            _restorePending = false;
            staleRestoreDropped = true;
            message = string.Empty;
            return true;
        }
        if (!observation.IsStableTown)
        {
            restorePending = true;
            message = string.Empty;
            return true;
        }

        if (!AzureDreamsTownCheckpoint.TryRestore(
                memory,
                identity,
                out AzureDreamsCheckpointMetadata restored,
                out message,
                _snapshotDirectory))
        {
            return false;
        }

        _restorePending = false;
        _towerObserved = false;
        _shopPurchasePending = false;
        _townReceivePending = false;
        restoredCheckpoint = restored;
        message = string.Empty;
        return true;
    }

    public bool TryObserveAndCommitBoundary(
        IEmulatorMemory memory,
        AzureDreamsSeedIdentity identity,
        out AzureDreamsTownObservation observation,
        out AzureDreamsCheckpointMetadata? checkpoint,
        out string message)
    {
        checkpoint = null;
        EnsureIdentity(identity);
        if (!AzureDreamsTownCheckpoint.TryObserve(memory, out observation, out message))
            return false;

        if (observation.IsTower)
        {
            _towerObserved = true;
            return TryCaptureTowerFloor(memory, identity, out checkpoint, out message);
        }
        if (!observation.IsStableTown)
        {
            message = string.Empty;
            return true;
        }

        AzureDreamsCheckpointReason? reason = _towerObserved
            ? AzureDreamsCheckpointReason.TowerReturn
            : !_snapshotExists
                ? AzureDreamsCheckpointReason.InitialTown
                : null;
        if (reason is null)
        {
            message = string.Empty;
            return true;
        }
        if (!AzureDreamsTownCheckpoint.TryCapture(
                memory,
                identity,
                reason.Value,
                out AzureDreamsCheckpointMetadata saved,
                out message,
                _snapshotDirectory))
        {
            return false;
        }

        _snapshotExists = true;
        _towerObserved = false;
        checkpoint = saved;
        message = string.Empty;
        return true;
    }

    /// <summary>
    /// One checkpoint per floor arrived at, so a quit or a death rolls back to
    /// the start of the floor you were on with the inventory you rode the
    /// elevator up with.
    ///
    /// <para>Arrival is the only consistent moment: the stats mirror at
    /// `0x80012194` is refreshed on a floor transition and not continuously,
    /// and the familiar's live record is written back at the same time. A
    /// mid-floor snapshot can be a level stale.</para>
    ///
    /// <para>The town stability guards do not apply here - there is no town
    /// modal root and no town mailbox - so the rule is narrower: a real floor
    /// number, no mode load in flight, and an idle CD queue. A capture during
    /// a floor load would snapshot a half-built floor.</para>
    /// </summary>
    private bool TryCaptureTowerFloor(
        IEmulatorMemory memory,
        AzureDreamsSeedIdentity identity,
        out AzureDreamsCheckpointMetadata? checkpoint,
        out string message)
    {
        checkpoint = null;
        message = string.Empty;

        Span<byte> floorBytes = stackalloc byte[2];
        if (!memory.TryRead(
                AzureDreamsTownCheckpoint.CurrentFloorAddress, floorBytes, out _))
        {
            return true;
        }
        int floor = BinaryPrimitives.ReadUInt16LittleEndian(floorBytes);
        if (floor < 1 || floor > AzureDreamsTownCheckpoint.TopFloor)
            return true;
        if (floor == _lastTowerFloorCaptured)
            return true;

        Span<byte> pending = stackalloc byte[1];
        if (!memory.TryRead(
                AzureDreamsTownCheckpoint.ModeLoadPendingAddress, pending, out _) ||
            pending[0] != 0)
        {
            return true;
        }
        Span<byte> head = stackalloc byte[1];
        Span<byte> tail = stackalloc byte[1];
        if (!memory.TryRead(AzureDreamsTownCheckpoint.CdQueueHeadAddress, head, out _) ||
            !memory.TryRead(AzureDreamsTownCheckpoint.CdQueueTailAddress, tail, out _) ||
            head[0] != tail[0])
        {
            return true;
        }

        if (!AzureDreamsTownCheckpoint.TryCapture(
                memory,
                identity,
                AzureDreamsCheckpointReason.TowerFloorEntry,
                out AzureDreamsCheckpointMetadata saved,
                out message,
                _snapshotDirectory))
        {
            return false;
        }

        _snapshotExists = true;
        _lastTowerFloorCaptured = floor;
        checkpoint = saved;
        message = string.Empty;
        return true;
    }

    public bool TryCommitPending(
        IEmulatorMemory memory,
        AzureDreamsSeedIdentity identity,
        out AzureDreamsCheckpointMetadata? checkpoint,
        out string message)
    {
        checkpoint = null;
        EnsureIdentity(identity);
        if (!_shopPurchasePending && !_townReceivePending)
        {
            message = string.Empty;
            return true;
        }
        if (!AzureDreamsTownCheckpoint.TryObserve(
                memory,
                out AzureDreamsTownObservation observation,
                out message))
        {
            return false;
        }
        if (!observation.IsTown)
        {
            message = string.Empty;
            return true;
        }
        // Same settled-frame rule as the boundary captures. A capture taken
        // on an unsettled frame can snapshot mid-write state - Nada's
        // delivery loop half-way through the inventory, a shop transaction
        // mid-commit - and a restore of that snapshot would resurrect it.
        // The request stays pending; CapturePending keeps the console's
        // "waiting for a settled frame" reporting honest while it waits.
        if (!observation.IsStableTown)
        {
            message = string.Empty;
            return true;
        }

        AzureDreamsCheckpointReason reason = _townReceivePending
            ? AzureDreamsCheckpointReason.TownReceiveAcknowledged
            : AzureDreamsCheckpointReason.ShopPurchase;
        if (!AzureDreamsTownCheckpoint.TryCapture(
                memory,
                identity,
                reason,
                out AzureDreamsCheckpointMetadata saved,
                out message,
                _snapshotDirectory))
        {
            return false;
        }

        _snapshotExists = true;
        _shopPurchasePending = false;
        _townReceivePending = false;
        checkpoint = saved;
        message = string.Empty;
        return true;
    }

    private void EnsureIdentity(AzureDreamsSeedIdentity identity)
    {
        if (_seedSignature is not null &&
            _seedSignature.SequenceEqual(identity.Signature))
        {
            return;
        }

        _seedSignature = identity.Signature.ToArray();
        _snapshotExists = File.Exists(
            AzureDreamsTownCheckpoint.GetSnapshotPath(identity, _snapshotDirectory));
        _restorePending = _snapshotExists;
        _towerObserved = false;
        _shopPurchasePending = false;
        _townReceivePending = false;
        _lastTowerFloorCaptured = 0;
    }
}
