"""Project paths and dataset constants."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"
ARTIFACTS = ROOT / "ml" / "artifacts"

SLOT_MINUTES = 5
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES  # 288
DAYS = 14
N_SLOTS = DAYS * SLOTS_PER_DAY  # 4032

# Chronological split (days are 1-based in the raw trace): train 1-10, validation 11-12, test 13-14.
TRAIN = slice(0, 10 * SLOTS_PER_DAY)
VAL = slice(10 * SLOTS_PER_DAY, 12 * SLOTS_PER_DAY)
TEST = slice(12 * SLOTS_PER_DAY, N_SLOTS)

# Apps whose median demand is below this many busy workers sit at the minimum replica
# count under any policy, so they are excluded from the scaling study (kept for forecasting).
MIN_MEDIAN_CONCURRENCY = 0.25
