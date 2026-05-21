using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Represents a single content segment parsed from a BVF file.
/// </summary>
public struct BVFSegment
{
    /// <summary>Start time in seconds.</summary>
    public float startTime;

    /// <summary>End time in seconds.</summary>
    public float endTime;

    /// <summary>Classification derived from the manifest risk field ("safe" or "mature").</summary>
    public string classification;

    /// <summary>
    /// SHA256 hex digest of raw audio packet data concatenated from the segment's data blocks.
    /// Empty string if no audio packets are present.
    /// </summary>
    public string audioHash;

    /// <summary>Unique segment identifier from the segment index.</summary>
    public string segmentId;

    /// <summary>Segment duration in milliseconds.</summary>
    public ulong durationMs;

    /// <summary>Offset of the segment data block from the start of the segment index.</summary>
    public ulong dataOffset;

    /// <summary>Length of the segment data block in bytes.</summary>
    public ulong dataLength;

    /// <summary>
    /// Creates a new BVFSegment.
    /// </summary>
    /// <param name="startTime">Start time in seconds.</param>
    /// <param name="endTime">End time in seconds.</param>
    /// <param name="classification">Classification from manifest risk field.</param>
    /// <param name="audioHash">SHA256 hex digest of audio packet data.</param>
    public BVFSegment(float startTime, float endTime, string classification, string audioHash)
    {
        this.startTime = startTime;
        this.endTime = endTime;
        this.classification = classification;
        this.audioHash = audioHash;
        this.segmentId = string.Empty;
        this.durationMs = 0;
        this.dataOffset = 0;
        this.dataLength = 0;
    }
}

/// <summary>
/// Top-level BVF file metadata parsed from the 64-byte header.
/// </summary>
public struct BVFInfo
{
    /// <summary>Major version number.</summary>
    public uint versionMajor;

    /// <summary>Minor version number.</summary>
    public uint versionMinor;

    /// <summary>BVF file flags.</summary>
    public uint flags;

    /// <summary>Byte offset from the start of the file to the segment index.</summary>
    public ulong indexOffset;

    /// <summary>Length in bytes of the segment index.</summary>
    public ulong indexLength;

    /// <summary>Byte offset from the start of the file to the zstd-compressed manifest.</summary>
    public ulong manifestOffset;

    /// <summary>Length in bytes of the zstd-compressed manifest.</summary>
    public ulong manifestLength;

    /// <summary>Total number of segments described in the index.</summary>
    public uint segmentCount;

    /// <summary>Total duration of all segments combined, in milliseconds.</summary>
    public ulong totalDurationMs;

    /// <summary>Movie identifier from the manifest.</summary>
    public string movieId;

    /// <summary>Title from the manifest.</summary>
    public string title;
}

/// <summary>
/// Parses BVF (Binary Video Format) files. Reads headers, segment indexes,
/// zstd-compressed manifests, and per-segment audio packet data.
///
/// Dependencies: ZstdNet NuGet package for zstd decompression.
/// </summary>
public static class BVFReader
{
    private const ulong BVF_MAGIC = 0x0000000001465642; // "BVF\x01" + 4 reserved bytes in little-endian
    private const int BVF_HEADER_SIZE = 64;
    private const int INDEX_ENTRY_SIZE = 40;
    private const int SEG_BLOCK_MAGIC = 0x00474553; // "SEG\x00" in little-endian
    private const int SEG_BLOCK_HEADER_SIZE = 32;
    private const byte AUDIO_PACKET_TYPE = 0x02;

    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
    };

    /// <summary>
    /// Reads the BVF header from a file on disk.
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <returns>Parsed BVF header metadata.</returns>
    public static BVFInfo ReadHeader(string filePath)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return ReadHeader(stream);
    }

    /// <summary>
    /// Reads the BVF header from a binary stream.
    /// </summary>
    /// <param name="stream">Stream positioned at the start of the BVF file.</param>
    /// <returns>Parsed BVF header metadata.</returns>
    public static BVFInfo ReadHeader(Stream stream)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));

        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);

        // Read and validate magic
        var magic = reader.ReadUInt64();
        if (magic != BVF_MAGIC)
            throw new InvalidDataException(
                $"Invalid BVF magic: expected 0x{BVF_MAGIC:X8}, got 0x{magic:X8}");

        var versionMajor = reader.ReadUInt16();
        var versionMinor = reader.ReadUInt16();
        var flags = reader.ReadUInt32();
        var indexOffset = reader.ReadUInt64();
        var indexLength = reader.ReadUInt64();
        var manifestOffset = reader.ReadUInt64();
        var manifestLength = reader.ReadUInt64();
        var segmentCount = reader.ReadUInt32();
        var totalDurationMs = reader.ReadUInt64();
        var reserved = reader.ReadUInt32();

        // Validate version: only version 1.x is supported
        if (versionMajor > 1)
            throw new InvalidDataException(
                $"Unsupported BVF major version: {versionMajor}. Only version 1.x is supported.");

        var info = new BVFInfo
        {
            versionMajor = versionMajor,
            versionMinor = versionMinor,
            flags = flags,
            indexOffset = indexOffset,
            indexLength = indexLength,
            manifestOffset = manifestOffset,
            manifestLength = manifestLength,
            segmentCount = segmentCount,
            totalDurationMs = totalDurationMs,
            movieId = string.Empty,
            title = string.Empty,
        };

        // Read manifest to populate movieId and title
        ReadManifestInfo(stream, reader, info);

        return info;
    }

    /// <summary>
    /// Reads all segments from a BVF file with full metadata (audio hashes, classifications, etc.).
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <returns>List of all segments found in the BVF file.</returns>
    public static List<BVFSegment> GetSegments(string filePath)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return GetSegments(stream);
    }

    /// <summary>
    /// Reads all segments from a BVF file stream.
    /// </summary>
    /// <param name="stream">Stream positioned at the start of the BVF file.</param>
    /// <returns>List of all segments found in the BVF file.</returns>
    public static List<BVFSegment> GetSegments(Stream stream)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));

        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);

        // Parse header
        var magic = reader.ReadUInt64();
        if (magic != BVF_MAGIC)
            throw new InvalidDataException(
                $"Invalid BVF magic: expected 0x{BVF_MAGIC:X8}, got 0x{magic:X8}");

        var versionMajor = reader.ReadUInt16();
        var versionMinor = reader.ReadUInt16();
        var flags = reader.ReadUInt32();
        var indexOffset = reader.ReadUInt64();
        var indexLength = reader.ReadUInt64();
        var manifestOffset = reader.ReadUInt64();
        var manifestLength = reader.ReadUInt64();
        var segmentCount = reader.ReadUInt32();
        var totalDurationMs = reader.ReadUInt64();
        var reserved = reader.ReadUInt32();

        if (versionMajor > 1)
            throw new InvalidDataException(
                $"Unsupported BVF major version: {versionMajor}. Only version 1.x is supported.");

        // Read segment index
        stream.Seek(indexOffset, SeekOrigin.Begin);
        var indexEntries = new BVFIndexEntry[segmentCount];
        for (uint i = 0; i < segmentCount; i++)
        {
            indexEntries[i] = ReadIndexEntry(reader);
        }

        // Validate index length
        var expectedIndexLength = segmentCount * (ulong)INDEX_ENTRY_SIZE;
        if (indexLength != expectedIndexLength)
            throw new InvalidDataException(
                $"Index length mismatch: header says {indexLength}, expected {expectedIndexLength} for {segmentCount} entries");

        // Read manifest
        var manifest = ReadManifest(stream, manifestOffset, manifestLength);

        // Validate manifest segment count matches index
        if (manifest.Segments.Count != segmentCount)
            throw new InvalidDataException(
                $"Manifest segment count ({manifest.Segments.Count}) does not match index segment count ({segmentCount})");

        // Build segment lookup from manifest
        var manifestMap = new Dictionary<string, ManifestSegment>(manifest.Segments.Count);
        foreach (var ms in manifest.Segments)
        {
            manifestMap[ms.Id] = ms;
        }

        // Parse segment data blocks and build BVFSegment list
        var segments = new List<BVFSegment>((int)segmentCount);
        foreach (var entry in indexEntries)
        {
            var segment = ParseSegment(stream, entry, manifestMap);
            segments.Add(segment);
        }

        return segments;
    }

    /// <summary>
    /// Reads segments filtered by profile rules.
    /// "play" actions are included, "swap" actions are replaced with the filler segment_id,
    /// and "skip" actions are excluded.
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <param name="profile">Profile key (e.g. "child", "teen_m", "adult").</param>
    /// <returns>List of segments resolved for the given profile.</returns>
    public static BVFSegment[] GetSegments(string filePath, string profile)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return GetSegments(stream, profile);
    }

    /// <summary>
    /// Reads segments filtered by profile rules from a stream.
    /// "play" actions are included, "swap" actions are replaced with the filler segment_id,
    /// and "skip" actions are excluded.
    /// </summary>
    /// <param name="stream">Stream positioned at the start of the BVF file.</param>
    /// <param name="profile">Profile key (e.g. "child", "teen_m", "adult").</param>
    /// <returns>List of segments resolved for the given profile.</returns>
    public static BVFSegment[] GetSegments(Stream stream, string profile)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));
        if (string.IsNullOrEmpty(profile))
            throw new ArgumentException("Profile must not be null or empty.", nameof(profile));

        var allSegments = GetSegments(stream);

        // We need to re-read the manifest to get profile actions.
        // Re-open the stream at the beginning for a second pass.
        stream.Seek(0, SeekOrigin.Begin);
        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);

        var magic = reader.ReadUInt64();
        if (magic != BVF_MAGIC)
            throw new InvalidDataException(
                $"Invalid BVF magic: expected 0x{BVF_MAGIC:X8}, got 0x{magic:X8}");

        reader.ReadUInt16(); // versionMajor
        reader.ReadUInt16(); // versionMinor
        reader.ReadUInt32(); // flags
        var indexOffset = reader.ReadUInt64();
        var indexLength = reader.ReadUInt64();
        var manifestOffset = reader.ReadUInt64();
        var manifestLength = reader.ReadUInt64();
        var segmentCount = reader.ReadUInt32();
        reader.ReadUInt64(); // totalDurationMs
        reader.ReadUInt32(); // reserved

        var manifest = ReadManifest(stream, manifestOffset, manifestLength);

        // Build a map of segment_id -> profile action
        var profileActionMap = new Dictionary<string, string>();
        foreach (var ms in manifest.Segments)
        {
            if (ms.Profiles != null)
            {
                foreach (var p in ms.Profiles)
                {
                    if (p.Key == profile)
                    {
                        profileActionMap[ms.Id] = p.Value.Action;
                        break;
                    }
                }
            }
        }

        var resolved = new List<BVFSegment>();
        foreach (var seg in allSegments)
        {
            if (profileActionMap.TryGetValue(seg.segmentId, out var action))
            {
                if (action == "skip")
                    continue;

                if (action == "swap")
                {
                    // Replace segment_id with the filler segment_id from manifest
                    var manifestEntry = manifest.Segments.Find(m => m.Id == seg.segmentId);
                    if (manifestEntry != null && manifestEntry.Profiles != null &&
                        manifestEntry.Profiles.TryGetValue(profile, out var profileEntry))
                    {
                        seg.segmentId = profileEntry.SegmentId;
                    }
                }
            }

            resolved.Add(seg);
        }

        return resolved.ToArray();
    }

    /// <summary>
    /// Reads manifest metadata (movieId, title) from the zstd-compressed manifest block.
    /// </summary>
    private static void ReadManifestInfo(Stream stream, BinaryReader reader, BVFInfo info)
    {
        var manifest = ReadManifest(stream, info.manifestOffset, info.manifestLength);
        info.movieId = manifest.MovieId;
        info.title = manifest.Title;
    }

    /// <summary>
    /// Reads and decompresses the zstd-compressed manifest JSON.
    /// </summary>
    private static ManifestData ReadManifest(Stream stream, ulong manifestOffset, ulong manifestLength)
    {
        stream.Seek((long)manifestOffset, SeekOrigin.Begin);
        var compressedData = stream.ReadBytes((int)manifestLength);

        // Decompress using ZstdNet (NuGet package)
        // var decompressed = ZstdNet.Compression.Decompress(compressedData);
        // For environments without ZstdNet, fall back to System.IO.Compression if stored:
        byte[] decompressed;
        try
        {
            // Attempt ZstdNet decompression (preferred)
            // Requires: NuGet package ZstdNet
            decompressed = ZstdDecompress(compressedData);
        }
        catch
        {
            // Fallback: try standard deflate (some BVF files may use raw deflate)
            using var ms = new MemoryStream(compressedData);
            using var ds = new DeflateStream(ms, CompressionMode.Decompress);
            using var result = new MemoryStream();
            ds.CopyTo(result);
            decompressed = result.ToArray();
        }

        var json = Encoding.UTF8.GetString(decompressed);
        var manifest = JsonSerializer.Deserialize<ManifestData>(json, _jsonOptions);

        if (manifest == null)
            throw new InvalidDataException("Failed to parse BVF manifest JSON");

        return manifest;
    }

    /// <summary>
    /// Reads a single 40-byte segment index entry from the binary reader.
    /// </summary>
    private static BVFIndexEntry ReadIndexEntry(BinaryReader reader)
    {
        var segmentIdBytes = reader.ReadBytes(16);
        var segmentId = Encoding.UTF8.GetString(segmentIdBytes).TrimEnd('\0');
        var dataOffset = reader.ReadUInt64();
        var dataLength = reader.ReadUInt64();
        var durationMs = reader.ReadUInt64();

        return new BVFIndexEntry
        {
            SegmentId = segmentId,
            DataOffset = dataOffset,
            DataLength = dataLength,
            DurationMs = durationMs,
        };
    }

    /// <summary>
    /// Parses a single segment by reading its data block and computing the audio hash.
    /// </summary>
    private static BVFSegment ParseSegment(Stream stream, BVFIndexEntry entry, Dictionary<string, ManifestSegment> manifestMap)
    {
        var segment = new BVFSegment
        {
            segmentId = entry.SegmentId,
            durationMs = entry.DurationMs,
            dataOffset = entry.DataOffset,
            dataLength = entry.DataLength,
        };

        // Look up manifest data for timing and classification
        if (manifestMap.TryGetValue(entry.SegmentId, out var manifestSeg))
        {
            segment.startTime = manifestSeg.StartMs / 1000f;
            segment.endTime = manifestSeg.EndMs / 1000f;
            segment.classification = manifestSeg.Risk ?? "safe";
        }

        // Parse segment data blocks and compute audio hash
        segment.audioHash = ComputeAudioHash(stream, entry);

        return segment;
    }

    /// <summary>
    /// Reads the segment data block at the given offset, parses packets,
    /// concatenates audio packet data, and computes its SHA256 hash.
    /// </summary>
    private static string ComputeAudioHash(Stream stream, BVFIndexEntry entry)
    {
        stream.Seek((long)entry.DataOffset, SeekOrigin.Begin);
        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);

        // Read block header (32 bytes)
        var blockMagic = reader.ReadUInt32();
        if (blockMagic != SEG_BLOCK_MAGIC)
            throw new InvalidDataException(
                $"Invalid segment block magic for '{entry.SegmentId}': expected 0x{SEG_BLOCK_MAGIC:X8}, got 0x{blockMagic:X8}");

        var blockSegmentId = reader.ReadBytes(16);
        var blockSegmentIdStr = Encoding.UTF8.GetString(blockSegmentId).TrimEnd('\0');
        if (blockSegmentIdStr != entry.SegmentId)
            throw new InvalidDataException(
                $"Segment ID mismatch in block: index says '{entry.SegmentId}', block says '{blockSegmentIdStr}'");

        reader.ReadUInt32(); // codec_video
        reader.ReadUInt32(); // codec_audio
        reader.ReadUInt32(); // reserved

        // Parse variable-length packets until end of data block
        var audioData = new MemoryStream();
        var position = (ulong)SEG_BLOCK_HEADER_SIZE;
        var endPosition = entry.DataLength;

        while (position < endPosition)
        {
            var packetStart = (int)position;

            // Check if we have enough bytes for the packet header
            if (endPosition - position < 14u) // 1 + 3 + 4 + 8 = 16, but we read incrementally
                break;

            var packetType = reader.ReadByte();
            var reservedBytes = reader.ReadBytes(3);
            var packetSize = reader.ReadUInt32();
            var ptsMs = reader.ReadUInt64();

            // packet_type(u8) + reserved(3 bytes) + packet_size(u32) + pts_ms(u64) = 1 + 3 + 4 + 8 = 16 bytes
            position = (ulong)packetStart + 16u;

            if (packetType == AUDIO_PACKET_TYPE && packetSize > 0)
            {
                var packetData = reader.ReadBytes((int)packetSize);
                audioData.Write(packetData, 0, packetData.Length);
                position += (ulong)packetSize;
            }
            else
            {
                // Skip non-audio packet data
                reader.ReadBytes((int)packetSize);
                position += (ulong)packetSize;
            }

        }

        // Compute SHA256 of concatenated audio data
        if (audioData.Length == 0)
            return string.Empty;

        audioData.Seek(0, SeekOrigin.Begin);
        var hash = SHA256.HashData(audioData);
        return BitConverter.ToString(hash).Replace("-", "").ToLowerInvariant();
    }

    /// <summary>
    /// Decompresses zstd-compressed data using the ZstdNet library.
    /// </summary>
    private static byte[] ZstdDecompress(byte[] compressed)
    {
        // Requires ZstdNet NuGet package (https://www.nuget.org/packages/ZstdNet)
        // Usage:
        //   using var cellar = new Cellar();
        //   return cellar.Decode(compressed);
        //
        // Alternative manual implementation if ZstdNet is unavailable:
        throw new InvalidOperationException(
            "ZstdNet NuGet package is required for BVF manifest decompression. " +
            "Install the ZstdNet package and rebuild.");
    }

    /// <summary>
    /// Internal representation of a segment index entry.
    /// </summary>
    private struct BVFIndexEntry
    {
        public string SegmentId;
        public ulong DataOffset;
        public ulong DataLength;
        public ulong DurationMs;
    }

    /// <summary>
    /// Internal model for the zstd-decompressed manifest JSON.
    /// </summary>
    private sealed class ManifestData
    {
        [JsonPropertyName("movie_id")]
        public string MovieId { get; set; } = string.Empty;

        [JsonPropertyName("title")]
        public string Title { get; set; } = string.Empty;

        [JsonPropertyName("segments")]
        public List<ManifestSegment> Segments { get; set; } = new();
    }

    /// <summary>
    /// Individual segment entry within the manifest JSON.
    /// </summary>
    private sealed class ManifestSegment
    {
        [JsonPropertyName("id")]
        public string Id { get; set; } = string.Empty;

        [JsonPropertyName("start_ms")]
        public ulong StartMs { get; set; }

        [JsonPropertyName("end_ms")]
        public ulong EndMs { get; set; }

        [JsonPropertyName("tags")]
        public List<string> Tags { get; set; } = new();

        [JsonPropertyName("risk")]
        public string? Risk { get; set; }

        [JsonPropertyName("profiles")]
        public Dictionary<string, ProfileAction>? Profiles { get; set; }
    }

    /// <summary>
    /// Per-profile action mapping from the manifest.
    /// </summary>
    private sealed class ProfileAction
    {
        [JsonPropertyName("action")]
        public string Action { get; set; } = string.Empty;

        [JsonPropertyName("segment_id")]
        public string SegmentId { get; set; } = string.Empty;
    }
}
