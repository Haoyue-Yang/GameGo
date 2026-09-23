#!/usr/bin/env python3
"""Build a compact, code-free Shadertoy technique index for the Pipeline."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List


EFFECT_RULES = {
    "water_liquid": (
        "water", "ocean", "sea", "river", "lake", "foam", "caustic", "liquid", "pool",
    ),
    "atmosphere_volumetric": (
        "cloud", "fog", "volumetric", "smoke", "nebula", "sky", "aurora", "atmosphere",
    ),
    "energy_scifi": (
        "portal", "vortex", "shield", "hologram", "energy", "plasma", "neon", "laser", "forcefield",
    ),
    "fire_heat": ("fire", "flame", "lava", "molten", "explosion", "sun"),
    "terrain_environment": (
        "terrain", "landscape", "mountain", "rock", "cave", "planet", "forest", "city",
    ),
    "abstract_space": (
        "fractal", "raymarch", "ray marching", "mandelbulb", "kaleidoscope", "tunnel", "warp",
        "psychedelic", "space",
    ),
}

TECHNIQUE_RULES = {
    "sdf_raymarching": ("raymarch", "ray marching", "marching"),
    "signed_distance_fields": ("sdf", "signed distance", "map("),
    "fractal_brownian_motion": ("fbm", "fBm"),
    "domain_warping": ("domain warp", "domainwarp", "warp"),
    "procedural_noise": ("noise", "simplex", "perlin"),
    "voronoi_cells": ("voronoi", "worley"),
    "fresnel_response": ("fresnel",),
    "normal_estimation": ("normal", "calcNormal", "getNormal"),
    "reflection": ("reflect(", "reflection"),
    "refraction": ("refract(", "refraction"),
    "volumetric_accumulation": ("volumetric", "density", "transmittance"),
    "texture_sampling": ("iChannel", "texture(", "texture2D("),
    "screen_space_derivatives": ("fwidth(", "dFdx(", "dFdy("),
    "hash_noise": ("hash(", "hash11", "hash21", "hash22", "hash33"),
    "procedural_palette": ("palette(", "cosine palette"),
}


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no} must contain a JSON object")
            yield row


def matching_labels(text: str, rules: Dict[str, tuple[str, ...]]) -> List[str]:
    lowered = text.lower()
    def contains(term: str) -> bool:
        needle = term.lower()
        if re.fullmatch(r"[\w ]+", needle):
            return bool(re.search(rf"\b{re.escape(needle)}\b", lowered))
        return needle in lowered
    return [
        label
        for label, terms in rules.items()
        if any(contains(term) for term in terms)
    ]


def license_status(code: str) -> str:
    lowered = code.lower()
    if (
        "cc-by-nc-sa" in lowered
        or "attribution-noncommercial-sharealike" in lowered
        or "attribution noncommercial sharealike" in lowered
    ):
        return "restricted_noncommercial_or_sharealike"
    if "copyright" in lowered or "all rights reserved" in lowered:
        return "copyright_notice_present"
    if "spdx-license-identifier" in lowered or re.search(r"\blicen[cs]e\b", lowered):
        return "license_notice_requires_review"
    return "unknown"


def integration_modes(categories: List[str], techniques: List[str]) -> List[str]:
    modes: List[str] = []
    if any(x in categories for x in ("water_liquid", "energy_scifi", "fire_heat", "terrain_environment")):
        modes.append("threejs_material")
    if any(x in techniques for x in ("sdf_raymarching", "volumetric_accumulation")):
        modes.extend(("fullscreen_quad", "threejs_postprocess"))
    if not modes:
        modes.extend(("fullscreen_quad", "threejs_postprocess"))
    return list(dict.fromkeys(modes))


def compact_row(row: Dict[str, Any]) -> Dict[str, Any]:
    passes = [item for item in (row.get("passes") or []) if isinstance(item, dict)]
    code = "\n".join(str(item.get("code") or "") for item in passes)
    tags = [str(tag).strip().lower() for tag in (row.get("tags") or []) if str(tag).strip()]
    category_searchable = " ".join([
        str(row.get("title") or ""),
        str(row.get("description") or ""),
        *tags,
    ])
    technique_searchable = " ".join([category_searchable, code])
    categories = matching_labels(category_searchable, EFFECT_RULES)
    techniques = matching_labels(technique_searchable, TECHNIQUE_RULES)
    if not techniques:
        techniques = ["procedural_fragment_composition"]
    pass_types = list(dict.fromkeys(str(item.get("passType") or item.get("label") or "Image") for item in passes))
    code_chars = len(code)
    if len(passes) > 2 or code_chars > 30000 or "sdf_raymarching" in techniques:
        complexity = "high"
    elif len(passes) > 1 or code_chars > 9000:
        complexity = "medium"
    else:
        complexity = "low"
    return {
        "reference_id": f"shadertoy:{row.get('id')}",
        "source_id": str(row.get("id") or ""),
        "title": str(row.get("title") or "").strip(),
        "url": str(row.get("url") or "").strip(),
        "tags": tags[:12],
        "effect_categories": categories,
        "technique_components": techniques[:10],
        "integration_modes": integration_modes(categories, techniques),
        "pass_types": pass_types,
        "pass_count": len(passes),
        "complexity": complexity,
        "license_status": license_status(code),
        "usage_policy": "technique_reference_only_no_code_copy",
        "likes": int(row.get("likes") or 0),
        "viewed": int(row.get("viewed") or 0),
    }


def build_catalog(input_path: Path) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    input_rows = 0
    duplicate_ids: List[str] = []
    for row in read_jsonl(input_path):
        input_rows += 1
        source_id = str(row.get("id") or "").strip()
        if not source_id:
            continue
        if source_id in unique:
            duplicate_ids.append(source_id)
            continue
        unique[source_id] = compact_row(row)
    catalog = sorted(unique.values(), key=lambda item: (-item["likes"], item["source_id"]))
    report = {
        "input": str(input_path),
        "input_rows": input_rows,
        "unique_rows": len(catalog),
        "duplicate_source_ids": sorted(set(duplicate_ids)),
        "contains_raw_shader_code": False,
        "contains_html": False,
        "usage_policy": "technique_reference_only_no_code_copy",
    }
    return catalog, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_jsonl", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "shader_library" / "shadertoy_techniques.jsonl",
    )
    args = parser.parse_args()
    catalog, report = build_catalog(args.input_jsonl)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for item in catalog:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    report_path = args.output.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
