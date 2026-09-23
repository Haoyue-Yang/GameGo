from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from shader_reference import build_shader_reference_guidance, has_shader_opportunity


CARD_DIR = Path(__file__).resolve().parent / "skill_cards"


@dataclass(frozen=True)
class SkillRouting:
    stage_id: str
    selected_skills: List[str]
    guidance: str
    reference_ids: List[str]

    def audit_record(self) -> Dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "selected_skills": self.selected_skills,
            "reference_ids": self.reference_ids,
            "injected_characters": len(self.guidance),
            "policy": (
                "internal_insight_only_not_final_query_content"
                if self.selected_skills
                else "disabled_by_run_parameter"
            ),
        }


def _load_registry() -> Dict[str, Any]:
    return json.loads((CARD_DIR / "registry.json").read_text(encoding="utf-8"))


def _text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value.lower()
    elif isinstance(value, dict):
        for item in value.values():
            yield from _text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _text_values(item)


def _seed_spec(previous: List[Dict[str, str]]) -> Dict[str, Any]:
    content = next((item.get("content") for item in previous if item.get("stage_id") == "01_seed_spec"), None)
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def select_skill_ids(stage_id: str, previous: List[Dict[str, str]]) -> List[str]:
    """Select small insight cards from approved artifacts, never from randomness."""
    if stage_id == "01_seed_spec":
        return ["routing_observer"]
    if stage_id not in {"02_game_blueprint", "03_asset_contract"}:
        return []

    spec = _seed_spec(previous)
    gameplay_type = str(spec.get("primary_gameplay_type") or "").lower()
    archetype = str(spec.get("gameplay_archetype") or "").lower()
    dimension = str(spec.get("game_dimension") or "").lower()
    scope = spec.get("game_scope_profile")
    scope_name = str(scope.get("profile") if isinstance(scope, dict) else scope or "").lower()
    asset_mode = str(spec.get("asset_production_mode") or "").lower()
    evidence = " ".join(_text_values({
        "type": gameplay_type,
        "archetype": archetype,
        "tags": spec.get("secondary_gameplay_tags"),
        "mechanics": spec.get("core_mechanics"),
        "style": spec.get("visual_style"),
        "environment": spec.get("scene_environment"),
    }))

    combat_types = {"combat_action", "stealth_pursuit", "tower_defense"}
    spatial_types = {
        "platforming_obstacle", "driving_racing", "rpg_adventure", "exploration_open_world",
        "stealth_pursuit", "sports_competition", "survival_gathering",
    }
    feel_types = {
        "combat_action", "platforming_obstacle", "driving_racing", "sports_competition",
        "rhythm_music", "casual_reflex_arcade",
    }
    audio_signal = gameplay_type in {"rhythm_music", "combat_action", "narrative_choice"} or _has_any(
        evidence, ("music", "rhythm", "audio", "sound", "horror", "stealth", "音乐", "节奏", "音效", "恐怖")
    )

    selected: List[str]
    if stage_id == "02_game_blueprint":
        selected = ["gameplay_core"]
        if gameplay_type in combat_types or _has_any(evidence, ("boss", "combat", "fight", "attack", "战斗", "攻击")):
            selected.append("action_combat")
        if gameplay_type in spatial_types or dimension == "3d" or scope_name == "multi_phase_journey":
            selected.append("level_flow")
        if gameplay_type in feel_types:
            selected.append("animation_game_feel")
        if audio_signal:
            selected.append("audio_web")
        return selected[:4]

    selected = ["art_bible_compact", "asset_source_gate"]
    if has_shader_opportunity(previous):
        selected.append("shader_fx")
    if audio_signal:
        selected.append("audio_web")
    if dimension == "2d" and asset_mode == "generated_hybrid" and gameplay_type in {
        "combat_action", "platforming_obstacle", "rpg_adventure", "tower_defense",
        "survival_gathering", "casual_reflex_arcade",
    }:
        selected.append("sprite_pipeline")
    return selected[:4]


def route_skill_guidance(
    stage_id: str,
    previous: List[Dict[str, str]],
    *,
    enabled: bool = True,
) -> SkillRouting:
    if not enabled:
        return SkillRouting(stage_id, [], "", [])
    registry = _load_registry()
    selected = select_skill_ids(stage_id, previous)
    sections: List[str] = []
    for skill_id in selected:
        entry = registry["cards"].get(skill_id)
        if not isinstance(entry, dict):
            raise ValueError(f"Unknown skill card: {skill_id}")
        body = (CARD_DIR / entry["file"]).read_text(encoding="utf-8").strip()
        sections.append(body)
    reference_ids: List[str] = []
    if stage_id == "03_asset_contract" and "shader_fx" in selected:
        reference_guidance, reference_ids = build_shader_reference_guidance(previous)
        if reference_guidance:
            sections.append(reference_guidance)
    if not sections:
        return SkillRouting(stage_id, [], "", [])
    prefix = (
        "These are internal design lenses, not extra product requirements. Apply only where supported by the approved "
        "source facts. Do not mention skill names, routing, cards, or this guidance in the artifact. Do not add systems "
        "merely to satisfy a lens.\n\n"
    )
    return SkillRouting(stage_id, selected, prefix + "\n\n".join(sections), reference_ids)
