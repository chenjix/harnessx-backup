import json, sys
from collections import Counter
tid = sys.argv[1]
base = '/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i3-r7-traj/'
msgs = json.load(open(base + tid + '.messages.json'))
cmds = []
for i, m in enumerate(msgs):
    if m.get('role') == 'assistant' and m.get('tool_calls'):
        for tc in m['tool_calls']:
            try:
                a = json.loads(tc['function']['arguments'])
                cmds.append((i, a.get('command', '')))
            except Exception:
                cmds.append((i, '?'))
c = Counter(x[1] for x in cmds)
for cmd, n in c.most_common(3):
    print('--- x%d ---' % n)
    print(cmd[:300])
    print()
# redirect user messages injected by harness
kws = ['loop', 'harness', 'repeat', 'different approach', 'cut off', 'token limit', 'emit one', 'stuck']
for m in msgs:
    if m.get('role') == 'user':
        cont = m.get('content')
        if isinstance(cont, str):
            low = cont.lower()
            if any(k in low for k in kws):
                print('REDIRECT>', cont[:180].replace(chr(10), ' '))
