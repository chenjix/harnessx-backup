#!/usr/bin/env python3
"""Carefully filter TB2 evolve trajectories and build small-scale SFT ablations.

Success predicate (Harbor): verifier_result.rewards.reward > 0

Quality gates (conservative):
  - no exception_info
  - structured assistant tool_calls present (prefer type=assistant over raw_assistant)
  - min/max tool turns
  - readable tool observations (or content_ref resolvable)
  - dedupe by task keeping short, closedloop-preferred trials

Ablation splits written under data/:
  A1_evolve_success / A2_fulleval_success / A3_success_plus_recovery / A4_reward_weighted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / ".benchmarks" / "tb2"
OUT_ROOT = Path(__file__).resolve().parents[1] / "data"

SAMPLE16 = [
    "build-cython-ext",
    "build-pmars",
    "code-from-image",
    "configure-git-webserver",
    "constraints-scheduling",
    "custom-memory-heap-crash",
    "financial-document-processor",
    "fix-code-vulnerability",
    "fix-git",
    "gcode-to-text",
    "headless-terminal",
    "openssl-selfsigned-cert",
    "query-optimize",
    "torch-pipeline-parallelism",
    "vulnerable-secret",
]

# Task-level holdout: never train on these success-capable sample16 tasks.
HOLDOUT_TASKS = {
    "query-optimize",
    "headless-terminal",
    "constraints-scheduling",
    "code-from-image",
}

EVOLVE_HINTS = ("evolve", "closedloop", "crossh", "gpt55-fixed")
FULLEVAL_HINTS = ("r5-full", "r5-reasoning", "r5-traj")


@dataclass
class TrialMeta:
    run: str
    trial_dir: str
    task: str
    reward: float
    model: str
    n_tool_turns: int
    n_recovered_tools: int
    n_steps: int
    source_bucket: str  # evolve | fulleval | other
    quality_score: float
    has_structured_tools: bool
    exception: bool


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _reward_of(obj: dict[str, Any]) -> float | None:
    vr = obj.get("verifier_result") or {}
    rewards = vr.get("rewards") if isinstance(vr, dict) else None
    if not isinstance(rewards, dict):
        return None
    r = rewards.get("reward")
    return float(r) if isinstance(r, (int, float)) else None


def _model_of(obj: dict[str, Any]) -> str:
    cfg = obj.get("config") or {}
    agent = cfg.get("agent") if isinstance(cfg, dict) else {}
    if isinstance(agent, dict) and agent.get("model_name"):
        return str(agent["model_name"])
    return "unknown"


def _bucket(run_name: str) -> str:
    low = run_name.lower()
    if any(h in low for h in FULLEVAL_HINTS):
        return "fulleval"
    if any(h in low for h in EVOLVE_HINTS):
        return "evolve"
    return "other"


def _find_session_jsonl(trial_dir: Path) -> Path | None:
    oh = trial_dir / "agent" / "oh_runs"
    if not oh.is_dir():
        return None
    cands: list[Path] = []
    for p in oh.rglob("*.jsonl"):
        name = p.name
        if "_trace" in name or "_state" in name:
            continue
        cands.append(p)
    if not cands:
        return None
    # Prefer largest non-trace jsonl (usually the main session).
    cands.sort(key=lambda p: p.stat().st_size, reverse=True)
    return cands[0]


def _resolve_tool_content(msg: dict[str, Any], session_dir: Path) -> str:
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content
    meta = msg.get("meta") if isinstance(msg.get("meta"), dict) else {}
    # Sometimes meta is sibling on event, handled by caller.
    return ""


def _read_tool_obs(event: dict[str, Any], session_dir: Path) -> str:
    msg = event.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    meta = event.get("meta") or {}
    ref = meta.get("content_ref") if isinstance(meta, dict) else None
    if isinstance(ref, str) and ref:
        path = session_dir / ref
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                # Cap very large observations for SFT context.
                if len(text) > 12000:
                    text = text[:12000] + "\n...[truncated]..."
                return text
            except Exception:
                return ""
    return ""


def parse_messages(jsonl_path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build OpenAI-style messages from HarnessX session jsonl.

    Uses structured assistant/tool events; keeps system + first user task.
    """
    session_dir = jsonl_path.parent
    system_txt = ""
    task_txt = ""
    messages: list[dict[str, Any]] = []
    _seen_assistant: set[tuple[Any, str, int]] = set()
    stats = defaultdict(int)

    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        et = ev.get("type")
        if et == "session_start":
            task_txt = str(ev.get("task") or "").strip()
            stats["session_start"] += 1
        elif et == "system":
            msg = ev.get("message") or {}
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                system_txt = msg["content"].strip()
            stats["system"] += 1
        elif et == "raw_user":
            # Only keep the initial task user message if we don't have one yet.
            msg = ev.get("message") or {}
            content = (msg.get("content") if isinstance(msg, dict) else "") or ""
            if content.strip() and not any(m.get("role") == "user" for m in messages):
                messages.append({"role": "user", "content": content.strip()})
            stats["raw_user"] += 1
        elif et in ("assistant", "raw_assistant"):
            msg = ev.get("message") or {}
            if not isinstance(msg, dict):
                continue
            # Some sessions log both a `raw_assistant` and an `assistant` copy of the
            # same turn; others log only `raw_assistant`. Keep one per (step, content)
            # so shared parsing does not double-count tool calls.
            _key = (ev.get("step"), str(msg.get("content") or "")[:200],
                    len(msg.get("tool_calls") or []))
            if _key in _seen_assistant:
                continue
            _seen_assistant.add(_key)
            out: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
            tcs = msg.get("tool_calls") or []
            if tcs:
                openai_tcs = []
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    tid = str(tc.get("id") or f"call_{stats['tool_calls']}")
                    name = str(tc.get("name") or (tc.get("function") or {}).get("name") or "Bash")
                    args = tc.get("input")
                    if args is None:
                        args = (tc.get("function") or {}).get("arguments")
                    if isinstance(args, dict):
                        arg_str = json.dumps(args, ensure_ascii=False)
                    else:
                        arg_str = str(args or {})
                    openai_tcs.append(
                        {
                            "id": tid,
                            "type": "function",
                            "function": {"name": name, "arguments": arg_str},
                        }
                    )
                    if tid.startswith("recovered_"):
                        stats["recovered_tools"] += 1
                    stats["tool_calls"] += 1
                if openai_tcs:
                    out["tool_calls"] = openai_tcs
            messages.append(out)
            stats["assistant"] += 1
        elif et == "raw_tool":
            msg = ev.get("message") or {}
            obs = _read_tool_obs(ev, session_dir)
            tid = ""
            name = "Bash"
            if isinstance(msg, dict):
                tid = str(msg.get("tool_call_id") or "")
                name = str(msg.get("name") or "Bash")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid or f"tool_{stats['tool_obs']}",
                    "name": name,
                    "content": obs or "[empty tool result]",
                }
            )
            stats["tool_obs"] += 1

    # Ensure system + user exist at front.
    final: list[dict[str, Any]] = []
    if system_txt:
        final.append({"role": "system", "content": system_txt})
    if not any(m.get("role") == "user" for m in messages) and task_txt:
        final.append({"role": "user", "content": task_txt})
    final.extend(messages)
    return final, dict(stats)


def _quality_score(meta: TrialMeta) -> float:
    score = 1.0 * meta.reward
    # Prefer closedloop/evolve over noisy full eval duplicates.
    if meta.source_bucket == "evolve":
        score += 0.35
    elif meta.source_bucket == "fulleval":
        score += 0.10
    # Prefer compact successful trajectories.
    score += max(0.0, 0.25 - 0.005 * meta.n_tool_turns)
    # Mild penalty if almost all tools were recovery-injected (harness-heavy).
    if meta.n_tool_turns > 0:
        frac = meta.n_recovered_tools / meta.n_tool_turns
        score -= 0.15 * frac
    return score


def scan_trials(
    min_tools: int,
    max_tools: int,
) -> tuple[list[tuple[TrialMeta, list[dict[str, Any]]]], list[tuple[TrialMeta, list[dict[str, Any]]]]]:
    successes: list[tuple[TrialMeta, list[dict[str, Any]]]] = []
    recoveries: list[tuple[TrialMeta, list[dict[str, Any]]]] = []

    for run_dir in sorted(BENCH.iterdir()):
        if not run_dir.is_dir():
            continue
        run = run_dir.name
        for trial_dir in run_dir.iterdir():
            if not trial_dir.is_dir() or trial_dir.name.startswith("_"):
                continue
            rp = trial_dir / "result.json"
            if not rp.is_file():
                continue
            obj = _load_json(rp)
            if not obj:
                continue
            reward = _reward_of(obj)
            exc = obj.get("exception_info") is not None
            task = str(obj.get("task_name") or trial_dir.name.split("__")[0])
            model = _model_of(obj)
            jsonl = _find_session_jsonl(trial_dir)
            if jsonl is None:
                continue
            messages, stats = parse_messages(jsonl)
            n_tools = int(stats.get("tool_calls", 0))
            n_rec = int(stats.get("recovered_tools", 0))
            has_struct = n_tools > 0 and any(m.get("role") == "assistant" and m.get("tool_calls") for m in messages)
            if not has_struct:
                continue
            if n_tools < min_tools or n_tools > max_tools:
                continue
            # Need paired tool observations roughly.
            if int(stats.get("tool_obs", 0)) < max(1, n_tools // 2):
                continue

            meta = TrialMeta(
                run=run,
                trial_dir=str(trial_dir),
                task=task,
                reward=float(reward) if reward is not None else -1.0,
                model=model,
                n_tool_turns=n_tools,
                n_recovered_tools=n_rec,
                n_steps=int(stats.get("assistant", 0)),
                source_bucket=_bucket(run),
                quality_score=0.0,
                has_structured_tools=has_struct,
                exception=exc,
            )
            meta.quality_score = _quality_score(meta)

            if reward is not None and reward > 0 and not exc:
                successes.append((meta, messages))
            elif reward == 0.0 and not exc:
                # Failure-recovery candidate: continued after errors / recovered tools.
                blob = "\n".join(str(m.get("content", ""))[:500] for m in messages if m.get("role") == "tool")
                looks_recovery = n_rec > 0 or bool(
                    re.search(r"(error|exception|traceback|not found|failed|no such)", blob, re.I)
                )
                if looks_recovery and n_tools >= max(min_tools, 3):
                    recoveries.append((meta, messages))

    return successes, recoveries


def _messages_to_sft_record(meta: TrialMeta, messages: list[dict[str, Any]], weight: float = 1.0) -> dict[str, Any]:
    # Flatten to prompt/response pairs for LoRA SFT: supervise assistant turns only,
    # with prior context as prompt. Keep one record per assistant tool-turn + final.
    records_payload = {
        "task": meta.task,
        "run": meta.run,
        "trial_dir": meta.trial_dir,
        "reward": meta.reward,
        "model": meta.model,
        "source_bucket": meta.source_bucket,
        "quality_score": meta.quality_score,
        "weight": weight,
        "n_tool_turns": meta.n_tool_turns,
        "n_recovered_tools": meta.n_recovered_tools,
        "messages": messages,
    }
    return records_payload


def _normalize_tool_arguments(arguments: Any) -> Any:
    """Qwen chat_template requires ``arguments`` to be a mapping (``|items``)."""
    if isinstance(arguments, dict):
        return arguments
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        s = arguments.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
        except Exception:
            return {"_raw": arguments}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return arguments


def normalize_messages_for_chat_template(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite trajectory messages so ``apply_chat_template`` accepts them."""
    out: list[dict[str, Any]] = []
    for raw in messages:
        msg = dict(raw)
        if msg.get("content") is None:
            msg["content"] = ""
        elif not isinstance(msg["content"], str):
            msg["content"] = json.dumps(msg["content"], ensure_ascii=False)
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            fixed: list[dict[str, Any]] = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc = dict(tc)
                if isinstance(tc.get("function"), dict):
                    fn = dict(tc["function"])
                    fn["arguments"] = _normalize_tool_arguments(fn.get("arguments"))
                    tc["function"] = fn
                elif "arguments" in tc:
                    tc["arguments"] = _normalize_tool_arguments(tc.get("arguments"))
                fixed.append(tc)
            msg["tool_calls"] = fixed
        out.append(msg)
    return out


def expand_turn_pairs(record: dict[str, Any], max_pairs: int = 8) -> list[dict[str, Any]]:
    """Expand a trajectory into supervised turn pairs.

    Each pair is emitted in TRL's conversational prompt/completion form so the
    trainer can ``apply_chat_template`` and mask the prompt (official Tmax /
    open-instruct style). Legacy string fields ``prompt_text`` / ``response``
    are kept for char-length filters in older builders.
    """
    messages = normalize_messages_for_chat_template(record["messages"])
    pairs: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        prior = messages[:i]
        # Structured assistant turn (keep tool_calls); chat template renders XML.
        asst = dict(m)
        response = render_assistant(asst)
        if len(response.strip()) < 4:
            continue
        pairs.append(
            {
                # Conversational columns consumed by train_sft_lora.py / TRL.
                "prompt": prior,
                "completion": [asst],
                # Legacy string views (length gates, debugging).
                "prompt_text": render_chat(prior),
                "response": response,
                "task": record["task"],
                "run": record["run"],
                "reward": record["reward"],
                "weight": record.get("weight", 1.0),
                "source_bucket": record["source_bucket"],
                "quality_score": record["quality_score"],
                "turn_index": i,
            }
        )
        if len(pairs) >= max_pairs:
            break
    return pairs


def pair_char_len(pair: dict[str, Any]) -> int:
    """Character length for filtering; works for new and legacy pair shapes."""
    if isinstance(pair.get("prompt_text"), str) and isinstance(pair.get("response"), str):
        return len(pair["prompt_text"]) + len(pair["response"])
    prompt = pair.get("prompt")
    if isinstance(prompt, list):
        completion = pair.get("completion") or []
        asst = completion[0] if completion else {}
        return len(render_chat(prompt)) + len(render_assistant(asst if isinstance(asst, dict) else {}))
    return len(str(prompt or "")) + len(str(pair.get("response") or pair.get("completion") or ""))


def render_chat(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            parts.append(f"System:\n{m.get('content', '')}")
        elif role == "user":
            parts.append(f"User:\n{m.get('content', '')}")
        elif role == "assistant":
            parts.append("Assistant:\n" + render_assistant(m))
        elif role == "tool":
            parts.append(
                f"Tool({m.get('name', 'tool')}/{m.get('tool_call_id', '')}):\n{m.get('content', '')}"
            )
    return "\n\n".join(parts).strip()


def render_assistant(m: dict[str, Any]) -> str:
    chunks: list[str] = []
    content = m.get("content") or ""
    if isinstance(content, str) and content.strip():
        # Drop huge channel/thought dumps if present; keep short reasoning.
        text = content.strip()
        if len(text) > 4000:
            text = text[:4000] + "\n...[truncated]..."
        chunks.append(text)
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name") or "Bash"
        args = fn.get("arguments") or "{}"
        chunks.append(render_tool_call_xml(name, args))
    return "\n".join(chunks).strip()


def render_tool_call_xml(name: str, args: Any) -> str:
    """Serialize a tool call in Qwen3.5's native XML dialect.

    This must match what vLLM's ``qwen3_xml`` parser accepts, otherwise SFT
    teaches the model a format the serving stack cannot read back — the exact
    failure that zeroed every TB2 score before 2026-07-26.
    """
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except Exception:
            parsed = {"command": args}
    else:
        parsed = args
    if not isinstance(parsed, dict):
        parsed = {"command": str(parsed)}

    lines = ["<tool_call>", f"<function={name}>"]
    for key, value in parsed.items():
        rendered = (
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list))
            else str(value)
        )
        lines.extend([f"<parameter={key}>", rendered, "</parameter>"])
    lines.extend(["</function>", "</tool_call>"])
    return "\n".join(lines)


def dedupe_by_task(
    items: list[tuple[TrialMeta, list[dict[str, Any]]]],
    per_task: int,
) -> list[tuple[TrialMeta, list[dict[str, Any]]]]:
    by_task: dict[str, list[tuple[TrialMeta, list[dict[str, Any]]]]] = defaultdict(list)
    for meta, msgs in items:
        by_task[meta.task].append((meta, msgs))
    out: list[tuple[TrialMeta, list[dict[str, Any]]]] = []
    for task, lst in by_task.items():
        lst.sort(key=lambda x: (-x[0].quality_score, x[0].n_tool_turns))
        # Secondary dedupe by assistant-tool signature.
        seen = set()
        kept = 0
        for meta, msgs in lst:
            sig_src = json.dumps(
                [m for m in msgs if m.get("role") == "assistant"],
                ensure_ascii=False,
                sort_keys=True,
            )[:5000]
            sig = hashlib.md5(sig_src.encode()).hexdigest()
            if sig in seen:
                continue
            seen.add(sig)
            out.append((meta, msgs))
            kept += 1
            if kept >= per_task:
                break
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def split_pairs(pairs: list[dict[str, Any]], eval_ratio: float, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(seed)
    pairs = list(pairs)
    rng.shuffle(pairs)
    n_eval = max(1, int(len(pairs) * eval_ratio)) if pairs else 0
    return pairs[n_eval:], pairs[:n_eval]


def build_ablation(
    name: str,
    traj_records: list[dict[str, Any]],
    eval_ratio: float,
    seed: int,
    max_pairs_per_traj: int,
    weight_mode: str = "unit",
) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    for rec in traj_records:
        w = float(rec.get("weight", 1.0))
        if weight_mode == "quality":
            w = max(1.0, float(rec.get("quality_score", 1.0)))
        # Integer oversampling for weighted SFT without custom loss.
        copies = 1
        if weight_mode == "quality":
            copies = max(1, min(4, int(round(w))))
        for _ in range(copies):
            turn_pairs = expand_turn_pairs(rec, max_pairs=max_pairs_per_traj)
            for p in turn_pairs:
                p = dict(p)
                p["weight"] = w
                pairs.append(p)

    train, ev = split_pairs(pairs, eval_ratio=eval_ratio, seed=seed)
    out_dir = OUT_ROOT / name
    write_jsonl(out_dir / "train.jsonl", train)
    write_jsonl(out_dir / "eval.jsonl", ev if ev else train[:1])
    write_jsonl(out_dir / "trajectories.jsonl", traj_records)
    summary = {
        "name": name,
        "n_trajectories": len(traj_records),
        "n_train_pairs": len(train),
        "n_eval_pairs": len(ev),
        "tasks": sorted({r["task"] for r in traj_records}),
        "runs": sorted({r["run"] for r in traj_records}),
        "holdout_tasks": sorted(HOLDOUT_TASKS),
        "weight_mode": weight_mode,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-tools", type=int, default=2)
    ap.add_argument("--max-tools", type=int, default=60)
    ap.add_argument("--per-task", type=int, default=2)
    ap.add_argument("--max-recovery", type=int, default=20)
    ap.add_argument("--eval-ratio", type=float, default=0.08)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-pairs-per-traj", type=int, default=6)
    args = ap.parse_args()

    successes, recoveries = scan_trials(args.min_tools, args.max_tools)
    print(f"raw_success={len(successes)} raw_recovery_cands={len(recoveries)}")

    successes = dedupe_by_task(successes, per_task=args.per_task)
    print(f"dedup_success={len(successes)} tasks={len({m.task for m,_ in successes})}")

    # Inventory dump
    inv = [asdict(m) for m, _ in successes]
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    write_jsonl(OUT_ROOT / "inventory_success.jsonl", inv)

    def to_recs(items: list[tuple[TrialMeta, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
        return [_messages_to_sft_record(m, msgs) for m, msgs in items]

    # Exclude holdout tasks from all training ablations.
    train_success = [(m, msgs) for m, msgs in successes if m.task not in HOLDOUT_TASKS]
    holdout_success = [(m, msgs) for m, msgs in successes if m.task in HOLDOUT_TASKS]
    print(f"train_success={len(train_success)} holdout_success_instances={len(holdout_success)}")

    evolve = [(m, msgs) for m, msgs in train_success if m.source_bucket == "evolve"]
    fulleval = [(m, msgs) for m, msgs in train_success if m.source_bucket == "fulleval"]
    # If evolve alone is tiny, still keep pure evolve ablation but also a combined note.
    print(f"evolve_train={len(evolve)} fulleval_train={len(fulleval)}")

    # Recovery: exclude holdout tasks, dedupe, cap.
    rec_filt = [(m, msgs) for m, msgs in recoveries if m.task not in HOLDOUT_TASKS]
    rec_filt = dedupe_by_task(rec_filt, per_task=1)
    rec_filt.sort(key=lambda x: (-x[0].n_recovered_tools, -x[0].n_tool_turns))
    rec_filt = rec_filt[: args.max_recovery]
    print(f"recovery_kept={len(rec_filt)}")

    summaries = []
    summaries.append(
        build_ablation(
            "A1_evolve_success",
            to_recs(evolve),
            args.eval_ratio,
            args.seed,
            args.max_pairs_per_traj,
        )
    )
    summaries.append(
        build_ablation(
            "A2_fulleval_success",
            to_recs(fulleval),
            args.eval_ratio,
            args.seed + 1,
            args.max_pairs_per_traj,
        )
    )
    # Combined success (evolve+fulleval) used when A1 alone is too small.
    summaries.append(
        build_ablation(
            "A1b_all_success",
            to_recs(train_success),
            args.eval_ratio,
            args.seed + 2,
            args.max_pairs_per_traj,
        )
    )
    summaries.append(
        build_ablation(
            "A3_success_plus_recovery",
            to_recs(train_success) + to_recs(rec_filt),
            args.eval_ratio,
            args.seed + 3,
            args.max_pairs_per_traj,
        )
    )
    summaries.append(
        build_ablation(
            "A4_reward_weighted",
            to_recs(train_success),
            args.eval_ratio,
            args.seed + 4,
            args.max_pairs_per_traj,
            weight_mode="quality",
        )
    )

    # Holdout trajectory dump for analysis (not used in train).
    write_jsonl(OUT_ROOT / "holdout_success_trajectories.jsonl", to_recs(holdout_success))

    report = {
        "bench_root": str(BENCH),
        "sample16": SAMPLE16,
        "holdout_tasks": sorted(HOLDOUT_TASKS),
        "raw_success": len(inv),
        "summaries": summaries,
        "note": (
            "R0 base-run successful trajectories are essentially absent in this corpus; "
            "A2 uses fixed evolved-harness full-eval successes as the closest available contrast."
        ),
    }
    (OUT_ROOT / "build_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
