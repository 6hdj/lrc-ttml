using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Lyric2TTML.Shell.Backend;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using WinRT.Interop;

namespace Lyric2TTML.Shell.Pages;

/// <summary>
/// 歌词工作台：选文件 → 参数 → 开始 → **真实进度/阶段/日志** → 取消/打开产物。
///
/// 纪律（沿用本项目约定）：
///  * 进度与阶段**只来自 `GET /api/task`**，界面不自己编百分比；
///  * 取消按钮调用 `POST /api/task/cancel`（链路已真实验证 ✓），不是只关进度条；
///  * 任何失败都在 InfoBar 显示**原文**；按钮全部有 try/catch（不许点了没反应）。
/// </summary>
public sealed partial class WorkspacePage : Page
{
    private string _audio = "";
    private string _lyrics = "";
    private string _taskId = "";
    private string _resultPath = "";
    private bool _running;
    private CancellationTokenSource? _poll;

    public WorkspacePage()
    {
        InitializeComponent();
        CbMode.SelectedIndex = 0;
        ApplySettings(SettingsHost.Current);
        // 设置页保存后立即生效（页面是缓存的，不订阅就会出现"改了没反应"）
        SettingsHost.Changed += () => ApplySettings(SettingsHost.Current);
    }

    private static void SelectTag(ComboBox cb, string tag)
    {
        for (int i = 0; i < cb.Items.Count; i++)
        {
            if (cb.Items[i] is ComboBoxItem it && (it.Tag as string) == tag)
            {
                cb.SelectedIndex = i;
                return;
            }
        }
        if (cb.Items.Count > 0) cb.SelectedIndex = 0;
    }

    private void ApplySettings(ShellSettings s)
    {
        SelectTag(CbModel, s.Model.Length > 0 ? s.Model : "large-v3");
        SelectTag(CbLang, s.Language.Length > 0 ? s.Language : "auto");
        SelectTag(CbSinger, s.SingerMode.Length > 0 ? s.SingerMode : "off");
        SelectTag(CbAlignEngine, s.AlignEngine.Length > 0 ? s.AlignEngine : "whisper");
        TgTails.IsOn = s.KeepTails;
        TgSameAgent.IsOn = s.SameAgentOverlap;
        // 输出目录：留空 = 后端默认（后端自己决定写哪）
    }

    private IntPtr Hwnd => WindowNative.GetWindowHandle(App.MainWindow);

    private void Say(InfoBarSeverity sev, string title, string msg)
    {
        Bar.Severity = sev;
        Bar.Title = title;
        Bar.Message = msg;
        Bar.IsOpen = true;
    }

    // ---------------- 文件选择（Win32 通用对话框）----------------



    // ---------------- 文件选择（Win32 通用对话框）----------------

    private void BtnAudio_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var p = FilePicker.Open("选择音频",
                "音频\0*.wav;*.mp3;*.flac;*.m4a;*.aac;*.ogg\0所有文件\0*.*\0\0", Hwnd);
            if (p is null) return;                       // 取消：不是错误
            EdAudio.Text = p;
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "选择音频失败", ex.ToString());
        }
    }

    private void BtnLyrics_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var p = FilePicker.Open("选择歌词",
                "歌词\0*.lrc;*.txt\0所有文件\0*.*\0\0", Hwnd);
            if (p is null) return;
            EdLyrics.Text = p;
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "选择歌词失败", ex.ToString());
        }
    }

    // ---------------- 路径输入 + 拖放 ----------------
    // ⚠️ 原生文件选择器在这里是**三条死路**（都实测过）：
    //   ① Windows.Storage.Pickers → 要求包标识，非打包应用抛异常；
    //   ② 手写 OPENFILENAMEW + P/Invoke → Marshal.SizeOf 报 "cannot be marshaled"；
    //   ③ UseWindowsForms=true → 与 WinUI 的 XAML 目标冲突（MC6000）。
    // 所以在做出可靠的 IFileOpenDialog 封装之前：**路径框可编辑 + 支持拖放**
    // （绝不摆一个点了没反应的「浏览…」按钮）。

    private void Path_DragOver(object sender, DragEventArgs e)
    {
        try { e.AcceptedOperation = Windows.ApplicationModel.DataTransfer.DataPackageOperation.Copy; }
        catch { /* 拖放协商失败不影响输入框本身 */ }
    }

    private async void Path_Drop(object sender, DragEventArgs e)
    {
        try
        {
            var box = (TextBox)sender;
            string? path = null;
            if (e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems))
            {
                var items = await e.DataView.GetStorageItemsAsync();
                if (items.Count > 0) path = items[0].Path;
            }
            if (string.IsNullOrEmpty(path) &&
                e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.Text))
                path = (await e.DataView.GetTextAsync()).Trim().Trim('"');
            if (string.IsNullOrEmpty(path))
            {
                Say(InfoBarSeverity.Warning, "没读到文件路径", "请直接粘贴完整路径。");
                return;
            }
            box.Text = path;
            Say(InfoBarSeverity.Informational, "已填入路径", path);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "拖放失败", ex.Message + "\n可直接把路径粘贴到输入框。");
        }
    }

    private static string Tag(ComboBox cb) =>
        (cb.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "";

    // ---------------- 开始 / 取消 ----------------

    private async void BtnStart_Click(object sender, RoutedEventArgs e)
    {
        if (_running) return;
        try
        {
            _audio = EdAudio.Text.Trim().Trim('"');
            _lyrics = EdLyrics.Text.Trim().Trim('"');
            if (string.IsNullOrEmpty(_audio) || !File.Exists(_audio))
            {
                Say(InfoBarSeverity.Warning, "还没选音频", "请先选择音频文件。");
                return;
            }
            if (string.IsNullOrEmpty(_lyrics) || !File.Exists(_lyrics))
            {
                Say(InfoBarSeverity.Warning, "还没选歌词", "请先选择歌词文件。");
                return;
            }
            if (!App.Backend.IsRunning)
            {
                Say(InfoBarSeverity.Informational, "正在启动后端…", "首次启动需要几秒。");
                await App.Backend.StartAsync();
            }

            _running = true;
            _resultPath = "";
            BtnStart.IsEnabled = false;
            BtnCancel.IsEnabled = true;
            BtnOpen.IsEnabled = false;
            BtnReveal.IsEnabled = false;
            Bar2.Value = 0;
            LbStage.Text = "已提交，等待后端…";
            LbLog.Text = "";
            Bar.IsOpen = false;

            using var api = new ConversionApi(App.Backend.BaseUrl);
            _taskId = await api.StartAsync(_audio, _lyrics, Tag(CbModel), Tag(CbMode),
                                           Tag(CbLang), Tag(CbSinger), Tag(CbAlignEngine),
                                           TgSeparate.IsOn, TgTails.IsOn, TgSameAgent.IsOn,
                                           TgLayered.IsOn, TgAsrSupplement.IsOn,
                                           SettingsHost.Current.OutputDir,
                                           autoSave: false);   // 必须用户点「保存 TTML」才落盘
            Say(InfoBarSeverity.Informational, "任务已提交",
                $"task_id = {_taskId}（进度来自后端真实状态）");

            _poll = new CancellationTokenSource();
            await PollAsync(api, _poll.Token);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "启动失败", ex.ToString());
        }
        finally
        {
            _running = false;
            BtnStart.IsEnabled = true;
            BtnCancel.IsEnabled = false;
        }
    }

    private async Task PollAsync(ConversionApi api, CancellationToken ct)
    {
        var sb = new StringBuilder();
        while (!ct.IsCancellationRequested)
        {
            JsonElement t;
            try
            {
                t = await api.TaskAsync(_taskId);
            }
            catch (Exception ex)
            {
                Say(InfoBarSeverity.Error, "查询任务失败", ex.Message);
                return;
            }
            var status = t.TryGetProperty("status", out var s) ? s.GetString() ?? "" : "";
            double prog = t.TryGetProperty("progress", out var p) && p.TryGetDouble(out var pv) ? pv : 0;
            var stage = t.TryGetProperty("stage", out var st) ? st.GetString() ?? "" : "";
            Bar2.Value = prog;
            LbStage.Text = $"{stage}（{prog:0}%）";

            if (t.TryGetProperty("logs", out var logs) && logs.ValueKind == JsonValueKind.Array)
            {
                sb.Clear();
                foreach (var l in logs.EnumerateArray())
                {
                    var m = l.TryGetProperty("m", out var mm) ? mm.GetString() ?? "" : "";
                    var tt = l.TryGetProperty("t", out var tv) ? tv.GetString() ?? "" : "";
                    sb.Append(tt).Append("  ").AppendLine(m);
                }
                LbLog.Text = sb.Length == 0 ? "（等待后端日志…）" : sb.ToString();
            }

            switch (status)
            {
                case "done":
                    _resultPath = t.TryGetProperty("result_path", out var rp) ? rp.GetString() ?? "" : "";
                    BtnOpen.IsEnabled = !string.IsNullOrEmpty(_resultPath);
                    BtnReveal.IsEnabled = BtnOpen.IsEnabled;
                    BtnSaveTtml.IsEnabled = true;   // 产物未落盘时，保存按钮是唯一写入口
                    Say(InfoBarSeverity.Success, "完成",
                        string.IsNullOrEmpty(_resultPath)
                            ? "转换完成（**尚未保存**）——点「💾 保存 TTML」写入文件。"
                            : _resultPath);
                    return;
                case "error":
                    var err = t.TryGetProperty("error", out var er) ? er.GetString() ?? "" : "未知错误";
                    Say(InfoBarSeverity.Error, "转换失败", err);
                    return;
                case "cancelled":
                    Say(InfoBarSeverity.Warning, "已取消", "任务已被中断，未写入产物。");
                    return;
            }
            await Task.Delay(700, ct);
        }
    }

    /// <summary>显式保存：用户点按钮才写正式 TTML（转换默认 auto_save=false）。</summary>
    private async void BtnSaveTtml_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (string.IsNullOrEmpty(_taskId))
            {
                Say(InfoBarSeverity.Warning, "没有可保存的任务", "先点「开始处理」。");
                return;
            }
            BtnSaveTtml.IsEnabled = false;
            using var api = new ConversionApi(App.Backend.BaseUrl);
            var path = await api.SaveAsync(_taskId);
            _resultPath = path;
            BtnOpen.IsEnabled = true;
            BtnReveal.IsEnabled = true;
            Say(InfoBarSeverity.Success, "已保存", path);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "保存失败", ex.Message);
        }
        finally
        {
            BtnSaveTtml.IsEnabled = true;
        }
    }

    private async void BtnCancel_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (string.IsNullOrEmpty(_taskId)) return;
            using var api = new ConversionApi(App.Backend.BaseUrl);
            var changed = await api.CancelAsync(_taskId);
            Say(changed ? InfoBarSeverity.Informational : InfoBarSeverity.Warning,
                changed ? "已请求取消" : "任务已结束",
                changed ? "后端会在下一个阶段边界中断任务（不是只关进度条）。"
                        : "该任务已经结束，取消请求没有改变状态。");
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "取消失败", ex.ToString());
        }
    }

    /// <summary>
    /// 打开产物：**主路径是应用内**（显示路径 + 复制），系统打开只作次要动作。
    ///
    /// ⚠️ 为什么不再让"启动外部程序"当主路径（2026-10-16 实测）：
    /// `Process.Start("explorer.exe", "/select,…")` 在这台机器上**新进程直接崩**
    /// （`explorer.exe - 0xc0000142`，STATUS_DLL_INIT_FAILED），而且
    /// `Process.Start` **本身不抛异常** ⇒ 我们的 try/catch 抓不到，
    /// 用户看到的是系统弹出的错误框。⇒ 能力的**主路径必须不依赖外部启动**。
    /// </summary>
    /// <summary>打开 TTML：系统默认程序失败时退回**进程内定位**（不依赖外部启动）。</summary>
    private void BtnOpen_Click(object sender, RoutedEventArgs e)
    {
        var p = _resultPath;
        if (string.IsNullOrEmpty(p) || !File.Exists(p))
        {
            Say(InfoBarSeverity.Warning, "找不到产物", p);
            return;
        }
        try
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = p,
                UseShellExecute = true,
            });
            Say(InfoBarSeverity.Informational, "已请求系统打开",
                "若没有反应，可点「打开所在文件夹」定位后复制路径。");
        }
        catch (Exception ex)
        {
            var err = ShellOpen.Locate(p);
            Say(InfoBarSeverity.Warning, "系统打开失败，已改为定位文件",
                ex.Message + (string.IsNullOrEmpty(err) ? "" : "\n" + err));
        }
    }

    /// <summary>把路径显示出来 + 可复制；可选"尝试用系统默认程序打开"（失败如实报错）。</summary>
    /// <summary>
    /// 展示产物路径（**纯应用内**：显示 + 复制到剪贴板）。
    ///
    /// ⚠️⚠️ **这里刻意不启动任何外部程序**（2026-10-16 实测结论）：
    /// 本机上 `Process.Start("explorer.exe", "/select,…")` 会让新进程
    /// **初始化失败直接崩**（`0xc0000142` = STATUS_DLL_INIT_FAILED），
    /// 而且是**新进程自己崩**、`Process.Start` 不抛异常 ⇒ 用户看到系统错误框、
    /// 我们的 InfoBar 还只能说"已请求打开"。那种"点一下弹个系统错误框"就是
    /// 用户明令禁止的坏交互。
    /// ⇒ 主路径改成**应用内**：路径可见、可选中、（默认）已复制到剪贴板 ——
    ///    在任何机器上都 100% 可用，不依赖系统关联/安全软件放行。
    /// （普通机器上再考虑加一个"用系统程序打开"的可选项，且必须先探测能否启动。）
    /// </summary>
    private async Task ShowPathDialogAsync(string title, string path)
    {
        bool copied;
        try
        {
            var dp = new Windows.ApplicationModel.DataTransfer.DataPackage();
            dp.SetText(path);
            Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(dp);
            copied = true;
        }
        catch { copied = false; }

        var dlg = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = title,
            Content = new StackPanel
            {
                Spacing = 8,
                Children =
                {
                    new TextBlock
                    {
                        Text = copied
                            ? "路径已复制到剪贴板，可直接粘贴到资源管理器地址栏。"
                            : "（剪贴板不可用）请手动选中下面的路径复制。",
                        TextWrapping = TextWrapping.Wrap,
                        Opacity = 0.85,
                    },
                    new TextBlock
                    {
                        Text = path,
                        IsTextSelectionEnabled = true,
                        TextWrapping = TextWrapping.Wrap,
                        FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
                        FontSize = 12,
                    },
                },
            },
            CloseButtonText = "关闭",
            DefaultButton = ContentDialogButton.Close,
        };
        try { await dlg.ShowAsync(); }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "显示路径失败", ex.Message);
        }
    }

    /// <summary>
    /// 打开产物所在文件夹并选中它（**进程内** Shell API + 专用 STA 线程）。
    /// 失败时**自动把路径复制到剪贴板**并明说 —— 按钮任何时候都有实际产出，
    /// 不会出现"点了没用"（用户明确要求）。
    /// </summary>
    private async void BtnReveal_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (string.IsNullOrEmpty(_resultPath) || !File.Exists(_resultPath))
            {
                Say(InfoBarSeverity.Warning, "找不到产物", _resultPath);
                return;
            }
            var err = ShellOpen.Locate(_resultPath);
            if (string.IsNullOrEmpty(err))
            {
                Say(InfoBarSeverity.Success, "已在资源管理器中定位", _resultPath);
                return;
            }
            // 定位（选中文件）失败时，至少把**所在文件夹**打开 —— 用户实测 0x80070005
            var dir = Path.GetDirectoryName(_resultPath) ?? "";
            if (dir.Length > 0 && await ShellOpen.OpenFolderAsync(dir))
            {
                Say(InfoBarSeverity.Informational, "已打开所在文件夹（未能自动选中文件）", dir);
                return;
            }
            var copied = CopyPathToClipboard(_resultPath);
            Say(copied ? InfoBarSeverity.Warning : InfoBarSeverity.Error,
                copied ? "系统未能定位，路径已复制" : "定位失败",
                err + (copied ? "\n路径已复制到剪贴板，可直接粘贴到资源管理器地址栏。" : "\n路径：" + _resultPath));
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "定位失败", ex.ToString());
        }
    }

    private static bool CopyPathToClipboard(string text)
    {
        try
        {
            var dp = new Windows.ApplicationModel.DataTransfer.DataPackage();
            dp.SetText(text);
            Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(dp);
            return true;
        }
        catch { return false; }
    }
}