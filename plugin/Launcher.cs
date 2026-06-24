using System;
using System.Diagnostics;

namespace FourV4Worker
{
    /// <summary>
    /// Overwolfから外部プロセス（OCRワーカー）を起動・停止するためのプラグイン。
    /// JSからは overwolf.extensions.current.getExtraObject("worker-launcher", ...) で取得し、
    /// Launch(...) / Kill() を呼ぶ。
    /// </summary>
    public class Launcher
    {
        private Process _process;

        // Overwolfはウィンドウハンドル(int)付きコンストラクタ、または空コンストラクタを呼ぶ
        public Launcher() { }
        public Launcher(int hwnd) { }

        // Overwolfのブリッジは非ASCII文字列でエラーになるため、引数はBase64(UTF-8)で受け取る
        private static string DecodeB64(string b64)
        {
            if (string.IsNullOrEmpty(b64)) return "";
            var bytes = Convert.FromBase64String(b64);
            return System.Text.Encoding.UTF8.GetString(bytes);
        }

        /// <summary>
        /// ワーカーを起動する（既に起動中なら何もしない）。
        /// 各引数はJS側で Base64(UTF-8) にエンコードして渡すこと（日本語パス対策）。
        /// fileName 例: "pythonw"、arguments 例: "\"D:\\path\\worker.py\""、workingDir 例: "D:\\path"
        /// </summary>
        public string Launch(string fileNameB64, string argumentsB64, string workingDirB64)
        {
            try
            {
                if (_process != null && !_process.HasExited)
                    return "already-running";

                var psi = new ProcessStartInfo
                {
                    FileName = DecodeB64(fileNameB64),
                    Arguments = DecodeB64(argumentsB64),
                    WorkingDirectory = DecodeB64(workingDirB64),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                _process = Process.Start(psi);
                return "started";
            }
            catch (Exception e)
            {
                return "error: " + e.Message;
            }
        }

        /// <summary>起動中のワーカーを停止する。</summary>
        public string Kill()
        {
            try
            {
                if (_process != null && !_process.HasExited)
                    _process.Kill();
                _process = null;
                return "killed";
            }
            catch (Exception e)
            {
                return "error: " + e.Message;
            }
        }

        /// <summary>ワーカーが起動中かどうか。</summary>
        public bool IsRunning()
        {
            return _process != null && !_process.HasExited;
        }
    }
}
