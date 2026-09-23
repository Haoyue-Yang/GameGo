from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


CATALOG_PATH = Path(__file__).resolve().parent / "shader_library" / "shadertoy_techniques.jsonl"

EFFECT_TERMS = {
    "water_liquid": (
        "water", "ocean", "sea", "river", "lake", "wave", "foam", "caustic", "liquid",
        "水", "海", "河", "波浪", "液体",
    ),
    "atmosphere_volumetric": (
        "cloud", "fog", "volumetric", "smoke", "nebula", "aurora", "atmosphere",
        "云", "雾", "烟", "体积光", "极光",
    ),
    "energy_scifi": (
        "portal", "vortex", "shield", "hologram", "energy", "plasma", "forcefield",
        "传送门", "漩涡", "护盾", "全息", "能量", "等离子",
    ),
    "fire_heat": ("fire", "flame", "lava", "molten", "燃烧", "火焰", "熔岩"),
    "terrain_environment": (
        "procedural terrain", "alien landscape", "living landscape", "terrain shader",
        "程序化地形", "异星地貌",
    ),
    "abstract_space": (
        "fractal", "raymarch", "ray marching", "kaleidoscope", "psychedelic", "space warp",
        "分形", "光线步进", "万花筒", "空间扭曲",
    ),
}

EXPLICIT_SHADER_TERMS = (
    "shader", "glsl", "fragment shader", "post-processing", "postprocess",
    "着色器", "片元", "后处理",
)

DESIRED_TECHNIQUES = {
    "water_liquid": {
        "procedural_noise", "normal_estimation", "fresnel_response", "reflection", "refraction",
    },
    "atmosphere_volumetric": {
        "fractal_brownian_motion", "domain_warping", "procedural_noise",
        "volumetric_accumulation",
    },
    "energy_scifi": {
        "sdf_raymarching", "signed_distance_fields", "fresnel_response",
        "domain_warping", "procedural_palette",
    },
    "fire_heat": {
        "fractal_brownian_motion", "domain_warping", "procedural_noise",
        "procedural_palette",
    },
    "terrain_environment": {
        "fractal_brownian_motion", "domain_warping", "procedural_noise", "normal_estimation",
    },
    "abstract_space": {
        "sdf_raymarching", "signed_distance_fields", "domain_warping", "procedural_palette",
    },
}


def _artifact(previous: Sequence[Dict[str, str]], stage_id: str) -> Dict[str, Any]:
    content = next((item.get("content") for item in previous if item.get("stage_id") == stage_id), None)
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value.lower()
    elif isinstance(value, dict):
        for item in value.values():
            yield from _text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _text_values(item)


def shader_effect_categories(previous: Sequence[Dict[str, str]]) -> List[str]:
    seed = _artifact(previous, "01_seed_spec")
    blueprint = _artifact(previous, "02_game_blueprint")
    explicit = blueprint.get("shader_opportunities")
    has_explicit_opportunity = isinstance(explicit, list) and bool(explicit)
    if has_explicit_opportunity:
        evidence = " ".join(_text_values(explicit))
    else:
        evidence = " ".join(_text_values({
            "visual_style": seed.get("visual_style"),
            "environment": seed.get("scene_environment"),
            "mechanics": seed.get("core_mechanics"),
            "blueprint_visuals": {
                "three_d_scene_quality_contract": blueprint.get("three_d_scene_quality_contract"),
                "feedback_matrix": blueprint.get("feedback_matrix"),
                "scene_flow": blueprint.get("scene_flow"),
                "implementation_technology": blueprint.get("implementation_technology"),
            },
        }))
    categories = [
        category
        for category, terms in EFFECT_TERMS.items()
        if any(term in evidence for term in terms)
    ]
    if not categories and (
        has_explicit_opportunity
        or any(term in evidence for term in EXPLICIT_SHADER_TERMS)
    ):
        categories.append("abstract_space")
    return categories


def has_shader_opportunity(previous: Sequence[Dict[str, str]]) -> bool:
    return bool(_artifact(previous, "02_game_blueprint") and shader_effect_categories(previous))


def load_catalog(path: Path = CATALOG_PATH) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def retrieve_shader_techniques(
    previous: Sequence[Dict[str, str]],
    *,
    limit: int = 3,
    catalog_path: Path = CATALOG_PATH,
) -> List[Dict[str, Any]]:
    wanted = set(shader_effect_categories(previous))
    if not wanted:
        return []
    seed = _artifact(previous, "01_seed_spec")
    blueprint = _artifact(previous, "02_game_blueprint")
    dimension = str(seed.get("rendering_branch") or seed.get("game_dimension") or "").lower()
    retrieval_evidence = " ".join(_text_values({
        "visual_style": seed.get("visual_style"),
        "environment": seed.get("scene_environment"),
        "shader_opportunities": blueprint.get("shader_opportunities"),
        "three_d_scene_quality_contract": blueprint.get("three_d_scene_quality_contract"),
        "feedback_matrix": blueprint.get("feedback_matrix"),
    }))
    query_tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9_-]{2,}", retrieval_evidence)
        if token not in {
            "the", "and", "with", "from", "into", "game", "shader", "effect",
            "visual", "threejs", "material",
        }
    }
    preferred_modes = (
        {"threejs_material", "threejs_postprocess"}
        if dimension in {"2.5d", "3d"}
        else {"fullscreen_quad", "threejs_postprocess"}
    )
    scored = []
    desired_techniques = set().union(*(DESIRED_TECHNIQUES.get(item, set()) for item in wanted))
    for row in load_catalog(catalog_path):
        categories = set(map(str, row.get("effect_categories") or []))
        techniques = set(map(str, row.get("technique_components") or []))
        modes = set(map(str, row.get("integration_modes") or []))
        score = 8 * len(wanted & categories) + 2 * len(preferred_modes & modes)
        score += 2 * len(desired_techniques & techniques)
        candidate_tokens = {
            token
            for token in re.findall(
                r"[a-z][a-z0-9_-]{2,}",
                " ".join([
                    str(row.get("title") or "").lower(),
                    *map(str, row.get("tags") or []),
                    *categories,
                    *techniques,
                ]),
            )
        }
        score += min(len(query_tokens & candidate_tokens), 4)
        if row.get("complexity") == "high" and not (wanted & categories):
            score -= 2
        score += min(int(row.get("likes") or 0), 1000) / 1000
        if score > 0:
            reference_id = str(row.get("reference_id") or "")
            digest = hashlib.sha256(
                f"{retrieval_evidence}\0{reference_id}".encode("utf-8")
            ).digest()
            # Keep relevance as the main score while rotating similarly useful
            # candidates across distinct approved visual signatures.
            diversity_jitter = int.from_bytes(digest[:2], "big") / 65535 * 4.0
            scored.append((score + diversity_jitter, row))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("reference_id") or "")))
    selected: List[Dict[str, Any]] = []
    signatures = set()
    for _, row in scored:
        signature = tuple(row.get("technique_components") or [])[:3]
        if signature in signatures and len(scored) > limit:
            continue
        signatures.add(signature)
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def build_shader_reference_guidance(
    previous: Sequence[Dict[str, str]],
    *,
    limit: int = 3,
) -> tuple[str, List[str]]:
    rows = retrieve_shader_techniques(previous, limit=limit)
    if not rows:
        return "", []
    lines = [
        "Curated Shadertoy-derived technique references follow. They contain no source code and are not material presets.",
        "Use only relevant technique components, redesign the palette, motion, scale, surface language, lighting response, and gameplay feedback for this game.",
        "Never copy source code or reproduce a reference's complete composition. Treat every license as non-reusable until separately approved.",
    ]
    ids: List[str] = []
    for row in rows:
        reference_id = str(row.get("reference_id") or "")
        ids.append(reference_id)
        lines.append(
            f"- {reference_id}: effects={','.join(row.get('effect_categories') or [])}; "
            f"techniques={','.join(row.get('technique_components') or [])}; "
            f"modes={','.join(row.get('integration_modes') or [])}; "
            f"passes={row.get('pass_count')}; complexity={row.get('complexity')}."
        )
    return "\n".join(lines), ids
