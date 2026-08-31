using System.IO;
using MediaBrowser.Controller.Library;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;

namespace Jellyfin.Plugin.SmartBranching;

/// <summary>
/// Serves profile-resolved BVF media as a progressive MP4 stream.
/// </summary>
[ApiController]
[Route("SmartBranching")]
public sealed class BvfStreamController : ControllerBase
{
    private readonly IMediaSourceManager _mediaSourceManager;

    public BvfStreamController(IMediaSourceManager mediaSourceManager)
    {
        _mediaSourceManager = mediaSourceManager;
    }

    /// <summary>
    /// Streams an opened Smart Branch live stream as <c>video/mp4</c>.
    /// </summary>
    [Authorize]
    [HttpGet("{liveStreamId}/stream")]
    [Produces("video/mp4")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult GetStream([FromRoute] string liveStreamId)
    {
        // OpenMediaSource Path uses UniqueId; LiveStreamId is provider-prefixed after open.
        var liveStream = _mediaSourceManager.GetLiveStreamInfoByUniqueId(liveStreamId)
            ?? _mediaSourceManager.GetLiveStreamInfo(liveStreamId);
        if (liveStream is null)
            return NotFound();

        Stream stream = liveStream.GetStream();
        return File(stream, "video/mp4", enableRangeProcessing: true);
    }
}
