using System;
using System.Text;
using Jellyfin.Plugin.SmartBranching;
using Xunit;

namespace SmartBranching.Plugin.Tests;

public class Fmp4ConcatHelperTests
{
    [Fact]
    public void GetEmitRange_NonFmp4Payload_PassesThroughUnchanged()
    {
        var payload = Encoding.UTF8.GetBytes("AAAABBBB");

        var (emitStart, emitLength) = Fmp4ConcatHelper.GetEmitRange(
            payload,
            isFirstSegment: true,
            isLastSegment: true);

        Assert.Equal(0, emitStart);
        Assert.Equal(payload.Length, emitLength);
    }

    [Fact]
    public void GetEmitRange_FirstSegment_KeepsInitAndMediaBoxes()
    {
        var payload = BuildSegmentPayload(includeMfra: true);

        var (emitStart, emitLength) = Fmp4ConcatHelper.GetEmitRange(
            payload,
            isFirstSegment: true,
            isLastSegment: false);

        Assert.Equal(0, emitStart);
        Assert.Equal(payload.Length - 12, emitLength);
        Assert.Equal("ftyp", ReadBoxType(payload, 0));
        Assert.Equal("mdat", ReadBoxType(payload, (int)emitLength - 12));
    }

    [Fact]
    public void GetEmitRange_LaterSegment_StripsInitBoxes()
    {
        var payload = BuildSegmentPayload(includeMfra: false);

        var (emitStart, emitLength) = Fmp4ConcatHelper.GetEmitRange(
            payload,
            isFirstSegment: false,
            isLastSegment: true);

        Assert.Equal(24, emitStart);
        Assert.Equal("moof", ReadBoxType(payload, (int)emitStart));
        Assert.Equal(payload.Length - emitStart, emitLength);
    }

    private static byte[] BuildSegmentPayload(bool includeMfra)
    {
        using var stream = new System.IO.MemoryStream();
        WriteBox(stream, "ftyp", new byte[] { 0x69, 0x73, 0x6F, 0x35 });
        WriteBox(stream, "moov", new byte[] { 0x01, 0x02, 0x03, 0x04 });
        WriteBox(stream, "moof", new byte[] { 0x05, 0x06, 0x07, 0x08 });
        WriteBox(stream, "mdat", new byte[] { 0x09, 0x0A, 0x0B, 0x0C });
        if (includeMfra)
            WriteBox(stream, "mfra", new byte[] { 0x0D, 0x0E, 0x0F, 0x10 });

        return stream.ToArray();
    }

    private static void WriteBox(System.IO.Stream stream, string type, byte[] payload)
    {
        var size = 8 + payload.Length;
        stream.WriteByte((byte)((size >> 24) & 0xFF));
        stream.WriteByte((byte)((size >> 16) & 0xFF));
        stream.WriteByte((byte)((size >> 8) & 0xFF));
        stream.WriteByte((byte)(size & 0xFF));
        stream.Write(Encoding.ASCII.GetBytes(type));
        stream.Write(payload);
    }

    private static string ReadBoxType(byte[] payload, int offset)
        => Encoding.ASCII.GetString(payload, offset + 4, 4);
}
