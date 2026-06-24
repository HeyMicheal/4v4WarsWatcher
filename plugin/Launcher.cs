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

        /// <summary>
        /// ワーカーを起動する（既に起動中なら何もしない）。
        /// fileName 例: "pythonw"、arguments 例: "\"D:\\path\\worker.py\""、workingDir 例: "D:\\path"
        /// </summary>
        public string Launch(string fileName, string arguments, string workingDir)
        {
            try
            {
                if (_process != null && !_process.HasExited)
                    return "already-running";

                var psi = new ProcessStartInfo
                {
                    FileName = fileName,
                    Arguments = arguments,
                    WorkingDirectory = workingDir,
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
