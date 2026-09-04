import json
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i2-r1-traj/'
tasks=['task_000164_ee3fd873','task_000785_b727c84b','task_000348_31fb8c8a','task_000885_d1c1007b',
'task_001465_aa3ed3f8','task_000908_170e5e4e','task_000311_82f9e3ba','task_000506_c13429e7',
'task_000197_1460b736','task_000438_fee5a792','task_000669_0ef2d04d','task_000763_7713d5ae',
'task_001674_125bb961','task_000998_4d9c7852','task_002146_0bc2994c','task_001036_87bd136e']
for t in tasks:
    d=json.load(open(base+f'{t}.result.json'))
    tail=(d.get('final_pytest',{}) or {}).get('output_tail','') or ''
    print(f"=== {t} exit={d['agent'].get('exit_reason')} steps={d['agent'].get('steps')} ===")
    # last 260 chars of tail
    print(tail[-260:].replace('\n',' '))
    print()
