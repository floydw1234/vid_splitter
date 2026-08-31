using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Jellyfin.Plugin.SmartBranching.Models;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Serves profile-resolved BVF content as an HLS VOD stream. Segments are read
/// as byte ranges straight out of the BVF container, so no duplicate media files
/// are ever written to disk.
/// </summary>
[ApiController]
[Route("SmartBranching/hls")]
[Authorize]
public sealed class BvfHlsController : ControllerBase
{
    private const string PlaylistContentType = "application/vnd.apple.mpegurl";
    private const string SegmentContentType = "video/mp4";

    private readonly SegmentServer _segmentServer;
    private readonly ILogger<BvfHlsController> _logger;

    public BvfHlsController(SegmentServer segmentServer, ILogger<BvfHlsController> logger)
    {
        _segmentServer = segmentServer;
        _logger = logger;
    }

    /// <summary>
    /// Returns the HLS media playlist for a Smart Branch media source token.
    /// </summary>
    [HttpGet("{token}/main.m3u8")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult GetPlaylist([FromRoute] string token)
    {
        if (!TryResolve(token, out _, out var segments))
            return NotFound();

        var playlist = BvfHlsPlaylistBuilder.Build(segments, BuildQuerySuffix());
        return Content(playlist, PlaylistContentType);
    }

    /// <summary>
    /// Returns the fMP4 initialization segment (ftyp + moov of the first resolved segment).
    /// </summary>
    [HttpGet("{token}/init.mp4")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult GetInitSegment([FromRoute] string token)
    {
        if (!TryResolve(token, out var bvfPath, out var segments) || segments.Count == 0)
            return NotFound();

        var payload = BvfSegmentExtractor.ReadSegmentPayload(bvfPath, segments[0]);
        var (start, length) = Fmp4ConcatHelper.GetInitRange(payload);
        if (length <= 0)
        {
            _logger.LogWarning("BVF segment payload is not fMP4; cannot serve HLS init segment for {Path}", bvfPath);
            return NotFound();
        }

        return File(Slice(payload, start, length), SegmentContentType);
    }

    /// <summary>
    /// Returns one fMP4 media segment (moof + mdat) by playback index.
    /// </summary>
    [HttpGet("{token}/{index:int}.m4s")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult GetMediaSegment([FromRoute] string token, [FromRoute] int index)
    {
        if (!TryResolve(token, out var bvfPath, out var segments))
            return NotFound();

        if (index < 0 || index >= segments.Count)
            return NotFound();

        var payload = BvfSegmentExtractor.ReadSegmentPayload(bvfPath, segments[index]);
        var (start, length) = Fmp4ConcatHelper.GetMediaRange(payload);
        return File(Slice(payload, start, length), SegmentContentType);
    }

    private bool TryResolve(string token, out string bvfPath, out List<ResolvedSegment> segments)
    {
        bvfPath = string.Empty;
        segments = new List<ResolvedSegment>();

        if (Plugin.Instance?.Configuration.Enabled == false)
            return false;

        try
        {
            var (decodedPath, profileKey) = SegmentServer.DecodeToken(token);
            if (!System.IO.File.Exists(decodedPath))
                return false;

            bvfPath = decodedPath;
            segments = _segmentServer.ResolveSegmentsForProfile(decodedPath, profileKey);
            return segments.Count > 0;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to resolve HLS token {Token}", token);
            return false;
        }
    }

    /// <summary>
    /// hls.js does not propagate the playlist's query string to segment requests,
    /// so the api_key must be baked into every URI the playlist emits.
    /// </summary>
    private string BuildQuerySuffix()
    {
        var apiKey = Request.Query["api_key"].FirstOrDefault()
            ?? Request.Query["ApiKey"].FirstOrDefault();
        return string.IsNullOrEmpty(apiKey)
            ? string.Empty
            : "?api_key=" + Uri.EscapeDataString(apiKey);
    }

    private static byte[] Slice(byte[] payload, long start, long length)
    {
        if (start == 0 && length == payload.Length)
            return payload;

        var slice = new byte[length];
        Array.Copy(payload, start, slice, 0, length);
        return slice;
    }
}
