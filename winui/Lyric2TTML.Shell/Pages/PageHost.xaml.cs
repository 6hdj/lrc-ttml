using Microsoft.UI.Xaml.Controls;

namespace Lyric2TTML.Shell.Pages;

/// <summary>尚未迁移页面的**诚实占位**：明确说"未迁移"，不放假数据、不放点不动的按钮。</summary>
public sealed partial class PageHost : Page
{
    private static readonly System.Collections.Generic.Dictionary<string, string> Names = new()
    {
        ["welcome"] = "开始",
        ["home"] = "歌词工作台",
        ["tasks"] = "任务中心",
        ["models"] = "模型管理",
        ["runtime"] = "运行库",
        ["settings"] = "设置",
    };

    public PageHost(string tag)
    {
        InitializeComponent();
        LbTitle.Text = Names.TryGetValue(tag, out var n) ? n : tag;
    }
}
