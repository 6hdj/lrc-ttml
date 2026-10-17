using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace Lyric2TTML.Shell.Backend;

/// <summary>
/// Python 后端（HTTP）的**进程生命周期管理**：选空闲端口 → 拉起 → 健康检查 → 崩溃/退出清理。
///
/// 设计原则（沿用本项目一贯做法，禁止"看起来正常"）：
///  * **懒启动**：没人要后端就不起、不占端口（与旧壳的编辑台懒启动一致）；
///  * **绝不假装成功**：健康检查超时 = 失败，异常里带**后端 stderr 原文**；
///  * **端口不写死**：直接向系统要一个空闲端口，避免与用户其它程序冲突；
///  * **退出必清理**：先优雅停，超时再杀**整个进程树**（Python 会拉起 Demucs Worker 子进程）；
///  * **崩溃可感知**：`Exited` 事件 + `IsRunning`，界面必须能看到"后端挂了"。
/// </summary>
public sealed class BackendHost : IDisposable
{
    private readonly object _gate = new();
    private Process? _proc;
    private int _port;
    private string _logPath = "";
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(3) };

    public bool IsRunning
    {
        get { lock (_gate) { try { return _proc is { HasExited: false }; } catch { return false; } } }
    }
    public int Port { get { lock (_gate) return _port; } }
    public string BaseUrl => $"http://127.0.0.1:{Port}";
    public string LogPath { get { lock (_gate) return _logPath; } }
    public int LastExitCode { get; private set; } = int.MinValue;
    /// <summary>日志不可用时的原因（空 = 日志正常）。界面必须能显示它。</summary>
    public string LogError { get; private set; } = "";

    /// <summary>后端**意外退出**时触发（界面据此显示错误，而不是静默转圈）。</summary>
    public event Action<int>? Crashed;

    /// <summary>挑一个**真的能写**的日志目录；都不能写就返回空（调用方继续跑）。</summary>
    private static (string Path, string Error) PickLogPath()
    {
        var cands = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                         "Lyric2TTML", "logs"),
            Path.Combine(Path.GetTempPath(), "Lyric2TTML"),
            Path.Combine(AppContext.BaseDirectory, "logs"),
        };
        var errs = new System.Text.StringBuilder();
        foreach (var dir in cands)
        {
            try
            {
                Directory.CreateDirectory(dir);
                var probe = Path.Combine(dir, ".write-probe");
                File.WriteAllText(probe, "ok");
                File.Delete(probe);                     // 真实写探测，不是猜
                return (Path.Combine(dir, "backend-shell.log"), "");
            }
            catch (Exception ex)
            {
                errs.AppendLine($"  {dir} → {ex.Message}");
            }
        }
        return ("", "所有候选目录都不可写：\n" + errs);
    }
    private static int FreePort()
    {
        var l = new TcpListener(IPAddress.Loopback, 0);
        l.Start();
        int p = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return p;
    }

    /// <summary>启动后端并等到 /api/info 可用。失败抛异常（带后端 stderr）。</summary>
    public async Task StartAsync(int readyTimeoutMs = 30000, CancellationToken ct = default)
    {
        if (IsRunning) return;

        var (file, args) = ResolveEntry();
        int port = FreePort();
        // ⚠️⚠️ **日志失败绝不能拖垮后端启动**（2026-10-16 实测踩到）：
        // 用户机器上（Win11 + 安全软件）写 `%LOCALAPPDATA%\Lyric2TTML\logs` 被拒
        // （`Access to the path ... is denied`），而**同一条命令在普通 shell 里能跑通** ——
        // 说明是进程级拦截（安全软件对未签名新程序），不是权限配置。
        // 正确做法：逐个候选目录做**真实写探测**，挑第一个能写的；
        // 一个都不能写就**不带日志继续跑**（后端照常可用，界面明说"日志不可用"）。
        var (logPath, logErr) = PickLogPath();

        var psi = new ProcessStartInfo
        {
            FileName = file,
            WorkingDirectory = Path.GetDirectoryName(file) ?? Environment.CurrentDirectory,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        if (!string.IsNullOrEmpty(args)) psi.Arguments = args;
        psi.Environment["L2T_PORT"] = port.ToString();
        psi.Environment["L2T_HOST"] = "127.0.0.1";
        psi.Environment["L2T_HEADLESS"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUTF8"] = "1";
        var dataDir = CliBridge.DataDirOverride();
        if (dataDir is not null)
        {
            psi.Environment["L2T_DATA_DIR"] = dataDir;
            // 本机限制：非白名单程序只能写自己的目录（用户实测 LocalAppData / 外部目录均被拒）。
            // ⇒ 模型缓存也放进程序目录（可写）；已有的大模型用 junction 挂进来只读复用。
            psi.Environment["L2T_HF_CACHE"] = System.IO.Path.Combine(dataDir, "models", "hf_cache");
        }

        StreamWriter? log = null;
        if (!string.IsNullOrEmpty(logPath))
        {
            try
            {
                // ⚠️⚠️ **必须显式 FileShare.ReadWrite**（2026-10-16 实测踩到）：
                // 只写不共享的话，日志文件被本进程独占 ⇒ 界面读日志报
                // `The process cannot access the file … because it is being used by another process`，
                // 「打开日志」永远打不开。后端日志本来就是"边写边看"的。
                var fs = new FileStream(logPath, FileMode.Append, FileAccess.Write,
                                        FileShare.ReadWrite | FileShare.Delete);
                log = new StreamWriter(fs, Encoding.UTF8) { AutoFlush = true };
                log.WriteLine($"\n===== {DateTime.Now:yyyy-MM-dd HH:mm:ss} 启动后端 port={port} =====");
                log.WriteLine($"$ {file} {args}");
            }
            catch (Exception ex)
            {
                logErr = "日志文件打开失败：" + ex.Message;
                log = null;                       // **继续启动**，功能不依赖日志
            }
        }

        var proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
        var sbErr = new StringBuilder();
        proc.OutputDataReceived += (_, e) => { if (e.Data is not null) log?.WriteLine(e.Data); };
        proc.ErrorDataReceived += (_, e) =>
        {
            if (e.Data is null) return;
            sbErr.AppendLine(e.Data);
            log?.WriteLine("[stderr] " + e.Data);
        };
        proc.Exited += (_, _) =>
        {
            lock (_gate) { try { LastExitCode = proc.ExitCode; } catch { } }
            log?.WriteLine($"[后端退出] 退出码={LastExitCode} 时间={DateTime.Now:HH:mm:ss}");
            Crashed?.Invoke(LastExitCode);
        };

        if (!proc.Start()) throw new InvalidOperationException("无法启动 Python 后端进程");
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();
        lock (_gate) { _proc = proc; _port = port; _logPath = logPath; LogError = logErr; }

        // 健康检查：轮到 /api/info 返回 JSON 才算就绪
        var deadline = Environment.TickCount64 + readyTimeoutMs;
        Exception? last = null;
        while (Environment.TickCount64 < deadline)
        {
            if (proc.HasExited)
            {
                throw new InvalidOperationException(
                    $"后端启动即退出（退出码 {proc.ExitCode}）。\n" +
                    (sbErr.Length > 0 ? sbErr.ToString() : "（无 stderr 输出）"));
            }
            try
            {
                using var r = await _http.GetAsync($"{BaseUrl}/api/info", ct);
                if (r.IsSuccessStatusCode) return;      // 就绪
                last = new InvalidOperationException($"健康检查返回 {(int)r.StatusCode}");
            }
            catch (Exception ex)
            {
                last = ex;                               // 还没起来，继续等
            }
            await Task.Delay(200, ct);
        }
        // 超时：**不静默**，带日志路径与 stderr
        try { proc.Kill(entireProcessTree: true); } catch { }
        throw new TimeoutException(
            $"后端 {readyTimeoutMs / 1000}s 内没有就绪（端口 {port}）。\n" +
            $"最后错误：{last?.Message}\n日志：{logPath}\n" +
            (sbErr.Length > 0 ? "stderr:\n" + sbErr : ""));
    }

    /// <summary>优雅停止；超时杀整个进程树（Python 会带 Demucs Worker 子进程）。</summary>
    public async Task StopAsync(int graceMs = 3000)
    {
        Process? proc;
        lock (_gate) proc = _proc;
        if (proc is null) return;
        try
        {
            if (!proc.HasExited)
            {
                try { proc.Kill(entireProcessTree: false); } catch { }
                using var cts = new CancellationTokenSource(graceMs);
                try { await proc.WaitForExitAsync(cts.Token); }
                catch (OperationCanceledException)
                {
                    try { proc.Kill(entireProcessTree: true); } catch { }
                }
            }
        }
        catch { /* 已退出 */ }
        finally
        {
            lock (_gate) { _proc = null; _port = 0; }
            _http.DefaultRequestHeaders.Clear();
        }
    }

    private static (string File, string Args) ResolveEntry()
    {
        var appDir = AppContext.BaseDirectory;
        foreach (var cand in new[] { "Lyric2TTML.Service.exe", @"backend\Lyric2TTML.Service.exe" })
        {
            var p = Path.Combine(appDir, cand);
            if (File.Exists(p))
                return (p, "");          // 冻结版：后端 exe 自己就是入口（阶段 5 定名）
        }
        var forced = Environment.GetEnvironmentVariable("L2T_BACKEND_ROOT");
        var repo = string.IsNullOrEmpty(forced) ? CliBridge.FindRepoRootFor(appDir) : forced;
        if (repo is null) throw new FileNotFoundException(
            "找不到 Python 后端（既没有冻结版 Lyric2TTML.Service.exe，也没有仓库 launcher.py）");
        var py = Path.Combine(repo, ".venv", "Scripts", "python.exe");
        var ws = Path.Combine(repo, "web_server.py");
        if (!File.Exists(py) || !File.Exists(ws))
            throw new FileNotFoundException($"后端文件缺失：{py} 或 {ws}");
        return (py, "\"" + ws + "\"");
    }

    public void Dispose()
    {
        try { StopAsync(1500).GetAwaiter().GetResult(); } catch { }
        _http.Dispose();
    }
}
