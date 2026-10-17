using System;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace Lyric2TTML.Shell.Backend;

/// <summary>
/// 转换任务 API（HTTP）。**契约取自实测**（2026-10-16 验证）：
///   POST /api/convert   体 {audio, lyrics, +参数}（**本机绝对路径，不用上传**）
///                       → 200 {"task_id": "..."}  ← 键名是 task_id **不是 id**
///   GET  /api/task?id=  → {status, progress, stage, logs:[{t,m}], result_path, error, summary}
///   POST /api/task/cancel 体 {id} → {changed, status, stage}（幂等）
///
/// 原则：**失败原样抛出/返回**，不拿空对象冒充成功；调用方负责显示。
/// </summary>
public sealed class ConversionApi : IDisposable
{
    private readonly HttpClient _http;

    public ConversionApi(string baseUrl)
    {
        _http = new HttpClient { BaseAddress = new Uri(baseUrl), Timeout = TimeSpan.FromSeconds(60) };
    }

    private static async Task<JsonDocument> Parse(HttpResponseMessage r)
    {
        var text = await r.Content.ReadAsStringAsync();
        if (!r.IsSuccessStatusCode)
            throw new HttpRequestException($"HTTP {(int)r.StatusCode}：{Short(text)}");
        try { return JsonDocument.Parse(text); }
        catch (JsonException ex) { throw new InvalidOperationException("返回的不是 JSON：" + Short(text), ex); }
    }

    private static string Short(string s) => s.Length > 400 ? s[..400] + "…" : s;

    /// <summary>提交转换。返回 task_id（拿不到就是失败，不编）。</summary>
    public async Task<string> StartAsync(string audio, string lyrics, string model, string mode,
                                         string language, string singerMode, bool separate,
                                         bool extendTails, bool allowSameAgentOverlap = false,
                                         string outputDir = "", bool autoSave = true)
    {
        var body = new
        {
            audio,
            lyrics,
            model,
            mode,
            language,
            singer_mode = singerMode,
            separate_vocals = separate,
            extend_tails = extendTails,
            allow_same_agent_overlap = allowSameAgentOverlap,
            output_dir = outputDir,
            auto_save = autoSave,
        };
        using var content = new StringContent(
            JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        using var r = await _http.PostAsync("/api/convert", content);
        using var doc = await Parse(r);
        if (!doc.RootElement.TryGetProperty("task_id", out var t) ||
            string.IsNullOrEmpty(t.GetString()))
            throw new InvalidOperationException("后端没有返回 task_id（不当作成功）");
        return t.GetString()!;
    }

    public async Task<JsonElement> TaskAsync(string id)
    {
        using var r = await _http.GetAsync($"/api/task?id={Uri.EscapeDataString(id)}");
        using var doc = await Parse(r);
        return doc.RootElement.Clone();
    }

    /// <summary>
    /// 显式保存：把任务的编辑态写成正式 TTML（返回落盘路径）。
    /// 为什么需要：转换默认 auto_save=false，**用户点保存前不写正式文件**。
    /// </summary>
    public async Task<string> SaveAsync(string id)
    {
        using var content = new StringContent(
            JsonSerializer.Serialize(new { id }), Encoding.UTF8, "application/json");
        using var r = await _http.PostAsync("/api/save", content);
        using var doc = await Parse(r);
        if (!doc.RootElement.TryGetProperty("path", out var p) || string.IsNullOrEmpty(p.GetString()))
            throw new InvalidOperationException("后端没有返回保存路径（不当作成功）");
        return p.GetString()!;
    }

    /// <summary>请求取消。返回 changed（false = 任务已结束，**不报假成功**）。</summary>
    public async Task<bool> CancelAsync(string id)
    {
        using var content = new StringContent(
            JsonSerializer.Serialize(new { id }), Encoding.UTF8, "application/json");
        using var r = await _http.PostAsync("/api/task/cancel", content);
        using var doc = await Parse(r);
        return doc.RootElement.TryGetProperty("changed", out var c) && c.GetBoolean();
    }


    /// <summary>POST 一个 JSON 对象并返回响应 JSON。**非 2xx 一律抛**（含结构化错误体）。</summary>
    public async Task<JsonDocument> PostJsonAsync(string path, string jsonBody)
    {
        using var content = new StringContent(jsonBody, Encoding.UTF8, "application/json");
        using var r = await _http.PostAsync(path, content);
        var text = await r.Content.ReadAsStringAsync();
        if (!r.IsSuccessStatusCode)
            throw new HttpRequestException($"{(int)r.StatusCode}|{Short(text)}");
        try { return JsonDocument.Parse(text); }
        catch (JsonException ex) { throw new InvalidOperationException("返回的不是 JSON：" + Short(text), ex); }
    }

    public void Dispose() => _http.Dispose();

    /// <summary>本客户端指向的基址（编辑台拼音频 URL 用）。</summary>
    public string BaseUrl => _http.BaseAddress?.ToString().TrimEnd('/') ?? "";
}

/// <summary>
/// 标准 Win32 打开文件对话框（`GetOpenFileNameW`）。
///
/// 死路记录（都实测过，别再试）：
///   ① `Windows.Storage.Pickers` → 要求包标识，非打包应用抛异常；
///   ② `Marshal.SizeOf&lt;T&gt;()` 算结构体大小 → 报
///      "cannot be marshaled as an unmanaged structure"（本文件上一版就死在这）；
///   ③ `UseWindowsForms=true` + `OpenFileDialog` → 与 WinUI 的 XAML 目标冲突（MC6000）。
/// 现在的做法：结构体按官方定义写（字符串用 `StringBuilder`）✓，
/// **结构体大小不用 Marshal.SizeOf**，直接用 x64=152 / x86=88 的已知值 ✓。
/// </summary>

/// <summary>
/// 文件选择器 —— 用 **WinAppSDK 1.7 的 `Microsoft.Windows.Storage.Pickers`**。
///
/// 这是官方给**非打包（unpackaged）应用**提供的选择器，**不需要包标识** ✓。
/// 之前三条死路（都实测过，别再试）：
///   ① `Windows.Storage.Pickers`（UWP 那套）→ 要求包标识，非打包应用抛异常；
///   ② 手写 `OPENFILENAMEW` + P/Invoke → `Marshal.SizeOf` 报错；
///      改用 `StringBuilder` 做字段 → `TypeLoadException: Struct or class fields
///      cannot be of type StringBuilder`（现代 .NET 的硬限制）；
///   ③ `UseWindowsForms=true` + `OpenFileDialog` → 与 WinUI 的 XAML 目标冲突（MC6000）。
/// </summary>

/// <summary>
/// 文件选择器：Win32 `GetOpenFileNameW`。
///
/// **关键点（来自运行时的报错原文）**：现代 .NET 里 **`struct` 不允许 `StringBuilder` 字段**
///   `TypeLoadException: Struct or class fields cannot be of type StringBuilder`
/// ⇒ 结构体必须是 **`class`**（经典 .NET Framework 写法如此），
///   `class` 会被封送并在调用后**回写**，路径才拿得到。
/// 死路记录（都实测过）：① `Windows.Storage.Pickers` 要包标识 ✗；
/// ② `struct` + `StringBuilder` → TypeLoadException ✗；
/// ③ `Marshal.SizeOf&lt;struct&gt;` → cannot be marshaled ✗；
/// ④ `UseWindowsForms=true` → 与 WinUI XAML 目标冲突（MC6000）✗；
/// ⑤ WinAppSDK 1.7 的 `Microsoft.Windows.Storage.Pickers` → 该版本里不存在 ✗。
/// </summary>
public static class FilePicker
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private class OPENFILENAMEW
    {
        public int lStructSize;
        public IntPtr hwndOwner;
        public IntPtr hInstance;
        [MarshalAs(UnmanagedType.LPWStr)] public string lpstrFilter = "";
        [MarshalAs(UnmanagedType.LPWStr)] public string? lpstrCustomFilter;
        public int nMaxCustFilter;
        public int nFilterIndex;
        // ⚠️⚠️ **按运行时给的方案**：`StringBuilder` 在 **struct 和 class 里都不允许** ✗
        //   (`TypeLoadException: Struct or class fields cannot be of type StringBuilder`)；
        //   官方建议：**用 `string` 字段并预置成与缓冲区等长的字符串** ⇒
        //   封送时会按该长度分配缓冲区，调用后回写新字符串（配 `[In, Out]`）。
        [MarshalAs(UnmanagedType.LPWStr)] public string lpstrFile = new string('\0', 4096);
        public int nMaxFile;
        [MarshalAs(UnmanagedType.LPWStr)] public string? lpstrFileTitle;
        public int nMaxFileTitle;
        [MarshalAs(UnmanagedType.LPWStr)] public string? lpstrInitialDir;
        [MarshalAs(UnmanagedType.LPWStr)] public string? lpstrTitle;
        public int Flags;
        public short nFileOffset;
        public short nFileExtension;
        [MarshalAs(UnmanagedType.LPWStr)] public string? lpstrDefExt;
        public IntPtr lCustData;
        public IntPtr lpfnHook;
        [MarshalAs(UnmanagedType.LPWStr)] public string? lpTemplateName;
        public IntPtr pvReserved;
        public int dwReserved;
        public int FlagsEx;
    }

    [DllImport("comdlg32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetOpenFileNameW([In, Out] OPENFILENAMEW ofn);

    private const int OFN_FILEMUSTEXIST = 0x00001000;
    private const int OFN_PATHMUSTEXIST = 0x00000800;
    private const int OFN_EXPLORER = 0x00080000;
    private const int OFN_NOCHANGEDIR = 0x00000008;

    /// <summary>弹"打开文件"对话框（**同步**，需在 UI 线程调）。取消返回 null。</summary>
    public static string? Open(string title, string filter, IntPtr owner)
    {
        var ofn = new OPENFILENAMEW
        {
            lStructSize = IntPtr.Size == 8 ? 152 : 88,   // 不用 Marshal.SizeOf（它对手写结构体会抛）
            hwndOwner = owner,
            lpstrFilter = filter,
            nFilterIndex = 1,
            nMaxFile = 4096,
            lpstrTitle = title,
            Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_EXPLORER | OFN_NOCHANGEDIR,
        };
        if (!GetOpenFileNameW(ofn)) return null;          // 取消 / 失败
        var path = (ofn.lpstrFile ?? "").TrimEnd('\0');   // 去掉未用满的 NUL
        return path.Length == 0 ? null : path;
    }
}

/// <summary>
/// 在**本进程内**请求资源管理器定位到某个文件（打开所在文件夹并选中）。
///
/// ⚠️⚠️ 为什么不用 `Process.Start("explorer.exe", "/select,...")`（2026-10-16 用户实测）：
/// 那会**新起一个 explorer.exe 进程**，本机上它初始化失败直接崩
/// (`explorer.exe - 0xc0000142`)，而且 `Process.Start` 不抛异常 ⇒ 只弹系统错误框。
/// 而**同一个进程内**调 Win32 是正常的（「浏览…」能弹窗就是证据）。
/// ⇒ 用 `SHOpenFolderAndSelectItems`：把请求交给**已在运行的**资源管理器，
///    **自己不创建新进程**，因此不受"新进程初始化失败"影响。
/// </summary>
public static class ShellOpen
{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
    private static extern int SHParseDisplayName(string pszName, IntPtr pbc,
                                                 out IntPtr ppidl, uint sfgaoIn,
                                                 out uint psfgaoOut);

    [DllImport("shell32.dll", PreserveSig = true)]
    private static extern int SHOpenFolderAndSelectItems(IntPtr pidlFolder, uint cidl,
                                                         IntPtr apidl, uint dwFlags);

    [DllImport("ole32.dll")]
    private static extern void CoTaskMemFree(IntPtr pv);

    [DllImport("ole32.dll")]
    private static extern int CoInitializeEx(IntPtr pvReserved, uint dwCoInit);

    [DllImport("ole32.dll")]
    private static extern void CoUninitialize();

    /// <summary>返回空串 = 成功；否则返回可读错误（不吞、不假装成功）。</summary>
    public static string Locate(string path)
    {
        // ⚠️⚠️ **必须放到专用 STA 线程**（2026-10-16 实测 `0x80070005 E_ACCESSDENIED`）：
        // WinUI 3 的 UI 线程是 **ASTA**（不是普通 STA），而部分 Shell API
        // （`SHOpenFolderAndSelectItems`）在 ASTA 上会直接返回 E_ACCESSDENIED。
        // 起一个明确 `ApartmentState.STA` 的线程来调，是这类问题的标准解法。
        string result = "";
        var th = new System.Threading.Thread(() => { result = LocateCore(path); });
        th.SetApartmentState(System.Threading.ApartmentState.STA);
        th.IsBackground = true;
        th.Start();
        if (!th.Join(TimeSpan.FromSeconds(8)))
            return "定位超时（8s）：Shell 没有响应";
        return result;
    }

    /// <summary>
    /// 打开一个**文件夹**（不选中文件）。用 WinRT Launcher —— 用户实测
    /// SHOpenFolderAndSelectItems 在这台机上返回 E_ACCESSDENIED(0x80070005)，
    /// 而 Launcher 走的是另一条路径，通常可用；失败返回 false（调用方再兜底）。
    /// </summary>
    public static async System.Threading.Tasks.Task<bool> OpenFolderAsync(string dir)
    {
        try
        {
            var folder = await Windows.Storage.StorageFolder.GetFolderFromPathAsync(dir);
            return await Windows.System.Launcher.LaunchFolderAsync(folder);
        }
        catch
        {
            return false;
        }
    }

    /// <summary>用 shell 直接打开一个文件夹（不选中）。比 SHOpenFolderAndSelectItems 兼容性好。</summary>
    public static bool OpenFolderViaShell(string dir)
    {
        try
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = dir,
                UseShellExecute = true,
            });
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static string LocateCore(string path)
    {
        // ⚠️ 专用线程必须自己初始化 COM：Shell API（SHParseDisplayName）依赖 OLE，
        // 未初始化时会直接返回 E_ACCESSDENIED(0x80070005) —— 正是用户看到的错误。
        int hrInit = CoInitializeEx(IntPtr.Zero, 0x2 /*COINIT_APARTMENTTHREADED*/);
        IntPtr pidl = IntPtr.Zero;
        try
        {
            var hr = SHParseDisplayName(path, IntPtr.Zero, out pidl, 0, out _);
            if (hr != 0 || pidl == IntPtr.Zero)
                return $"SHParseDisplayName 失败（HRESULT 0x{hr:X8}）：{path}";
            hr = SHOpenFolderAndSelectItems(pidl, 0, IntPtr.Zero, 0);
            return hr == 0 ? "" : $"SHOpenFolderAndSelectItems 失败（HRESULT 0x{hr:X8}）";
        }
        catch (Exception ex)
        {
            return ex.GetType().Name + ": " + ex.Message;
        }
        finally
        {
            if (pidl != IntPtr.Zero) CoTaskMemFree(pidl);
            if (hrInit >= 0) CoUninitialize();
        }
    }
}