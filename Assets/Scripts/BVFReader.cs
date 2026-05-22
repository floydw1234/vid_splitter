using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Top-level BVF file metadata parsed from the 64-byte header.
/// </summary>
public struct BVFInfo
{
    public uint versionMajor;
    public uint versionMinor;
    public uint flags;
    public ulong indexOffset;
    public ulong indexLength;
    public ulong manifestOffset;
    public ulong manifestLength;
    public uint segmentCount;
    public ulong totalDurationMs;
    public string movieId;
    public string title;
}

/// <summary>
/// Parses BVF (Binary Video Format) files: header, segment index, zstd manifest, and segment data blocks.
/// </summary>
public static class BVFReader
{
    private const ulong BVF_MAGIC = 0x0000000001465642; // "BVF\x01" + 4 reserved bytes in little-endian
    private const int INDEX_ENTRY_SIZE = 40;
    private const int SEG_BLOCK_MAGIC = 0x00474553; // "SEG\0" in little-endian
    private const int SEG_BLOCK_HEADER_SIZE = 32;
    private const byte AUDIO_PACKET_TYPE = 0x02;

    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
    };

    public static BVFInfo ReadHeader(string filePath)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return ReadHeader(stream);
    }

    public static BVFInfo ReadHeader(Stream stream)
    {
        var document = ReadDocument(stream, readSegmentPayloads: false);
        return new BVFInfo
        {
            versionMajor = document.Header.VersionMajor,
            versionMinor = document.Header.VersionMinor,
            flags = document.Header.Flags,
            indexOffset = document.Header.IndexOffset,
            indexLength = document.Header.IndexLength,
            manifestOffset = document.Header.ManifestOffset,
            manifestLength = document.Header.ManifestLength,
            segmentCount = document.Header.SegmentCount,
            totalDurationMs = document.Header.TotalDurationMs,
            movieId = document.Manifest.MovieId,
            title = document.Manifest.Title,
        };
    }

    public static List<BVFSegment> GetSegments(string filePath)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return GetSegments(stream);
    }

    public static List<BVFSegment> GetSegments(Stream stream)
    {
        return ReadDocument(stream).Segments;
    }

    public static BVFSegment[] GetSegments(string filePath, string profile)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return GetSegments(stream, profile);
    }

    public static BVFSegment[] GetSegments(Stream stream, string profile)
    {
        if (string.IsNullOrWhiteSpace(profile))
            throw new ArgumentException("Profile must not be null or empty.", nameof(profile));

        var document = ReadDocument(stream);
        var segmentMap = new Dictionary<string, BVFSegment>(StringComparer.Ordinal);
        foreach (var segment in document.Segments)
        {
            segmentMap[segment.segmentId] = segment;
        }

        var resolved = new List<BVFSegment>();
        foreach (var manifestSegment in document.Manifest.Segments)
        {
            if (manifestSegment.IsFiller)
                continue;

            var targetSegmentId = manifestSegment.Id;
            if (manifestSegment.Profiles != null &&
                manifestSegment.Profiles.TryGetValue(profile, out var profileAction))
            {
                if (string.Equals(profileAction.Action, "skip", StringComparison.OrdinalIgnoreCase))
                    continue;

                if (string.Equals(profileAction.Action, "swap", StringComparison.OrdinalIgnoreCase) &&
                    !string.IsNullOrEmpty(profileAction.SegmentId))
                {
                    targetSegmentId = profileAction.SegmentId;
                }
            }

            if (segmentMap.TryGetValue(targetSegmentId, out var segment))
                resolved.Add(segment);
        }

        return resolved.ToArray();
    }

    public static BranchManifest LoadBvfManifest(string filePath, string? moviePath = null)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        var document = ReadDocument(stream, readSegmentPayloads: false);
        return ToBranchManifest(document.Manifest, filePath, moviePath);
    }

    public static Stream OpenSegmentDataStream(string filePath, BVFSegment segment)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        var stream = File.OpenRead(filePath);
        stream.Seek((long)segment.dataOffset, SeekOrigin.Begin);
        return new BoundedReadStream(stream, (long)segment.dataLength);
    }

    public static byte[] ReadSegmentData(string filePath, BVFSegment segment)
    {
        using var stream = OpenSegmentDataStream(filePath, segment);
        using var memory = new MemoryStream();
        stream.CopyTo(memory);
        return memory.ToArray();
    }

    private static BVFDocument ReadDocument(Stream stream, bool readSegmentPayloads = true)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));

        stream.Seek(0, SeekOrigin.Begin);
        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);
        var header = ReadHeader(reader);

        var expectedIndexLength = header.SegmentCount * (ulong)INDEX_ENTRY_SIZE;
        if (header.IndexLength != expectedIndexLength)
            throw new InvalidDataException(
                $"Index length mismatch: header says {header.IndexLength}, expected {expectedIndexLength} for {header.SegmentCount} entries");

        stream.Seek((long)header.IndexOffset, SeekOrigin.Begin);
        var indexEntries = new BVFIndexEntry[(int)header.SegmentCount];
        for (var i = 0; i < indexEntries.Length; i++)
        {
            indexEntries[i] = ReadIndexEntry(reader);
        }

        var manifest = ReadManifest(stream, header.ManifestOffset, header.ManifestLength);
        var manifestMap = new Dictionary<string, ManifestSegment>(manifest.Segments.Count, StringComparer.Ordinal);
        foreach (var manifestSegment in manifest.Segments)
        {
            manifestMap[manifestSegment.Id] = manifestSegment;
        }

        var segments = new List<BVFSegment>((int)header.SegmentCount);
        foreach (var entry in indexEntries)
        {
            segments.Add(ParseSegment(stream, entry, manifestMap, readSegmentPayloads));
        }

        return new BVFDocument(header, manifest, segments);
    }

    private static BVFHeader ReadHeader(BinaryReader reader)
    {
        var magic = reader.ReadUInt64();
        if (magic != BVF_MAGIC)
            throw new InvalidDataException($"Invalid BVF magic: expected 0x{BVF_MAGIC:X8}, got 0x{magic:X8}");

        var header = new BVFHeader
        {
            VersionMajor = reader.ReadUInt16(),
            VersionMinor = reader.ReadUInt16(),
            Flags = reader.ReadUInt32(),
            IndexOffset = reader.ReadUInt64(),
            IndexLength = reader.ReadUInt64(),
            ManifestOffset = reader.ReadUInt64(),
            ManifestLength = reader.ReadUInt64(),
            SegmentCount = reader.ReadUInt32(),
            TotalDurationMs = reader.ReadUInt64(),
            Reserved = reader.ReadUInt32(),
        };

        if (header.VersionMajor > 1)
            throw new InvalidDataException($"Unsupported BVF major version: {header.VersionMajor}. Only version 1.x is supported.");

        if (header.Reserved != 0)
            throw new InvalidDataException($"Invalid BVF reserved header value: {header.Reserved}");

        return header;
    }

    private static ManifestData ReadManifest(Stream stream, ulong manifestOffset, ulong manifestLength)
    {
        stream.Seek((long)manifestOffset, SeekOrigin.Begin);
        var compressedData = ReadExact(stream, checked((int)manifestLength));

        byte[] decompressed;
        try
        {
            using var decompressor = new ZstdNet.Decompressor();
            decompressed = decompressor.Unwrap(compressedData);
        }
        catch (Exception ex)
        {
            throw new InvalidDataException("Failed to decompress BVF manifest with zstd.", ex);
        }

        var json = Encoding.UTF8.GetString(decompressed);
        var manifest = JsonSerializer.Deserialize<ManifestData>(json, _jsonOptions);
        if (manifest == null)
            throw new InvalidDataException("Failed to parse BVF manifest JSON");

        return manifest;
    }

    private static BVFIndexEntry ReadIndexEntry(BinaryReader reader)
    {
        var segmentIdBytes = reader.ReadBytes(16);
        var segmentId = Encoding.UTF8.GetString(segmentIdBytes).TrimEnd('\0');
        return new BVFIndexEntry
        {
            SegmentId = segmentId,
            DataOffset = reader.ReadUInt64(),
            DataLength = reader.ReadUInt64(),
            DurationMs = reader.ReadUInt64(),
        };
    }

    private static BVFSegment ParseSegment(
        Stream stream,
        BVFIndexEntry entry,
        Dictionary<string, ManifestSegment> manifestMap,
        bool readSegmentPayloads)
    {
        var segment = new BVFSegment
        {
            segmentId = entry.SegmentId,
            durationMs = entry.DurationMs,
            dataOffset = entry.DataOffset,
            dataLength = entry.DataLength,
            classification = "safe",
            audioHash = string.Empty,
        };

        if (manifestMap.TryGetValue(entry.SegmentId, out var manifestSegment))
        {
            segment.startTime = (manifestSegment.StartMs ?? 0) / 1000f;
            segment.endTime = (manifestSegment.EndMs ?? 0) / 1000f;
            segment.classification = manifestSegment.Risk ?? "safe";
        }

        if (readSegmentPayloads)
            segment.audioHash = ComputeAudioHash(stream, entry);

        return segment;
    }

    private static string ComputeAudioHash(Stream stream, BVFIndexEntry entry)
    {
        stream.Seek((long)entry.DataOffset, SeekOrigin.Begin);
        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);

        var blockMagic = reader.ReadUInt32();
        if (blockMagic != SEG_BLOCK_MAGIC)
            throw new InvalidDataException(
                $"Invalid segment block magic for '{entry.SegmentId}': expected 0x{SEG_BLOCK_MAGIC:X8}, got 0x{blockMagic:X8}");

        var blockSegmentId = Encoding.UTF8.GetString(reader.ReadBytes(16)).TrimEnd('\0');
        if (blockSegmentId != entry.SegmentId)
            throw new InvalidDataException($"Segment ID mismatch in block: index says '{entry.SegmentId}', block says '{blockSegmentId}'");

        reader.ReadUInt32();
        reader.ReadUInt32();
        reader.ReadUInt32();

        using var audioData = new MemoryStream();
        var position = (ulong)SEG_BLOCK_HEADER_SIZE;
        while (position + 16 <= entry.DataLength)
        {
            var packetTypeAndReserved = reader.ReadUInt32();
            var packetType = (byte)(packetTypeAndReserved >> 24);
            var packetSize = reader.ReadUInt32();
            reader.ReadUInt64();
            position += 16;

            if (packetSize > entry.DataLength - position)
                throw new InvalidDataException($"Packet in segment '{entry.SegmentId}' exceeds segment block length.");

            var packetData = reader.ReadBytes(checked((int)packetSize));
            if (packetType == AUDIO_PACKET_TYPE)
                audioData.Write(packetData, 0, packetData.Length);

            position += packetSize;
        }

        if (audioData.Length == 0)
            return string.Empty;

        audioData.Seek(0, SeekOrigin.Begin);
        return Convert.ToHexString(SHA256.HashData(audioData)).ToLowerInvariant();
    }

    private static BranchManifest ToBranchManifest(ManifestData manifest, string bvfPath, string? moviePath)
    {
        var branchManifest = new BranchManifest
        {
            MovieId = manifest.MovieId,
            MoviePath = moviePath ?? bvfPath,
            DurationSeconds = manifest.DurationMs / 1000d,
            AnalyzedAt = manifest.AnalyzedAt,
            Profiles = new Dictionary<string, UserProfile>(StringComparer.Ordinal),
            Segments = new List<Segment>(),
        };

        foreach (var profile in manifest.Profiles)
        {
            branchManifest.Profiles[profile.Key] = new UserProfile
            {
                Filters = profile.Value.Filters,
            };
        }

        foreach (var segment in manifest.Segments)
        {
            branchManifest.Segments.Add(new Segment
            {
                Id = segment.Id,
                StartTime = (segment.StartMs ?? 0) / 1000d,
                EndTime = (segment.EndMs ?? 0) / 1000d,
                Tags = segment.Tags,
                Risk = segment.Risk ?? "safe",
                IsFiller = segment.IsFiller,
                Profiles = segment.Profiles ?? new Dictionary<string, SegmentProfileAction>(StringComparer.Ordinal),
            });
        }

        return branchManifest;
    }

    private static byte[] ReadExact(Stream stream, int count)
    {
        var buffer = new byte[count];
        var offset = 0;
        while (offset < count)
        {
            var read = stream.Read(buffer, offset, count - offset);
            if (read == 0)
                throw new EndOfStreamException($"Unexpected end of BVF stream while reading {count} bytes.");

            offset += read;
        }

        return buffer;
    }

    private struct BVFHeader
    {
        public uint VersionMajor;
        public uint VersionMinor;
        public uint Flags;
        public ulong IndexOffset;
        public ulong IndexLength;
        public ulong ManifestOffset;
        public ulong ManifestLength;
        public uint SegmentCount;
        public ulong TotalDurationMs;
        public uint Reserved;
    }

    private struct BVFIndexEntry
    {
        public string SegmentId;
        public ulong DataOffset;
        public ulong DataLength;
        public ulong DurationMs;
    }

    private sealed class ManifestData
    {
        [JsonPropertyName("movie_id")]
        public string MovieId { get; set; } = string.Empty;

        [JsonPropertyName("title")]
        public string Title { get; set; } = string.Empty;

        [JsonPropertyName("duration_ms")]
        public ulong DurationMs { get; set; }

        [JsonPropertyName("analyzed_at")]
        public string? AnalyzedAt { get; set; }

        [JsonPropertyName("profiles")]
        public Dictionary<string, ManifestProfile> Profiles { get; set; } = new();

        [JsonPropertyName("segments")]
        public List<ManifestSegment> Segments { get; set; } = new();
    }

    private sealed class ManifestProfile
    {
        [JsonPropertyName("filters")]
        public List<string> Filters { get; set; } = new();
    }

    private sealed class ManifestSegment
    {
        [JsonPropertyName("id")]
        public string Id { get; set; } = string.Empty;

        [JsonPropertyName("start_ms")]
        public ulong? StartMs { get; set; }

        [JsonPropertyName("end_ms")]
        public ulong? EndMs { get; set; }

        [JsonPropertyName("tags")]
        public List<string> Tags { get; set; } = new();

        [JsonPropertyName("risk")]
        public string? Risk { get; set; }

        [JsonPropertyName("is_filler")]
        public bool IsFiller { get; set; }

        [JsonPropertyName("profiles")]
        public Dictionary<string, SegmentProfileAction>? Profiles { get; set; }
    }

    private sealed record BVFDocument(BVFHeader Header, ManifestData Manifest, List<BVFSegment> Segments);

    private sealed class BoundedReadStream : Stream
    {
        private readonly Stream _inner;
        private long _remaining;

        public BoundedReadStream(Stream inner, long length)
        {
            _inner = inner;
            _remaining = length;
        }

        public override bool CanRead => _inner.CanRead;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();

        public override long Position
        {
            get => throw new NotSupportedException();
            set => throw new NotSupportedException();
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            if (_remaining <= 0)
                return 0;

            var bytesToRead = (int)Math.Min(count, _remaining);
            var bytesRead = _inner.Read(buffer, offset, bytesToRead);
            _remaining -= bytesRead;
            return bytesRead;
        }

        public override void Flush()
        {
        }

        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

        protected override void Dispose(bool disposing)
        {
            if (disposing)
                _inner.Dispose();

            base.Dispose(disposing);
        }
    }
}
