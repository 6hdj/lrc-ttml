using System;
using System.Collections.Generic;
using System.Linq;

namespace Lyric2TTML.Shell.Editing;

/// <summary>选中对象的层级（属性面板按这个切换要编辑什么）。</summary>
public enum SelKind { None, Word, Line, Doc }

/// <summary>
/// **选中模型**（批 1 的"选中即编辑属性面板"地基；纯逻辑，可单测）。
///
/// 两条来自用户的**强制要求**：
///  ① **切换选中对象时不能把尚未保存的修改错误地覆盖掉** ⇒ 本类只**记录脏标记**，
///    绝不因为"换选中"而丢弃修改；是否保存由调用方（UI）通过
///    <see cref="MaySwitchWithoutAsking"/> 判断：脏了就**先问或先提交**，不许静默丢弃。
///  ② **结构编辑后光标/选中不能指向已删除或已替换的对象** ⇒ <see cref="ClampTo"/>：
///     优先按 **`line.Key`（如 "L1"，稳定标识）** 重新定位；找不到才退回同一下标，
///     再不行就退到最后一个有效位置；**空文档也能安全返回 None 而不崩**。
/// </summary>
public sealed class EditSelection
{
    public SelKind Kind { get; private set; } = SelKind.None;
    public int LineIndex { get; private set; } = -1;
    public int WordIndex { get; private set; } = -1;
    /// <summary>选中行的稳定标识（`EditLine.Key`，如 "L1"）；结构变化后靠它找回。</summary>
    public string LineKey { get; private set; } = "";

    /// <summary>是否有**尚未保存**的修改（切换选中时用它决定是否要问用户）。</summary>
    public bool IsDirty { get; private set; }

    /// <summary>脏的时候**不要**直接换选中对象 —— 先保存或先问（要求①）。</summary>
    public bool MaySwitchWithoutAsking => !IsDirty;

    public void MarkDirty() => IsDirty = true;
    public void MarkClean() => IsDirty = false;

    public void SelectDoc()
    {
        Kind = SelKind.Doc;
        LineIndex = -1; WordIndex = -1; LineKey = "";
    }

    public void SelectLine(int lineIndex, IReadOnlyList<EditLine> lines)
    {
        if (lines is null || lineIndex < 0 || lineIndex >= lines.Count) { SelectDoc(); return; }
        Kind = SelKind.Line;
        LineIndex = lineIndex;
        LineKey = lines[lineIndex].Key ?? "";
        WordIndex = -1;
    }

    public void SelectWord(int lineIndex, int wordIndex, IReadOnlyList<EditLine> lines)
    {
        if (lines is null || lineIndex < 0 || lineIndex >= lines.Count) { SelectDoc(); return; }
        var line = lines[lineIndex];
        if (line.Words.Count == 0) { SelectLine(lineIndex, lines); return; }
        Kind = SelKind.Word;
        LineIndex = lineIndex;
        LineKey = line.Key ?? "";
        WordIndex = Math.Clamp(wordIndex, 0, line.Words.Count - 1);
    }

    /// <summary>当前坐标（无效时返回 (-1,-1)）。</summary>
    public (int Line, int Word) Current => (LineIndex, WordIndex);

    /// <summary>面板标题用的描述（给用户看"现在在编辑什么"）。</summary>
    public string Describe(IReadOnlyList<EditLine> lines)
    {
        switch (Kind)
        {
            case SelKind.Doc: return "整份文档（元数据）";
            case SelKind.Line:
                return LineIndex >= 0 && LineIndex < lines.Count
                    ? $"第 {LineIndex + 1} 行 {lines[LineIndex].Key} · {lines[LineIndex].Agent}"
                    : "第 ? 行";
            case SelKind.Word:
                if (LineIndex >= 0 && LineIndex < lines.Count)
                {
                    var w = lines[LineIndex].Words;
                    if (WordIndex >= 0 && WordIndex < w.Count)
                        return $"字「{w[WordIndex].Text}」（第 {LineIndex + 1} 行 第 {WordIndex + 1} 字）" +
                               $" {w[WordIndex].Begin:0.000}~{w[WordIndex].End:0.000}s";
                }
                return "字 ?";
            default: return "未选中";
        }
    }

    /// <summary>
    /// **结构变化（插行/删行/合并/改字）之后校正选中**（要求②）。返回 true 表示位置被调整过。
    /// 顺序：按 `LineKey` 找回 → 同下标 → 最后一个有效位置 → None（空文档）。
    /// </summary>
    public bool ClampTo(IReadOnlyList<EditLine> lines)
    {
        var before = (Kind, LineIndex, WordIndex, LineKey);
        if (Kind == SelKind.None || Kind == SelKind.Doc) return false;

        if (lines is null || lines.Count == 0)
        {
            Kind = SelKind.None; LineIndex = -1; WordIndex = -1; LineKey = "";
            return true;
        }

        int li = -1;
        if (!string.IsNullOrEmpty(LineKey))
            li = FindIndexByKey(lines, LineKey);          // ① 稳定标识优先
        if (li < 0 && LineIndex >= 0 && LineIndex < lines.Count)
            li = LineIndex;                               // ② 同下标
        if (li < 0) li = lines.Count - 1;                 // ③ 最后一个有效行

        LineIndex = li;
        LineKey = lines[li].Key ?? "";
        if (Kind == SelKind.Word)
        {
            var words = lines[li].Words;
            if (words.Count == 0) Kind = SelKind.Line;    // 字全没了 → 降级为选行
            else WordIndex = Math.Clamp(WordIndex, 0, words.Count - 1);
        }
        return (Kind, LineIndex, WordIndex, LineKey) != before;
    }

    private static int FindIndexByKey(IReadOnlyList<EditLine> lines, string key)
    {
        for (int i = 0; i < lines.Count; i++)
            if (string.Equals(lines[i].Key, key, StringComparison.Ordinal)) return i;
        return -1;
    }
}
