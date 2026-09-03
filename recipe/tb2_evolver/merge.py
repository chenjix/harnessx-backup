"""Synthesize a harness that keeps the additive edits of several candidates.

Used by fan-out v2 when one proposal newly solves a failed probe task and
another preserves already-solved ones: union their processors/tools/sidecars
onto the parent rather than picking only one.

Removals are applied only when every parent in the merge agrees — cutting a
mechanism that another sibling was relying on would throw away the point of
the merge. ``file://`` processor paths are rewritten to the merge directory so
the synthesized config stays self-contained after promotion.
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
from pathlib import Path
from typing import Sequence

from harnessx.core.config_schema import ToolRegistryConfig
from harnessx.core.harness import HarnessConfig
from harnessx.meta_harness.agent import _label_from_target

logger = logging.getLogger(__name__)

__all__ = ["merge_harness_configs"]

_SIDECAR_DIRS = ("processors", "tools", "templates")


def _as_plain(entry: object) -> dict:
    if isinstance(entry, dict):
        return copy.deepcopy(entry)
    try:
        from omegaconf import OmegaConf

        return copy.deepcopy(OmegaConf.to_container(entry, resolve=True))  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        return dict(entry)  # type: ignore[arg-type]


def _copy_sidecars(src_dir: Path, dst_dir: Path) -> None:
    for name in _SIDECAR_DIRS:
        src = src_dir / name
        if not src.is_dir():
            continue
        dst = dst_dir / name
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir():
                if target.exists():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copytree(item, target)
            elif target.exists() and target.read_bytes() != item.read_bytes():
                # Same filename, different body: keep both under a src-dir prefix.
                alt = dst / f"{src_dir.name}_{item.name}"
                shutil.copy2(item, alt)
            else:
                shutil.copy2(item, target)


def _rewrite_file_uris(text: str, src_dirs: Sequence[Path], dst_dir: Path) -> str:
    """Point ``file://`` targets that lived under a child dir at the merge dir."""
    dst = str(dst_dir.resolve())
    # Longest prefix first so a nested path cannot clobber a shorter sibling.
    roots = sorted(
        {str(p.resolve()) for p in src_dirs} | {str(p) for p in src_dirs},
        key=len,
        reverse=True,
    )
    for root in roots:
        text = text.replace(f"file://{root}", f"file://{dst}")
    return text


def _union_tools(parent: HarnessConfig, children: Sequence[HarnessConfig]) -> ToolRegistryConfig:
    src = parent.tool_registry
    builtin = list(getattr(src, "builtin", None) or []) if src is not None else []
    custom = list(getattr(src, "custom", None) or []) if src is not None else []
    seen_b, seen_c = set(builtin), set(custom)
    for child in children:
        ctr = getattr(child, "tool_registry", None)
        if ctr is None:
            continue
        for name in getattr(ctr, "builtin", None) or []:
            if name not in seen_b:
                builtin.append(name)
                seen_b.add(name)
        for name in getattr(ctr, "custom", None) or []:
            if name not in seen_c:
                custom.append(name)
                seen_c.add(name)
    return ToolRegistryConfig(builtin=builtin, custom=custom)


def _union_processors(
    parent: HarnessConfig,
    children: Sequence[HarnessConfig],
) -> list[dict]:
    """Parent processors, then each child's newly-added labels (first wins)."""
    out: list[dict] = [_as_plain(p) for p in (parent.processors or [])]
    seen = {
        _label_from_target(str(p.get("_target_", "") or ""))
        for p in out
        if p.get("_target_")
    }
    for child in children:
        for entry in child.processors or []:
            plain = _as_plain(entry)
            label = _label_from_target(str(plain.get("_target_", "") or ""))
            if not label or label in seen:
                continue
            out.append(plain)
            seen.add(label)
    return out


def _pick_system_prompt(
    parent_config: Path,
    child_configs: Sequence[Path],
    dst_dir: Path,
) -> None:
    """Prefer the first child that actually edited the prompt; else parent."""
    parent_prompt = parent_config.parent / "system_prompt.txt"
    parent_text = parent_prompt.read_text() if parent_prompt.is_file() else None
    chosen: Path | None = None
    for cfg in child_configs:
        prompt = cfg.parent / "system_prompt.txt"
        if not prompt.is_file():
            continue
        text = prompt.read_text()
        if parent_text is None or text != parent_text:
            chosen = prompt
            break
    src = chosen or (parent_prompt if parent_prompt.is_file() else None)
    if src is not None:
        shutil.copy2(src, dst_dir / "system_prompt.txt")


def merge_harness_configs(
    parent_config: Path,
    child_configs: Sequence[Path],
    out_dir: Path,
    *,
    source_idxs: Sequence[int] | None = None,
) -> Path:
    """Write a union harness under ``out_dir`` and return its ``config.yaml``.

    ``child_configs`` order matters: the first child is the "gainer" whose
    prompt wins when several children edited ``system_prompt.txt``.
    """
    parent_config = Path(parent_config).resolve()
    children = [Path(c).resolve() for c in child_configs]
    if len(children) < 2:
        raise ValueError("merge_harness_configs needs at least two children")
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    parent = HarnessConfig.from_yaml_file(parent_config)
    loaded = [HarnessConfig.from_yaml_file(c) for c in children]

    merged = parent.copy(
        processors=_union_processors(parent, loaded),
        tool_registry=_union_tools(parent, loaded),
    )
    cfg_path = out_dir / "config.yaml"
    yaml_text = merged.to_yaml()
    yaml_text = _rewrite_file_uris(yaml_text, [c.parent for c in children], out_dir)
    cfg_path.write_text(yaml_text, encoding="utf-8")

    for cfg in children:
        _copy_sidecars(cfg.parent, out_dir)
    _pick_system_prompt(parent_config, children, out_dir)

    meta = {
        "merged_from": list(source_idxs) if source_idxs is not None else [c.parent.name for c in children],
        "children": [str(c) for c in children],
        "parent": str(parent_config),
    }
    (out_dir / "merge.json").write_text(json.dumps(meta, indent=2) + "\n")
    logger.info(
        "[merge] wrote %s from %s",
        cfg_path,
        ", ".join(c.parent.name for c in children),
    )
    return cfg_path
