using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;
using Lyric2TTML.Shell.Backend;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Lyric2TTML.Shell.Pages;

/// <summary>
/// 任务中心：列表 / 状态 / 阶段 / 进度 / 日志 / 取消 / 复制产物路径。
///
/// 数据**全部来自后端** `GET /api/tasks`（真实任务，不编状态）；
/// 取消走 `POST /api/task/cancel`（链路已真实验证 ✓）。
/// 所有处理器都有 try/catch + InfoBar 原文（本项目"零静默失败"纪律）。
/// </summary>
public sealed partial class TasksPage : Page
{
    /// <summary>
    /// 列表行。**必须支持属性变更通知**（2026-10-16 用户实测报的"一直闪烁"）：
    /// 之前每 1.5s 轮询都 `List.ItemsSource = rows` **整表重建** ⇒ 所有卡片（含进度条）
    /// 被重新创建 ⇒ 视觉上一直闪，用户会以为"一直在刷新"。
    /// 现在集合只建一次，轮询只**原地改属性** ⇒ 不重建、不闪。
    /// </summary>
    public sealed class TaskRow : System.ComponentModel.INotifyPropertyChanged
    {
        private string _status = "";
        private string _stage = "";
        private double _progress;

        public string Id { get; init; } = "";
        public string Title { get; init; } = "";
        public string ResultPath { get; set; } = "";
        public string Error { get; set; } = "";

        public string StatusText
        {
            get => _status;
            set { if (_status != value) { _status = value; Raise(nameof(StatusText)); } }
        }
        public string Stage
        {
            get => _stage;
            set { if (_stage != value) { _stage = value; Raise(nameof(Stage)); } }
        }
        public double Progress
        {
            get => _progress;
            set { if (Math.Abs(_progress - value) > 0.01) { _progress = value; Raise(nameof(Progress)); } }
        }

        public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;
        private void Raise(string n) =>
            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(n));
    }

    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(1500) };
    // 集合只建一次；按 id 复用行对象（见 TaskRow 注释：整表重建会导致持续闪烁）
    private readonly System.Collections.ObjectModel.ObservableCollection<TaskRow> _rows = new();
    private readonly Dictionary<string, TaskRow> _byId = new();
    private string _logsCache = "";

    public TasksPage()
    {
        InitializeComponent();
        _timer.Tick += async (_, _) => await RefreshAsync(silent: true);
        List.ItemsSource = _rows;                       // 只绑一次
        Loaded += async (_, _) => { await RefreshAsync(); _timer.Start(); };
        Unloaded += (_, _) => _timer.Stop();
    }

    private void Say(InfoBarSeverity sev, string title, string msg)
    {
        Bar.Severity = sev;
        Bar.Title = title;
        Bar.Message = msg;
        Bar.IsOpen = true;
    }

    private async void BtnRefresh_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async Task RefreshAsync(bool silent = false)
    {
        if (!App.Backend.IsRunning)
        {
            if (!silent) Say(InfoBarSeverity.Informational, "后端未启动",
                             "请到「帮助 / 诊断」页点「启动后端」，或先在工作台发起一次转换。");
            LbInfo.Text = "后端未启动";
            return;
        }
        if (!silent) { Busy.IsActive = true; LbInfo.Text = "正在刷新…"; }
        try
        {
            using var api = new BackendClient(App.Backend.BaseUrl);
            using var doc = await api.GetJsonAsync("/api/tasks");
            var seen = new HashSet<string>();
            int running = 0, count = 0;
            if (doc.RootElement.TryGetProperty("tasks", out var arr))
            {
                foreach (var t in arr.EnumerateArray())
                {
                    var id = t.TryGetProperty("id", out var i) ? i.GetString() ?? "" : "";
                    if (string.IsNullOrEmpty(id)) continue;
                    seen.Add(id);
                    count++;
                    var status = t.TryGetProperty("status", out var s) ? s.GetString() ?? "" : "";
                    if (status is "running" or "pending") running++;

                    if (!_byId.TryGetValue(id, out var row))
                    {
                        row = new TaskRow
                        {
                            Id = id,
                            Title = t.TryGetProperty("title", out var ti) ? ti.GetString() ?? "" : "",
                        };
                        _byId[id] = row;
                        _rows.Add(row);                     // 新任务才插入
                    }
                    // **只改属性**（不重建集合 ⇒ 不闪）
                    row.StatusText = StatusText(status);
                    row.Stage = t.TryGetProperty("stage", out var st) ? st.GetString() ?? "" : "";
                    row.Progress = t.TryGetProperty("progress", out var p) && p.TryGetDouble(out var pv) ? pv : 0;
                    row.ResultPath = t.TryGetProperty("result_path", out var rp) ? rp.GetString() ?? "" : "";
                    row.Error = t.TryGetProperty("error", out var er) ? er.GetString() ?? "" : "";
                }
            }
            // 后端已经不认识的（重启过）才移除
            for (int k = _rows.Count - 1; k >= 0; k--)
                if (!seen.Contains(_rows[k].Id)) { _byId.Remove(_rows[k].Id); _rows.RemoveAt(k); }
            LbInfo.Text = count == 0 ? "还没有任务" : $"共 {count} 个任务，其中 {running} 个进行中";
            if (!silent) Bar.IsOpen = false;
            return;
        }
        catch (Exception ex)
        {
            if (!silent) Say(InfoBarSeverity.Error, "刷新任务列表失败", ex.ToString());
            LbInfo.Text = "刷新失败：" + ex.Message;
        }
        finally
        {
            if (!silent) Busy.IsActive = false;
        }
    }

    private static string StatusText(string s) => s switch
    {
        "pending" => "排队中",
        "running" => "进行中",
        "done" => "已完成",
        "cancelled" => "已取消",
        "error" => "失败",
        _ => s,
    };

    private static string TagOf(object sender) =>
        (sender as FrameworkElement)?.Tag?.ToString() ?? "";

    private async void BtnCancelOne_Click(object sender, RoutedEventArgs e)
    {
        var id = TagOf(sender);
        if (string.IsNullOrEmpty(id)) return;
        try
        {
            using var api = new ConversionApi(App.Backend.BaseUrl);
            var changed = await api.CancelAsync(id);
            Say(changed ? InfoBarSeverity.Informational : InfoBarSeverity.Warning,
                changed ? "已请求取消" : "任务已结束",
                changed ? $"{id}：后端会在下一个阶段边界中断它（不是只关进度条）。"
                        : $"{id} 已经结束，取消没有改变状态。");
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "取消失败", ex.ToString());
        }
        await RefreshAsync(silent: true);
    }

    private async void BtnLogs_Click(object sender, RoutedEventArgs e)
    {
        var id = TagOf(sender);
        if (string.IsNullOrEmpty(id)) return;
        try
        {
            using var api = new BackendClient(App.Backend.BaseUrl);
            using var doc = await api.GetJsonAsync($"/api/task?id={Uri.EscapeDataString(id)}");
            var sb = new System.Text.StringBuilder();
            if (doc.RootElement.TryGetProperty("logs", out var logs))
                foreach (var l in logs.EnumerateArray())
                {
                    var t = l.TryGetProperty("t", out var tv) ? tv.GetString() ?? "" : "";
                    var m = l.TryGetProperty("m", out var mv) ? mv.GetString() ?? "" : "";
                    sb.Append(t).Append("  ").AppendLine(m);
                }
            _logsCache = sb.Length == 0 ? "（这个任务还没有日志）" : sb.ToString();

            var dlg = new ContentDialog
            {
                XamlRoot = XamlRoot,
                Title = $"任务日志 · {id}",
                Content = new ScrollViewer
                {
                    Height = 420,
                    MinWidth = 720,
                    Content = new TextBlock
                    {
                        Text = _logsCache,
                        IsTextSelectionEnabled = true,
                        TextWrapping = TextWrapping.Wrap,
                        FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
                        FontSize = 12,
                        Margin = new Thickness(4),
                    },
                },
                PrimaryButtonText = "复制全部",
                CloseButtonText = "关闭",
                DefaultButton = ContentDialogButton.Close,
            };
            var r = await dlg.ShowAsync();
            if (r == ContentDialogResult.Primary)
            {
                var dp = new Windows.ApplicationModel.DataTransfer.DataPackage();
                dp.SetText(_logsCache);
                Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(dp);
                Say(InfoBarSeverity.Informational, "日志已复制", $"任务 {id}");
            }
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "读取日志失败", ex.ToString());
        }
    }

    private async void BtnPath_Click(object sender, RoutedEventArgs e)
    {
        var id = TagOf(sender);
        if (string.IsNullOrEmpty(id)) return;
        try
        {
            using var api = new BackendClient(App.Backend.BaseUrl);
            using var doc = await api.GetJsonAsync($"/api/task?id={Uri.EscapeDataString(id)}");
            var path = doc.RootElement.TryGetProperty("result_path", out var rp)
                ? rp.GetString() ?? "" : "";
            if (string.IsNullOrEmpty(path))
            {
                Say(InfoBarSeverity.Warning, "还没有产物", $"{id} 尚未完成，或后端没有给出路径。");
                return;
            }
            bool copied;
            try
            {
                var dp = new Windows.ApplicationModel.DataTransfer.DataPackage();
                dp.SetText(path);
                Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(dp);
                copied = true;
            }
            catch { copied = false; }
            Say(copied ? InfoBarSeverity.Success : InfoBarSeverity.Warning,
                copied ? "产物路径已复制" : "剪贴板不可用",
                path);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "读取产物路径失败", ex.ToString());
        }
    }
}
