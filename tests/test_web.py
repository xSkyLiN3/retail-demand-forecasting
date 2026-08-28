from __future__ import annotations

import json

from retail_forecasting.web import seed_repository


class RecordingRepository:
    def __init__(self) -> None:
        self.runs = []
        self.monitoring = []

    def save_run(self, run, forecasts) -> bool:
        self.runs.append((run, forecasts))
        return True

    def save_monitoring(self, run_id, rows) -> bool:
        self.monitoring.append((run_id, rows))
        return True


def test_seed_repository_preserves_run_boundaries(tmp_path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runs": [{"run_id": "one"}, {"run_id": "two"}],
                "forecasts": [
                    {"run_id": "one", "sku": "A"},
                    {"run_id": "two", "sku": "B"},
                ],
                "monitoring": [{"run_id": "one", "actual": 1}],
            }
        ),
        encoding="utf-8",
    )
    repository = RecordingRepository()

    seed_repository(repository, path)

    assert [run[0]["run_id"] for run in repository.runs] == ["one", "two"]
    assert repository.runs[0][1] == [{"run_id": "one", "sku": "A"}]
    assert repository.runs[1][1] == [{"run_id": "two", "sku": "B"}]
    assert repository.monitoring == [("one", [{"run_id": "one", "actual": 1}])]
