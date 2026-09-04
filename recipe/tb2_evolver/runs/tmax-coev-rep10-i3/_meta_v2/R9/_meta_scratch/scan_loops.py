import json, glob, os
os.chdir('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep10-i3-r8-traj/')
for f in sorted(glob.glob('task_*.messages.json')):
    m = json.load(open(f))
    msgs = m if isinstance(m, list) else m.get('messages', m)
    asst = [str(x.get('content'))[:200] for x in msgs if x.get('role') == 'assistant' and x.get('content')]
    best = 0
    cur = 1
    for i in range(1, len(asst)):
        if asst[i] == asst[i-1]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    r = json.load(open(f.replace('.messages.json', '.result.json')))
    if best >= 4:
        print(f'{r["task_id"]} maxconsec_identical_asst={best} reward={r["reward"]} exit={r["agent"].get("exit_reason")}')
