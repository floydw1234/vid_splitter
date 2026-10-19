"""AI replacement generation (stub)."""

from __future__ import annotations

from pathlib import Path

from editor.project import Project, Segment


def generate_replacement(project: Project, segment: Segment, prompt: str) -> Path:
    """
    Generate a replacement clip for ``segment`` matching the source timeline slot.

    Contract (intended future implementation):
    - Output file is written under the project directory (e.g. replacements/).
    - Video matches ``project.width`` x ``project.height``, ``project.fps``, and
      duration ``segment.end - segment.start``.
    - Returns the path to the new clip (relative name registered in project.replacement_clips).
    - ``prompt`` guides visual content for the replacement region.

    Raises NotImplementedError until an AI backend is wired.
    """
    raise NotImplementedError(
        "AI replacement generation is not implemented; hook: editor.generate.generate_replacement"
    )
