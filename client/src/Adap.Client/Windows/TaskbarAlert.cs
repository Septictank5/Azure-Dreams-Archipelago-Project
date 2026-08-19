using System.Runtime.InteropServices;

namespace Adap.Client.Windows;

/// <summary>
/// Asks the taskbar for the player's attention.
///
/// <para>Both of this app's windows spend a session behind the game. When the
/// link to that game drops - the emulator crashed, was closed, or stopped
/// answering - the status label saying so is on a window nobody is looking at.
/// A flashing taskbar button is the one channel that reaches past a fullscreen
/// emulator without stealing focus from it.</para>
/// </summary>
internal static class TaskbarAlert
{
    /// <summary>
    /// Flashes the window's taskbar button until the player brings it forward.
    /// A window that is already in front is left alone - it has their
    /// attention, which is the whole thing this is asking for.
    /// </summary>
    public static void Flash(Form window)
    {
        if (window.IsDisposed || !window.IsHandleCreated)
            return;
        if (Form.ActiveForm == window)
            return;

        var info = new FLASHWINFO
        {
            cbSize = (uint)Marshal.SizeOf<FLASHWINFO>(),
            hwnd = window.Handle,
            // Tray only, not the caption: the caption flash is for a window
            // the player can already see, and this one is behind a game.
            // TIMERNOFG keeps it going until the window comes to the front.
            dwFlags = FLASHW_TRAY | FLASHW_TIMERNOFG,
            uCount = uint.MaxValue,
            dwTimeout = 0,
        };
        try
        {
            FlashWindowEx(ref info);
        }
        catch (Exception)
        {
            // A missing or refused shell notification is not worth failing a
            // session over; the status label still says what happened.
        }
    }

    private const uint FLASHW_TRAY = 0x00000002;
    private const uint FLASHW_TIMERNOFG = 0x0000000C;

    [StructLayout(LayoutKind.Sequential)]
    private struct FLASHWINFO
    {
        public uint cbSize;
        public IntPtr hwnd;
        public uint dwFlags;
        public uint uCount;
        public uint dwTimeout;
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool FlashWindowEx(ref FLASHWINFO pwfi);
}
