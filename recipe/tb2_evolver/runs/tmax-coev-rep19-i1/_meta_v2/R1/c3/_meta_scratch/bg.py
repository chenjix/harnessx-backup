import json
base="/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep19-i1-r0-traj/"
for t in ["task_000010_644ab1c2","task_001818_b251e5ea"]:
    d=json.load(open(base+t+".messages.json"))
    print("\n########", t)
    for m in d:
        for tc in (m.get('tool_calls') or []):
            try:
                a=json.loads(tc['function']['arguments'])
                cmd=a.get('command','')
                if '&' in cmd or 'nohup' in cmd or 'ps aux' in cmd or 'pgrep' in cmd:
                    print('   CMD:', cmd.replace(chr(10),' ')[:110])
            except: pass
