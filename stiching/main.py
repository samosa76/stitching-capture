import os
from pathlib import Path

from PIL import Image
from  dotenv import load_dotenv

load_dotenv()

CROP_FRACTION = float(os.getenv("STITCH_CROP_FRACTION", "0.25"))

CLEAN_UP_FRAMES = os.getenv("STITCH_CLEANUP_FRAMES", "true").lower() == "true"

def _crop_center(img: Image.Image, crop_fraction: float) -> Image.Image:
    """
    Crop crop_fraction off the left edge and crop_fraction off the right
    edge, keeping the vertical (top/bottom) extent untouched.
    """

    width, height = img.size
    left = int(width * crop_fraction)
    right = int(width * (1 - crop_fraction))
    return img.crop((left, 0, right, height))

def stitch_camera_sequences(
        event_dir: Path, camera_id: str, frame_count: int, crop_fraction: float = CROP_FRACTION, cleanup: bool = CLEAN_UP_FRAMES,
) -> Path:
    """
    Loads cam{camera_id}_0001.jpg .. cam{camera_id}_{frame_count}.jpg from
    event_dir, crops crop_fraction off each side of every frame, and
    concatenates the cropped strips left-to-right (in capture order) into
    one long image saved as {camera_id}_stitched.jpg in the same folder.
 
    Frames that failed to capture (missing file, from a per-frame camera
    error) are simply skipped rather than breaking the whole stitch —
    a gap in the sequence is better than losing the rest of it.
    """
    cropped_strips = []
    frame_path_used = []

    for sequences in range(1, frame_count + 1):
        frame_path = event_dir / f"{camera_id}_{sequences:04d}.jpg"
        if not frame_path.exists():
            continue
        with Image.open(frame_path) as img:
            cropped_strips.append(_crop_center(img.copy(), crop_fraction))
        frame_path_used.append(frame_path)

    if not cropped_strips:
        return ValueError(f"No frames found for {camera_id} in {event_dir}")

    total_width = sum(strip.width for strip in cropped_strips)
    max_height = max(strip.height for strip in cropped_strips)

    stitched = Image.new("RGB", (total_width, max_height))
    x_offset = 0
    for strip in cropped_strips:
        stitched.paste(strip, (x_offset, 0))
        x_offset += strip.width

    output_path = event_dir / f"{camera_id}_stitched.jpg"
    stitched.save(output_path, quality=90)

    if cleanup:
        for frame_path in frame_path_used:
            try:
                frame_path.unlink()
            except OSError:
                pass

    return output_path