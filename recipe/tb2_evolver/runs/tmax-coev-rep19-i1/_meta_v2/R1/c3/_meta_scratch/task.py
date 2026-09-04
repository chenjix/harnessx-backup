import json
d=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep19-i1-r0-traj/task_000118_3043e92d.messages.json'))
# print full task
print("=== TASK DESCRIPTION ===")
print(d[0]['content'])
