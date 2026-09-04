import json
m=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep23-i1-r0-fe-c4-traj/task_000015_89886d8d.messages.json'))
last_migrate=None; last_test=None
CATW = 'cat ' + '>'
for i,e in enumerate(m):
    if e.get('role')=='assistant':
        for tc in (e.get('tool_calls') or []):
            args=tc.get('function',{}).get('arguments','')
            try:
                cmd=json.loads(args).get('command','')
            except Exception:
                cmd=str(args)
            if 'migrate.py' in cmd and CATW in cmd:
                last_migrate=(i,cmd)
            if 'test_parser.py' in cmd and CATW in cmd:
                last_test=(i,cmd)
print('=== LAST migrate.py step', last_migrate[0], '===')
print(last_migrate[1][:3500])
print()
print('=== LAST test_parser.py step', last_test[0], '===')
print(last_test[1][:3500])
