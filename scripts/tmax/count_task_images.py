"""Count distinct task images a coevolve run will build.

The sbatch uses this to size its image-store space check to the actual run
instead of assuming the full 152-task set. Accepts either task-json shape:
a bare list of task_ids, or a dict carrying "task_ids"/"tasks".
"""
import json
import sys


def n_tasks(path: str) -> int:
    with open(path) as fh:
        d = json.load(fh)
    if isinstance(d, list):
        return len(d)
    return len(d.get("task_ids") or d.get("tasks") or [])


if __name__ == "__main__":
    print(sum(n_tasks(p) for p in sys.argv[1:]))
