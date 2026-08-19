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

    /// <summary>
    /// Uncle's shortcut stages the level Koh should start floor 10/20/30 at
    /// here (`patch.SHORTCUT_PENDING_LEVEL_ADDRESS`), and the generator's
    /// wrapper on the floor's monster-levelling loop consumes it and clears it
    /// once Koh's actor exists. Nonzero therefore means "this floor is still
    /// being built and Koh has not been levelled yet".
    ///
    /// <para>This is the tower-floor capture's real completion signal. The
    /// floor halfword is stamped by the bootstrap helper at the very START of
    /// the build, so floor number, an idle CD queue and a clear mode-load flag
    /// can all read ready while generation is still running - and a snapshot
    /// taken there holds the level Koh had in town. That is the shortcut bug:
    /// warp to floor 20, quit, resume, and the run comes back at the level the
    /// climb was supposed to replace.</para>
    /// </summary>
    public const uint ShortcutPendingLevelAddress = 0x8001_5fec;

    /// <summary>
    /// Koh's live UnitStats, and the save-block mirror of it. The game writes
    /// live -> mirror on a floor transition and never reads it back, so the
    /// mirror is what a resume restores from and the live struct is the truth.
    /// A tower-floor capture reconciles the two rather than trusting whichever
    /// side of the shortcut level grant the game's own copy happened to fall.
    /// </summary>
    public const uint LiveUnitStatsAddress = 0x8008_34b8;
    public const uint SavedUnitStatsAddress = 0x8001_2194;
    public const int UnitStatsRecordSize = 0x4C;
    /// <summary>Level within the record; 1-99 for a real Koh.</summary>
    public const int UnitStatsLevelOffset = 0x11;
    /// <summary>Current / maximum HP within the record.</summary>
    public const int UnitStatsMaximumHpOffset = 0x29;

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

    /// <summary>
    /// Koh's twenty physical four-byte descriptors, and the twenty display
    /// pointers into them. The category byte at +1 is zero for a free slot -
    /// vanilla's own occupancy test - and the order table terminates on a
    /// null. Both are inside the snapshot, which is what lets a send be
    /// subtracted from a stored checkpoint.
    /// </summary>
    public const uint InventoryDescriptorAddress = 0x8001_0248;
    public const uint InventoryOrderAddress = 0x8001_029c;
    public const int InventorySlotCount = 20;

    private const uint SnapshotMagic = 0x5043_4441; // "ADCP"
    private const ushort SnapshotVersion = 1;
    private const ushort SnapshotHeaderSize = 0x50;
    private const int SnapshotHashOffset = 0x30;
    private const int SnapshotHashSize = 32;
    private const string SnapshotExtension = ".adcp";
    /// <summary>
    /// Sidecar beside the snapshot holding the gift-delivery watermark as it
    /// stood when the block was captured. It is not part of the payload
    /// because it is not game state: gifts are consumed through Archipelago's
    /// data storage, which a memory rollback cannot touch. Without it a gift
    /// delivered after a checkpoint is lost on a restore - the item goes back
    /// with the inventory and the server still calls it delivered.
    /// </summary>
    private const string CompanionExtension = ".gifts";

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
        string? snapshotDirectory = null,
        string? giftWatermark = null)
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
        if (reason == AzureDreamsCheckpointReason.TowerFloorEntry)
            RefreshSavedUnitStats(memory, payload);

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
        // After the snapshot, and never fatal: a missing companion costs a
        // gift replay after a rollback, a missing snapshot costs the run.
        WriteCompanion(path, giftWatermark);

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
        IEnumerable<long>? serverCheckedLocations,
        out AzureDreamsCheckpointMetadata metadata,
        out int mergedChecks,
        out string message,
        string? snapshotDirectory = null,
        Action<string?>? applyGiftWatermark = null)
    {
        metadata = default;
        mergedChecks = 0;
        string path = GetSnapshotPath(identity, snapshotDirectory);
        if (!TryRead(path, identity, out byte[] payload, out metadata, out message))
            return false;

        // The server's checked set goes into the payload BEFORE it lands: a
        // tower-floor checkpoint is taken on arrival, so everything collected
        // on that floor afterwards is missing from its journal, and a resume
        // rebuilds that floor (spawner reading the journal) before the poll
        // loop's own merge could possibly run. Merging into the block first
        // means the restored floor never respawns a banked marker.
        int persistentOffset = checked(
            (int)(AzureDreamsReceiveState.PersistentStateAddress - SaveBlockAddress));
        if (serverCheckedLocations is not null)
        {
            mergedChecks = AzureDreamsReceiveState.MergeCheckedLocationsIntoState(
                payload.AsSpan(persistentOffset, AzureDreamsReceiveState.PersistentStateSize),
                serverCheckedLocations);
        }

        if (!memory.TryWrite(SaveBlockAddress, payload, out string? writeError))
        {
            message = writeError ?? "Could not restore the Azure Dreams save-backed RAM block.";
            return false;
        }

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

        // Rewind gift delivery to where it stood when this block was captured,
        // so anything that entered the world after it is offered again. The
        // items themselves went back with the block; without this they would
        // be gone from the game and marked delivered on the server.
        //
        // Before the resume is armed: once the game is running a restored
        // tower floor it can be handed items, and the watermark should already
        // be the captured one by then.
        applyGiftWatermark?.Invoke(ReadCompanion(path));

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

    /// <summary>
    /// Copy Koh's LIVE stats over the payload's mirror, which is what a resume
    /// reads back. Only for a tower-floor capture, where the live struct is
    /// Koh and is freshly templated.
    ///
    /// <para>The game syncs the mirror on a floor transition, so at floor entry
    /// the two normally agree and this changes nothing. Where it earns its
    /// keep is the shortcut: Uncle's warp levels Koh from a wrapper on the
    /// floor's monster-levelling loop, and nothing re-syncs the mirror
    /// afterwards until the NEXT floor change. Doing the game's own write here
    /// makes the snapshot right whichever side of that the capture lands on -
    /// belt to the pending-level braces in TryCaptureTowerFloor.</para>
    ///
    /// <para>Refuses on a live record that is not a plausible Koh (no level,
    /// no maximum HP), so a read taken at a bad moment leaves the game's own
    /// mirror in place rather than replacing it with zeros.</para>
    /// </summary>
    private static void RefreshSavedUnitStats(IEmulatorMemory memory, byte[] payload)
    {
        Span<byte> live = stackalloc byte[UnitStatsRecordSize];
        if (!memory.TryRead(LiveUnitStatsAddress, live, out _))
            return;
        int level = live[UnitStatsLevelOffset];
        if (level is < 1 or > 99 || live[UnitStatsMaximumHpOffset] == 0)
            return;
        live.CopyTo(payload.AsSpan(
            checked((int)(SavedUnitStatsAddress - SaveBlockAddress)), UnitStatsRecordSize));
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

    private static string GetCompanionPath(string snapshotPath) =>
        snapshotPath + CompanionExtension;

    /// <summary>
    /// Replace the snapshot's gift watermark, or drop it when the caller has
    /// none. Dropping matters: a stale companion belongs to an older block and
    /// would rewind gift delivery to the wrong point.
    /// </summary>
    private static void WriteCompanion(string snapshotPath, string? content)
    {
        string companionPath = GetCompanionPath(snapshotPath);
        try
        {
            if (string.IsNullOrEmpty(content))
            {
                if (File.Exists(companionPath))
                    File.Delete(companionPath);
                return;
            }

            string temporaryPath = companionPath + ".tmp";
            File.WriteAllText(temporaryPath, content);
            File.Move(temporaryPath, companionPath, overwrite: true);
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or
            System.Security.SecurityException)
        {
            Console.Error.WriteLine(
                $"Could not store the checkpoint's gift watermark: {ex.Message}. A restore " +
                "will not re-offer gifts delivered after this checkpoint.");
        }
    }

    private static string? ReadCompanion(string snapshotPath)
    {
        try
        {
            string companionPath = GetCompanionPath(snapshotPath);
            return File.Exists(companionPath) ? File.ReadAllText(companionPath) : null;
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or
            System.Security.SecurityException)
        {
            Console.Error.WriteLine(
                $"Could not read the checkpoint's gift watermark: {ex.Message}.");
            return null;
        }
    }

    /// <summary>
    /// Subtract one sent item from the STORED checkpoint, so a restore cannot
    /// give it back.
    ///
    /// <para>A send is the one thing a rollback must not undo. The item is
    /// already in another player's world and the token is already spent, so
    /// restoring the block as captured hands the sender a second copy of both
    /// - a duplication the recipient never sees and cannot refuse.</para>
    ///
    /// <para>Re-capturing on every send would fix that and cost more than it
    /// is worth: the player would lose the floor they are mid-way through
    /// beating, because the checkpoint's whole promise is the inventory they
    /// rode the elevator up with. So the stored block is EDITED instead. One
    /// token comes off the counter, and the sent descriptor is removed only if
    /// the checkpoint was already carrying it. An item found on this floor and
    /// sent from it is not in the stored inventory at all, and then the token
    /// is the entire correction - exactly as it should be.</para>
    ///
    /// <para>Nothing here needs the game: the snapshot is a file, and the live
    /// counter was decremented by the commit routine itself.</para>
    /// </summary>
    public static bool TryAmendForSentItem(
        AzureDreamsSeedIdentity identity,
        ReadOnlySpan<byte> descriptor,
        out bool inventoryEdited,
        out string message,
        string? snapshotDirectory = null)
    {
        inventoryEdited = false;
        if (descriptor.Length != 4)
        {
            message = "A sent item is four descriptor bytes.";
            return false;
        }

        string path = GetSnapshotPath(identity, snapshotDirectory);
        if (!File.Exists(path))
        {
            // No checkpoint to correct. Not an error: nothing can roll back.
            message = string.Empty;
            return true;
        }
        if (!TryRead(path, identity, out byte[] payload, out AzureDreamsCheckpointMetadata metadata, out message))
            return false;

        SpendStoredSendToken(payload);
        inventoryEdited = RemoveStoredInventoryItem(payload, descriptor);
        return TryRewrite(path, payload, identity, metadata, out message);
    }

    /// <summary>
    /// One token off the stored counter, floored at zero. An unwitnessed pair
    /// is a block the game has not touched yet, which reads as the starting
    /// grant - so the witness is written alongside, or the game would decide
    /// the save was new and hand the token straight back.
    /// </summary>
    private static void SpendStoredSendToken(byte[] payload)
    {
        int countOffset = checked((int)(AzureDreamsReceiveState.SendTokenCountAddress - SaveBlockAddress));
        int magicOffset = checked((int)(AzureDreamsReceiveState.SendTokenMagicAddress - SaveBlockAddress));
        uint stored =
            BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(magicOffset)) ==
                AzureDreamsReceiveState.SendTokenMagic
                ? BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(countOffset))
                : AzureDreamsReceiveState.SendTokenStartingCount;
        BinaryPrimitives.WriteUInt32LittleEndian(
            payload.AsSpan(countOffset), stored == 0 ? 0 : stored - 1);
        BinaryPrimitives.WriteUInt32LittleEndian(
            payload.AsSpan(magicOffset), AzureDreamsReceiveState.SendTokenMagic);
    }

    /// <summary>
    /// Remove one descriptor matching <paramref name="descriptor"/> from the
    /// stored inventory, and take its pointer out of the display order the way
    /// the game's own removal does - survivors shift down, a null closes the
    /// list. Returns false when the checkpoint was not carrying the item,
    /// which is the ordinary case for something found on the current floor.
    /// </summary>
    private static bool RemoveStoredInventoryItem(byte[] payload, ReadOnlySpan<byte> descriptor)
    {
        int descriptorBase = checked((int)(InventoryDescriptorAddress - SaveBlockAddress));
        int slot = -1;
        int drifted = -1;
        for (int index = 0; index < InventorySlotCount; index++)
        {
            Span<byte> candidate = payload.AsSpan(descriptorBase + index * 4, 4);
            if (candidate[1] == 0)
                continue;                                    // free slot: vanilla's own test
            if (candidate.SequenceEqual(descriptor))
            {
                slot = index;
                break;
            }
            // Identity is the id and the category; quality and flags are state
            // the floor changes. A ball spends charges, a sword gets equipped,
            // and the descriptor that left is then not byte-identical to the
            // one the checkpoint holds. Matching on identity alone as a
            // fallback is what stops that drift reading as "the checkpoint
            // never had it" and duplicating the item on the next restore.
            if (drifted < 0 && candidate[0] == descriptor[0] && candidate[1] == descriptor[1])
                drifted = index;
        }
        if (slot < 0)
            slot = drifted;
        if (slot < 0)
            return false;

        uint slotPointer = (uint)(InventoryDescriptorAddress + slot * 4);
        payload.AsSpan(descriptorBase + slot * 4, 4).Clear();

        int orderBase = checked((int)(InventoryOrderAddress - SaveBlockAddress));
        int write = 0;
        for (int read = 0; read < InventorySlotCount; read++)
        {
            uint entry = BinaryPrimitives.ReadUInt32LittleEndian(payload.AsSpan(orderBase + read * 4));
            if (entry == 0)
                break;
            if (entry == slotPointer)
                continue;
            BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(orderBase + write * 4), entry);
            write++;
        }
        if (write < InventorySlotCount)
            BinaryPrimitives.WriteUInt32LittleEndian(payload.AsSpan(orderBase + write * 4), 0);
        return true;
    }

    /// <summary>
    /// Write an edited payload back over its own snapshot, keeping the reason
    /// and the capture time - an amendment is a correction to that checkpoint,
    /// not a new one - and re-deriving the hash and the header's cached
    /// receive/shop words so TryRead's cross-checks still hold. The companion
    /// beside it is left exactly as it was; a send changes nothing about which
    /// gifts have been delivered.
    /// </summary>
    private static bool TryRewrite(
        string path,
        byte[] payload,
        AzureDreamsSeedIdentity identity,
        AzureDreamsCheckpointMetadata metadata,
        out string message)
    {
        int persistentOffset = checked(
            (int)(AzureDreamsReceiveState.PersistentStateAddress - SaveBlockAddress));
        uint receiveCursor = BinaryPrimitives.ReadUInt32LittleEndian(
            payload.AsSpan(persistentOffset + AzureDreamsReceiveState.ReceivedItemCountOffset));
        uint shopMask = BinaryPrimitives.ReadUInt32LittleEndian(
            payload.AsSpan(persistentOffset + AzureDreamsReceiveState.PersistentShopMaskOffset));

        byte[] header = new byte[SnapshotHeaderSize];
        BinaryPrimitives.WriteUInt32LittleEndian(header, SnapshotMagic);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(4), SnapshotVersion);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(6), SnapshotHeaderSize);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(8), SaveBlockAddress);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(12), (uint)SaveBlockSize);
        identity.Signature.CopyTo(header, 16);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(24), receiveCursor);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(28), shopMask);
        BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(32), (uint)metadata.Reason);
        BinaryPrimitives.WriteInt64LittleEndian(header.AsSpan(40), metadata.CreatedUnixMilliseconds);
        SHA256.HashData(payload).CopyTo(header, SnapshotHashOffset);

        string temporaryPath = path + ".tmp";
        try
        {
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
            message = $"Could not amend the town checkpoint: {ex.Message}";
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

    /// <summary>
    /// Reads the gift-delivery watermark to store beside a capture, and puts
    /// one back on a restore. Supplied by the client rather than reached for
    /// here, because the watermark lives on the Archipelago session and this
    /// class knows only about memory and files.
    /// </summary>
    public Func<string?>? GiftWatermarkProvider { get; set; }

    public Action<string?>? GiftWatermarkRestorer { get; set; }

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

    /// <summary>
    /// One item has left the world for another player's. Correct the stored
    /// checkpoint so a restore cannot hand it back - see
    /// <see cref="AzureDreamsTownCheckpoint.TryAmendForSentItem"/>. Reported
    /// rather than returned: a send that cannot be recorded is worth a console
    /// line, and is not a reason to stop the poll loop.
    /// </summary>
    public void NoteItemSent(
        AzureDreamsSeedIdentity identity, AzureDreamsItemDescriptor descriptor)
    {
        EnsureIdentity(identity);
        if (!_snapshotExists)
            return;
        if (!AzureDreamsTownCheckpoint.TryAmendForSentItem(
                identity,
                descriptor.ToBytes(),
                out bool inventoryEdited,
                out string message,
                _snapshotDirectory))
        {
            Console.Error.WriteLine(
                $"Could not record a send against the saved checkpoint: {message} " +
                "Restoring it would return the sent item and its token.");
            return;
        }
        Console.WriteLine(
            inventoryEdited
                ? "Send recorded against the saved checkpoint: the item and its token are gone "
                  + "from it, so a restore cannot hand them back."
                : "Send recorded against the saved checkpoint: the token is gone from it. The "
                  + "item was found after the checkpoint, so it was never in it.");
    }

    public bool TryRestoreAtStartup(
        IEmulatorMemory memory,
        AzureDreamsSeedIdentity identity,
        AzureDreamsTownObservation observation,
        bool saveIsPristine,
        IEnumerable<long>? serverCheckedLocations,
        out bool restorePending,
        out AzureDreamsCheckpointMetadata? restoredCheckpoint,
        out int mergedChecks,
        out bool staleRestoreDropped,
        out string message)
    {
        restorePending = false;
        restoredCheckpoint = null;
        mergedChecks = 0;
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
                serverCheckedLocations,
                out AzureDreamsCheckpointMetadata restored,
                out mergedChecks,
                out message,
                _snapshotDirectory,
                GiftWatermarkRestorer))
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
                _snapshotDirectory,
                GiftWatermarkProvider?.Invoke()))
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
    /// number, no mode load in flight, an idle CD queue, and no shortcut level
    /// grant still pending. A capture during a floor load would snapshot a
    /// half-built floor.</para>
    ///
    /// <para>The pending-level guard is the one that was missing. The other
    /// three all read ready near the START of a floor build, and a shortcut
    /// warp does its levelling near the END of one, so floor 10/20/30 used to
    /// snapshot the level the player left town with.</para>
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
        // The shortcut's level grant is still owed to this floor: Koh is not
        // yet who he will be when the player takes their first step, so this
        // is not the floor's arrival state. It clears within the same build,
        // and the poll comes round again in a few milliseconds.
        Span<byte> pendingLevel = stackalloc byte[1];
        if (!memory.TryRead(
                AzureDreamsTownCheckpoint.ShortcutPendingLevelAddress, pendingLevel, out _) ||
            pendingLevel[0] != 0)
        {
            return true;
        }

        if (!AzureDreamsTownCheckpoint.TryCapture(
                memory,
                identity,
                AzureDreamsCheckpointReason.TowerFloorEntry,
                out AzureDreamsCheckpointMetadata saved,
                out message,
                _snapshotDirectory,
                GiftWatermarkProvider?.Invoke()))
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
                _snapshotDirectory,
                GiftWatermarkProvider?.Invoke()))
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
