using System;
using System.IO;
using Lyric2TTML.Shell.Pages;
using Microsoft.UI;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using WinRT.Interop;

namespace Lyric2TTML.Shell;

public sealed partial class MainWindow : Window
{
    private readonly DiagnosticsPage _diag = new();
    // ⚠️⚠️ **页面必须缓存**（2026-10-16 用户实测报的 bug）：
    // 原来每次切页都 `new WorkspacePage()` ⇒ 切走再回来**整页重建**，
    // 正在跑的进度、日志、已选文件全没了（用户描述："切到设置再切回来就清空了"）。
    // 修法：一个 tag 一个实例，切页只换 Content。
    private readonly System.Collections.Generic.Dictionary<string, Microsoft.UI.Xaml.Controls.Page> _pages = new();

    public MainWindow()
    {
        InitializeComponent();
        Title = "Lyric2TTML";
        // 窗口/任务栏图标：exe 图标由 csproj 的 ApplicationIcon 提供；
        // 运行时再显式设一次（未打包应用的任务栏/标题栏图标有时不跟 exe）。
        try
        {
            var ico = Path.Combine(AppContext.BaseDirectory, "Assets", "app.ico");
            if (File.Exists(ico)) AppWindow.SetIcon(ico);
        }
        catch { /* 图标失败不影响启动 */ }
        TryEnableMica();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        // 启动时读一次外壳设置（主题 / 默认参数），失败不崩——设置页会显示原因
        Backend.SettingsHost.Reload();
        ApplyTheme(Backend.SettingsHost.Current.Theme);
        Backend.SettingsHost.Changed += () => ApplyTheme(Backend.SettingsHost.Current.Theme);
        Closed += async (_, _) => await App.Backend.StopAsync();
        Nav.SelectedItem = Nav.MenuItems[0];
        Navigate("welcome");
    }

    /// <summary>供开始页/设置页跳转：按 tag 选中左侧导航项（会触发 Navigate）。</summary>
    public void GoTo(string tag)
    {
        foreach (var item in Nav.MenuItems)
        {
            if (item is NavigationViewItem ni && ni.Tag is string t && t == tag)
            {
                Nav.SelectedItem = ni;
                return;
            }
        }
        Navigate(tag);
    }

    /// <summary>
    /// 应用主题（设置页保存后立即生效）。
    /// ⚠️ 侧边栏那份「外观」下拉已按用户要求删除 ⇒ 主题只能从这里直接设到内容根上；
    /// 原来的实现是"改 ThemeBox.SelectedIndex"，ThemeBox 一删就完全失效（点了没反应）。
    /// </summary>
    private int _theme = 0;

    private static ElementTheme ToElementTheme(int theme) => Math.Clamp(theme, 0, 2) switch
    {
        1 => ElementTheme.Light,
        2 => ElementTheme.Dark,
        _ => ElementTheme.Default,
    };

    public void ApplyTheme(int theme)
    {
        _theme = Math.Clamp(theme, 0, 2);
        var e = ToElementTheme(_theme);
        // ① 内容根（所有页面都在它下面，正常情况这一条就够）
        if (Content is FrameworkElement root) root.RequestedTheme = e;
        // ② 页面是**缓存**的（切页不重建）⇒ 逐个显式设置，避免个别页面保留旧主题
        foreach (var page in _pages.Values)
        {
            try { page.RequestedTheme = e; } catch { }
        }
        // ③ 标题栏区域（自定义 TitleBar 在 Content 里，一般随 ①；这里兜底）
        try { AppTitleBar.RequestedTheme = e; } catch { }
        try
        {
            var actual = (Content as FrameworkElement)?.ActualTheme.ToString() ?? "?";
            System.IO.File.AppendAllText(
                System.IO.Path.Combine(AppContext.BaseDirectory, "theme-debug.log"),
                $"{DateTime.Now:HH:mm:ss} ApplyTheme({_theme}) requested={e} rootActual={actual}\n");
        }
        catch { }
    }

    /// <summary>`--theme-smoke` 用：报告主题是否真的生效（给自动化/诊断用）。</summary>
    public string ThemeReport()
    {
        var root = Content as FrameworkElement;
        return $"theme={_theme} requested={root?.RequestedTheme} actualRoot={root?.ActualTheme} "
             + $"navActual={Nav.ActualTheme} frameActual={ContentFrame.ActualTheme}";
    }

    /// <summary>Mica：仅主窗口用；内容区一律不透明（不滥用模糊）。</summary>
    private void TryEnableMica()
    {
        try
        {
            if (MicaController.IsSupported())
                SystemBackdrop = new MicaBackdrop { Kind = MicaKind.BaseAlt };
        }
        catch (Exception)
        {
            // 不支持就退纯色，不崩、不假成功
        }
    }

    private void Nav_SelectionChanged(NavigationView sender,
                                      NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is NavigationViewItem item && item.Tag is string tag)
            Navigate(tag);
    }

    private void Navigate(string tag)
    {
        // ⚠️⚠️ **页面必须缓存**（2026-10-16 用户实测报的 bug）：
        // 原来每次切页都 `new WorkspacePage()` ⇒ 切走再回来**整页重建**：
        // 正在跑的任务进度、实时日志、已选文件**全部清空**
        //（用户原话："切到设置再切回歌词工作台，显示内容被清空了"）。
        // 修法：一个 tag 一个实例，切页只换 `ContentFrame.Content`。
        if (!_pages.TryGetValue(tag, out var page))
        {
            page = tag switch
            {
                "welcome" => new WelcomePage(),
                "home" => new WorkspacePage(),
                "editor" => new EditorPage(),
                "tasks" => new TasksPage(),
                "models" => new ModelsPage(),
                "runtime" => new RuntimePage(),
                "settings" => new SettingsPage(),
                "help" => _diag,
                _ => new PageHost(tag),
            };
            _pages[tag] = page;
            if (tag == "help") _diag.Refresh();   // 只在**首次**进入时自动检查一次
        }
        ContentFrame.Content = page;
    }

    /// <summary>
    /// `--pages-smoke` 的实现：把每个页面都**真正构造**一遍（XAML 解析、资源键、控件名
    /// 都会在这里被验证），返回逐页 PASS/FAIL 文本。异常必须带类型和消息，不吞。
    /// </summary>
    public string RunPagesSmoke()
    {
        var sb = new System.Text.StringBuilder();
        var tags = new[] { "welcome", "home", "editor", "tasks", "models", "runtime", "settings", "help" };
        foreach (var tag in tags)
        {
            try
            {
                Microsoft.UI.Xaml.Controls.Page page = tag switch
                {
                    "welcome" => new WelcomePage(),
                    "home" => new WorkspacePage(),
                    "editor" => new EditorPage(),
                    "tasks" => new TasksPage(),
                    "models" => new ModelsPage(),
                    "runtime" => new RuntimePage(),
                    "settings" => new SettingsPage(),
                    "help" => new DiagnosticsPage(),
                    _ => new PageHost(tag),
                };
                sb.AppendLine($"{tag}: OK {page.GetType().Name}");
            }
            catch (Exception ex)
            {
                sb.AppendLine($"{tag}: FAIL {ex.GetType().Name}: {ex.Message}");
            }
        }
        return sb.ToString();
    }
}
