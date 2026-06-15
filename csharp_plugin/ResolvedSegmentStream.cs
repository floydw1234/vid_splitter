using System;
using System.Collections.Generic;
using System.IO;
using Jellyfin.Plugin.SmartBranching.Models;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Exposes resolved BVF segment payloads as one logical stream without buffering
/// the full movie into memory.
/// </summary>
public sealed class ResolvedSegmentStream : Stream
{
    private const int AssetBlockHeaderSize = 32;

    private readonly FileStream _fileStream;
    private readonly SegmentSlice[] _segments;
    private readonly long _length;

    private long _position;
    private int _currentSegmentIndex = -1;
    private bool _disposed;

    public ResolvedSegmentStream(string bvfPath, IReadOnlyList<ResolvedSegment> segments)
    {
        if (string.IsNullOrWhiteSpace(bvfPath))
            throw new ArgumentException("BVF path must not be null or empty.", nameof(bvfPath));
        if (segments == null)
            throw new ArgumentNullException(nameof(segments));

        _fileStream = File.OpenRead(bvfPath);
        _segments = BuildSegmentSlices(segments);

        long totalLength = 0;
        foreach (var segment in _segments)
        {
            totalLength += segment.PayloadLength;
        }

        _length = totalLength;
    }

    public override bool CanRead => !_disposed;

    public override bool CanSeek => !_disposed;

    public override bool CanWrite => false;

    public override long Length
    {
        get
        {
            ThrowIfDisposed();
            return _length;
        }
    }

    public override long Position
    {
        get
        {
            ThrowIfDisposed();
            return _position;
        }
        set => Seek(value, SeekOrigin.Begin);
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        ThrowIfDisposed();

        ArgumentNullException.ThrowIfNull(buffer);
        if (offset < 0 || offset > buffer.Length)
            throw new ArgumentOutOfRangeException(nameof(offset));
        if (count < 0 || offset + count > buffer.Length)
            throw new ArgumentOutOfRangeException(nameof(count));
        if (count == 0 || _position >= _length)
            return 0;

        var remaining = (int)Math.Min(count, _length - _position);
        var totalRead = 0;

        while (remaining > 0 && _position < _length)
        {
            var segmentIndex = FindSegmentIndex(_position);
            if (segmentIndex < 0)
                break;

            var segment = _segments[segmentIndex];
            var offsetInSegment = _position - segment.LogicalStart;
            var bytesAvailable = segment.PayloadLength - offsetInSegment;
            var bytesToRead = (int)Math.Min(remaining, bytesAvailable);

            EnsureFilePosition(segment, offsetInSegment, segmentIndex);

            var bytesRead = _fileStream.Read(buffer, offset + totalRead, bytesToRead);
            if (bytesRead <= 0)
                break;

            totalRead += bytesRead;
            remaining -= bytesRead;
            _position += bytesRead;
        }

        return totalRead;
    }

    public override long Seek(long offset, SeekOrigin origin)
    {
        ThrowIfDisposed();

        var target = origin switch
        {
            SeekOrigin.Begin => offset,
            SeekOrigin.Current => _position + offset,
            SeekOrigin.End => _length + offset,
            _ => throw new ArgumentOutOfRangeException(nameof(origin)),
        };

        if (target < 0)
            throw new IOException("Attempted to seek before the beginning of the stream.");

        if (target > _length)
            target = _length;

        _position = target;
        _currentSegmentIndex = -1;
        return _position;
    }

    public override void Flush()
    {
    }

    public override void SetLength(long value) => throw new NotSupportedException();

    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (!_disposed && disposing)
        {
            _fileStream.Dispose();
        }

        _disposed = true;
        base.Dispose(disposing);
    }

    private static SegmentSlice[] BuildSegmentSlices(IReadOnlyList<ResolvedSegment> segments)
    {
        var slices = new SegmentSlice[segments.Count];
        long logicalStart = 0;

        for (var i = 0; i < segments.Count; i++)
        {
            var segment = segments[i];
            if (segment.DataLength < AssetBlockHeaderSize)
                throw new InvalidDataException(
                    $"Resolved segment '{segment.SegmentId}' has invalid data length {segment.DataLength}.");

            var payloadLength = checked((long)segment.DataLength - AssetBlockHeaderSize);
            var payloadOffset = checked((long)segment.DataOffset + AssetBlockHeaderSize);

            slices[i] = new SegmentSlice(logicalStart, payloadOffset, payloadLength);
            logicalStart += payloadLength;
        }

        return slices;
    }

    private void EnsureFilePosition(SegmentSlice segment, long offsetInSegment, int segmentIndex)
    {
        var filePosition = segment.PayloadOffset + offsetInSegment;
        if (_currentSegmentIndex != segmentIndex || _fileStream.Position != filePosition)
        {
            _fileStream.Seek(filePosition, SeekOrigin.Begin);
            _currentSegmentIndex = segmentIndex;
        }
    }

    private int FindSegmentIndex(long logicalPosition)
    {
        for (var i = 0; i < _segments.Length; i++)
        {
            var segment = _segments[i];
            if (logicalPosition >= segment.LogicalStart &&
                logicalPosition < segment.LogicalStart + segment.PayloadLength)
            {
                return i;
            }
        }

        return -1;
    }

    private void ThrowIfDisposed()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
    }

    private readonly record struct SegmentSlice(long LogicalStart, long PayloadOffset, long PayloadLength);
}
