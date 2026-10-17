using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Lyric2TTML.Shell.Pages;

/// <summary>开始页：只做导航与说明，不显示任何假状态。</summary>
public sealed partial class WelcomePage : Page
{
    public WelcomePage() => InitializeComponent();

    private static void Go(string tag)
    {
        if (App.MainWindow is MainWindow mw) mw.GoTo(tag);
    }

    private void BtnWorkspace_Click(object sender, RoutedEventArgs e) => Go("home");
    private void BtnModels_Click(object sender, RoutedEventArgs e) => Go("models");
    private void BtnRuntime_Click(object sender, RoutedEventArgs e) => Go("runtime");
    private void BtnDiagnostics_Click(object sender, RoutedEventArgs e) => Go("help");
}
