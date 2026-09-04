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


async def run_case(label, per_step_drop, distinct_calls=False):
    p = CompactionSafeLoopDetectionProcessor(
        window_size=12, warn_threshold=3, threshold=5,
        name_warn_threshold=999, compaction_drop_threshold=5,
    )
    await drain(p.on_task_start(TaskStartEvent(run_id="r", step_id=0)))
    msgcount = 60
    for i in range(40):
        if per_step_drop:
            msgcount = msgcount - 8
            if msgcount < 10:
                msgcount = 60
        ss = StepStartEvent(run_id="r", step_id=i, messages=[None] * msgcount)
        await drain(p.on_step_start(ss))
        cmd = ("cmd-%d" % i) if distinct_calls else "same"
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
    print("%s: never fired" % label)


async def main():
    # Identical loop, no compaction: must fire at 5 (baseline behavior preserved)
    await run_case("identical / no-compaction", per_step_drop=False)
    # Identical loop WITH per-step compaction: previously defeated, must now fire
    await run_case("identical / per-step-compaction", per_step_drop=True)
    # DISTINCT calls with per-step compaction: must NOT fire (no false positive)
    await run_case("distinct / per-step-compaction (should never fire)", per_step_drop=True, distinct_calls=True)


asyncio.run(main())
