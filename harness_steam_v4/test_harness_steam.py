import json
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import lifecycle_harness_steam as harness


class PipelineTests(unittest.TestCase):
    def test_procedural_pixel_query_forbids_image_generation(self):
        spec = {
            "product_identity": {"asset_production_mode": "procedural_pixel"},
            "gameplay_spec": {}, "presentation_and_technology": {}, "asset_contract": {},
        }
        query = harness.build_rle_query(harness.query_seed("8-bit platformer"), [], "", spec)
        self.assertIn("Do not call generate_image or fetch_media", query)
        self.assertNotIn("Execute each generation prompt", query)

    def test_final_query_reminds_3d_directional_movement(self):
        spec = {
            "product_identity": {
                "asset_production_mode": "procedural_pixel",
                "rendering_branch": "3d",
            },
            "gameplay_spec": {"controls": {"movement": "WASD or arrow keys"}},
            "presentation_and_technology": {},
            "asset_contract": {"required_assets": [{
                "id": "UI_TEST", "expected_file_path": "assets/ui/test.js",
                "source": "canvas_draw", "composition_and_layers": "test panel",
            }]},
        }
        query = harness.compose_final_rle_query("# Game", spec)
        self.assertEqual(
            query.count(harness.SCREEN_SPACE_DIRECTIONAL_CONTROL_REMINDER), 1
        )
        self.assertNotIn("test every bound direction separately", query)
        spec["gameplay_spec"] = {}
        query_without_movement = harness.compose_final_rle_query("# Game", spec)
        self.assertNotIn(harness.SCREEN_SPACE_DIRECTIONAL_CONTROL_REMINDER, query_without_movement)
        spec["gameplay_spec"] = {"controls": {"movement": "WASD"}}
        spec["product_identity"]["rendering_branch"] = "2.5d"
        query_2_5d = harness.compose_final_rle_query("# Game", spec)
        self.assertNotIn(harness.SCREEN_SPACE_DIRECTIONAL_CONTROL_REMINDER, query_2_5d)
        spec["product_identity"]["rendering_branch"] = "2d"
        query_2d = harness.compose_final_rle_query("# Game", spec)
        self.assertNotIn(harness.SCREEN_SPACE_DIRECTIONAL_CONTROL_REMINDER, query_2d)

    def test_compact_automatically_repairs_remaining_chinese(self):
        args = SimpleNamespace(
            base_url="http://example.test", model="test-model",
            compact_max_tokens=5000, timeout_s=30, api_retries=0,
            api_retry_sleep_s=0,
        )
        first = "# Game\n\n" + ("Implement the complete game and preserve every asset. " * 12) + "禁止白色背景"
        repaired = "# Game\n\n" + ("Implement the complete game and preserve every asset. " * 12) + "No white background."
        with mock.patch.object(harness, "call_chat_completion", side_effect=[first, repaired]) as call:
            result = harness.compact_rle_query(args=args, api_key="key", raw_query="raw")
        self.assertEqual(result, repaired)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[1].kwargs["temperature"], 0.0)

    def test_compact_recompacts_oversized_semantic_output(self):
        args = SimpleNamespace(
            base_url="http://example.test", model="test-model",
            compact_max_tokens=5000, timeout_s=30, api_retries=0,
            api_retry_sleep_s=0,
        )
        oversized = "# Game\n\n" + (
            "repeated requirement " * (harness.COMPACT_TARGET_MAX_WORDS + 100)
        )
        compacted = "# Game\n\n" + " ".join(
            f"requirement{i}" for i in range(700)
        )
        self.assertFalse(harness.compact_is_calibrated(oversized))
        self.assertTrue(harness.compact_is_calibrated(compacted))
        with mock.patch.object(
            harness, "call_chat_completion", side_effect=[oversized, compacted]
        ) as call:
            result = harness.compact_rle_query(
                args=args, api_key="key", raw_query="raw"
            )
        self.assertEqual(result, compacted)
        self.assertEqual(call.call_count, 2)
        second_messages = call.call_args_list[1].kwargs["messages"]
        self.assertEqual(second_messages[0]["content"], harness.DOMAIN_RECOMPACT_SYSTEM)
        self.assertEqual(call.call_args_list[1].kwargs["temperature"], 0.0)

    def test_compact_rejects_oversized_second_pass(self):
        args = SimpleNamespace(
            base_url="http://example.test", model="test-model",
            compact_max_tokens=5000, timeout_s=30, api_retries=0,
            api_retry_sleep_s=0,
        )
        oversized = "# Game\n\n" + (
            "repeated requirement " * (harness.COMPACT_TARGET_MAX_WORDS + 100)
        )
        with mock.patch.object(
            harness, "call_chat_completion", side_effect=[oversized, oversized]
        ):
            with self.assertRaisesRegex(ValueError, "exceeded its enforced budget"):
                harness.compact_rle_query(args=args, api_key="key", raw_query="raw")

    def test_compact_recompacts_repeated_topic_ownership_within_budget(self):
        args = SimpleNamespace(
            base_url="http://example.test", model="test-model",
            compact_max_tokens=5000, timeout_s=30, api_retries=0,
            api_retry_sleep_s=0,
        )
        repeated = (
            "# Game\n\n" + "identity " * 150
            + "\n\n## Ordered Gameplay Flow\n" + "phase detail " * 120
            + "\n\n## Scene Flow\n" + "the same phase detail " * 120
            + "\n\n## Progression, Win/Lose/Restart\n" + "the same result detail " * 80
        )
        compacted = "# Game\n\n" + " ".join(
            f"requirement{i}" for i in range(700)
        )
        reasons = harness.compact_repetition_reasons(repeated)
        self.assertIn("parallel ordered-flow and scene-flow sections", reasons)
        self.assertIn("win/lose/restart repeated outside ordered flow", reasons)
        with mock.patch.object(
            harness, "call_chat_completion", side_effect=[repeated, compacted]
        ) as call:
            result = harness.compact_rle_query(
                args=args, api_key="key", raw_query="raw"
            )
        self.assertEqual(result, compacted)
        self.assertEqual(call.call_count, 2)

    def test_resume_detects_consecutive_stage_prefix_and_successful_rle(self):
        stages = harness.parse_stages(Path(__file__).with_name("stages.json"))
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run_1"
            first = run / "stages/01_seed_spec"
            second = run / "stages/02_game_blueprint"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "seed_spec.json").write_text('{"ok": true}')
            (second / "game_blueprint.json").write_text('{"ok": true}')
            self.assertEqual(harness.completed_stage_prefix(run, stages), 2)
            third = run / "stages/03_asset_contract"
            third.mkdir(parents=True)
            (third / "asset_manifest.json").write_text('{broken')
            self.assertEqual(harness.completed_stage_prefix(run, stages), 2)
            (third / "asset_manifest.json").write_text('{"ok": true}')
            fourth = run / "stages/04_implementation"
            fourth.mkdir(parents=True)
            (fourth / "implementation_response.md").write_text('done')
            (fourth / "rle_summary.json").write_text('{"success": false}')
            self.assertEqual(harness.completed_stage_prefix(run, stages), 3)
            (fourth / "rle_summary.json").write_text('{"success": true}')
            self.assertEqual(harness.completed_stage_prefix(run, stages), 4)

    def test_resume_selects_latest_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "run_20260101_000000"
            new = root / "run_20260102_000000"
            old.mkdir(); new.mkdir()
            self.assertEqual(harness.latest_resumable_run(root), new)

    def test_resume_prefers_more_complete_run_over_newer_failure(self):
        stages = harness.parse_stages(Path(__file__).with_name("stages.json"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            complete = root / "run_20260101_000000"
            failed_new = root / "run_20260102_000000"
            for stage in stages[:2]:
                path = complete / "stages" / stage.stage_id
                path.mkdir(parents=True)
                (path / stage.artifact_file).write_text('{"ok": true}')
            failed_new.mkdir()
            self.assertEqual(harness.latest_resumable_run(root, stages), complete)

    @unittest.skip("Historical cross-version comparison requires an unreleased version.")
    def test_steam_flow_and_asset_prompts_diverge_from_v2(self):
        steam_dir = Path(__file__).parent
        v2_dir = steam_dir.with_name("harness_v2")
        self.assertEqual((steam_dir / "prompts" / "04_implementation.md").read_bytes(), (v2_dir / "prompts" / "04_implementation.md").read_bytes())
        seed_prompt = (steam_dir / "prompts" / "01_seed_spec.md").read_text()
        self.assertIn("gameplay_flow_contract", seed_prompt)
        blueprint_prompt = (steam_dir / "prompts" / "02_game_blueprint.md").read_text()
        self.assertIn("rule_refs", blueprint_prompt)
        self.assertIn("gameplay_flow_contract", blueprint_prompt)
        self.assertIn("1800–2800 tokens", blueprint_prompt)
        asset_prompt = (steam_dir / "prompts" / "03_asset_contract.md").read_text()
        self.assertIn("6–8 张", asset_prompt)
        self.assertIn("不得超过 10 张", asset_prompt)
        self.assertIn("transparent_background: true", asset_prompt)

    @unittest.skip("Historical cross-version comparison requires an unreleased version.")
    def test_canonical_selector_intentionally_diverges_from_v2(self):
        v2_path = Path(__file__).parent.with_name("harness_v2") / "lifecycle_harness_v2.py"
        spec = importlib.util.spec_from_file_location("harness_v2_alignment", v2_path)
        v2 = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = v2
        spec.loader.exec_module(v2)
        seed3 = harness.query_seed("game")
        seed2 = v2.query_seed("game")
        previous = [
            {"stage_id": "01_seed_spec", "content": json.dumps({"prd_request": "Fight", "game_dimension": "2d"})},
            {"stage_id": "02_game_blueprint", "content": json.dumps({"core_loop": ["fight"], "scene_flow": [{"id": "battle"}]})},
            {"stage_id": "03_asset_contract", "content": json.dumps({
                "required_asset_ids": ["fighters"],
                "visual_assets": [{"id": "fighters", "source": "generate_image", "required_in_stage_4": True,
                                   "generation_prompt": "two fighters", "expected_file_path": "assets/fighters.webp"}],
            })},
        ]
        query = harness.build_rle_query(seed3, previous, "")
        self.assertNotEqual(query, v2.build_rle_query(seed2, previous, ""))
        self.assertIn("Canonical selected specification", query)
        self.assertNotIn('"prd_request"', query)

    def test_config_has_four_pipeline_stages(self):
        stages = harness.parse_stages(Path(__file__).with_name("stages.json"))
        self.assertEqual([x.stage_id for x in stages], [
            "01_seed_spec", "02_game_blueprint", "03_asset_contract", "04_implementation",
        ])

    def test_steam_images_only_enter_stage_one(self):
        row = {"appid": 1, "appdetails": {"data": {
            "name": "Reference", "genres": [], "categories": [],
            "about_the_game": "Play", "screenshots": [{"path_full": "https://example.com/a.jpg"}],
        }}}
        seed = harness.steam_seed_from_row(row)
        with tempfile.TemporaryDirectory() as temp:
            prompt = Path(temp) / "prompt.md"
            prompt.write_text("system")
            first = harness.Stage("01_seed_spec", "Seed", prompt, "seed_spec.json")
            self.assertIsInstance(harness.build_stage_messages(seed=seed, stage=first, previous=[], feedback="")[1]["content"], list)
        second = harness.parse_stages(Path(__file__).with_name("stages.json"))[1]
        previous = [{
            "stage_id": "01_seed_spec", "stage_name": "Seed Distillation",
            "content": '{"rendering_branch":"2d","game_dimension":"2d","primary_gameplay_type":"card_tabletop","gameplay_archetype":"card_shedding"}', "path": "seed_spec.json",
        }]
        self.assertIsInstance(harness.build_stage_messages(seed=seed, stage=second, previous=previous, feedback="")[1]["content"], str)

    def test_curated_steam20_envelope_and_data_id(self):
        row = {
            "sample_index": 7,
            "appid": 1809540,
            "title": "Envelope Title",
            "local_image_paths": ["images/07/screenshot_01.jpg"],
            "source_image_urls": [f"https://example.com/source_{i}.jpg" for i in range(7)],
            "steam_raw": {"appdetails": {"data": {
                "name": "Canonical Reference", "steam_appid": 1809540,
                "genres": [{"description": "Action"}],
                "categories": [{"description": "Single-player"}],
                "about_the_game": "Fight and explore.",
                "screenshots": [{"path_full": "https://example.com/fallback.jpg"}],
            }}},
        }
        seed = harness.steam_seed_from_row(row)
        self.assertEqual(seed.data_id, "Steam_Data7")
        self.assertEqual(seed.metadata["data_id"], "Steam_Data7")
        self.assertEqual(seed.metadata["steam_appid"], "1809540")
        self.assertEqual(seed.metadata["sample_index"], 7)
        self.assertEqual(seed.metadata["local_image_paths"], ["images/07/screenshot_01.jpg"])
        self.assertEqual(len(seed.image_urls), 5)
        self.assertEqual(seed.image_urls[0], "https://example.com/source_0.jpg")
        self.assertNotIn("fallback.jpg", seed.image_urls)

    def test_normalized_non_steam_reference_without_media(self):
        row = {
            "content": {
                "clean_title": "Reference Puzzle",
                "clean_description": "Remove every screw.",
                "clean_instructions": "Tap or drag to play.",
            },
            "dedup_metadata": {
                "origin": "web_games",
                "entity_id": "web_games:abcdef",
            },
            "source_metadata": {
                "categories": ["Puzzle"],
                "tags": ["Logic"],
            },
        }
        seed = harness.steam_seed_from_row(row)
        self.assertEqual(seed.source_kind, "reference_game")
        self.assertEqual(seed.data_id, "web_games_abcdef")
        self.assertEqual(seed.image_urls, [])
        self.assertIn("Tap or drag to play.", seed.prompt_text)
        self.assertEqual(seed.metadata["gameplay_categories"], ["Puzzle", "Logic"])

    def test_compiler_produces_compact_execution_spec(self):
        seed = harness.query_seed("game")
        previous = [
            {"stage_id": "01_seed_spec", "content": json.dumps({"prd_request": "Fight", "game_dimension": "2d"})},
            {"stage_id": "02_game_blueprint", "content": json.dumps({"core_loop": ["fight"], "scene_flow": [{"id": "battle"}]})},
            {"stage_id": "03_asset_contract", "content": json.dumps({
                "required_asset_ids": ["fighters"],
                "visual_assets": [{"id": "fighters", "source": "generate_image", "required_in_stage_4": True,
                                   "generation_prompt": "two fighters", "expected_file_path": "assets/fighters.webp"}],
            })},
        ]
        encoded = json.dumps(harness.select_canonical_spec(seed, previous))
        self.assertIn("two fighters", encoded)
        self.assertIn("battle", encoded)

    def test_scene_composition_can_satisfy_required_asset_id(self):
        seed = harness.query_seed("game")
        previous = [
            {"stage_id": "01_seed_spec", "content": json.dumps({"prd_request": "Fight"})},
            {"stage_id": "02_game_blueprint", "content": json.dumps({"core_loop": ["fight"]})},
            {"stage_id": "03_asset_contract", "content": json.dumps({
                "required_asset_ids": ["battle_composition"],
                "scene_composition_assets": [{"id": "battle_composition", "generation_prompt": "two fighters clash"}],
            })},
        ]
        encoded = json.dumps(harness.select_canonical_spec(seed, previous))
        self.assertIn("battle_composition", encoded)

    def test_generated_three_d_asset_keeps_image_prompt_and_full_recipe(self):
        seed = harness.query_seed("game")
        asset = {
            "id": "hero", "source": "generate_image", "required_in_stage_4": True,
            "generation_prompt": "painted hero texture reference",
            "geometry_or_model_strategy": "procedural articulated model",
            "named_visible_parts": ["head", "torso", "arm"],
            "material_recipe": "toon material", "texture_recipe": "generated texture",
            "dimensions_or_scale": "2 meters", "placement_in_scene": "foreground",
            "animation_or_motion_loop": "idle and attack", "lighting_dependency": "rim light",
        }
        previous = [
            {"stage_id": "01_seed_spec", "content": json.dumps({"prd_request": "Fight"})},
            {"stage_id": "02_game_blueprint", "content": json.dumps({"core_loop": ["fight"]})},
            {"stage_id": "03_asset_contract", "content": json.dumps({
                "required_asset_ids": ["hero"], "three_d_assets": [asset],
            })},
        ]
        encoded = json.dumps(harness.select_canonical_spec(seed, previous))
        for value in ["painted hero texture reference", "procedural articulated model", "toon material",
                      "generated texture", "idle and attack", "rim light"]:
            self.assertIn(value, encoded)

    def test_canonical_selector_preserves_shared_prompt_preset_and_asset_delta(self):
        seed = harness.query_seed("game")
        previous = [
            {"stage_id": "01_seed_spec", "content": json.dumps({"game_dimension": "2d"})},
            {"stage_id": "02_game_blueprint", "content": json.dumps({"core_loop": []})},
            {"stage_id": "03_asset_contract", "content": json.dumps({
                "prompt_presets": {
                    "transparent_character": {
                        "prompt": "hand-painted character, isolated, transparent background",
                        "negative_prompt": "white background, text, watermark",
                    }
                },
                "required_asset_ids": ["hero"],
                "visual_assets": [{
                    "id": "hero", "role": "player", "source": "generate_image",
                    "required_in_stage_4": True, "expected_file_path": "assets/hero.png",
                    "transparent_background": True,
                    "prompt_preset": "transparent_character",
                    "generation_prompt_delta": "cyan pilot holding a wrench",
                    "negative_prompt_delta": "helmet",
                }],
            })},
        ]
        spec = harness.select_canonical_spec(seed, previous)
        encoded = json.dumps(spec, ensure_ascii=False)
        for value in ["transparent_character", "cyan pilot holding a wrench", "helmet", "assets/hero.png"]:
            self.assertIn(value, encoded)

    def test_batch_barrier_materializes_stage_four_input_after_planning(self):
        stages = harness.parse_stages(Path(__file__).with_name("stages.json"))
        row = {"appid": 42, "appdetails": {"data": {
            "name": "Test", "about_the_game": "Fight.", "genres": [], "categories": [],
        }}}
        seed = harness.steam_seed_from_row(row)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / harness.safe_name(seed.data_id, "item_0001") / "run_1"
            artifacts = [
                {"prd_request": "Fight", "game_dimension": "2d"},
                {"core_loop": ["fight"], "scene_flow": [{"id": "battle"}]},
                {"required_asset_ids": ["board"], "visual_assets": [{
                    "id": "board", "source": "canvas_draw", "required_in_stage_4": True,
                    "expected_file_path": "assets/board.js", "composition_and_layers": "Game board",
                }]},
            ]
            for stage, content in zip(stages[:3], artifacts):
                stage_dir = run / "stages" / stage.stage_id
                stage_dir.mkdir(parents=True)
                (stage_dir / stage.artifact_file).write_text(json.dumps(content))
            result = harness.prepare_steam_rle_inputs(rows=[row], root=root, stages=stages)
            self.assertEqual(result, {"prepared": 1, "failed": 0})
            rle_input = run / "stages/04_implementation/attempt_0_rle_input.jsonl"
            self.assertTrue(rle_input.exists())
            payload = json.loads(rle_input.read_text())
            self.assertIn("query", payload)
            self.assertIn("data_id", payload)
            batch_rows = [json.loads(line) for line in (root / "rle_inputs.jsonl").read_text().splitlines()]
            self.assertEqual(len(batch_rows), 1)
            self.assertEqual(set(batch_rows[0]), {"data_id", "query"})
            self.assertEqual(batch_rows[0]["data_id"], seed.data_id)


if __name__ == "__main__":
    unittest.main()
