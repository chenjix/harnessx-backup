import json, difflib, sys, glob

def extract(c):
    if isinstance(c, list):
        return ' '.join(str(x.get('text','') if isinstance(x, dict) else x) for x in c)
    return str(c)

def norm(t):
    return ' '.join((t or '').split())

D = '/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i4-r4-traj/'
tasks = ['task_000032_3fb303f6','task_001201_1340f4e2','task_000938_6d7bdc5c',
         'task_001044_45c70cf1','task_001465_aa3ed3f8','task_002108_a8cfbf2a',
         'task_001028_5bc8bc70','task_002138_2e85672e','task_000796_828a72cf',
         'task_000910_16cc0daf']

for t in tasks:
    msgs = json.load(open(D + t + '.messages.json'))
    ass = [norm(extract(m.get('content'))) for m in msgs if m.get('role') == 'assistant']
    thr = 0.85
    mx = 1; cur = 1
    for a, b in zip(ass, ass[1:]):
        if not a or not b:
            cur = 1; continue
        r = difflib.SequenceMatcher(None, a, b).ratio()
        if r >= thr:
            cur += 1; mx = max(mx, cur)
        else:
            cur = 1
    bmx = 1; bc = 1
    for a, b in zip(ass, ass[1:]):
        if a == b and a:
            bc += 1; bmx = max(bmx, bc)
        else:
            bc = 1
    print(t, 'n_assistant', len(ass), 'byte_run', bmx, 'sim085_run', mx)

# Now the PASSING set - ensure similarity detector wouldn't trip them
print('--- PASSING SET ---')
passing = ['task_000061_59de805f','task_000132_53c9b8b3','task_000560_7dc63881',
           'task_000591_3cf3e3ea','task_000728_24ab8073','task_000899_879f9c13',
           'task_001117_45cc5b85','task_001185_636b067c','task_001601_61cb1458',
           'task_002047_a04b2a01']
for t in passing:
    msgs = json.load(open(D + t + '.messages.json'))
    ass = [norm(extract(m.get('content'))) for m in msgs if m.get('role') == 'assistant']
    thr = 0.85
    mx = 1; cur = 1
    for a, b in zip(ass, ass[1:]):
        if not a or not b:
            cur = 1; continue
        r = difflib.SequenceMatcher(None, a, b).ratio()
        if r >= thr:
            cur += 1; mx = max(mx, cur)
        else:
            cur = 1
    print(t, 'n_assistant', len(ass), 'sim085_run', mx)
