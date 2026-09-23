#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import json
import os
import re
import selectors
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

_VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"
if _VENDOR_DIR.is_dir():
    sys.path.insert(0, str(_VENDOR_DIR))
try:
    from json_repair import repair_json as _repair_json
except ImportError:
    _repair_json = None

from shader_reference import load_catalog as load_shader_reference_catalog
from skill_router import route_skill_guidance


DEFAULT_BASE_URL = os.environ.get("GAMEGO_BASE_URL", "")
DEFAULT_MODEL = os.environ.get("GAMEGO_MODEL", "")
DEFAULT_RLE_SCRIPT = os.environ.get("GAMEGO_RLE_SCRIPT", "")
DEFAULT_RLE_DOCKER_IMAGE = os.environ.get("GAMEGO_RLE_DOCKER_IMAGE", "")
API_KEY_ENV = "GAMEGO_API_KEY"
API_RETRY_BACKOFF_MODE = "exponential"
PRIORITY_PAUSE_FILE = ".pipeline_priority.pause"
PRIORITY_PAUSE_ACK_FILE = ".pipeline_priority.pause.ack.json"
PRIORITY_HEARTBEAT_FILE = ".pipeline_priority.heartbeat"
PRIORITY_BYPASS_ENV = "GAMEGO_TAKEOVER_BYPASS"
PRIORITY_HEARTBEAT_STALE_SECONDS = 300

REPAIRED_ARTIFACT_REQUIRED_KEYS = {
    "01_seed_spec": {
        "allowed_adaptations", "asset_production_evidence", "asset_production_mode",
        "camera_mobility", "camera_perspective", "color_system", "core_mechanics",
        "creative_distance_rules", "difficulty", "dimension_evidence",
        "dimension_requirement", "entity_relationships", "feedback_effects",
        "game_dimension", "game_scope_profile", "gameplay_archetype",
        "gameplay_attention_points", "gameplay_flow_contract", "genre_required_phases",
        "goal_or_win_condition", "image_only_facts", "level_layout",
        "locked_requirements", "normalized_game_type", "player_actions", "prd_request",
        "primary_gameplay_type", "progression_systems", "reference_identity_policy",
        "rendering_branch", "scene_environment", "secondary_gameplay_tags",
        "semantic_visual_anchors", "source_facts", "source_id", "source_kind",
        "ui_hud", "uncertain_inferences", "visual_evidence_level", "visual_style",
    },
    "02_game_blueprint": {
        "acceptance_criteria", "allowed_scope_reductions", "asset_production_evidence",
        "asset_production_mode", "cinematics_and_transitions", "color_role_contract",
        "content_scope", "controls", "core_experience", "core_loop", "entities",
        "feedback_matrix", "forbidden_drift", "game_dimension", "game_rules",
        "game_scope_profile", "gameplay_archetype", "gameplay_attention_points",
        "gameplay_flow_contract", "genre_pillars", "genre_required_phases",
        "implementation_technology", "independent_game_title", "manual_test_script",
        "must_implement", "one_line_pitch", "performance_budget",
        "primary_gameplay_type", "progression", "render_viewport", "rendering_branch",
        "scene_flow", "screens_and_states", "secondary_gameplay_tags",
        "semantic_visual_anchors", "shader_opportunities", "source_alignment",
        "state_and_data_model", "target_platform", "ui_hud", "visual_evidence_level",
        "win_lose_restart",
    },
    "03_asset_contract": {
        "art_direction", "asset_generation_strategy", "asset_production_evidence",
        "asset_production_mode", "audio_assets", "color_role_contract", "effect_assets",
        "forbidden_fallbacks", "game_dimension", "global_negative_prompt",
        "global_style_prompt", "implementation_checklist", "required_asset_ids",
        "scene_asset_coverage", "scene_composition_assets", "semantic_visual_anchors",
        "style_consistency_rules", "three_d_assets", "three_d_scene_quality_contract",
        "ui_assets", "uncovered_blueprint_requirements", "visual_assets",
        "visual_evidence_level",
    },
}


def build_direct_api_opener() -> urllib.request.OpenerDirector:
    """Build an opener, optionally binding a specific local IPv4 address."""
    source_address = os.environ.get("GAMEGO_SOURCE_ADDRESS", "").strip()
    if not source_address:
        return urllib.request.build_opener()

    class SourceBoundHTTPConnection(http.client.HTTPConnection):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("source_address", (source_address, 0))
            super().__init__(*args, **kwargs)

    class SourceBoundHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, request: urllib.request.Request) -> Any:
            return self.do_open(SourceBoundHTTPConnection, request)

    return urllib.request.build_opener(
        SourceBoundHTTPHandler(),
    )

SHADER_CODE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
SHADER_INTEGRATION_TARGETS = {
    "threejs_material", "threejs_postprocess", "fullscreen_quad",
}
SHADER_CONTRACT_FIELDS = [
    "effect_role", "integration_target", "technique_components", "visual_signature",
    "uniform_contract", "texture_channels", "pass_graph", "render_state", "performance_budget",
    "fallback_strategy", "originality_delta", "reference_technique_ids",
]

NON_GAMEPLAY_CATEGORIES = {
    "steam achievements", "steam cloud", "full controller support", "partial controller support",
    "controller", "family sharing", "steam trading cards", "steam workshop", "captions available",
    "includes level editor", "remote play on tv", "remote play together", "remote play on phone",
    "remote play on tablet", "steam leaderboards", "stats", "commentary available", "hdr available",
}


def shader_contract(asset: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        key: asset[key]
        for key in SHADER_CONTRACT_FIELDS
        if key in asset and asset[key] not in (None, "", [], {})
    }
    for list_field in ("texture_channels", "reference_technique_ids"):
        if list_field in asset:
            result[list_field] = asset[list_field]
    return result


def validate_inline_shader_asset(asset: Dict[str, Any]) -> None:
    asset_id = str(asset.get("id") or "<unknown>")
    suffix = Path(str(asset.get("expected_file_path") or "")).suffix.lower()
    if suffix not in SHADER_CODE_SUFFIXES:
        raise ValueError(
            f"inline_shader asset {asset_id} must use an executable .js/.ts module path"
        )
    required = (
        "effect_role", "integration_target", "technique_components", "visual_signature",
        "uniform_contract", "pass_graph", "render_state", "performance_budget", "fallback_strategy",
        "originality_delta",
    )
    missing = [key for key in required if asset.get(key) in (None, "", [], {})]
    if missing:
        raise ValueError(
            f"inline_shader asset {asset_id} lacks shader contract fields: {', '.join(missing)}"
        )
    if asset.get("integration_target") not in SHADER_INTEGRATION_TARGETS:
        raise ValueError(
            f"inline_shader asset {asset_id} has unsupported integration_target"
        )
    for key in ("texture_channels", "reference_technique_ids"):
        if key in asset and not isinstance(asset[key], list):
            raise ValueError(f"inline_shader asset {asset_id} field {key} must be a list")
    reference_ids = list(asset.get("reference_technique_ids") or [])
    if reference_ids:
        known_ids = {
            str(item.get("reference_id") or "")
            for item in load_shader_reference_catalog()
        }
        unknown_ids = sorted(set(map(str, reference_ids)) - known_ids)
        if unknown_ids:
            raise ValueError(
                f"inline_shader asset {asset_id} cites unknown technique references: "
                + ", ".join(unknown_ids)
            )
    if any(key in asset for key in ("code", "source_code", "shadertoy_code", "fragment_source")):
        raise ValueError(f"inline_shader asset {asset_id} must not embed reference shader source")
    unresolved = json.dumps(
        {
            "uniform_contract": asset.get("uniform_contract"),
            "texture_channels": asset.get("texture_channels"),
        },
        ensure_ascii=False,
    )
    if re.search(r"\biChannel\d+\b|\bmainImage\s*\(", unresolved):
        raise ValueError(
            f"inline_shader asset {asset_id} contains unresolved Shadertoy interface names"
        )


@dataclass
class Stage:
    stage_id: str
    name: str
    prompt_file: Path
    artifact_file: str
    description: str = ""
    max_revisions: int = 1


@dataclass
class SeedInput:
    source_kind: str
    data_id: str
    prompt_text: str
    image_urls: List[str]
    metadata: Dict[str, Any]


SPATIAL_DIMENSION_PREFERENCE = (
    "This item belongs to the batch's spatial-dimension diversity subset. This is a soft preference "
    "below every explicit source-derived constraint: when 2D and a true spatial presentation are both "
    "equally faithful, prefer 2.5D or 3D instead of defaulting to 2D. Use 2.5D only for real 3D geometry, "
    "materials, lighting, and depth under a fixed core-play camera; use 3D only when a movable camera or "
    "spatial observation is already natural to the mechanics. Never override required 2D, pixel/sprite, "
    "side-view precision, or flat-information requirements, and never invent depth navigation or camera "
    "mechanics to satisfy a batch target. Keep dimension_requirement truthful and cite concrete "
    "dimension_evidence."
)


def spatial_dimension_preference_selected(data_id: str, rate: float) -> bool:
    """Select a stable fraction of data IDs, independent of row order and worker count."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(data_id.encode("utf-8")).digest()
    sample = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return sample < rate


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(read_text(path))


def wait_for_priority_takeover_gate(root: Path) -> None:
    """Pause the legacy serial 0805 controller while the priority controller owns it.

    New takeover workers explicitly bypass this gate. A stale shared heartbeat
    releases the legacy worker so a crashed takeover cannot leave generation idle.
    """
    if os.environ.get(PRIORITY_BYPASS_ENV) == "1":
        return
    control_root = root.parent
    pause_path = control_root / PRIORITY_PAUSE_FILE
    if not pause_path.exists():
        return
    ack_path = control_root / PRIORITY_PAUSE_ACK_FILE
    heartbeat_path = control_root / PRIORITY_HEARTBEAT_FILE
    write_text(ack_path, json.dumps({
        "pid": os.getpid(),
        "root": str(root),
        "paused_at": int(time.time()),
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"[priority takeover] Paused by {pause_path}", flush=True)
    while pause_path.exists():
        now = time.time()
        try:
            heartbeat_age = now - heartbeat_path.stat().st_mtime
        except OSError:
            try:
                heartbeat_age = now - pause_path.stat().st_mtime
            except OSError:
                break
        if heartbeat_age > PRIORITY_HEARTBEAT_STALE_SECONDS:
            print(
                f"[priority takeover] Heartbeat stale for {heartbeat_age:.0f}s; "
                "legacy generation is resuming",
                file=sys.stderr,
                flush=True,
            )
            return
        time.sleep(10)
    print("[priority takeover] Pause released", flush=True)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Bad JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Bad JSONL at {path}:{line_no}: each line must be an object")
            rows.append(row)
    return rows


def safe_name(value: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return value or fallback


def clean_html_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def steam_seed_from_row(row: Dict[str, Any], *, max_description_chars: int = 5000, max_images: int = 5) -> SeedInput:
    appdetails = row.get("appdetails") or {}
    data = appdetails.get("data") if isinstance(appdetails, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Steam row is missing appdetails.data")

    appid = str(row.get("appid") or data.get("steam_appid") or "unknown")
    name = str(data.get("name") or "").strip()
    genres = [str(x.get("description", "")).strip() for x in data.get("genres", []) if isinstance(x, dict)]
    categories = [
        str(x.get("description", "")).strip()
        for x in data.get("categories", [])
        if isinstance(x, dict) and str(x.get("description", "")).strip().lower() not in NON_GAMEPLAY_CATEGORIES
    ]
    descriptions = [data.get("about_the_game"), data.get("detailed_description"), data.get("short_description")]
    description = max((clean_html_text(x) for x in descriptions), key=len, default="")[:max_description_chars]
    image_urls = [
        str(x.get("path_full") or x.get("path_thumbnail") or "").strip()
        for x in data.get("screenshots", [])
        if isinstance(x, dict) and (x.get("path_full") or x.get("path_thumbnail"))
    ][:max_images]

    minimal = {
        "source_id": f"steam_{appid}",
        "reference_title": name,
        "reference_title_usage": "只用于识别并移除原作名称；不得作为新游戏名称或复制目标。",
        "genres": genres,
        "gameplay_categories": categories,
        "description_for_seed": description,
        "screenshot_count": len(image_urls),
    }
    prompt_text = "# Steam 种子最小输入\n\n" + json.dumps(minimal, ensure_ascii=False, indent=2)
    return SeedInput("steam", f"steam_{appid}", prompt_text, image_urls, minimal)


def query_seed(query: str, data_id: str = "query") -> SeedInput:
    return SeedInput("query", data_id, "# 原始生成需求\n\n" + query.strip(), [], {"query": query.strip()})


def normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        values = value
    else:
        values = str(value or "").split("|")
    return list(dict.fromkeys(
        str(item).strip() for item in values if str(item).strip()
    ))


def normalized_webgame_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Read either the legacy flat catalog row or the deduplicated corpus envelope."""
    content = row.get("content") if isinstance(row.get("content"), dict) else {}
    source_metadata = (
        row.get("source_metadata") if isinstance(row.get("source_metadata"), dict) else {}
    )
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    dedup = row.get("dedup_metadata") if isinstance(row.get("dedup_metadata"), dict) else {}
    title = clean_html_text(content.get("clean_title") or row.get("title"))
    description = clean_html_text(
        content.get("clean_description")
        or content.get("clean_short_description")
        or row.get("description")
    )
    instructions = clean_html_text(
        content.get("clean_instructions") or row.get("instructions")
    )
    categories = normalize_string_list(
        source_metadata.get("categories") or row.get("categories")
    )
    tags = normalize_string_list(source_metadata.get("tags") or row.get("tags"))
    source_url = str(row.get("source_url") or source.get("url") or "").strip()
    return {
        "title": title,
        "description": description,
        "instructions": instructions,
        "categories": categories,
        "tags": tags,
        "source_url": source_url,
        "source_origin": str(dedup.get("origin") or source.get("merge_origin") or "").strip(),
        "information_density": str(
            row.get("information_density") or dedup.get("information_density") or ""
        ).strip(),
    }


def webgame_row_fingerprint(row: Dict[str, Any]) -> str:
    """Return a stable identity for a JSONL item, independent of its line number."""
    fields = normalized_webgame_fields(row)
    dedup = row.get("dedup_metadata") if isinstance(row.get("dedup_metadata"), dict) else {}
    identity = {
        "entity_id": str(dedup.get("entity_id") or "").strip(),
        **fields,
    }
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]


def webgame_source_record_id(row: Dict[str, Any]) -> str:
    """Return the input's own ID when present, otherwise derive it from source_url."""
    for key in ("data_id", "id", "game_id", "source_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    dedup = row.get("dedup_metadata") if isinstance(row.get("dedup_metadata"), dict) else {}
    entity_id = str(dedup.get("entity_id") or "").strip()
    if entity_id:
        return entity_id
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    record_id = str(source.get("record_id") or "").strip()
    if record_id:
        return record_id
    source_data_id = str(dedup.get("source_data_id") or "").strip()
    if source_data_id:
        origin = safe_name(str(dedup.get("origin") or ""), "")
        return f"{origin}:{source_data_id}" if origin else source_data_id
    source_url = normalized_webgame_fields(row)["source_url"].rstrip("/")
    if source_url:
        tail = source_url.rsplit("/", 1)[-1]
        if tail:
            return tail
    return webgame_row_fingerprint(row)


def webgame_seed_from_row(row: Dict[str, Any], *, position: int = 0) -> SeedInput:
    """Adapt one text-only web-game catalog row without fetching its source URL."""
    if not isinstance(row, dict):
        raise ValueError("Web-game JSONL row must be an object")
    fields = normalized_webgame_fields(row)
    title = fields["title"]
    description = fields["description"]
    instructions = fields["instructions"]
    categories = fields["categories"]
    tags = fields["tags"]
    if not description and not instructions:
        raise ValueError("Web-game row must contain description or instructions")
    fingerprint = webgame_row_fingerprint(row)
    source_record_id = webgame_source_record_id(row)
    data_id = safe_name(source_record_id, f"webgame_{fingerprint}")
    minimal = {
        "source_id": data_id,
        "reference_title": title,
        "reference_title_usage": "只用于识别并移除原作名称；不得作为新游戏名称或复制目标。",
        "description": description,
        "instructions": instructions,
        "categories": categories,
        "tags": tags,
        "input_fingerprint": fingerprint,
        "source_record_id": source_record_id,
        "input_position": position,
        "source_origin": fields["source_origin"],
        "information_density": fields["information_density"],
        "visual_evidence_policy": "输入没有图片。必须根据玩法自行规划独立的新视觉方向，不得声称观察到了原作画面。",
    }
    prompt_text = "# 纯文字网页游戏种子\n\n" + json.dumps(minimal, ensure_ascii=False, indent=2)
    return SeedInput("webgame_text", data_id, prompt_text, [], minimal)


def redact_reference_identity(text: str, seed: SeedInput) -> str:
    """Keep reference titles available to stage 1, but never forward them downstream."""
    if seed.source_kind not in {"steam", "webgame_text"}:
        return text
    title = str(seed.metadata.get("reference_title") or "").strip()
    if not title:
        return text
    pattern = re.escape(title)
    # Short ASCII titles can also occur inside schema identifiers (for example,
    # title "IFO" appears inside "uniform_contract"). Preserve identifiers while
    # still redacting standalone title mentions in natural-language values.
    if title[0].isascii() and (title[0].isalnum() or title[0] == "_"):
        pattern = r"(?<![A-Za-z0-9_])" + pattern
    if title[-1].isascii() and (title[-1].isalnum() or title[-1] == "_"):
        pattern += r"(?![A-Za-z0-9_])"
    return re.sub(pattern, "[REFERENCE_TITLE_REMOVED]", text, flags=re.IGNORECASE)


def parse_stages(config_path: Path) -> List[Stage]:
    root = load_json(config_path)
    stages = []
    for item in root["stages"]:
        stages.append(Stage(
            stage_id=item["id"], name=item["name"],
            prompt_file=(config_path.parent / item["prompt_file"]).resolve(),
            artifact_file=item["artifact_file"], description=item.get("description", ""),
            max_revisions=int(item.get("max_revisions", root.get("max_revisions", 1))),
        ))
    if len(stages) != 4 or stages[-1].stage_id != "04_implementation":
        raise ValueError("WebGame Pipeline config must contain exactly four stages ending in 04_implementation")
    return stages


def normalize_chat_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return base_url + ("/chat/completions" if base_url.endswith("/v1") else "/v1/chat/completions")


def normalize_openai_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url[:-len("/chat/completions")]
    return base_url if base_url.endswith("/v1") else base_url + "/v1"


def decode_chat_completion_response(response: Any) -> tuple[str, Dict[str, Any], Optional[str]]:
    """Decode either an OpenAI JSON response or an SSE streaming response."""
    content_type = str(response.headers.get("Content-Type", "")).lower()
    if "text/event-stream" not in content_type:
        data = json.loads(response.read().decode("utf-8"))
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            )
        return str(content or ""), data.get("usage") or {}, choice.get("finish_reason")

    chunks: List[str] = []
    usage: Dict[str, Any] = {}
    finish_reason: Optional[str] = None
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        event_text = line[5:].strip()
        if not event_text or event_text == "[DONE]":
            continue
        event = json.loads(event_text)
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        for choice in event.get("choices") or []:
            finish_reason = choice.get("finish_reason") or finish_reason
            content = (choice.get("delta") or {}).get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                chunks.extend(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict) and part.get("text")
                )
    return "".join(chunks), usage, finish_reason


def call_chat_completion(*, base_url: str, api_key: str, model: str, messages: List[Dict[str, Any]],
                         temperature: float, max_tokens: int, timeout_s: int,
                         retries: int, retry_sleep_s: float,
                         json_mode: bool = False) -> str:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # Some compatible providers do not accept sampling parameters.
    if os.environ.get("GAMEGO_OMIT_TEMPERATURE", "0") != "1":
        payload["temperature"] = temperature
    body = json.dumps(payload, ensure_ascii=False).encode()
    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        proxy_session_id = (
            f"gamego-text-{os.getpid()}-{attempt}-{time.time_ns()}"
        )
        request = urllib.request.Request(normalize_chat_url(base_url), data=body, method="POST", headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            
        })
        try:
            # Respect the deployment environment networking configuration.
            opener = build_direct_api_opener()
            with opener.open(request, timeout=timeout_s) as response:
                content, usage, _finish_reason = decode_chat_completion_response(response)
            if isinstance(usage, dict):
                print(
                    "Model API usage: " + json.dumps(usage, ensure_ascii=False, sort_keys=True),
                    file=sys.stderr,
                )
            if not content.strip():
                raise ValueError("model API stream returned no text content")
            return content
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if (exc.code < 500 and exc.code != 429) or attempt >= retries:
                raise RuntimeError(f"HTTP {exc.code} from model API: {detail[:2000]}") from exc
            last_error = exc
        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            OSError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ) as exc:
            if attempt >= retries:
                raise RuntimeError(f"Model API failed after {attempt + 1} attempt(s): {exc}") from exc
            last_error = exc
        wait_s = (
            retry_sleep_s
            if API_RETRY_BACKOFF_MODE == "fixed"
            else retry_sleep_s * (2 ** attempt)
        )
        print(f"Model API attempt {attempt + 1} failed: {last_error}; retrying in {wait_s:.1f}s", file=sys.stderr)
        time.sleep(wait_s)
    raise RuntimeError("Model API failed")


def json_candidates(text: str) -> List[str]:
    stripped = text.strip()
    candidates = [stripped]
    if re.match(r"^```(?:json)?\s*", stripped, re.I) and re.search(r"\s*```$", stripped):
        outer = re.sub(r"^```(?:json)?\s*", "", stripped, count=1, flags=re.I)
        outer = re.sub(r"\s*```$", "", outer, count=1)
        candidates.insert(0, outer)
    candidates.extend(re.findall(r"```(?:json)?\s*(.*?)```", stripped, re.I | re.S))
    return list(dict.fromkeys(candidates))


def repaired_artifact_missing_keys(stage: Stage, parsed: Any) -> List[str]:
    if not isinstance(parsed, dict):
        return ["<JSON object>"]
    required = REPAIRED_ARTIFACT_REQUIRED_KEYS.get(stage.stage_id, set())
    return sorted(required - set(parsed))


def extract_json(text: str) -> tuple[Any, bool]:
    candidates = json_candidates(text)
    for candidate in candidates:
        try:
            return json.loads(candidate.strip()), False
        except (json.JSONDecodeError, TypeError):
            pass
    if _repair_json is not None:
        for candidate in candidates:
            try:
                parsed = _repair_json(
                    candidate,
                    return_objects=True,
                    skip_json_loads=True,
                )
            except (TypeError, ValueError, IndexError):
                continue
            if isinstance(parsed, (dict, list)):
                return parsed, True
    raise ValueError("Stage declared a JSON artifact but returned no valid JSON object")


def materialize_artifact(stage: Stage, response: str, stage_dir: Path) -> Path:
    path = stage_dir / stage.artifact_file
    if path.suffix == ".json":
        parsed, repaired = extract_json(response)
        if not isinstance(parsed, dict):
            raise ValueError(f"{stage.artifact_file} must be a JSON object")
        if repaired:
            missing = repaired_artifact_missing_keys(stage, parsed)
            if missing:
                raise ValueError(
                    f"Repaired {stage.artifact_file} is incomplete; missing required keys: "
                    + ", ".join(missing)
                )
            print(
                f"[JSON repair] Recovered complete {stage.artifact_file}; "
                "normal validation still applies",
                flush=True,
            )
        write_text(path, json.dumps(parsed, ensure_ascii=False, indent=2) + "\n")
    else:
        write_text(path, response.rstrip() + "\n")
    return path


def validate_text_visual_manifest(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if re.search(r"[\u4e00-\u9fff]", text):
        raise ValueError(
            "asset_manifest contains Chinese text; translate every natural-language value "
            "to English while preserving IDs, paths, dimensions, and asset coverage"
        )
    manifest = load_json(path)
    for group in ("visual_assets", "ui_assets", "effect_assets", "three_d_assets"):
        for asset in manifest.get(group) or []:
            if isinstance(asset, dict) and asset.get("source") == "inline_shader":
                validate_inline_shader_asset(asset)
    for group in ("visual_assets", "ui_assets", "effect_assets"):
        for asset in manifest.get(group) or []:
            if not isinstance(asset, dict):
                continue
            if asset.get("source") != "generate_image" or asset.get("transparent_background") is not True:
                continue
            asset_id = str(asset.get("id") or "<unknown>")
            required = {
                "foreground_strategy": "chroma_key_runtime",
                "key_color": "#FF00FF",
                "runtime_background_removal_required": True,
                "edge_cleanup": "soft_alpha_and_magenta_despill",
                "alpha_trim_required": True,
            }
            invalid = [
                key for key, expected in required.items()
                if asset.get(key) != expected
            ]
            if invalid:
                raise ValueError(
                    f"transparent generated foreground {asset_id} lacks required chroma-key fields: "
                    + ", ".join(invalid)
                )
    asset_mode = str(manifest.get("asset_production_mode") or "generated_hybrid")
    generated = []
    forbidden_pixel_sources = []
    required_visuals = []
    for group in ("visual_assets", "ui_assets", "effect_assets", "three_d_assets"):
        required_visuals.extend(
            item for item in (manifest.get(group) or [])
            if isinstance(item, dict) and item.get("required_in_stage_4") is True
        )
        generated.extend(
            item for item in (manifest.get(group) or [])
            if isinstance(item, dict) and item.get("source") == "generate_image"
            and item.get("required_in_stage_4") is True and str(item.get("generation_prompt") or "").strip()
        )
        forbidden_pixel_sources.extend(
            item.get("id", "<unknown>") for item in (manifest.get(group) or [])
            if isinstance(item, dict) and item.get("source") in {"generate_image", "fetch_media"}
        )
    compositions = [
        item for item in (manifest.get("scene_composition_assets") or [])
        if isinstance(item, dict) and item.get("required_in_stage_4") is True
        and str(item.get("generation_prompt") or "").strip()
    ]
    generated_image_count = len(generated) + len(compositions)
    if asset_mode == "procedural_pixel" and generated_image_count != 0:
        raise ValueError("procedural_pixel asset contract must contain zero generated images")
    if asset_mode == "procedural_pixel" and forbidden_pixel_sources:
        raise ValueError(
            "procedural_pixel asset contract forbids generate_image/fetch_media: "
            + ", ".join(map(str, forbidden_pixel_sources))
        )
    invalid_generated = [
        item.get("id", "<unknown>") for item in generated
        if (
            item.get("background_only") is True
            and item.get("transparent_background") is not False
        ) or (
            item.get("background_only") is not True
            and item.get("transparent_background") is not True
        )
    ]
    if invalid_generated:
        raise ValueError(
            "WebGame Pipeline generate_image assets must be either opaque backgrounds or transparent foregrounds: "
            + ", ".join(map(str, invalid_generated))
        )
    invalid_compositions = [
        item.get("id", "<unknown>") for item in compositions
        if item.get("background_only") is not True
        or item.get("transparent_background", False) is True
    ]
    if invalid_compositions:
        raise ValueError(
            "WebGame Pipeline scene compositions must be background-only: "
            + ", ".join(map(str, invalid_compositions))
        )


def recover_complete_artifact_from_raw(stage: Stage, stage_dir: Path) -> bool:
    """Recover syntax-damaged JSON only when the complete stage contract survives.

    This is deliberately conservative: every required top-level field must remain,
    and Stage 3 must pass the same strict asset validator as a normal model result.
    """
    artifact_path = stage_dir / stage.artifact_file
    if artifact_path.exists() or _repair_json is None:
        return False
    raw_paths = sorted(
        stage_dir.glob("attempt_*_raw.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for raw_path in raw_paths:
        try:
            parsed, repaired = extract_json(read_text(raw_path))
        except (OSError, ValueError):
            continue
        missing = repaired_artifact_missing_keys(stage, parsed)
        if missing:
            continue
        candidate_path = stage_dir / f".{stage.artifact_file}.repair.{os.getpid()}"
        try:
            write_text(candidate_path, json.dumps(parsed, ensure_ascii=False, indent=2) + "\n")
            if stage.stage_id == "03_asset_contract":
                validate_text_visual_manifest(candidate_path)
            os.replace(candidate_path, artifact_path)
        except (OSError, ValueError):
            if candidate_path.exists():
                candidate_path.unlink()
            continue
        write_text(stage_dir / "pipeline_status.json", json.dumps({
            "stage": stage.stage_id,
            "status": "completed",
            "artifact": str(artifact_path),
            "timestamp": int(time.time()),
            "recovered_from_raw": str(raw_path),
            "json_syntax_repaired": repaired,
            "quality_gate": "required_keys_and_existing_stage_validation",
        }, ensure_ascii=False, indent=2) + "\n")
        print(
            f"[JSON repair] Recovered {stage.stage_id} from {raw_path.name}; "
            "complete-field quality gate passed",
            flush=True,
        )
        return True
    return False


def collect_artifacts(run_dir: Path, stages: List[Stage], current_index: int) -> List[Dict[str, str]]:
    result = []
    for stage in stages[:current_index]:
        path = run_dir / "stages" / stage.stage_id / stage.artifact_file
        if path.exists():
            result.append({"stage_id": stage.stage_id, "stage_name": stage.name, "path": str(path), "content": read_text(path)})
    return result


def stage_is_completed(run_dir: Path, stage: Stage) -> bool:
    stage_dir = run_dir / "stages" / stage.stage_id
    status_path = stage_dir / "pipeline_status.json"
    artifact_path = stage_dir / stage.artifact_file
    if not status_path.exists() or not artifact_path.exists():
        return False
    try:
        status = load_json(status_path)
    except (OSError, ValueError):
        return False
    return isinstance(status, dict) and status.get("status") == "completed"


def resolve_branch_prompt(stage: Stage, previous: List[Dict[str, str]]) -> tuple[Path, Optional[str]]:
    """Select the Stage 2/3 rendering overlay from the approved Stage 1 artifact."""
    if stage.stage_id not in {"02_game_blueprint", "03_asset_contract"}:
        return stage.prompt_file, None
    seed_artifact = next((x["content"] for x in previous if x["stage_id"] == "01_seed_spec"), None)
    if seed_artifact is None:
        raise ValueError(f"{stage.stage_id} requires an approved 01_seed_spec artifact")
    seed_spec = json.loads(seed_artifact)
    branch = str(seed_spec.get("rendering_branch") or seed_spec.get("game_dimension") or "").lower()
    if branch not in {"2d", "2.5d", "3d"}:
        raise ValueError(f"Unsupported rendering_branch for {stage.stage_id}: {branch!r}")
    overlay = stage.prompt_file.parent / "branches" / branch / stage.prompt_file.name
    if not overlay.exists():
        raise FileNotFoundError(f"Missing {branch} prompt overlay: {overlay}")
    return overlay, branch


def resolve_gameplay_guidance(stage: Stage, previous: List[Dict[str, str]]) -> tuple[str, Optional[str]]:
    if stage.stage_id not in {"02_game_blueprint", "03_asset_contract"}:
        return "", None
    seed_artifact = next((x["content"] for x in previous if x["stage_id"] == "01_seed_spec"), None)
    if seed_artifact is None:
        raise ValueError(f"{stage.stage_id} requires an approved 01_seed_spec artifact")
    seed_spec = json.loads(seed_artifact)
    gameplay_type = str(seed_spec.get("primary_gameplay_type") or "").lower()
    archetype = str(seed_spec.get("gameplay_archetype") or "").lower()
    library = load_json(Path(__file__).resolve().parent.parent / "GAMEPLAY_TYPE_PROFILES.json")
    profile = (library.get("profiles") or {}).get(gameplay_type)
    if not isinstance(profile, dict):
        raise ValueError(f"Unsupported primary_gameplay_type for {stage.stage_id}: {gameplay_type!r}")
    key = "stage2" if stage.stage_id == "02_game_blueprint" else "stage3"
    rules = list(profile.get(key) or [])
    archetype_profile = (library.get("archetypes") or {}).get(archetype)
    if isinstance(archetype_profile, dict):
        rules.extend(archetype_profile.get(key) or [])
    priority = (
        "- PRIORITY: explicit source-derived rules and ordered phases in seed_spec always override this library guidance. "
        "Use these rules only to focus attention where the corresponding mechanic exists; never invent content or reshape a game to match a known pattern."
    )
    text = priority + "\n" + "\n".join(f"- {rule}" for rule in rules)
    return text, f"{gameplay_type}/{archetype or 'generic'}"


def build_stage_messages(
    *,
    seed: SeedInput,
    stage: Stage,
    previous: List[Dict[str, str]],
    feedback: str,
    use_skill_cards: bool = True,
    spatial_dimension_preference_rate: float = 0.0,
) -> List[Dict[str, Any]]:
    branch_prompt, branch = resolve_branch_prompt(stage, previous)
    system = read_text(stage.prompt_file)
    if branch:
        system += f"\n\n# Selected rendering pipeline: {branch}\n" + read_text(branch_prompt)
    gameplay_guidance, gameplay_route = resolve_gameplay_guidance(stage, previous)
    if gameplay_route:
        system += f"\n\n# Selected gameplay pipeline: {gameplay_route}\n" + gameplay_guidance
    skill_routing = route_skill_guidance(
        stage.stage_id, previous, enabled=use_skill_cards
    )
    if skill_routing.guidance:
        system += "\n\n# Internal specialist lenses\n" + skill_routing.guidance
    previous_text = "\n\n".join(
        f"## {x['stage_name']}\n{redact_reference_identity(x['content'], seed)}" for x in previous
    )
    text_parts = [seed.prompt_text, f"# 当前阶段\n{stage.stage_id}: {stage.name}"]
    if stage.description:
        text_parts.append("# 阶段目标\n" + stage.description)
    if previous_text:
        text_parts.append("# 已批准的前序产物\n" + previous_text)
    if feedback:
        text_parts.append("# 人工反馈\n" + feedback)
    if stage.stage_id == "01_seed_spec" and spatial_dimension_preference_selected(
        seed.data_id, spatial_dimension_preference_rate
    ):
        text_parts.append("# Batch dimension diversity preference\n" + SPATIAL_DIMENSION_PREFERENCE)
    text_parts.append(f"# 输出要求\n只输出 `{stage.artifact_file}`，不要实现后续阶段。")
    text = "\n\n".join(text_parts)
    if stage.stage_id == "01_seed_spec" and seed.image_urls:
        content: Any = [{"type": "text", "text": text}]
        content.extend({"type": "image_url", "image_url": {"url": url}} for url in seed.image_urls)
    else:
        content = text
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def select_fields(source: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    return {key: source[key] for key in fields if key in source and source[key] not in (None, "", [], {})}


def select_canonical_entities(source: Any) -> Any:
    fields = ["name", "role", "gameplay_role", "behavior", "ai", "mechanics", "abilities", "states"]
    if isinstance(source, dict):
        return {key: select_fields(item, fields) for key, item in source.items() if isinstance(item, dict)}
    if isinstance(source, list):
        return [select_fields(item, ["id", *fields]) for item in source if isinstance(item, dict)]
    return {}


def select_canonical_spec(seed: SeedInput, previous: List[Dict[str, str]]) -> Dict[str, Any]:
    artifacts = {x["stage_id"]: redact_reference_identity(x["content"], seed) for x in previous}
    required_stages = ("01_seed_spec", "02_game_blueprint", "03_asset_contract")
    missing = [stage_id for stage_id in required_stages if stage_id not in artifacts]
    if missing:
        raise ValueError(f"Cannot select canonical spec; missing stages: {', '.join(missing)}")
    try:
        seed_spec = json.loads(artifacts["01_seed_spec"])
        blueprint = json.loads(artifacts["02_game_blueprint"])
        manifest = json.loads(artifacts["03_asset_contract"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Planning artifact is not valid JSON: {exc}") from exc

    product = select_fields(seed_spec, [
        "game_dimension", "rendering_branch", "dimension_requirement", "dimension_evidence",
        "camera_mobility", "normalized_game_type", "game_scope_profile", "creative_distance_rules",
        "primary_gameplay_type", "gameplay_archetype", "secondary_gameplay_tags", "genre_required_phases",
        "gameplay_attention_points", "locked_requirements",
        "asset_production_mode", "asset_production_evidence",
        "visual_evidence_level", "semantic_visual_anchors",
        "color_system",
    ])
    for field in ("visual_evidence_level", "semantic_visual_anchors"):
        if not product.get(field):
            for legacy_source in (blueprint, manifest):
                if legacy_source.get(field):
                    product[field] = legacy_source[field]
                    break
    product.update(select_fields(blueprint, ["independent_game_title"]))
    gameplay = select_fields(blueprint, [
        "gameplay_flow_contract", "render_viewport", "camera_contract", "controls", "game_rules", "progression", "win_lose_restart",
        "ui_hud", "feedback_matrix", "content_scope", "allowed_scope_reductions",
        "acceptance_criteria",
    ])
    gameplay["entities"] = select_canonical_entities(blueprint.get("entities"))
    gameplay["core_loop"] = [
        select_fields(item, ["action", "reward", "next_state"])
        for item in blueprint.get("core_loop") or [] if isinstance(item, dict)
    ]
    gameplay["scene_flow"] = [
        select_fields(scene, [
            "id", "entry_condition", "visible_entities", "exit_condition",
            "next_scene_or_state",
        ])
        for scene in blueprint.get("scene_flow") or [] if isinstance(scene, dict)
    ]
    gameplay["cinematics_and_transitions"] = [
        select_fields(item, [
            "id", "trigger", "trigger_condition", "duration", "duration_range",
            "camera_and_composition", "camera_or_composition_change", "animation_content",
            "skippable", "end_state", "next_state",
        ])
        for item in blueprint.get("cinematics_and_transitions") or [] if isinstance(item, dict)
    ]
    rendering = {
        **select_fields(blueprint, [
            "implementation_technology", "state_and_data_model", "performance_budget",
            "three_d_scene_quality_contract", "shader_opportunities", "color_role_contract",
        ]),
        **select_fields(manifest, [
            "art_direction", "global_style_prompt", "global_negative_prompt", "style_consistency_rules",
            "prompt_presets",
            "three_d_scene_quality_contract", "scene_density_contract", "lighting_contract",
            "environmental_motion_contract", "geometry_density_budget", "color_role_contract",
        ]),
    }

    required_ids = set(manifest.get("required_asset_ids") or [])
    required_assets: List[Dict[str, Any]] = []
    for group in ("visual_assets", "ui_assets", "effect_assets", "audio_assets", "three_d_assets"):
        for asset in manifest.get(group) or []:
            if not isinstance(asset, dict):
                continue
            asset_id = asset.get("id")
            if asset.get("required_in_stage_4") is True or asset_id in required_ids:
                compact_asset = select_fields(asset, [
                    "id", "role", "used_in_scene_ids", "source", "expected_file_path",
                ])
                source = str(asset.get("source") or "")
                if source in {"generate_image", "fetch_media"}:
                    compact_asset.update(select_fields(asset, [
                        "dimensions", "transparent_background", "background_only",
                        "generation_prompt", "negative_prompt",
                        "canonical_facing", "orientation_landmarks", "runtime_flip_policy",
                    ]))
                elif source == "inline_shader":
                    compact_asset.update(shader_contract(asset))
                elif group == "three_d_assets":
                    compact_asset.update(select_fields(asset, [
                        "geometry_or_model_strategy", "named_visible_parts", "material_recipe",
                        "texture_recipe", "dimensions_or_scale", "placement_in_scene",
                        "animation_or_motion_loop", "lighting_dependency",
                    ]))
                else:
                    compact_asset.update(select_fields(asset, [
                        "composition_and_layers", "animation_or_state_variants",
                    ]))
                required_assets.append(compact_asset)

    scene_compositions = [select_fields(item, [
        "id", "scene_id", "purpose", "generation_prompt", "negative_prompt",
        "expected_file_path", "dimensions", "transparent_background", "background_only",
        "required_in_stage_4",
    ]) for item in (manifest.get("scene_composition_assets") or []) if isinstance(item, dict)]
    found_ids = {asset.get("id") for asset in required_assets}
    found_ids.update(item.get("id") for item in scene_compositions)
    missing_assets = sorted(required_ids - found_ids)
    if missing_assets:
        raise ValueError(f"Required asset ids have no definitions: {', '.join(missing_assets)}")

    assets = {
        "required_assets": required_assets,
        **({"scene_composition_assets": scene_compositions} if scene_compositions else {}),
        **select_fields(manifest, ["uncovered_blueprint_requirements"]),
    }
    return {
        "product_identity": product,
        "gameplay_spec": gameplay,
        "presentation_and_technology": rendering,
        "asset_contract": assets,
    }


def build_rle_query(seed: SeedInput, previous: List[Dict[str, str]], feedback: str,
                    implementation_spec: Optional[Dict[str, Any]] = None) -> str:
    spec = implementation_spec or select_canonical_spec(seed, previous)
    asset_mode = str((spec.get("product_identity") or {}).get("asset_production_mode") or "generated_hybrid")
    if asset_mode == "procedural_pixel":
        image_constraints = [
            "This item uses procedural_pixel production. Do not call generate_image or fetch_media and do not create generated-image prompts.",
            "Create all backgrounds, pixel characters, enemies, props, UI, tiles, and effects with Canvas/SVG/CSS or programmatic sprites/textures. Preserve authored silhouettes, internal pixel detail, animation states, and complete scene coverage; crude placeholder blocks are forbidden.",
        ]
    else:
        image_constraints = [
            "Call generate_image separately for every specified generated asset. Use background_only assets as opaque full-frame scene backgrounds. For transparent_background foreground assets, use the upgraded image tool's background-removal capability and integrate the result as an independently positioned, scaled, and animated transparent layer.",
            "Reject fake transparency: never use white, checkerboard, or baked backgrounds as foreground cutouts. If background removal fails for an individual foreground asset, preserve and report the tool error and implement a detailed procedural fallback for that asset instead of displaying the bad cutout.",
            "If generate_image fails, preserve and report the original tool error before degrading that individual asset. If no generate_image call is made, the implementation is incomplete even if build_project succeeds.",
        ]
    parts = [
        "# GameGo Text Pipeline: Generated-Asset Web Game Implementation",
        "This is a new, standalone game-generation task. Catalog text was used only to extract transferable abstract mechanics. There are no reference images; all visuals and assets were designed originally during planning. Do not copy any source title, site identity, character, location, trademark, copy, or asset.",
        "# Canonical selected specification (sole source of truth)",
        json.dumps(spec, ensure_ascii=False, separators=(",", ":")),
    ]
    if feedback:
        parts.extend(["# Human feedback for this attempt", feedback])
    parts.extend([
        "# Mandatory execution constraints",
        "Implement the complete playable loop, scene/state flow, opening, transitions, and results, and satisfy every acceptance criterion.",
        "Render each full-screen background as one full-bleed image matching render_viewport aspect ratio. Use proportional cover/crop only; never tile, repeat, mirror-repeat, stretch, or place duplicate copies side by side.",
        "Obey rendering_branch exactly: keep 2D in layered 2D space; keep 2.5D in a true 3D scene with a fixed camera; use movable screen-relative camera controls only for 3D. Do not silently switch branch.",
        "For 2.5D/3D, implement the complete three_d_scene_quality_contract with authored high-density geometry, layered foreground/midground/background dressing, material variation, three-layer lighting, shadows/fog, and ambient motion. A few default primitives or a sparse diorama are unacceptable.",
        "Implement color_role_contract exactly: keep environment, entity semantics, UI chrome, and typography on separate tokens. Preserve the blueprint's evidence-based warm, cool, or other palette; never derive global text from any environment hue, and never replace it with a default beige, navy/cyan, blue-gray, mechanical, or neon theme.",
        "Implement every required asset at its expected_file_path and actually reference it in every used_in_scene_id. Execute each image-generation prompt, procedural-rendering recipe, 3D geometry/material recipe, and effects recipe as specified.",
        "Implement every inline_shader as an original project-owned executable module. Preserve its visual_signature, bounded performance_budget, render_state, uniforms, gameplay-triggered variation, and fallback_strategy; never copy a complete Shadertoy composition or leave iChannel/mainImage interfaces unresolved.",
        *image_constraints,
        "For 2D, use browser-appropriate Canvas/SVG/Pixi/Phaser rendering. For fixed-camera 2.5D and movable-camera 3D, use Three.js/WebGL and implement the specified composition, geometry, materials, textures, lighting, shadows, environmental density, and feedback.",
        "Do not degrade core assets into emoji, plain text, simple color blocks, or a handful of default primitives. Do not depend on a dedicated game editor, commercial asset store, or external art team.",
        "Keep all implementation-facing prose, code, identifiers, filenames, comments, and reports in English. Player-visible text must also be English unless the specification explicitly requires another language.",
        "When finished, run build_project. Explicitly report any required asset or acceptance criterion that could not be satisfied.",
    ])
    return "\n\n".join(parts)


DOMAIN_COMPACT_SYSTEM = """You perform Protected Domain Compact for a browser-game generation pipeline.
Rewrite the supplied planning package into one compact, implementation-ready English query for an RLE coding agent.

Requirements:
- Output only the final query with short Markdown headings; never discuss the rewrite.
- The exact asset contract has intentionally been removed from your input and will be appended deterministically by the pipeline. Do not invent, enumerate, summarize, or reserve space for asset IDs, file paths, generation prompts, prompt presets, or an asset appendix.
- Preserve the color-role separation and typography behavior. Do not collapse environment, entity, UI-chrome, and text palettes into one theme color.
- Remove semantic repetition both within each planning stage and across the brief, mechanics, rules, scenes, assets, and acceptance criteria.
- State each mechanic fully once. Later sections may reference it without explaining it again.
- Treat `gameplay_flow_contract` as protected, authoritative ordered state. Preserve every distinct phase, its ordering, entry/exit conditions, system events, and victory/failure-to-results-to-restart paths. Never replace it with a core-loop or genre summary.
- Respect `game_scope_profile`: keep `compact_loop` games minimal and do not invent extra phases; preserve the complete ordered chain for `multi_phase_journey`. A closed loop does not require many states.
- Preserve `rendering_branch`, `dimension_requirement`, and `camera_contract`. Never silently convert between 2D, fixed-camera 2.5D, and movable-camera 3D.
- For 2.5D/3D, treat `three_d_scene_quality_contract`, `scene_density_contract`, `lighting_contract`, `environmental_motion_contract`, and numeric `geometry_density_budget` as protected content. Retain concrete geometry hierarchy, visible-detail density, foreground/midground/background composition, material/color variation, lights/shadows/fog, camera-corner fill, and motion loops.
- Keep gameplay and 3D presentation at roughly balanced information density. Never shorten the visual contract into generic adjectives to save tokens, and never remove gameplay to preserve art prose. Remove repetition elsewhere first.
- Preserve `primary_gameplay_type`, `gameplay_archetype`, and every `genre_required_phase`. Keep the selected gameplay pipeline's distinct rules and ordered phases; never compact them into a generic genre sentence.
- Treat gameplay-library guidance as expert attention routing only. Apply it where the source-derived mechanic exists; never invent a mode, system, or phase merely because the library mentions it.
- Preserve only the semantic meaning of `asset_production_mode`: retain the zero-image rule for `procedural_pixel`, or the requirement to execute the later deterministic asset table for `generated_hybrid`.
- Use natural imperative English for dimension, genre, camera, gameplay, controls, rules, progression, scenes, visual direction, and execution requirements. Do not emit the original large nested JSON.
- Retain only: concise game brief; core mechanics; controls and critical numeric rules; win/lose/restart; minimal scene/state flow; visual/camera direction; technology/performance constraints; unique acceptance checks.
- Preserve `render_viewport` and every background dimension/aspect-ratio requirement exactly. Require one full-bleed background with proportional cover/crop; never tile, repeat, mirror-repeat, stretch, or duplicate it to fill the viewport.
- Preserve every distinct gameplay-critical requirement while dropping repeated summaries, decorative metadata, provenance, and duplicated must-implement statements.
- Target 800 to 1,800 English words; exceed this only when distinct required asset prompts make it unavoidable. Prefer shorter text for simple games.
- Use English only, including player-visible copy requirements. Do not retain Chinese text.
- Do not call tools and do not implement the game. Output only the query that will later be sent to RLE.
"""

DOMAIN_COMPACT_LANGUAGE_REPAIR_SYSTEM = """Repair the supplied implementation query without shortening or redesigning it.
Translate every remaining Chinese/CJK phrase into precise implementation-ready English. Preserve all non-Chinese content, Markdown structure, requirements, numeric values, asset IDs, file paths, dimensions, transparency/background rules, and asset prompt meaning. Output only the complete repaired query. Do not explain the repair and do not omit any field."""


def _strip_optional_fence(response: str) -> str:
    response = response.strip()
    fenced = re.fullmatch(r"```(?:markdown|md|text)?\s*(.*?)\s*```", response, re.I | re.S)
    return (fenced.group(1) if fenced else response).strip()


def compact_rle_query(*, args: argparse.Namespace, api_key: str, raw_query: str) -> str:
    response = call_chat_completion(
        base_url=args.base_url, api_key=api_key, model=args.model,
        messages=[
            {"role": "system", "content": DOMAIN_COMPACT_SYSTEM},
            {"role": "user", "content": raw_query},
        ],
        temperature=0.1, max_tokens=args.compact_max_tokens,
        timeout_s=args.timeout_s, retries=args.api_retries,
        retry_sleep_s=args.api_retry_sleep_s,
    )
    compact = _strip_optional_fence(response)
    if len(compact) < 500:
        raise ValueError("Domain Compact returned an implausibly short query")
    if re.search(r"[\u4e00-\u9fff]", compact):
        repaired = call_chat_completion(
            base_url=args.base_url, api_key=api_key, model=args.model,
            messages=[
                {"role": "system", "content": DOMAIN_COMPACT_LANGUAGE_REPAIR_SYSTEM},
                {"role": "user", "content": compact},
            ],
            temperature=0.0, max_tokens=args.compact_max_tokens,
            timeout_s=args.timeout_s, retries=args.api_retries,
            retry_sleep_s=args.api_retry_sleep_s,
        )
        compact = _strip_optional_fence(repaired)
        if len(compact) < 500:
            raise ValueError("Domain Compact language repair returned an implausibly short query")
        if re.search(r"[\u4e00-\u9fff]", compact):
            raise ValueError("Domain Compact language repair still contains Chinese text")
    return compact


def semantic_only_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    semantic = json.loads(json.dumps(spec, ensure_ascii=False))
    semantic.pop("asset_contract", None)
    presentation = semantic.get("presentation_and_technology")
    if isinstance(presentation, dict):
        for key in ("global_style_prompt", "global_negative_prompt", "prompt_presets"):
            presentation.pop(key, None)
    return semantic


def deterministic_asset_table(spec: Dict[str, Any]) -> Dict[str, Any]:
    presentation = spec.get("presentation_and_technology") or {}
    source_contract = spec.get("asset_contract") or {}
    compositions = source_contract.get("scene_composition_assets") or []
    assets = list(source_contract.get("required_assets") or []) + list(compositions)
    rows_by_id: Dict[str, Dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        has_prompt = bool(asset.get("generation_prompt") or asset.get("generation_prompt_delta"))
        source = str(asset.get("source") or ("generate_image" if has_prompt else "canvas_draw"))
        if source == "inline_shader":
            validate_inline_shader_asset(asset)
        row = select_fields(asset, [
            "id", "role", "purpose", "expected_file_path", "dimensions",
            "transparent_background", "background_only", "prompt_preset",
            "foreground_strategy", "key_color",
            "runtime_background_removal_required", "edge_cleanup",
            "alpha_trim_required",
            "generation_prompt_delta", "negative_prompt_delta",
            "generation_prompt", "negative_prompt",
            "canonical_facing", "orientation_landmarks", "runtime_flip_policy",
        ])
        procedural_delta = select_fields(asset, [
            "composition_and_layers", "animation_or_state_variants",
        ])
        shader_delta = shader_contract(asset) if source == "inline_shader" else {}
        three_d_delta = select_fields(asset, [
            "geometry_or_model_strategy", "named_visible_parts", "material_recipe",
            "texture_recipe", "dimensions_or_scale", "placement_in_scene",
            "animation_or_motion_loop", "lighting_dependency",
        ])
        if shader_delta:
            row["implementation_delta"] = shader_delta
        elif three_d_delta:
            row["implementation_delta"] = three_d_delta
        elif procedural_delta:
            row["implementation_delta"] = procedural_delta
        row["source"] = source
        asset_id = str(row.get("id") or "")
        if not asset_id:
            raise ValueError("Incomplete deterministic asset row: <missing id>")
        merged = rows_by_id.setdefault(asset_id, {})
        merged.update({
            key: value for key, value in row.items()
            if key != "source" and value not in (None, "", [], {})
        })
        if asset.get("source") or has_prompt or not merged.get("source"):
            merged["source"] = source
        # The runtime contract is deterministic pipeline policy, not creative
        # model output. Normalize it here so a valid foreground prompt cannot
        # fail merely because the planner omitted redundant execution fields.
        if merged.get("transparent_background") is True and source == "generate_image":
            merged.update({
                "foreground_strategy": "chroma_key_runtime",
                "key_color": "#FF00FF",
                "runtime_background_removal_required": True,
                "edge_cleanup": "soft_alpha_and_magenta_despill",
                "alpha_trim_required": True,
            })
    rows = list(rows_by_id.values())
    prompt_presets = presentation.get("prompt_presets") or {}
    if isinstance(prompt_presets, list):
        preset_ids = {
            str(item.get("id") or item.get("preset_id"))
            for item in prompt_presets if isinstance(item, dict)
        }
    elif isinstance(prompt_presets, dict):
        preset_ids = set(map(str, prompt_presets))
    else:
        preset_ids = set()
    for row in rows:
        if not row.get("expected_file_path") or not row.get("source"):
            raise ValueError(f"Incomplete deterministic asset row: {row['id']}")
        if row["source"] in {"generate_image", "fetch_media"} and not (
            row.get("generation_prompt") or row.get("generation_prompt_delta")
        ):
            raise ValueError(f"Generated asset has no prompt: {row['id']}")
        if row.get("prompt_preset") and str(row["prompt_preset"]) not in preset_ids:
            raise ValueError(f"Asset references missing prompt preset: {row['id']}")
        if row["source"] == "inline_shader":
            validate_inline_shader_asset({
                "id": row["id"],
                "expected_file_path": row["expected_file_path"],
                **(row.get("implementation_delta") or {}),
            })
        if row.get("transparent_background") is True and row["source"] == "generate_image":
            if not (
                row.get("foreground_strategy") == "chroma_key_runtime"
                and row.get("key_color") == "#FF00FF"
                and row.get("runtime_background_removal_required") is True
                and row.get("edge_cleanup") == "soft_alpha_and_magenta_despill"
                and row.get("alpha_trim_required") is True
            ):
                raise ValueError(f"Generated foreground lacks chroma-key execution contract: {row['id']}")
    if not rows:
        raise ValueError("Deterministic asset table is empty")
    table = {
        "asset_production_mode": (spec.get("product_identity") or {}).get("asset_production_mode"),
        "shared_presets": select_fields(presentation, [
            "art_direction", "global_style_prompt", "global_negative_prompt",
            "style_consistency_rules", "prompt_presets", "color_role_contract",
        ]),
        "assets": rows,
    }
    uncovered = source_contract.get("uncovered_blueprint_requirements")
    if uncovered:
        table["uncovered_blueprint_requirements"] = uncovered
    return table


SCREEN_SPACE_DIRECTIONAL_CONTROL_REMINDER = (
    "For this 3D game's declared directional movement, keep horizontal and vertical/depth inputs "
    "on separate camera-relative axes—A/Left must move screen-left, D/Right screen-right, and when "
    "W/Up and S/Down are movement inputs they must move screen-up/forward and screen-down/backward "
    "respectively; never reverse directions or swap the horizontal and vertical/depth axes."
)


SIDE_VIEW_ASSET_ORIENTATION_REMINDER = (
    "For every directional actor or vehicle that appears in a 2D side view, use image-right as the "
    "canonical +X/forward direction unless the approved gameplay contract explicitly says otherwise. "
    "The source artwork must put an unmistakable front landmark (face, nose, cab, front fork, or front "
    "wheel assembly) on image-right and a rear landmark (tail, counterweight, rear fork, or rear wheel "
    "assembly) on image-left; the words 'facing right' alone are not sufficient. After generate_image "
    "returns, inspect those landmarks before integration. If the bitmap is reversed, regenerate it or "
    "flip it horizontally exactly once, then treat the corrected bitmap as canonical and do not apply a "
    "second runtime mirror. Verify in the running game that positive forward input moves toward screen-right "
    "while the actor's front points screen-right. Reverse input normally drives backward; only mirror from "
    "the canonical asset when the gameplay explicitly turns the actor around."
)


def requires_3d_directional_movement_reminder(spec: Dict[str, Any]) -> bool:
    product = spec.get("product_identity") or {}
    branch = str(
        product.get("rendering_branch") or product.get("game_dimension") or ""
    ).strip().lower()
    if branch != "3d":
        return False
    gameplay = spec.get("gameplay_spec") or {}
    movement_fields = {
        key: gameplay.get(key)
        for key in ("controls", "game_rules", "core_loop", "gameplay_flow_contract")
        if gameplay.get(key)
    }
    movement_text = json.dumps(movement_fields, ensure_ascii=False).lower()
    english_movement = re.search(
        r"\b(?:wasd|arrows?|move|movement|navigate|navigation|walk|run|strafe|"
        r"steer|drive|fly|swim|locomotion|left|right)\b",
        movement_text,
    )
    return bool(english_movement) or any(token in movement_text for token in (
        "移动", "方向", "行走", "奔跑", "驾驶", "操纵", "飞行", "游泳",
    ))


def requires_2d_side_view_asset_reminder(spec: Dict[str, Any]) -> bool:
    product = spec.get("product_identity") or {}
    branch = str(
        product.get("rendering_branch") or product.get("game_dimension") or ""
    ).strip().lower()
    if branch != "2d":
        return False
    contract_text = json.dumps({
        "gameplay": spec.get("gameplay_spec") or {},
        "presentation": spec.get("presentation_and_technology") or {},
        "assets": spec.get("asset_contract") or {},
    }, ensure_ascii=False).lower()
    return bool(re.search(
        r"\b(?:side[ -]?view|side[ -]?scroll(?:ing|er)?|profile view)\b",
        contract_text,
    )) or any(token in contract_text for token in ("侧视", "横版", "横向卷轴"))


def compose_final_rle_query(semantic_query: str, spec: Dict[str, Any]) -> str:
    table = deterministic_asset_table(spec)
    reminders: List[str] = []
    if requires_3d_directional_movement_reminder(spec):
        reminders.append(SCREEN_SPACE_DIRECTIONAL_CONTROL_REMINDER)
    if requires_2d_side_view_asset_reminder(spec):
        reminders.append(SIDE_VIEW_ASSET_ORIENTATION_REMINDER)
    reminder_text = "".join("\n\n" + reminder for reminder in reminders)
    return (
        semantic_query.rstrip()
        + reminder_text
        + "\n\n# Deterministic Asset Execution Contract\n"
        + "The following pipeline-generated table is authoritative. Create and use every listed asset; "
          "do not rename paths or replace generated foregrounds with baked-background images. "
          "For every chroma_key_runtime foreground, generate the specified uniform key-color background, "
          "then load it through one shared runtime keying function that applies soft alpha, color despill, "
          "and alpha trimming; no rectangular white, checkerboard, or key-color background may remain visible.\n\n```json\n"
        + json.dumps(table, ensure_ascii=False, separators=(",", ":"))
        + "\n```"
    )


def summarize_rle_output(output_path: Path, stage_dir: Path) -> str:
    if not output_path.exists() or not output_path.read_text(encoding="utf-8").strip():
        return "# RLE Implementation Result\n\nRLE output missing or empty.\n"
    trajectory = json.loads(output_path.read_text(encoding="utf-8").splitlines()[-1])
    files = trajectory.get("files") or {}
    summary = {key: trajectory.get(key) for key in ["success", "error", "bos_id", "bos_url", "total_time_cost", "model", "client_type"]}
    summary["file_count"] = len(files)
    write_text(stage_dir / "rle_summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    write_text(stage_dir / "files_snapshot.json", json.dumps(files, ensure_ascii=False, indent=2) + "\n")
    write_text(stage_dir / "bos_url.txt", str(summary.get("bos_url") or "") + "\n")
    return "\n".join(["# RLE Implementation Result", "", *(f"- {k}: {v}" for k, v in summary.items()), ""])


def count_rle_tool_calls(output_path: Path, tool_name: str) -> int:
    if not output_path.exists() or not output_path.read_text(encoding="utf-8").strip():
        return 0
    trajectory = json.loads(output_path.read_text(encoding="utf-8").splitlines()[-1])
    return sum(
        1
        for message in (trajectory.get("messages") or [])
        for call in (message.get("tool_calls") or [])
        if (call.get("function") or {}).get("name") == tool_name
    )


def run_rle(*, args: argparse.Namespace, run_dir: Path, stage: Stage, stage_dir: Path, seed: SeedInput,
            previous: List[Dict[str, str]], feedback: str, api_key: str, attempt: int) -> tuple[str, Path]:
    rle_input, rle_output = prepare_rle_input(
        run_dir=run_dir, stage=stage, stage_dir=stage_dir, seed=seed,
        previous=previous, feedback=feedback, attempt=attempt,
    )

    if args.dry_run:
        response = f"# RLE Implementation Result\n\n[DRY RUN] Input: `{rle_input}`\n"
    else:
        if not args.rle_script or not Path(args.rle_script).is_file():
            raise ValueError("An external execution runner is required for this legacy hook. "
                             "Use the batch JSONL interface for query-only generation.")
        rle_model = args.rle_model or args.model
        command = [sys.executable, args.rle_script, "--input_file", str(rle_input), "--output", str(rle_output),
                   "--client", "openai", "--model", rle_model, "--base-url", normalize_openai_base_url(args.base_url),
                   "--api-key", api_key, "--workers", "1", "--max-steps", str(args.rle_max_steps),
                   "--max-tokens", str(args.rle_max_tokens), "--temperature", str(args.rle_temperature),
                   "--top-p", str(args.rle_top_p)]
        recorded_command = command.copy()
        recorded_command[recorded_command.index("--api-key") + 1] = f"${API_KEY_ENV}"
        write_text(
            stage_dir / f"attempt_{attempt}_rle_command.json",
            json.dumps(recorded_command, ensure_ascii=False, indent=2) + "\n",
        )
        try:
            child_env = os.environ.copy()
            child_env["RLE_DOCKER_IMAGE"] = args.rle_docker_image
            for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
                child_env.pop(key, None)
            log_path = stage_dir / f"attempt_{attempt}_rle_stdout.log"
            started_at = time.monotonic()
            with log_path.open("w", encoding="utf-8", buffering=1) as log_handle:
                proc = subprocess.Popen(
                    command, cwd=str(Path(args.rle_script).resolve().parent), text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, env=child_env,
                )
                selector = selectors.DefaultSelector()
                assert proc.stdout is not None
                selector.register(proc.stdout, selectors.EVENT_READ)
                while proc.poll() is None:
                    if time.monotonic() - started_at > args.rle_timeout_s:
                        proc.kill()
                        proc.wait()
                        raise RuntimeError(f"RLE timed out after {args.rle_timeout_s}s")
                    for key, _ in selector.select(timeout=1.0):
                        line = key.fileobj.readline()
                        if line:
                            log_handle.write(line)
                            print(f"[{seed.data_id} RLE] {line.rstrip()}", flush=True)
                # Drain buffered output emitted just before process exit.
                for line in proc.stdout:
                    log_handle.write(line)
                    print(f"[{seed.data_id} RLE] {line.rstrip()}", flush=True)
                selector.close()
            if proc.returncode == 0:
                generate_image_calls = count_rle_tool_calls(rle_output, "generate_image")
                write_text(stage_dir / "asset_execution_audit.json", json.dumps({
                    "generate_image_calls": generate_image_calls, "required": True,
                }, ensure_ascii=False, indent=2) + "\n")
                if generate_image_calls == 0:
                    raise RuntimeError("RLE skipped all required generate_image prompts")
                response = summarize_rle_output(rle_output, stage_dir)
            else:
                raise RuntimeError(f"RLE failed with return code {proc.returncode}")
        except BaseException:
            if "proc" in locals() and proc.poll() is None:
                proc.kill()
                proc.wait()
            raise
    path = stage_dir / stage.artifact_file
    write_text(path, response)
    return response, path


def prepare_rle_input(*, run_dir: Path, stage: Stage, stage_dir: Path, seed: SeedInput,
                      previous: List[Dict[str, str]], feedback: str, attempt: int,
                      args: Optional[argparse.Namespace] = None, api_key: str = "") -> tuple[Path, Path]:
    """Select Stage 1-3 facts, compact them, and persist one RLE input without starting RLE."""
    rle_input = stage_dir / f"attempt_{attempt}_rle_input.jsonl"
    rle_output = stage_dir / f"attempt_{attempt}_rle_output.jsonl"
    data_id = f"{safe_name(seed.data_id, 'item')}_{run_dir.name}_{stage.stage_id}_attempt{attempt}"
    implementation_spec = select_canonical_spec(seed, previous)
    write_text(
        stage_dir / "canonical_selected_spec.json",
        json.dumps(implementation_spec, ensure_ascii=False, indent=2) + "\n",
    )
    raw_query = build_rle_query(seed, previous, feedback, implementation_spec)
    write_text(stage_dir / "canonical_rle_query.md", raw_query.rstrip() + "\n")
    semantic_query = build_rle_query(
        seed, previous, feedback, semantic_only_spec(implementation_spec)
    )
    write_text(stage_dir / "semantic_rle_query.md", semantic_query.rstrip() + "\n")
    compact_semantic = semantic_query
    if args is not None and not args.dry_run:
        compact_path = stage_dir / "compact_semantic_query.md"
        reusable = read_text(compact_path).strip() if args.resume and compact_path.exists() else ""
        if len(reusable) >= 500 and not re.search(r"[\u4e00-\u9fff]", reusable):
            compact_semantic = reusable
            print(f"[Domain Compact] Reusing semantic compact for {seed.data_id}", flush=True)
        else:
            compact_semantic = compact_rle_query(
                args=args, api_key=api_key, raw_query=semantic_query
            )
        write_text(compact_path, compact_semantic.rstrip() + "\n")
    query = compose_final_rle_query(compact_semantic, implementation_spec)
    write_text(stage_dir / "compact_rle_query.md", query.rstrip() + "\n")
    write_text(rle_input, json.dumps({
        "data_id": data_id,
        "query": query,
    }, ensure_ascii=False) + "\n")
    return rle_input, rle_output


def run_stage(*, args: argparse.Namespace, run_dir: Path, seed: SeedInput, stages: List[Stage], index: int, api_key: str) -> None:
    stage = stages[index]
    stage_dir = run_dir / "stages" / stage.stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = stage_dir / stage.artifact_file
    if args.resume and recover_complete_artifact_from_raw(stage, stage_dir):
        return
    if args.resume and stage.stage_id == "03_asset_contract" and artifact_path.exists():
        try:
            validate_text_visual_manifest(artifact_path)
        except (OSError, ValueError):
            pass
        else:
            write_text(stage_dir / "pipeline_status.json", json.dumps({
                "stage": stage.stage_id, "status": "completed", "artifact": str(artifact_path),
                "timestamp": int(time.time()), "recovered_from_existing_artifact": True,
            }, ensure_ascii=False, indent=2) + "\n")
            print(f"[{index + 1}/{len(stages)}] Resume: recovered valid {stage.stage_id}: {artifact_path}", flush=True)
            return
    # Keep the V2 stage execution path; WebGame Pipeline automatically approves each stage.
    feedback = ""
    for attempt in range(stage.max_revisions + 1):
        previous = collect_artifacts(run_dir, stages, index)
        skill_routing = route_skill_guidance(
            stage.stage_id, previous, enabled=args.use_skill_cards
        )
        write_text(stage_dir / "skill_routing.json", json.dumps(
            skill_routing.audit_record(), ensure_ascii=False, indent=2
        ) + "\n")
        messages = build_stage_messages(
            seed=seed,
            stage=stage,
            previous=previous,
            feedback=feedback,
            use_skill_cards=args.use_skill_cards,
            spatial_dimension_preference_rate=args.spatial_dimension_preference_rate,
        )
        write_text(stage_dir / f"attempt_{attempt}_messages.json", json.dumps(messages, ensure_ascii=False, indent=2) + "\n")
        print(f"[{index + 1}/{len(stages)}] Running {stage.stage_id} - {stage.name}", flush=True)
        try:
            if stage.stage_id == "04_implementation":
                response, artifact = run_rle(args=args, run_dir=run_dir, stage=stage, stage_dir=stage_dir, seed=seed,
                                             previous=previous, feedback=feedback, api_key=api_key, attempt=attempt)
            elif args.dry_run:
                response = json.dumps({"dry_run": True, "stage": stage.stage_id}, ensure_ascii=False)
                artifact = materialize_artifact(stage, response, stage_dir)
            else:
                response = call_chat_completion(base_url=args.base_url, api_key=api_key, model=args.model, messages=messages,
                                                temperature=args.temperature, max_tokens=args.max_tokens,
                                                timeout_s=args.timeout_s, retries=args.api_retries,
                                                retry_sleep_s=args.api_retry_sleep_s,
                                                json_mode=True)
                # Persist the provider response before parsing so malformed or truncated JSON remains diagnosable.
                write_text(stage_dir / f"attempt_{attempt}_raw.md", str(response).rstrip() + "\n")
                artifact = materialize_artifact(stage, response, stage_dir)
        except ValueError as exc:
            if stage.artifact_file.endswith(".json") and attempt < stage.max_revisions:
                feedback = (
                    "The previous response was not a complete valid JSON artifact. Return the entire artifact "
                    "again as one JSON object, preserving every required field and all substantive detail. "
                    "Do not use markdown fences or embed unescaped multiline code inside JSON strings. "
                    f"Parser/quality error: {exc}"
                )
                print(
                    f"[{index + 1}/{len(stages)}] JSON artifact failed; retrying current stage: {exc}",
                    flush=True,
                )
                continue
            raise
        if stage.stage_id == "03_asset_contract" and not args.dry_run:
            try:
                validate_text_visual_manifest(artifact)
            except ValueError as exc:
                if attempt < stage.max_revisions:
                    feedback = (
                        "The asset contract failed WebGame Pipeline validation. Correct the JSON and return the entire "
                        f"asset_manifest.json again. Validation error: {exc}"
                    )
                    print(f"[3/4] Validation failed; retrying: {exc}", flush=True)
                    continue
                raise
        write_text(stage_dir / f"attempt_{attempt}_raw.md", response.rstrip() + "\n")
        write_text(stage_dir / f"attempt_{attempt}_review.json", json.dumps({
            "stage": stage.stage_id, "attempt": attempt, "approved": True, "feedback": "",
            "artifact": str(artifact), "timestamp": int(time.time()), "review_mode": "automatic",
        }, ensure_ascii=False, indent=2) + "\n")
        write_text(stage_dir / "pipeline_status.json", json.dumps({
            "stage": stage.stage_id, "status": "completed", "artifact": str(artifact),
            "timestamp": int(time.time()),
        }, ensure_ascii=False, indent=2) + "\n")
        print(f"[{index + 1}/{len(stages)}] Completed {stage.stage_id}: {artifact}", flush=True)
        return


def run_one(*, args: argparse.Namespace, seed: SeedInput, run_dir: Path, stages: List[Stage], start: int, stop: int, api_key: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    write_text(run_dir / "seed_input.json", json.dumps({
        "source_kind": seed.source_kind, "data_id": seed.data_id, "metadata": seed.metadata, "image_urls": seed.image_urls,
    }, ensure_ascii=False, indent=2) + "\n")
    write_text(run_dir / "run_config.json", json.dumps({
        "config": str(args.config.resolve()), "model": args.model, "rle_model": args.rle_model or args.model,
        "rle_docker_image": args.rle_docker_image,
        "from_stage": args.from_stage,
        "to_stage": args.to_stage, "pipeline_mode": True, "dry_run": args.dry_run,
        "use_skill_cards": args.use_skill_cards,
        "spatial_dimension_preference_rate": args.spatial_dimension_preference_rate,
        "spatial_dimension_preference_selected": spatial_dimension_preference_selected(
            seed.data_id, args.spatial_dimension_preference_rate
        ),
    }, ensure_ascii=False, indent=2) + "\n")
    resume_start = start
    if args.resume:
        for index in range(start, stop + 1):
            stage = stages[index]
            if stage_is_completed(run_dir, stage):
                print(f"[{index + 1}/{len(stages)}] Resume: skipping completed {stage.stage_id}", flush=True)
                resume_start = index + 1
            else:
                break
    for index in range(resume_start, stop + 1):
        run_stage(args=args, run_dir=run_dir, seed=seed, stages=stages, index=index, api_key=api_key)


def run_webgame_batch(*, args: argparse.Namespace, rows: List[Dict[str, Any]], root: Path,
                    stages: List[Stage], start: int, stop: int, api_key: str,
                    timestamp: str, compact_each_item: bool = False) -> Dict[str, Any]:
    checkpoint_path = root / "completed_items.json"
    checkpoint = load_json(checkpoint_path) if checkpoint_path.exists() else {"version": 1, "items": {}}
    completed_items = checkpoint.get("items") if isinstance(checkpoint.get("items"), dict) else {}
    target_stage = stages[stop].stage_id
    target_stage_config = stages[stop]
    def completed_in_data_directory(position: int, row: Dict[str, Any]) -> bool:
        data_id = safe_name(webgame_source_record_id(row), f"Data{position}")
        return stage_is_completed(root / data_id, target_stage_config)

    batch_path = root / "rle_inputs.jsonl"
    streamed_inputs: List[Optional[Dict[str, str]]] = [None] * len(rows)
    if compact_each_item and batch_path.exists() and not args.resume:
        batch_path.unlink()
    elif compact_each_item and batch_path.exists() and args.resume:
        saved = {
            str(item.get("data_id")): item
            for item in read_jsonl(batch_path)
            if item.get("data_id") and item.get("query")
        }
        for position, row in enumerate(rows, 1):
            data_id = webgame_seed_from_row(row, position=position).data_id
            if data_id in saved:
                streamed_inputs[position - 1] = {
                    "data_id": data_id, "query": str(saved[data_id]["query"]),
                }

    pending = [
        (position, row, webgame_row_fingerprint(row))
        for position, row in enumerate(rows, 1)
        if not (
            args.resume
            and (
                (compact_each_item and streamed_inputs[position - 1] is not None)
                or (not compact_each_item and completed_in_data_directory(position, row))
            )
        )
    ]
    skipped = len(rows) - len(pending)
    workers = max(1, min(args.pipeline_workers, len(pending))) if pending else 1
    print(f"Web-game batch: {len(rows)} item(s), pipeline concurrency={workers}, root={root}", flush=True)
    if skipped:
        print(f"Resume: skipping {skipped} item(s) already completed through {target_stage}", flush=True)

    input_map = []
    for position, row in enumerate(rows, 1):
        fingerprint = webgame_row_fingerprint(row)
        data_id = safe_name(webgame_source_record_id(row), f"Data{position}")
        data_dir = root / data_id
        prior = completed_items.get(fingerprint) if isinstance(completed_items.get(fingerprint), dict) else {}
        directory_completed = stage_is_completed(data_dir, target_stage_config)
        input_map.append({
            "input_position": position,
            "source_record_id": webgame_source_record_id(row),
            "title": normalized_webgame_fields(row)["title"],
            "source_url": normalized_webgame_fields(row)["source_url"],
            "input_fingerprint": fingerprint,
            "data_id": data_id,
            "status": "completed" if directory_completed else "pending",
            "run_dir": str(data_dir) if directory_completed else prior.get("run_dir", ""),
        })
    write_text(root / "input_game_map.json", json.dumps(input_map, ensure_ascii=False, indent=2) + "\n")
    def compile_item(position: int, row: Dict[str, Any], seed: SeedInput,
                     item_dir: Path) -> Dict[str, str]:
        previous = collect_artifacts(item_dir, stages, 3)
        if len(previous) != 3:
            raise ValueError(f"expected 3 planning artifacts, found {len(previous)}")
        stage = stages[3]
        stage_dir = item_dir / "stages" / stage.stage_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        rle_input, _ = prepare_rle_input(
            run_dir=item_dir, stage=stage, stage_dir=stage_dir, seed=seed,
            previous=previous, feedback="", attempt=0, args=args, api_key=api_key,
        )
        payload = json.loads(read_text(rle_input).strip())
        return {"data_id": seed.data_id, "query": str(payload["query"])}

    def run_item(position: int, row: Dict[str, Any], fingerprint: str) -> Dict[str, Any]:
        seed = webgame_seed_from_row(row, position=position)
        item_dir = root / safe_name(seed.data_id, f"Data{position}")
        print(f"\n[item {position}/{len(rows)}] START {seed.data_id}: {item_dir}", flush=True)
        phase = "planning"
        try:
            planning_complete = args.resume and stage_is_completed(item_dir, stages[min(stop, 2)])
            if planning_complete:
                print(f"[item {position}/{len(rows)}] Resume: planning already complete", flush=True)
            else:
                run_one(args=args, seed=seed, run_dir=item_dir, stages=stages,
                        start=start, stop=stop, api_key=api_key)
            phase = "compact"
            payload = compile_item(position, row, seed, item_dir) if compact_each_item else None
        except BaseException as exc:
            if phase == "compact" and stop >= 2:
                stage_dir = item_dir / "stages" / stages[2].stage_id
                for stale in (stage_dir / stages[2].artifact_file,
                              stage_dir / "pipeline_status.json"):
                    if stale.exists():
                        stale.unlink()
            failure = {
                "position": position, "data_id": seed.data_id, "status": "failed",
                "run_dir": str(item_dir), "error_type": type(exc).__name__, "error": str(exc),
                "timestamp": int(time.time()),
            }
            write_text(item_dir / "pipeline_failure.json", json.dumps(failure, ensure_ascii=False, indent=2) + "\n")
            print(f"[item {position}/{len(rows)}] FAILED {seed.data_id}: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            return failure
        result = {
            "position": position, "data_id": seed.data_id, "status": "completed",
            "input_fingerprint": fingerprint, "completed_through_stage": target_stage,
            "run_dir": str(item_dir), "timestamp": int(time.time()),
        }
        if payload is not None:
            result["rle_payload"] = payload
        print(f"[item {position}/{len(rows)}] COMPLETED {seed.data_id}", flush=True)
        return result

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gamefactory-item") as pool:
        futures = {pool.submit(run_item, position, row, fingerprint): position for position, row, fingerprint in pending}
        for future in as_completed(futures):
            try:
                item = future.result()
                results.append(item)
                payload = item.get("rle_payload")
                if compact_each_item and isinstance(payload, dict):
                    streamed_inputs[item["position"] - 1] = payload
                    with batch_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
                        stream.flush()
                    print(
                        f"[RLE input] Streamed {payload['data_id']} "
                        f"({sum(row is not None for row in streamed_inputs)}/{len(rows)}): {batch_path}",
                        flush=True,
                    )
                if item.get("status") == "completed" and item.get("input_fingerprint"):
                    completed_items[item["input_fingerprint"]] = item
                    for mapped in input_map:
                        if mapped["input_fingerprint"] == item["input_fingerprint"]:
                            mapped["status"] = "completed"
                            mapped["run_dir"] = item.get("run_dir", "")
            except BaseException as exc:
                # Covers malformed input failures before an item directory can be created.
                position = futures[future]
                results.append({
                    "position": position, "data_id": f"item_{position:04d}", "status": "failed",
                    "error_type": type(exc).__name__, "error": str(exc), "timestamp": int(time.time()),
                })

    results.sort(key=lambda item: item["position"])
    write_text(checkpoint_path, json.dumps({
        "version": 1, "input_file": str(args.webgame_jsonl.resolve()),
        "updated_at": int(time.time()), "items": completed_items,
    }, ensure_ascii=False, indent=2) + "\n")
    write_text(root / "input_game_map.json", json.dumps(input_map, ensure_ascii=False, indent=2) + "\n")
    summary = {
        "total": len(results),
        "completed": sum(item["status"] == "completed" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "skipped_completed": skipped,
        "pipeline_workers": workers,
        "items": results,
    }
    write_text(root / "batch_summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if compact_each_item:
        summary["prepared"] = sum(row is not None for row in streamed_inputs)
    return summary


def prepare_webgame_rle_inputs(*, rows: List[Dict[str, Any]], root: Path,
                               stages: List[Stage], args: Optional[argparse.Namespace] = None,
                               api_key: str = "") -> Dict[str, int]:
    """Prepare per-item inputs and one ordered batch JSONL without running RLE."""
    stage = stages[3]
    prepared = failed = 0
    ordered_inputs: List[Optional[Dict[str, str]]] = [None] * len(rows)
    batch_path = root / "rle_inputs.jsonl"
    if batch_path.exists():
        batch_path.unlink()
    def prepare_one(position: int, row: Dict[str, Any]) -> tuple[int, Dict[str, str]]:
        seed = webgame_seed_from_row(row, position=position)
        run_dir = root / safe_name(seed.data_id, f"Data{position}")
        previous = collect_artifacts(run_dir, stages, 3)
        if len(previous) != 3:
            raise ValueError(f"expected 3 planning artifacts, found {len(previous)}")
        stage_dir = run_dir / "stages" / stage.stage_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        rle_input, _ = prepare_rle_input(
            run_dir=run_dir, stage=stage, stage_dir=stage_dir, seed=seed,
            previous=previous, feedback="", attempt=0, args=args, api_key=api_key,
        )
        payload = json.loads(read_text(rle_input).strip())
        return position - 1, {"data_id": seed.data_id, "query": str(payload["query"])}

    workers = args.pipeline_workers if args is not None else 1
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(prepare_one, position, row): position
            for position, row in enumerate(rows, 1)
        }
        for future in as_completed(futures):
            position = futures[future]
            try:
                index, payload = future.result()
                ordered_inputs[index] = payload
                prepared += 1
                print(f"[RLE input] Prepared {payload['data_id']}", flush=True)
            except Exception as exc:
                failed += 1
                print(f"[RLE input] FAILED Data{position}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    if failed == 0:
        batch_inputs = [item for item in ordered_inputs if item is not None]
        write_text(
            batch_path,
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in batch_inputs),
        )
        print(f"[RLE input] Batch JSONL: {root / 'rle_inputs.jsonl'}", flush=True)
    return {"prepared": prepared, "failed": failed}


def main() -> None:
    global API_RETRY_BACKOFF_MODE
    parser = argparse.ArgumentParser(description="GameGo text-input task construction")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--query")
    inputs.add_argument("--query-file", type=Path)
    inputs.add_argument("--seed-jsonl", "--webgame-jsonl", dest="webgame_jsonl", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--batch-limit", type=int, default=0)
    parser.add_argument(
        "--resume", action=argparse.BooleanOptionalAction, default=True,
        help="Skip JSONL items already completed in this run directory (default: enabled).",
    )
    parser.add_argument(
        "--pipeline-workers", type=int, default=1,
        help="并发数据条数；每条完成 Stage 1-3 后立即 Compact 并写入 rle_inputs.jsonl。",
    )
    parser.add_argument(
        "--item-retries", type=int, default=2,
        help="批次结束后自动补跑失败项的轮数；默认2轮，复用成功Stage和已落盘Query。",
    )
    parser.add_argument(
        "--skill-cards",
        dest="use_skill_cards",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Inject routed Skill Cards into planning prompts (default: enabled). Use --no-skill-cards for ablation.",
    )
    parser.add_argument(
        "--spatial-dimension-preference-rate",
        type=float,
        default=0.40,
        help=(
            "Stable fraction of Stage-1 items receiving a soft 2.5D/3D diversity preference "
            "(default: 0.40; use 0 to disable). Explicit source constraints always win."
        ),
    )
    parser.add_argument("--from-stage", default="")
    parser.add_argument("--to-stage", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--rle-model", default="",
        help="仅用于最终 RLE 实现；为空时沿用 --model。需提供用户自己的执行模型。",
    )
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=32000)
    parser.add_argument(
        "--compact-max-tokens", "--query-compiler-max-tokens",
        dest="compact_max_tokens", type=int, default=12000,
        help="Maximum output tokens for the mandatory Protected Domain Compact step.",
    )
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--api-retries", type=int, default=4)
    parser.add_argument("--api-retry-sleep-s", type=float, default=5.0)
    parser.add_argument(
        "--api-retry-backoff",
        choices=("exponential", "fixed"),
        default="exponential",
        help="Retry delay policy: exponential uses base*2^attempt; fixed always uses the base delay.",
    )
    parser.add_argument("--rle-script", default=DEFAULT_RLE_SCRIPT)
    parser.add_argument(
        "--rle-docker-image", default=DEFAULT_RLE_DOCKER_IMAGE,
        help="Docker image used by the RLE scaffold session.",
    )
    parser.add_argument("--rle-max-steps", type=int, default=60)
    parser.add_argument("--rle-max-tokens", type=int, default=32000)
    parser.add_argument("--rle-temperature", type=float, default=0.6)
    parser.add_argument("--rle-top-p", type=float, default=0.95)
    parser.add_argument("--rle-timeout-s", type=int, default=3600)
    args = parser.parse_args()
    if not args.dry_run and (not args.base_url or not args.model):
        parser.error("Set --base-url and --model, or GAMEGO_BASE_URL and GAMEGO_MODEL.")
    if hasattr(args, "vision_model") and not args.vision_model:
        args.vision_model = args.model
    API_RETRY_BACKOFF_MODE = args.api_retry_backoff
    if args.pipeline_workers < 1:
        raise SystemExit("--pipeline-workers must be at least 1")
    if args.item_retries < 0:
        raise SystemExit("--item-retries must not be negative")
    if not 0.0 <= args.spatial_dimension_preference_rate <= 1.0:
        raise SystemExit("--spatial-dimension-preference-rate must be between 0 and 1")

    stages = parse_stages(args.config)
    stage_ids = [x.stage_id for x in stages]
    try:
        start = stage_ids.index(args.from_stage) if args.from_stage else 0
        stop = stage_ids.index(args.to_stage) if args.to_stage else len(stages) - 1
    except ValueError as exc:
        raise SystemExit(f"Unknown stage id. Available: {', '.join(stage_ids)}") from exc
    if start > stop:
        raise SystemExit("--from-stage must not be later than --to-stage")
    api_key = os.environ.get(API_KEY_ENV, "")
    model_will_run = start <= min(stop, 2) or stop >= 2
    if model_will_run and not api_key and not args.dry_run:
        raise SystemExit(f"Missing API key: export {API_KEY_ENV}=...")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if args.webgame_jsonl:
        rows = read_jsonl(args.webgame_jsonl)
        if args.batch_limit > 0:
            rows = rows[:args.batch_limit]
        default_name = "webgame_" + safe_name(args.webgame_jsonl.stem, "batch")
        root = args.run_dir.resolve() if args.run_dir else (args.config.parent / "result" / default_name).resolve()
        wait_for_priority_takeover_gate(root)
        # WebGame Pipeline is query-only. Each item flows directly from planning
        # through Compact into the ordered JSONL; RLE is never launched here.
        if stop >= 2:
            if start <= 2:
                print("\n=== Streaming pipeline: Stage 1-3 -> Compact -> JSONL per item ===", flush=True)
                for retry_round in range(args.item_retries + 1):
                    planning = run_webgame_batch(
                        args=args, rows=rows, root=root, stages=stages,
                        start=start, stop=2, api_key=api_key, timestamp=timestamp,
                        compact_each_item=True,
                    )
                    if not planning["failed"]:
                        break
                    if retry_round < args.item_retries:
                        print(
                            f"\n=== Automatic item retry {retry_round + 1}/{args.item_retries}: "
                            f"{planning['failed']} failed item(s) ===",
                            flush=True,
                        )
                        args.resume = True
                if planning["failed"]:
                    print(f"Streaming query export failed for {planning['failed']} item(s).",
                          file=sys.stderr)
                    raise SystemExit(1)
                prepared = planning.get("prepared", 0)
            else:
                prepared_result = prepare_webgame_rle_inputs(
                    rows=rows, root=root, stages=stages, args=args, api_key=api_key,
                )
                if prepared_result["failed"]:
                    raise SystemExit(1)
                prepared = prepared_result["prepared"]
            print(f"\nWebGame Pipeline query export complete: {prepared} row(s): {root / 'rle_inputs.jsonl'}")
            return
        else:
            summary = run_webgame_batch(args=args, rows=rows, root=root, stages=stages, start=start, stop=stop,
                                        api_key=api_key, timestamp=timestamp)
        print(f"\nWeb-game batch complete: {summary['completed']} completed, {summary['skipped_completed']} skipped, {summary['failed']} failed: {root}")
        if summary["failed"]:
            raise SystemExit(1)
        return

    raw_query = args.query if args.query is not None else read_text(args.query_file)
    seed = query_seed(raw_query)
    run_dir = args.run_dir.resolve() if args.run_dir else (args.config.parent / "result" / f"run_{timestamp}").resolve()
    if stop < 2:
        run_one(args=args, seed=seed, run_dir=run_dir, stages=stages, start=start, stop=stop, api_key=api_key)
        print(f"\nWebGame Pipeline planning complete: {run_dir}")
        return
    if start <= 2:
        run_one(args=args, seed=seed, run_dir=run_dir, stages=stages, start=start, stop=2, api_key=api_key)
    stage = stages[3]
    stage_dir = run_dir / "stages" / stage.stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    previous = collect_artifacts(run_dir, stages, 3)
    rle_input, _ = prepare_rle_input(
        run_dir=run_dir, stage=stage, stage_dir=stage_dir, seed=seed,
        previous=previous, feedback="", attempt=0, args=args, api_key=api_key,
    )
    payload = json.loads(read_text(rle_input).strip())
    write_text(run_dir / "rle_inputs.jsonl", json.dumps({
        "data_id": seed.data_id, "query": str(payload["query"]),
    }, ensure_ascii=False) + "\n")
    print(f"\nWebGame Pipeline query export complete: {run_dir / 'rle_inputs.jsonl'}")


if __name__ == "__main__":
    main()
