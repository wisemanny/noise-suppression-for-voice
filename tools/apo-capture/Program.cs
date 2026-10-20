using NAudio.CoreAudioApi;
using NAudio.Wave;

// Records a capture endpoint through the Windows audio engine (WASAPI shared
// mode) - the path EqualizerAPO's APO actually sits in.
//
// Do not use `ffmpeg -f dshow` for verification on this machine: DirectShow
// talks to the KS filter directly and records the raw microphone, so a working
// APO chain looks completely dead.
//
//   dotnet run -c Release -- list
//   dotnet run -c Release -- "Sennheiser" 12 take.wav

internal static class Program
{
    private static int Main(string[] args)
    {
        using var enumerator = new MMDeviceEnumerator();
        var devices = enumerator.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active);

        if (args.Length > 0 && args[0] == "list")
        {
            foreach (var d in devices)
                Console.WriteLine(d.FriendlyName);
            return 0;
        }

        var match = args.Length > 0 ? args[0] : "Sennheiser";
        var seconds = args.Length > 1 ? int.Parse(args[1]) : 5;
        var outPath = args.Length > 2 ? args[2] : "capture.wav";

        MMDevice? device = null;
        foreach (var d in devices)
            if (d.FriendlyName.Contains(match, StringComparison.OrdinalIgnoreCase)) { device = d; break; }

        if (device is null)
        {
            Console.Error.WriteLine($"no active capture device matching '{match}'");
            return 2;
        }

        using var capture = new WasapiCapture(device, false, 20);
        Console.WriteLine($"device: {device.FriendlyName}");
        Console.WriteLine($"format: {capture.WaveFormat}");

        using var writer = new WaveFileWriter(outPath, capture.WaveFormat);
        capture.DataAvailable += (_, e) => writer.Write(e.Buffer, 0, e.BytesRecorded);

        capture.StartRecording();
        System.Threading.Thread.Sleep(seconds * 1000);
        capture.StopRecording();
        writer.Flush();

        Console.WriteLine($"wrote {outPath}");
        return 0;
    }
}
