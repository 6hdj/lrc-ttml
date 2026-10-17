using System;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Lyric2TTML.Shell.Backend;
using Lyric2TTML.Shell.Editing;

namespace Lyric2TTML.Shell.Editing;

/// <summary>
/// `IEditBackend` 的**真实 HTTP 实现**（对接既有后端端点，不改后端契约）。
///
/// 契约来自已读源码：
///  * `POST /api/validate`：入参 `{doc}`，**只算不写**，返回 `{normalize, check, doc, version}`
///    ⇒ 用来做预检，把"服务端会怎么自动修"先摆出来（不自己发明规则）；
///  * `POST /api/edit`：入参 `{id, version, doc}`；**必须带 version**，
///    因为后端 `check_version(expected=None)` 会**直接放行**（等于没锁）；
///    409 ⇒ 冲突，**绝不覆盖**。
/// </summary>
public sealed class HttpEditBackend : IEditBackend
{
    private readonly string _baseUrl;
    public HttpEditBackend(string baseUrl) => _baseUrl = baseUrl.TrimEnd('/');

    public async Task<ValidateResult> ValidateAsync(string docJson, CancellationToken ct = default)
    {
        using var api = new BackendClient(_baseUrl);
        var body = new JsonObject { ["doc"] = JsonNode.Parse(docJson) };
        try
        {
            using var res = await api.PostJsonAsync("/api/validate", body.ToJsonString());
            var root = res.RootElement;
            var docOut = root.TryGetProperty("doc", out var d) ? d.GetRawText() : docJson;
            return new ValidateResult { Ok = true, DocJson = docOut, FixCount = CountFixes(root) };
        }
        catch (HttpRequestException ex)
        {
            return new ValidateResult { Ok = false, Detail = ex.Message };
        }
    }

    public async Task<SaveResult> SaveAsync(string taskId, int version, string docJson,
                                            CancellationToken ct = default)
    {
        using var api = new BackendClient(_baseUrl);
        var body = new JsonObject
        {
            ["id"] = taskId,
            ["version"] = version,                       // **必带**：否则后端不做锁检查
            ["doc"] = JsonNode.Parse(docJson),
            // ⚠️ WinUI 编辑台的编辑**只写草稿(.edit.json)**，正式 TTML 由「保存」按钮写。
            // 不传这个字段后端默认 true（旧 Web 界面/自动化脚本行为不变）。
            ["persist"] = false,
        };
        try
        {
            using var res = await api.PostJsonAsync("/api/edit", body.ToJsonString());
            var root = res.RootElement;
            int ver = root.TryGetProperty("version", out var v) && v.TryGetInt32(out var vi) ? vi : version + 1;
            var docOut = root.TryGetProperty("doc", out var d) ? d.GetRawText() : docJson;
            return new SaveResult { Ok = true, DocJson = docOut, Version = ver, FixCount = CountFixes(root) };
        }
        catch (HttpRequestException ex) when (ex.Message.StartsWith("409"))
        {
            return new SaveResult { Ok = false, Conflict = true, Detail = ex.Message };
        }
        catch (HttpRequestException ex)
        {
            return new SaveResult { Ok = false, Detail = ex.Message };
        }
    }

    /// <summary>
    /// `POST /api/text`：`{id, version, op, ...payload}`（payload 平铺，见 `edit_text.apply_op`）。
    /// 后端会：改模型 → normalize → check → 原子落盘，并返回新的 `{doc, version}`。
    /// </summary>
    public async Task<SaveResult> TextOpAsync(string taskId, int version, string op,
                                              JsonObject payload, CancellationToken ct = default)
    {
        using var api = new BackendClient(_baseUrl);
        var body = new JsonObject { ["id"] = taskId, ["version"] = version, ["op"] = op, ["persist"] = false };
        if (payload is not null)
            foreach (var kv in payload) body[kv.Key] = kv.Value?.DeepClone();
        try
        {
            using var res = await api.PostJsonAsync("/api/text", body.ToJsonString());
            var root = res.RootElement;
            int ver = root.TryGetProperty("version", out var v) && v.TryGetInt32(out var vi) ? vi : version + 1;
            var docOut = root.TryGetProperty("doc", out var d) ? d.GetRawText() : "";
            return new SaveResult { Ok = true, DocJson = docOut, Version = ver, FixCount = CountFixes(root) };
        }
        catch (HttpRequestException ex) when (ex.Message.StartsWith("409"))
        {
            return new SaveResult { Ok = false, Conflict = true, Detail = ex.Message };
        }
        catch (HttpRequestException ex)
        {
            return new SaveResult { Ok = false, Detail = ex.Message };
        }
    }

    /// <summary>容错地取"自动修复条数"（后端字段名变了也不会抛）。</summary>
    private static int CountFixes(JsonElement root)
    {
        try
        {
            if (!root.TryGetProperty("normalize", out var n)) return 0;
            if (n.TryGetProperty("fixes", out var f) && f.ValueKind == JsonValueKind.Array) return f.GetArrayLength();
            if (n.TryGetProperty("count", out var c) && c.TryGetInt32(out var ci)) return ci;
        }
        catch { }
        return 0;
    }
}
