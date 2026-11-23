using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Obrasys
{
    [STAThread]
    private static void Main()
    {
        var directory = AppDomain.CurrentDomain.BaseDirectory;
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = Path.Combine(Environment.SystemDirectory,
                    @"WindowsPowerShell\v1.0\powershell.exe"),
                Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" +
                    Path.Combine(directory, "Start-Obrasys.ps1") + "\"",
                WorkingDirectory = directory,
                UseShellExecute = false,
                CreateNoWindow = true
            });
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "Obrasys", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
