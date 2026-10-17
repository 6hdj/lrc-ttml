using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Lyric2TTML.Shell.Backend;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Lyric2TTML.Shell.Pages;

/// <summary>
/// 运行库页（阶段 4 第 4 项）：torch/CUDA 运行时 + Demucs Worker 分离运行库。
/// 全部走真实 JSON CLI；安装/删除真的执行，失败显示原因。
/// </summary>
public sealed partial class RuntimePage : Page
{
    public sealed class Row
    {
        public string Id { get; init; } = "";
        public string Title { get; init; } = "";
        public string Detail { get; init; } = "";
        public string Meta { get; init; } = "";
        public string Glyph { get; init; } = "";
        public bool CanDownload { get; init; }
        public bool CanRemove { get; init; }
    }

    private bool _busy;
    private WorkerStatus? _worker;

    public RuntimePage()
    {
        InitializeComponent();
        _ = RefreshAsync();
    }

    private void SetBusy(bool busy, string text)
    {
        _busy = busy;
        Busy.IsActive = busy;
        BtnRefresh.IsEnabled = !busy;
        LbStatus.Text = text;
    }

    private void Fail(string title, string message)
    {
        Bar.Severity = InfoBarSeverity.Error;
        Bar.Title = title;
        Bar.Message = message;
        Bar.IsOpen = true;
    }

    public async Task RefreshAsync()
    {
        if (_busy) return;
        SetBusy(true, "正在读取运行库状态…");
        Bar.IsOpen = false;
        RuntimeList.ItemsSource = null;
        try
        {
            // 1) torch / cuda
            var (ok1, doc1, err1) = await CliBridge.RunJsonAsync(
                new[] { "--runtime-cli", "--status", "--json" }, 120_000);
            if (!ok1 || doc1 is null)
            {
                Fail("读取运行库状态失败", err1);
                return;
            }
            RuntimeStatus st;
            using (doc1) st = Management.ParseRuntimes(doc1.RootElement.GetRawText());
            LbRuntimeRoot.Text = "运行时根目录：" + st.Root;

            var rows = new List<Row>();
            foreach (var r in st.Runtimes)
            {
                bool ready = r.Status == "ready";
                rows.Add(new Row
                {
                    Id = r.Id,
                    Title = r.Name,
                    Detail = r.Detail.Length > 0 ? r.Detail : r.StatusLabel,
                    Meta = r.PresentBytes > 0
                        ? $"已占用 {Management.Human(r.PresentBytes)}"
                        : r.DownloadBytes > 0 ? $"需下载 {Management.Human(r.DownloadBytes)}" : "",
                    Glyph = ready ? "\uE73E" : "\uE783",
                    CanDownload = true,
                    CanRemove = ready,
                });
            }
            RuntimeList.ItemsSource = rows;

            // 2) demucs worker
            var (ok2, doc2, err2) = await CliBridge.RunJsonAsync(new[] { "--worker-cli", "--status" }, 120_000);
            if (!ok2 || doc2 is null)
            {
                LbWorker.Text = "读取失败：" + err2;
            }
            else
            {
                using (doc2) _worker = Management.ParseWorker(doc2.RootElement.GetRawText());
                var w = _worker;
                var srcText = w.SourcesReady ? "可用"
                    : "不可用：" + (w.SourcesReason.Length > 0 ? w.SourcesReason
                        : "清单里没有配置可用的下载源（需联网或改用 GitHub 镜像）");
                LbWorker.Text = $"状态：{w.State}（{w.Detail}）\n目标版本：{w.WantVersion}"
                                + $"\n组件源：{srcText}"
                                + (w.Exe.Length > 0 ? $"\n程序：{w.Exe}" : "");
                bool ready = w.Ready;
                BtnWorkerInstall.IsEnabled = !ready;
                BtnWorkerVerify.IsEnabled = ready;
                BtnWorkerUninstall.IsEnabled = ready;
            }

            int bad = 0;
            foreach (var r in rows) if (r.Glyph != "\uE73E") bad++;
            Bar.Severity = bad == 0 ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
            Bar.Title = bad == 0 ? "运行时齐全" : $"{bad} 个运行时未就绪";
            Bar.Message = bad == 0 ? "torch 与 CUDA 运行库都已就绪。" : "点「安装/修复」补齐。";
            Bar.IsOpen = true;
            LbStatus.Text = "状态已更新";
        }
        catch (Exception ex)
        {
            Fail("读取运行库状态失败", ex.ToString());
        }
        finally
        {
            SetBusy(false, LbStatus.Text);
        }
    }

    private async Task RunCliAsync(string title, string[] args, int timeoutMs)
    {
        if (_busy) return;
        SetBusy(true, title + "…");
        Bar.IsOpen = false;
        try
        {
            var r = await CliBridge.RunAsync(args, timeoutMs);
            if (!r.Ok)
            {
                Fail(title + "失败", (r.Error + "\n" + r.StdOut).Trim());
                return;
            }
            Bar.Severity = InfoBarSeverity.Success;
            Bar.Title = title + "完成";
            Bar.Message = (r.StdOut ?? "").Trim();
            if (Bar.Message.Length == 0) Bar.Message = "后端返回成功（无输出）。";
            Bar.IsOpen = true;
        }
        catch (Exception ex)
        {
            Fail(title + "失败", ex.ToString());
        }
        finally
        {
            SetBusy(false, LbStatus.Text);
        }
        await RefreshAsync();
    }

    private async void BtnRefresh_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async void BtnInstallRuntime_Click(object sender, RoutedEventArgs e)
    {
        if (((FrameworkElement)sender).Tag is not string id || id.Length == 0) return;
        await RunCliAsync("安装运行库 " + id,
            new[] { "--runtime-cli", "--install", id }, 3_600_000);
    }

    private async void BtnRemoveRuntime_Click(object sender, RoutedEventArgs e)
    {
        if (((FrameworkElement)sender).Tag is not string id || id.Length == 0) return;
        await RunCliAsync("删除运行库 " + id,
            new[] { "--runtime-cli", "--remove", id }, 600_000);
    }

    private async void BtnWorkerInstall_Click(object sender, RoutedEventArgs e)
        => await RunCliAsync("安装分离运行库", new[] { "--worker-cli", "--install" }, 3_600_000);

    private async void BtnWorkerVerify_Click(object sender, RoutedEventArgs e)
        => await RunCliAsync("校验分离运行库", new[] { "--worker-cli", "--verify" }, 600_000);

    private async void BtnWorkerUninstall_Click(object sender, RoutedEventArgs e)
        => await RunCliAsync("卸载分离运行库", new[] { "--worker-cli", "--uninstall" }, 600_000);
}
