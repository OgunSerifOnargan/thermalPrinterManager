"""ETA — type-specific moving average over last N successful prints (per K3).

text and image are tracked separately because their durations differ
significantly (image is dominated by raster transfer + dithering).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.job_repository import JobRepository


_WINDOW: int = 10


@dataclass
class EtaSnapshot:
    text_ms: int | None
    image_ms: int | None
    samples_text: int
    samples_image: int


class EtaService:
    def __init__(self, repository: JobRepository):
        self._repo = repository

    def snapshot(self) -> EtaSnapshot:
        text_durations = self._fetch_durations("print_text")
        image_durations = self._fetch_durations("print_image")
        return EtaSnapshot(
            text_ms=self._average(text_durations),
            image_ms=self._average(image_durations),
            samples_text=len(text_durations),
            samples_image=len(image_durations),
        )

    def _fetch_durations(self, op_type: str) -> list[int]:
        rows = self._repo.list_recent_done(op_type=op_type, limit=_WINDOW)
        return [r.duration_ms for r in rows if r.duration_ms is not None]

    @staticmethod
    def _average(values: list[int]) -> int | None:
        if not values:
            return None
        return int(sum(values) / len(values))
