import json
import unittest

from skill_router import route_skill_guidance, select_skill_ids


def previous(seed_spec):
    return [{"stage_id": "01_seed_spec", "content": json.dumps(seed_spec)}]


class SkillRouterTests(unittest.TestCase):
    def test_action_game_gets_relevant_lenses(self):
        items = select_skill_ids("02_game_blueprint", previous({
            "primary_gameplay_type": "combat_action",
            "gameplay_archetype": "boss_rush",
            "game_dimension": "2d",
            "game_scope_profile": "multi_phase_journey",
            "asset_production_mode": "generated_hybrid",
        }))
        self.assertIn("gameplay_core", items)
        self.assertIn("action_combat", items)
        self.assertIn("animation_game_feel", items)
        self.assertLessEqual(len(items), 4)

    def test_simple_puzzle_does_not_receive_combat_or_level_invention(self):
        items = select_skill_ids("02_game_blueprint", previous({
            "primary_gameplay_type": "puzzle_logic",
            "gameplay_archetype": "nonogram",
            "game_dimension": "2d",
            "game_scope_profile": "compact_loop",
            "asset_production_mode": "generated_hybrid",
        }))
        self.assertEqual(items, ["gameplay_core"])

    def test_sprite_card_requires_generated_2d_subject_game(self):
        generated = previous({
            "primary_gameplay_type": "platforming_obstacle", "game_dimension": "2d",
            "asset_production_mode": "generated_hybrid",
        })
        procedural = previous({
            "primary_gameplay_type": "platforming_obstacle", "game_dimension": "2d",
            "asset_production_mode": "procedural_pixel",
        })
        self.assertIn("sprite_pipeline", select_skill_ids("03_asset_contract", generated))
        self.assertNotIn("sprite_pipeline", select_skill_ids("03_asset_contract", procedural))

    def test_guidance_is_small_and_marks_itself_internal(self):
        routing = route_skill_guidance("03_asset_contract", previous({
            "primary_gameplay_type": "combat_action", "game_dimension": "2d",
            "asset_production_mode": "generated_hybrid",
        }))
        self.assertLess(len(routing.guidance), 3000)
        self.assertIn("not extra product requirements", routing.guidance)
        self.assertEqual(routing.audit_record()["policy"], "internal_insight_only_not_final_query_content")


if __name__ == "__main__":
    unittest.main()
