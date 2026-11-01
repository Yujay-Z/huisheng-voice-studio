using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class SetupLauncher {
    [STAThread]
    static void Main() {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(root, "Install-Offline.ps1");
            if (!File.Exists(script)) throw new Exception("请将安装程序和所有分卷放在同一文件夹中。");
            var info = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe"));
            info.Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"";
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            Process.Start(info);
        } catch (Exception error) {
            MessageBox.Show(error.Message, "回声 · 离线安装", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
