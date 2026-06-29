"""
Grad-CAM visualizer for the proof YOLO classifier.

Shows WHERE the model looks when it picks a class: outputs a side-by-side
JPEG (left = what the model sees after preprocessing, right = heat overlay,
red = the pixels that drove the decision) labeled with class + confidence.

Usage:
    python3 tests/gradcam_viz.py <image> [<image> ...]
    # writes gradcam_<name>.jpg next to each input

Run from the repo root (needs weights/proof_best.pt).
Useful for: checking the model keys on the right UI elements (APPLIED badge,
search box) and debugging misfires — run it on any image from the heads-up /
testing channel to see what the model thought it was looking at.
"""

import os
import sys

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

from ultralytics import YOLO
from ultralytics.data.augment import classify_transforms

WEIGHTS = os.path.join("weights", "proof_best.pt")
IMGSZ   = 1536

# Falls back to "class N" past the end, so an 8-class model still works.
CLASS_NAMES = ["Following Only", "Liking Only", "Liking+Following",
               "Press search", "Creator code OK", "Zoom Out",
               "Other / Not a proof", "Scam"]


def _jet(v: np.ndarray) -> np.ndarray:
    return np.stack([np.clip(1.5 - np.abs(4 * v - 3), 0, 1),
                     np.clip(1.5 - np.abs(4 * v - 2), 0, 1),
                     np.clip(1.5 - np.abs(4 * v - 1), 0, 1)], -1)


def main(paths: list[str]):
    yolo = YOLO(WEIGHTS)
    net = yolo.model
    net.eval()
    tf = classify_transforms(size=IMGSZ)

    acts, grads, logit_store = {}, {}, {}
    # Layer 9 (C2PSA) = last spatial feature block; layer 10 head's linear
    # gives raw pre-softmax scores (the eval forward output is ALREADY
    # softmaxed — do not softmax it again).
    net.model[9].register_forward_hook(lambda m, i, o: acts.__setitem__("v", o))
    net.model[9].register_full_backward_hook(lambda m, gi, go: grads.__setitem__("v", go[0]))
    net.model[10].linear.register_forward_hook(lambda m, i, o: logit_store.__setitem__("v", o))

    try:
        font = ImageFont.load_default(size=20)
    except TypeError:
        font = ImageFont.load_default()

    for path in paths:
        im = Image.open(path).convert("RGB")
        x = tf(im).unsqueeze(0)
        x.requires_grad_(True)
        probs = net(x)
        if isinstance(probs, (list, tuple)):
            probs = probs[0]
        p = probs[0]
        cid = int(p.argmax())
        conf = float(p[cid])
        name = CLASS_NAMES[cid] if cid < len(CLASS_NAMES) else f"class {cid}"

        net.zero_grad()
        logit_store["v"][0, cid].backward()
        A, G = acts["v"][0], grads["v"][0]
        w = G.mean(dim=(1, 2), keepdim=True)
        cam = torch.relu((w * A).sum(0)).detach().numpy()
        cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)

        seen = (x[0].detach().permute(1, 2, 0).numpy() * 255).clip(0, 255).astype(np.uint8)
        H = 460
        base = Image.fromarray(seen).resize((H, H))
        cam_big = np.array(Image.fromarray((cam * 255).astype(np.uint8))
                           .resize((H, H), Image.BILINEAR)) / 255.0
        overlay = Image.blend(base, Image.fromarray((_jet(cam_big) * 255).astype(np.uint8)), 0.45)

        gap, lab = 12, 40
        canvas = Image.new("RGB", (H * 2 + gap, H + lab), (30, 31, 34))
        canvas.paste(base, (0, lab))
        canvas.paste(overlay, (H + gap, lab))
        d = ImageDraw.Draw(canvas)
        d.text((6, 8), "what the model sees", fill=(200, 200, 200), font=font)
        d.text((H + gap + 6, 8), f"where it looks -> {name} {conf * 100:.1f}%",
               fill=(255, 120, 120), font=font)

        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(os.path.dirname(path) or ".", f"gradcam_{stem}.jpg")
        canvas.save(out, "JPEG", quality=88)
        print(f"{os.path.basename(path):45s} -> {name:20s} {conf * 100:5.1f}%   saved {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
