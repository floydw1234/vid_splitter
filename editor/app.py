"""FastAPI web editor backend."""

from __future__ import annotations

import argparse
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.requests import Request

from editor.detect import DEFAULT_INTERVAL, DEFAULT_THRESHOLD, DEFAULT_TOPICS, run_detection
from editor.export import run_export_with_progress
from editor.ffmpeg_util import ffmpeg_available, probe_video
from editor.jobs import JobRegistry
from editor.project import (
    Project,
    ProjectValidationError,
    ReplacementEntry,
    Segment,
    load_project,
    new_project_id,
    new_segment_id,
    project_dir,
    save_project,
    validate_segment,
    validate_segment_replacements,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm"}
RANGE_CHUNK = 1024 * 1024
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _video_media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "video/mp4"


def _parse_byte_range(range_header: str, size: int) -> tuple[int, int]:
    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="invalid range")
    spec = range_header.removeprefix("bytes=").split("-", 1)
    if len(spec) != 2:
        raise HTTPException(status_code=416, detail="invalid range")
    start_s, end_s = spec[0], spec[1]
    if start_s == "" and end_s:
        suffix = int(end_s)
        if suffix <= 0:
            raise HTTPException(status_code=416, detail="range not satisfiable")
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        raise HTTPException(status_code=416, detail="range not satisfiable")
    return start, end


def _iter_file_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    length = end - start + 1
    with path.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(RANGE_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _ranged_file_response(path: Path, request: Request, media_type: str) -> Response:
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes"}
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(
            path,
            media_type=media_type,
            filename=path.name,
            headers=headers,
        )
    start, end = _parse_byte_range(range_header, size)
    length = end - start + 1
    headers.update(
        {
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        }
    )
    return StreamingResponse(
        _iter_file_range(path, start, end),
        status_code=206,
        media_type=media_type,
        headers=headers,
    )


class DetectBody(BaseModel):
    topics: list[str] = Field(default_factory=lambda: list(DEFAULT_TOPICS))
    threshold: float = DEFAULT_THRESHOLD
    interval: float = DEFAULT_INTERVAL


class CreateFromMediaBody(BaseModel):
    media: str
    name: str | None = None


class SegmentBody(BaseModel):
    start: float
    end: float
    topic: str = ""
    score: float = 0.0
    source: str = "manual"
    status: str = "suggested"
    action: str = "none"
    replacement_clip: str | None = None
    replacement_mode: str = "trim"


class SegmentPatch(BaseModel):
    start: float | None = None
    end: float | None = None
    topic: str | None = None
    score: float | None = None
    status: str | None = None
    action: str | None = None
    replacement_clip: str | None = None
    replacement_mode: str | None = None


def _safe_basename(name: str) -> str:
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="path traversal rejected")
    base = Path(name).name
    if not base or base in {".", ".."}:
        raise HTTPException(status_code=400, detail="invalid path")
    return base


def create_app(data_dir: Path, media_dir: Path) -> FastAPI:
    data_dir = data_dir.expanduser().resolve()
    media_dir = media_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    jobs = JobRegistry()
    app = FastAPI(title="vid_splitter editor")

    @app.exception_handler(FileNotFoundError)
    def _not_found_handler(_request: Request, exc: FileNotFoundError) -> JSONResponse:
        detail = str(exc) or "not found"
        return JSONResponse(status_code=404, content={"detail": detail})

    def _list_project_ids() -> list[str]:
        root = data_dir / "projects"
        if not root.is_dir():
            return []
        return sorted(
            p.name for p in root.iterdir() if (p / "project.json").is_file()
        )

    def _init_project_from_video(
        video_path: Path, name: str | None = None
    ) -> Project:
        if not ffmpeg_available():
            raise HTTPException(status_code=503, detail="ffmpeg/ffprobe required")
        meta = probe_video(video_path)
        pid = new_project_id()
        pdir = project_dir(data_dir, pid)
        pdir.mkdir(parents=True, exist_ok=True)
        ext = video_path.suffix.lower() or ".mp4"
        dest_name = f"source{ext}"
        dest = pdir / dest_name
        if video_path.resolve() != dest.resolve():
            if dest.exists():
                dest.unlink()
            try:
                os.symlink(video_path.resolve(), dest)
            except OSError:
                shutil.copy2(video_path, dest)
        project = Project(
            id=pid,
            name=name or video_path.stem,
            source_video=dest_name,
            duration=float(meta["duration"]),
            fps=float(meta["fps"]),
            width=int(meta["width"]),
            height=int(meta["height"]),
            has_audio=bool(meta["has_audio"]),
        )
        save_project(data_dir, project)
        return project

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        out = []
        for pid in _list_project_ids():
            try:
                p = load_project(data_dir, pid)
                out.append(
                    {
                        "id": p.id,
                        "name": p.name,
                        "duration": p.duration,
                        "width": p.width,
                        "height": p.height,
                    }
                )
            except (ProjectValidationError, FileNotFoundError):
                continue
        return out

    @app.get("/api/media")
    def list_media() -> list[str]:
        names = []
        if media_dir.is_dir():
            for path in sorted(media_dir.iterdir()):
                if path.is_file() and path.suffix.lower() in VIDEO_EXTS:
                    names.append(path.name)
        return names

    @app.post("/api/projects")
    async def create_project(request: Request, file: UploadFile | None = File(None)) -> dict[str, Any]:
        if file is not None and file.filename:
            pid = new_project_id()
            pdir = project_dir(data_dir, pid)
            pdir.mkdir(parents=True, exist_ok=True)
            safe = _safe_basename(file.filename)
            ext = Path(safe).suffix.lower() or ".mp4"
            dest_name = f"source{ext}"
            dest = pdir / dest_name
            with dest.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    out.write(chunk)
            meta = probe_video(dest)
            project = Project(
                id=pid,
                name=Path(safe).stem,
                source_video=dest_name,
                duration=float(meta["duration"]),
                fps=float(meta["fps"]),
                width=int(meta["width"]),
                height=int(meta["height"]),
                has_audio=bool(meta["has_audio"]),
            )
            save_project(data_dir, project)
            return project.to_dict()

        if "application/json" in (request.headers.get("content-type") or ""):
            payload = CreateFromMediaBody.model_validate(await request.json())
            name = _safe_basename(payload.media)
            src = (media_dir / name).resolve()
            if not src.is_file():
                raise HTTPException(status_code=404, detail="media not found")
            try:
                src.relative_to(media_dir.resolve())
            except ValueError:
                raise HTTPException(status_code=400, detail="path traversal rejected")
            project = _init_project_from_video(src, payload.name or Path(name).stem)
            return project.to_dict()

        raise HTTPException(status_code=400, detail="upload file or JSON media required")

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return load_project(data_dir, project_id).to_dict()

    @app.put("/api/projects/{project_id}")
    def put_project(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        stored = load_project(data_dir, project_id)
        if "revision" not in payload:
            raise HTTPException(status_code=400, detail="revision required")
        client_revision = int(payload["revision"])
        if client_revision != stored.revision:
            raise HTTPException(
                status_code=409,
                detail="project changed on server; reload and retry",
            )
        name = payload.get("name")
        if name is not None:
            stored.name = str(name)
        raw_segments = payload.get("segments")
        if raw_segments is not None:
            segments: list[Segment] = []
            for item in raw_segments:
                seg = Segment.from_dict(item)
                validate_segment(seg, stored.duration)
                validate_segment_replacements(seg, stored)
                segments.append(seg)
            stored.segments = segments
        try:
            save_project(data_dir, stored)
        except (ProjectValidationError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return stored.to_dict()

    @app.get("/api/projects/{project_id}/video")
    def get_video(project_id: str, request: Request):
        project = load_project(data_dir, project_id)
        path = project_dir(data_dir, project_id) / project.source_video
        if not path.is_file():
            raise HTTPException(status_code=404, detail="video missing")
        return _ranged_file_response(path, request, _video_media_type(path))

    @app.post("/api/projects/{project_id}/segments")
    def add_segment(project_id: str, body: SegmentBody) -> dict[str, Any]:
        project = load_project(data_dir, project_id)
        seg = Segment(
            id=new_segment_id(),
            start=body.start,
            end=body.end,
            topic=body.topic,
            score=body.score,
            source=body.source,
            status=body.status,
            action=body.action,
            replacement_clip=body.replacement_clip,
            replacement_mode=body.replacement_mode,
        )
        try:
            validate_segment(seg, project.duration)
            validate_segment_replacements(seg, project)
        except ProjectValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        project.segments.append(seg)
        save_project(data_dir, project)
        return seg.to_dict()

    @app.patch("/api/projects/{project_id}/segments/{segment_id}")
    def patch_segment(
        project_id: str, segment_id: str, body: SegmentPatch
    ) -> dict[str, Any]:
        project = load_project(data_dir, project_id)
        seg = next((s for s in project.segments if s.id == segment_id), None)
        if not seg:
            raise HTTPException(status_code=404, detail="segment not found")
        data = seg.to_dict()
        for key, val in body.model_dump(exclude_unset=True).items():
            data[key] = val
        updated = Segment.from_dict({**data, "id": segment_id})
        try:
            validate_segment(updated, project.duration)
            validate_segment_replacements(updated, project)
        except ProjectValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        project.segments = [
            updated if s.id == segment_id else s for s in project.segments
        ]
        save_project(data_dir, project)
        return updated.to_dict()

    @app.delete("/api/projects/{project_id}/segments/{segment_id}")
    def delete_segment(project_id: str, segment_id: str) -> dict[str, str]:
        project = load_project(data_dir, project_id)
        before = len(project.segments)
        project.segments = [s for s in project.segments if s.id != segment_id]
        if len(project.segments) == before:
            raise HTTPException(status_code=404, detail="segment not found")
        save_project(data_dir, project)
        return {"status": "deleted"}

    @app.post("/api/projects/{project_id}/detect")
    def start_detect(project_id: str, body: DetectBody) -> dict[str, str]:
        load_project(data_dir, project_id)
        job = jobs.create()
        busy = jobs.claim_detect_job(job.id)
        if busy:
            raise HTTPException(
                status_code=409,
                detail={"message": "detection already running", "job_id": busy},
            )

        def work() -> None:
            try:
                def on_progress(p: float, msg: str) -> None:
                    jobs.update(job.id, progress=p, message=msg)

                result = run_detection(
                    data_dir,
                    project_id,
                    topics=body.topics,
                    threshold=body.threshold,
                    interval=body.interval,
                    on_progress=on_progress,
                )
                jobs.update(job.id, result=result)
            finally:
                jobs.release_detect_job(job.id)

        jobs.run_in_thread(job.id, work)
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        if not JOB_ID_RE.match(job_id):
            raise HTTPException(status_code=404, detail="job not found")
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return jobs.to_api(job)

    @app.post("/api/projects/{project_id}/replacements")
    async def upload_replacement(
        project_id: str, file: UploadFile = File(...)
    ) -> dict[str, Any]:
        project = load_project(data_dir, project_id)
        safe = _safe_basename(file.filename or "clip.mp4")
        repl_dir = project_dir(data_dir, project_id) / "replacements"
        repl_dir.mkdir(parents=True, exist_ok=True)
        dest = repl_dir / safe
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        meta = probe_video(dest)
        entry = ReplacementEntry(
            name=safe,
            duration=float(meta["duration"]),
            has_audio=bool(meta["has_audio"]),
            width=int(meta["width"]),
            height=int(meta["height"]),
        )
        project.replacements = [r for r in project.replacements if r.name != safe]
        project.replacements.append(entry)
        if safe not in project.replacement_clips:
            project.replacement_clips.append(safe)
        save_project(data_dir, project)
        return {"name": safe, "meta": meta}

    @app.get("/api/projects/{project_id}/replacements/{name}")
    def get_replacement_clip(project_id: str, name: str, request: Request):
        safe = _safe_basename(name)
        path = project_dir(data_dir, project_id) / "replacements" / safe
        if not path.is_file():
            raise HTTPException(status_code=404, detail="clip not found")
        return _ranged_file_response(path, request, _video_media_type(path))

    @app.post("/api/projects/{project_id}/segments/{segment_id}/generate-replacement")
    def generate_replacement_stub(project_id: str, segment_id: str) -> JSONResponse:
        load_project(data_dir, project_id)
        return JSONResponse(
            status_code=501,
            content={
                "status": "not_implemented",
                "detail": "AI replacement generation is not available in this MVP.",
                "hook": "editor.generate.generate_replacement",
            },
        )

    @app.post("/api/projects/{project_id}/export")
    def start_export(project_id: str) -> dict[str, str]:
        load_project(data_dir, project_id)
        job = jobs.create()

        def work() -> None:
            project = load_project(data_dir, project_id)
            exports = project_dir(data_dir, project_id) / "exports"
            exports.mkdir(parents=True, exist_ok=True)
            out_name = f"export_{uuid.uuid4().hex[:8]}.mp4"
            out_path = exports / out_name

            def on_progress(p: float) -> None:
                jobs.update(job.id, progress=p, message="exporting")

            run_export_with_progress(project, out_path, data_dir, on_progress)
            jobs.update(job.id, result={"file": out_name, "url": f"/api/projects/{project_id}/exports/{out_name}"})

        jobs.run_in_thread(job.id, work)
        return {"job_id": job.id}

    @app.get("/api/projects/{project_id}/exports/{filename}")
    def download_export(project_id: str, filename: str, request: Request):
        safe = _safe_basename(filename)
        path = project_dir(data_dir, project_id) / "exports" / safe
        if not path.is_file():
            raise HTTPException(status_code=404, detail="export not found")
        return _ranged_file_response(path, request, _video_media_type(path))

    def _ensure_filmstrip(project: Project, count: int) -> list[dict[str, Any]]:
        pdir = project_dir(data_dir, project.id)
        thumbs = pdir / "thumbs"
        thumbs.mkdir(parents=True, exist_ok=True)
        video = pdir / project.source_video
        count = max(1, min(400, count))
        slot = project.duration / count if count else project.duration
        entries: list[dict[str, Any]] = []
        need_gen = []
        for i in range(count):
            t_start = i * slot
            t = min(project.duration, (i + 0.5) * slot)
            name = f"c{count}_{i:04d}.jpg"
            thumb = thumbs / name
            entries.append(
                {
                    "t": t,
                    "t_start": t_start,
                    "t_end": min(project.duration, (i + 1) * slot),
                    "url": f"/api/projects/{project.id}/thumbs/{name}",
                }
            )
            if not thumb.is_file():
                need_gen.append((t, thumb))
        if need_gen and ffmpeg_available():
            # Batch extract with one ffmpeg invocation per missing batch
            for t, thumb in need_gen:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        f"{max(0.0, t):.3f}",
                        "-i",
                        str(video),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=-1:68",
                        "-q:v",
                        "5",
                        str(thumb),
                    ],
                    check=False,
                    capture_output=True,
                )
        return entries

    @app.get("/api/projects/{project_id}/filmstrip")
    def filmstrip(project_id: str, count: int = 20) -> list[dict[str, Any]]:
        project = load_project(data_dir, project_id)
        return _ensure_filmstrip(project, count)

    @app.get("/api/projects/{project_id}/thumbs/{filename}")
    def get_thumb(project_id: str, filename: str):
        safe = _safe_basename(filename)
        path = project_dir(data_dir, project_id) / "thumbs" / safe
        if not path.is_file():
            raise HTTPException(status_code=404, detail="thumb missing")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="vid_splitter web editor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("VID_EDITOR_DATA", "~/.vid_splitter_editor"),
    )
    parser.add_argument(
        "--media-dir",
        default=os.environ.get("VID_EDITOR_MEDIA", "./videos"),
    )
    parser.add_argument(
        "--fake-detector",
        action="store_true",
        help="Use deterministic fake detector (or set VID_EDITOR_FAKE_DETECTOR=1)",
    )
    args = parser.parse_args(argv)
    if args.fake_detector:
        os.environ["VID_EDITOR_FAKE_DETECTOR"] = "1"

    import uvicorn

    app = create_app(Path(args.data_dir), Path(args.media_dir))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
