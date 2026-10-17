using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace Lyric2TTML.Shell.Backend;

/// <summary>
/// 调用 Python 后端的**命令行 JSON 入口**（--doctor --json / --worker-cli --status …）。
///
/// 为什么先用它而不是 HTTP：这些入口**冻结版里已经存在且返回 JSON**（第九阶段验收过），
/// 阶段 2 用它把"UI↔后端"这条链先打通；HTTP（web_server /api/*）在阶段 3 接入转换与任务。
///
/// 铁律（沿用本项目约定）：
///  * **不吞异常**：失败必须把 stderr/退出码原样带回给界面显示；
///  * **不假装成功**：解析不到 JSON 就是失败，不返回空对象冒充成功；
///  * 超时必须有上限（默认 60s），超时=失败，不无限等。
/// </summary>
public static class CliBridge
{
    public sealed record CliResult(bool Ok, int ExitCode, string StdOut, string StdErr,
                                   string Raw)
    {
        public string Error => Ok ? "" :
            string.IsNullOrWhiteSpace(StdErr) ? $"退出码 {ExitCode}" : StdErr.Trim();
    }

    /// <summary>后端可执行入口：优先冻结版 exe 旁的 python 后端，其次仓库 launcher.py。</summary>
    public static (string File, string Args) ResolveEntry()
    {
        var appDir = AppContext.BaseDirectory;
        // 冻结版：同目录下放的 Python 后端（阶段 5 集成时会改名为 _backend）
        foreach (var cand in new[] { "Lyric2TTML.Service.exe", "backend\\Lyric2TTML.Service.exe" })
        {
            var p = Path.Combine(appDir, cand);
            if (File.Exists(p)) return (p, "");
        }
        // 开发期：仓库 launcher.py + .venv
        var forced = Environment.GetEnvironmentVariable("L2T_BACKEND_ROOT");
        var repo = string.IsNullOrEmpty(forced) ? FindRepoRoot(appDir) : forced;
        if (repo is not null)
        {
            var py = Path.Combine(repo, ".venv", "Scripts", "python.exe");
            var lp = Path.Combine(repo, "launcher.py");
            if (File.Exists(py) && File.Exists(lp)) return (py, lp);
        }
        throw new FileNotFoundException("找不到 Python 后端（既没有冻结版，也没有仓库 launcher.py）");
    }

    private static string? FindRepoRoot(string start)
    {
        var d = new DirectoryInfo(start);
        for (int i = 0; i < 16 && d is not null; i++, d = d.Parent)
        {
            if (File.Exists(Path.Combine(d.FullName, "launcher.py")) &&
                Directory.Exists(Path.Combine(d.FullName, "desktop")))
                return d.FullName;
        }
        return null;
    }


    /// <summary>供 BackendHost 复用的仓库根查找（向上 16 层）。</summary>
    public static string? FindRepoRootFor(string start) => FindRepoRoot(start);
    public static async Task<CliResult> RunAsync(string[] args, int timeoutMs = 60000)
    {
        var (file, prefix) = ResolveEntry();
        var psi = new ProcessStartInfo
        {
            FileName = file,
            // ⚠️⚠️ **绝不要同时设 Arguments 与 ArgumentList**（2026-10-16 实测踩到）：
            // .NET 规定二者互斥，同时用会抛
            // `InvalidOperationException: The Arguments and ArgumentList properties cannot be used together`
            // ⇒ 每次 CLI 调用必失败，而界面只看到"失败"两个字。
            // 统一走 ArgumentList（Python 路径带空格也能正确传）。
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        if (!string.IsNullOrEmpty(prefix)) psi.ArgumentList.Add(prefix);   // launcher.py 的路径
        foreach (var a in args) psi.ArgumentList.Add(a);
        psi.Environment["L2T_HEADLESS"] = "1";   // CLI 模式禁弹窗（项目约定）
        // ⚠️⚠️ 必须显式给子进程 UTF-8：冻结版的 stdout 走系统代码页（简体中文 = GBK），
        // 而我们按 UTF-8 解码 ⇒ 中文全变乱码、JSON 也可能解析失败（实测 model-cli 直接抛
        // UnicodeDecodeError）。`PYTHONIOENCODING` 只在**解释器启动前**生效，放 Python 里设晚了。
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUTF8"] = "1";
        var dataDir = DataDirOverride();
        if (dataDir is not null)
        {
            psi.Environment["L2T_DATA_DIR"] = dataDir;
            // 本机限制：非白名单程序只能写自己的目录（用户实测 LocalAppData / 外部目录均被拒）。
            // ⇒ 模型缓存也放进程序目录（可写）；已有的大模型用 junction 挂进来只读复用。
            psi.Environment["L2T_HF_CACHE"] = System.IO.Path.Combine(dataDir, "models", "hf_cache");
        }

        using var p = new Process { StartInfo = psi };
        var so = new StringBuilder();
        var se = new StringBuilder();
        p.OutputDataReceived += (_, e) => { if (e.Data is not null) so.AppendLine(e.Data); };
        p.ErrorDataReceived += (_, e) => { if (e.Data is not null) se.AppendLine(e.Data); };
        if (!p.Start()) return new CliResult(false, -1, "", "无法启动后端进程", "");
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();

        using var cts = new System.Threading.CancellationTokenSource(timeoutMs);
        try
        {
            await p.WaitForExitAsync(cts.Token);
        }
        catch (OperationCanceledException)
        {
            try { p.Kill(entireProcessTree: true); } catch { /* 已退出 */ }
            return new CliResult(false, -2, so.ToString(), se.ToString(),
                                 $"[超时] 后端 {timeoutMs / 1000}s 未返回");
        }
        var stdout = so.ToString();
        var stderr = se.ToString();
        return new CliResult(p.ExitCode == 0, p.ExitCode, stdout, stderr, stdout);
    }

    /// <summary>
    /// 后端数据目录探测（用户实测：`%LOCALAPPDATA%\Lyric2TTML` 写入抛 Access denied，
    /// 导致「保存设置」「运行库安装」「分离组件安装」全失败）。
    /// 默认位置可写 ⇒ 返回 null（不覆盖）；不可写 ⇒ 返回程序目录\data（保证功能可用）。
    /// </summary>
    public static string? DataDirOverride()
    {
        try
        {
            var d = System.IO.Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Lyric2TTML");
            System.IO.Directory.CreateDirectory(d);
            var probe = System.IO.Path.Combine(d, ".l2t_write_probe");
            System.IO.File.WriteAllText(probe, "ok");
            System.IO.File.Delete(probe);
            return null;
        }
        catch
        {
            try
            {
                var d = System.IO.Path.Combine(AppContext.BaseDirectory, "data");
                System.IO.Directory.CreateDirectory(d);
                return d;
            }
            catch
            {
                return null;
            }
        }
    }

    /// <summary>跑一个返回 JSON 的子命令。解析失败 = 失败（**不返回空对象冒充成功**）。</summary>
    public static async Task<(bool Ok, JsonDocument? Doc, string Error)> RunJsonAsync(
        string[] args, int timeoutMs = 60000)
    {
        var r = await RunAsync(args, timeoutMs);
        var text = r.StdOut.Trim();
        int idx = text.IndexOf('{');
        if (idx < 0)
            return (false, null, r.Ok ? "后端没有返回 JSON" : r.Error);
        try
        {
            var doc = JsonDocument.Parse(text[idx..]);
            return (r.Ok, doc, r.Ok ? "" : r.Error);
        }
        catch (JsonException ex)
        {
            return (false, null, "JSON 解析失败：" + ex.Message);
        }
    }
}
