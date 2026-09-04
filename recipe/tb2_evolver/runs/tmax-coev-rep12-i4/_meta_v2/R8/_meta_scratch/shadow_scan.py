import json, glob, os, sys
d = "/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i4-r7-traj/"
# stdlib top-level module names (py3.10 approx common ones)
stdlib = set("""operator json re os sys io time math random string collections functools itertools
subprocess socket http server client email base64 hashlib hmac struct types typing enum abc queue
threading logging argparse csv sqlite3 pickle copy datetime calendar decimal fractions statistics
array bisect heapq contextlib dataclasses pathlib glob shutil tempfile signal select ssl uuid
xml html urllib asyncio concurrent multiprocessing test token""".split())
for f in sorted(glob.glob(d + "task_*.result.json")):
    dd = json.load(open(f))
    tail = dd.get("final_pytest", {}).get("output_tail", "") or ""
    tid = os.path.basename(f)[:24]
    # detect shadowing crash signature
    if ("circular import" in tail or "partially initialized module" in tail) and "/home/user/" in tail:
        print("SHADOW-CRASH", tid, "reward", dd.get("reward"))
    # detect any /home/user/<stdlibname>.py referenced in traceback
    import re as _re
    for m in _re.findall(r"/home/user/([A-Za-z_][A-Za-z0-9_]*)\.py", tail):
        if m in stdlib:
            print("  stdlib-name-file:", tid, m)
