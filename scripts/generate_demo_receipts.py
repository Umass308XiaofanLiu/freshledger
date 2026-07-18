"""Generate deterministic, original receipt fixtures for FreshLedger Demo Mode.

The stores, addresses, transactions, and items below are fictional.  Every image
is visibly marked as a synthetic sample so it cannot be mistaken for purchase
evidence.  Pillow is the only runtime dependency.
"""

from __future__ import annotations

import json
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
RECEIPT_DIR = ROOT / "fixtures" / "receipts"
EXPECTED_DIR = RECEIPT_DIR / "expected"
APP_SAMPLE_DIR = ROOT / "app" / "assets" / "samples"

IMAGE_SIZE = (1080, 1720)
PAPER_WIDTH = 790
TEXT_LEFT = 62
TEXT_RIGHT = PAPER_WIDTH - 62


@dataclass(frozen=True)
class DemoReceipt:
    slug: str
    seed: int
    store_name: str
    address: str
    purchased_at: str
    display_date: str
    display_time: str
    subtotal: float
    tax: float
    total: float
    items: tuple[dict[str, Any], ...]


def storage(method: str, temp_c: float, duration_days: int) -> dict[str, Any]:
    return {
        "method": method,
        "temp_c": temp_c,
        "duration_days": duration_days,
    }


def item(
    raw_text: str,
    name: str,
    canonical_key: str | None,
    qty: float,
    unit: str,
    unit_price: float,
    line_total: float,
    category: str,
    is_perishable: bool,
    storage_plan: dict[str, Any] | None,
    eat_by: tuple[int, int] | None,
    confidence: float = 0.98,
) -> dict[str, Any]:
    return {
        "raw_text": raw_text,
        "name": name,
        "canonical_key": canonical_key,
        "qty": qty,
        "unit": unit,
        "unit_price": unit_price,
        "line_total": line_total,
        "category": category,
        "is_perishable": is_perishable,
        "storage": storage_plan,
        "eat_by_window": (
            {"start_days": eat_by[0], "end_days": eat_by[1]}
            if eat_by is not None
            else None
        ),
        "confidence": confidence,
        "needs_review": confidence < 0.85,
    }


RECEIPTS = (
    DemoReceipt(
        slug="r1",
        seed=112358,
        store_name="Fresh Basket Lab",
        address="101 Demo Avenue  |  Testville, MA 01001",
        purchased_at="2026-07-12",
        display_date="07/12/2026",
        display_time="05:42 PM",
        subtotal=23.80,
        tax=0.00,
        total=23.80,
        items=(
            item(
                "BABY SPINACH 5OZ          3.49",
                "Baby spinach",
                "baby_spinach",
                1,
                "each",
                3.49,
                3.49,
                "produce",
                True,
                storage("fridge", 4, 5),
                (2, 5),
            ),
            item(
                "WHOLE MILK 1GAL           4.29",
                "Whole milk",
                "whole_milk",
                1,
                "gallon",
                4.29,
                4.29,
                "dairy",
                True,
                storage("fridge", 4, 7),
                (5, 7),
            ),
            item(
                "CHICKEN BREAST 1.72LB     9.44",
                "Chicken breast",
                "raw_chicken_breast",
                1.72,
                "lb",
                5.49,
                9.44,
                "meat",
                True,
                storage("fridge", 4, 2),
                (1, 2),
            ),
            item(
                "SOURDOUGH LOAF            4.99",
                "Sourdough bread",
                "sourdough_bread",
                1,
                "each",
                4.99,
                4.99,
                "bakery",
                True,
                storage("pantry", 21, 4),
                (3, 4),
            ),
            item(
                "BANANAS 2.31LB            1.59",
                "Bananas",
                "banana",
                2.31,
                "lb",
                0.69,
                1.59,
                "produce",
                True,
                storage("pantry", 21, 5),
                (3, 5),
            ),
        ),
    ),
    DemoReceipt(
        slug="r2",
        seed=271828,
        store_name="Northwood Test Grocer",
        address="24 Prototype Road  |  Sampleton, MA 01002",
        purchased_at="2026-07-14",
        display_date="07/14/2026",
        display_time="09:16 AM",
        subtotal=41.41,
        tax=0.47,
        total=41.88,
        items=(
            item(
                "LARGE EGGS 1DOZ           5.19",
                "Large eggs",
                "eggs",
                1,
                "dozen",
                5.19,
                5.19,
                "dairy",
                True,
                storage("fridge", 4, 21),
                (14, 21),
            ),
            item(
                "PLAIN GREEK YOGURT 4PK    6.49",
                "Plain Greek yogurt",
                "greek_yogurt",
                1,
                "pack",
                6.49,
                6.49,
                "dairy",
                True,
                storage("fridge", 4, 10),
                (7, 10),
            ),
            item(
                "FROZEN MIXED BERRIES      8.99",
                "Frozen mixed berries",
                "frozen_mixed_berries",
                1,
                "pack",
                8.99,
                8.99,
                "frozen",
                True,
                storage("freezer", -18, 180),
                (120, 180),
            ),
            item(
                "LONG GRAIN RICE 5LB       4.79",
                "Long-grain rice",
                "dry_white_rice",
                1,
                "pack",
                4.79,
                4.79,
                "pantry_staple",
                False,
                storage("pantry", 21, 365),
                None,
            ),
            item(
                "CANNED BLACK BEANS X3     3.87",
                "Canned black beans",
                "canned_black_beans",
                3,
                "each",
                1.29,
                3.87,
                "pantry_staple",
                False,
                storage("pantry", 21, 365),
                None,
            ),
            item(
                "ORANGE JUICE 52OZ         4.59",
                "Orange juice",
                "orange_juice",
                1,
                "each",
                4.59,
                4.59,
                "beverage",
                True,
                storage("fridge", 4, 7),
                (5, 7),
            ),
            item(
                "PAPER TOWELS 6PK          7.49",
                "Paper towels",
                "paper_towels",
                1,
                "pack",
                7.49,
                7.49,
                "non_food",
                False,
                None,
                None,
            ),
        ),
    ),
    DemoReceipt(
        slug="r3",
        seed=314159,
        store_name="Cedar Lane Demo Foods",
        address="8 Sandbox Street  |  Mock Harbor, MA 01003",
        purchased_at="2026-07-16",
        display_date="07/16/2026",
        display_time="06:33 PM",
        subtotal=62.41,
        tax=0.00,
        total=62.41,
        items=(
            item(
                "SALMON FILLET 1.18LB     14.15",
                "Salmon fillet",
                "raw_salmon",
                1.18,
                "lb",
                11.99,
                14.15,
                "seafood",
                True,
                storage("fridge", 4, 2),
                (1, 2),
            ),
            item(
                "GROUND BEEF 1.35LB        8.76",
                "Ground beef",
                "raw_ground_beef",
                1.35,
                "lb",
                6.49,
                8.76,
                "meat",
                True,
                storage("fridge", 4, 2),
                (1, 2),
            ),
            item(
                "MIXED GREENS 5OZ          3.79",
                "Mixed greens",
                "mixed_greens",
                1,
                "each",
                3.79,
                3.79,
                "produce",
                True,
                storage("fridge", 4, 5),
                (2, 5),
            ),
            item(
                "AVOCADOS X3               3.75",
                "Avocados",
                "avocado",
                3,
                "each",
                1.25,
                3.75,
                "produce",
                True,
                storage("pantry", 21, 4),
                (2, 4),
            ),
            item(
                "DELI TURKEY 0.75LB        7.49",
                "Sliced deli turkey",
                "deli_turkey",
                0.75,
                "lb",
                9.99,
                7.49,
                "deli",
                True,
                storage("fridge", 4, 4),
                (3, 4),
            ),
            item(
                "CHEDDAR CHEESE 8OZ        5.99",
                "Cheddar cheese",
                "cheddar_cheese",
                1,
                "each",
                5.99,
                5.99,
                "dairy",
                True,
                storage("fridge", 4, 21),
                (14, 21),
            ),
            item(
                "SPARKLING WATER 8PK       5.49",
                "Sparkling water",
                "sparkling_water",
                1,
                "pack",
                5.49,
                5.49,
                "beverage",
                False,
                storage("pantry", 21, 180),
                None,
            ),
            item(
                "EXTRA VIRGIN OLIVE OIL   12.99",
                "Extra-virgin olive oil",
                "olive_oil",
                1,
                "each",
                12.99,
                12.99,
                "pantry_staple",
                False,
                storage("pantry", 21, 365),
                None,
            ),
        ),
    ),
)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    filename = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    windows_name = "consolab.ttf" if bold else "consola.ttf"
    candidates = (
        filename,
        str(Path("C:/Windows/Fonts") / windows_name),
        str(Path("/usr/share/fonts/truetype/dejavu") / filename),
        str(Path("/usr/share/fonts/truetype/liberation2") / "LiberationMono-Regular.ttf"),
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def centered(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.ImageFont, fill: str) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    x = (PAPER_WIDTH - (box[2] - box[0])) // 2
    draw.text((x, y), text, font=font, fill=fill)


def money_line(label: str, value: float) -> str:
    return f"{label:<25}${value:>8.2f}"


def receipt_parse(receipt: DemoReceipt) -> dict[str, Any]:
    return {
        "store_name": receipt.store_name,
        "purchased_at": receipt.purchased_at,
        "subtotal": receipt.subtotal,
        "tax": receipt.tax,
        "total": receipt.total,
        "overall_confidence": 0.98,
        "image_quality_issue": None,
        "items": list(receipt.items),
    }


def validate_fixture(receipt: DemoReceipt, payload: dict[str, Any]) -> None:
    required_root = {
        "store_name",
        "purchased_at",
        "subtotal",
        "tax",
        "total",
        "overall_confidence",
        "image_quality_issue",
        "items",
    }
    required_item = {
        "raw_text",
        "name",
        "canonical_key",
        "qty",
        "unit",
        "unit_price",
        "line_total",
        "category",
        "is_perishable",
        "storage",
        "eat_by_window",
        "confidence",
        "needs_review",
    }
    if set(payload) != required_root:
        raise ValueError(f"{receipt.slug}: invalid ReceiptParse root fields")
    if not payload["items"]:
        raise ValueError(f"{receipt.slug}: fixture must contain at least one item")
    for index, line_item in enumerate(payload["items"]):
        if set(line_item) != required_item:
            raise ValueError(f"{receipt.slug}: invalid item fields at index {index}")
        plan = line_item["storage"]
        window = line_item["eat_by_window"]
        if plan is None and window is not None:
            raise ValueError(f"{receipt.slug}: eat-by window without storage at index {index}")
        if plan is not None and not 1 <= plan["duration_days"] <= 730:
            raise ValueError(f"{receipt.slug}: unsafe duration at index {index}")
        if window is not None and window["end_days"] > plan["duration_days"]:
            raise ValueError(f"{receipt.slug}: eat-by window exceeds duration at index {index}")
    line_sum = round(sum(line_item["line_total"] for line_item in payload["items"]), 2)
    if line_sum != receipt.subtotal:
        raise ValueError(f"{receipt.slug}: line sum {line_sum:.2f} != subtotal {receipt.subtotal:.2f}")
    if round(receipt.subtotal + receipt.tax, 2) != receipt.total:
        raise ValueError(f"{receipt.slug}: subtotal + tax != total")


def draw_receipt_paper(receipt: DemoReceipt) -> Image.Image:
    rng = random.Random(receipt.seed)
    paper_height = 1060 + max(0, len(receipt.items) - 5) * 46
    paper = Image.new("RGBA", (PAPER_WIDTH, paper_height), (250, 246, 231, 255))
    draw = ImageDraw.Draw(paper, "RGBA")

    # Subtle deterministic fibers, crease lines, and slightly uneven ink mimic a
    # photographed thermal receipt without obscuring any fixture text.
    for _ in range(1150):
        x = rng.randrange(10, PAPER_WIDTH - 10)
        y = rng.randrange(8, paper_height - 8)
        shade = rng.randrange(205, 246)
        alpha = rng.randrange(10, 34)
        draw.point((x, y), fill=(shade, shade - 2, shade - 8, alpha))
    for y in (260, paper_height - 145):
        draw.line((14, y, PAPER_WIDTH - 14, y + 1), fill=(145, 138, 120, 14), width=1)
        draw.line((14, y + 3, PAPER_WIDTH - 14, y + 4), fill=(255, 255, 255, 28), width=1)

    title_font = load_font(31, bold=True)
    body_font = load_font(22)
    body_bold = load_font(22, bold=True)
    small_font = load_font(18)
    banner_font = load_font(24, bold=True)

    centered(draw, receipt.store_name.upper(), 62, title_font, "#252525")
    centered(draw, "FICTIONAL STORE / DEMO FIXTURE", 108, small_font, "#585858")
    centered(draw, receipt.address, 143, small_font, "#454545")
    draw.rounded_rectangle(
        (50, 187, PAPER_WIDTH - 50, 241),
        radius=8,
        outline=(145, 36, 36, 255),
        width=3,
        fill=(255, 236, 225, 220),
    )
    centered(draw, "SAMPLE / NOT A REAL PURCHASE", 201, banner_font, "#8D2020")

    y = 274
    draw.text((TEXT_LEFT, y), f"DATE {receipt.display_date}", font=body_font, fill="#292929")
    time_text = f"TIME {receipt.display_time}"
    time_box = draw.textbbox((0, 0), time_text, font=body_font)
    draw.text((TEXT_RIGHT - (time_box[2] - time_box[0]), y), time_text, font=body_font, fill="#292929")
    y += 44
    draw.text((TEXT_LEFT, y), f"RECEIPT DEMO-{receipt.slug.upper()}", font=body_font, fill="#292929")
    y += 47
    draw.line((TEXT_LEFT, y, TEXT_RIGHT, y), fill=(65, 65, 65, 220), width=2)
    y += 18

    for line_item in receipt.items:
        # All raw fixture text is short enough to fit the fixed-width image.
        jitter = rng.choice((-1, 0, 0, 0, 1))
        draw.text(
            (TEXT_LEFT + jitter, y),
            line_item["raw_text"],
            font=body_font,
            fill=(30, 30, 30, rng.randrange(224, 253)),
        )
        y += 43

    y += 7
    draw.line((TEXT_LEFT, y, TEXT_RIGHT, y), fill=(65, 65, 65, 220), width=2)
    y += 18
    for label, value, font in (
        ("SUBTOTAL", receipt.subtotal, body_font),
        ("TAX", receipt.tax, body_font),
        ("TOTAL", receipt.total, body_bold),
    ):
        text = money_line(label, value)
        box = draw.textbbox((0, 0), text, font=font)
        draw.text((TEXT_RIGHT - (box[2] - box[0]), y), text, font=font, fill="#202020")
        y += 43

    y += 15
    centered(draw, "DEMO TENDER — NO PAYMENT PROCESSED", y, small_font, "#444444")
    y += 48
    centered(draw, "SYNTHETIC FIXTURE FOR FRESHLEDGER", y, small_font, "#444444")
    y += 34
    centered(draw, "THANK YOU FOR TESTING FOOD-SAFE STORAGE", y, small_font, "#444444")

    # A faint diagonal watermark remains legible when the image is cropped.
    watermark = Image.new("RGBA", paper.size, (0, 0, 0, 0))
    watermark_draw = ImageDraw.Draw(watermark, "RGBA")
    watermark_font = load_font(43, bold=True)
    watermark_draw.text(
        (105, paper_height - 112),
        "SYNTHETIC SAMPLE",
        font=watermark_font,
        fill=(135, 38, 38, 25),
    )
    return Image.alpha_composite(paper, watermark)


def photograph_receipt(receipt: DemoReceipt) -> Image.Image:
    rng = random.Random(receipt.seed + 7)
    background = Image.new("RGB", IMAGE_SIZE, (72, 82, 76))
    bg_draw = ImageDraw.Draw(background, "RGBA")
    for _ in range(900):
        x = rng.randrange(IMAGE_SIZE[0])
        y = rng.randrange(IMAGE_SIZE[1])
        shade = rng.randrange(45, 105)
        bg_draw.ellipse((x, y, x + rng.randrange(1, 5), y + rng.randrange(1, 5)), fill=(shade, shade + 5, shade, 24))

    paper = draw_receipt_paper(receipt)
    angle = {"r1": -1.25, "r2": 0.85, "r3": -0.55}[receipt.slug]
    rotated = paper.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    x = (IMAGE_SIZE[0] - rotated.width) // 2 + {"r1": -10, "r2": 13, "r3": 2}[receipt.slug]
    y = (IMAGE_SIZE[1] - rotated.height) // 2

    shadow = Image.new("RGBA", IMAGE_SIZE, (0, 0, 0, 0))
    shadow_shape = Image.new("RGBA", rotated.size, (0, 0, 0, 0))
    shadow_shape.paste((0, 0, 0, 150), (0, 0, rotated.width, rotated.height), rotated)
    shadow_shape = shadow_shape.filter(ImageFilter.GaussianBlur(22))
    shadow.alpha_composite(shadow_shape, (x + 18, y + 24))
    composite = Image.alpha_composite(background.convert("RGBA"), shadow)
    composite.alpha_composite(rotated, (x, y))

    # Soft edge glare suggests a phone photo while leaving all item text clear.
    glare = Image.new("RGBA", IMAGE_SIZE, (0, 0, 0, 0))
    glare_draw = ImageDraw.Draw(glare, "RGBA")
    glare_draw.ellipse((750, -140, 1260, 440), fill=(255, 255, 248, 22))
    glare = glare.filter(ImageFilter.GaussianBlur(38))
    return Image.alpha_composite(composite, glare).convert("RGB")


def generate() -> None:
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    APP_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    for receipt in RECEIPTS:
        payload = receipt_parse(receipt)
        validate_fixture(receipt, payload)

        json_path = EXPECTED_DIR / f"{receipt.slug}.json"
        json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        image_path = RECEIPT_DIR / f"{receipt.slug}.jpg"
        image = photograph_receipt(receipt)
        image.save(image_path, "JPEG", quality=92, optimize=True, progressive=True)
        with Image.open(image_path) as saved:
            saved.verify()
        shutil.copyfile(image_path, APP_SAMPLE_DIR / image_path.name)

        print(
            f"generated {receipt.slug}: {len(receipt.items)} items, "
            f"${receipt.total:.2f}, {image.width}x{image.height}"
        )


if __name__ == "__main__":
    generate()
