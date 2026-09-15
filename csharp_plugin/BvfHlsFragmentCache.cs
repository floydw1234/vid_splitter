using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// LRU cache of assembled HLS fMP4 parts with read-ahead prefetch (CD-style buffering).
/// </summary>
internal sealed class BvfHlsFragmentCache
{
    /// <summary>How many seconds of media to assemble ahead of the current request.</summary>
    public const double PrefetchAheadSeconds = 5.0;

    private const long DefaultMaxBytes = 128L * 1024 * 1024;

    private readonly object _gate = new();
    private readonly Dictionary<string, LinkedListNode<Entry>> _index = new(StringComparer.Ordinal);
    private readonly LinkedList<Entry> _lru = new();
    private readonly long _maxBytes;
    private long _totalBytes;

    private sealed class Entry
    {
        public required string Key { get; init; }

        public required byte[] Bytes { get; init; }
    }

    public BvfHlsFragmentCache(long maxBytes = DefaultMaxBytes)
    {
        _maxBytes = maxBytes > 0 ? maxBytes : DefaultMaxBytes;
    }

    public static string FragmentKey(string streamKey, int partIndex)
        => streamKey + "|p" + partIndex.ToString(System.Globalization.CultureInfo.InvariantCulture);

    public byte[] GetOrAdd(string key, Func<byte[]> factory)
    {
        lock (_gate)
        {
            if (_index.TryGetValue(key, out var node))
            {
                Touch(node);
                return node.Value.Bytes;
            }
        }

        var bytes = factory();

        lock (_gate)
        {
            if (_index.TryGetValue(key, out var existing))
            {
                Touch(existing);
                return existing.Value.Bytes;
            }

            Insert(key, bytes);
            return bytes;
        }
    }

    /// <summary>
    /// Assembles upcoming parts on a background thread so the next client requests are instant.
    /// </summary>
    public void PrefetchAhead(
        string streamKey,
        BvfHlsTimeline timeline,
        int startPartIndex,
        Func<int, byte[]> assemblePart)
    {
        if (startPartIndex < 0 || startPartIndex >= timeline.Parts.Count)
            return;

        _ = Task.Run(() =>
        {
            try
            {
                var aheadSeconds = 0.0;
                for (var i = startPartIndex; i < timeline.Parts.Count && aheadSeconds < PrefetchAheadSeconds; i++)
                {
                    var key = FragmentKey(streamKey, i);
                    var known = false;
                    lock (_gate)
                        known = _index.ContainsKey(key);

                    if (!known)
                        GetOrAdd(key, () => assemblePart(i));

                    aheadSeconds += timeline.Parts[i].DurationSeconds;
                }
            }
            catch
            {
                // Prefetch is best-effort; playback still works on cache miss.
            }
        }, CancellationToken.None);
    }

    public void Clear()
    {
        lock (_gate)
        {
            _index.Clear();
            _lru.Clear();
            _totalBytes = 0;
        }
    }

    internal int EntryCount
    {
        get
        {
            lock (_gate)
                return _index.Count;
        }
    }

    internal long TotalBytes
    {
        get
        {
            lock (_gate)
                return _totalBytes;
        }
    }

    private void Insert(string key, byte[] bytes)
    {
        var node = _lru.AddFirst(new Entry { Key = key, Bytes = bytes });
        _index[key] = node;
        _totalBytes += bytes.LongLength;
        EvictIfNeeded();
    }

    private void Touch(LinkedListNode<Entry> node)
    {
        if (node.List == null || node.List.First == node)
            return;

        _lru.Remove(node);
        _lru.AddFirst(node);
    }

    private void EvictIfNeeded()
    {
        while (_totalBytes > _maxBytes && _lru.Last != null)
        {
            var tail = _lru.Last;
            _lru.RemoveLast();
            _index.Remove(tail.Value.Key);
            _totalBytes -= tail.Value.Bytes.LongLength;
        }
    }
}
