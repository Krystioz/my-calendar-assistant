using System;
using System.Diagnostics;

internal static class CalendarAssistantLauncher
{
    private static int Main()
    {
        try
        {
            var start = new ProcessStartInfo
            {
                FileName = System.IO.Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.System),
                    @"WindowsPowerShell\v1.0\powershell.exe"),
                Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File \"" +
                    @"__SCRIPT_PATH__" + "\"",
                UseShellExecute = false
            };
            using (var process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Cannot start Calendar Assistant: " + error.Message);
            Console.WriteLine("Press Enter to close.");
            Console.ReadLine();
            return 1;
        }
    }
}
