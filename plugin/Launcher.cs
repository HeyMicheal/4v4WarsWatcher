using System;
using System.Diagnostics;

namespace FourV4Worker
{
    /// <summary>
    /// Overwolfから外部プロセス（OCRワーカー）を起動・停止するためのプラグイン。
    /// JSからは overwolf.extensions.current.getExtraObject("worker-launcher", ...) で取得し、
    /// Launch(...) / Kill() を呼ぶ。
    ///
    /// Overwolfの規約: メソッドの最後の引数は Action&lt;object&gt; のコールバックにする。
    /// 結果はコールバックで返す（戻り値ではなく）。引数は全て必須。
    /// 文字列引数はJS側で Base64(UTF-8) にして渡し、ここでデコードする（日本語パス対策）。
    /// </summary>
    public class Launcher
    {
        private Process _process;

        // Overwolfはウィンドウハンドル(int)付きコンストラクタ、または空コンストラクタを呼ぶ
        public Launcher() { }
        public Launcher(int hwnd) { }

        private static string DecodeB64(string b64)
        {
            if (string.IsNullOrEmpty(b64)) return "";
            var bytes = Convert.FromBase64String(b64);
            return System.Text.Encoding.UTF8.GetString(bytes);
        }

        /// <summary>
        /// ワーカーを起動する（既に起動中なら何もしない）。各引数は Base64(UTF-8)。
        /// </summary>
        public void Launch(string fileNameB64, string argumentsB64, string workingDirB64,
                           Action<object> callback)
        {
            try
            {
                if (_process != null && !_process.HasExited)
                {
                    callback("already-running");
                    return;
                }

                var psi = new ProcessStartInfo
                {
                    FileName = DecodeB64(fileNameB64),
                    Arguments = DecodeB64(argumentsB64),
                    WorkingDirectory = DecodeB64(workingDirB64),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                _process = Process.Start(psi);
                callback("started");
            }
            catch (Exception e)
            {
                callback("error: " + e.Message);
            }
        }

        /// <summary>起動中のワーカーを停止する。</summary>
        public void Kill(Action<object> callback)
        {
            try
            {
                if (_process != null && !_process.HasExited)
                    _process.Kill();
                _process = null;
                callback("killed");
            }
            catch (Exception e)
            {
                callback("error: " + e.Message);
            }
        }

        /// <summary>ワーカーが起動中かどうかをコールバックで返す。</summary>
        public void IsRunning(Action<object> callback)
        {
            callback(_process != null && !_process.HasExited);
        }
    }
}
