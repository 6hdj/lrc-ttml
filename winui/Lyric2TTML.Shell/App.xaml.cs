using System;
using System.IO;
using System.Linq;
using Lyric2TTML.Shell.Backend;
using Microsoft.UI.Xaml;

namespace Lyric2TTML.Shell;

public partial class App : Application
{
    private Window? _window;

    /// <summary>主窗口引用（文件对话框需要 owner 句柄）。</summary>
    public static Window? MainWindow { get; private set; }

    /// <summary>全应用唯一的后端进程管理器（退出时必须清理，见 MainWindow.Closed）。</summary>
    public static BackendHost Backend { get; } = new();

    public App() => InitializeComponent();

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _window = new MainWindow();
        MainWindow = _window;
        _window.Activate();

        // `--pages-smoke`：**逐页构造**并把结果写文件再退出。
        // 为什么需要它：XAML 错误（未定义资源键 / 布局越界）只在**页面真正加载**时才抛，
        // 而这里没有 UI 自动化 ⇒ 用一个自检开关把"每个页面都能构造"变成可验收项。
        if (Environment.GetCommandLineArgs().Any(a => a == "--theme-smoke"))
        {
            var mw = _window as MainWindow;
            try
            {
                mw?.ApplyTheme(2);   // 深色
                // 等一个布局周期，让 ActualTheme 真正更新
                _window.DispatcherQueue.TryEnqueue(() =>
                {
                    try
                    {
                        File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "theme-smoke.txt"),
                            mw?.ThemeReport() ?? "no window");
                    }
                    catch { }
                    Environment.Exit(0);
                });
            }
            catch
            {
                Environment.Exit(1);
            }
            return;
        }

        if (Environment.GetCommandLineArgs().Any(a => a == "--pages-smoke"))
        {
            var report = (_window as MainWindow)?.RunPagesSmoke() ?? "no main window";
            try
            {
                File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "pages-smoke.txt"), report);
            }
            catch
            {
                // 写不了就只靠退出码，不假装成功
            }
            Environment.Exit(report.Contains("FAIL", StringComparison.Ordinal) ? 1 : 0);
        }
    }
}
