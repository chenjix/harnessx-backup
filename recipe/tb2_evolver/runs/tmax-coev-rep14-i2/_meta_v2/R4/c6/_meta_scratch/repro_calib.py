import asyncio
import sys
sys.path.insert(0, "/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep14-i2/_meta_v2/R4/c6/processors")
from loop_detection_compaction_safe import CompactionSafeLoopDetectionProcessor
from harnessx.core.events import StepStartEvent, ToolCallEvent, ToolResultEvent, TaskStartEvent
from harnessx.core.runloop import LoopDetectedError

WINDOW = 40
WARN = 8
THRESH = 30
CDT = 5


async def drain(agen):
    out = []
    async for e in agen:
        out.append(e)
    return out


async def run_case(label, n_identical, per_step_drop, then_recover=False):
    p = CompactionSafeLoopDetectionProcessor(
        window_size=WINDOW, warn_threshold=WARN, threshold=THRESH,
        name_warn_threshold=999, compaction_drop_threshold=CDT,
    )
    await drain(p.on_task_start(TaskStartEvent(run_id="r", step_id=0)))
    msgcount = 60
    total = n_identical + (5 if then_recover else 0)
    for i in range(total):
        if per_step_drop:
            msgcount = msgcount - 8
            if msgcount < 10:
                msgcount = 60
        ss = StepStartEvent(run_id="r", step_id=i, messages=[None] * msgcount)
        await drain(p.on_step_start(ss))
        cmd = "same" if i < n_identical else ("recover-%d" % i)
        tc = ToolCallEvent(run_id="r", step_id=i, tool_name="Bash",
                           tool_call_id="c%d" % i, tool_input={"command": cmd}, approved=True)
        await drain(p.on_before_tool(tc))
        tr = ToolResultEvent(run_id="r", step_id=i, tool_name="Bash",
                             tool_call_id="c%d" % i, result="out")
        try:
            await drain(p.on_after_tool(tr))
        except LoopDetectedError:
            print("%s: RAISED at call #%d" % (label, i + 1))
            return
    print("%s: never raised (ran %d calls)" % (label, total))


async def main():
    # task_000936-like: 26 identical then recovers, with compaction interference -> must NOT be cut
    await run_case("PASS-probe 26-identical+recover (compaction)", 26, True, then_recover=True)
    await run_case("PASS-probe 26-identical+recover (no compaction)", 26, False, then_recover=True)
    # pathological 33 non-recovering with compaction -> must get clean cut at 30
    await run_case("pathological 33 (compaction)", 40, True)
    await run_case("pathological 33 (no compaction)", 40, False)
    # mid loops 15-20 -> should NOT hard-cut (only warn)
    await run_case("mid-loop 20 (compaction)", 20, True)


asyncio.run(main())
