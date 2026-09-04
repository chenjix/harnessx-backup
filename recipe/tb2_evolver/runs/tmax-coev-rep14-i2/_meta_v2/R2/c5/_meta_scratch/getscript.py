import json
d=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i2-r1-traj/task_000118_3043e92d/messages.json'))
for i,m in enumerate(d):
    tc=m.get('tool_calls')
    if tc:
        for t in tc:
            args=str(t.get('function',{}).get('arguments',''))
            if 'deployment_monitor.py' in args and 'cat' in args and 'EOF' in args:
                cmd=json.loads(t['function']['arguments'])['command']
                print('=== WRITE at step', i, '===')
                print(cmd)
                print('======')
