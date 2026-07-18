from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from PIL import Image

from app.models import ReceiptParse
from app.services.receipt_pipeline import reconcile


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_demo_fixtures_are_strict_deterministic_and_bundled() -> None:
    for sample_id in ("r1", "r2", "r3"):
        fixture_image = REPO_ROOT / "fixtures" / "receipts" / f"{sample_id}.jpg"
        app_image = REPO_ROOT / "app" / "assets" / "samples" / f"{sample_id}.jpg"
        expected_path = (
            REPO_ROOT / "fixtures" / "receipts" / "expected" / f"{sample_id}.json"
        )

        parsed = ReceiptParse.model_validate(
            json.loads(expected_path.read_text(encoding="utf-8"))
        )
        assert parsed.purchased_at is not None
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", parsed.purchased_at)
        assert reconcile(parsed).status == "ok"
        assert parsed.items

        fixture_bytes = fixture_image.read_bytes()
        assert hashlib.sha256(fixture_bytes).digest() == hashlib.sha256(
            app_image.read_bytes()
        ).digest()
        with Image.open(fixture_image) as image:
            image.verify()
