using Adap.Client;
using Adap.Client.Archipelago;
using Adap.Client.Patching;
using Adap.Client.Windows;

// The development and diagnostic modes that used to live here - the dev
// control panel, RAM watch, receive-state dump, region canary, render
// harnesses, mailbox probes and the bare DuckStation probe - were stripped
// for the public release on 2026-08-05. They live on, frozen, in
// client/src/Adap.Client.Dev (see its README-DEV.md).
internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length >= 3 && args.Length <= 4 &&
            args[0].Equals("--connect", StringComparison.OrdinalIgnoreCase))
        {
            using CancellationTokenSource cancellation = new();
            ConsoleCancelEventHandler cancelHandler = (_, eventArgs) =>
            {
                eventArgs.Cancel = true;
                cancellation.Cancel();
            };
            Console.CancelKeyPress += cancelHandler;
            try
            {
                return AzureDreamsArchipelagoClient.RunAsync(
                        args[1],
                        args[2],
                        args.Length == 4 ? args[3] : null,
                        cancellation.Token)
                    .GetAwaiter()
                    .GetResult();
            }
            finally
            {
                Console.CancelKeyPress -= cancelHandler;
            }
        }

        if (args.Length == 0)
        {
            WindowsConsole.Detach();
            ApplicationConfiguration.Initialize();
            Application.Run(new ConnectionWindow());
            return 0;
        }

        // A single existing-file argument is a double-clicked patch: the shell
        // passes exactly this when the patch association fires. Open the
        // window with that patch already loaded and start the game.
        if (args.Length == 1 &&
            !args[0].StartsWith("--", StringComparison.Ordinal) &&
            File.Exists(args[0]))
        {
            // Channel guard: a stale Open With choice or a hand-typed launch
            // can still route the other channel's patch here (the channels
            // carry different exe names precisely because Windows records
            // choices by bare name, but old registrations linger). A dev seed
            // against the stable client - or the reverse - is a protocol
            // mismatch that surfaces as unattributable bugs, so refuse it by
            // name instead of loading it.
            if (!args[0].EndsWith(
                    PatchFileAssociation.Extension,
                    StringComparison.OrdinalIgnoreCase))
            {
                WindowsConsole.Detach();
                MessageBox.Show(
                    $"This is the \"{PatchFileAssociation.FriendlyName}\" client; it opens " +
                    $"*{PatchFileAssociation.Extension} files only.\n\n" +
                    $"\"{Path.GetFileName(args[0])}\" belongs to the other release channel - " +
                    "open it with that channel's client (dev seeds come from the dev " +
                    "Archipelago install, stable seeds from archipelago-stable).",
                    "Wrong release channel",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning);
                return 1;
            }
            WindowsConsole.Detach();
            ApplicationConfiguration.Initialize();
            Application.Run(new ConnectionWindow(Path.GetFullPath(args[0])));
            return 0;
        }

        if (args.Length == 1 && args[0].Equals("--self-test", StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                return SelfTest.Run();
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"FAIL: {ex.Message}");
                return 1;
            }
        }

        // Draws the main window to a PNG with a session staged, so a layout
        // change can be LOOKED at without a room, a game or a screenshot from
        // the player. Every UI mistake worth catching here was one that read
        // fine in the source and wrong on screen.
        if (args.Length >= 2 &&
            args[0].Equals("--render-window", StringComparison.OrdinalIgnoreCase))
        {
            ApplicationConfiguration.Initialize();
            bool compact = args.Length > 2 &&
                args[2].Equals("compact", StringComparison.OrdinalIgnoreCase);
            // Optional size, clamped up to the mode's own minimum: pass 1 1 to
            // render whatever the smallest allowed window actually looks like.
            int.TryParse(args.Length > 3 ? args[3] : null, out int width);
            int.TryParse(args.Length > 4 ? args[4] : null, out int height);
            return ConnectionWindow.RenderToFile(
                Path.GetFullPath(args[1]), compact, width, height);
        }

        if (args.Length == 3 &&
            args[0].Equals("--apply-ppf", StringComparison.OrdinalIgnoreCase))
        {
            try
            {
                PatchResult result = PpfPatchService.ApplyAsync(
                        args[1],
                        args[2],
                        overwrite: false)
                    .GetAwaiter()
                    .GetResult();
                Console.WriteLine($"Patched BIN: {result.BinPath}");
                Console.WriteLine($"CUE: {result.CuePath}");
                Console.WriteLine($"Applied {result.RecordCount} PPF records.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Patch failed: {ex.Message}");
                return 1;
            }
        }

        if (args.Length == 1 &&
            args[0].Equals("--register-patch-type", StringComparison.OrdinalIgnoreCase))
        {
            bool ok = PatchFileAssociation.Register(out string message);
            Console.WriteLine(message);
            return ok ? 0 : 1;
        }
        if (args.Length == 1 &&
            args[0].Equals("--unregister-patch-type", StringComparison.OrdinalIgnoreCase))
        {
            bool ok = PatchFileAssociation.Unregister(out string message);
            Console.WriteLine(message);
            return ok ? 0 : 1;
        }

        Console.Error.WriteLine(
            "Azure Dreams Archipelago client. Run with no arguments (or " +
            $"double-click a {PatchFileAssociation.Extension} file) for the launcher window, or:");
        Console.Error.WriteLine("  --connect <host:port> <slot> [password]");
        Console.Error.WriteLine($"  --apply-ppf <patch{PatchFileAssociation.Extension}> <original.bin>");
        Console.Error.WriteLine("  --register-patch-type | --unregister-patch-type");
        Console.Error.WriteLine("  --self-test");
        return 1;
    }
}
