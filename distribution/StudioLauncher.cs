using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class StudioLauncher {
    [STAThread]
    static void Main() {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "runtime\\app\\pythonw.exe");
            string script = Path.Combine(root, "desktop_launcher.pyw");
            if (!File.Exists(python) || !File.Exists(script)) throw new Exception("离线运行环境不完整，请重新运行离线安装程序。");
            var info = new ProcessStartInfo(python, "\"" + script + "\"");
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WindowStyle = ProcessWindowStyle.Hidden;
            Process.Start(info);
        } catch (Exception error) {
            MessageBox.Show(error.Message, "回声 · 音色工作室", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
