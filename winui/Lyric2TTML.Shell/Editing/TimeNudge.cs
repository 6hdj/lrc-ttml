using System;
using System.Collections.Generic;

namespace Lyric2TTML.Shell.Editing;

/// <summary>微调目标：动 begin / end / 两者（保持时长）。</summary>
public enum NudgeTarget { Begin, End, Both }

/// <summary>一次微调的结果。**失败必须带可读原因**（本项目"零静默失败"纪律）。</summary>
public sealed class NudgeOutcome
{
    public bool Ok { get; init; }
    public string Reason { get; init; } = "";
    /// <summary>实际被改动的字段数（Begin/End 各算 1；Both 算 2）。</summary>
    public int ChangedFields { get; init; }
    public int ChangedWords { get; init; }
    public override string ToString() => Ok ? $"OK 改了 {ChangedWords} 字 / {ChangedFields} 字段" : "拒绝：" + Reason;
}

/// <summary>
/// **±10ms 微调的纯逻辑**（批 1 核心；不依赖 UI，可单测）。
///
/// 边界规则**严格照后端契约**（已读 `edit_api.py` 源码，不是猜的）：
///  * `apply_doc` 保存时会跑 `normalize_timeline(policy="auto")` **就地自动修时间轴**，
///    所以本地必须**先保守拦截**，不要把明显非法的时间丢给服务端去"修"；
///  * `check_ttml(strict=True)` 只**记录结果、不阻断保存** ⇒ 不合法的时间**能存进去**，
///    因此拦截必须在客户端完成；
///  * 预检用 `POST /api/validate`（只算不写、返回修复后的 doc），**不自己发明规则**。
///
/// 另遵守 AGENTS.md **铁律 #13**：`span ⊆ p` —— 字的区间必须落在所属行内，
/// 所以微调时把"行的 begin/end"作为可选硬边界传进来（越界即拒绝，不悄悄改行）。
/// </summary>
public static class TimeNudge
{
    /// <summary>允许的最小字时长（1ms）：`begin < end` 是 TTML 硬要求，等号也不合法。</summary>
    public const double MinWordDuration = 0.001;

    /// <summary>时间轴上的绝对下限（负时间非法）。</summary>
    public const double MinTime = 0.0;

    /// <summary>
    /// 单个字微调。`lineBegin`/`lineEnd` 给了就作为**硬边界**（span ⊆ p）。
    /// 只在**全部合法**时才写入（失败不产生任何改动 ⇒ 不会留半完成状态）。
    /// </summary>
    public static NudgeOutcome ApplyOne(EditWord w, double deltaSec, NudgeTarget target,
                                       double? lineBegin = null, double? lineEnd = null)
    {
        if (w is null) return Bad("没有字");
        if (double.IsNaN(deltaSec) || double.IsInfinity(deltaSec)) return Bad("位移不是有效数字");
        if (deltaSec == 0) return new NudgeOutcome { Ok = true, ChangedFields = 0, ChangedWords = 0 };

        double b = w.Begin, e = w.End;
        double nb = b, ne = e;
        if (target is NudgeTarget.Begin or NudgeTarget.Both) nb = b + deltaSec;
        if (target is NudgeTarget.End or NudgeTarget.Both) ne = e + deltaSec;

        var why = Check(nb, ne, lineBegin, lineEnd);
        if (why is not null) return Bad(why);

        // 全部合法 → 才写入（原子）
        int fields = 0;
        if (target is NudgeTarget.Begin or NudgeTarget.Both) { w.Begin = nb; fields++; }
        if (target is NudgeTarget.End or NudgeTarget.Both) { w.End = ne; fields++; }
        return new NudgeOutcome { Ok = true, ChangedFields = fields, ChangedWords = 1 };
    }

    /// <summary>
    /// **批量**微调（区间，闭区间 `[from, to]`）。
    /// **全有或全无**：先逐个试算，任何一个不合法就整体拒绝、**一个字段都不改**
    /// （这样一次批量 = 一个事务、一个撤销单位，与批 4 的撤销粒度一致）。
    /// </summary>
    public static NudgeOutcome ApplyRange(IReadOnlyList<EditWord> words, int from, int to,
                                          double deltaSec, NudgeTarget target,
                                          double? lineBegin = null, double? lineEnd = null)
    {
        if (words is null || words.Count == 0) return Bad("这一行没有字");
        if (from < 0 || to < from || to >= words.Count) return Bad($"区间越界（{from}..{to}，共 {words.Count} 字）");
        if (deltaSec == 0) return new NudgeOutcome { Ok = true, ChangedFields = 0, ChangedWords = 0 };

        // ① 先全部试算（不改动）
        for (int i = from; i <= to; i++)
        {
            var w = words[i];
            double nb = w.Begin, ne = w.End;
            if (target is NudgeTarget.Begin or NudgeTarget.Both) nb += deltaSec;
            if (target is NudgeTarget.End or NudgeTarget.Both) ne += deltaSec;
            var why = Check(nb, ne, lineBegin, lineEnd);
            if (why is not null) return Bad($"第 {i + 1} 个字「{w.Text}」{why} ⇒ 整批未改动");
        }
        // ② 全部合法才写
        int fields = 0;
        for (int i = from; i <= to; i++)
        {
            var w = words[i];
            if (target is NudgeTarget.Begin or NudgeTarget.Both) { w.Begin += deltaSec; fields++; }
            if (target is NudgeTarget.End or NudgeTarget.Both) { w.End += deltaSec; fields++; }
        }
        return new NudgeOutcome { Ok = true, ChangedFields = fields, ChangedWords = to - from + 1 };
    }

    /// <summary>合法性检查；返回 null 表示合法，否则返回**给人看的原因**。</summary>
    private static string? Check(double begin, double end, double? lineBegin, double? lineEnd)
    {
        if (double.IsNaN(begin) || double.IsNaN(end)) return "时间不是有效数字";
        if (begin < MinTime) return $"开始时间会变成负值（{begin:0.000}s）";
        if (end - begin < MinWordDuration)
            return $"区间会变得非法（begin {begin:0.000}s ≥ end {end:0.000}s）";
        if (lineBegin is { } lb && begin < lb - 1e-9)
            return $"开始时间会跑到本行之前（{begin:0.000}s < 行首 {lb:0.000}s）";
        if (lineEnd is { } le && end > le + 1e-9)
            return $"结束时间会超出本行（{end:0.000}s > 行尾 {le:0.000}s）";
        return null;
    }

    private static NudgeOutcome Bad(string reason) => new() { Ok = false, Reason = reason };

    /// <summary>
    /// 批量偏移的**预检**（只算不写，用于界面"将改动 N 个字"的提示）。
    /// </summary>
    public static NudgeOutcome Preview(IReadOnlyList<EditWord> words, int from, int to,
                                       double deltaSec, NudgeTarget target,
                                       double? lineBegin = null, double? lineEnd = null)
    {
        if (words is null || words.Count == 0) return Bad("这一行没有字");
        if (from < 0 || to < from || to >= words.Count) return Bad($"区间越界（{from}..{to}）");
        for (int i = from; i <= to; i++)
        {
            var w = words[i];
            double nb = w.Begin, ne = w.End;
            if (target is NudgeTarget.Begin or NudgeTarget.Both) nb += deltaSec;
            if (target is NudgeTarget.End or NudgeTarget.Both) ne += deltaSec;
            var why = Check(nb, ne, lineBegin, lineEnd);
            if (why is not null) return Bad($"第 {i + 1} 个字「{w.Text}」{why}");
        }
        int fields = (target == NudgeTarget.Both ? 2 : 1) * (to - from + 1);
        return new NudgeOutcome { Ok = true, ChangedFields = fields, ChangedWords = to - from + 1 };
    }
}
