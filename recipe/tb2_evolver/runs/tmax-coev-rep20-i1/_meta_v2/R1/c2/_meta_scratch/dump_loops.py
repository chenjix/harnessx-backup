import json
for tid in ['task_000344_e265c898','task_000010_644ab1c2','task_000956_7e92337f']:
    f='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep20-i1-r0-traj/%s.messages.json'%tid
    d=json.load(open(f))
    print("========",tid)
    # show the repeated command and a few surrounding tool results
    for m in d:
        if m.get('role')=='assistant' and m.get('tool_calls'):
            fn=m['tool_calls'][0].get('function',{})
            print("CALL:", str(fn.get('arguments',''))[:150])
            break
    # show distribution of commands
    from collections import Counter
    c=Counter()
    for m in d:
        if m.get('role')=='assistant' and m.get('tool_calls'):
            for t in m['tool_calls']:
                fn=t.get('function',{})
                c[str(fn.get('arguments',''))[:80]]+=1
    for cmd,n in c.most_common(3):
        print("  x%d: %s"%(n,cmd))
    # last assistant text
    for m in reversed(d):
        if m.get('role')=='assistant' and isinstance(m.get('content'),str) and m['content'].strip():
            print("  LASTTEXT:", m['content'][:200].replace(chr(10),' ')); break
