import asyncio
import sys
sys.path.insert(0, "/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep14-i2/_meta_v2/R4/c6/processors")
from loop_detection_compaction_safe import CompactionSafeLoopDetectionProcessor
from harnessx.core.events import StepStartEvent, ToolCallEvent, ToolResultEvent, TaskStartEvent
from harnessx.core.runloop import LoopDetectedError


async def drain(agen):
    out = []
    async for e in agen:
        out.append(e)
    return out


async def main():
    p = CompactionSafeLoopDetectionProcessor(
        window_size=40, warn_threshold=8, threshold=30,
        name_warn_threshold=999, compaction_drop_threshold=5,
    )
    await drain(p.on_task_start(TaskStartEvent(run_id="r", step_id=0)))
    msgcount = 60
    warns = []
    for i in range(12):
        msgcount = msgcount - 8
        if msgcount < 10:
            msgcount = 60
        ss = StepStartEvent(run_id="r", step_id=i, messages=[None] * msgcount)
        await drain(p.on_step_start(ss))
        tc = ToolCallEvent(run_id="r", step_id=i, tool_name="Bash",
                           tool_call_id="c%d" % i, tool_input={"command": "same"}, approved=True)
        await drain(p.on_before_tool(tc))
        tr = ToolResultEvent(run_id="r", step_id=i, tool_name="Bash",
                             tool_call_id="c%d" % i, result="out")
        try:
            outs = await drain(p.on_after_tool(tr))
            for e in outs:
                res = getattr(e, "result", "") or ""
                if "LoopDetection" in res:
                    warns.append(i + 1)
        except LoopDetectedError:
            print("RAISED at call #%d" % (i + 1))
            break
    print("warn fired at identical-call counts (under per-step compaction):", warns)


asyncio.run(main())
