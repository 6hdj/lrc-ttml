using System;
using System.IO;
using System.Text.Json;

namespace Lyric2TTML.Shell.Backend;

/// <summary>
/// WinUI 外壳的本地设置（只保存**外壳自己的默认值**，不复制后端配置）。
///
/// 与后端的关系：这些值只是"下次打开时预选的参数"，点「开始处理」时仍原样发给后端；
/// 后端有自己的 config（旧壳用），这里**不读也不写**它，避免两处真相不一致。
/// 纯 C#，测试工程可直接链接。
/// </summary>
public sealed record ShellSettings
{
    public string Model { get; init; } = "large-v3";
    public string Language { get; init; } = "auto";
    public string SingerMode { get; init; } = "off";
    public string AlignEngine { get; init; } = "whisper";
    /// <summary>TIFA 实验引擎使用的外部 Python（空 = 禁用 TIFA）。</summary>
    public string TifaPython { get; init; } = "";
    public bool KeepTails { get; init; } = true;
    public bool VerifyAmll { get; init; } = true;
    public bool SameAgentOverlap { get; init; }
    public int Theme { get; init; } // 0=跟随系统 1=浅色 2=深色

    /// <summary>歌词（TTML）保存目录。空 = 后端默认（程序目录\outputs）。</summary>
    public string OutputDir { get; init; } = "";

    public static string DefaultPath =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                     "Lyric2TTML", "shell-settings.json");

    private static readonly JsonSerializerOptions Opts = new()
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true,
    };

    /// <summary>读设置；文件不存在/损坏 ⇒ 返回默认值并把原因写进 error（**不静默**）。</summary>
    public static ShellSettings Load(out string error, string? path = null)
    {
        error = "";
        // 保存会在 LocalAppData 写不进时回退到程序目录 ⇒ 读取也要看两个位置
        var candidates = path is not null
            ? new[] { path }
            : new[] { DefaultPath, Path.Combine(AppContext.BaseDirectory, "shell-settings.json") };
        var problems = new System.Collections.Generic.List<string>();
        foreach (var candidate in candidates)
        {
            try
            {
                if (!File.Exists(candidate)) continue;
                var text = File.ReadAllText(candidate);
                if (string.IsNullOrWhiteSpace(text)) continue;
                var s = JsonSerializer.Deserialize<ShellSettings>(text, Opts);
                if (s is null)
                {
                    problems.Add(candidate + " 内容为空");
                    continue;
                }
                LastSavedPath = candidate;
                return s;
            }
            catch (Exception ex)
            {
                problems.Add(candidate + " → " + ex.Message);
            }
        }
        if (problems.Count > 0) error = "读取设置失败：" + string.Join("；", problems);
        return new ShellSettings();
    }

    /// <summary>上次成功保存的位置（界面用来显示"实际存到哪"）。</summary>
    public static string LastSavedPath { get; private set; } = "";

    private static string TryWrite(string path, ShellSettings s)
    {
        try
        {
            var dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            File.WriteAllText(path, JsonSerializer.Serialize(s, Opts));
            LastSavedPath = path;
            return "";
        }
        catch (Exception ex)
        {
            return ex.Message;
        }
    }

    /// <summary>
    /// 保存设置；返回 error（空 = 成功）。
    /// ⚠️ 用户实测 LocalAppData 下写 shell-settings.json 会 Access denied（权限/杀软环境差异），
    /// 所以按顺序回退：LocalAppData → 程序目录。两个都失败才报错，并带上两个位置的原因。
    /// </summary>
    public string Save(string? path = null)
    {
        if (path is not null)
        {
            var e1 = TryWrite(path, this);
            return e1.Length == 0 ? "" : "保存设置失败：" + e1;
        }
        var errs = new System.Collections.Generic.List<string>();
        foreach (var candidate in new[]
                 {
                     DefaultPath,
                     Path.Combine(AppContext.BaseDirectory, "shell-settings.json"),
                 })
        {
            var err = TryWrite(candidate, this);
            if (err.Length == 0) return "";
            errs.Add(candidate + " → " + err);
        }
        return "保存设置失败（两个位置都写不进去）：" + string.Join("；", errs);
    }
}

/// <summary>
/// 进程级设置宿主：启动时读一次，设置页保存后通知各页刷新。
/// 为什么要有它：页面是**缓存的**（切页不重建），不能让每个页面自己读文件、
/// 否则设置页改了、工作台还是旧值（这正是旧壳踩过的"改了不生效"）。
/// </summary>
public static class SettingsHost
{
    public static ShellSettings Current { get; private set; } = new();
    public static string LoadError { get; private set; } = "";
    public static event Action? Changed;

    public static void Reload()
    {
        Current = ShellSettings.Load(out var err);
        LoadError = err;
        Changed?.Invoke();
    }

    public static string Save(ShellSettings s)
    {
        var err = s.Save();
        if (err.Length == 0)
        {
            Current = s;
            LoadError = "";
            Changed?.Invoke();
        }
        return err;
    }
}

