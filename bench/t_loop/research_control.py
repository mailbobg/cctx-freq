"""Generation follows hypothesis participation, task switch and global gate."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bench.research_scheduler.scheduler import Gate


def generation_allowed(qpath: Path) -> bool:
    return (qpath.parent / 'HYP').exists() and Gate(qpath.resolve().parents[2]).allowed('t_loop')
