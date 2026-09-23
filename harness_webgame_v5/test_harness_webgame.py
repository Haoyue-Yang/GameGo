import json
import tempfile
import unittest
from pathlib import Path

import lifecycle_harness_webgame as harness


class WebGameAdapterTests(unittest.TestCase):
    def test_color_prompts_preserve_evidence_based_palette_diversity(self):
        harness_dir = Path(__file__).parent
        seed_prompt = (harness_dir / "prompts" / "01_seed_spec.md").read_text()
        blueprint_prompt = (harness_dir / "prompts" / "02_game_blueprint.md").read_text()
        asset_prompt = (harness_dir / "prompts" / "03_asset_contract.md").read_text()
        implementation_prompt = (harness_dir / "prompts" / "04_implementation.md").read_text()
        self.assertIn("visual_evidence_level", seed_prompt)
        self.assertIn("source facts and gameplay semantics → `semantic_visual_anchors`", seed_prompt)
        self.assertIn("generic style label may not be chosen first", seed_prompt)
        self.assertIn("`semantic_visual_anchors` are the evidence boundary", blueprint_prompt)
        self.assertIn("must reference `semantic_visual_anchors`", asset_prompt)
        self.assertIn("Implement `semantic_visual_anchors`", implementation_prompt)

    def test_canonical_selector_uses_legacy_visual_anchor_backfill(self):
        seed = harness.SeedInput("query", "item", "", [], {})
        previous = [
            {"stage_id": "01_seed_spec", "content": "{}"},
            {"stage_id": "02_game_blueprint", "content": json.dumps({
                "visual_evidence_level": "sparse",
                "semantic_visual_anchors": [{"anchor_id": "A1", "legacy_backfill": True}],
            })},
            {"stage_id": "03_asset_contract", "content": "{}"},
        ]
        product = harness.select_canonical_spec(seed, previous)["product_identity"]
        self.assertEqual(product["visual_evidence_level"], "sparse")
        self.assertTrue(product["semantic_visual_anchors"][0]["legacy_backfill"])

    def test_spatial_dimension_preference_is_stable_and_stage1_only(self):
        selected = [
            harness.spatial_dimension_preference_selected(f"item-{index}", 0.40)
            for index in range(10000)
        ]
        self.assertTrue(3900 <= sum(selected) <= 4100)
        self.assertFalse(harness.spatial_dimension_preference_selected("same-id", 0.0))
        self.assertTrue(harness.spatial_dimension_preference_selected("same-id", 1.0))
        self.assertEqual(
            harness.spatial_dimension_preference_selected("same-id", 0.40),
            harness.spatial_dimension_preference_selected("same-id", 0.40),
        )
        with tempfile.TemporaryDirectory() as temp:
            prompt = Path(temp) / "prompt.md"
            prompt.write_text("system prompt")
            seed = harness.SeedInput("query", "same-id", "make a game", [], {})
            stage1 = harness.Stage("01_seed_spec", "Seed", prompt, "seed_spec.json")
            later_stage = harness.Stage("04_implementation", "Implementation", prompt, "result.md")
            preferred = harness.build_stage_messages(
                seed=seed, stage=stage1, previous=[], feedback="",
                use_skill_cards=False, spatial_dimension_preference_rate=1.0,
            )
            neutral = harness.build_stage_messages(
                seed=seed, stage=stage1, previous=[], feedback="",
                use_skill_cards=False, spatial_dimension_preference_rate=0.0,
            )
            later = harness.build_stage_messages(
                seed=seed, stage=later_stage, previous=[], feedback="",
                use_skill_cards=False, spatial_dimension_preference_rate=1.0,
            )
        self.assertIn("Batch dimension diversity preference", preferred[1]["content"])
        self.assertNotIn("Batch dimension diversity preference", neutral[1]["content"])
        self.assertNotIn("Batch dimension diversity preference", later[1]["content"])

    def test_procedural_pixel_query_forbids_image_generation(self):
        spec = {
            "product_identity": {"asset_production_mode": "procedural_pixel"},
            "gameplay_spec": {}, "presentation_and_technology": {}, "asset_contract": {},
        }
        query = harness.build_rle_query(harness.query_seed("pixel art platformer"), [], "", spec)
        self.assertIn("Do not call generate_image or fetch_media", query)
        self.assertNotIn("Call generate_image separately", query)

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
        spec["gameplay_spec"] = {
            "controls": {"movement": "Left/Right"},
            "camera_contract": "fixed side-view vehicle game",
        }
        spec["product_identity"]["rendering_branch"] = "2d"
        query_2d = harness.compose_final_rle_query("# Game", spec)
        self.assertNotIn(harness.SCREEN_SPACE_DIRECTIONAL_CONTROL_REMINDER, query_2d)
        self.assertEqual(
            query_2d.count(harness.SIDE_VIEW_ASSET_ORIENTATION_REMINDER), 1
        )
        spec["gameplay_spec"]["camera_contract"] = "top-down vehicle game"
        query_top_down = harness.compose_final_rle_query("# Game", spec)
        self.assertNotIn(harness.SIDE_VIEW_ASSET_ORIENTATION_REMINDER, query_top_down)

    def test_side_view_orientation_contract_survives_final_asset_table(self):
        asset = {
            "id": "PLAYER_CRANE",
            "role": "side-view player crane",
            "source": "generate_image",
            "expected_file_path": "public/assets/crane.png",
            "generation_prompt": "Crane with cab on image-right and counterweight on image-left.",
            "canonical_facing": "right",
            "orientation_landmarks": {
                "front": "cab on image-right",
                "rear": "counterweight on image-left",
            },
            "runtime_flip_policy": "verify_then_flip_once",
        }
        table = harness.deterministic_asset_table({
            "product_identity": {"asset_production_mode": "generated_hybrid"},
            "presentation_and_technology": {},
            "asset_contract": {"required_assets": [asset]},
        })
        row = table["assets"][0]
        self.assertEqual(row["canonical_facing"], "right")
        self.assertEqual(row["orientation_landmarks"], asset["orientation_landmarks"])
        self.assertEqual(row["runtime_flip_policy"], "verify_then_flip_once")

    def test_procedural_pixel_manifest_allows_zero_generated_images(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "asset_manifest.json"
            assets = [{
                "id": f"pixel_{i}", "source": "canvas_draw", "required_in_stage_4": True,
                "composition_and_layers": "authored pixel layers",
            } for i in range(12)]
            path.write_text(json.dumps({
                "asset_production_mode": "procedural_pixel", "visual_assets": assets,
                "ui_assets": [], "effect_assets": [], "three_d_assets": [],
                "scene_composition_assets": [],
            }))
            harness.validate_text_visual_manifest(path)

    def row(self):
        return {
            "source_domain": "example.invalid",
            "source_url": "https://example.invalid/game/1",
            "title": "Reference Title",
            "description": "Arrange related words into groups.",
            "instructions": "Drag cards into rows. Clear every group.",
            "categories": "Puzzle|Casual",
            "tags": "word|logic|Kids Friendly",
        }

    def test_text_only_adapter(self):
        seed = harness.webgame_seed_from_row(self.row(), position=1)
        self.assertEqual(seed.source_kind, "webgame_text")
        self.assertEqual(seed.image_urls, [])
        self.assertEqual(seed.metadata["categories"], ["Puzzle", "Casual"])
        self.assertEqual(seed.metadata["tags"], ["word", "logic", "Kids Friendly"])
        self.assertNotIn("source_url", seed.metadata)

    def test_item_identity_does_not_depend_on_jsonl_position(self):
        first = harness.webgame_seed_from_row(self.row(), position=1)
        moved = harness.webgame_seed_from_row(self.row(), position=99)
        self.assertEqual(first.data_id, moved.data_id)
        self.assertEqual(first.metadata["input_fingerprint"], moved.metadata["input_fingerprint"])
        self.assertEqual(first.metadata["source_record_id"], "1")

    def test_explicit_input_data_id_has_priority(self):
        row = self.row()
        row["data_id"] = "catalog-game-42"
        seed = harness.webgame_seed_from_row(row, position=3)
        self.assertEqual(seed.metadata["source_record_id"], "catalog-game-42")
        self.assertEqual(seed.data_id, "catalog-game-42")

    def test_reference_title_is_removed_downstream(self):
        seed = harness.webgame_seed_from_row(self.row(), position=1)
        self.assertEqual(
            harness.redact_reference_identity("Build Reference Title now", seed),
            "Build [REFERENCE_TITLE_REMOVED] now",
        )

    def test_stage_one_message_is_text_only(self):
        seed = harness.webgame_seed_from_row(self.row(), position=1)
        with tempfile.TemporaryDirectory() as temp:
            prompt = Path(temp) / "prompt.md"
            prompt.write_text("system")
            stage = harness.Stage("01_seed_spec", "Seed", prompt, "seed_spec.json")
            messages = harness.build_stage_messages(seed=seed, stage=stage, previous=[], feedback="")
        self.assertIsInstance(messages[1]["content"], str)

    def test_deduplicated_corpus_envelope(self):
        row = {
            "content": {
                "clean_title": "Reference Puzzle",
                "clean_description": "Remove every screw from the board.",
                "clean_instructions": "Tap or drag to play.",
            },
            "dedup_metadata": {
                "origin": "web_games",
                "entity_id": "web_games:abcdef",
                "information_density": "medium",
            },
            "source": {
                "url": "https://example.invalid/game/abcdef",
                "record_id": "abcdef",
            },
            "source_metadata": {
                "categories": ["Puzzle", "Casual"],
                "tags": ["logic", "Kids Friendly"],
            },
        }
        seed = harness.webgame_seed_from_row(row, position=7)
        self.assertEqual(seed.source_kind, "webgame_text")
        self.assertEqual(seed.data_id, "web_games_abcdef")
        self.assertEqual(seed.metadata["description"], "Remove every screw from the board.")
        self.assertEqual(seed.metadata["instructions"], "Tap or drag to play.")
        self.assertEqual(seed.metadata["categories"], ["Puzzle", "Casual"])
        self.assertEqual(seed.metadata["tags"], ["logic", "Kids Friendly"])
        self.assertEqual(seed.metadata["source_origin"], "web_games")
        self.assertNotIn("source_url", seed.metadata)
        self.assertEqual(
            harness.webgame_source_record_id(row), "web_games:abcdef"
        )


class VisualContractTests(unittest.TestCase):
    def test_batch_query_export_preserves_input_order_and_shape(self):
        stages = harness.parse_stages(Path(__file__).with_name("stages.json"))
        rows = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for position in (1, 2):
                row = {
                    "data_id": f"Data{position}", "title": f"Game {position}",
                    "description": "Match items.", "instructions": "Click matching items.",
                }
                rows.append(row)
                seed = harness.webgame_seed_from_row(row, position=position)
                run = root / seed.data_id
                artifacts = [
                    {"prd_request": "Match", "game_dimension": "2d"},
                    {"core_loop": ["match"], "scene_flow": [{"id": "play"}]},
                    {"required_asset_ids": ["board"], "visual_assets": [{
                        "id": "board", "source": "canvas_draw", "required_in_stage_4": True,
                        "expected_file_path": "assets/board.js", "composition_and_layers": "Puzzle board",
                    }]},
                ]
                for stage, content in zip(stages[:3], artifacts):
                    stage_dir = run / "stages" / stage.stage_id
                    stage_dir.mkdir(parents=True)
                    (stage_dir / stage.artifact_file).write_text(json.dumps(content))
            result = harness.prepare_webgame_rle_inputs(rows=rows, root=root, stages=stages)
            self.assertEqual(result, {"prepared": 2, "failed": 0})
            exported = [json.loads(line) for line in (root / "rle_inputs.jsonl").read_text().splitlines()]
            self.assertEqual([item["data_id"] for item in exported], ["Data1", "Data2"])
            self.assertTrue(all(set(item) == {"data_id", "query"} for item in exported))

    def test_rle_query_enforces_image_tool_execution(self):
        seed = harness.query_seed("puzzle")
        spec = {"asset_execution_contract": {"required_assets": [{"source": "generate_image"}]}}
        query = harness.build_rle_query(seed, [], "", spec)
        self.assertIn("Call generate_image separately", query)
        self.assertIn("If no generate_image call is made", query)
        self.assertNotRegex(query, r"[\u4e00-\u9fff]")

    def test_empty_asset_contract_is_rejected_during_export(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "asset_manifest.json"
            path.write_text(json.dumps({"visual_assets": [], "scene_composition_assets": []}))
            harness.validate_text_visual_manifest(path)
            with self.assertRaisesRegex(ValueError, "Deterministic asset table is empty"):
                harness.compose_final_rle_query("Build a game", {
                    "product_identity": {}, "gameplay_spec": {},
                    "presentation_and_technology": {}, "asset_contract": {},
                })

    def test_valid_visual_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "asset_manifest.json"
            path.write_text(json.dumps({
                "visual_assets": [
                    {"id": "background", "source": "generate_image", "required_in_stage_4": True,
                     "generation_prompt": "full-frame opaque game background",
                     "transparent_background": False, "background_only": True}
                ] + [
                    {"id": f"procedural_{i}", "source": "procedural_svg", "required_in_stage_4": True}
                    for i in range(11)
                ],
                "scene_composition_assets": [
                    {"id": "win", "required_in_stage_4": True, "generation_prompt": "victory background",
                     "background_only": True},
                    {"id": "action", "required_in_stage_4": True, "generation_prompt": "action background",
                     "background_only": True},
                ],
            }))
            harness.validate_text_visual_manifest(path)

    def test_generated_transparent_foreground_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "asset_manifest.json"
            path.write_text(json.dumps({
                "visual_assets": [
                    {"id": "hero", "source": "generate_image", "required_in_stage_4": True,
                     "generation_prompt": "isolated hero, clean transparent background",
                     "transparent_background": True, "background_only": False,
                     "foreground_strategy": "chroma_key_runtime", "key_color": "#FF00FF",
                     "runtime_background_removal_required": True,
                     "edge_cleanup": "soft_alpha_and_magenta_despill", "alpha_trim_required": True}
                ] + [
                    {"id": f"svg_{i}", "source": "procedural_svg", "required_in_stage_4": True}
                    for i in range(11)
                ],
                "scene_composition_assets": [],
            }))
            harness.validate_text_visual_manifest(path)

    def test_generated_foreground_without_transparency_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "asset_manifest.json"
            path.write_text(json.dumps({
                "visual_assets": [
                    {"id": "hero", "source": "generate_image", "required_in_stage_4": True,
                     "generation_prompt": "isolated hero", "transparent_background": False,
                     "background_only": False}
                ] + [
                    {"id": f"svg_{i}", "source": "procedural_svg", "required_in_stage_4": True}
                    for i in range(11)
                ],
                "scene_composition_assets": [],
            }))
            with self.assertRaisesRegex(ValueError, "transparent foregrounds"):
                harness.validate_text_visual_manifest(path)

    def test_background_only_scene_composition_counts_as_generated_background(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "asset_manifest.json"
            path.write_text(json.dumps({
                "visual_assets": [
                    {"id": f"svg_{i}", "source": "procedural_svg", "required_in_stage_4": True}
                    for i in range(12)
                ],
                "scene_composition_assets": [{
                    "id": "gameplay_bg", "required_in_stage_4": True,
                    "generation_prompt": "full-frame opaque gameplay background",
                    "background_only": True,
                }],
            }))
            harness.validate_text_visual_manifest(path)


class ShaderContractTests(unittest.TestCase):
    def shader_asset(self):
        return {
            "id": "water_shader",
            "role": "reactive stylized water",
            "source": "inline_shader",
            "expected_file_path": "src/shaders/waterShader.ts",
            "effect_role": "water surface feedback",
            "integration_target": "threejs_material",
            "technique_components": ["layered directional waves", "fresnel rim"],
            "visual_signature": {
                "palette": "deep teal with amber wake lines",
                "motion": "crossing angular waves",
                "gameplay_feedback": "wake brightens near the player",
            },
            "uniform_contract": {"uTime": "seconds", "uWakeOrigin": "world position"},
            "texture_channels": [],
            "pass_graph": "single_pass",
            "render_state": "opaque, depth write on",
            "performance_budget": "single pass, no raymarching, at most 12 noise samples",
            "fallback_strategy": "animated layered normal maps",
            "originality_delta": "angular wake language and amber interaction replace any reference composition",
            "reference_technique_ids": ["shadertoy:XslGRr"],
            "required_in_stage_4": True,
        }

    def test_shader_contract_survives_deterministic_asset_table(self):
        asset = self.shader_asset()
        table = harness.deterministic_asset_table({
            "product_identity": {"asset_production_mode": "generated_hybrid"},
            "presentation_and_technology": {},
            "asset_contract": {"required_assets": [asset]},
        })
        row = table["assets"][0]
        self.assertEqual(row["source"], "inline_shader")
        self.assertEqual(row["implementation_delta"]["visual_signature"], asset["visual_signature"])
        self.assertEqual(row["implementation_delta"]["texture_channels"], [])

    def test_shader_contract_rejects_unresolved_shadertoy_channels(self):
        asset = self.shader_asset()
        asset["uniform_contract"] = {"iChannel0": "water texture"}
        with self.assertRaisesRegex(ValueError, "unresolved Shadertoy"):
            harness.validate_inline_shader_asset(asset)


if __name__ == "__main__":
    unittest.main()
