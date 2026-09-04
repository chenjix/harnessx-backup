import asyncio
from harnessx.processors.control.loop_detection import LoopDetectionProcessor
from harnessx.core.events import StepStartEvent, ToolCallEvent, ToolResultEvent, TaskStartEvent
from harnessx.core.runloop import LoopDetectedError


async def drain(agen):
    out = []
    async for e in agen:
        out.append(e)
    return out


async def run_case(label, per_step_drop, drop_threshold=5):
    p = LoopDetectionProcessor(
        window_size=12, warn_threshold=3, threshold=5,
        name_warn_threshold=999, compaction_drop_threshold=drop_threshold,
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
        tc = ToolCallEvent(run_id="r", step_id=i, tool_name="Bash",
                           tool_call_id="c%d" % i, tool_input={"command": "same"}, approved=True)
        await drain(p.on_before_tool(tc))
        tr = ToolResultEvent(run_id="r", step_id=i, tool_name="Bash",
                             tool_call_id="c%d" % i, result="out")
        try:
            await drain(p.on_after_tool(tr))
        except LoopDetectedError:
            print("%s: RAISED at identical call #%d" % (label, i + 1))
            return
    print("%s: NEVER FIRED on 40 identical calls (DEFEAT)" % label)


async def main():
    await run_case("no-compaction", per_step_drop=False)
    await run_case("per-step-compaction-reset", per_step_drop=True)


asyncio.run(main())
