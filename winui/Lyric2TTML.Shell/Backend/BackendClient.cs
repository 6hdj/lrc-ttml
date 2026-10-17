using System;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;

namespace Lyric2TTML.Shell.Backend;

/// <summary>
/// HTTP 客户端（类型化）。**错误必须原样抛/回**，不许把失败吞成空对象。
/// 现阶段只用 health/info；阶段 3 的 upload/convert/task 都挂在这里。
/// </summary>
public sealed class BackendClient : IDisposable
{
    private readonly HttpClient _http;

    public BackendClient(string baseUrl)
    {
        _http = new HttpClient { BaseAddress = new Uri(baseUrl), Timeout = TimeSpan.FromSeconds(30) };
    }

    public async Task<JsonDocument> GetJsonAsync(string path)
    {
        using var r = await _http.GetAsync(path);
        var text = await r.Content.ReadAsStringAsync();
        if (!r.IsSuccessStatusCode)
            throw new HttpRequestException($"GET {path} → {(int)r.StatusCode}：{Trim(text)}");
        try { return JsonDocument.Parse(text); }
        catch (JsonException ex) { throw new InvalidOperationException($"GET {path} 返回的不是 JSON：{Trim(text)}", ex); }
    }

    private static string Trim(string s) => s.Length > 300 ? s[..300] + "…" : s;


    /// <summary>POST 一个 JSON 对象并返回响应 JSON。**非 2xx 一律抛**（含结构化错误体）。</summary>
    public async Task<JsonDocument> PostJsonAsync(string path, string jsonBody)
    {
        using var content = new StringContent(jsonBody, System.Text.Encoding.UTF8, "application/json");
        using var r = await _http.PostAsync(path, content);
        var text = await r.Content.ReadAsStringAsync();
        if (!r.IsSuccessStatusCode)
            throw new HttpRequestException($"{(int)r.StatusCode}|{Trim(text)}");
        try { return JsonDocument.Parse(text); }
        catch (JsonException ex) { throw new InvalidOperationException("返回的不是 JSON：" + Trim(text), ex); }
    }

    public void Dispose() => _http.Dispose();
}
