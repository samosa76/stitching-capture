import os
import time
import logging
import threading

from dotenv import load_dotenv

from adam.main import read_do2, read_do3
from camera.main import capture_all, SAVE_DIR, CAMERAS
from stiching.main import stitch_camera_sequences

load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
logger = logging.getLogger("speed_trigger")

SENSOR_DISTANCE_M = float(os.getenv("SENSOR_DISTANCE_M", "1.0"))
CAPTURE_ADVANCE_DISTANCE_M = float(os.getenv("CAPTURE_ADVANCE_DISTANCE_M", "0.5"))
POLL_INTERVAL_S = float(os.getenv("POLL_INTERVAL_S", "0.1"))
MIN_CAPTURE_INTERVAL_S = float(os.getenv("MIN_CAPTURE_INTERVAL_S", "0.05"))
 
# --- States for our simple state machine ---
WAITING_DO0 = "WAITING_DO0"
WAITING_DO1 = "WAITING_DO1"
CAPTURING = "CAPTURING"

def _capture_and_log(event_id: str, sequence: int):
    """
    Runs capture_all() for one frame and logs the per-camera result.
    Meant to be launched on its own background thread — this is what
    keeps the main polling loop from freezing while the camera network
    requests are in flight.
    """
    result = capture_all(event_id=event_id, sequence=sequence)
    for camera_id, info in result["results"].items():
        if info["status"] == "ok":
            logger.info(f"  frame {sequence} — {camera_id}: OK")
        else:
            logger.error(f"  frame {sequence} — {camera_id}: FAILED -> {info['error']}")

def _stitch_and_log(event_id: str, frame_count: int, capture_threads: list):
    """
    Waits for every capture thread from this pass to actually finish
    writing its file (the last frame or two may still be mid-flight when
    DO1 clears), then stitches each camera's sequence into one long image.
    """
    for t in capture_threads:
        t.join()

    event_dir = SAVE_DIR / event_id
    for camera_id in CAMERAS:
        try:
            output_path = stitch_camera_sequences(event_dir, camera_id, frame_count)
            logger.info(f"Stitched {camera_id}: {output_path}")
        except Exception as e:
            logger.error(f"Failed to stitch {camera_id}: {e}")



def main():
    state = WAITING_DO0
    t1 = None
    next_capture_time = None
    frame_count = 0
    capture_threads = []
    last_do0, last_do1 = None, None
 
    # Check what the hardware is ACTUALLY reporting before we start
    # interpreting anything — this is exactly the check that would have
    # caught "DO0/DO1 were already left ON from earlier testing" instantly.
    try:
        start_do0 = read_do2()
        start_do1 = read_do3()
    except Exception as e:
        logger.error(f"Could not read initial DO states — check connection: {e}")
        return
 
    logger.info(f"Initial state — DO0={int(start_do0)}  DO1={int(start_do1)}")
    if start_do0 or start_do1:
        logger.warning(
            "One or both are already ON at startup. If you didn't mean to "
            "simulate a truck already mid-pass, reset them to 0 first, then "
            "restart this script for a clean test."
        )
 
    logger.info("Watching DO0/DO1 — waiting for a truck (DO0) to trigger...")
 
    while True:
        try:
            do0 = read_do2()
            do1 = read_do3()
        except Exception as e:
            # A Modbus hiccup shouldn't kill the whole script — log it,
            # skip this tick, and try again on the next poll.
            logger.error(f"Read failed, will retry next poll: {e}")
            time.sleep(POLL_INTERVAL_S)
            continue
 
        # Raw edge logging, independent of the state machine — this shows
        # you exactly what the script sees the instant either bit flips,
        # regardless of which state we're currently interpreting it in.
        if do0 != last_do0 or do1 != last_do1:
            logger.info(f"  [raw] DO0={int(do0)}  DO1={int(do1)}")
            last_do0, last_do1 = do0, do1
 
        if state == WAITING_DO0:
            if do0:
                t1 = time.monotonic()
                state = WAITING_DO1
                logger.info("DO0 triggered — waiting for DO1...")
 
        elif state == WAITING_DO1:
            if do1:
                t2 = time.monotonic()
                delta_t = t2 - t1
 
                if delta_t <= 0:
                    # Shouldn't normally happen, but guards against a
                    # divide-by-zero if both fired on the same poll tick.
                    logger.error("DO1 fired with no measurable time gap — resetting.")
                    state = WAITING_DO0
                    continue
 
                speed = SENSOR_DISTANCE_M / delta_t  # meters per second
                capture_interval = max(
                    CAPTURE_ADVANCE_DISTANCE_M / speed, MIN_CAPTURE_INTERVAL_S
                )
 
                logger.info(
                    f"Speed calculated: {speed:.2f} m/s "
                    f"({speed * 3.6:.1f} km/h) -> "
                    f"capture interval: {capture_interval:.2f}s"
                )
 
                frame_count = 0
                capture_threads = []
                event_id = time.strftime("%Y%m%d_%H%M%S")
                logger.info(f"Saving this pass to event: {event_id}")
                next_capture_time = time.monotonic()
                state = CAPTURING
 
        elif state == CAPTURING:
            if not do1:
                # DO1 has dropped back to 0 — vehicle has fully cleared
                # that point, so we're done capturing this pass. Kick off
                # stitching in the background (it'll wait for any
                # still-running capture threads itself) and go back to
                # watching for the next truck immediately.
                logger.info(f"DO1 cleared — stopping. Total frames: {frame_count}")
                threading.Thread(
                    target=_stitch_and_log,
                    args=(event_id, frame_count, capture_threads),
                    daemon=True,
                ).start()
                state = WAITING_DO0
                t1 = None
                continue
 
            now = time.monotonic()
            if now >= next_capture_time:
                frame_count += 1
                logger.info(f"capturing... (frame {frame_count})")
                # Run the actual camera capture on its own thread so this
                # loop keeps polling DO0/DO1 promptly instead of freezing
                # for however long the camera network requests take.
                t = threading.Thread(
                    target=_capture_and_log,
                    args=(event_id, frame_count),
                    daemon=True,
                )
                t.start()
                capture_threads.append(t)
                # Scheduling the NEXT capture off the previous target time
                # (not off "now") avoids the interval slowly drifting
                # later due to small delays elsewhere in the loop.
                next_capture_time += capture_interval
 
        time.sleep(POLL_INTERVAL_S)

if __name__ == "__main__":
    main()