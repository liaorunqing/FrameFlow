from pathlib import Path

from PIL import Image

from backend.app.frame_canvas import image_matches_canvas, normalize_to_canvas


def test_vertical_keyframe_is_normalized_to_exact_landscape_canvas(tmp_path: Path) -> None:
    source = tmp_path / "vertical.jpg"
    destination = tmp_path / "landscape.jpg"
    Image.new("RGB", (720, 1280), "#d6a15c").save(source)

    dimensions = normalize_to_canvas(
        source,
        destination,
        aspect_ratio="16:9",
    )

    assert dimensions == (1536, 864)
    assert image_matches_canvas(destination, "16:9")


def test_square_keyframe_is_normalized_to_exact_vertical_canvas(tmp_path: Path) -> None:
    source = tmp_path / "square.jpg"
    destination = tmp_path / "vertical.jpg"
    Image.new("RGB", (1024, 1024), "#6d8e8c").save(source)

    normalize_to_canvas(source, destination, aspect_ratio="9:16")

    assert image_matches_canvas(destination, "9:16")
