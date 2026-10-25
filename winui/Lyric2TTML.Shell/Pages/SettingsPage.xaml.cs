using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using Lyric2TTML.Shell.Backend;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;

namespace Lyric2TTML.Shell.Pages;

/// <summary>
/// 设置页（阶段 4 第 5 项 + 关于）。
/// 只写外壳自己的 shell-settings.json（默认值），不复制后端配置；保存失败必须显示原因。
/// </summary>
public sealed partial class SettingsPage : Page
{
    public SettingsPage()
    {
        InitializeComponent();
        LoadIntoUi(SettingsHost.Current, SettingsHost.LoadError);
        ShowAbout();
    }

    private static string TagOf(ComboBox cb) =>
        cb.SelectedItem is ComboBoxItem it && it.Tag is string s ? s : "";

    private static void Select(ComboBox cb, string tag)
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

    private void LoadIntoUi(ShellSettings s, string loadError)
    {
        Select(CbTheme, s.Theme.ToString());
        Select(CbModel, s.Model);
        Select(CbLang, s.Language);
        Select(CbSinger, s.SingerMode);
        CkTails.IsChecked = s.KeepTails;
        CkVerify.IsChecked = s.VerifyAmll;
        CkSameAgent.IsChecked = s.SameAgentOverlap;
        EdOutput.Text = s.OutputDir;
        EdTifaPython.Text = s.TifaPython;

        if (!string.IsNullOrEmpty(loadError))
        {
            Bar.Severity = InfoBarSeverity.Warning;
            Bar.Title = "读取设置时出现问题";
            Bar.Message = loadError + "（已使用默认值）";
            Bar.IsOpen = true;
        }
    }

    private ShellSettings FromUi() => new()
    {
        Theme = int.TryParse(TagOf(CbTheme), out var t) ? t : 0,
        Model = TagOf(CbModel),
        Language = TagOf(CbLang),
        SingerMode = TagOf(CbSinger),
        KeepTails = CkTails.IsChecked == true,
        VerifyAmll = CkVerify.IsChecked == true,
        SameAgentOverlap = CkSameAgent.IsChecked == true,
        OutputDir = EdOutput.Text.Trim(),
        TifaPython = EdTifaPython.Text.Trim(),
    };

    private void ShowAbout()
    {
        var asm = typeof(App).Assembly;
        string ver = asm.GetName().Version?.ToString() ?? "?";
        string info = asm.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "";
        var settingsPath = ShellSettings.LastSavedPath.Length > 0
            ? ShellSettings.LastSavedPath
            : (System.IO.File.Exists(ShellSettings.DefaultPath)
                ? ShellSettings.DefaultPath
                : System.IO.Path.Combine(AppContext.BaseDirectory, "shell-settings.json"));
        LbVersion.Text = $"外壳版本：{ver}" + (info.Length > 0 ? $"（{info}）" : "")
                         + $"\n设置文件：{settingsPath}";
        LbBackend.Text = "后端：" + (App.Backend.IsRunning
            ? $"运行中 {App.Backend.BaseUrl}（端口 {App.Backend.Port}）"
            : "未启动") + "\n日志：" + (App.Backend.LogPath.Length > 0
            ? App.Backend.LogPath : "（尚未创建）");
    }

    private void Say(InfoBarSeverity sev, string title, string msg)
    {
        Bar.Severity = sev;
        Bar.Title = title;
        Bar.Message = msg;
        Bar.IsOpen = true;
    }

    private async void BtnBrowse_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new FolderPicker();
            picker.FileTypeFilter.Add("*");
            var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(App.MainWindow!);
            WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
            var folder = await picker.PickSingleFolderAsync();
            if (folder is not null) EdOutput.Text = folder.Path;
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "无法打开目录选择器", ex.Message);
        }
    }

    /// <summary>目录可写性探针：用户选了不可写目录时必须当场说清楚（不能默默回退）。</summary>
    private static string CheckWritable(string dir)
    {
        try
        {
            Directory.CreateDirectory(dir);
            var probe = Path.Combine(dir, ".l2t_write_probe");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
            return "";
        }
        catch (Exception ex)
        {
            return ex.Message;
        }
    }

    /// <summary>主题下拉**选中即预览**（和旧侧边栏行为一致；保存后才持久化）。</summary>
    private void CbTheme_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        try
        {
            var tag = TagOf(CbTheme);
            if (tag.Length > 0 && int.TryParse(tag, out var t))
                if (App.MainWindow is MainWindow mw) mw.ApplyTheme(t);
        }
        catch
        {
            // 预览失败不影响保存
        }
    }

    private void BtnSave_Click(object sender, RoutedEventArgs e)
    {
        var s = FromUi();
        // ⚠️ 探针只用来**提醒**，不阻止保存：用户实测"换任何路径都提示不可写"，
        // 而同样的目录在资源管理器里可以写 —— 说明是这个进程被安全软件限制了写入。
        // 硬拦会让人根本没法保存设置；真正写不进去时后端会报错（并写任务日志）。
        string probeWarning = "";
        if (!string.IsNullOrWhiteSpace(s.OutputDir))
        {
            var why = CheckWritable(s.OutputDir.Trim());
            if (why.Length > 0)
                probeWarning = "\n⚠ 写入测试失败（可能是安全软件拦截本程序）：" + why
                             + "\n保存仍会用这个目录，实际能否写入以后端转换结果为准。";
        }
        var err = SettingsHost.Save(s);
        if (err.Length > 0)
        {
            Say(InfoBarSeverity.Error, "保存失败", err);
            LbStatus.Text = "保存失败";
            return;
        }
        // 主题立即生效（设置页是唯一入口：侧边栏那份已删除）
        if (App.MainWindow is MainWindow mw) mw.ApplyTheme(s.Theme);
        RefreshAbout();
        if (probeWarning.Length > 0)
        {
            Say(InfoBarSeverity.Warning, "已保存（目录写入测试未通过）",
                "已写入：" + ShellSettings.LastSavedPath + probeWarning);
        }
        else
        {
            Say(InfoBarSeverity.Success, "已保存", "已写入：" + ShellSettings.LastSavedPath);
        }
        LbStatus.Text = "已保存 " + DateTime.Now.ToString("HH:mm:ss");
    }

    /// <summary>关于区是活状态（后端/日志/设置文件路径），每次进入页面都要刷新。</summary>
    private void RefreshAbout() => ShowAbout();

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        try
        {
            SettingsHost.Reload();
            LoadIntoUi(SettingsHost.Current, SettingsHost.LoadError);
            RefreshAbout();
        }
        catch
        {
            // 刷新失败不影响页面可用
        }
    }

    private void BtnReset_Click(object sender, RoutedEventArgs e)
    {
        LoadIntoUi(new ShellSettings(), "");
        LbStatus.Text = "已恢复默认（点保存生效）";
    }

    private void BtnLog_Click(object sender, RoutedEventArgs e)
    {
        var p = App.Backend.LogPath;
        if (string.IsNullOrEmpty(p) || !File.Exists(p))
        {
            Say(InfoBarSeverity.Warning, "还没有日志", "先启动一次后端（转换一次）再来看。");
            return;
        }
        try
        {
            var err = ShellOpen.Locate(p);
            if (err.Length == 0) { Say(InfoBarSeverity.Success, "已定位后端日志", p); return; }
            Say(InfoBarSeverity.Warning, "系统定位失败，路径已复制", err + "\n" + p);
            try { var dp = new Windows.ApplicationModel.DataTransfer.DataPackage(); dp.SetText(p);
                  Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(dp); } catch { }
        }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "打开日志失败", ex.Message); }
    }

    private async void BtnOutput_Click(object sender, RoutedEventArgs e)
    {
        // 打开**真正会写入**的目录：设置里填了就用它，否则程序目录\outputs。
        var s = SettingsHost.Current;
        string dir = string.IsNullOrWhiteSpace(s.OutputDir)
            ? Path.Combine(AppContext.BaseDirectory, "outputs")
            : s.OutputDir.Trim();
        try
        {
            Directory.CreateDirectory(dir);
            // 目录用 shell 直接打开更符合预期（ShellOpen.Locate 是"在父目录里选中它"）；
            // 失败再退回进程内 Shell API，最后才报错——不出现"点了没反应"。
            try
            {
                Process.Start(new ProcessStartInfo { FileName = dir, UseShellExecute = true });
                Say(InfoBarSeverity.Success, "已打开输出目录", dir);
                return;
            }
            catch
            {
                var err = ShellOpen.Locate(dir);
                if (err.Length == 0 || await ShellOpen.OpenFolderAsync(dir))
                {
                    Say(InfoBarSeverity.Success, "已打开输出目录", dir);
                    return;
                }
                Say(InfoBarSeverity.Error, "打开输出目录失败", err + "\n路径：" + dir);
            }
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "打开输出目录失败", ex.Message + "\n路径：" + dir);
        }
    }

    private void BtnDiagnostics_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindow is MainWindow mw) mw.GoTo("help");
    }
}
