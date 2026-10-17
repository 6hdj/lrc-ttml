using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;
using Lyric2TTML.Shell.Backend;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;

namespace Lyric2TTML.Shell.Pages;

public sealed partial class DiagnosticsPage : Page
{
    public sealed class Row
    {
        public string Key { get; init; } = "";
        public string Detail { get; init; } = "";
        public string Scope { get; init; } = "";
        public string Glyph { get; init; } = "";
    }

    private string _lastRaw = "";

    public DiagnosticsPage() => InitializeComponent();

    /// <summary>真实跑一次后端诊断。失败就明说失败，不显示空列表假装正常。</summary>
    public async void Refresh()
    {
        RefreshBackend();
        Busy.IsActive = true;
        LbStatus.Text = "正在检查…";
        Bar.IsOpen = false;
        List.ItemsSource = null;
        try
        {
            var (ok, doc, err) = await CliBridge.RunJsonAsync(new[] { "--doctor", "--json" }, 120000);
            if (!ok || doc is null)
            {
                Bar.Severity = InfoBarSeverity.Error;
                Bar.Title = "后端诊断失败";
                Bar.Message = err;
                var first = (err ?? "").Split('\n')[0].Trim();
                LbStatus.Text = "失败：" + (first.Length > 120 ? first[..120] + "…" : first);
                return;
            }
            using (doc)
            {
                _lastRaw = doc.RootElement.GetRawText();
                var rows = new List<Row>();
                if (doc.RootElement.TryGetProperty("items", out var items))
                {
                    foreach (var it in items.EnumerateArray())
                    {
                        bool good = !it.TryGetProperty("ok", out var o) || o.GetBoolean();
                        rows.Add(new Row
                        {
                            Key = it.TryGetProperty("key", out var k) ? k.GetString() ?? "" : "",
                            Detail = it.TryGetProperty("detail", out var d) ? d.GetString() ?? "" : "",
                            Scope = it.TryGetProperty("scope", out var s) ? "范围：" + s.GetString() : "",
                            Glyph = good ? "\uE73E" : "\uE783",
                        });
                    }
                }
                List.ItemsSource = rows;
                int bad = 0;
                foreach (var r in rows) if (r.Glyph == "\uE783") bad++;
                LbStatus.Text = $"完成：{rows.Count} 项，{bad} 项异常";
                Bar.Severity = bad == 0 ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
                Bar.Title = bad == 0 ? "环境检查通过" : $"{bad} 项需要处理";
                Bar.Message = bad == 0 ? "全部检查项正常。" : "请按下方列表逐项处理。";
                Bar.IsOpen = true;
            }
        }
        catch (Exception ex)
        {
            Bar.Severity = InfoBarSeverity.Error;
            Bar.Title = "无法调用后端";
            Bar.Message = ex.ToString();
            var f1 = (ex.Message ?? "").Split('\n')[0].Trim();
            LbStatus.Text = "失败：" + (f1.Length > 120 ? f1[..120] + "…" : f1);
        }
        finally
        {
            Busy.IsActive = false;
        }
    }

    private void BtnRefresh_Click(object sender, RoutedEventArgs e) => Refresh();

    private void BtnCopy_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var dp = new DataPackage();
            dp.SetText(string.IsNullOrEmpty(_lastRaw) ? LbStatus.Text : _lastRaw);
            Clipboard.SetContent(dp);
            LbStatus.Text = "已复制到剪贴板";
        }
        catch (Exception ex)
        {
            // 非打包应用下剪贴板 API 也可能不可用 —— 如实报错，不假装成功
            Bar.Severity = InfoBarSeverity.Error;
            Bar.Title = "复制失败";
            Bar.Message = ex.Message;
            Bar.IsOpen = true;
        }
    }
    // ---------------- 后端进程（真操作）----------------

    public void RefreshBackend()
    {
        try
        {
            var host = App.Backend;
            if (host.IsRunning)
            {
                var logInfo = string.IsNullOrEmpty(host.LogError)
                    ? (string.IsNullOrEmpty(host.LogPath) ? "日志：不可用" : $"日志：{host.LogPath}")
                    : $"⚠ 日志不可用：{host.LogError}";
                LbBackend.Text = $"\u25CF 运行中  {host.BaseUrl}\n{logInfo}";
            }
            else if (host.LastExitCode != int.MinValue)
                LbBackend.Text = $"\u25CB 未运行 —— **后端曾异常退出**，退出码 {host.LastExitCode}\n日志：{host.LogPath}";
            else
                LbBackend.Text = "\u25CB 未启动（按需启动：不点就不占端口）";
        }
        catch (Exception ex)
        {
            LbBackend.Text = "状态读取失败：" + ex.Message;
        }
    }

    private async void BtnStart_Click(object sender, RoutedEventArgs e)
    {
        BtnStart.IsEnabled = false;
        RefreshBackend();
        Busy.IsActive = true;
        LbBackend.Text = "正在启动后端…";
        try
        {
            await App.Backend.StartAsync();
            LbBackend.Text = $"\u25CF 运行中  {App.Backend.BaseUrl}";
            // 真调一次 /api/info 证明它真的在服务（不是只看进程存活）
            using var client = new Lyric2TTML.Shell.Backend.BackendClient(App.Backend.BaseUrl);
            using var doc = await client.GetJsonAsync("/api/info");
            Bar.Severity = InfoBarSeverity.Success;
            Bar.Title = "后端已就绪";
            Bar.Message = "GET /api/info → " + doc.RootElement.GetRawText();
            Bar.IsOpen = true;
        }
        catch (Exception ex)
        {
            Bar.Severity = InfoBarSeverity.Error;
            Bar.Title = "后端启动失败";
            Bar.Message = ex.Message;
            Bar.IsOpen = true;
        }
        finally
        {
            Busy.IsActive = false;
            BtnStart.IsEnabled = true;
            RefreshBackend();
        }
    }

    private async void BtnStop_Click(object sender, RoutedEventArgs e)
    {
        BtnStop.IsEnabled = false;
        try
        {
            await App.Backend.StopAsync();
            Bar.Severity = InfoBarSeverity.Informational;
            Bar.Title = "后端已停止";
            Bar.Message = "端口已释放，不会留下残进程。";
            Bar.IsOpen = true;
        }
        finally
        {
            BtnStop.IsEnabled = true;
            RefreshBackend();
        }
    }

    private async void BtnLog_Click(object sender, RoutedEventArgs e)
    {
        // ⚠️⚠️ **不要用 Windows.Storage / Launcher 打开文件**（2026-10-16 实测踩到）：
        // 它们**要求包标识（package identity）**，而我们是**非打包（unpackaged）**应用
        // ⇒ 直接抛异常；配 `async void` + 无 try/catch = 异常被吞 = **按钮点了没反应**。
        // 改用 `Process.Start(UseShellExecute=true)`（非打包应用正常工作），
        // 并且**任何失败都必须在界面上看得见** —— 遵守"不允许点不动的按键"。
        try
        {
            var p = App.Backend.LogPath;
            if (string.IsNullOrEmpty(p) || !System.IO.File.Exists(p))
            {
                Bar.Severity = InfoBarSeverity.Warning;
                Bar.Title = "还没有日志";
                Bar.Message = string.IsNullOrEmpty(App.Backend.LogError)
                    ? "后端尚未启动过，所以还没有日志文件。"
                    : "日志不可用：" + App.Backend.LogError;
                Bar.IsOpen = true;
                return;
            }
            // ⚠️ 这台机器上 `ShellExecute` 打开 `.log` 被拒（Win32Exception 5 拒绝访问，
            // 与"写不进 LOCALAPPDATA/TEMP"同一类拦截）⇒ **不能只依赖外部程序**。
            // 主路径改成**在应用内看**（也更原生）：ContentDialog + 只读文本框显示尾部若干行；
            // 「用记事本打开」作为次级动作，显式调 notepad.exe（不走文件关联）。
            string text;
            try
            {
                // 读取端也声明共享：即使对方（后端/Python）没设 FileShare，也能读到正在写的文件
                using (var rs = new System.IO.FileStream(p, System.IO.FileMode.Open,
                           System.IO.FileAccess.Read,
                           System.IO.FileShare.ReadWrite | System.IO.FileShare.Delete))
                using (var sr = new System.IO.StreamReader(rs, System.Text.Encoding.UTF8))
                {
                    text = await sr.ReadToEndAsync();
                }
            }
            catch (Exception ex)
            {
                Bar.Severity = InfoBarSeverity.Error;
                Bar.Title = "读取日志失败";
                Bar.Message = $"{ex.Message}\n路径：{p}";
                Bar.IsOpen = true;
                return;
            }
            var lines = text.Replace("\r\n", "\n").Split('\n');
            var tail = string.Join("\n", lines.Length > 200 ? lines[^200..] : lines);
            // ⚠️ **不要用 ScrollViewer + 只读 TextBox 装日志**（2026-10-16 实测）：
            // 在 ContentDialog 里这种组合实测**文本不渲染**（框是空的，标题却显示"共 20 行"）。
            // 改用 ScrollViewer + TextBlock（IsTextSelectionEnabled=true 同样可选中复制），
            // 渲染稳定；想整段复制走「复制结果」。
            var viewer = new TextBlock
            {
                Text = tail,
                IsTextSelectionEnabled = true,
                TextWrapping = TextWrapping.Wrap,
                FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas"),
                FontSize = 12,
                Margin = new Thickness(4),
            };
            var scroll = new ScrollViewer
            {
                Content = viewer,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
                MinWidth = 720,
                Height = 420,
                Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["LayerFillColorAltBrush"],
            };
            var dlg = new ContentDialog
            {
                XamlRoot = XamlRoot,
                Title = $"后端日志（尾部 200 行，共 {lines.Length} 行）",
                Content = scroll,
                PrimaryButtonText = "用记事本打开",
                CloseButtonText = "关闭",
                DefaultButton = ContentDialogButton.Close,
            };
            var choice = await dlg.ShowAsync();
            if (choice == ContentDialogResult.Primary)
            {
                try
                {
                    System.Diagnostics.Process.Start("notepad.exe", $"\"{p}\"");
                    Bar.Severity = InfoBarSeverity.Informational;
                    Bar.Title = "已用记事本打开";
                    Bar.Message = p;
                }
                catch (Exception ex)
                {
                    Bar.Severity = InfoBarSeverity.Warning;
                    Bar.Title = "无法启动记事本（日志内容已在上方对话框里看过）";
                    Bar.Message = ex.Message + "\n路径：" + p;
                }
                Bar.IsOpen = true;
            }
        }
        catch (Exception ex)
        {
            Bar.Severity = InfoBarSeverity.Error;
            Bar.Title = "打开日志失败";
            Bar.Message = ex.ToString();
            Bar.IsOpen = true;
        }
    }
}
