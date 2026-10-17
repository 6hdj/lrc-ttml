using System;
using System.Collections.Generic;
using System.Text.Json;

namespace Lyric2TTML.Shell.Backend;

/// <summary>
/// 「模型管理 / 运行库」页面的**纯数据层**：把后端的 JSON 解析成强类型行。
///
/// 为什么单独抽出来：这些页面只允许显示**后端的真实状态**（项目纪律：不放假数据、
/// 不假装成功）。解析逻辑放进 WinUI 页面里就没法单测；这里做成纯 C#（只依赖
/// System.Text.Json），由控制台测试工程直接链接并断言真实 JSON 样本。
///
/// 解析失败一律 **抛异常**（不回退成空列表冒充"全部正常"）。
/// </summary>
public sealed record ModelRow(
    string Id, string Name, string Category, string Purpose, string Note, string License,
    string Status, string StatusLabel, string Detail, string Location,
    long ApproxSize, long PresentBytes, bool CanDownload, bool IsDefault,
    bool MissingFiles, bool SizeMismatch);

public sealed record ModelCatalog(
    int Total, int Ready, int Missing, string HfCache, string Manifest,
    IReadOnlyList<ModelRow> Models);

public sealed record RuntimeRow(
    string Id, string Name, string Status, string StatusLabel, string Detail, string Purpose,
    string Location, long DownloadBytes, long PresentBytes, bool CanDownload);

public sealed record RuntimeStatus(string Root, IReadOnlyList<RuntimeRow> Runtimes);

public sealed record WorkerStatus(
    string State, string Detail, double Progress, string WantVersion,
    bool SourcesReady, string SourcesReason, string Exe)
{
    // worker_service 的状态值有 "ready" 和 "installed" 两种（用户实测：显示已安装但
    // 「校验/卸载」却是灰的 —— 因为只认了 "ready"）。两者都算就绪。
    public bool Ready => State == "ready" || State == "installed";
}

public static class Management
{
    private static JsonDocument ParseDoc(string json)
    {
        if (string.IsNullOrWhiteSpace(json))
            throw new InvalidOperationException("后端返回为空");
        try
        {
            return JsonDocument.Parse(json);
        }
        catch (JsonException ex)
        {
            throw new InvalidOperationException("后端返回的不是 JSON：" + ex.Message, ex);
        }
    }

    private static string Str(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString() ?? "" : "";

    private static long Long(JsonElement e, string name)
    {
        if (!e.TryGetProperty(name, out var v)) return 0;
        return v.ValueKind switch
        {
            JsonValueKind.Number when v.TryGetInt64(out var n) => n,
            JsonValueKind.Number => (long)v.GetDouble(),
            _ => 0,
        };
    }

    private static bool Bool(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;

    private static double Dbl(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number
            ? v.GetDouble() : 0.0;

    private static List<string> StrList(JsonElement e, string name)
    {
        var outList = new List<string>();
        if (!e.TryGetProperty(name, out var v) || v.ValueKind != JsonValueKind.Array) return outList;
        foreach (var item in v.EnumerateArray())
            if (item.ValueKind == JsonValueKind.String) outList.Add(item.GetString() ?? "");
        return outList;
    }

    /// <summary>解析 `--model-cli json` 的输出。</summary>
    public static ModelCatalog ParseModels(string json)
    {
        using var doc = ParseDoc(json);
        var root = doc.RootElement;
        var models = new List<ModelRow>();
        if (root.TryGetProperty("models", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var m in arr.EnumerateArray())
            {
                models.Add(new ModelRow(
                    Id: Str(m, "id"),
                    Name: Str(m, "name"),
                    Category: Str(m, "category"),
                    Purpose: Str(m, "purpose"),
                    Note: Str(m, "note"),
                    License: Str(m, "license"),
                    Status: Str(m, "status"),
                    StatusLabel: Str(m, "status_label"),
                    Detail: Str(m, "detail"),
                    Location: Str(m, "location"),
                    ApproxSize: Long(m, "approx_size"),
                    PresentBytes: Long(m, "present_bytes"),
                    CanDownload: Bool(m, "can_download"),
                    IsDefault: Bool(m, "default"),
                    MissingFiles: StrList(m, "missing").Count > 0,
                    SizeMismatch: StrList(m, "size_mismatch").Count > 0));
            }
        }
        return new ModelCatalog(
            Total: (int)Long(root, "total"),
            Ready: (int)Long(root, "ready"),
            Missing: (int)Long(root, "missing"),
            HfCache: Str(root, "hf_cache"),
            Manifest: Str(root, "manifest"),
            Models: models);
    }

    /// <summary>解析 `--runtime-cli --status --json` 的输出。</summary>
    public static RuntimeStatus ParseRuntimes(string json)
    {
        using var doc = ParseDoc(json);
        var root = doc.RootElement;
        var rows = new List<RuntimeRow>();
        if (root.TryGetProperty("runtimes", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var r in arr.EnumerateArray())
            {
                rows.Add(new RuntimeRow(
                    Id: Str(r, "id"),
                    Name: Str(r, "name"),
                    Status: Str(r, "status"),
                    StatusLabel: Str(r, "status_label"),
                    Detail: Str(r, "detail"),
                    Purpose: Str(r, "purpose"),
                    Location: Str(r, "location"),
                    DownloadBytes: Long(r, "download_bytes"),
                    PresentBytes: Long(r, "present_bytes"),
                    CanDownload: Bool(r, "can_download")));
            }
        }
        return new RuntimeStatus(Str(root, "root"), rows);
    }

    /// <summary>解析 `--worker-cli --status` 的输出（单对象）。</summary>
    public static WorkerStatus ParseWorker(string json)
    {
        using var doc = ParseDoc(json);
        var r = doc.RootElement;
        return new WorkerStatus(
            State: Str(r, "state"),
            Detail: Str(r, "detail"),
            Progress: Dbl(r, "progress"),
            WantVersion: Str(r, "want_version"),
            SourcesReady: Bool(r, "sources_ready"),
            SourcesReason: Str(r, "sources_reason"),
            Exe: Str(r, "exe"));
    }

    /// <summary>把字节数变成好读的字符串（页面用；测试也断言它）。</summary>
    public static string Human(long bytes)
    {
        if (bytes <= 0) return "—";
        string[] units = { "B", "KB", "MB", "GB", "TB" };
        double v = bytes;
        int i = 0;
        while (v >= 1024 && i < units.Length - 1) { v /= 1024; i++; }
        return i == 0 ? $"{bytes} {units[i]}" : $"{v:0.#} {units[i]}";
    }
}
