"""契約自測：example 通過 schema，三份產物的 source 一致。"""

from __future__ import annotations

import copy
import json
import re

import pytest

from src.errors import PipelineError
from src.paths import PROJECT_ROOT, SCHEMA_DIR, image_name, slugify
from src.schema import validate

KINDS = ["article", "highlights", "post"]


def _example(kind: str) -> dict:
    with (SCHEMA_DIR / "examples" / f"{kind}.example.json").open(encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("kind", KINDS)
def test_example_passes_schema(kind: str) -> None:
    validate(kind, _example(kind))


def test_source_object_is_consistent_across_stages() -> None:
    slugs = {k: _example(k)["source"]["slug"] for k in KINDS}
    assert len(set(slugs.values())) == 1, slugs


def test_paragraph_indices_are_contiguous() -> None:
    idx = [p["index"] for p in _example("article")["paragraphs"]]
    assert idx == list(range(len(idx)))


def test_body_is_paragraphs_joined() -> None:
    art = _example("article")
    assert art["body"] == "\n\n".join(p["text"] for p in art["paragraphs"])


def test_highlights_example_is_grounded() -> None:
    from src.analyze.grounding import review

    findings = review(_example("highlights"), _example("article"))
    assert findings and all(f.ok for f in findings), [f.problem for f in findings if not f.ok]


def test_post_images_match_naming_rule() -> None:
    pattern = re.compile(r"^images/\d{2}_(cover|quote|outro|point|steps|contrast)(_\d+)?\.(png|jpg)$")
    for img in _example("post")["images"]:
        assert pattern.match(img["path"]), img["path"]


def test_every_post_carries_attribution() -> None:
    post = _example("post")
    for p in post["posts"]:
        assert p["attribution"].strip()
        assert post["source"]["url"] in p["caption"], "貼文必須帶原文連結"


def test_threads_caption_within_limit() -> None:
    from src.compose.write_post import THREADS_MAX_CHARS

    threads = next(p for p in _example("post")["posts"] if p["platform"] == "threads")
    assert len(threads["caption"]) <= THREADS_MAX_CHARS


def test_image_name_helper() -> None:
    assert image_name(1, "cover") == "01_cover.png"
    assert image_name(2, "quote", 1) == "02_quote_1.png"
    with pytest.raises(ValueError):
        image_name(2, "quote")


def test_slugify_handles_ascii_and_cjk() -> None:
    assert slugify("Why Your To-Do List Never Ends") == "why-your-to-do-list-never-ends"
    cjk = slugify("為什麼你的待辦清單永遠做不完")
    assert re.match(r"^[a-z0-9][a-z0-9-]*$", cjk), cjk


def test_spec_exists() -> None:
    assert (PROJECT_ROOT / "docs" / "spec.md").exists()
