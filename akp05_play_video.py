"""
Play a video on the AKP05's touch strip (800x112), frame by frame.

Set expectations before you try this: the strip has no partial-update
capability (see akp05_device.py's docstring -- the only write is a full
800x112 replace), so every frame is a complete re-upload: a scoped wake
sequence, the JPEG chunked into ~1024-byte packets, then a commit, each
packet paced by send_commands()'s existing 0.05s delay. For a typical
compressed frame that's on the order of a second, not a video frame
interval -- real-time 24-30fps is not achievable here. This decodes at a
much lower target rate instead (--fps, default 4) by sampling the source
video, and prints the actually-achieved rate once running so you see
real numbers rather than a silent slow-motion surprise.

Setup (not needed for anything else in this repo, video-specific):
    pip install imageio imageio-ffmpeg
imageio-ffmpeg bundles a static ffmpeg binary for your platform
(downloaded once on first use, like akp05_icons.py's MDI font) --
no separate ffmpeg install needed.

Usage:
    python akp05_play_video.py video.mp4
    python akp05_play_video.py video.mp4 --fps 2 --loop
    python akp05_play_video.py video.mp4 --mode fill
    python akp05_play_video.py video.mp4 --mode fit --background 20,20,40
"""

import argparse
import sys
import time

from PIL import Image

from akp05_device import (
    STRIP_IMAGE_SIZE,
    STRIP_WIRE_KEY,
    encode_image,
    open_device,
    save_strip_canvas,
    upload_image,
)

try:
    import imageio.v2 as imageio
except ImportError:
    print("Needs imageio + imageio-ffmpeg: pip install imageio imageio-ffmpeg")
    sys.exit(1)


def fit_frame(frame_rgb, mode: str, background: tuple[int, int, int]) -> Image.Image:
    """frame_rgb: an HxWx3 uint8 numpy array (imageio's native frame
    format). Returns a PIL RGB Image sized exactly STRIP_IMAGE_SIZE --
    reconciling whatever aspect ratio the source video has with the
    strip's very wide, short one (800x112, ~7:1) is the whole problem
    here; a straight resize would badly distort almost any real video."""
    src = Image.fromarray(frame_rgb)
    target_w, target_h = STRIP_IMAGE_SIZE

    if mode == "stretch":
        return src.resize((target_w, target_h), Image.LANCZOS)

    src_w, src_h = src.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if mode == "fill":
        if src_ratio > target_ratio:
            new_h = target_h
            new_w = max(1, round(new_h * src_ratio))
        else:
            new_w = target_w
            new_h = max(1, round(new_w / src_ratio))
        resized = src.resize((new_w, new_h), Image.LANCZOS)
        x = (new_w - target_w) // 2
        y = (new_h - target_h) // 2
        return resized.crop((x, y, x + target_w, y + target_h))

    # "fit" (default): letterbox -- whole frame visible, padded to fill
    if src_ratio > target_ratio:
        new_w = target_w
        new_h = max(1, round(new_w / src_ratio))
    else:
        new_h = target_h
        new_w = max(1, round(new_h * src_ratio))
    resized = src.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), background)
    canvas.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return canvas


def parse_color(text: str) -> tuple[int, int, int]:
    parts = [int(p) for p in text.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected R,G,B e.g. 20,20,40")
    return tuple(parts)


def main():
    parser = argparse.ArgumentParser(description="Play a video on the AKP05 touch strip.")
    parser.add_argument("video", help="path to a video file (anything ffmpeg can decode)")
    parser.add_argument(
        "--fps",
        type=float,
        default=4.0,
        help="target sampling rate from the source video (default: 4). The achieved upload "
        "rate is usually lower than this -- see the module docstring -- this just controls "
        "how much of the source is skipped between uploaded frames.",
    )
    parser.add_argument(
        "--mode",
        choices=["fit", "fill", "stretch"],
        default="fit",
        help="how to fit the source's aspect ratio into 800x112: fit = whole frame visible, "
        "letterboxed (default); fill = fills the strip, crops overflow; stretch = distorts to fill exactly",
    )
    parser.add_argument(
        "--background",
        type=parse_color,
        default=(0, 0, 0),
        help="letterbox background as R,G,B for --mode fit (default: 0,0,0 black)",
    )
    parser.add_argument("--loop", action="store_true", help="repeat until Ctrl+C")
    args = parser.parse_args()

    try:
        reader = imageio.get_reader(args.video)
    except Exception as exc:
        print(f"Couldn't open {args.video}: {exc}")
        sys.exit(1)

    meta = reader.get_meta_data()
    source_fps = meta.get("fps") or 30.0
    frame_step = max(1, round(source_fps / args.fps))
    print(f"Source: {source_fps:.1f}fps, size {meta.get('size')} -- sampling every {frame_step} frame(s) for a ~{args.fps:.1f}fps target")
    print("Actual achieved rate will likely be lower -- see the module docstring for why. Ctrl+C to stop.")

    device = open_device()
    try:
        while True:
            frame_index = 0
            uploaded = 0
            t_start = time.monotonic()
            for frame_rgb in reader:
                if frame_index % frame_step == 0:
                    img = fit_frame(frame_rgb, args.mode, args.background)
                    save_strip_canvas(img)
                    jpeg_bytes = encode_image(img, STRIP_IMAGE_SIZE)
                    upload_image(device, STRIP_WIRE_KEY, jpeg_bytes)
                    uploaded += 1
                    if uploaded % 10 == 0:
                        elapsed = time.monotonic() - t_start
                        print(f"  {uploaded} frames uploaded, {uploaded / elapsed:.2f} actual fps")
                frame_index += 1
            if not args.loop:
                break
            reader = imageio.get_reader(args.video)  # restart from the beginning
        print("Done.")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        reader.close()
        device.close()


if __name__ == "__main__":
    main()
