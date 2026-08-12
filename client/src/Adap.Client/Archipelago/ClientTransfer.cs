namespace Adap.Client.Archipelago;

internal enum ClientTransferKind
{
    Sent,
    Received,
}

internal sealed record ClientTransfer(
    ClientTransferKind Kind,
    string ItemName,
    string SourcePlayer,
    string TargetPlayer,
    bool SourceIsLocal,
    bool TargetIsLocal);
