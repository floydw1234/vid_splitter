namespace Jellyfin.Plugin.SmartBranching
{
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

        /// <summary>Audio hash derived from the segment data.</summary>
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
        /// <param name="audioHash">Audio hash from segment data.</param>
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

        /// <summary>
        /// Creates a new BVFSegment with all fields.
        /// </summary>
        /// <param name="startTime">Start time in seconds.</param>
        /// <param name="endTime">End time in seconds.</param>
        /// <param name="classification">Classification from manifest risk field.</param>
        /// <param name="audioHash">SHA256 hex digest of audio packet data.</param>
        /// <param name="segmentId">Unique segment identifier from the segment index.</param>
        /// <param name="durationMs">Segment duration in milliseconds.</param>
        /// <param name="dataOffset">Offset of the segment data block.</param>
        /// <param name="dataLength">Length of the segment data block in bytes.</param>
        public BVFSegment(float startTime, float endTime, string classification, string audioHash, string segmentId, ulong durationMs, ulong dataOffset, ulong dataLength)
        {
            this.startTime = startTime;
            this.endTime = endTime;
            this.classification = classification;
            this.audioHash = audioHash;
            this.segmentId = segmentId;
            this.durationMs = durationMs;
            this.dataOffset = dataOffset;
            this.dataLength = dataLength;
        }
    }
}
