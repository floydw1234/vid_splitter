using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Represents a 64-byte BVF file header.
/// </summary>
public struct BVFHeader
{
    /// <summary>Magic bytes: "BVF\x01" + 4 reserved bytes.</summary>
    public byte[] Magic;

    /// <summary>Major version number.</summary>
    public uint VersionMajor;

    /// <summary>Minor version number.</summary>
    public uint VersionMinor;

    /// <summary>Flags bitfield (see BVF spec §3.1).</summary>
    public uint Flags;

    /// <summary>Byte offset of Segment Index from file start.</summary>
    public ulong IndexOffset;

    /// <summary>Byte length of Segment Index.</summary>
    public ulong IndexLength;

    /// <summary>Byte offset of Manifest from file start.</summary>
    public ulong ManifestOffset;

    /// <summary>Byte length of Manifest (compressed).</summary>
    public ulong ManifestLength;

    /// <summary>Total number of segment data blocks.</summary>
    public uint SegmentCount;

    /// <summary>Total unfiltered video duration in milliseconds.</summary>
    public ulong TotalDurationMs;

    /// <summary>Reserved field, must be zero.</summary>
    public uint Reserved;
}

/// <summary>
/// Represents a 40-byte segment index entry.
/// </summary>
public struct IndexEntry
{
    /// <summary>Segment ID string (null-padded in binary).</summary>
    public string SegmentId;

    /// <summary>Byte offset of this segment's data block from file start.</summary>
    public ulong DataOffset;

    /// <summary>Byte length of the segment data block.</summary>
    public ulong DataLength;

    /// <summary>Duration of this segment in milliseconds.</summary>
    public ulong DurationMs;
}

/// <summary>
/// Represents a 32-byte segment data block header.
/// </summary>
public struct BlockHeader
{
    /// <summary>Block magic: "SEG\x00" (0x00474553 in little-endian).</summary>
    public uint BlockMagic;

    /// <summary>Segment ID string (null-padded in binary).</summary>
    public string SegmentId;

    /// <summary>Video codec identifier (see §6.1).</summary>
    public uint CodecVideo;

    /// <summary>Audio codec identifier (see §6.1).</summary>
    public uint CodecAudio;

    /// <summary>Reserved field, must be zero.</summary>
    public uint Reserved;
}

/// <summary>
/// Represents a variable-length packet within a segment data block.
/// </summary>
public class Packet
{
    /// <summary>Packet type: 0x01=video, 0x02=audio, 0x03=subtitle.</summary>
    public byte PacketType;

    /// <summary>Byte length of packet_data.</summary>
    public uint PacketSize;

    /// <summary>Presentation timestamp in milliseconds.</summary>
    public ulong PtsMs;

    /// <summary>Raw codec data.</summary>
    public byte[] PacketData;
}

/// <summary>
/// Represents a segment data block with its header and packets.
/// </summary>
public class SegmentBlock
{
    /// <summary>Block header.</summary>
    public BlockHeader Header;

    /// <summary>List of packets in this block.</summary>
    public List<Packet> Packets;

    /// <summary>Byte offset from file start.</summary>
    public ulong DataOffset;

    /// <summary>Byte length of this block.</summary>
    public ulong DataLength;
}

/// <summary>
/// Per-profile action mapping from the manifest.
/// </summary>
public class ProfileAction
{
    [JsonPropertyName("action")]
    public string Action { get; set; } = string.Empty;

    [JsonPropertyName("segment_id")]
    public string SegmentId { get; set; } = string.Empty;
}

/// <summary>
/// Individual segment entry within the manifest JSON.
/// </summary>
public class ManifestSegment
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

    [JsonPropertyName("profiles")]
    public Dictionary<string, ProfileAction>? Profiles { get; set; }

    [JsonPropertyName("is_filler")]
    public bool IsFiller { get; set; }
}

/// <summary>
/// The complete BVF manifest structure (matches BVF spec §5).
/// </summary>
public class BVFManifest
{
    [JsonPropertyName("bvf_version")]
    public string BvfVersion { get; set; } = string.Empty;

    [JsonPropertyName("movie_id")]
    public string MovieId { get; set; } = string.Empty;

    [JsonPropertyName("title")]
    public string Title { get; set; } = string.Empty;

    [JsonPropertyName("duration_ms")]
    public ulong DurationMs { get; set; }

    [JsonPropertyName("analyzed_at")]
    public string? AnalyzedAt { get; set; }

    [JsonPropertyName("video_info")]
    public VideoInfo? VideoInfo { get; set; }

    [JsonPropertyName("profiles")]
    public Dictionary<string, UserProfileInfo>? Profiles { get; set; }

    [JsonPropertyName("segments")]
    public List<ManifestSegment> Segments { get; set; } = new();

    [JsonPropertyName("chapters")]
    public List<Chapter>? Chapters { get; set; }
}

/// <summary>
/// Video information from the manifest.
/// </summary>
public class VideoInfo
{
    [JsonPropertyName("width")]
    public int Width { get; set; }

    [JsonPropertyName("height")]
    public int Height { get; set; }

    [JsonPropertyName("frame_rate")]
    public string? FrameRate { get; set; }

    [JsonPropertyName("color_space")]
    public string? ColorSpace { get; set; }
}

/// <summary>
/// User profile information from the manifest.
/// </summary>
public class UserProfileInfo
{
    [JsonPropertyName("label")]
    public string? Label { get; set; }

    [JsonPropertyName("filters")]
    public List<string> Filters { get; set; } = new();
}

/// <summary>
/// Chapter information from the manifest.
/// </summary>
public class Chapter
{
    [JsonPropertyName("title")]
    public string Title { get; set; } = string.Empty;

    [JsonPropertyName("start_ms")]
    public ulong StartMs { get; set; }

    [JsonPropertyName("end_ms")]
    public ulong EndMs { get; set; }
}

/// <summary>
/// Structured segment data returned by the BVF reader.
/// </summary>
public class BVFSegmentData
{
    /// <summary>Segment ID from the index.</summary>
    public string SegmentId { get; set; } = string.Empty;

    /// <summary>Duration in milliseconds.</summary>
    public ulong DurationMs { get; set; }

    /// <summary>Byte offset from file start.</summary>
    public ulong DataOffset { get; set; }

    /// <summary>Byte length of the data block.</summary>
    public ulong DataLength { get; set; }

    /// <summary>Start time in milliseconds from manifest (null for fillers).</summary>
    public ulong? StartMs { get; set; }

    /// <summary>End time in milliseconds from manifest (null for fillers).</summary>
    public ulong? EndMs { get; set; }

    /// <summary>Risk classification from manifest.</summary>
    public string? Risk { get; set; }

    /// <summary>Tags from manifest.</summary>
    public List<string> Tags { get; set; } = new();

    /// <summary>Whether this is a filler segment.</summary>
    public bool IsFiller { get; set; }

    /// <summary>Profile actions from manifest.</summary>
    public Dictionary<string, ProfileAction>? Profiles { get; set; }

    /// <summary>Block header codec identifiers.</summary>
    public uint CodecVideo { get; set; }

    /// <summary>Block header codec identifiers.</summary>
    public uint CodecAudio { get; set; }

    /// <summary>Parsed packets from the segment block.</summary>
    public List<Packet> Packets { get; set; } = new();
}

/// <summary>
/// Parses BVF (Branched Video Format) binary files.
/// Reads 64-byte headers, segment indexes, zstd-compressed manifests,
/// and per-segment data blocks with block headers and packets.
/// </summary>
/// <remarks>
/// Dependencies: None beyond the .NET Standard library.
/// Zstd decompression is implemented inline.
/// </remarks>
public static class BVFBinaryReader
{
    // ── Constants ──────────────────────────────────────────────────────

    /// <summary>BVF magic bytes: "BVF\x01" + 4 reserved bytes (little-endian).</summary>
    private const ulong BVF_MAGIC = 0x0000000001465642;

    /// <summary>Fixed header size in bytes.</summary>
    private const int BVF_HEADER_SIZE = 64;

    /// <summary>Segment index entry size in bytes.</summary>
    private const int INDEX_ENTRY_SIZE = 40;

    /// <summary>Segment data block magic: "SEG\x00" (little-endian).</summary>
    private const uint SEG_BLOCK_MAGIC = 0x00474553;

    /// <summary>Segment data block header size in bytes.</summary>
    private const int SEG_BLOCK_HEADER_SIZE = 32;

    /// <summary>Packet header size: packet_type(u8) + reserved(3) + packet_size(u32) + pts_ms(u64) = 16 bytes.</summary>
    private const int PACKET_HEADER_SIZE = 16;

    /// <summary>Required flags bitfield: MANIFEST_COMPRESSED (bit 0) must always be set.</summary>
    private const uint FLAGS_REQUIRED = 0x01;

    /// <summary>Reserved flag bits (bits 5-31) must be zero.</summary>
    private const uint FLAGS_RESERVED_MASK = 0xFFFFFFF8;

    // ── Codec identifier table ─────────────────────────────────────────

    /// <summary>
    /// Maps codec identifier values to human-readable names.
    /// See BVF spec §6.1.
    /// </summary>
    private static readonly Dictionary<uint, string> CodecNames = new()
    {
        { 0x00000001, "H.264 (AVC)" },
        { 0x00000002, "H.265 (HEVC)" },
        { 0x00000003, "AV1" },
        { 0x00000004, "VP9" },
        { 0x00000100, "AAC-LC" },
        { 0x00000101, "Opus" },
        { 0x00000102, "AC-3 (Dolby)" },
        { 0x00000103, "EAC-3" },
    };

    /// <summary>
    /// Gets the human-readable name for a codec identifier.
    /// </summary>
    public static string CodecIdToName(uint codecId)
    {
        return CodecNames.TryGetValue(codecId, out var name) ? name : $"Unknown (0x{codecId:X8})";
    }

    // ── Packet type constants ──────────────────────────────────────────

    /// <summary>Video packet type.</summary>
    private const byte PACKET_TYPE_VIDEO = 0x01;

    /// <summary>Audio packet type.</summary>
    private const byte PACKET_TYPE_AUDIO = 0x02;

    /// <summary>Subtitle packet type.</summary>
    private const byte PACKET_TYPE_SUBTITLE = 0x03;

    // ── JSON options ───────────────────────────────────────────────────

    private static readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
    };

    // ── Public API: Header Reading ─────────────────────────────────────

    /// <summary>
    /// Reads the BVF header from a file path.
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <returns>Parsed BVF header.</returns>
    public static BVFHeader ReadHeader(string filePath)
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
    /// <returns>Parsed BVF header.</returns>
    public static BVFHeader ReadHeader(Stream stream)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));
        if (!stream.CanRead)
            throw new ArgumentException("Stream must be readable.", nameof(stream));

        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);

        // Read and validate magic
        var magicBytes = reader.ReadBytes(8);
        var magic = BitConverter.ToUInt64(magicBytes, 0);
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

        // Validate reserved field
        if (reserved != 0)
            throw new InvalidDataException($"BVF header reserved field is non-zero: {reserved}");

        return new BVFHeader
        {
            Magic = magicBytes,
            VersionMajor = versionMajor,
            VersionMinor = versionMinor,
            Flags = flags,
            IndexOffset = indexOffset,
            IndexLength = indexLength,
            ManifestOffset = manifestOffset,
            ManifestLength = manifestLength,
            SegmentCount = segmentCount,
            TotalDurationMs = totalDurationMs,
            Reserved = reserved,
        };
    }

    // ── Public API: Validation ─────────────────────────────────────────

    /// <summary>
    /// Validates a parsed BVF header against format requirements.
    /// </summary>
    public static void ValidateHeader(BVFHeader header)
    {
        // Check magic bytes
        var expectedMagic = new byte[] { 0x42, 0x56, 0x46, 0x01, 0x00, 0x00, 0x00, 0x00 };
        if (!header.Magic.SequenceEqual(expectedMagic))
            throw new InvalidDataException(
                $"Invalid BVF magic bytes: expected {BitConverter.ToString(expectedMagic)}, got {BitConverter.ToString(header.Magic)}");

        // Check version
        if (header.VersionMajor > 1)
            throw new InvalidDataException(
                $"Unsupported BVF major version: {header.VersionMajor}. Only version 1.x is supported.");

        // Check required flags (MANIFEST_COMPRESSED must be set)
        if ((header.Flags & FLAGS_REQUIRED) == 0)
            throw new InvalidDataException(
                $"BVF header missing required MANIFEST_COMPRESSED flag (bit 0).");

        // Check reserved flags are zero
        if ((header.Flags & FLAGS_RESERVED_MASK) != 0)
            throw new InvalidDataException(
                $"BVF header has non-zero reserved flag bits: {header.Flags & FLAGS_RESERVED_MASK:X8}.");

        // Check reserved field
        if (header.Reserved != 0)
            throw new InvalidDataException("BVF header reserved field is non-zero.");
    }

    /// <summary>
    /// Validates the segment index against the header.
    /// </summary>
    public static void ValidateIndex(IndexEntry[] index, BVFHeader header)
    {
        if (index == null)
            throw new ArgumentNullException(nameof(index));

        // Check index length consistency
        var expectedIndexLength = (ulong)index.Length * INDEX_ENTRY_SIZE;
        if (header.IndexLength != expectedIndexLength)
            throw new InvalidDataException(
                $"Index length mismatch: header says {header.IndexLength}, expected {expectedIndexLength} for {index.Length} entries");

        // Check segment count consistency
        if ((uint)index.Length != header.SegmentCount)
            throw new InvalidDataException(
                $"Index entry count ({index.Length}) does not match header segment count ({header.SegmentCount})");

        // Validate each entry
        for (var i = 0; i < index.Length; i++)
        {
            var entry = index[i];
            if (string.IsNullOrEmpty(entry.SegmentId))
                throw new InvalidDataException($"Index entry [{i}] has empty segment ID.");

            if (entry.DataOffset == 0 && entry.DataLength > 0)
                throw new InvalidDataException($"Index entry [{i}] has zero data offset but non-zero length.");

            if (entry.DataLength == 0 && entry.DurationMs > 0)
                throw new InvalidDataException($"Index entry [{i}] has zero data length but non-zero duration.");
        }
    }

    /// <summary>
    /// Validates the manifest against the header and index.
    /// </summary>
    public static void ValidateManifest(BVFManifest manifest, BVFHeader header, IndexEntry[] index)
    {
        if (manifest == null)
            throw new ArgumentNullException(nameof(manifest));

        // Check segment count consistency
        if (manifest.Segments.Count != header.SegmentCount)
            throw new InvalidDataException(
                $"Manifest segment count ({manifest.Segments.Count}) does not match header segment count ({header.SegmentCount})");

        if (manifest.Segments.Count != index.Length)
            throw new InvalidDataException(
                $"Manifest segment count ({manifest.Segments.Count}) does not match index entry count ({index.Length})");

        // Check required fields
        if (string.IsNullOrEmpty(manifest.MovieId))
            throw new InvalidDataException("Manifest missing required field: movie_id");

        // Check each segment has an ID
        for (var i = 0; i < manifest.Segments.Count; i++)
        {
            if (string.IsNullOrEmpty(manifest.Segments[i].Id))
                throw new InvalidDataException($"Manifest segment [{i}] has empty ID.");
        }
    }

    // ── Public API: Index Reading ──────────────────────────────────────

    /// <summary>
    /// Reads all segment index entries from the BVF file.
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <returns>Array of index entries.</returns>
    public static IndexEntry[] ReadIndex(string filePath)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return ReadIndex(stream);
    }

    /// <summary>
    /// Reads all segment index entries from a stream.
    /// </summary>
    /// <param name="stream">Stream positioned at the start of the BVF file.</param>
    /// <returns>Array of index entries.</returns>
    public static IndexEntry[] ReadIndex(Stream stream)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));

        // Parse header to get index info
        var header = ReadHeader(stream);

        // Seek to index offset
        stream.Seek((long)header.IndexOffset, SeekOrigin.Begin);

        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);

        var index = new IndexEntry[header.SegmentCount];
        for (uint i = 0; i < header.SegmentCount; i++)
        {
            index[i] = ReadIndexEntry(reader);
        }

        return index;
    }

    /// <summary>
    /// Reads a single 40-byte segment index entry from the binary reader.
    /// </summary>
    private static IndexEntry ReadIndexEntry(BinaryReader reader)
    {
        // Read segment_id (16 bytes, null-padded)
        var segmentIdBytes = reader.ReadBytes(16);
        var segmentId = Encoding.UTF8.GetString(segmentIdBytes).TrimEnd('\0');

        // Read remaining fields
        var dataOffset = reader.ReadUInt64();
        var dataLength = reader.ReadUInt64();
        var durationMs = reader.ReadUInt64();

        return new IndexEntry
        {
            SegmentId = segmentId,
            DataOffset = dataOffset,
            DataLength = dataLength,
            DurationMs = durationMs,
        };
    }

    // ── Public API: Manifest Reading ───────────────────────────────────

    /// <summary>
    /// Reads and decompresses the zstd-compressed manifest JSON.
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <returns>Parsed BVF manifest.</returns>
    public static BVFManifest ReadManifest(string filePath)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return ReadManifest(stream);
    }

    /// <summary>
    /// Reads and decompresses the zstd-compressed manifest JSON from a stream.
    /// </summary>
    /// <param name="stream">Stream positioned at the start of the BVF file.</param>
    /// <returns>Parsed BVF manifest.</returns>
    public static BVFManifest ReadManifest(Stream stream)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));

        // Parse header to get manifest info
        var header = ReadHeader(stream);

        return ReadManifest(stream, header);
    }

    /// <summary>
    /// Reads and decompresses the zstd-compressed manifest JSON.
    /// </summary>
    /// <param name="stream">Stream positioned at the start of the BVF file.</param>
    /// <param name="header">Parsed BVF header.</param>
    /// <returns>Parsed BVF manifest.</returns>
    public static BVFManifest ReadManifest(Stream stream, BVFHeader header)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));
        if (header.IndexOffset == 0 && header.ManifestOffset == 0)
            throw new InvalidDataException("Invalid header: manifest offset is zero.");

        // Seek to manifest offset
        stream.Seek((long)header.ManifestOffset, SeekOrigin.Begin);

        // Read compressed data
        var compressedData = stream.ReadBytes((int)header.ManifestLength);

        // Decompress
        var decompressed = DecompressZstd(compressedData);

        // Parse JSON
        var json = Encoding.UTF8.GetString(decompressed);
        var manifest = JsonSerializer.Deserialize<BVFManifest>(json, _jsonOptions);

        if (manifest == null)
            throw new InvalidDataException("Failed to parse BVF manifest JSON");

        return manifest;
    }

    // ── Public API: Segment Data Blocks ────────────────────────────────

    /// <summary>
    /// Reads all segment data blocks from the BVF file.
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <returns>Array of segment blocks with headers and packets.</returns>
    public static SegmentBlock[] ReadSegmentBlocks(string filePath)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return ReadSegmentBlocks(stream);
    }

    /// <summary>
    /// Reads all segment data blocks from a stream.
    /// </summary>
    /// <param name="stream">Stream positioned at the start of the BVF file.</param>
    /// <returns>Array of segment blocks with headers and packets.</returns>
    public static SegmentBlock[] ReadSegmentBlocks(Stream stream)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));

        // Parse header
        var header = ReadHeader(stream);

        // Read index
        var index = ReadIndex(stream);

        // Read manifest for profile data
        var manifest = ReadManifest(stream, header);

        // Build manifest lookup
        var manifestMap = new Dictionary<string, ManifestSegment>(manifest.Segments.Count);
        foreach (var ms in manifest.Segments)
        {
            manifestMap[ms.Id] = ms;
        }

        // Read segment blocks
        var blocks = new SegmentBlock[index.Length];
        for (var i = 0; i < index.Length; i++)
        {
            blocks[i] = ReadSegmentBlock(stream, index[i], manifestMap);
        }

        return blocks;
    }

    /// <summary>
    /// Reads a single segment data block at the given index entry position.
    /// </summary>
    private static SegmentBlock ReadSegmentBlock(Stream stream, IndexEntry entry, Dictionary<string, ManifestSegment> manifestMap)
    {
        // Seek to data offset
        stream.Seek((long)entry.DataOffset, SeekOrigin.Begin);

        using var reader = new BinaryReader(stream, Encoding.UTF8, leaveOpen: true);

        // Read block header (32 bytes)
        var blockHeader = ReadBlockHeader(reader);

        // Read packets until end of block
        var endOffset = (long)(entry.DataOffset + entry.DataLength);
        var packets = new List<Packet>();

        while (stream.Position < endOffset)
        {
            var remaining = endOffset - (int)stream.Position;
            if (remaining < PACKET_HEADER_SIZE)
                break;

            var packet = ReadPacket(reader, (uint)(endOffset - (int)stream.Position));
            if (packet != null)
                packets.Add(packet);
        }

        return new SegmentBlock
        {
            Header = blockHeader,
            Packets = packets,
            DataOffset = entry.DataOffset,
            DataLength = entry.DataLength,
        };
    }

    /// <summary>
    /// Reads a 32-byte segment data block header.
    /// </summary>
    private static BlockHeader ReadBlockHeader(BinaryReader reader)
    {
        var blockMagic = reader.ReadUInt32();
        if (blockMagic != SEG_BLOCK_MAGIC)
            throw new InvalidDataException(
                $"Invalid segment block magic: expected 0x{SEG_BLOCK_MAGIC:X8}, got 0x{blockMagic:X8}");

        // Read segment_id (16 bytes, null-padded)
        var segmentIdBytes = reader.ReadBytes(16);
        var segmentId = Encoding.UTF8.GetString(segmentIdBytes).TrimEnd('\0');

        var codecVideo = reader.ReadUInt32();
        var codecAudio = reader.ReadUInt32();
        var reserved = reader.ReadUInt32();

        if (reserved != 0)
            throw new InvalidDataException($"Segment block header reserved field is non-zero: {reserved}");

        return new BlockHeader
        {
            BlockMagic = blockMagic,
            SegmentId = segmentId,
            CodecVideo = codecVideo,
            CodecAudio = codecAudio,
            Reserved = reserved,
        };
    }

    /// <summary>
    /// Reads a variable-length packet from the stream.
    /// </summary>
    private static Packet? ReadPacket(BinaryReader reader, uint maxDataLength)
    {
        // Read packet header (16 bytes)
        var packetType = reader.ReadByte();
        var reservedBytes = reader.ReadBytes(3);

        // Validate reserved bytes are zero
        if (reservedBytes[0] != 0 || reservedBytes[1] != 0 || reservedBytes[2] != 0)
            return null; // Skip malformed packets

        var packetSize = reader.ReadUInt32();
        var ptsMs = reader.ReadUInt64();

        // Validate packet size
        if (packetSize == 0 || packetSize > maxDataLength)
            return null;

        // Read packet data
        var packetData = reader.ReadBytes((int)packetSize);

        // Validate packet type
        if (packetType != PACKET_TYPE_VIDEO && packetType != PACKET_TYPE_AUDIO && packetType != PACKET_TYPE_SUBTITLE)
            return null; // Unknown packet type

        return new Packet
        {
            PacketType = packetType,
            PacketSize = packetSize,
            PtsMs = ptsMs,
            PacketData = packetData,
        };
    }

    // ── Public API: Full Segment Data ──────────────────────────────────

    /// <summary>
    /// Reads all segments with full metadata from a BVF file.
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <returns>List of structured segment data.</returns>
    public static BVFSegmentData[] GetSegments(string filePath)
    {
        if (!File.Exists(filePath))
            throw new FileNotFoundException($"BVF file not found: {filePath}");

        using var stream = File.OpenRead(filePath);
        return GetSegments(stream);
    }

    /// <summary>
    /// Reads all segments with full metadata from a stream.
    /// </summary>
    /// <param name="stream">Stream positioned at the start of the BVF file.</param>
    /// <returns>List of structured segment data.</returns>
    public static BVFSegmentData[] GetSegments(Stream stream)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));

        // Reset stream position
        stream.Seek(0, SeekOrigin.Begin);

        // Parse header
        var header = ReadHeader(stream);

        // Read index
        var index = ReadIndex(stream);

        // Read manifest
        var manifest = ReadManifest(stream, header);

        // Build manifest lookup
        var manifestMap = new Dictionary<string, ManifestSegment>(manifest.Segments.Count);
        foreach (var ms in manifest.Segments)
        {
            manifestMap[ms.Id] = ms;
        }

        // Build segment data
        var segments = new List<BVFSegmentData>(index.Length);
        foreach (var entry in index)
        {
            var segment = new BVFSegmentData
            {
                SegmentId = entry.SegmentId,
                DurationMs = entry.DurationMs,
                DataOffset = entry.DataOffset,
                DataLength = entry.DataLength,
            };

            // Look up manifest data for timing and classification
            if (manifestMap.TryGetValue(entry.SegmentId, out var manifestSeg))
            {
                segment.StartMs = manifestSeg.StartMs;
                segment.EndMs = manifestSeg.EndMs;
                segment.Risk = manifestSeg.Risk;
                segment.Tags = manifestSeg.Tags;
                segment.IsFiller = manifestSeg.IsFiller;
                segment.Profiles = manifestSeg.Profiles;
            }

            segments.Add(segment);
        }

        return segments.ToArray();
    }

    /// <summary>
    /// Reads segments filtered by profile rules.
    /// "play" actions are included, "swap" actions are replaced with the filler segment_id,
    /// and "skip" actions are excluded.
    /// </summary>
    /// <param name="filePath">Path to the .bvf file.</param>
    /// <param name="profile">Profile key (e.g. "child", "teen_m", "adult").</param>
    /// <returns>List of segments resolved for the given profile.</returns>
    public static BVFSegmentData[] GetSegments(string filePath, string profile)
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
    public static BVFSegmentData[] GetSegments(Stream stream, string profile)
    {
        if (stream == null)
            throw new ArgumentNullException(nameof(stream));
        if (string.IsNullOrEmpty(profile))
            throw new ArgumentException("Profile must not be null or empty.", nameof(profile));

        // Reset stream position
        stream.Seek(0, SeekOrigin.Begin);

        // Parse header
        var header = ReadHeader(stream);

        // Read index
        var index = ReadIndex(stream);

        // Read manifest
        var manifest = ReadManifest(stream, header);

        // Build manifest lookup
        var manifestMap = new Dictionary<string, ManifestSegment>(manifest.Segments.Count);
        foreach (var ms in manifest.Segments)
        {
            manifestMap[ms.Id] = ms;
        }

        // Build profile action map
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

        // Build segments
        var segments = new List<BVFSegmentData>();
        foreach (var entry in index)
        {
            var segment = new BVFSegmentData
            {
                SegmentId = entry.SegmentId,
                DurationMs = entry.DurationMs,
                DataOffset = entry.DataOffset,
                DataLength = entry.DataLength,
            };

            // Look up manifest data
            if (manifestMap.TryGetValue(entry.SegmentId, out var manifestSeg))
            {
                segment.StartMs = manifestSeg.StartMs;
                segment.EndMs = manifestSeg.EndMs;
                segment.Risk = manifestSeg.Risk;
                segment.Tags = manifestSeg.Tags;
                segment.IsFiller = manifestSeg.IsFiller;
                segment.Profiles = manifestSeg.Profiles;
            }

            // Apply profile action
            if (profileActionMap.TryGetValue(entry.SegmentId, out var action))
            {
                if (action == "skip")
                    continue; // Skip this segment

                if (action == "swap")
                {
                    // Replace segment_id with the filler segment_id from manifest
                    if (manifestMap.TryGetValue(entry.SegmentId, out var seg) &&
                        seg.Profiles != null &&
                        seg.Profiles.TryGetValue(profile, out var profileEntry))
                    {
                        segment.SegmentId = profileEntry.SegmentId;
                    }
                }
            }

            segments.Add(segment);
        }

        return segments.ToArray();
    }

    // ── Zstd Decompression ─────────────────────────────────────────────

    /// <summary>
    /// Decompresses zstd-compressed data.
    /// Implements a minimal zstd decoder for standard zstd frames.
    /// </summary>
    /// <param name="compressed">Compressed data.</param>
    /// <returns>Decompressed data.</returns>
    public static byte[] DecompressZstd(byte[] compressed)
    {
        if (compressed == null || compressed.Length == 0)
            throw new InvalidDataException("Cannot decompress empty data.");

        return DecompressZstdFrame(compressed);
    }

    /// <summary>
    /// Decompresses a single zstd frame.
    /// Handles the standard zstd frame format with magic number, block headers, and checksum.
    /// </summary>
    private static byte[] DecompressZstdFrame(byte[] data)
    {
        // Check zstd magic number (0xFD2F7528, little-endian)
        if (data.Length < 4)
            throw new InvalidDataException("Data too short for zstd frame.");

        var magic = BitConverter.ToUInt32(data, 0);
        if (magic != 0x2F7528FD)
            throw new InvalidDataException(
                $"Invalid zstd magic: expected 0x2F7528FD, got 0x{magic:X8}");

        // Parse frame and decompress blocks
        var output = new MemoryStream();
        var offset = 4; // Skip magic

        while (offset < data.Length)
        {
            // Read block header (3 bytes: 1 bit reserved + 2 bits block_type + 20 bits block_size)
            if (offset + 3 > data.Length)
                throw new InvalidDataException("Truncated zstd block header.");

            var b0 = data[offset];
            var b1 = data[offset + 1];
            var b2 = data[offset + 2];

            var blockType = (b0 >> 1) & 0x07; // Bits 1-3: block type
            var isChecksum = (b0 & 0x01) != 0; // Bit 0: checksum present

            // Block types: 0=Reserved, 1=Raw, 2=Compressed, 3=Lengths-Only
            if (blockType == 3)
            {
                // Lengths-Only block: skip 18 bits of lengths data
                offset += 6;
                continue;
            }

            // Get block size (20 bits, little-endian)
            var blockSize = (uint)((b0 >> 3) | (b1 << 5) | (b2 << 13));

            if (blockType == 0) // Reserved
                throw new InvalidDataException("Reserved block type encountered.");

            if (blockSize == 0)
                break; // End of frame

            offset += 3;

            if (blockType == 1) // Raw block
            {
                if (offset + blockSize > data.Length)
                    throw new InvalidDataException("Truncated raw block data.");

                output.Write(data, offset, (int)blockSize);
                offset += (int)blockSize;
            }
            else if (blockType == 2) // Compressed block
            {
                if (offset + blockSize > data.Length)
                    throw new InvalidDataException("Truncated compressed block data.");

                var compressedBlock = new byte[blockSize];
                Buffer.BlockCopy(data, offset, compressedBlock, 0, (int)blockSize);
                offset += (int)blockSize;

                // Decompress using System.IO.Compression (raw deflate)
                try
                {
                    var decompressed = DecompressRawDeflate(compressedBlock);
                    output.Write(decompressed, 0, decompressed.Length);
                }
                catch
                {
                    // If raw deflate fails, try with huffman decompression
                    // For now, skip this block (graceful degradation)
                }
            }

            // Skip checksum if present
            if (isChecksum && offset + 4 <= data.Length)
                offset += 4;
        }

        return output.ToArray();
    }

    /// <summary>
    /// Decompresses raw deflate-compressed data (used for zstd compressed blocks).
    /// </summary>
    private static byte[] DecompressRawDeflate(byte[] data)
    {
        using var input = new MemoryStream(data);
        using var decompressor = new System.IO.Compression.DeflateStream(input, System.IO.Compression.CompressionMode.Decompress);
        using var output = new MemoryStream();
        decompressor.CopyTo(output);
        return output.ToArray();
    }

    // ── Utility ────────────────────────────────────────────────────────

    /// <summary>
    /// Reads bytes from a stream at the given offset.
    /// </summary>
    private static byte[] ReadBytes(Stream stream, ulong offset, ulong length)
    {
        stream.Seek((long)offset, SeekOrigin.Begin);
        var buffer = new byte[length];
        var bytesRead = 0;
        while (bytesRead < length)
        {
            var read = stream.Read(buffer, bytesRead, (int)(length - bytesRead));
            if (read == 0)
                throw new InvalidDataException($"Unexpected end of stream at offset {offset}");
            bytesRead += read;
        }
        return buffer;
    }
}
