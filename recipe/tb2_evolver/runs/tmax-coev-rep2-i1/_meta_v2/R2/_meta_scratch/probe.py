import json, hashlib, os, sys

base = "/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep2-i1-r1-traj"
passing = ['task_001536_acfe6c35','task_001652_86e1d185','task_001498_df8254c9','task_001031_a8f0eb37',
 'task_000578_cebe85a5','task_000587_9862bb19','task_001090_c61c71f2','task_000740_59416444',
 'task_001591_8901fea6','task_001673_86224c91','task_001706_24462a09','task_001761_f620d44d',
 'task_000956_7e92337f','task_000965_069bb95f','task_001088_6f566806','task_001089_220cc46b',
 'task_001264_9f4ca84a','task_001421_1f2cf4c7','task_001515_eed714e6','task_001697_af4b85fb',
 'task_000760_76ba653c','task_000818_315382d9','task_000863_7acceb19','task_000912_770802f8',
 'task_000024_a0664029','task_000206_a943669b','task_000329_a3ac56b0','task_000338_27d6a1be',
 'task_000344_e265c898','task_000536_9c16e8ef','task_000667_2d762a00','task_000684_1a33ef37']

def maxrun(t):
    p = os.path.join(base, t, "messages.json")
    if not os.path.exists(p): return None
    msgs = json.load(open(p))
    fps = []
    for m in msgs:
        if m.get('role') == 'assistant' and m.get('tool_calls'):
            for tc in m['tool_calls']:
                s = tc['function']['name'] + '\x00' + tc['function']['arguments']
                fps.append(hashlib.sha256(s.encode()).hexdigest()[:16])
    mr = 0; cur = 0; prev = None
    for f in fps:
        if f == prev: cur += 1
        else: cur = 1; prev = f
        mr = max(mr, cur)
    return len(fps), mr

print("=== PASSING tasks: max consecutive IDENTICAL tool call run ===")
for t in passing:
    r = maxrun(t)
    if r and r[1] >= 3:
        print(f"{t}: n_calls={r[0]} max_consec_identical={r[1]}")
print("(only tasks with max>=3 shown)")

print("\n=== FAILING budget/loop tasks ===")
for t in ['task_000264_ab8c7253','task_000958_4bb2b05d','task_001032_1adaccb9',
          'task_001321_658ce4a8','task_001701_95e3bbcb','task_001818_b251e5ea']:
    r = maxrun(t)
    print(f"{t}: n_calls={r[0]} max_consec_identical={r[1]}")
