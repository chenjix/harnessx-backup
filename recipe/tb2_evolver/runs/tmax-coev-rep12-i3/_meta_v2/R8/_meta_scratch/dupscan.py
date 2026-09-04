import json, glob, os
base = '/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i3-r7-traj/'
rows = []
for rf in sorted(glob.glob(base + 'task_*.result.json')):
    d = json.load(open(rf))
    tid = d['task_id']
    mf = base + tid + '.messages.json'
    if not os.path.exists(mf):
        continue
    msgs = json.load(open(mf))
    cmds = []
    for m in msgs:
        if m.get('role') == 'assistant' and m.get('tool_calls'):
            tcs = m['tool_calls']
            if len(tcs) == 1:
                tc = tcs[0]
                fn = tc.get('function', {})
                if fn.get('name') == 'Bash':
                    try:
                        a = json.loads(fn.get('arguments', '{}'))
                        cmds.append((a.get('command', '') or '').strip())
                    except Exception:
                        cmds.append('?')
                else:
                    cmds.append(None)  # non-bash single call breaks run
            else:
                cmds.append(None)  # multi call breaks run
        elif m.get('role') == 'assistant':
            cmds.append(None)  # narration breaks run
    # max consecutive identical NON-EMPTY
    maxrun = 0; cur = 0; last = object()
    for c in cmds:
        if c and c != '?' and c == last:
            cur += 1
        elif c and c != '?':
            cur = 1; last = c
        else:
            cur = 0; last = object()
        maxrun = max(maxrun, cur)
    rows.append((tid, d['reward'], maxrun))
rows.sort(key=lambda r: (-r[2]))
print(f"{'task':26} {'reward':>6} {'maxIdenticalBashRun':>20}")
for r in rows:
    print(f"{r[0]:26} {r[1]:>6} {r[2]:>20}")
