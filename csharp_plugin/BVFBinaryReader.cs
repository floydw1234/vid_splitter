using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Reads and parses Branched Video Format (.bvf) binary files.
/// 
/// BVF Specification:
/// - 64-byte file header with magic, version, flags, offsets
/// - Segment index (40-byte entries, O(1) random access)
/// - Zstd-compressed manifest JSON
/// - Segment data blocks (SEG\x00 header + codec packets)
/// </summary>
public static class BVFBinaryReader
{
    // Magic bytes
    private const string MagicString = "BVF\x01";
    private static readonly byte[] MagicBytes = { 0x42, 0x56, 0x46, 0x01, 0x00, 0x00, 0x00, 0x00 };
    private const string SegmentMagic = "SEG\x00";
    private static readonly byte[] SegmentMagicBytes = { 0x53, 0x45, 0x47, 0x00 };

    // File header constants
    private const int FileHeaderSize = 64;
    private const int IndexEntrySize = 40;

    // Block header constants
    private const int BlockHeaderSize = 32;

    // Flag bits
    private const uint ManifestCompressedFlag = 0x00000001;
    private const uint HasChaptersFlag = 0x00000002;
    private const uint HasSubtitlesFlag = 0x00000004;
    private const uint SeekableFlag = 0x00000008;

    // Codec identifiers
    public static class Codecs
    {
        // Video codecs
        public const uint H264 = 0x00000001;
        public const uint H265 = 0x00000002;
        public const uint AV1 = 0x00000003;
        public const uint VP9 = 0x00000004;

        // Audio codecs
        public const uint AAC_LC = 0x00000100;
        public const uint Opus = 0x00000101;
        public const uint AC3 = 0x00000102;
        public const uint EAC3 = 0x00000103;

        // Packet types
        public const byte PacketTypeVideo = 0x01;
        public const byte PacketTypeAudio = 0x02;
        public const byte PacketTypeSubtitle = 0x03;

        /// <summary>
        /// Human-readable codec name from identifier.
        /// </summary>
        public static string GetCodecName(uint codecId)
        {
            return codecId switch
            {
                H264 => "H.264 (AVC)",
                H265 => "H.265 (HEVC)",
                AV1 => "AV1",
                VP9 => "VP9",
                AAC_LC => "AAC-LC",
                Opus => "Opus",
                AC3 => "AC-3 (Dolby)",
                EAC3 => "EAC-3",
                _ => $"Unknown (0x{codecId:X8})"
            };
        }
    }

    /// <summary>
    /// BVF file header (64 bytes, fixed).
    /// </summary>
    public class BvfFileHeader
    {
        public string Magic { get; set; } = string.Empty;
        public int VersionMajor { get; set; }
        public int VersionMinor { get; set; }
        public uint Flags { get; set; }
        public ulong IndexOffset { get; set; }
        public ulong IndexLength { get; set; }
        public ulong ManifestOffset { get; set; }
        public ulong ManifestLength { get; set; }
        public uint SegmentCount { get; set; }
        public ulong TotalDurationMs { get; set; }
        public uint Reserved { get; set; }

        public bool ManifestIsCompressed => (Flags & ManifestCompressedFlag) != 0;
        public bool HasChapters => (Flags & HasChaptersFlag) != 0;
        public bool HasSubtitles => (Flags & HasSubtitlesFlag) != 0;
        public bool Seekable => (Flags & SeekableFlag) != 0;
    }

    /// <summary>
    /// Segment index entry (40 bytes each).
    /// </summary>
    public class IndexEntry
    {
        public string SegmentId { get; set; } = string.Empty;
        public ulong DataOffset { get; set; }
        public ulong DataLength { get; set; }
        public ulong DurationMs { get; set; }
    }

    /// <summary>
    /// Segment data block header (32 bytes).
    /// </summary>
    public class SegmentBlockHeader
    {
        public string BlockMagic { get; set; } = string.Empty;
        public string SegmentId { get; set; } = string.Empty;
        public uint CodecVideo { get; set; }
        public uint CodecAudio { get; set; }
        public uint Reserved { get; set; }
    }

    /// <summary>
    /// A single packet within a segment data block.
    /// </summary>
    public class PacketData
    {
        public byte PacketType { get; set; }
        public uint PacketSize { get; set; }
        public ulong PtMs { get; set; }
        public byte[] Data { get; set; } = Array.Empty<byte>();

        public bool IsVideo => PacketType == Codecs.PacketTypeVideo;
        public bool IsAudio => PacketType == Codecs.PacketTypeAudio;
        public bool IsSubtitle => PacketType == Codecs.PacketTypeSubtitle;
    }

    /// <summary>
    /// A complete segment data block with header and packets.
    /// </summary>
    public class SegmentBlock
    {
        public SegmentBlockHeader Header { get; set; } = new();
        public List<PacketData> Packets { get; set; } = new();
        public ulong DataOffset { get; set; }
        public ulong DataLength { get; set; }
    }

    private FileStream? _fileStream;
    private BvfFileHeader? _header;
    private List<IndexEntry>? _indexEntries;
    private string? _manifestJson;
    private List<SegmentBlock>? _segmentBlocks;

    /// <summary>
    /// Opens a BVF file for reading.
    /// </summary>
    public static BvfFileHeader ReadHeader(string path)
    {
        using var fs = File.OpenRead(path);
        return ReadHeaderFromStream(fs);
    }

    private static BvfFileHeader ReadHeaderFromStream(Stream stream)
    {
        var header = new BvfFileHeader();

        // Read magic (8 bytes)
        var magicBytes = new byte[8];
        ReadExactly(stream, magicBytes, 0, 8);
        header.Magic = Encoding.ASCII.GetString(magicBytes.Take(4).ToArray());
        if (header.Magic != MagicString)
            throw new InvalidDataException(
                $"Invalid BVF magic: expected '{MagicString}', got '{header.Magic}'");

        // Validate reserved bytes are zero
        if (magicBytes[4] != 0 || magicBytes[5] != 0 || magicBytes[6] != 0 || magicBytes[7] != 0)
            throw new InvalidDataException("BVF magic reserved bytes are not zero");

        // Version (u16 x2)
        header.VersionMajor = ReadUInt16(stream);
        header.VersionMinor = ReadUInt16(stream);

        if (header.VersionMajor != 1)
            throw new InvalidDataException($"Unsupported BVF major version: {header.VersionMajor}");

        // Flags (u32)
        header.Flags = ReadUInt32(stream);

        // Offsets and lengths (u64 x4)
        header.IndexOffset = ReadUInt64(stream);
        header.IndexLength = ReadUInt64(stream);
        header.ManifestOffset = ReadUInt64(stream);
        header.ManifestLength = ReadUInt64(stream);

        // Segment count (u32)
        header.SegmentCount = ReadUInt32(stream);

        // Total duration (u64)
        header.TotalDurationMs = ReadUInt64(stream);

        // Reserved (u32)
        header.Reserved = ReadUInt32(stream);

        return header;
    }

    /// <summary>
    /// Opens and fully reads a BVF file.
    /// </summary>
    public static BVFBinaryReader Open(string path)
    {
        var reader = new BVFBinaryReader();
        reader._fileStream = File.OpenRead(path);
        reader._header = ReadHeaderFromStream(reader._fileStream);

        // Read segment index
        reader._fileStream.Position = reader._header.IndexOffset;
        reader._indexEntries = new List<IndexEntry>((int)reader._header.SegmentCount);
        for (uint i = 0; i < reader._header.SegmentCount; i++)
        {
            var entry = ReadIndexEntry(reader._fileStream);
            reader._indexEntries.Add(entry);
        }

        // Read and decompress manifest
        reader._fileStream.Position = reader._header.ManifestOffset;
        var compressedData = new byte[reader._header.ManifestLength];
        ReadExactly(reader._fileStream, compressedData, 0, (int)reader._header.ManifestLength);
        reader._manifestJson = DecompressZstd(compressedData);

        // Read segment data blocks
        reader._segmentBlocks = new List<SegmentBlock>((int)reader._header.SegmentCount);
        foreach (var entry in reader._indexEntries!)
        {
            reader._fileStream.Position = (long)entry.DataOffset;
            var block = ReadSegmentBlock(reader._fileStream, entry);
            reader._segmentBlocks.Add(block);
        }

        return reader;
    }

    /// <summary>
    /// Reads the file header (can be called multiple times).
    /// </summary>
    public BvfFileHeader GetHeader() => _header ?? throw new InvalidOperationException("File not opened");

    /// <summary>
    /// Reads the segment index entries.
    /// </summary>
    public List<IndexEntry> GetIndexEntries() => _indexEntries ?? throw new InvalidOperationException("File not opened");

    /// <summary>
    /// Reads the decompressed manifest JSON.
    /// </summary>
    public string GetManifestJson() => _manifestJson ?? throw new InvalidOperationException("File not opened");

    /// <summary>
    /// Reads all segment data blocks.
    /// </summary>
    public List<SegmentBlock> GetSegmentBlocks() => _segmentBlocks ?? throw new InvalidOperationException("File not opened");

    /// <summary>
    /// Reads a specific segment block by index.
    /// </summary>
    public SegmentBlock GetSegmentBlock(int index)
    {
        if (_segmentBlocks == null)
            throw new InvalidOperationException("File not opened");
        if (index < 0 || index >= _segmentBlocks.Count)
            throw new ArgumentOutOfRangeException(nameof(index), $"Segment index {index} out of range [0, {_segmentBlocks.Count - 1}]");
        return _segmentBlocks[index];
    }

    /// <summary>
    /// Gets the index entry for a specific segment.
    /// </summary>
    public IndexEntry GetIndexEntry(string segmentId)
    {
        if (_indexEntries == null)
            throw new InvalidOperationException("File not opened");
        return _indexEntries.FirstOrDefault(e => e.SegmentId == segmentId)
            ?? throw new KeyNotFoundException($"Segment '{segmentId}' not found in index");
    }

    /// <summary>
    /// Gets a segment block by segment ID (looks up in index, then returns block).
    /// </summary>
    public SegmentBlock? GetSegmentBlockByName(string segmentId)
    {
        if (_segmentBlocks == null || _indexEntries == null)
            throw new InvalidOperationException("File not opened");

        var entry = _indexEntries.FirstOrDefault(e => e.SegmentId == segmentId);
        if (entry == null)
            return null;

        var index = _indexEntries.IndexOf(entry);
        return _segmentBlocks[index];
    }

    /// <summary>
    /// Closes the file stream.
    /// </summary>
    public void Close()
    {
        _fileStream?.Close();
        _fileStream = null;
    }

    // ─── Index Entry Parsing ────────────────────────────────────────

    private static IndexEntry ReadIndexEntry(Stream stream)
    {
        var entry = new IndexEntry();

        // Segment ID (16 bytes, null-padded)
        var idBytes = new byte[16];
        ReadExactly(stream, idBytes, 0, 16);
        entry.SegmentId = Encoding.ASCII.GetString(idBytes).TrimEnd('\0');

        // Data offset (u64)
        entry.DataOffset = ReadUInt64(stream);

        // Data length (u64)
        entry.DataLength = ReadUInt64(stream);

        // Duration (u64)
        entry.DurationMs = ReadUInt64(stream);

        return entry;
    }

    // ─── Segment Block Parsing ──────────────────────────────────────

    private static SegmentBlock ReadSegmentBlock(Stream stream, IndexEntry indexEntry)
    {
        var block = new SegmentBlock
        {
            DataOffset = indexEntry.DataOffset,
            DataLength = indexEntry.DataLength
        };

        // Read block header (32 bytes)
        var headerBytes = new byte[BlockHeaderSize];
        ReadExactly(stream, headerBytes, 0, BlockHeaderSize);

        // Block magic (4 bytes)
        var magic = Encoding.ASCII.GetString(headerBytes.Take(4).ToArray());
        if (magic != SegmentMagic)
            throw new InvalidDataException(
                $"Invalid segment block magic: expected 'SEG\\x00', got '{magic}' for segment '{indexEntry.SegmentId}'");

        block.Header.BlockMagic = magic;

        // Segment ID (16 bytes, null-padded)
        var idBytes = headerBytes.Skip(4).Take(16).ToArray();
        block.Header.SegmentId = Encoding.ASCII.GetString(idBytes).TrimEnd('\0');

        // Codec video (u32)
        block.Header.CodecVideo = ReadUInt32FromBytes(headerBytes, 20);

        // Codec audio (u32)
        block.Header.CodecAudio = ReadUInt32FromBytes(headerBytes, 24);

        // Reserved (u32)
        block.Header.Reserved = ReadUInt32FromBytes(headerBytes, 28);

        // Read packets until end of block
        var remainingBytes = (int)indexEntry.DataLength - BlockHeaderSize;
        while (remainingBytes > 0)
        {
            var packet = ReadPacket(stream, remainingBytes);
            block.Packets.Add(packet);
            remainingBytes -= (int)packet.PacketSize + 16; // header (16 bytes) + data
        }

        return block;
    }

    private static PacketData ReadPacket(Stream stream, int maxRemaining)
    {
        var packet = new PacketData();

        // Packet type (u8)
        packet.PacketType = ReadByte(stream);

        // Reserved (u24 = 3 bytes)
        var reserved = new byte[3];
        ReadExactly(stream, reserved, 0, 3);

        // Packet size (u32)
        packet.PacketSize = ReadUInt32(stream);

        // PTS (u64)
        packet.PtMs = ReadUInt64(stream);

        // Packet data (N bytes)
        packet.Data = new byte[packet.PacketSize];
        if (packet.PacketSize > 0)
        {
            ReadExactly(stream, packet.Data, 0, (int)packet.PacketSize);
        }

        return packet;
    }

    // ─── Zstd Decompression ─────────────────────────────────────────

    private static string DecompressZstd(byte[] compressedData)
    {
        // Try using the system zstd command-line tool as a fallback
        // This is a simple approach for the plugin context
        try
        {
            var result = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = "zstd",
                Arguments = $"-d -c",
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true
            })!;

            using var input = result.StandardInput;
            using var output = result.StandardOutput;

            input.Write(compressedData, 0, compressedData.Length);
            input.Close();

            var decompressed = new byte[compressedData.Length * 10]; // generous initial buffer
            var bytesRead = 0;
            int read;
            while ((read = output.Read(decompressed, bytesRead, decompressed.Length - bytesRead)) > 0)
            {
                bytesRead += read;
                if (bytesRead == decompressed.Length)
                {
                    // Resize buffer
                    var newBuffer = new byte[decompressed.Length * 2];
                    Array.Copy(decompressed, newBuffer, decompressed.Length);
                    decompressed = newBuffer;
                }
            }

            result.WaitForExit();
            return Encoding.UTF8.GetString(decompressed, 0, bytesRead);
        }
        catch
        {
            // If zstd CLI isn't available, try a pure C# approach
            // This is a simplified implementation — in production, use a proper zstd library
            return DecompressZstdManaged(compressedData);
        }
    }

    /// <summary>
    /// Fallback zstd decompression using a simple approach.
    /// Note: This is a placeholder — in production, use the libzstd NuGet package.
    /// For now, we assume the manifest is stored uncompressed if zstd isn't available.
    /// </summary>
    private static string DecompressZstdManaged(byte[] data)
    {
        // Check if data is actually already decompressed (common during development/testing)
        if (data.Length > 0 && data[0] == '{')
            return Encoding.UTF8.GetString(data);

        // If zstd magic is present (0x28 0xB5 0x2F), attempt decompression
        // For a production plugin, this should use the official zstd library
        if (data.Length >= 4 && data[0] == 0x28 && data[1] == 0xB5 && data[2] == 0x2F)
        {
            // Try using the system zstd via Process
            try
            {
                var result = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "zstd",
                    Arguments = $"-d -c",
                    RedirectStandardInput = true,
                    RedirectStandardOutput = true,
                    UseShellExecute = false,
                    CreateNoWindow = true
                })!;

                using var input = result.StandardInput;
                using var output = result.StandardOutput;

                input.Write(data, 0, data.Length);
                input.Close();

                var decompressed = new byte[data.Length * 10];
                var bytesRead = 0;
                int read;
                while ((read = output.Read(decompressed, bytesRead, decompressed.Length - bytesRead)) > 0)
                {
                    bytesRead += read;
                    if (bytesRead == decompressed.Length)
                    {
                        var newBuffer = new byte[decompressed.Length * 2];
                        Array.Copy(decompressed, newBuffer, decompressed.Length);
                        decompressed = newBuffer;
                    }
                }

                result.WaitForExit();
                return Encoding.UTF8.GetString(decompressed, 0, bytesRead);
            }
            catch
            {
                throw new InvalidDataException(
                    "BVF manifest is zstd-compressed but zstd CLI is not available. " +
                    "Install zstd or use the libzstd NuGet package.");
            }
        }

        // Not compressed, return as-is (for testing/development)
        return Encoding.UTF8.GetString(data);
    }

    // ─── Binary Reading Helpers ─────────────────────────────────────

    private static void ReadExactly(Stream stream, byte[] buffer, int offset, int count)
    {
        var totalRead = 0;
        while (totalRead < count)
        {
            var read = stream.Read(buffer, offset + totalRead, count - totalRead);
            if (read == 0)
                throw new EndOfStreamException(
                    $"Expected {count} bytes at offset {offset}, but reached EOF after {totalRead} bytes");
            totalRead += read;
        }
    }

    private static byte ReadByte(Stream stream)
    {
        var b = new byte[1];
        ReadExactly(stream, b, 0, 1);
        return b[0];
    }

    private static ushort ReadUInt16(Stream stream)
    {
        var b = new byte[2];
        ReadExactly(stream, b, 0, 2);
        return (ushort)(b[0] | (b[1] << 8));
    }

    private static uint ReadUInt32(Stream stream)
    {
        var b = new byte[4];
        ReadExactly(stream, b, 0, 4);
        return (uint)(b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24));
    }

    private static uint ReadUInt32FromBytes(byte[] bytes, int offset)
    {
        return (uint)(bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16) | (bytes[offset + 3] << 24));
    }

    private static ulong ReadUInt64(Stream stream)
    {
        var b = new byte[8];
        ReadExactly(stream, b, 0, 8);
        return (ulong)(b[0]
            | ((ulong)b[1] << 8)
            | ((ulong)b[2] << 16)
            | ((ulong)b[3] << 24)
            | ((ulong)b[4] << 32)
            | ((ulong)b[5] << 40)
            | ((ulong)b[6] << 48)
            | ((ulong)b[7] << 56));
    }
}
