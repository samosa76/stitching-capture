import os
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv

load_dotenv()

# --- Camera settings (loaded from .env) ---
CAMERA_USER = os.getenv("CAMERA_USER", "admin")
CAMERA_PASS = os.getenv("CAMERA_PASS", "")
CAMERA_CHANNEL = os.getenv("CAMERA_CHANNEL", "101")
SAVE_DIR = Path(os.getenv("SAVE_DIR", "./captures"))

# Map friendly camera_id -> IP address
CAMERAS = {
    "cam1": os.getenv("CAMERA_1_IP", "192.168.18.64"),
    # "cam2": os.getenv("CAMERA_2_IP", "192.168.18.128"),
}

SAVE_DIR.mkdir(parents=True, exist_ok=True)


class CameraNotFoundError(Exception):
    """Raised when camera_id is not in the CAMERAS map."""


class CameraCaptureError(Exception):
    """Raised when the ISAPI request to the camera fails."""


def get_camera_ip(camera_id: str) -> str:
    ip = CAMERAS.get(camera_id)
    if not ip:
        raise CameraNotFoundError(f"Unknown camera id: {camera_id}")
    return ip


def build_picture_url(ip: str, channel: str = CAMERA_CHANNEL) -> str:
    # Standard Hikvision ISAPI snapshot endpoint
    return f"http://{ip}/ISAPI/Streaming/channels/{channel}/picture"


def _fetch_snapshot(ip: str) -> bytes:
    """
    Does the actual ISAPI GET, applies the dewarp correction (a no-op
    passthrough until DEWARP_ENABLED=true is set — see imaging/dewarp.py),
    and returns the resulting JPEG bytes.
    Raises CameraCaptureError on any network/HTTP failure.
    Shared by both capture_image() and capture_all().
    """
    url = build_picture_url(ip)
    try:
        response = requests.get(
            url,
            auth=HTTPDigestAuth(CAMERA_USER, CAMERA_PASS),
            timeout=5,
        )
        response.raise_for_status()
        return response.content
    except requests.exceptions.RequestException as e:
        # Covers: connection refused (camera off), timeout, DNS/host
        # unreachable, bad HTTP status from raise_for_status(), etc.
        raise CameraCaptureError(f"Failed to capture from {ip}: {e}")


def capture_image(camera_id: str) -> Path:
    """
    Capture a snapshot from a single camera and save it into the default
    SAVE_DIR. Used by the Postman/HTTP single-camera trigger.
    """
    ip = get_camera_ip(camera_id)
    data = _fetch_snapshot(ip)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{camera_id}_{timestamp}.jpg"
    filepath = SAVE_DIR / filename

    try:
        with open(filepath, "wb") as f:
            f.write(data)
    except OSError as e:
        # Covers: disk full, permissions issue, bad SAVE_DIR path, etc.
        raise CameraCaptureError(f"Failed to save image from {camera_id}: {e}")

    return filepath


def _capture_one(camera_id: str, ip: str, event_dir: Path, sequence: int = None) -> tuple:
    """
    Capture and save a single camera's snapshot. Returns (camera_id, info)
    instead of raising, so it's safe to run inside a worker thread without
    needing extra exception-passing machinery.

    If sequence is given, the filename includes it (e.g. cam1_0007.jpg)
    so repeated calls into the same event_dir don't overwrite each other
    — needed for a burst of frames during one truck pass, as opposed to
    the single-shot case where each event gets its own folder anyway.
    """
    try:
        data = _fetch_snapshot(ip)
        if sequence is not None:
            filename = f"{camera_id}_{sequence:04d}.jpg"
        else:
            filename = f"{camera_id}.jpg"
        filepath = event_dir / filename
        with open(filepath, "wb") as f:
            f.write(data)
        return camera_id, {"status": "ok", "file": str(filepath)}
    except (CameraCaptureError, OSError) as e:
        return camera_id, {"status": "error", "error": str(e)}


def capture_all(event_id: str = None, sequence: int = None) -> dict:
    """
    Capture from every camera in CAMERAS at the same time and save them
    together into one new subfolder under SAVE_DIR:
    capture/<folder>/cam1.jpg, cam2.jpg, ... (or cam1_0001.jpg, etc. if
    sequence is given).

    event_id, if given, is used as the folder name (e.g. a gate/container
    event id). If not given, a timestamp is used instead. Pass the SAME
    event_id across multiple calls (with an increasing sequence number)
    to accumulate a burst of frames from one truck pass into one folder,
    instead of each call creating a brand new folder.

    Each camera's request runs concurrently on its own thread (this is
    I/O-bound work — mostly waiting on the network — so threads are fine
    here even with Python's GIL). This matters because the container is
    moving: capturing cam1 and cam2 one after another could catch it in
    two different positions, while firing both requests together keeps
    the two shots close in time.

    Each camera is still independent — if cam1 fails, cam2 is still
    captured and saved. Nothing here raises; failures are reported back
    per-camera in the returned dict instead.
    """
    folder_name = event_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    event_dir = SAVE_DIR / folder_name
    event_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    with ThreadPoolExecutor(max_workers=len(CAMERAS)) as executor:
        futures = [
            executor.submit(_capture_one, camera_id, ip, event_dir, sequence)
            for camera_id, ip in CAMERAS.items()
        ]
        for future in as_completed(futures):
            camera_id, info = future.result()
            results[camera_id] = info

    return {"folder": str(event_dir), "results": results}