using System.Runtime.InteropServices;

namespace Adap.Client.Windows;

internal static class WindowsConsole
{
    public static void Detach() => _ = FreeConsole();

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool FreeConsole();
}
