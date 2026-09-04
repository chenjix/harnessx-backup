import json, sys, re
# Heuristic: task has a numeric size/limit (MB/GB/bytes/percent) in description
# AND agent's bash outputs contain byte-size measurements (ls -l style or "total N")
LIMIT_RE = re.compile(r'\b\d+\s?(MB|GB|KB|bytes|%|percent|seconds|ms)\b', re.I)
SIZE_OUT_RE = re.compile(r'\btotal\s+\d{4,}\b|\b\d{7,}\b')  # ls total or big byte counts

for p in sys.argv[1:]:
    tid=p.split('/')[-2]
    try:
        msgs=json.load(open(p))
    except Exception as e:
        print(tid,'ERR'); continue
    desc = ''
    for m in msgs:
        if m.get('role')=='user':
            c=m.get('content')
            if isinstance(c,list): c=' '.join(str(x.get('text','')) for x in c if isinstance(x,dict))
            desc=str(c); break
    has_limit = bool(LIMIT_RE.search(desc))
    # gather tool outputs
    tool_out=''
    for m in msgs:
        if m.get('role')=='tool':
            c=m.get('content')
            if isinstance(c,list): c=' '.join(str(x.get('text','')) for x in c if isinstance(x,dict))
            tool_out += str(c)+'\n'
    has_size = bool(SIZE_OUT_RE.search(tool_out))
    print('%s limit=%s size_out=%s'%(tid, has_limit, has_size))
