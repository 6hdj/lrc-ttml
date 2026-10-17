using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Lyric2TTML.Shell.Backend;
using Lyric2TTML.Shell.Editing;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.Media.Core;
using Windows.Media.Playback;

namespace Lyric2TTML.Shell.Pages;

/// <summary>
/// 编辑台第一切片：**载入任务 → 逐字渲染 → 播放驱动逐字高亮**。
///
/// 技术选择（都有理由）：
///  * 每行一个 `TextBlock` + 多个 `Run`；高亮用 **`TextHighlighter`**
///    ⇒ **只改颜色、不改字号/字重 ⇒ 天然满足「高亮不能改变盒子尺寸」**（AGENTS.md 硬约束 2）。
///  * 音频用 **`MediaPlayer`** + 后端 `/api/audio?id=`（**支持 Range** ⇒ 拖进度条不会重下整份）。
///  * 高亮判定**全部走 `HighlightEngine`**（纯逻辑，已 32/32 单测覆盖 ✓）：
///    状态优先级、咬字/拖音两阶段、并行高亮、后缀最大 end。
///
/// 本切片**不含**编辑动作（改时间/改声部/改文本），它们接在下一步 —— 我不会假装已经做完。
/// </summary>
public sealed partial class EditorPage : Page
{
    private sealed class WordRef
    {
        public EditWord Word = new();
        public int Start, Length;              // 在行文本中的偏移（供 TextHighlighter 用）
        public TextBlock Host = null!;          // 它所在行的 TextBlock
        public int LineIndex;                   // 供点击时显示"这是第几行/哪个声部"
        public int WordIndex;
    }

    private readonly List<EditLine> _lines = new();
    private readonly List<List<WordRef>> _wordRefs = new();   // 与 _lines 对应
    private readonly List<TextBlock> _lineBlocks = new();
    private readonly MediaPlayer _player = new();
    private DispatcherQueueTimer? _tick;
    private string _taskId = "";
    private string _audioTaskId = "";         // ⚠️ 音频当前挂的是哪个任务（同一个不重开）
    private string _lastAudioUrl = "";
    private bool _audioIsLocal;   // 当前装的是不是本地直读源（回退时用）
    private double? _pendingSeek;             // 媒体未就绪时的待办 seek（就绪后补做）
    private System.Text.Json.Nodes.JsonNode? _docNode;   // **整份原始 doc**（只改必要字段再回传）
    private int _docVersion;                              // 乐观锁用（后端拿存储版本比）
    private string _lastKey = "";                             // 去重：同一批高亮不重复设置

    public EditorPage()
    {
        InitializeComponent();
        HookPanel();          // 批 1：把属性面板挂到 Loaded（幂等）
        // 主题切换后：显式重算歌词颜色（颜色是按 ActualTheme 选的，不会自己更新）
        ActualThemeChanged += (_, _) =>
        {
            try
            {
                if (_lines.Count > 0) { Render(); SyncHighlightAfterRender(); }
            }
            catch { }
        };
        AudioHost.SetMediaPlayer(_player);      // 必须承载，否则可能不出声
        _tick = DispatcherQueue.CreateTimer();
        _tick.Interval = TimeSpan.FromMilliseconds(60);       // ≈16fps 足够，别更密（省电/顺滑平衡）
        _tick.Tick += (_, _) => Tick();
        // ⚠️ 播放失败**必须看得见**：MediaPlayer 不报错时会静默什么都不做
        //（用户实测："点播放没反应"）⇒ 把失败原因和播放状态都显示出来。
        _player.MediaFailed += (_, e) =>
        {
            // ⚠️ 这是**真错误**（不是"打开慢"）⇒ 只有此时才回退 HTTP。
            // 之前用"2 秒内状态还是 None 就当失败"判过 ✗ —— 63MB 的 flac 打开常超 2 秒，
            // 会产生**假失败**，把音频莫名切到 HTTP 流（更慢、更容易卡）。
            if (_audioIsLocal && _audioTaskId.Length > 0)
            {
                var httpUrl = $"{App.Backend.BaseUrl}/api/audio?id={Uri.EscapeDataString(_audioTaskId)}";
                _lastAudioUrl = httpUrl + "   （本地直读真失败 ⇒ 已回退 HTTP）";
                _audioIsLocal = false;
                _player.Source = MediaSource.CreateFromUri(new Uri(httpUrl));
                LbAudio.Text = "音频（HTTP 回退）：" + httpUrl;
                LbState.Text = "状态：本地直读失败，已回退 HTTP…";
                return;
            }
            LbAudio.Text = $"⚠ 音频打开失败：{e.ErrorMessage}";
        };
        _player.PlaybackSession.PlaybackStateChanged += (_, _) =>
        {
            LbState.Text = "状态：" + DescribeState(_player.PlaybackSession.PlaybackState);
            // 就绪后补做挂起的 seek（"编辑后从当前句继续"靠它落地）
            if (_pendingSeek is { } sec &&
                _player.PlaybackSession.PlaybackState is MediaPlaybackState.Playing
                    or MediaPlaybackState.Paused)
            {
                _pendingSeek = null;
                try { _player.PlaybackSession.Position = TimeSpan.FromSeconds(sec); }
                catch { /* 下一帧再试也行 */ }
                _lastKey = "";
            }
        };
        Loaded += (_, _) => _tick?.Start();
        Unloaded += (_, _) => { _tick?.Stop(); _player.Pause(); };
    }

    private void Say(InfoBarSeverity sev, string title, string msg)
    {
        Bar.Severity = sev;
        Bar.Title = title;
        Bar.Message = msg;
        Bar.IsOpen = true;
    }

    // ---------------- 载入 ----------------

    private async void BtnLoad_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (!App.Backend.IsRunning)
            {
                Say(InfoBarSeverity.Informational, "后端未启动", "先到工作台发起一次转换，或到诊断页启动后端。");
                return;
            }
            using var api = new BackendClient(App.Backend.BaseUrl);
            using var doc = await api.GetJsonAsync("/api/tasks");
            var items = new List<(string Id, string Title)>();
            if (doc.RootElement.TryGetProperty("tasks", out var arr))
                foreach (var t in arr.EnumerateArray())
                {
                    var id = t.TryGetProperty("id", out var i) ? i.GetString() ?? "" : "";
                    var title = t.TryGetProperty("title", out var ti) ? ti.GetString() ?? "" : "";
                    if (id.Length > 0) items.Add((id, title));
                }
            if (items.Count == 0)
            {
                Say(InfoBarSeverity.Warning, "没有可编辑的任务", "先在工作台完成一次转换。");
                return;
            }
            CbTask.ItemsSource = items.ConvertAll(x => $"{x.Id} · {x.Title}");
            CbTask.SelectedIndex = 0;
            await LoadDocAsync(items[0].Id);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "读取任务列表失败", ex.ToString());
        }
    }

    /// <summary>
    /// 显式保存：把当前编辑态（含元数据）写成正式 TTML。
    /// ⚠️ 编辑台的改动平时只写 `.edit.json` 草稿（persist=false），不点这里不会改正式文件。
    /// </summary>
    private async void BtnSaveTtml_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (string.IsNullOrEmpty(_taskId))
            {
                Say(InfoBarSeverity.Warning, "没有可保存的任务", "先点「读取任务列表」并选一个任务。");
                return;
            }
            BtnSaveTtml.IsEnabled = false;
            using var api = new Lyric2TTML.Shell.Backend.ConversionApi(App.Backend.BaseUrl);
            var path = await api.SaveAsync(_taskId);
            Say(InfoBarSeverity.Success, "已保存 TTML", path);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "保存失败", ex.Message);
        }
        finally
        {
            BtnSaveTtml.IsEnabled = true;
        }
    }

    private async void CbTask_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        try
        {
            var s = CbTask.SelectedItem?.ToString() ?? "";
            var id = s.Split(' ')[0];
            if (id.Length > 0) await LoadDocAsync(id);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "切换任务失败", ex.ToString());
        }
    }

    private async Task LoadDocAsync(string taskId, bool reloadAudio = true)
    {
        await LoadDocAsync(taskId, null);
    }

    /// <summary>
    /// 载入编辑态并渲染。
    ///
    /// ``resume``：**编辑保存后的重新载入**用它保住播放进度。
    /// 没有它就会出现用户实测的那个 bug：改完一个字，音频**从头开始播**。
    /// 原因见 <see cref="LoadAudioIfNeeded"/> 的说明。
    /// </summary>
    private async Task LoadDocAsync(string taskId, Resume? resume)
    {
        try
        {
            // 编辑前若在播，先记下位置并暂停（用户预期："编辑的时候应该暂停在编辑处"）
            var keepPlaying = resume is { WasPlaying: true };
            if (resume is not null) _player.Pause();

            using var api = new BackendClient(App.Backend.BaseUrl);
            using var doc = await api.GetJsonAsync($"/api/edit?id={Uri.EscapeDataString(taskId)}");
            if (!doc.RootElement.TryGetProperty("doc", out var d))
            {
                Say(InfoBarSeverity.Warning, "任务还没有编辑态", "该任务可能未完成，或后端未生成编辑态。");
                return;
            }
            _taskId = taskId;
            BtnSaveTtml.IsEnabled = true;   // 有任务了才允许「保存 TTML」
            // 保留整份原始 doc + version：写回时**只改目标字段**，其余原样带回
            _docNode = JsonNode.Parse(d.GetRawText());
            _docVersion = d.TryGetProperty("version", out var vv) && vv.TryGetInt32(out var v0) ? v0 : 0;
            ParseDoc(d);
            Render();
            LoadAudioIfNeeded(taskId);      // ⚠️ 同一个任务**不再重开音频**
            SyncHighlightAfterRender();     // ⚠️ 重建 DOM 后必须重置高亮状态

            if (resume is not null)
            {
                SeekTo(resume.Value.Seconds);
                if (keepPlaying) _player.Play();
                Say(InfoBarSeverity.Success, "已保存并刷新",
                    $"{_lines.Count} 行 / {CountWords()} 字 · 从 {resume.Value.Seconds:0.000}s 继续");
            }
            else
            {
                Say(InfoBarSeverity.Success, "已载入",
                    $"{_lines.Count} 行 / {CountWords()} 字（任务 {taskId}）");
            }
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "载入编辑态失败", ex.ToString());
        }
    }

    /// <summary>重新载入时用来恢复播放现场。</summary>
    private readonly record struct Resume(double Seconds, bool WasPlaying);

    /// <summary>
    /// 重建 DOM 后把高亮状态**清干净再点亮一次**。
    ///
    /// ⚠️ 真 bug：重建后 `_lastKey` 还是旧值，Tick 会认为"没变化"直接 return，
    /// 于是**保存/切页之后高亮就消失**，直到播放跨到下一个字才恢复
    /// （网页端踩过同一个坑，见 AGENTS.md）。
    /// </summary>
    private void SyncHighlightAfterRender()
    {
        _lastKey = "";
        Tick();
    }

    private void ParseDoc(JsonElement d)
    {
        _lines.Clear();
        if (!d.TryGetProperty("lines", out var lines) || lines.ValueKind != JsonValueKind.Array) return;
        foreach (var ln in lines.EnumerateArray())
        {
            var line = new EditLine
            {
                Agent = ln.TryGetProperty("agent", out var a) ? a.GetString() ?? "v1" : "v1",
                // 实测：key 是字符串（"L1"），xml_id 同样是字符串
                Key = ln.TryGetProperty("key", out var k) ? (k.GetString() ?? "") : "",
                // 整行起止（微调的行边界；铁律 #13 span ⊆ p）
                Begin = ln.TryGetProperty("begin", out var lb) && lb.TryGetDouble(out var lbv) ? lbv : 0,
                End = ln.TryGetProperty("end", out var le) && le.TryGetDouble(out var lev) ? lev : 0,
            };
            if (ln.TryGetProperty("words", out var ws) && ws.ValueKind == JsonValueKind.Array)
                foreach (var w in ws.EnumerateArray())
                {
                    line.Words.Add(new EditWord
                    {
                        Text = w.TryGetProperty("text", out var tx) ? tx.GetString() ?? "" : "",
                        // ⚠️ 契约：EditDoc 的 JSON 字段是 begin/end（start 只是 Python 侧别名）
                        Begin = w.TryGetProperty("begin", out var b) && b.TryGetDouble(out var bv) ? bv : 0,
                        End = w.TryGetProperty("end", out var en) && en.TryGetDouble(out var ev) ? ev : 0,
                        IsBg = w.TryGetProperty("is_bg", out var bg) && bg.ValueKind == JsonValueKind.True,
                        Matched = !(w.TryGetProperty("matched", out var m) && m.ValueKind == JsonValueKind.False),
                        // ⚠️⚠️ 实测字段名是 **prob**（0~1），**没有** confidence ——
                        // 读错字段名不会报错，只会让"低可信"标记**永远不出现**（静默失效）。
                        Confidence = w.TryGetProperty("prob", out var cf) && cf.TryGetDouble(out var cv)
                                     ? cv : null,
                    });
                }
            _lines.Add(line);
        }
    }

    private int CountWords()
    {
        int n = 0; foreach (var l in _lines) n += l.Words.Count; return n;
    }

    /// <summary>
    /// 主题感知颜色。⚠️ 不能用 `Application.Current.Resources[...]` 取 —— 那是**应用级**
    /// （默认浅色）字典，深色主题下取到的是深色文字 ⇒ 黑字写在黑底上（用户实测看不见）。
    /// 这里按页面实际主题给色，并在 ActualThemeChanged 时重绘。
    /// </summary>
    private Brush ThemeTextBrush(string role)
    {
        bool dark = ActualTheme == ElementTheme.Dark;
        Windows.UI.Color c = role switch
        {
            "secondary" => dark ? Windows.UI.Color.FromArgb(0xB3, 0xFF, 0xFF, 0xFF)
                                : Windows.UI.Color.FromArgb(0x99, 0x00, 0x00, 0x00),
            "tertiary"  => dark ? Windows.UI.Color.FromArgb(0x99, 0xFF, 0xFF, 0xFF)
                                : Windows.UI.Color.FromArgb(0x66, 0x00, 0x00, 0x00),
            _           => dark ? Windows.UI.Color.FromArgb(0xFF, 0xFF, 0xFF, 0xFF)
                                : Windows.UI.Color.FromArgb(0xFF, 0x00, 0x00, 0x00),
        };
        return new SolidColorBrush(c);
    }

    // ---------------- 渲染（每行一个 TextBlock，逐字 Run）----------------

    private void Render()
    {
        LinesHost.Children.Clear();
        _lineBlocks.Clear();
        _wordRefs.Clear();

        for (int li = 0; li < _lines.Count; li++)
        {
            var line = _lines[li];
            var tb = new TextBlock
            {
                TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true,
                Margin = new Thickness(0, 3, 0, 3),
            };
            var refs = new List<WordRef>();
            int offset = 0;

            // 行首做成**可点击**：点它改这一行的声部（网页端 👥 标签的行为）
            var head = new Hyperlink { UnderlineStyle = UnderlineStyle.None };
            var headText = $"{line.Key} {line.Agent}  ";      // Hyperlink 是 Span，**没有 Text 属性** ⇒ 记住长度用
            head.Inlines.Add(new Run { Text = headText });
            int headLine = li;
            head.Click += (_, _) => _ = EditLineAgentAsync(headLine);
            head.Foreground = ThemeTextBrush("tertiary");
            tb.Inlines.Add(head);
            offset += headText.Length;

            foreach (var w in line.Words)
            {
                // ⚠️ 用 **Hyperlink** 而不是 Run：`Run` **不可点击**（用户实测"点字没反应"）。
                // Hyperlink 是 Span，颜色照样能设，且自带 Click。
                var run = new Hyperlink { UnderlineStyle = UnderlineStyle.None };
                switch (HighlightEngine.StatusOf(w))
                {
                    case WordStatus.Interpolated:
                        run.Foreground = new SolidColorBrush(Microsoft.UI.Colors.OrangeRed);
                        break;
                    case WordStatus.LowConfidence:
                        run.Foreground = new SolidColorBrush(Microsoft.UI.Colors.Goldenrod);
                        break;
                    case WordStatus.Background:
                        run.Foreground = ThemeTextBrush("secondary");
                        break;
                    default:
                        run.Foreground = ThemeTextBrush("primary");
                        break;
                }
                run.Inlines.Add(new Run { Text = w.Text });
                tb.Inlines.Add(run);
                var wr = new WordRef
                {
                    Word = w, Host = tb, Start = offset, Length = w.Text.Length,
                    LineIndex = li, WordIndex = refs.Count,
                };
                // 点击 = 跳到该字 + 显示信息（网页端编辑台就是点字跳转）
                run.Click += (_, _) => OnWordClicked(wr);
                refs.Add(wr);
                offset += w.Text.Length;
            }
            _lineBlocks.Add(tb);
            _wordRefs.Add(refs);
            LinesHost.Children.Add(tb);
        }
    }

    // ---------------- 点击字：跳转 + 显示信息 ----------------

    /// <summary>
    /// 点一个字：**跳到它的开始时间**（像播放器里点歌词那样），并显示真实信息。
    ///
    /// ⚠️ 这里**不再自动弹"改时间"对话框**（上一版会弹）。用户实测的反馈是
    /// "操作逻辑不太顺手"：点一下字就跳出一个模态框，等于每次试听都被打断，
    /// 而且没有"点字只跳转"的轻量操作。现在改成：
    ///   * 单击 = 只跳转 + 刷新高亮（最常用的动作，零打断）；
    ///   * 改时间 / 改声部 = 明确的「✏️ 改这个字」「🔁 改这行声部」按钮——
    ///     目标就是当前字/当前行，不必先点一遍。
    /// 这也和竞品一致：选中与编辑是两个动作。
    /// </summary>
    private void OnWordClicked(WordRef r)
    {
        try
        {
            SeekTo(r.Word.Begin);
            _lastKey = "";                                   // 强制下一帧重算高亮（暂停时也能立刻亮）
            _selLine = r.LineIndex;
            _selWord = r.WordIndex;
            RefreshSelectionLabel();
            // ⭐ 用户要求：**在歌词里选完字，面板就直接认这个字**，不用再选一遍。
            _panelSel.SelectWord(r.LineIndex, r.WordIndex, _lines);
            if (_expander is not null) _expander.IsExpanded = true;   // 自动展开，直接就能改
            PanelRefreshSafe();
            Tick();                                          // 立即刷新高亮
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "跳转失败", ex.ToString());
        }
    }

    /// <summary>当前选中的字（改时间/改声部按钮的目标）。</summary>
    private int _selLine = -1;
    private int _selWord = -1;

    private WordRef? Selected()
    {
        if (_selLine < 0 || _selLine >= _wordRefs.Count) return null;
        var refs = _wordRefs[_selLine];
        return (_selWord >= 0 && _selWord < refs.Count) ? refs[_selWord] : null;
    }

    private void RefreshSelectionLabel()
    {
        var r = Selected();
        if (r is null) { LbSel.Text = "当前选中：未选"; return; }
        var agent = (_selLine >= 0 && _selLine < _lines.Count) ? _lines[_selLine].Agent : "?";
        var status = HighlightEngine.StatusOf(r.Word) switch
        {
            WordStatus.Interpolated => "插值",
            WordStatus.LowConfidence => $"低可信({r.Word.Confidence:0.00})",
            WordStatus.Background => "和声",
            _ => "正常",
        };
        LbSel.Text = $"当前选中：{_lines[_selLine].Key} 第{_selWord + 1}字「{r.Word.Text}」"
                   + $" {r.Word.Begin:0.000}~{r.Word.End:0.000}s · {agent} · {status}";
    }

    private void BtnEditWord_Click(object sender, RoutedEventArgs e)
    {
        var r = Selected();
        if (r is null) { Say(InfoBarSeverity.Informational, "先选一个字", "点歌词里的任意一个字，再点这里改时间。"); return; }
        _ = EditWordTimeAsync(r);
    }

    private void BtnEditAgent_Click(object sender, RoutedEventArgs e)
    {
        var r = Selected();
        if (r is null) { Say(InfoBarSeverity.Informational, "先选一个字", "点歌词里的任意一个字，再点这里改这一行的声部。"); return; }
        _ = EditLineAgentAsync(r.LineIndex);
    }

    // ---------------- 写回编辑：⏱ 改时间 ----------------

    /// <summary>
    /// 改这个字的时间（网页端 ⏱ 时间轴标签的核心动作）。
    ///
    /// 只改 `begin`/`end` 两个字段，**整份 doc 原样回传** —— 避免丢字段；
    /// 带 `version` 走乐观锁，409 时明确提示"别人改过了，请重新载入"（不静默覆盖）。
    /// </summary>
    private async Task EditWordTimeAsync(WordRef r)
    {
        if (_docNode is null) return;
        try
        {
            var nb = new NumberBox
            {
                Header = "开始（秒）", Value = r.Word.Begin,
                SmallChange = 0.01, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
                Minimum = 0,
            };
            var ne = new NumberBox
            {
                Header = "结束（秒）", Value = r.Word.End,
                SmallChange = 0.01, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline,
                Minimum = 0,
            };
            var dlg = new ContentDialog
            {
                XamlRoot = XamlRoot,
                Title = $"改时间 · 「{r.Word.Text}」（第 {r.LineIndex + 1} 行 第 {r.WordIndex + 1} 字）",
                Content = new StackPanel
                {
                    Spacing = 10,
                    Children =
                    {
                        new TextBlock { Text = $"原区间：{r.Word.Begin:0.000}s ~ {r.Word.End:0.000}s（时长 {r.Word.End - r.Word.Begin:0.000}s）" },
                        nb, ne,
                        new TextBlock
                        {
                            Text = "保存只改这一个字的时间，其余字段原样写回（不会动你别的改动）。",
                            TextWrapping = TextWrapping.Wrap, Opacity = 0.75, FontSize = 12,
                        },
                    },
                },
                PrimaryButtonText = "保存",
                CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Primary,
            };
            if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
            if (ne.Value <= nb.Value)
            {
                Say(InfoBarSeverity.Warning, "时间不合理", "结束必须大于开始，未保存。");
                return;
            }
            await SaveWordTimeAsync(r, nb.Value, ne.Value);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "改时间失败", ex.ToString());
        }
    }

    // ---------------- 写回编辑：👥 改声部 ----------------

    /// <summary>
    /// 点行首改声部（网页端 👥 声部标签的核心动作）。
    /// 只改这一行的 `agent`，其余字段一个字节都不动。
    /// </summary>
    private async Task EditLineAgentAsync(int lineIndex)
    {
        if (_docNode is null || lineIndex < 0 || lineIndex >= _lines.Count) return;
        try
        {
            var cb = new ComboBox { Header = "这一行谁唱", MinWidth = 180 };
            foreach (var (label0, _code0) in AgentChoices) cb.Items.Add(label0);
            var curLabel0 = AgentLabelOf(_lines[lineIndex].Agent);
            if (!cb.Items.Contains(curLabel0)) cb.Items.Add(curLabel0);          // 尊重文档里已有的特殊值（如 v1000 合唱）
            cb.SelectedItem = _lines[lineIndex].Agent;
            var dlg = new ContentDialog
            {
                XamlRoot = XamlRoot,
                Title = $"改声部 · {_lines[lineIndex].Key}（当前 {AgentLabelOf(_lines[lineIndex].Agent)}）",
                Content = new StackPanel
                {
                    Spacing = 10,
                    Children =
                    {
                        cb,
                        new TextBlock
                        {
                            Text = "一声部/二声部不同时才触发 Apple Music 左右分栏；旁白与合唱属特例。文件里仍按规范写 v1/v2/v3/v1000。",
                            TextWrapping = TextWrapping.Wrap, Opacity = 0.75, FontSize = 12,
                        },
                    },
                },
                PrimaryButtonText = "保存",
                CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Primary,
            };
            if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
            var agent = AgentCodeOf(cb.SelectedItem?.ToString() ?? "");   // 汉字 → 代码
            if (string.IsNullOrEmpty(agent) || agent == _lines[lineIndex].Agent) return;
            await SaveLineAgentAsync(lineIndex, agent, CaptureResume(lineIndex));
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "改声部失败", ex.ToString());
        }
    }

    private async Task SaveLineAgentAsync(int lineIndex, string agent, Resume? resume = null)
    {
        if (_docNode is null) { Say(InfoBarSeverity.Warning, "没有可保存的编辑态", "请先载入任务。"); return; }
        // 编辑前记住"播到哪了"，保存后原地续播（用户预期：编辑时暂停在编辑处、编辑完从这句继续）
        var ctx = resume ?? CaptureResume(lineIndex);
        try
        {
            _docNode["lines"]!.AsArray()[lineIndex]!["agent"] = agent;    // **只改这一个字段**
            var body = new JsonObject
            {
                ["id"] = _taskId,
                ["version"] = _docVersion,          // 乐观锁：后端拿"存储版本"比
                ["doc"] = _docNode.DeepClone(),
            };
            using var api = new BackendClient(App.Backend.BaseUrl);
            using var res = await api.PostJsonAsync("/api/edit", body.ToJsonString());
            _docVersion = res.RootElement.TryGetProperty("version", out var nv) && nv.TryGetInt32(out var nvv)
                ? nvv : _docVersion + 1;
            Say(InfoBarSeverity.Success, "已保存声部",
                $"{_lines[lineIndex].Key} → {agent}（新版本 {_docVersion}）");
            await LoadDocAsync(_taskId, ctx);            // 重载后端规范化后的结果（保住播放位置）
        }
        catch (HttpRequestException ex) when (ex.Message.StartsWith("409"))
        {
            Say(InfoBarSeverity.Error, "版本冲突（409）",
                "这个任务在别处被改过了（存储版本与提交版本不一致）。请重新载入后再改 —— 我没有覆盖对方的改动。\n" + ex.Message);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "保存失败", ex.ToString());
        }
    }

    /// <summary>
    /// 记录"编辑前播放到哪"：正在唱的那句优先（编辑完从这句继续），
    /// 否则用当前播放位置。同时给出"当时是否在播"，用于决定保存后是否自动继续。
    /// </summary>
    private Resume CaptureResume(int preferLine = -1)
    {
        bool playing;
        double pos;
        try
        {
            playing = _player.PlaybackSession.PlaybackState == MediaPlaybackState.Playing;
            pos = _player.PlaybackSession.Position.TotalSeconds;
        }
        catch { playing = false; pos = 0; }

        // 优先"当前正在唱的行"（用户的预期是"编辑完之后从这句话开始播放"）
        int line = preferLine;
        if (line < 0)
        {
            var cur = CurrentWord();
            if (cur is { } c) line = c.Line;
        }
        if (line >= 0 && LineSpan(line) is { } span) pos = span.Start;
        return new Resume(Math.Max(0, pos), playing);
    }

    private async Task SaveWordTimeAsync(WordRef r, double begin, double end, Resume? resume = null)
    {
        if (_docNode is null) { Say(InfoBarSeverity.Warning, "没有可保存的编辑态", "请先载入任务。"); return; }
        var ctx = resume ?? CaptureResume(r.LineIndex);
        try
        {
            // **只改这两个字段**（其余节点原样保留）
            var lines = _docNode["lines"]!.AsArray();
            var words = lines[r.LineIndex]!["words"]!.AsArray();
            words[r.WordIndex]!["begin"] = begin;
            words[r.WordIndex]!["end"] = end;

            var body = new JsonObject
            {
                ["id"] = _taskId,
                ["version"] = _docVersion,          // 乐观锁：后端拿"存储版本"比
                ["doc"] = _docNode.DeepClone(),
            };
            using var api = new BackendClient(App.Backend.BaseUrl);
            using var res = await api.PostJsonAsync("/api/edit", body.ToJsonString());
            var newVer = res.RootElement.TryGetProperty("version", out var nv) && nv.TryGetInt32(out var nvv)
                ? nvv : _docVersion + 1;
            _docVersion = newVer;
            Say(InfoBarSeverity.Success, "已保存",
                $"「{r.Word.Text}」→ {begin:0.000}s ~ {end:0.000}s（新版本 {_docVersion}）");
            // 本地同步，避免再点同一个字时用的是旧值
            r.Word.Begin = begin; r.Word.End = end;
            _lastKey = "";
            await LoadDocAsync(_taskId, ctx);            // 重载（保住播放位置，不从头播）
        }
        catch (HttpRequestException ex) when (ex.Message.StartsWith("409"))
        {
            Say(InfoBarSeverity.Error, "版本冲突（409）",
                "这个任务在别处被改过了（存储版本与提交版本不一致）。请重新载入后再改 —— " +
                "我没有覆盖对方的改动。\n" + ex.Message);
        }
        catch (Exception ex)
        {
            Say(InfoBarSeverity.Error, "保存失败", ex.ToString());
        }
    }

    // ---------------- 音频 + 高亮 ----------------

    /// <summary>
    /// 装音频源。**同一个任务只装一次**（重复装会重新打开整个文件 ⇒ 用户报"加载特别慢"）。
    ///
    /// ⚠️⚠️ **优先本地文件直读**（`GET /api/audio-path` → `file:///…`）：
    /// 走 HTTP 流时，Python 那套简易 HTTP 服务要边播边供几十 MB（那首 flac 有 63 MB），
    /// **供不上就卡死在同一个位置**（用户实测："播五六秒就卡住，每次卡在相同地方"）。
    /// 本地直读由系统解码器直接读盘 ⇒ 打开与 seek 都是毫秒级，不会断供。
    /// 拿不到本地路径才退回 HTTP 流（不报错打断编辑）。
    /// </summary>
    // ================= 批 1：属性面板（选中即编辑）=================
    // 面板**用代码构建**（不动 XAML ⇒ 不存在 XAML/CS 名字不匹配的风险）。
    // 选中由面板自己的「选行 + 上/下字」驱动 ⇒ 不依赖改动 OnWordClicked。
    // ⚠️ 字段初始化器在**构造时**执行，用它把 Loaded 事件挂上（免去改构造函数）。

    private readonly Lyric2TTML.Shell.Editing.EditSelection _panelSel = new();
    private Lyric2TTML.Shell.Editing.EditSession? _panelSess;
    private ComboBox? _cbLine2, _cbAgent2;
    private TextBlock? _lblSel2, _lblWord2, _lblHint2;
    private NumberBox? _nbB, _nbE;
    private Button? _btnSaveTime2, _btnM10, _btnP10, _btnSaveAgent2;
    private bool _panelBuilt;
    private Expander? _expander;
    private TextBox? _tbBg;
    private Button? _btnSaveBg, _btnDelBg;

    /// <summary>
    /// 声部选项的**汉字标签 ↔ TTML 代码**对照。
    /// 用户看不懂 v1/v3/v1000 ⇒ 界面只说人话；写入文件仍用规范代码
    /// （AGENTS.md §4.1：v1000 = group 合唱；[旁白] → v3 + x-narration）。
    /// 非标准值原样显示、原样透传（保真，不丢）。
    /// </summary>
    private static readonly (string Label, string Code)[] AgentChoices =
    {
        ("一声部", "v1"),
        ("二声部", "v2"),
        ("旁白", "v3"),
        ("合唱", "v1000"),
    };

    private static string AgentLabelOf(string code)
    {
        foreach (var (label, cd) in AgentChoices) if (cd == code) return label;
        return string.IsNullOrEmpty(code) ? "（未设置）" : code;
    }

    private static string AgentCodeOf(string label)
    {
        foreach (var (lb, cd) in AgentChoices) if (lb == label) return cd;
        return label;
    }


    /// <summary>构造时把面板挂到 Loaded（幂等；避免改构造函数或 XAML）。</summary>
    private bool HookPanel()
    {
        Loaded += (_, _) => { try { BuildPanel(); RefreshPanel(); } catch { /* 面板失败不影响编辑台其它功能 */ } };
        return true;
    }

    /// <summary>懒建/复用会话；**换了任务自动重建**（否则会把上一首的 version/doc 用到新任务上）。</summary>
    private Lyric2TTML.Shell.Editing.EditSession? EnsureSession()
    {
        if (_panelSess is not null && _panelSess.TaskId != _taskId) _panelSess = null;
        if (_panelSess is not null) return _panelSess;
        if (!App.Backend.IsRunning || _docNode is null || string.IsNullOrEmpty(_taskId))
        {
            if (_lblHint2 is not null) _lblHint2.Text = "还没有载入任务（先点「读取任务列表」）";
            return null;
        }
        _panelSess = new Lyric2TTML.Shell.Editing.EditSession(
            _taskId, _docVersion, _docNode.ToJsonString(),
            new Lyric2TTML.Shell.Editing.HttpEditBackend(App.Backend.BaseUrl));
        return _panelSess;
    }

    /// <summary>建面板并插入页面根 Grid 第 3 行（把原 Row≥3 的内容整体下移 ⇒ 纯代码，不动 XAML）。</summary>
    private void BuildPanel()
    {
        if (_panelBuilt) return;
        // 页面根现在套了一层 ScrollViewer（整页可滚，修"面板展开后底部被切掉"），
        // 所以要穿过它去拿真正的 Grid，否则面板根本不会建。
        Grid? root = Content as Grid;
        if (root is null && Content is ScrollViewer sv) root = sv.Content as Grid;
        if (root is null) return;
        _panelBuilt = true;

        _lblSel2 = new TextBlock { Text = "未选中", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap };
        _lblWord2 = new TextBlock { VerticalAlignment = VerticalAlignment.Center, Opacity = 0.85 };
        _lblHint2 = new TextBlock { Opacity = 0.65, FontSize = 12, TextWrapping = TextWrapping.Wrap };

        _cbLine2 = new ComboBox { Header = "选行", MinWidth = 340 };
        _cbLine2.SelectionChanged += (_, _) =>
        {
            if (_cbLine2.SelectedIndex < 0 || _cbLine2.SelectedIndex >= _lines.Count) return;
            _panelSel.SelectLine(_cbLine2.SelectedIndex, _lines);
            RefreshPanel();
        };
        var btnPrev = new Button { Content = "◀ 上字" };
        btnPrev.Click += (_, _) => StepWord(-1);
        var btnNext = new Button { Content = "下字 ▶" };
        btnNext.Click += (_, _) => StepWord(+1);

        _nbB = new NumberBox { Header = "开始(秒)", Width = 130, SmallChange = 0.01, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
        _nbE = new NumberBox { Header = "结束(秒)", Width = 130, SmallChange = 0.01, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
        _btnSaveTime2 = new Button { Content = "保存这个字的时间", VerticalAlignment = VerticalAlignment.Bottom };
        _btnSaveTime2.Click += async (_, _) => await SaveWordTimeAsync();
        _btnM10 = new Button { Content = "◀ −10ms", VerticalAlignment = VerticalAlignment.Bottom };
        _btnM10.Click += async (_, _) => await NudgeAsync(-0.010);
        _btnP10 = new Button { Content = "+10ms ▶", VerticalAlignment = VerticalAlignment.Bottom };
        _btnP10.Click += async (_, _) => await NudgeAsync(+0.010);

        _cbAgent2 = new ComboBox { Header = "这一行谁唱", MinWidth = 150 };
        // 用户明确指出：**一首歌只有两个声部**（v1/v2）⇒ 选项就这两个。
        // 但文档里可能已有 v3/v1000（旁白/合唱等特例）⇒ RefreshPanel 会把"当前值"补进列表，
        // 避免显示为空、也避免用户想改回去时选不到。
        _cbAgent2.ItemsSource = new List<string> { "一声部", "二声部", "旁白", "合唱" };
        _btnSaveAgent2 = new Button { Content = "保存这一行声部", VerticalAlignment = VerticalAlignment.Bottom };
        _btnSaveAgent2.Click += async (_, _) => await SaveAgentAsync();

        var inner = new StackPanel { Spacing = 8, Children = { _lblSel2 } };
        inner.Children.Add(new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { _cbLine2, btnPrev, btnNext, _lblWord2 } });
        inner.Children.Add(new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { _nbB, _nbE, _btnSaveTime2, _btnM10, _btnP10 } });
        inner.Children.Add(new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { _cbAgent2, _btnSaveAgent2 } });

        // 和声（背景人声 x-bg）：服务端 set_bg_text 会算好时间（沿用原跨度 /
        // 没有就从主唱结束后接 / 必要时撑开行尾），本地不拼时间。
        _tbBg = new TextBox
        {
            Header = "和声（背景人声）", MinWidth = 300,
            PlaceholderText = "留空并保存 = 删除和声",
        };
        _btnSaveBg = new Button { Content = "保存和声", VerticalAlignment = VerticalAlignment.Bottom };
        _btnSaveBg.Click += async (_, _) => await SaveBgAsync();
        _btnDelBg = new Button { Content = "删除和声", VerticalAlignment = VerticalAlignment.Bottom };
        _btnDelBg.Click += async (_, _) => await RemoveBgAsync();
        inner.Children.Add(new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8,
                                           Children = { _tbBg, _btnSaveBg, _btnDelBg } });

        var btnMeta = new Button { Content = "编辑元数据（歌曲名/歌手/专辑/ID…）" };
        btnMeta.Click += async (_, _) => await EditMetadataAsync();
        inner.Children.Add(new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Children = { btnMeta } });
        inner.Children.Add(_lblHint2);

        var border = new Border
        {
            CornerRadius = new CornerRadius(8),
            BorderThickness = new Thickness(1),
            Padding = new Thickness(12),
            Margin = new Thickness(0, 0, 0, 8),
            // ⚠️⚠️ 这里必须**默认收起**：之前直接把整块面板放在歌词上方，
            // 把歌词区挤出了可视区（用户实测："歌词不显示了、页面超出范围还不能滚动"）。
            // 收成一行标题后，歌词区恢复原来的高度；要编辑时点开。
            Child = new Expander
            {
                Header = "编辑面板（点开：改时间 / ±10ms / 声部 / 和声 / 元数据）",
                IsExpanded = false,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                HorizontalContentAlignment = HorizontalAlignment.Stretch,
                // ⚠️ 用户实测：展开后显示不全、且**滚轮不能滚**（"元数据编辑"被挡住点不到）。
                // 内容套一层 ScrollViewer + 限高 ⇒ 鼠标滚轮可用，任何窗口高度都能看到全部项。
                Content = new ScrollViewer
                {
                    MaxHeight = 320,
                    VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                    VerticalScrollMode = ScrollMode.Auto,
                    HorizontalScrollMode = ScrollMode.Disabled,
                    Content = inner,
                },
            },
        };

        root.RowDefinitions.Insert(3, new RowDefinition { Height = GridLength.Auto });
        foreach (var child in root.Children)
            if (child is FrameworkElement fe && Grid.GetRow(fe) >= 3) Grid.SetRow(fe, Grid.GetRow(fe) + 1);
        _expander = border.Child as Expander;
        Grid.SetRow(border, 3);
        root.Children.Add(border);

        // ←/→ = ±10ms。**关键：输入框/下拉聚焦时绝不触发**（否则打字会误改时间）。
        AddHandler(UIElement.KeyDownEvent, new KeyEventHandler(OnPanelKey), true);
        RefreshPanel();
    }

    /// <summary>外部（点歌词里的字）触发的面板刷新；面板还没建好时安全返回。</summary>
    private void PanelRefreshSafe()
    {
        try { if (_panelBuilt) RefreshPanel(); } catch { /* 面板问题不影响选字/播放 */ }
    }

    private void StepWord(int delta)
    {
        if (_lines.Count == 0) return;
        if (_panelSel.LineIndex < 0) _panelSel.SelectWord(0, 0, _lines);
        else
        {
            var line = _lines[_panelSel.LineIndex];
            int wi = _panelSel.WordIndex + delta;
            if (wi < 0 || wi >= line.Words.Count)
            {
                int li = _panelSel.LineIndex + (delta > 0 ? 1 : -1);
                if (li < 0 || li >= _lines.Count) return;
                _panelSel.SelectWord(li, delta > 0 ? 0 : Math.Max(0, _lines[li].Words.Count - 1), _lines);
            }
            else _panelSel.SelectWord(_panelSel.LineIndex, wi, _lines);
        }
        RefreshPanel();
    }

    /// <summary>按选中刷新面板；脏状态显式可见（切选中不静默丢弃未保存修改）。</summary>
    private void RefreshPanel()
    {
        if (!_panelBuilt || _lblSel2 is null) return;
        try
        {
            if (_lines.Count == 0)
            {
                _lblSel2.Text = "没有载入任务（先点「读取任务列表」）";
                return;
            }
            if (_cbLine2 is not null)
            {
                if (_cbLine2.Items.Count != _lines.Count)
                {
                    var items = new List<string>();
                    for (int i = 0; i < _lines.Count; i++)
                    {
                        var l = _lines[i];
                        var t = string.Concat(l.Words.ConvertAll(w => w.Text));
                        if (t.Length > 14) t = t.Substring(0, 14) + "…";
                        items.Add($"{l.Key} [{l.Agent}] {t}");
                    }
                    _cbLine2.ItemsSource = items;
                }
                if (_panelSel.LineIndex >= 0 && _cbLine2.SelectedIndex != _panelSel.LineIndex)
                    _cbLine2.SelectedIndex = _panelSel.LineIndex;
            }
            _panelSel.ClampTo(_lines);
            _lblSel2.Text = _panelSel.Describe(_lines) + (_panelSel.IsDirty ? "   ⚠ 有未保存修改" : "");

            bool isWord = _panelSel.Kind == Lyric2TTML.Shell.Editing.SelKind.Word;
            if (_nbB is not null) _nbB.IsEnabled = isWord;
            if (_nbE is not null) _nbE.IsEnabled = isWord;
            if (_btnSaveTime2 is not null) _btnSaveTime2.IsEnabled = isWord;
            if (_btnM10 is not null) _btnM10.IsEnabled = isWord;
            if (_btnP10 is not null) _btnP10.IsEnabled = isWord;
            if (isWord)
            {
                var w = _lines[_panelSel.LineIndex].Words[_panelSel.WordIndex];
                if (_nbB is not null) _nbB.Value = w.Begin;
                if (_nbE is not null) _nbE.Value = w.End;
                if (_lblWord2 is not null) _lblWord2.Text = $"「{w.Text}」 {w.Begin:0.000}~{w.End:0.000}s";
            }
            else if (_lblWord2 is not null) _lblWord2.Text = "（未选字）";
            if (_cbAgent2 is not null && _panelSel.LineIndex >= 0 && _panelSel.LineIndex < _lines.Count)
            {
                var curLabel = AgentLabelOf(_lines[_panelSel.LineIndex].Agent);
                if (_cbAgent2.ItemsSource is List<string> list && !list.Contains(curLabel))
                {
                    list.Add(curLabel);              // 非标准代码：原样补进列表（保真、可改回）
                    _cbAgent2.ItemsSource = null;
                    _cbAgent2.ItemsSource = list;
                }
                _cbAgent2.SelectedItem = curLabel;
            }
            if (_btnSaveAgent2 is not null) _btnSaveAgent2.IsEnabled = _panelSel.LineIndex >= 0;

            // 显示该行当前和声（x-bg 层的字拼起来）
            if (_tbBg is not null)
            {
                bool hasLine = _panelSel.LineIndex >= 0 && _panelSel.LineIndex < _lines.Count;
                _tbBg.IsEnabled = hasLine;
                if (_btnSaveBg is not null) _btnSaveBg.IsEnabled = hasLine;
                if (_btnDelBg is not null) _btnDelBg.IsEnabled = hasLine;
                if (hasLine)
                {
                    var bg = new System.Text.StringBuilder();
                    foreach (var w in _lines[_panelSel.LineIndex].Words)
                        if (w.IsBg) bg.Append(w.Text);
                    if (_tbBg.Text != bg.ToString()) _tbBg.Text = bg.ToString();
                }
            }
            if (_lblHint2 is not null)
                _lblHint2.Text = _panelSel.IsDirty
                    ? "⚠ 有未保存修改 —— 先保存（成功提示里会显示新版本号）"
                    : "边界校验与后端一致：负时间 / begin≥end / 超出本行都会被拒绝（不会留下半完成数据）";
        }
        catch (Exception ex)
        {
            if (_lblHint2 is not null) _lblHint2.Text = "面板刷新失败：" + ex.Message;
        }
    }

    private async Task SaveWordTimeAsync()
    {
        try
        {
            var session = EnsureSession();
            if (session is null) return;
            if (_panelSel.Kind != Lyric2TTML.Shell.Editing.SelKind.Word) { Say(InfoBarSeverity.Warning, "先选一个字", "用面板的「选行 + 上/下字」选中。"); return; }
            double b = _nbB?.Value ?? 0, e = _nbE?.Value ?? 0;
            if (e - b < 0.001) { Say(InfoBarSeverity.Warning, "时间不合理", "结束必须大于开始（未提交）。"); return; }
            int li = _panelSel.LineIndex, wi = _panelSel.WordIndex;
            string text = _lines[li].Words[wi].Text;
            var r = await session.CommitAsync(doc =>
            {
                var arr = doc["lines"]?.AsArray();
                if (arr is null || li >= arr.Count) return false;
                var ws = arr[li]?["words"]?.AsArray();
                if (ws is null || wi >= ws.Count) return false;
                ws[wi]!["begin"] = b; ws[wi]!["end"] = e;
                return true;
            });
            ReportCommit(r, $"「{text}」→ {b:0.000}~{e:0.000}s");
            if (r.Ok) await LoadDocAsync(_taskId, reloadAudio: false);
        }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "保存时间失败", ex.ToString()); }
    }

    private async Task NudgeAsync(double delta)
    {
        try
        {
            var session = EnsureSession();
            if (session is null) return;
            if (_panelSel.Kind != Lyric2TTML.Shell.Editing.SelKind.Word) { Say(InfoBarSeverity.Warning, "先选一个字", "选中字之后再微调。"); return; }
            var line = _lines[_panelSel.LineIndex];
            var w = line.Words[_panelSel.WordIndex];
            var check = Lyric2TTML.Shell.Editing.TimeNudge.ApplyOne(
                w, delta, Lyric2TTML.Shell.Editing.NudgeTarget.Both,
                line.End > line.Begin ? line.Begin : (double?)null,
                line.End > line.Begin ? line.End : (double?)null);   // 铁律 #13：不得越出本行
            if (!check.Ok) { Say(InfoBarSeverity.Warning, "微调被拒绝（未提交）", check.Reason); return; }
            double b = w.Begin, e = w.End;
            int li = _panelSel.LineIndex, wi = _panelSel.WordIndex;
            var r = await session.CommitAsync(doc =>
            {
                var arr = doc["lines"]?.AsArray();
                if (arr is null || li >= arr.Count) return false;
                var ws = arr[li]?["words"]?.AsArray();
                if (ws is null || wi >= ws.Count) return false;
                ws[wi]!["begin"] = b; ws[wi]!["end"] = e;
                return true;
            });
            ReportCommit(r, $"{delta * 1000:0}ms → {b:0.000}~{e:0.000}s");
            if (r.Ok) await LoadDocAsync(_taskId, reloadAudio: false);
        }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "微调失败", ex.ToString()); }
    }

    private async Task SaveAgentAsync()
    {
        try
        {
            var session = EnsureSession();
            if (session is null) return;
            if (_panelSel.LineIndex < 0 || _panelSel.LineIndex >= _lines.Count) return;
            var agent = AgentCodeOf(_cbAgent2?.SelectedItem?.ToString() ?? "");   // 汉字 → v1/v2/v3/v1000
            if (agent.Length == 0 || agent == _lines[_panelSel.LineIndex].Agent) return;
            int li = _panelSel.LineIndex;
            var key = _lines[li].Key;
            var r = await session.CommitAsync(doc =>
            {
                var arr = doc["lines"]?.AsArray();
                if (arr is null || li >= arr.Count) return false;
                arr[li]!["agent"] = agent;
                return true;
            });
            ReportCommit(r, $"{key} 声部 → {AgentLabelOf(agent)}");
            if (r.Ok) await LoadDocAsync(_taskId, reloadAudio: false);
        }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "保存声部失败", ex.ToString()); }
    }

    /// <summary>←/→ = ±10ms。**焦点在输入控件上时直接放行**（不让打字变成改时间）。</summary>
    private async void OnPanelKey(object sender, KeyRoutedEventArgs e)
    {
        try
        {
            var focused = FocusManager.GetFocusedElement(XamlRoot);
            if (focused is TextBox || focused is NumberBox || focused is ComboBox || focused is AutoSuggestBox) return;
            if (e.Key == Windows.System.VirtualKey.Left) { e.Handled = true; await NudgeAsync(-0.010); }
            else if (e.Key == Windows.System.VirtualKey.Right) { e.Handled = true; await NudgeAsync(+0.010); }
        }
        catch { /* 键盘处理失败不影响其它功能 */ }
    }

    /// <summary>保存和声：走服务端 set_bg（时间由它算）；传空串等价于删除和声。</summary>
    private async Task SaveBgAsync()
    {
        try
        {
            var session = EnsureSession();
            if (session is null) return;
            if (_panelSel.LineIndex < 0 || _panelSel.LineIndex >= _lines.Count)
            { Say(InfoBarSeverity.Warning, "先选一行", "用面板的「选行」下拉选一行。"); return; }
            var key = _lines[_panelSel.LineIndex].Key;
            var text = _tbBg?.Text ?? "";
            var r = await session.CommitOpAsync("set_bg",
                new System.Text.Json.Nodes.JsonObject { ["key"] = key, ["text"] = text });
            ReportCommit(r, text.Length == 0
                ? $"{key} 和声已清空（等价删除和声）"
                : $"{key} 和声 → 「{text}」");
            if (r.Ok) await LoadDocAsync(_taskId, reloadAudio: false);
        }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "保存和声失败", ex.ToString()); }
    }

    /// <summary>删除和声：走服务端 remove_bg（会把被和声撑开的行尾收回到主唱末尾）。</summary>
    private async Task RemoveBgAsync()
    {
        try
        {
            var session = EnsureSession();
            if (session is null) return;
            if (_panelSel.LineIndex < 0 || _panelSel.LineIndex >= _lines.Count)
            { Say(InfoBarSeverity.Warning, "先选一行", "用面板的「选行」下拉选一行。"); return; }
            var key = _lines[_panelSel.LineIndex].Key;
            var r = await session.CommitOpAsync("remove_bg",
                new System.Text.Json.Nodes.JsonObject { ["key"] = key });
            ReportCommit(r, $"{key} 已删除和声");
            if (r.Ok) await LoadDocAsync(_taskId, reloadAudio: false);
        }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "删除和声失败", ex.ToString()); }
    }

    /// <summary>
    /// 元数据键表（键名照抄 AMLL 生态 + 本项目的平台扩展；**平台 ID 以这里列出的为准**）。
    /// List=true 的键在文件里是**多条**（artists / songwriters）⇒ 用多行文本，每行一条。
    /// </summary>
    private static readonly (string Key, string Label, bool List)[] MetaFields =
    {
        ("musicName", "歌曲名称", false),
        ("artists", "歌手（每行一位）", true),
        ("album", "专辑名", false),
        ("songwriters", "词曲作者（每行一位）", true),
        ("isrc", "ISRC 码", false),
        ("ncmMusicId", "网易云 ID", false),
        ("qqMusicId", "QQ 音乐 ID", false),
        ("appleMusicId", "Apple Music ID", false),
        ("spotifyId", "Spotify ID", false),
        ("kuwoMusicId", "酷我 ID", false),
        ("kugouMusicId", "酷狗 ID", false),
        ("ttmlAuthorGithub", "作者 GitHub", false),
        ("ttmlAuthorGithubLogin", "作者 GitHub 用户名", false),
    };

    /// <summary>读当前值 → 文本（列表键按行拼）。</summary>
    private string MetaTextOf(string key)
    {
        var meta = _docNode?["meta"];
        if (meta is null) return "";
        var v = meta[key];
        if (v is System.Text.Json.Nodes.JsonArray arr)
        {
            var parts = new List<string>();
            foreach (var it in arr) parts.Add(it?.ToString() ?? "");
            return string.Join("\n", parts);
        }
        return v?.ToString() ?? "";
    }

    /// <summary>元数据对话框：**只改列出的键**；未列出的（含自定义）字段一个都不动。</summary>
    private async Task EditMetadataAsync()
    {
        try
        {
            var session = EnsureSession();
            if (session is null) return;
            var boxes = new Dictionary<string, TextBox>();
            var panel = new StackPanel { Spacing = 8, MinWidth = 460, MaxHeight = 520 };
            var scroll = new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
            foreach (var (key, label, isList) in MetaFields)
            {
                var tb = new TextBox
                {
                    Header = label,
                    Text = MetaTextOf(key),
                    AcceptsReturn = isList,
                    TextWrapping = isList ? TextWrapping.Wrap : TextWrapping.NoWrap,
                    PlaceholderText = isList ? "每行一条；留空 = 清空" : "留空 = 清空",
                };
                boxes[key] = tb;
                panel.Children.Add(tb);
            }
            panel.Children.Add(new TextBlock
            {
                Text = "只保存你改动过的项。文件里其它元数据（含本工具不认识的字段）不会被触碰。",
                TextWrapping = TextWrapping.Wrap, Opacity = 0.7, FontSize = 12,
            });

            var dlg = new ContentDialog
            {
                XamlRoot = XamlRoot,
                Title = "元数据（歌曲信息 / 平台 ID / 作者）",
                Content = scroll,
                PrimaryButtonText = "保存",
                CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Primary,
            };
            if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;

            // 收集**改动过**的键
            var changed = new List<(string Key, string[] Values, bool List)>();
            foreach (var (key, _label, isList) in MetaFields)
            {
                var now = boxes[key].Text ?? "";
                if (now == MetaTextOf(key)) continue;
                var vals = now.Split('\n').Select(x => x.Trim()).Where(x => x.Length > 0).ToArray();
                changed.Add((key, vals, isList));
            }
            if (changed.Count == 0) { Say(InfoBarSeverity.Informational, "没有改动", "元数据未变，未提交。"); return; }

            var r = await session.CommitAsync(doc =>
            {
                if (doc["meta"] is null) doc["meta"] = new System.Text.Json.Nodes.JsonObject();
                var meta = doc["meta"]!;
                foreach (var (key, vals, isList) in changed)
                {
                    if (vals.Length == 0) { meta.AsObject().Remove(key); continue; }     // 清空 = 删这一项
                    if (isList)
                    {
                        var arr = new System.Text.Json.Nodes.JsonArray();
                        foreach (var v in vals) arr.Add(v);
                        meta[key] = arr;
                    }
                    else meta[key] = vals[0];
                }
                return true;                     // ⚠️ 其余键原样保留（不重建 meta）
            });
            ReportCommit(r, $"元数据改动 {changed.Count} 项：" +
                            string.Join("、", changed.ConvertAll(x => x.Key)));
            if (r.Ok) await LoadDocAsync(_taskId, reloadAudio: false);
        }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "编辑元数据失败", ex.ToString()); }
    }

    /// <summary>如实报出事务结果（成功 / 冲突 / 失败 / 服务端自动修复都不静默）。</summary>
    private void ReportCommit(Lyric2TTML.Shell.Editing.CommitResult r, string what)
    {
        if (r.Ok)
            Say(InfoBarSeverity.Success, "已保存",
                $"{what} · v{r.Version}" + (r.FixCount > 0 ? $"（服务端自动修复 {r.FixCount} 处）" : ""));
        else if (r.Conflict)
            Say(InfoBarSeverity.Error, "版本冲突（409）",
                "这份文档在别处被改过 —— 我没有覆盖对方，你的修改仍保留，请重新载入后再保存。");
        else
            Say(InfoBarSeverity.Error, "保存失败", r.Reason);
        RefreshPanel();
    }

    private async void LoadAudioIfNeeded(string taskId)
    {
        if (taskId == _audioTaskId) return;              // 已装过 ⇒ 不重复加载
        try
        {
            string? localPath = null;
            try
            {
                using var api0 = new BackendClient(App.Backend.BaseUrl);
                using var doc0 = await api0.GetJsonAsync(
                    $"/api/audio-path?id={Uri.EscapeDataString(taskId)}");
                if (doc0.RootElement.TryGetProperty("path", out var pv)) localPath = pv.GetString();
            }
            catch { localPath = null; }                  // 端点不可用 ⇒ 退回 HTTP

            string url;
            if (!string.IsNullOrEmpty(localPath) && File.Exists(localPath))
            {
                url = new Uri(localPath!).AbsoluteUri;   // file:///… ⇒ 本地直读
                _audioIsLocal = true;
                LbAudio.Text = "音频（本地直读）：" + localPath;
            }
            else
            {
                url = $"{App.Backend.BaseUrl}/api/audio?id={Uri.EscapeDataString(taskId)}";
                _audioIsLocal = false;
                LbAudio.Text = "音频（HTTP 流，可能卡顿）：" + url;
            }
            _lastAudioUrl = url;
            _player.Source = MediaSource.CreateFromUri(new Uri(url));
            _audioTaskId = taskId;
            LbState.Text = "状态：正在打开…";
        }
        catch (Exception ex)
        {
            LbAudio.Text = "音频加载失败：" + ex.Message;
        }
    }

    /// <summary>请后端后台生成小体积代理音频（失败无所谓，只是加速）。</summary>
    private async Task WarmUpProxyAsync(string taskId)
    {
        try
        {
            using var api = new BackendClient(App.Backend.BaseUrl);
            using var _ = await api.GetJsonAsync(
                $"/api/audio_proxy?act=build&id={Uri.EscapeDataString(taskId)}");
        }
        catch { /* 加速失败不影响试听 */ }
    }

    /// <summary>
    /// 把播放状态变成人话。
    ///
    /// ⚠️ 之前显示的是 `PlaybackState` 的枚举名，而且在 LoadAudio 里写死
    /// "正在打开…" —— 播放器真正打开后那行文案没人清掉，于是
    /// **音频都在播了、界面还显示"正在打开"**（用户实测）。
    /// 现在状态只由这一个事件更新，并且映射成中文。
    /// </summary>
    private static string DescribeState(MediaPlaybackState s) => s switch
    {
        MediaPlaybackState.None => "空闲",
        MediaPlaybackState.Opening => "正在打开音频…",
        MediaPlaybackState.Buffering => "缓冲中…",
        MediaPlaybackState.Playing => "播放中",
        MediaPlaybackState.Paused => "已暂停",
        _ => s.ToString(),
    };

    /// <summary>
    /// 跳到指定秒数。
    ///
    /// ⚠️ 媒体还没打开时设 `Position` 会被**静默忽略**，所以未就绪时先记下来，
    /// 等状态变成 Playing/Paused 再补做 —— 否则"编辑后从当前句继续"会落空。
    /// </summary>
    private void SeekTo(double seconds)
    {
        if (double.IsNaN(seconds) || seconds < 0) seconds = 0;
        var session = _player.PlaybackSession;
        if (session is null) { _pendingSeek = seconds; return; }
        if (session.PlaybackState is MediaPlaybackState.Opening
            or MediaPlaybackState.None or MediaPlaybackState.Buffering)
        {
            _pendingSeek = seconds;
            return;
        }
        try { session.Position = TimeSpan.FromSeconds(seconds); }
        catch { _pendingSeek = seconds; }
    }

    /// <summary>整行区间（用于"编辑后从这句继续"）。</summary>
    private (double Start, double End)? LineSpan(int lineIndex)
    {
        if (lineIndex < 0 || lineIndex >= _lines.Count) return null;
        var line = _lines[lineIndex];
        if (line.Words.Count == 0) return null;
        double s = double.MaxValue, e = double.MinValue;
        foreach (var w in line.Words) { s = Math.Min(s, w.Begin); e = Math.Max(e, w.End); }
        return s <= e ? (s, e) : null;
    }

    /// <summary>当前正在唱的字（取第一个命中），没有就返回 null。</summary>
    private (int Line, int Word)? CurrentWord()
    {
        try
        {
            double t = _player.PlaybackSession?.Position.TotalSeconds ?? 0;
            var hits = HighlightEngine.WordsAt(_lines, t);
            if (hits.Count > 0) return (hits[0].LineIndex, hits[0].WordIndex);
        }
        catch { /* 播放器未就绪 */ }
        return null;
    }

    private void Tick()
    {
        try
        {
            if (_lines.Count == 0 || _player.PlaybackSession is null) return;
            double t = _player.PlaybackSession.Position.TotalSeconds;
            LbPos.Text = $"{t:0.000}s";
            // 状态**每帧读**（原来只在事件里更新 ⇒ 已经在放还显示"正在打开"）
            LbState.Text = $"状态：{_player.PlaybackSession.PlaybackState}";
            // **真相行**：直接暴露播放器内部状态，界面自己说明卡在哪一步
            var s0 = _player.PlaybackSession;
            LbTruth.Text = $"真相：源={(_player.Source is null ? "无" : "有")} " +
                           $"状态={s0.PlaybackState} 时长={s0.NaturalDuration.TotalSeconds:0.0}s " +
                           $"可跳={s0.CanSeek} 位置={s0.Position.TotalSeconds:0.000}s " +
                           $"错误={(string.IsNullOrEmpty(s0.PlaybackState.ToString()) ? "?" : "")}" +
                           $"\nURL={_lastAudioUrl}";
            var hits = HighlightEngine.WordsAt(_lines, t);

            // 变化才更新（每帧重设 highlighter 会闪）
            var key = string.Join(",", hits.ConvertAll(h => $"{h.LineIndex}:{h.WordIndex}:{h.Phase}"));
            if (key == _lastKey) return;
            _lastKey = key;

            foreach (var tb in _lineBlocks) tb.TextHighlighters.Clear();
            foreach (var (li, wi, phase) in hits)
            {
                if (li >= _wordRefs.Count || wi >= _wordRefs[li].Count) continue;
                var r = _wordRefs[li][wi];
                // ⚠️ 只改颜色（不改字号/字重）⇒ 不改变盒子尺寸、不抖动
                var hl = new TextHighlighter
                {
                    Background = new SolidColorBrush(phase == WordPhase.Tail
                        ? Windows.UI.Color.FromArgb(0x66, 0x22, 0xD3, 0xEE)   // 拖音：更淡
                        : Windows.UI.Color.FromArgb(0xCC, 0x22, 0xD3, 0xEE)), // 咬字：实底
                    Foreground = new SolidColorBrush(Microsoft.UI.Colors.Black),
                };
                // WinUI 3 的 TextRange 是 StartIndex + Length（**没有 EndIndex**）
                hl.Ranges.Add(new TextRange { StartIndex = r.Start, Length = r.Length });
                r.Host.TextHighlighters.Add(hl);
            }
        }
        catch { /* 单帧失败不影响播放；不弹窗刷屏 */ }
    }

    private void BtnPlay_Click(object sender, RoutedEventArgs e)
    {
        try { _player.Play(); }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "播放失败", ex.Message); }
    }

    private void BtnPause_Click(object sender, RoutedEventArgs e)
    {
        try { _player.Pause(); }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "暂停失败", ex.Message); }
    }

    private void BtnJump_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            if (!double.TryParse(EdJump.Text.Trim(), out var sec)) return;
            SeekTo(sec);
            _lastKey = "";                       // 强制下一帧重算
        }
        catch (Exception ex) { Say(InfoBarSeverity.Error, "跳转失败", ex.Message); }
    }
}
