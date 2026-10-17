using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using System.Linq;
using Lyric2TTML.Shell.Editing;
using Lyric2TTML.Shell.Backend;

namespace Lyric2TTML.Shell.Tests;

/// <summary>
/// 编辑台纯逻辑的回归测试。
/// 覆盖 AGENTS.md 里**被真实 bug 打磨过**的行为；每条都写明"为什么"，
/// 这样将来改算法时能一眼看出是否踩了老坑。
/// </summary>
internal static class Program
{
    private static int _pass, _fail;

    private static void Check(string name, bool ok, string detail = "")
    {
        if (ok) { _pass++; Console.WriteLine($"  [PASS] {name}"); }
        else { _fail++; Console.WriteLine($"  [FAIL] {name}" + (detail.Length > 0 ? $"\n         {detail}" : "")); }
    }

    private static void Eq<T>(string name, T got, T want) =>
        Check(name, EqualityComparer<T>.Default.Equals(got, want), $"期望 {want}，实际 {got}");

    private static EditWord W(string text, double b, double e, bool bg = false,
                              bool matched = true, double? conf = null) =>
        new() { Text = text, Begin = b, End = e, IsBg = bg, Matched = matched, Confidence = conf };

    public static int Main()
    {
        Console.WriteLine("============================================================");
        Console.WriteLine("  编辑台纯逻辑回归（HighlightEngine）");
        Console.WriteLine("============================================================");

        // ── ① 状态优先级：插值 > 低可信 > 背景和声 ─────────────────────────
        Console.WriteLine("\n[1] 状态优先级（警告必须压过「这是和声」，否则会把最该看的错误藏起来）");
        Eq("普通字 → Normal", HighlightEngine.StatusOf(W("啊", 0, 1)), WordStatus.Normal);
        Eq("和声字 → Background", HighlightEngine.StatusOf(W("啊", 0, 1, bg: true)), WordStatus.Background);
        Eq("低可信 → LowConfidence",
           HighlightEngine.StatusOf(W("啊", 0, 1, conf: 0.31)), WordStatus.LowConfidence);
        Eq("插值 → Interpolated",
           HighlightEngine.StatusOf(W("啊", 0, 1, matched: false)), WordStatus.Interpolated);
        Eq("插值+和声 → Interpolated（插值优先）",
           HighlightEngine.StatusOf(W("啊", 0, 1, bg: true, matched: false)), WordStatus.Interpolated);
        Eq("插值+低可信 → Interpolated（插值优先）",
           HighlightEngine.StatusOf(W("啊", 0, 1, matched: false, conf: 0.1)), WordStatus.Interpolated);
        Eq("低可信+和声 → LowConfidence（低可信优先于和声）",
           HighlightEngine.StatusOf(W("啊", 0, 1, bg: true, conf: 0.2)), WordStatus.LowConfidence);
        Eq("恰好等于阈值 0.5 → 不算低可信",
           HighlightEngine.StatusOf(W("啊", 0, 1, conf: 0.5)), WordStatus.Normal);

        // ── ② 中位字长（两阶段判据）────────────────────────────────────────
        Console.WriteLine("\n[2] 行内中位字长（用中位数而非平均：一行里一个超长拖音会把平均拉偏）");
        Eq("空行 → 0", HighlightEngine.MedianWordLength(new EditLine()), 0.0);
        var l3 = new EditLine { Words = { W("a", 0, 1), W("b", 1, 2), W("c", 2, 3) } };
        Eq("三个 1s 字 → 1.0", HighlightEngine.MedianWordLength(l3), 1.0);
        var l4 = new EditLine { Words = { W("a", 0, 1), W("b", 1, 2), W("c", 2, 4), W("d", 4, 8) } };
        Eq("偶数个 → 取中间两个的平均 (1+2)/2=1.5", HighlightEngine.MedianWordLength(l4), 1.5);
        var lTail = new EditLine { Words = { W("a", 0, 1), W("b", 1, 2), W("c", 2, 10) } };
        Eq("字长 [1,1,8] 的中位数是 1（不被超长拖音拉偏 —— 平均值会是 3.33）",
           HighlightEngine.MedianWordLength(lTail), 1.0);

        // ── ③ 咬字 / 拖音两阶段 ────────────────────────────────────────────
        Console.WriteLine("\n[3] 咬字 / 拖音两阶段（超过中位字长的部分算拖出来的音）");
        var w = W("尽", 1.8, 3.0);
        Eq("区间外 → None", HighlightEngine.PhaseOf(w, 1.0, 0.4), WordPhase.None);
        Eq("起点 → Bite", HighlightEngine.PhaseOf(w, 1.8, 0.4), WordPhase.Bite);
        Eq("中位以内 → Bite", HighlightEngine.PhaseOf(w, 2.19, 0.4), WordPhase.Bite);
        Eq("超过中位 → Tail", HighlightEngine.PhaseOf(w, 2.2, 0.4), WordPhase.Tail);
        Eq("末端仍算命中（t < end）", HighlightEngine.PhaseOf(w, 2.99, 0.4), WordPhase.Tail);
        Eq("无参照时算 Bite", HighlightEngine.PhaseOf(w, 2.9, 0.0), WordPhase.Bite);

        // ── ④ 并行高亮：同一时刻多行同时亮（v1 拖长音 + v2 已起唱）──────────
        Console.WriteLine("\n[4] 并行高亮（每行取一个 ⇒ 多个声部同时亮）");
        var lines = new List<EditLine>
        {
            new() { Agent = "v1", Words = { W("尽", 1.8, 3.0) } },          // v1 在拖音
            new() { Agent = "v2", Words = { W("我", 2.4, 2.8) } },          // v2 已起唱
            new() { Agent = "v2", Words = { W("们", 5.0, 5.4) } },
        };
        var at24 = HighlightEngine.WordsAt(lines, 2.4);
        Eq("t=2.4 命中 2 行（并行）", at24.Count, 2);
        Check("包含 v1 的第 0 行", at24.Any(h => h.LineIndex == 0));
        Check("包含 v2 的第 1 行", at24.Any(h => h.LineIndex == 1));
        Check("v2 那一刻是咬字", at24.Any(h => h.LineIndex == 1 && h.Phase == WordPhase.Bite));
        Eq("t=4.0 什么都不在唱", HighlightEngine.WordsAt(lines, 4.0).Count, 0);

        // ── ⑤ 后缀最大 end（AGENTS.md 记录的真 bug）────────────────────────
        Console.WriteLine("\n[5] 后缀最大 end：end 无序时不能「扫到早结束的就退出」");
        // 真 bug 复现：按 begin 排序后 `尽`(1.8~3.0) 在 `等`(2.4~2.8) 之前，
        // 若用"前一个的 end"判断，扫到 `等` 就 break ⇒ `尽` 永远不亮。
        var byBegin = new List<EditWord> { W("尽", 1.8, 3.0), W("等", 2.4, 2.8) };
        var smax = HighlightEngine.SuffixMaxEnd(byBegin);
        Check("后缀最大 end[0] = 3.0", Math.Abs(smax[0] - 3.0) < 1e-9, $"实际 {smax[0]}");
        Check("后缀最大 end[1] = 2.8", Math.Abs(smax[1] - 2.8) < 1e-9, $"实际 {smax[1]}");
        Eq("t=2.9 必须命中 `尽`（旧逻辑会漏）", HighlightEngine.FindAt(byBegin, smax, 2.9), 0);
        Eq("t=2.5 命中第一个区间包含它的字", HighlightEngine.FindAt(byBegin, smax, 2.5), 0);
        Eq("t=2.85 命中 `尽`", HighlightEngine.FindAt(byBegin, smax, 2.85), 0);
        Eq("t=3.1 全结束 → -1", HighlightEngine.FindAt(byBegin, smax, 3.1), -1);
        Eq("t=1.0 起点都在后 → -1", HighlightEngine.FindAt(byBegin, smax, 1.0), -1);

        // 阴性对照（**必须真的能区分**，否则这条用例是假绿 —— AGENTS.md 纪律）：
        //   A(1.0~2.0) 先开始、已结束；B(1.5~3.0) 后开始、t=2.5 仍在唱。
        //   朴素实现「遇到 end <= t 就 break」会在 A 处退出 ⇒ **漏掉 B**；
        //   用后缀最大 end 则 smax[0]=3.0 > 2.5 ⇒ 继续扫 ⇒ 命中 B ✓
        var z = new List<EditWord> { W("A", 1.0, 2.0), W("B", 1.5, 3.0) };
        var zmax = HighlightEngine.SuffixMaxEnd(z);
        Eq("后缀方案：t=2.5 命中 B（下标 1）", HighlightEngine.FindAt(z, zmax, 2.5), 1);

        int naiveHit = -1;                              // 朴素的「只看当前字 end」实现
        for (int i = 0; i < z.Count; i++)
        {
            if (z[i].End <= 2.5) break;                 // ← 这就是旧实现的 bug
            if (2.5 >= z[i].Begin && 2.5 < z[i].End) { naiveHit = i; break; }
        }
        Check("阴性对照：朴素早退确实漏掉 B（证明这条用例有效）", naiveHit == -1,
              $"朴素实现命中了 {naiveHit}");

        // ── ⑥ ±10ms 微调（TimeNudge，批 1 核心）────────────────────────────
        Console.WriteLine("\n[6] ±10ms 微调：边界照后端契约 + 铁律#13（span ⊆ p）");

        // 单字 +10ms（改 begin）
        var n1 = W("热", 10.000, 10.200);
        var r1 = TimeNudge.ApplyOne(n1, +0.010, NudgeTarget.Begin);
        Check("+10ms 改 begin → 通过", r1.Ok, r1.Reason);
        Check("begin 变成 10.010", Math.Abs(n1.Begin - 10.010) < 1e-9, $"实际 {n1.Begin}");
        Check("end 不动", Math.Abs(n1.End - 10.200) < 1e-9, $"实际 {n1.End}");

        // −10ms 到负值 → 拒绝且**一个字段都不改**
        var n2 = W("热", 0.005, 0.200);
        var r2 = TimeNudge.ApplyOne(n2, -0.010, NudgeTarget.Begin);
        Check("−10ms 会变负值 → 拒绝", !r2.Ok, r2.Reason);
        Check("拒绝时未改动（原子）", Math.Abs(n2.Begin - 0.005) < 1e-9, $"实际 {n2.Begin}");

        // End 让 begin ≥ end → 拒绝
        var n3 = W("热", 10.000, 10.010);
        var r3 = TimeNudge.ApplyOne(n3, -0.010, NudgeTarget.End);
        Check("改 end 使 begin ≥ end → 拒绝", !r3.Ok, r3.Reason);

        // Both 保持时长
        var n4 = W("热", 10.000, 10.250);
        var r4 = TimeNudge.ApplyOne(n4, +0.010, NudgeTarget.Both);
        Check("Both → 通过", r4.Ok, r4.Reason);
        Check("Both 保持时长 0.250", Math.Abs((n4.End - n4.Begin) - 0.250) < 1e-9,
              $"{n4.End - n4.Begin}");
        Eq("Both 改动 2 个字段", r4.ChangedFields, 2);

        // 行边界（span ⊆ p）：超出本行 → 拒绝
        var n5 = W("热", 10.000, 10.200);
        var r5 = TimeNudge.ApplyOne(n5, +0.100, NudgeTarget.End, 10.000, 10.250);
        Check("end 超出本行行尾 → 拒绝（铁律#13）", !r5.Ok, r5.Reason);
        Check("拒绝时 end 未变", Math.Abs(n5.End - 10.200) < 1e-9, $"实际 {n5.End}");

        // 0ms → 通过但零改动
        var n6 = W("热", 10.000, 10.200);
        var r6 = TimeNudge.ApplyOne(n6, 0.0, NudgeTarget.Both);
        Check("0ms → 通过", r6.Ok, r6.Reason);
        Eq("0ms 改动字段数 = 0", r6.ChangedFields, 0);

        // 批量：区间越界 → 拒绝
        var batch = new List<EditWord> { W("a", 1.0, 1.2), W("b", 1.2, 1.4), W("c", 1.4, 1.6) };
        var badR = TimeNudge.ApplyRange(batch, 0, 5, +0.01, NudgeTarget.Begin);
        Check("批量区间越界 → 拒绝", !badR.Ok, badR.Reason);

        // 批量**原子性**（关键）：区间里最后一个字会非法 ⇒ 整批一个都不改
        var atom = new List<EditWord> { W("a", 1.000, 1.200), W("b", 1.200, 1.400), W("c", 0.005, 0.050) };
        var atomR = TimeNudge.ApplyRange(atom, 0, 2, -0.010, NudgeTarget.Begin);
        Check("批量含非法项 → 整体拒绝", !atomR.Ok, atomR.Reason);
        Check("原子性：第 1 字未被改", Math.Abs(atom[0].Begin - 1.000) < 1e-9, $"实际 {atom[0].Begin}");
        Check("原子性：第 2 字未被改", Math.Abs(atom[1].Begin - 1.200) < 1e-9, $"实际 {atom[1].Begin}");
        Check("原子性：第 3 字未被改", Math.Abs(atom[2].Begin - 0.005) < 1e-9, $"实际 {atom[2].Begin}");

        // 批量全部合法 → 全改
        var okBatch = new List<EditWord> { W("a", 1.000, 1.200), W("b", 1.200, 1.400) };
        var okR = TimeNudge.ApplyRange(okBatch, 0, 1, +0.010, NudgeTarget.Begin);
        Check("批量合法 → 通过", okR.Ok, okR.Reason);
        Eq("批量改了 2 个字", okR.ChangedWords, 2);
        Check("第 1 字 +10ms", Math.Abs(okBatch[0].Begin - 1.010) < 1e-9, $"实际 {okBatch[0].Begin}");
        Check("第 2 字 +10ms", Math.Abs(okBatch[1].Begin - 1.210) < 1e-9, $"实际 {okBatch[1].Begin}");

        // Preview 只算不写
        var prev = new List<EditWord> { W("a", 1.000, 1.200) };
        var pv = TimeNudge.Preview(prev, 0, 0, +0.010, NudgeTarget.Both);
        Check("Preview → 通过", pv.Ok, pv.Reason);
        Check("Preview 不写文档", Math.Abs(prev[0].Begin - 1.000) < 1e-9, $"实际 {prev[0].Begin}");

        // ── ⑦ 选中模型（批 1：属性面板地基）────────────────────────────────
        Console.WriteLine("\n[7] 选中模型：切选中不吞脏修改 + 结构变化后光标不失效");
        var selL1 = new EditLine { Key = "L1", Agent = "v1", Words = { W("热", 1.0, 1.2), W("爱", 1.2, 1.4) } };
        var selL2 = new EditLine { Key = "L2", Agent = "v2", Words = { W("曾", 2.0, 2.2) } };
        var selL3 = new EditLine { Key = "L3", Agent = "v1", Words = { W("是", 3.0, 3.2) } };
        var docLines = new List<EditLine> { selL1, selL2, selL3 };
        var sel = new EditSelection();

        Eq("初始为未选中", sel.Kind, SelKind.None);
        Check("未选中时 Describe 可读", sel.Describe(docLines).Length > 0);

        sel.SelectWord(1, 0, docLines);
        Eq("选中字 → Word", sel.Kind, SelKind.Word);
        Eq("行下标 = 1", sel.LineIndex, 1);
        Eq("字下标 = 0", sel.WordIndex, 0);
        Check("记住稳定 key L2", sel.LineKey == "L2", sel.LineKey);
        Check("Describe 带字与时间", sel.Describe(docLines).Contains("曾") &&
                                     sel.Describe(docLines).Contains("2.000"), sel.Describe(docLines));

        // ⭐ 要求①：改了东西后**切换选中对象**，脏标记必须还在
        sel.MarkDirty();
        Check("脏 = 需要先问", !sel.MaySwitchWithoutAsking);
        sel.SelectLine(2, docLines);
        Eq("切到行 → Kind=Line", sel.Kind, SelKind.Line);
        Check("切选中**没有吞掉**未保存修改（IsDirty 仍为真）", sel.IsDirty);

        sel.MarkClean();
        Check("保存后可以自由切换", sel.MaySwitchWithoutAsking);

        // ⭐ 要求②：结构变化后按 key 找回（行顺序被调换）
        sel.SelectWord(2, 0, docLines);                       // 指向 L3
        var reordered = new List<EditLine> { selL3, selL1, selL2 };    // L3 跑到最前
        bool moved = sel.ClampTo(reordered);
        Check("结构变化被检测到", moved);
        Eq("按 key 找回：仍指向 L3（新下标 0）", sel.LineIndex, 0);
        Check("key 仍是 L3", sel.LineKey == "L3", sel.LineKey);

        // 删行（原来指向的行没了）→ 落到有效位置，不崩
        sel.SelectWord(2, 0, docLines);                       // L3
        var shrunk = new List<EditLine> { selL1, selL2 };           // L3 被删
        var moved2 = sel.ClampTo(shrunk);
        Check("行被删 → 位置被调整", moved2);
        Check("落点仍在有效范围", sel.LineIndex >= 0 && sel.LineIndex < shrunk.Count,
              $"LineIndex={sel.LineIndex}");
        Check("且选中对象存在", sel.LineIndex < shrunk.Count && sel.WordIndex < shrunk[sel.LineIndex].Words.Count,
              $"WordIndex={sel.WordIndex}");

        // 字越界 → 落到该行末字
        sel.SelectWord(0, 99, docLines);
        Eq("字越界被夹到末字", sel.WordIndex, 1);

        // 空文档 → 安全降级为 None，不崩
        sel.SelectWord(0, 0, docLines);
        var empty = new List<EditLine>();
        sel.ClampTo(empty);
        Eq("空文档 → None", sel.Kind, SelKind.None);

        // 空行（没有字）→ 降级为选行
        var emptyLine = new List<EditLine> { new EditLine { Key = "L1", Agent = "v1" } };
        sel.SelectWord(0, 0, emptyLine);
        Eq("空行选中 → Line", sel.Kind, SelKind.Line);


        // ── ⑧ 统一编辑事务（EditSession，批 1 地基）────────────────────────
        Console.WriteLine("\n[8] 编辑事务：失败回滚 / 必带 version / 不静默自动修复");
        var fx = new FakeBackend();
        var sess = new EditSession("t1", 3, "{\"version\":3,\"lines\":[]}", fx);

        var c1 = fx.Run(sess, d => { d["lines"] = new System.Text.Json.Nodes.JsonArray(); return true; });
        Check("成功保存", c1.Ok, c1.Reason);
        Eq("版本前进到 4", sess.Version, 4);
        Eq("返回结果里的版本也是 4", c1.Version, 4);
        Check("保存后不脏", !sess.IsDirty);
        Eq("确实调用了保存", fx.SaveCalls, 1);
        Eq("保存时带的 version", fx.LastVersion, 3);

        var v0 = sess.Version;
        var c2 = fx.Run(sess, _ => throw new InvalidOperationException("模拟改动出错"));
        Check("改动抛错 → 失败", !c2.Ok, c2.Reason);
        Eq("版本没变", sess.Version, v0);
        Check("失败后不留脏标记", !sess.IsDirty);
        Eq("抛错时没有发保存请求", fx.SaveCalls, 1);

        var c3 = fx.Run(sess, _ => false);
        Check("无改动 → 标记 NoChange", c3.NoChange);
        Eq("无改动不发保存", fx.SaveCalls, 1);

        fx.FailValidate = true;
        var c4 = fx.Run(sess, d => { d["lines"] = new System.Text.Json.Nodes.JsonArray(); return true; });
        Check("预检失败 → 失败", !c4.Ok, c4.Reason);
        Eq("预检失败不发保存", fx.SaveCalls, 1);
        Check("预检失败后不脏", !sess.IsDirty);
        fx.FailValidate = false;

        fx.ConflictNext = true;
        var c5 = fx.Run(sess, d => { d["lines"] = new System.Text.Json.Nodes.JsonArray(); return true; });
        Check("409 → 标记冲突", c5.Conflict);
        Eq("冲突后版本不变", sess.Version, v0);
        Eq("冲突不重试保存", fx.SaveCalls, 2);
        Check("冲突后**保留本地修改**（不丢弃用户刚改的）", sess.IsDirty);
        fx.ConflictNext = false;

        fx.FixCount = 2;
        var c6 = fx.Run(sess, d => { d["lines"] = new System.Text.Json.Nodes.JsonArray(); return true; });
        Check("自动修复被如实报告", c6.Ok && c6.FixCount == 2 && c6.Reason.Contains("修复"), c6.Reason);
        fx.FixCount = 0;

        var before8 = sess.Version;
        fx.Run(sess, d => { d["lines"] = new System.Text.Json.Nodes.JsonArray(); return true; });
        fx.Run(sess, d => { d["lines"] = new System.Text.Json.Nodes.JsonArray(); return true; });
        Eq("两次事务后版本 +2", sess.Version, before8 + 2);

        // ── ⑨ 文本/和声操作事务（批 2：必须走 /api/text）──────────────────
        Console.WriteLine("\n[9] 文本/和声操作：op 名正确 / 409 保留脏 / 失败不变版本");
        var fx9 = new FakeBackend();
        var s9 = new EditSession("t2", 7, "{\"version\":7,\"lines\":[]}", fx9);

        var payload = new System.Text.Json.Nodes.JsonObject
        {
            ["key"] = "L1", ["layer"] = "bg", ["text"] = "一路践行",
        };
        var r9 = fx9.RunOp(s9, "set_bg", payload);
        Check("set_bg 成功", r9.Ok, r9.Reason);
        Eq("传下去的 op 名是 set_bg（与后端一致）", fx9.LastOp, "set_bg");
        Check("payload 里带 key/layer/text",
              fx9.LastPayload?["key"]?.ToString() == "L1" &&
              fx9.LastPayload?["layer"]?.ToString() == "bg" &&
              fx9.LastPayload?["text"]?.ToString() == "一路践行");
        Eq("版本前进到 8", s9.Version, 8);
        Check("成功后不脏", !s9.IsDirty);

        // 空 op 不发请求
        var before9 = fx9.SaveCalls;
        var r9b = fx9.RunOp(s9, "", new System.Text.Json.Nodes.JsonObject());
        Check("空 op → 拒绝", !r9b.Ok, r9b.Reason);
        Eq("空 op 不发请求", fx9.SaveCalls, before9);

        // 409 → 冲突 + 保留脏 + 版本不变
        fx9.ConflictNext = true;
        var v9 = s9.Version;
        var r9c = fx9.RunOp(s9, "remove_bg", new System.Text.Json.Nodes.JsonObject { ["key"] = "L1" });
        Check("remove_bg 冲突被标记", r9c.Conflict);
        Eq("冲突后版本不变", s9.Version, v9);
        Check("冲突后保留改动意图（脏）", s9.IsDirty);
        Check("冲突时传的 op 是 remove_bg", fx9.LastOp == "remove_bg");
        fx9.ConflictNext = false;

        // ── 汇总 ───────────────────────────────────────────────────────────
        Console.WriteLine();
        Console.WriteLine("============================================================");
        // ── ⑩ 模型/运行库 JSON 解析 + 外壳设置（阶段 4 新增页面）─────────────
        Console.WriteLine("\n[10] 模型/运行库解析（失败必须抛错，不许空列表冒充正常）");
        const string modelsJson = """
        {"hf_cache":"C:/c","manifest":"C:/m.json","total":2,"ready":1,"missing":1,
         "models":[
          {"id":"whisper-large-v3","name":"Whisper large-v3","category":"asr","purpose":"p",
           "note":"n","default":true,"license":"MIT","status":"ready","status_label":"已就绪",
           "detail":"文件齐全","location":"C:/c/x","approx_size":3200000000,
           "present_bytes":3090835702,"missing":[],"size_mismatch":[],"can_download":false},
          {"id":"whisper-medium","name":"Whisper medium","category":"asr","purpose":"p",
           "note":"","default":false,"license":"MIT","status":"not_installed","status_label":"未安装",
           "detail":"缺少 2 个文件","location":"C:/c/y","approx_size":1600000000,
           "present_bytes":0,"missing":["a","b"],"size_mismatch":[],"can_download":true}
         ]}
        """;
        var cat = Management.ParseModels(modelsJson);
        Eq("模型总数", cat.Total, 2);
        Eq("就绪数", cat.Ready, 1);
        Eq("缺失数", cat.Missing, 1);
        Eq("首个模型 id", cat.Models[0].Id, "whisper-large-v3");
        Check("默认模型标记", cat.Models[0].IsDefault);
        Check("就绪模型不可再下载", !cat.Models[0].CanDownload);
        Check("缺失模型可下载", cat.Models[1].CanDownload);
        Check("缺失文件列表能识别", cat.Models[1].MissingFiles);
        Eq("字节数解析", cat.Models[0].ApproxSize, 3200000000L);
        try
        {
            Management.ParseModels("not json");
            Check("非法 JSON 必须抛错", false, "居然没抛");
        }
        catch (Exception ex) when (ex is InvalidOperationException)
        {
            Check("非法 JSON 必须抛错", true);
        }
        try
        {
            Management.ParseModels("");
            Check("空输出必须抛错", false, "居然没抛");
        }
        catch (Exception ex) when (ex is InvalidOperationException)
        {
            Check("空输出必须抛错", true);
        }

        Console.WriteLine("\n[11] 运行库/Worker 解析");
        const string rtJson = """
        {"root":"C:/rt","runtimes":[
          {"id":"torch","name":"PyTorch","purpose":"p","status":"ready","status_label":"已就绪",
           "detail":"torch 2.14 cpu","location":"C:/rt/torch","download_bytes":124110781,
           "present_bytes":475961509,"can_download":true},
          {"id":"cuda","name":"CUDA","purpose":"p","status":"not_installed","status_label":"未安装",
           "detail":"缺文件","location":"C:/rt/cuda","download_bytes":553162896,
           "present_bytes":0,"can_download":true}]}
        """;
        var rt = Management.ParseRuntimes(rtJson);
        Eq("运行库根目录", rt.Root, "C:/rt");
        Eq("运行库个数", rt.Runtimes.Count, 2);
        Eq("torch 状态", rt.Runtimes[0].Status, "ready");
        Eq("cuda 已占用", rt.Runtimes[1].PresentBytes, 0L);
        const string workerJson = """
        {"state":"ready","detail":"已安装","progress":1.0,"exe":"C:/w/x.exe",
         "want_version":"0.1.0","sources_ready":true,"sources_reason":""}
        """;
        var wk = Management.ParseWorker(workerJson);
        Check("worker ready 判定", wk.Ready);
        Eq("worker 版本", wk.WantVersion, "0.1.0");
        Eq("Human(0) 显示 —", Management.Human(0), "—");
        Eq("Human(1024) 显示 1 KB", Management.Human(1024), "1 KB");
        Eq("Human(3.2GB)", Management.Human(3200000000L), "3 GB");

        Console.WriteLine("\n[12] 外壳设置：往返 / 损坏文件 / 缺文件");
        var tmp = System.IO.Path.Combine(AppContext.BaseDirectory,
                                         "l2t-settings-" + Guid.NewGuid().ToString("N") + ".json");
        var s1 = new ShellSettings
        {
            Model = "medium", Language = "ja",
            SingerMode = "3", KeepTails = false, VerifyAmll = false,
            SameAgentOverlap = true, Theme = 2,
        };
        Eq("保存成功", s1.Save(tmp), "");
        var s2 = ShellSettings.Load(out var e1, tmp);
        Eq("往返 model", s2.Model, "medium");
        Eq("往返 singer", s2.SingerMode, "3");
        Eq("往返 theme", s2.Theme, 2);
        Check("往返布尔", s2.SameAgentOverlap && !s2.KeepTails);
        Eq("正常读取无错误", e1, "");
        System.IO.File.WriteAllText(tmp, "{ broken json");
        var s3 = ShellSettings.Load(out var e2, tmp);
        Check("损坏文件 → 默认值", s3.Model == "large-v3" && s3.Theme == 0);
        Check("损坏文件 → 有错误信息", e2.Length > 0, e2);
        System.IO.File.Delete(tmp);
        var s4 = ShellSettings.Load(out var e3, tmp);
        Check("缺文件 → 默认值且无错误", s4.Model == "large-v3" && e3 == "");

        Console.WriteLine($"  通过 {_pass} / {_pass + _fail}" + (_fail == 0 ? "  ✅ 全部通过" : $"  ❌ 失败 {_fail}"));
        Console.WriteLine("============================================================");
        return _fail == 0 ? 0 : 1;
    }
}

/// <summary>单测用的假后端：只验证**事务逻辑**，不碰网络（真实现走 HTTP）。</summary>
internal sealed class FakeBackend : IEditBackend
{
    public int SaveCalls;
    public int LastVersion = -1;
    public bool FailValidate;
    public bool ConflictNext;
    public int FixCount;

    public Task<ValidateResult> ValidateAsync(string docJson, CancellationToken ct = default) =>
        Task.FromResult(FailValidate
            ? new ValidateResult { Ok = false, Detail = "模拟预检失败" }
            : new ValidateResult { Ok = true, DocJson = docJson, FixCount = FixCount });

    public Task<SaveResult> SaveAsync(string taskId, int version, string docJson, CancellationToken ct = default)
    {
        SaveCalls++;
        LastVersion = version;
        if (ConflictNext)
            return Task.FromResult(new SaveResult { Ok = false, Conflict = true, Detail = "版本冲突：文档已被改动" });
        return Task.FromResult(new SaveResult
        {
            Ok = true, DocJson = docJson, Version = version + 1, FixCount = FixCount,
        });
    }

    public string? LastOp;
    public System.Text.Json.Nodes.JsonObject? LastPayload;

    public Task<SaveResult> TextOpAsync(string taskId, int version, string op,
                                        System.Text.Json.Nodes.JsonObject payload,
                                        CancellationToken ct = default)
    {
        LastOp = op; LastPayload = payload; SaveCalls++; LastVersion = version;
        if (ConflictNext)
            return Task.FromResult(new SaveResult { Ok = false, Conflict = true, Detail = "版本冲突：文档已被改动" });
        return Task.FromResult(new SaveResult
        {
            Ok = true, DocJson = "{\"version\":" + (version + 1) + ",\"lines\":[]}",
            Version = version + 1, FixCount = FixCount,
        });
    }

    /// <summary>文本/和声操作的同步包装（批 2）。</summary>
    public CommitResult RunOp(EditSession session, string op, System.Text.Json.Nodes.JsonObject payload) =>
        session.CommitOpAsync(op, payload).GetAwaiter().GetResult();

    /// <summary>同步包装，便于在控制台测试里顺序断言。</summary>
    public CommitResult Run(EditSession session, Func<System.Text.Json.Nodes.JsonNode, bool> mutate) =>
        session.CommitAsync(mutate).GetAwaiter().GetResult();
}

