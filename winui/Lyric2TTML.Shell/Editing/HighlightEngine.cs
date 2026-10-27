using System;
using System.Collections.Generic;
using System.Linq;

namespace Lyric2TTML.Shell.Editing;

/// <summary>
/// 编辑台**纯逻辑**（不依赖 UI，可单测）。
///
/// 这些算法在 Web 版（`index.html`）里被**真实 bug 打磨过**，AGENTS.md 有完整记录。
/// 原生版必须保住同样的行为，所以先在这里用纯函数固定住，再写 XAML：
///
///  ① **状态优先级：插值 &gt; 低可信 &gt; 背景和声**
///     （真发生过：一个插值的和声字显示成"正常"，把最该人工核对的错误藏起来了）
///  ② **高亮不能改变盒子尺寸** —— Web 侧靠"只改颜色/不加粗"；原生侧用
///     `TextHighlighter` 背景 + `Run.Foreground`，天然不改布局 ✓
///  ③ **并行高亮**：同一时刻可能多个字在唱（v1 拖长音 + v2 已起唱），
///     按"每行取一个"返回，两个声部同时亮
///  ④ **咬字/拖音两阶段**：用该行的**中位字长**判定，超过部分算拖出来的音
///  ⑤ **`end` 不是有序的** ⇒ 必须用**后缀最大 end** 才能安全早退
///     （真 bug：`尽`1.8~3.0 排在 `等`2.4~2.8 前面，扫到 `等` 就 break，`尽` 永远不亮）
/// </summary>
public sealed class EditWord
{
    public string Text { get; set; } = "";
    public double Begin { get; set; }
    public double End { get; set; }
    public bool IsBg { get; set; }          // 背景和声（x-bg）
    // 后端给的分层标记：main / bg / translation（删字要按**层内下标**操作，见 TextDeletion）
    public string Layer { get; set; } = "";
    public bool Matched { get; set; } = true;  // false = 插值补出来的字
    public double? Confidence { get; set; } // 低可信（后端给的分数，可空）
}

public sealed class EditLine
{
    /// <summary>整行的 `<p begin/end>`（**后端给出**，不是从字推算）。
    /// 它是"微调不得越出本行"的边界来源（AGENTS.md 铁律 #13：`span ⊆ p`）。</summary>
    public double Begin { get; set; }
    public double End { get; set; }
    public string Agent { get; set; } = "v1";
    // ⚠️ `key` 的真实类型是**字符串**（如 "L1"，实测取样），不是数字 —— 按数字读会抛
    // `InvalidOperationException: requires an element of type 'Number'`。
    public string Key { get; set; } = "";
    public List<EditWord> Words { get; set; } = new();
}

public enum WordStatus
{
    Normal,
    Background,   // 背景和声
    LowConfidence,
    Interpolated, // 插值（人工补的/无证据）
}

public enum WordPhase
{
    None,
    Bite,  // 咬字
    Tail,  // 拖出来的音
}

public static class HighlightEngine
{
    /// <summary>低可信阈值：与 Web 版一致（后端 confidence 字段存在且低于它）。</summary>
    public const double LowConfidenceThreshold = 0.5;

    /// <summary>
    /// 字的状态。**优先级严格按 插值 &gt; 低可信 &gt; 背景和声**（警告必须压过"这是和声"）。
    /// </summary>
    public static WordStatus StatusOf(EditWord w)
    {
        if (w is null) throw new ArgumentNullException(nameof(w));
        if (!w.Matched) return WordStatus.Interpolated;
        if (w.Confidence is { } c && c < LowConfidenceThreshold) return WordStatus.LowConfidence;
        if (w.IsBg) return WordStatus.Background;
        return WordStatus.Normal;
    }

    /// <summary>
    /// 行的**中位字长**（用来判定"咬字 / 拖音"两阶段）。空行返回 0。
    /// 用中位数而不是平均：一行里有个超长拖音时，平均会被它拉偏。
    /// </summary>
    public static double MedianWordLength(EditLine line)
    {
        if (line?.Words is null || line.Words.Count == 0) return 0;
        var lens = line.Words.Select(w => w.End - w.Begin).Where(v => v > 0)
                             .OrderBy(v => v).ToList();
        if (lens.Count == 0) return 0;
        int mid = lens.Count / 2;
        return lens.Count % 2 == 1 ? lens[mid] : (lens[mid - 1] + lens[mid]) / 2.0;
    }

    /// <summary>
    /// 某个字在时刻 t 处于哪个阶段：咬字（不超过中位字长）还是拖音（超出部分）。
    /// t 不在该字区间内返回 <see cref="WordPhase.None"/>。
    /// </summary>
    public static WordPhase PhaseOf(EditWord w, double t, double lineMedian)
    {
        if (w is null || t < w.Begin || t >= w.End) return WordPhase.None;
        if (lineMedian <= 0) return WordPhase.Bite;      // 没参照就当咬字
        return (t - w.Begin) < lineMedian ? WordPhase.Bite : WordPhase.Tail;
    }

    /// <summary>
    /// 时刻 t 正在唱的字：**每行最多取一个**（主唱 + 和声各算一层时由调用方分行调用），
    /// 从而支持"并行高亮"（多个声部同时亮）。
    /// </summary>
    public static List<(int LineIndex, int WordIndex, WordPhase Phase)> WordsAt(
        IReadOnlyList<EditLine> lines, double t)
    {
        var hits = new List<(int, int, WordPhase)>();
        if (lines is null) return hits;
        for (int li = 0; li < lines.Count; li++)
        {
            var line = lines[li];
            if (line?.Words is null || line.Words.Count == 0) continue;
            double median = MedianWordLength(line);
            for (int wi = 0; wi < line.Words.Count; wi++)
            {
                var w = line.Words[wi];
                // 字唱完后高亮保持到同一层下一个字开始；最后一个字保持到本行 end。
                // 这样字与字之间的空隙不会让上一个字提前灭掉。
                double hold = w.End;
                double nextBegin = double.PositiveInfinity;
                for (int nj = 0; nj < line.Words.Count; nj++)
                {
                    var n = line.Words[nj];
                    if (n.IsBg == w.IsBg && n.Begin > w.Begin
                        && n.Begin < nextBegin)
                        nextBegin = n.Begin;
                }
                hold = Math.Max(hold, double.IsInfinity(nextBegin) ? line.End : nextBegin);
                if (t >= w.Begin && t < hold)
                {
                    var phase = t < w.End ? PhaseOf(w, t, median) : WordPhase.Bite;
                    hits.Add((li, wi, phase));
                    break;                                // 每行一个（并行靠多行实现）
                }
            }
        }
        return hits;
    }

    /// <summary>
    /// 每个下标 i 的**后缀最大 end**：`maxEnd[i] = max(end[i..])`。
    ///
    /// ⚠️ 为什么需要它：`wordIndex` 按 **begin** 排序，而 **end 不是有序的**。
    /// 只用"前一个字的 end"做提前退出，会漏掉后开始但更早结束的字
    /// （AGENTS.md 记录的真 bug：`尽`1.8~3.0 排在 `等`2.4~2.8 前面，`尽` 永远不亮）。
    /// </summary>
    public static double[] SuffixMaxEnd(IReadOnlyList<EditWord> words)
    {
        var arr = new double[words?.Count ?? 0];
        double run = double.NegativeInfinity;
        for (int i = arr.Length - 1; i >= 0; i--)
        {
            run = Math.Max(run, words![i].End);
            arr[i] = run;
        }
        return arr;
    }

    /// <summary>
    /// 在"按 begin 排序"的字表里找时刻 t 命中的字（用后缀最大 end 安全早退）。
    /// 返回第一个命中的下标，找不到返回 -1。
    /// </summary>
    public static int FindAt(IReadOnlyList<EditWord> sortedByBegin, double[] suffixMaxEnd, double t)
    {
        if (sortedByBegin is null || sortedByBegin.Count == 0) return -1;
        for (int i = 0; i < sortedByBegin.Count; i++)
        {
            if (sortedByBegin[i].Begin > t) return -1;    // 起点都在 t 之后 ⇒ 不可能命中
            if (suffixMaxEnd[i] <= t) return -1;          // 从 i 往后全结束 ⇒ 安全早退
            var w = sortedByBegin[i];
            if (t >= w.Begin && t < w.End) return i;
        }
        return -1;
    }
}
