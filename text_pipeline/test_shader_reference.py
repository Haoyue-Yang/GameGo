import json
import unittest

from shader_reference import (
    CATALOG_PATH,
    has_shader_opportunity,
    load_catalog,
    retrieve_shader_techniques,
    shader_effect_categories,
)


def artifacts(seed, blueprint):
    return [
        {"stage_id": "01_seed_spec", "content": json.dumps(seed)},
        {"stage_id": "02_game_blueprint", "content": json.dumps(blueprint)},
    ]


class ShaderReferenceTests(unittest.TestCase):
    def test_generic_3d_does_not_trigger_shader_route(self):
        previous = artifacts(
            {"rendering_branch": "3d", "visual_style": "low-poly village"},
            {"scene_flow": [{"id": "play", "purpose": "collect fruit"}]},
        )
        self.assertFalse(has_shader_opportunity(previous))
        self.assertEqual(shader_effect_categories(previous), [])

    def test_water_effect_retrieves_diverse_code_free_techniques(self):
        previous = artifacts(
            {"rendering_branch": "3d", "visual_style": "stylized ocean"},
            {
                "shader_opportunities": [{
                    "effect_role": "reactive ocean surface",
                    "effect_categories": ["water"],
                    "visual_signature_requirements": "angular teal waves with amber wake foam",
                }]
            },
        )
        rows = retrieve_shader_techniques(previous)
        self.assertTrue(has_shader_opportunity(previous))
        self.assertIn("water_liquid", shader_effect_categories(previous))
        self.assertGreaterEqual(len(rows), 1)
        self.assertLessEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row["usage_policy"], "technique_reference_only_no_code_copy")
            self.assertNotIn("code", row)
            self.assertNotIn("html", row)

    def test_bundled_catalog_is_deduplicated_and_code_free(self):
        rows = load_catalog(CATALOG_PATH)
        self.assertEqual(len(rows), 50)
        self.assertEqual(len({row["source_id"] for row in rows}), 50)
        self.assertTrue(all("code" not in row and "html" not in row for row in rows))

    def test_same_effect_uses_blueprint_for_stable_reference_diversity(self):
        neon = artifacts(
            {"rendering_branch": "3d", "visual_style": "neon geometric ocean"},
            {"shader_opportunities": [{"effect_role": "holographic water with sharp cyan waves"}]},
        )
        natural = artifacts(
            {"rendering_branch": "3d", "visual_style": "misty natural river"},
            {"shader_opportunities": [{"effect_role": "muddy reflective water with soft ripples"}]},
        )
        neon_ids = [row["reference_id"] for row in retrieve_shader_techniques(neon)]
        natural_ids = [row["reference_id"] for row in retrieve_shader_techniques(natural)]
        self.assertEqual(neon_ids, [
            row["reference_id"] for row in retrieve_shader_techniques(neon)
        ])
        self.assertNotEqual(neon_ids, natural_ids)


if __name__ == "__main__":
    unittest.main()
