using System;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace Lyric2TTML.Shell.Editing;

/// <summary>预检结果（对应 `POST /api/validate`：**只算不写**，返回修好后的 doc）。</summary>
public sealed class ValidateResult
{
    public bool Ok { get; init; }
    public string DocJson { get; init; } = "";
    /// <summary>规范化会改动几处（后端 `normalize.fixes` 的条数）。</summary>
    public int FixCount { get; init; }
    public string Detail { get; init; } = "";
}

/// <summary>保存结果（对应 `POST /api/edit`：`apply_doc` 返回 doc/version/normalize/check）。</summary>
public sealed class SaveResult
{
    public bool Ok { get; init; }
    public string DocJson { get; init; } = "";
    public int Version { get; init; }
    public int FixCount { get; init; }
    /// <summary>HTTP 409：文档已被别处改动 —— **绝不覆盖**，由界面提示重载。</summary>
    public bool Conflict { get; init; }
    public string Detail { get; init; } = "";
}

/// <summary>后端接缝（真实现走 HTTP；单测用假的 ⇒ 事务逻辑可无网络验证）。</summary>
public interface IEditBackend
{
    Task<ValidateResult> ValidateAsync(string docJson, CancellationToken ct = default);
    Task<SaveResult> SaveAsync(string taskId, int version, string docJson, CancellationToken ct = default);

    /// <summary>
    /// 文本/和声类操作走 `POST /api/text`（**服务端算**：如和声的时间跨度均分、撑开行尾）。
    /// `op` 必须是后端认识的 8 个名字之一（set_word/insert_word/delete_word/set_line_text/
    /// insert_line/delete_line/set_bg/remove_bg），payload 平铺进请求体（key/layer/index/text/agent）。
    /// </summary>
    Task<SaveResult> TextOpAsync(string taskId, int version, string op, JsonObject payload,
                                 CancellationToken ct = default);
}

/// <summary>一次事务的结果。</summary>
public sealed class CommitResult
{
    public bool Ok { get; init; }
    public bool NoChange { get; init; }        // 本地没改出东西 ⇒ 不发请求
    public bool Conflict { get; init; }        // 409：不覆盖，保留本地修改
    public int FixCount { get; init; }         // 服务端规范化改动了几处（如实告诉用户）
    public string Reason { get; init; } = "";
    public override string ToString() =>
        Ok ? (NoChange ? "无改动" : $"已保存 v{_version}") : (Conflict ? "版本冲突(409)" : "失败：" + Reason);
    internal int _version;
    public int Version => _version;
}

/// <summary>
/// **统一编辑事务**（批 1 地基；纯逻辑 + 后端接口注入 ⇒ 可单测）。
///
/// 契约来自已读的后端源码（不是猜的）：
///  * `apply_doc` 保存时**会自动 `normalize_timeline(policy="auto")` 就地修时间轴** ⇒
///    **必须用返回值刷新**，并**如实告诉用户"服务端自动修复了 N 处"**（不静默）；
///  * `validate()` **只算不写**且返回修好后的 doc ⇒ 用它做**预检**，不自己发明规则；
///  * `check_version`：**`expected is None` 直接放行** ⇒ 客户端**必须每次带 version**；
///  * 409 ⇒ **不覆盖对方改动**，保留本地修改并让用户重载。
///
/// 事务保证（用户要求 #6）：**任何一步失败都不留半完成状态** ——
/// 本地改动、脏标记、已保存版本三者**只在保存成功时才一起前进**。
/// </summary>
public sealed class EditSession
{
    private readonly IEditBackend _backend;

    public string TaskId { get; }
    public int Version { get; private set; }
    public JsonNode Doc { get; private set; }
    /// <summary>选中模型（与脏标记联动：脏时界面必须先提交或先问）。</summary>
    public EditSelection Selection { get; } = new();
    /// <summary>是否有未保存修改（成功保存后清零；失败/冲突**保持为真**）。</summary>
    public bool IsDirty { get; private set; }

    public EditSession(string taskId, int version, string docJson, IEditBackend backend)
    {
        TaskId = taskId;
        Version = version;
        Doc = JsonNode.Parse(docJson) ?? new JsonObject();
        _backend = backend;
    }

    /// <summary>
    /// **统一事务入口**：`mutate` 只负责改本地 `Doc`（返回 false 表示没改出东西）。
    /// 流程：本地改 → 预检（只算不写）→ 保存（带 version）→ 成功才前进版本、清脏。
    /// </summary>
    public async Task<CommitResult> CommitAsync(Func<JsonNode, bool> mutate, CancellationToken ct = default)
    {
        if (mutate is null) return new CommitResult { Ok = false, Reason = "没有改动动作" };

        // ① 在**快照**上先改：失败可整体回滚（不留半完成）
        var snapshot = Doc.DeepClone();
        bool changed;
        try { changed = mutate(Doc); }
        catch (Exception ex)
        {
            Doc = snapshot;                       // 回滚
            return new CommitResult { Ok = false, Reason = "改动出错（已回滚）：" + ex.Message };
        }
        if (!changed)
        {
            Doc = snapshot;
            return new CommitResult { Ok = true, NoChange = true, Reason = "没有实际改动" };
        }
        IsDirty = true;                            // 本地已改：从这一刻起算"未保存"

        // ② 预检（只算不写）：把服务端规范化会做什么**先告诉用户**，不自己发明规则
        ValidateResult pre;
        try { pre = await _backend.ValidateAsync(Doc.ToJsonString(), ct); }
        catch (Exception ex)
        {
            Doc = snapshot;                        // 预检失败 ⇒ 回滚本地改动
            IsDirty = false;
            return new CommitResult { Ok = false, Reason = "预检失败（已回滚）：" + ex.Message };
        }
        if (!pre.Ok)
        {
            Doc = snapshot;
            IsDirty = false;
            return new CommitResult { Ok = false, Reason = "预检不通过（已回滚）：" + pre.Detail };
        }

        // ③ 保存：**必带 version**（后端 expected=None 会直接放行，等于没有锁）
        SaveResult save;
        try { save = await _backend.SaveAsync(TaskId, Version, Doc.ToJsonString(), ct); }
        catch (Exception ex)
        {
            Doc = snapshot;
            IsDirty = false;
            return new CommitResult { Ok = false, Reason = "保存失败（已回滚）：" + ex.Message };
        }
        if (save.Conflict)
        {
            // **不覆盖对方的改动**（版本、服务端文件一概不动），
            // 但**保留用户刚做的本地修改** ⇒ 保持 IsDirty，界面提示"冲突：请重载或重试"。
            // ⚠️ 早期版本这里 `Doc = snapshot` 把用户的改动丢掉了（自己在用例里标为已知局限，已修）。
            return new CommitResult { Ok = false, Conflict = true, Reason = save.Detail };
        }
        if (!save.Ok)
        {
            Doc = snapshot;
            IsDirty = false;
            return new CommitResult { Ok = false, Reason = "保存被拒绝（已回滚）：" + save.Detail };
        }

        // ④ 成功：用**服务端返回值**刷新（它会规范化），版本前进，脏标记清零
        if (!string.IsNullOrWhiteSpace(save.DocJson))
        {
            try { Doc = JsonNode.Parse(save.DocJson) ?? Doc; } catch { /* 解析不了就保留本地 */ }
        }
        Version = save.Version;
        IsDirty = false;
        Selection.MarkClean();
        if (pre.FixCount > 0 || save.FixCount > 0)
        {
            // 如实报告"服务端自动修复"，不静默（AGENTS.md 设计文档 §5.2 的要求）
            return new CommitResult
            {
                Ok = true, FixCount = Math.Max(pre.FixCount, save.FixCount),
                Reason = $"服务端自动修复了 {Math.Max(pre.FixCount, save.FixCount)} 处时间",
                _version = Version,
            };
        }
        return new CommitResult { Ok = true, _version = Version };
    }

    /// <summary>
    /// 文本/和声操作的**统一事务入口**（与 <see cref="CommitAsync"/> 同一套语义：
    /// 成功才前进版本并清脏；409 **不覆盖对方**且**保留本地脏标记**；失败不留半完成）。
    ///
    /// ⚠️ 这类操作**必须走服务端** `POST /api/text`：和声的时间分布（沿用原跨度 / 从主唱后接 /
    /// 撑开行尾）是 `edit_text.set_bg_text` 算的，本地拼不出来。
    /// </summary>
    public async Task<CommitResult> CommitOpAsync(string op, JsonObject payload, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(op)) return new CommitResult { Ok = false, Reason = "没有操作名" };
        SaveResult save;
        try { save = await _backend.TextOpAsync(TaskId, Version, op, payload ?? new JsonObject(), ct); }
        catch (Exception ex) { return new CommitResult { Ok = false, Reason = "文本操作失败：" + ex.Message }; }

        if (save.Conflict)
        {
            IsDirty = true;                       // 保留用户的改动意图，界面提示重载/重试
            return new CommitResult { Ok = false, Conflict = true, Reason = save.Detail };
        }
        if (!save.Ok) return new CommitResult { Ok = false, Reason = "被拒绝：" + save.Detail };

        if (!string.IsNullOrWhiteSpace(save.DocJson))
        {
            try { Doc = JsonNode.Parse(save.DocJson) ?? Doc; } catch { /* 解析不了就保留旧 doc */ }
        }
        Version = save.Version;
        IsDirty = false;
        Selection.MarkClean();
        return new CommitResult { Ok = true, FixCount = save.FixCount, _version = Version };
    }

    /// <summary>外部（如"重新载入"）替换整份文档：版本与脏标记一起重置。</summary>
    public void Reset(string docJson, int version)
    {
        Doc = JsonNode.Parse(docJson) ?? new JsonObject();
        Version = version;
        IsDirty = false;
        Selection.MarkClean();
    }
}
