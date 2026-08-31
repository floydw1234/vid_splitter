using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;

namespace SmartBranching.Plugin.Tests;

internal static class FfmpegTestHelpers
{
    public static bool IsAvailable()
    {
        try
        {
            using var process = Process.Start(new ProcessStartInfo
            {
                FileName = ResolveFfmpegPath(),
                Arguments = "-version",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            });

            return process?.WaitForExit(5000) == true && process.ExitCode == 0;
        }
        catch
        {
            return false;
        }
    }

    public static byte[] CreateFragmentedMp4(TimeSpan duration)
    {
        var outputPath = Path.Combine(Path.GetTempPath(), $"bvf-test-seg-{Guid.NewGuid():N}.mp4");
        try
        {
            var seconds = duration.TotalSeconds.ToString("0.###", CultureInfo.InvariantCulture);
            var args =
                $"-hide_banner -loglevel error -y -f lavfi -i color=c=black:s=160x120:d={seconds} " +
                $"-an -c:v libx264 -preset ultrafast -t {seconds} " +
                "-movflags frag_keyframe+empty_moov+default_base_moof -f mp4 " +
                $"\"{outputPath}\"";

            RunFfmpeg(args);
            return File.ReadAllBytes(outputPath);
        }
        finally
        {
            if (File.Exists(outputPath))
                File.Delete(outputPath);
        }
    }

    private static void RunFfmpeg(string arguments)
    {
        using var process = Process.Start(new ProcessStartInfo
        {
            FileName = ResolveFfmpegPath(),
            Arguments = arguments,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        }) ?? throw new InvalidOperationException("Unable to start ffmpeg.");

        var stderr = process.StandardError.ReadToEnd();
        process.WaitForExit();
        if (process.ExitCode != 0)
            throw new InvalidOperationException($"ffmpeg failed: {stderr}");
    }

    private static string ResolveFfmpegPath()
    {
        var fromEnv = Environment.GetEnvironmentVariable("JELLYFIN_FFMPEG");
        if (!string.IsNullOrWhiteSpace(fromEnv) && File.Exists(fromEnv))
            return fromEnv;

        const string jellyfinBundled = "/usr/lib/jellyfin-ffmpeg/ffmpeg";
        if (File.Exists(jellyfinBundled))
            return jellyfinBundled;

        return "ffmpeg";
    }
}
