using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using Lyric2TTML.Shell.Backend;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Lyric2TTML.Shell.Pages;

/// <summary>
/// 模型管理（阶段 4 第 3 项）。
/// 只显示后端真实状态；下载/校验/清理都真的调 `--model-cli`，失败显示 stderr 原文。
/// </summary>
public sealed partial class ModelsPage : Page
{
    public sealed class Row
    {
        public string Id { get; init; } = "";
        public string Title { get; init; } = "";
        public string Detail { get; init; } = "";
        public string Meta { get; init; } = "";
        public string Glyph { get; init; } = "";
        public string Location { get; init; } = "";
        public bool CanDownload { get; init; }
        public bool CanCleanup { get; init; }
        public bool HasLocation { get; init; }
    }

    private bool _busy;

    public ModelsPage()
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

    private void Fail(string title, string message, string shortText)
    {
        Bar.Severity = InfoBarSeverity.Error;
        Bar.Title = title;
        Bar.Message = message;
        Bar.IsOpen = true;
        LbStatus.Text = shortText;
    }

    public async Task RefreshAsync()
    {
        if (_busy) return;
        SetBusy(true, "正在检查模型状态…");
        Bar.IsOpen = false;
        List.ItemsSource = null;
        try
        {
            var (ok, doc, err) = await CliBridge.RunJsonAsync(new[] { "--model-cli", "json" }, 180_000);
            if (!ok || doc is null)
            {
                Fail("无法读取模型状态", err, "失败：" + FirstLine(err));
                return;
            }
            ModelCatalog catalog;
            using (doc) catalog = Management.ParseModels(doc.RootElement.GetRawText());

            var rows = new List<Row>();
            foreach (var m in catalog.Models)
            {
                bool ready = m.Status == "ready";
                rows.Add(new Row
                {
                    Id = m.Id,
                    Title = m.Name + (m.IsDefault ? "（默认）" : ""),
                    Detail = ready ? (m.Detail.Length > 0 ? m.Detail : "已就绪")
                                   : (m.Detail.Length > 0 ? m.Detail : "缺失：" + m.StatusLabel),
                    Meta = $"{m.Category} · 约 {Management.Human(m.ApproxSize)}"
                           + (m.PresentBytes > 0 ? $"（现有 {Management.Human(m.PresentBytes)}）" : "")
                           + (m.License.Length > 0 ? $" · {m.License}" : ""),
                    Glyph = ready ? "\uE73E" : "\uE783",
                    Location = m.Location,
                    CanDownload = m.CanDownload && !ready,
                    CanCleanup = ready,
                    HasLocation = !string.IsNullOrWhiteSpace(m.Location),
                });
            }
            List.ItemsSource = rows;
            LbSummary.Text = $"共 {catalog.Total} 个模型：{catalog.Ready} 个已就绪，{catalog.Missing} 个缺失。"
                             + $"\n缓存目录：{catalog.HfCache}";
            if (catalog.Missing == 0)
            {
                Bar.Severity = InfoBarSeverity.Success;
                Bar.Title = "模型齐全";
                Bar.Message = "所有模型已就绪。";
            }
            else
            {
                Bar.Severity = InfoBarSeverity.Warning;
                Bar.Title = $"{catalog.Missing} 个模型缺失";
                Bar.Message = "点对应条目的「下载」补齐；下载耗时取决于体积与网速。";
            }
            Bar.IsOpen = true;
            LbStatus.Text = $"完成：{catalog.Ready}/{catalog.Total} 就绪";
        }
        catch (Exception ex)
        {
            Fail("读取模型状态失败", ex.ToString(), "失败：" + FirstLine(ex.Message));
        }
        finally
        {
            SetBusy(false, LbStatus.Text);
        }
    }

    private static string FirstLine(string s)
    {
        var line = (s ?? "").Split('\n')[0].Trim();
        return line.Length > 120 ? line[..120] + "…" : line;
    }

    private async Task RunCliAsync(string title, string[] args, int timeoutMs)
    {
        if (_busy)
        {
            // ⚠️ 以前直接 return ⇒ 用户点「校验」看着"没反应"；必须给可见反馈
            Bar.Severity = InfoBarSeverity.Informational;
            Bar.Title = "上一个操作还在进行";
            Bar.Message = "请等它结束（完成后会自动刷新）。";
            Bar.IsOpen = true;
            return;
        }
        SetBusy(true, title + "…");
        Bar.IsOpen = false;
        try
        {
            var r = await CliBridge.RunAsync(args, timeoutMs);
            if (!r.Ok)
            {
                var raw = (r.Error + "\n" + r.StdOut).Trim();
                // 网络类错误在用户屏幕上会是一大坨 traceback；给一句人话 + 原始详情。
                string friendly = "";
                if (raw.Contains("ConnectTimeout") || raw.Contains("ConnectError")
                    || raw.Contains("LocalEntryNotFoundError") || raw.Contains("Max retries exceeded"))
                    friendly = "连不上模型源（hf-mirror.com / huggingface.co 都超时）。\n"
                             + "→ 检查网络/代理后重试；或手动把模型放到缓存目录（下方路径）。";
                else if (raw.Contains("无可用下载源"))
                    friendly = "当前没有可用的下载源。";
                Fail(title + "失败", (friendly.Length > 0 ? friendly + "\n\n" : "") + raw,
                     "失败：" + (friendly.Length > 0 ? "网络连不上模型源（详见下方）" : FirstLine(r.Error)));
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
            Fail(title + "失败", ex.ToString(), "失败：" + FirstLine(ex.Message));
        }
        finally
        {
            SetBusy(false, LbStatus.Text);
        }
        await RefreshAsync();
    }

    private async void BtnRefresh_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async void BtnDownload_Click(object sender, RoutedEventArgs e)
    {
        if (((FrameworkElement)sender).Tag is not string id || id.Length == 0) return;
        // 3GB 级下载：给足超时；进度不做假，等它跑完再刷新
        await RunCliAsync("下载 " + id, new[] { "--model-cli", "download", id }, 3_600_000);
    }

    private async void BtnCheckOne_Click(object sender, RoutedEventArgs e)
    {
        if (((FrameworkElement)sender).Tag is not string id || id.Length == 0) return;
        await RunCliAsync("校验 " + id, new[] { "--model-cli", "check", id }, 600_000);
    }

    private async void BtnCleanup_Click(object sender, RoutedEventArgs e)
    {
        if (((FrameworkElement)sender).Tag is not string id || id.Length == 0) return;
        await RunCliAsync("清理 " + id, new[] { "--model-cli", "cleanup", id }, 600_000);
    }

    /// <summary>
    /// 应用内"打开位置"兜底：外部资源管理器调不起来时，至少显示路径 + 所在文件夹内容 + 复制。
    /// 不依赖任何外部进程，所以在这台机器上一定可用。
    /// </summary>
    private async System.Threading.Tasks.Task ShowPathDialogAsync(string path, string why)
    {
        try
        {
            var dir = Directory.Exists(path) ? path : (Path.GetDirectoryName(path) ?? "");
            var lines = new System.Collections.Generic.List<string>();
            try
            {
                if (dir.Length > 0 && Directory.Exists(dir))
                {
                    var items = Directory.GetFileSystemEntries(dir);
                    foreach (var it in System.Linq.Enumerable.Take(items, 40))
                    {
                        var name = Path.GetFileName(it);
                        lines.Add(Directory.Exists(it) ? name + "\\  (文件夹)" : name);
                    }
                    if (lines.Count == 0) lines.Add("（这个文件夹是空的）");
                }
                else lines.Add("（文件夹不可读或不存在）");
            }
            catch (Exception ex) { lines.Add("读取失败：" + ex.Message); }

            var panel = new StackPanel { Spacing = 8 };
            panel.Children.Add(new TextBlock
            {
                Text = "系统无法调起资源管理器（" + why + "）。下面是该位置的内容：",
                TextWrapping = TextWrapping.Wrap, Opacity = 0.85,
            });
            panel.Children.Add(new TextBox
            {
                Text = path, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, AcceptsReturn = false,
            });
            panel.Children.Add(new ScrollViewer
            {
                MaxHeight = 240,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Content = new ItemsControl
                {
                    ItemsSource = lines,
                    ItemTemplate = (DataTemplate)Microsoft.UI.Xaml.Markup.XamlReader.Load(
                        "<DataTemplate xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation'>" +
                        "<TextBlock Text='{Binding}' FontSize='12' TextWrapping='Wrap'/></DataTemplate>"),
                },
            });

            var dlg = new ContentDialog
            {
                Title = "位置",
                Content = panel,
                PrimaryButtonText = "复制路径",
                CloseButtonText = "关闭",
                XamlRoot = XamlRoot,
            };
            if (await dlg.ShowAsync() == ContentDialogResult.Primary)
            {
                var dp = new Windows.ApplicationModel.DataTransfer.DataPackage();
                dp.SetText(path);
                Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(dp);
                Bar.Severity = InfoBarSeverity.Success;
                Bar.Title = "路径已复制";
                Bar.Message = path;
                Bar.IsOpen = true;
            }
        }
        catch (Exception ex)
        {
            Bar.Severity = InfoBarSeverity.Error;
            Bar.Title = "无法显示位置";
            Bar.Message = ex.Message + "\n" + path;
            Bar.IsOpen = true;
        }
    }

    private async void BtnLocate_Click(object sender, RoutedEventArgs e)
    {
        if (((FrameworkElement)sender).Tag is not string path) return;
        try
        {
            if (!File.Exists(path) && !Directory.Exists(path))
            {
                Bar.Severity = InfoBarSeverity.Warning;
                Bar.Title = "路径不存在";
                Bar.Message = path + "\n（模型可能还没下载，或缓存链接已失效）";
                Bar.IsOpen = true;
                return;
            }
            // 本机实测 Process.Start("explorer.exe") 新进程会崩（0xc0000142）⇒ 用进程内 Shell API
            var err = ShellOpen.Locate(path);
            if (err.Length == 0)
            {
                Bar.Severity = InfoBarSeverity.Success;
                Bar.Title = "已在资源管理器中定位";
                Bar.Message = path;
            }
            else
            {
                // 本机 SHParseDisplayName/SHOpenFolderAndSelectItems 报 0x80070005 ⇒
                // 至少把**所在文件夹**用 WinRT Launcher 打开（用户要求"要能打开"）。
                var dir = System.IO.Directory.Exists(path)
                    ? path : (System.IO.Path.GetDirectoryName(path) ?? "");
                if (dir.Length > 0 && ShellOpen.OpenFolderViaShell(dir))
                {
                    Bar.Severity = InfoBarSeverity.Informational;
                    Bar.Title = "已打开所在文件夹（未能自动选中文件）";
                    Bar.Message = dir;
                }
                else if (dir.Length > 0 && await ShellOpen.OpenFolderAsync(dir))
                {
                    Bar.Severity = InfoBarSeverity.Informational;
                    Bar.Title = "已打开所在文件夹（未能自动选中）";
                    Bar.Message = dir;
                }
                else
                {
                    // ⚠️ 这台机器上 explorer.exe 无法被调起（外部打开全部失败）⇒ 用**应用内**
                    // 对话框至少让用户看到路径和所在文件夹内容，并能复制路径。
                    await ShowPathDialogAsync(path, err);
                }
            }
            Bar.IsOpen = true;
        }
        catch (Exception ex)
        {
            Fail("打开位置失败", ex.ToString(), "失败：" + FirstLine(ex.Message));
        }
    }
}
