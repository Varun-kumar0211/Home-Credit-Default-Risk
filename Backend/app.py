"""Compatibility launcher for the HTML dashboard."""

import re
import sys
from pathlib import Path


def _extract_percent(value) -> float:
    """Normalize numeric/percent input to a 0-100 float."""
    is_percent_string = isinstance(value, str) and "%" in value
    is_score_string = isinstance(value, str) and "/" in value
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"[-+]?\d*\.?\d+", str(value))
        number = float(match.group()) if match else 0.0
    if not is_percent_string and not is_score_string and 0 <= number <= 1:
        number *= 100
    return max(0.0, min(100.0, number))


if __name__ == "__main__":
    import uvicorn

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    uvicorn.run("Backend.main:app", host="0.0.0.0", port=7860)
