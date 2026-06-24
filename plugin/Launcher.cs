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

        // "pythonw" のような実行ファイル名を、PATHを検索してフルパスに解決する。
        // UseShellExecute=false ではPATH解決が効かないことがあるため。
        private static string ResolveExe(string name)
        {
            if (string.IsNullOrEmpty(name)) return name;
            // 既にパス区切りを含む（フルパス指定）ならそのまま使う
            if (name.IndexOf('\\') >= 0 || name.IndexOf('/') >= 0)
                return name;

            string withExe = name.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)
                ? name : name + ".exe";
            string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (var dir in pathEnv.Split(';'))
            {
                if (string.IsNullOrWhiteSpace(dir)) continue;
                try
                {
                    string candidate = System.IO.Path.Combine(dir.Trim(), withExe);
                    if (System.IO.File.Exists(candidate)) return candidate;
                }
                catch { /* 不正なPATH要素は無視 */ }
            }

            // PATHに無ければ、よくあるPythonのインストール先を探す
            string lower = withExe.ToLowerInvariant();
            if (lower == "python.exe" || lower == "pythonw.exe")
            {
                var roots = new[]
                {
                    System.IO.Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                        "Programs", "Python"),
                    Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                };
                foreach (var root in roots)
                {
                    try
                    {
                        if (!System.IO.Directory.Exists(root)) continue;
                        foreach (var sub in System.IO.Directory.GetDirectories(root, "Python*"))
                        {
                            string candidate = System.IO.Path.Combine(sub, withExe);
                            if (System.IO.File.Exists(candidate)) return candidate;
                        }
                    }
                    catch { /* アクセス不可は無視 */ }
                }
            }

            return name;  // 見つからなければ元の名前のまま（従来動作）
        }

        /// <summary>
        /// ワーカーを起動する（既に起動中なら何もしない）。各引数は Base64(UTF-8)。
        /// tesseractCmdB64 が指定されていれば、子プロセスに環境変数 TESSERACT_CMD として渡す。
        /// </summary>
        public void Launch(string fileNameB64, string argumentsB64, string workingDirB64,
                           string tesseractCmdB64, Action<object> callback)
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
                    FileName = ResolveExe(DecodeB64(fileNameB64)),
                    Arguments = DecodeB64(argumentsB64),
                    WorkingDirectory = DecodeB64(workingDirB64),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                string tess = DecodeB64(tesseractCmdB64);
                if (!string.IsNullOrEmpty(tess))
                    psi.EnvironmentVariables["TESSERACT_CMD"] = tess;

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
