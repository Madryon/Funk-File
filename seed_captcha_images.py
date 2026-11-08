"""
seed_captcha_images.py
Generates a bank of royalty-free, CC0-licensed 300x300 JPEG images across 8 target
categories plus a negative/distractor category for the CAPTCHA system.
All images are stripped of EXIF metadata.
"""

import os
import random
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

BASE_DIR = Path(__file__).resolve().parent
IMAGE_BANK_DIR = BASE_DIR / "captcha" / "images"

CATEGORIES = [
    "cat",
    "dog",
    "car",
    "bicycle",
    "traffic_light",
    "airplane",
    "boat",
    "tree",
    "negative",
]

COUNT_PER_CATEGORY = 45
IMAGE_SIZE = (300, 300)


def draw_cat(draw, rng):
    # Background
    bg_types = ["indoor", "grass", "rug", "wood"]
    bg = rng.choice(bg_types)
    if bg == "indoor":
        draw.rectangle([0, 0, 300, 300], fill=(rng.randint(220, 245), rng.randint(215, 235), rng.randint(200, 225)))
        draw.rectangle([0, 200, 300, 300], fill=(rng.randint(180, 205), rng.randint(150, 180), rng.randint(120, 150)))
    elif bg == "grass":
        draw.rectangle([0, 0, 300, 120], fill=(135 + rng.randint(-15, 15), 206 + rng.randint(-15, 15), 235))
        draw.rectangle([0, 120, 300, 300], fill=(60 + rng.randint(-10, 20), 150 + rng.randint(-20, 20), 60 + rng.randint(-10, 15)))
    elif bg == "rug":
        draw.rectangle([0, 0, 300, 300], fill=(rng.randint(160, 190), rng.randint(60, 90), rng.randint(70, 100)))
        # Pattern lines
        for y in range(0, 300, 30):
            draw.line([(0, y), (300, y)], fill=(rng.randint(200, 220), rng.randint(180, 200), rng.randint(140, 160)), width=2)
    else:
        # Wood floor
        for y in range(0, 300, 25):
            draw.rectangle([0, y, 300, y + 23], fill=(190 + rng.randint(-10, 10), 140 + rng.randint(-10, 10), 95 + rng.randint(-10, 10)))

    # Cat color
    cat_colors = [
        ((230, 130, 40), (255, 180, 90)),   # Orange tabby
        ((40, 40, 45), (70, 70, 75)),       # Black
        ((235, 235, 240), (200, 200, 210)), # White
        ((120, 120, 130), (160, 160, 170)), # Gray
        ((110, 70, 45), (160, 110, 80)),    # Brown
    ]
    body_col, highlight_col = rng.choice(cat_colors)

    cx = rng.randint(120, 180)
    cy = rng.randint(160, 200)
    scale = rng.uniform(0.85, 1.15)

    # Cat body
    bw, bh = int(60 * scale), int(75 * scale)
    draw.ellipse([cx - bw, cy - bh, cx + bw, cy + bh], fill=body_col)

    # Cat tail
    tail_pts = [
        (cx + int(45 * scale), cy + int(40 * scale)),
        (cx + int(80 * scale), cy + int(20 * scale)),
        (cx + int(95 * scale), cy - int(20 * scale)),
        (cx + int(85 * scale), cy - int(40 * scale))
    ]
    draw.line(tail_pts, fill=body_col, width=int(12 * scale), joint="curve")

    # Head
    hx, hy = cx - int(10 * scale), cy - int(75 * scale)
    hr = int(38 * scale)
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=body_col)

    # Ears (Triangles)
    ear_size = int(22 * scale)
    left_ear = [(hx - int(30 * scale), hy - int(15 * scale)),
                (hx - int(28 * scale), hy - int(50 * scale)),
                (hx - int(8 * scale), hy - int(32 * scale))]
    right_ear = [(hx + int(8 * scale), hy - int(32 * scale)),
                 (hx + int(28 * scale), hy - int(50 * scale)),
                 (hx + int(30 * scale), hy - int(15 * scale))]
    draw.polygon(left_ear, fill=body_col)
    draw.polygon(right_ear, fill=body_col)
    # Inner ears (pinkish)
    draw.polygon([(p[0] * 0.9 + hx * 0.1, p[1] * 0.9 + hy * 0.1) for p in left_ear], fill=(250, 180, 190))
    draw.polygon([(p[0] * 0.9 + hx * 0.1, p[1] * 0.9 + hy * 0.1) for p in right_ear], fill=(250, 180, 190))

    # Eyes
    eye_y = hy - int(4 * scale)
    eye_x1 = hx - int(14 * scale)
    eye_x2 = hx + int(14 * scale)
    er = int(6 * scale)
    eye_color = rng.choice([(60, 180, 75), (230, 190, 50), (60, 140, 220)])
    draw.ellipse([eye_x1 - er, eye_y - er, eye_x1 + er, eye_y + er], fill=eye_color)
    draw.ellipse([eye_x2 - er, eye_y - er, eye_x2 + er, eye_y + er], fill=eye_color)
    # Pupils
    draw.ellipse([eye_x1 - 1, eye_y - er + 1, eye_x1 + 1, eye_y + er - 1], fill=(10, 10, 10))
    draw.ellipse([eye_x2 - 1, eye_y - er + 1, eye_x2 + 1, eye_y + er - 1], fill=(10, 10, 10))

    # Nose & Muzzle
    draw.polygon([(hx - 4, hy + int(8 * scale)), (hx + 4, hy + int(8 * scale)), (hx, hy + int(13 * scale))], fill=(240, 150, 160))

    # Whiskers
    for dy in [-2, 2]:
        draw.line([(hx - 10, hy + 12 + dy), (hx - int(40 * scale), hy + 10 + dy * 3)], fill=(220, 220, 220), width=1)
        draw.line([(hx + 10, hy + 12 + dy), (hx + int(40 * scale), hy + 10 + dy * 3)], fill=(220, 220, 220), width=1)


def draw_dog(draw, rng):
    # Background: lawn, park or indoor
    draw.rectangle([0, 0, 300, 130], fill=(140 + rng.randint(-15, 15), 195 + rng.randint(-15, 15), 235))
    draw.rectangle([0, 130, 300, 300], fill=(70 + rng.randint(-15, 15), 160 + rng.randint(-20, 20), 75 + rng.randint(-15, 15)))

    dog_colors = [
        ((195, 140, 80), (220, 175, 120)),   # Golden / beige
        ((110, 65, 35), (150, 95, 55)),      # Chocolate brown
        ((240, 240, 245), (40, 40, 45)),     # White with spots
        ((35, 35, 40), (80, 80, 85)),        # Black
    ]
    body_col, sec_col = rng.choice(dog_colors)

    cx = rng.randint(120, 170)
    cy = rng.randint(165, 195)
    scale = rng.uniform(0.85, 1.15)

    # Dog body (longer than cat)
    bw, bh = int(75 * scale), int(55 * scale)
    draw.ellipse([cx - bw, cy - bh, cx + bw, cy + bh], fill=body_col)

    # Legs
    for lx in [-bw + 15, -bw + 40, bw - 40, bw - 15]:
        draw.rectangle([cx + lx - 8, cy + 20, cx + lx + 8, cy + 80 * scale], fill=body_col)

    # Tail (wagging upward)
    draw.arc([cx - bw - 20, cy - 40, cx - bw + 25, cy + 20], start=120, end=270, fill=body_col, width=int(12 * scale))

    # Head and snout
    hx = cx + int(50 * scale)
    hy = cy - int(45 * scale)
    hr = int(32 * scale)
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=body_col)

    # Snout
    snout_w, snout_h = int(24 * scale), int(16 * scale)
    draw.ellipse([hx + int(12 * scale), hy - int(2 * scale), hx + int(12 * scale) + snout_w, hy + snout_h], fill=sec_col)
    # Nose
    draw.ellipse([hx + int(18 * scale) + snout_w - 10, hy + 2, hx + int(18 * scale) + snout_w, hy + 12], fill=(20, 20, 20))

    # Floppy ears
    draw.ellipse([hx - int(22 * scale), hy - int(18 * scale), hx - int(4 * scale), hy + int(24 * scale)], fill=sec_col)
    draw.ellipse([hx + int(6 * scale), hy - int(22 * scale), hx + int(22 * scale), hy + int(14 * scale)], fill=sec_col)

    # Eye
    draw.ellipse([hx + int(6 * scale), hy - int(10 * scale), hx + int(14 * scale), hy - int(2 * scale)], fill=(30, 20, 10))
    # Collar
    collar_col = rng.choice([(220, 40, 40), (40, 90, 220), (220, 170, 30)])
    draw.rectangle([hx - int(22 * scale), hy + int(18 * scale), hx + int(15 * scale), hy + int(26 * scale)], fill=collar_col)


def draw_car(draw, rng):
    # Road scene background
    sky_h = rng.randint(90, 130)
    draw.rectangle([0, 0, 300, sky_h], fill=(130 + rng.randint(-20, 20), 180 + rng.randint(-20, 20), 235))
    # Road
    draw.rectangle([0, sky_h, 300, 300], fill=(55, 60, 68))
    # Lane stripes
    for x in range(-20, 320, 50):
        draw.rectangle([x, 240, x + 30, 246], fill=(235, 215, 60))

    car_colors = [
        (220, 40, 45),   # Red
        (40, 110, 220),  # Blue
        (240, 200, 30),  # Yellow
        (215, 220, 225), # Silver
        (35, 35, 40),    # Black
        (40, 160, 75),   # Green
    ]
    body_col = rng.choice(car_colors)

    cx = rng.randint(130, 170)
    cy = rng.randint(170, 200)
    scale = rng.uniform(0.85, 1.15)

    # Main chassis lower rectangle
    cw, ch = int(105 * scale), int(32 * scale)
    draw.rounded_rectangle([cx - cw, cy, cx + cw, cy + ch], radius=8, fill=body_col)

    # Cabin roof (trapezoid-like polygon)
    rw, rh = int(65 * scale), int(34 * scale)
    cabin_pts = [
        (cx - rw - 15, cy),
        (cx - rw + 15, cy - rh),
        (cx + rw - 15, cy - rh),
        (cx + rw + 15, cy)
    ]
    draw.polygon(cabin_pts, fill=body_col)

    # Windows (light blue/reflective)
    win_pts = [
        (cx - rw - 10, cy - 2),
        (cx - rw + 17, cy - rh + 4),
        (cx + rw - 17, cy - rh + 4),
        (cx + rw + 10, cy - 2)
    ]
    draw.polygon(win_pts, fill=(180, 220, 245))
    # Center window pillar
    draw.line([(cx, cy - rh + 4), (cx, cy - 2)], fill=body_col, width=4)

    # Headlights & Taillights
    draw.rectangle([cx + cw - 8, cy + 6, cx + cw, cy + 18], fill=(255, 245, 120))
    draw.rectangle([cx - cw, cy + 6, cx - cw + 8, cy + 18], fill=(240, 30, 30))

    # Wheels
    wheel_y = cy + ch - 4
    for wx in [cx - int(60 * scale), cx + int(60 * scale)]:
        r = int(18 * scale)
        draw.ellipse([wx - r, wheel_y - r, wx + r, wheel_y + r], fill=(20, 20, 25))
        draw.ellipse([wx - r + 6, wheel_y - r + 6, wx + r - 6, wheel_y + r - 6], fill=(180, 185, 190))


def draw_bicycle(draw, rng):
    # Outdoor ground scene
    draw.rectangle([0, 0, 300, 140], fill=(160 + rng.randint(-15, 15), 205 + rng.randint(-15, 15), 235))
    draw.rectangle([0, 140, 300, 300], fill=(110 + rng.randint(-15, 15), 115 + rng.randint(-15, 15), 125))

    frame_colors = [(225, 45, 45), (45, 125, 235), (45, 195, 85), (245, 180, 30), (180, 45, 220)]
    fcol = rng.choice(frame_colors)

    cx, cy = rng.randint(140, 160), rng.randint(185, 205)
    scale = rng.uniform(0.85, 1.1)

    r_wheel = int(38 * scale)
    dist = int(75 * scale)
    w1 = (cx - dist, cy)
    w2 = (cx + dist, cy)

    # Wheels & spokes
    for w in [w1, w2]:
        draw.ellipse([w[0] - r_wheel, w[1] - r_wheel, w[0] + r_wheel, w[1] + r_wheel], outline=(30, 30, 35), width=5)
        # Spokes
        for a in range(0, 360, 45):
            rad = math.radians(a)
            draw.line([w, (w[0] + int(r_wheel * math.cos(rad)), w[1] + int(r_wheel * math.sin(rad)))], fill=(180, 180, 185), width=1)

    # Frame diamond
    bb = (cx - int(5 * scale), cy)       # Bottom bracket
    seat_post = (cx - int(25 * scale), cy - int(55 * scale))
    head_tube = (cx + int(45 * scale), cy - int(65 * scale))

    draw.line([w1, bb], fill=fcol, width=4)
    draw.line([bb, seat_post], fill=fcol, width=4)
    draw.line([w1, seat_post], fill=fcol, width=4)
    draw.line([seat_post, head_tube], fill=fcol, width=4)
    draw.line([bb, head_tube], fill=fcol, width=4)
    draw.line([head_tube, w2], fill=fcol, width=4)

    # Handlebars
    draw.line([head_tube, (head_tube[0] - int(10 * scale), head_tube[1] - int(15 * scale))], fill=(30, 30, 30), width=4)
    draw.line([(head_tube[0] - int(18 * scale), head_tube[1] - int(15 * scale)),
               (head_tube[0] + int(5 * scale), head_tube[1] - int(15 * scale))], fill=(30, 30, 30), width=5)

    # Saddle
    draw.rectangle([seat_post[0] - 14, seat_post[1] - 8, seat_post[0] + 14, seat_post[1] - 2], fill=(20, 20, 20))


def draw_traffic_light(draw, rng):
    # Sky background
    sky_colors = [
        ((120, 175, 235), (200, 225, 250)),
        ((40, 55, 95), (90, 80, 130)), # Evening
        ((225, 140, 80), (245, 200, 140)) # Sunset
    ]
    top_c, bot_c = rng.choice(sky_colors)
    for y in range(300):
        t = y / 300.0
        r = int(top_c[0] * (1 - t) + bot_c[0] * t)
        g = int(top_c[1] * (1 - t) + bot_c[1] * t)
        b = int(top_c[2] * (1 - t) + bot_c[2] * t)
        draw.line([(0, y), (300, y)], fill=(r, g, b))

    cx = rng.randint(130, 170)
    cy = rng.randint(120, 150)
    scale = rng.uniform(0.85, 1.15)

    # Pole
    draw.rectangle([cx - int(7 * scale), cy + int(70 * scale), cx + int(7 * scale), 300], fill=(60, 65, 70))

    # Housing
    hw, hh = int(38 * scale), int(95 * scale)
    draw.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh], radius=10, fill=(25, 28, 32), outline=(15, 15, 18), width=3)

    # 3 lights: Red, Yellow, Green
    lamp_r = int(22 * scale)
    light_ys = [cy - int(58 * scale), cy, cy + int(58 * scale)]

    # Choose one to illuminate brightly
    active_idx = rng.randint(0, 2)

    # Red
    if active_idx == 0:
        draw.ellipse([cx - lamp_r - 8, light_ys[0] - lamp_r - 8, cx + lamp_r + 8, light_ys[0] + lamp_r + 8], fill=(255, 80, 80, 100))
        draw.ellipse([cx - lamp_r, light_ys[0] - lamp_r, cx + lamp_r, light_ys[0] + lamp_r], fill=(255, 40, 40))
        draw.ellipse([cx - lamp_r + 5, light_ys[0] - lamp_r + 5, cx + lamp_r - 5, light_ys[0] + lamp_r - 5], fill=(255, 140, 140))
    else:
        draw.ellipse([cx - lamp_r, light_ys[0] - lamp_r, cx + lamp_r, light_ys[0] + lamp_r], fill=(80, 20, 20))

    # Yellow
    if active_idx == 1:
        draw.ellipse([cx - lamp_r - 8, light_ys[1] - lamp_r - 8, cx + lamp_r + 8, light_ys[1] + lamp_r + 8], fill=(255, 230, 80, 100))
        draw.ellipse([cx - lamp_r, light_ys[1] - lamp_r, cx + lamp_r, light_ys[1] + lamp_r], fill=(255, 210, 30))
        draw.ellipse([cx - lamp_r + 5, light_ys[1] - lamp_r + 5, cx + lamp_r - 5, light_ys[1] + lamp_r - 5], fill=(255, 245, 160))
    else:
        draw.ellipse([cx - lamp_r, light_ys[1] - lamp_r, cx + lamp_r, light_ys[1] + lamp_r], fill=(80, 70, 15))

    # Green
    if active_idx == 2:
        draw.ellipse([cx - lamp_r - 8, light_ys[2] - lamp_r - 8, cx + lamp_r + 8, light_ys[2] + lamp_r + 8], fill=(80, 255, 140, 100))
        draw.ellipse([cx - lamp_r, light_ys[2] - lamp_r, cx + lamp_r, light_ys[2] + lamp_r], fill=(35, 230, 95))
        draw.ellipse([cx - lamp_r + 5, light_ys[2] - lamp_r + 5, cx + lamp_r - 5, light_ys[2] + lamp_r - 5], fill=(160, 255, 195))
    else:
        draw.ellipse([cx - lamp_r, light_ys[2] - lamp_r, cx + lamp_r, light_ys[2] + lamp_r], fill=(15, 75, 30))


def draw_airplane(draw, rng):
    # Blue sky with clouds
    draw.rectangle([0, 0, 300, 300], fill=(110 + rng.randint(-15, 15), 175 + rng.randint(-15, 15), 235))
    # Puffy clouds
    for _ in range(rng.randint(3, 6)):
        clx, cly = rng.randint(20, 280), rng.randint(30, 260)
        cr = rng.randint(25, 45)
        draw.ellipse([clx - cr, cly - cr // 2, clx + cr, cly + cr // 2], fill=(245, 248, 255))
        draw.ellipse([clx - cr // 2, cly - cr, clx + cr // 2, cly + cr // 4], fill=(245, 248, 255))

    cx = rng.randint(140, 160)
    cy = rng.randint(140, 160)
    scale = rng.uniform(0.85, 1.15)
    angle = rng.uniform(-25, 25)

    plane_img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(plane_img)

    pcx, pcy = 150, 150
    # Main fuselage
    fw, fh = int(22 * scale), int(115 * scale)
    pdraw.ellipse([pcx - fw, pcy - fh, pcx + fw, pcy + fh], fill=(240, 242, 248), outline=(180, 185, 195), width=2)

    # Main Wings
    wing_span = int(120 * scale)
    wing_pts = [
        (pcx - wing_span, pcy + int(20 * scale)),
        (pcx, pcy - int(25 * scale)),
        (pcx + wing_span, pcy + int(20 * scale)),
        (pcx, pcy + int(10 * scale))
    ]
    pdraw.polygon(wing_pts, fill=(215, 220, 230), outline=(170, 175, 185))

    # Tail fin & stabilizers
    tail_span = int(48 * scale)
    tail_pts = [
        (pcx - tail_span, pcy + int(95 * scale)),
        (pcx, pcy + int(70 * scale)),
        (pcx + tail_span, pcy + int(95 * scale)),
        (pcx, pcy + int(85 * scale))
    ]
    pdraw.polygon(tail_pts, fill=(215, 220, 230))
    # Vertical tail fin
    pdraw.polygon([(pcx - 5, pcy + int(60 * scale)), (pcx + 5, pcy + int(60 * scale)), (pcx, pcy + int(105 * scale))], fill=(225, 45, 55))

    # Cockpit windshield
    pdraw.polygon([(pcx - 8, pcy - int(80 * scale)), (pcx + 8, pcy - int(80 * scale)),
                   (pcx + 10, pcy - int(65 * scale)), (pcx - 10, pcy - int(65 * scale))], fill=(40, 80, 140))

    # Rotate airplane
    rotated_plane = plane_img.rotate(angle, resample=Image.Resampling.BICUBIC, center=(150, 150))
    return rotated_plane


def draw_boat(draw, rng):
    # Ocean scene
    draw.rectangle([0, 0, 300, 150], fill=(145 + rng.randint(-15, 15), 195 + rng.randint(-15, 15), 235))
    # Water waves
    draw.rectangle([0, 150, 300, 300], fill=(35 + rng.randint(-10, 10), 95 + rng.randint(-10, 10), 160 + rng.randint(-15, 15)))
    for y in range(160, 300, 25):
        draw.arc([10, y, 290, y + 15], start=0, end=180, fill=(75, 145, 215), width=2)

    cx = rng.randint(130, 170)
    cy = rng.randint(170, 195)
    scale = rng.uniform(0.85, 1.15)

    # Boat hull
    hw = int(75 * scale)
    hh = int(28 * scale)
    hull_pts = [
        (cx - hw, cy),
        (cx - hw + int(20 * scale), cy + hh),
        (cx + hw - int(20 * scale), cy + hh),
        (cx + hw + int(10 * scale), cy)
    ]
    draw.polygon(hull_pts, fill=(235, 238, 245), outline=(180, 60, 60), width=3)

    # Mast & Sail (or cabin)
    is_sailboat = rng.choice([True, False])
    if is_sailboat:
        # Mast
        draw.rectangle([cx - 3, cy - int(95 * scale), cx + 3, cy], fill=(120, 80, 45))
        # Large Triangular Sail
        sail_pts = [(cx + 4, cy - int(90 * scale)), (cx + 4, cy - int(10 * scale)), (cx + int(55 * scale), cy - int(10 * scale))]
        sail_col = rng.choice([(255, 255, 255), (255, 235, 200), (235, 60, 60)])
        draw.polygon(sail_pts, fill=sail_col, outline=(180, 180, 180))
        # Small front jib sail
        draw.polygon([(cx - 4, cy - int(75 * scale)), (cx - 4, cy - int(10 * scale)), (cx - int(38 * scale), cy - int(10 * scale))], fill=(245, 245, 250))
    else:
        # Motor cabin
        draw.rectangle([cx - int(35 * scale), cy - int(30 * scale), cx + int(30 * scale), cy], fill=(220, 225, 235), outline=(100, 105, 115), width=2)
        # Windows
        for wx in [cx - 25, cx - 5, cx + 15]:
            draw.rectangle([wx, cy - int(24 * scale), wx + 12, cy - int(8 * scale)], fill=(50, 110, 180))


def draw_tree(draw, rng):
    # Meadow background
    draw.rectangle([0, 0, 300, 180], fill=(145 + rng.randint(-15, 15), 205 + rng.randint(-15, 15), 245))
    draw.rectangle([0, 180, 300, 300], fill=(75 + rng.randint(-10, 15), 160 + rng.randint(-15, 15), 65 + rng.randint(-10, 15)))

    cx = rng.randint(130, 170)
    cy = rng.randint(185, 215)
    scale = rng.uniform(0.85, 1.15)

    # Trunk
    tw = int(18 * scale)
    th = int(95 * scale)
    draw.rectangle([cx - tw // 2, cy - th, cx + tw // 2, cy + int(20 * scale)], fill=(115, 75, 45))

    tree_type = rng.choice(["oak", "pine", "apple"])
    if tree_type == "pine":
        # Triangular tiers of green
        for i, ty in enumerate([cy - th - 30, cy - th + 5, cy - th + 40]):
            pw = int((50 + i * 22) * scale)
            pts = [(cx, ty - 35), (cx - pw, ty + 20), (cx + pw, ty + 20)]
            draw.polygon(pts, fill=(35 + i * 10, 110 + i * 15, 45 + i * 8))
    else:
        # Round leafy canopy clusters
        foliage_colors = [
            (45, 145, 55),
            (60, 165, 70),
            (35, 125, 45),
            (75, 180, 80),
        ]
        cr = int(52 * scale)
        centers = [
            (cx, cy - th - int(30 * scale)),
            (cx - int(38 * scale), cy - th - int(10 * scale)),
            (cx + int(38 * scale), cy - th - int(10 * scale)),
            (cx - int(22 * scale), cy - th + int(12 * scale)),
            (cx + int(22 * scale), cy - th + int(12 * scale)),
        ]
        for c in centers:
            draw.ellipse([c[0] - cr, c[1] - cr, c[0] + cr, c[1] + cr], fill=rng.choice(foliage_colors))
            if tree_type == "apple" and rng.random() > 0.4:
                ax = c[0] + rng.randint(-20, 20)
                ay = c[1] + rng.randint(-20, 20)
                draw.ellipse([ax - 4, ay - 4, ax + 4, ay + 4], fill=(225, 35, 35))


def draw_negative(draw, rng):
    # Patterns, geometric shapes, brick walls, textures without animals or vehicles
    scene = rng.choice(["brick", "tiles", "counter", "books", "abstract", "stars"])
    if scene == "brick":
        draw.rectangle([0, 0, 300, 300], fill=(175 + rng.randint(-15, 15), 75 + rng.randint(-10, 10), 55 + rng.randint(-10, 10)))
        for y in range(0, 300, 20):
            draw.line([(0, y), (300, y)], fill=(220, 215, 210), width=3)
            offset = 20 if (y // 20) % 2 == 0 else 0
            for x in range(offset, 320, 40):
                draw.line([(x, y), (x, y + 20)], fill=(220, 215, 210), width=3)
    elif scene == "tiles":
        for x in range(0, 300, 30):
            for y in range(0, 300, 30):
                c = (rng.randint(180, 230), rng.randint(190, 240), rng.randint(200, 250))
                draw.rectangle([x, y, x + 28, y + 28], fill=c)
    elif scene == "counter":
        draw.rectangle([0, 0, 300, 300], fill=(235, 235, 240))
        for _ in range(120):
            x, y = rng.randint(0, 295), rng.randint(0, 295)
            draw.ellipse([x, y, x + rng.randint(2, 6), y + rng.randint(2, 6)], fill=(rng.randint(60, 120), rng.randint(60, 120), rng.randint(60, 120)))
    elif scene == "books":
        draw.rectangle([0, 0, 300, 300], fill=(90, 60, 40))
        for y in [90, 190, 290]:
            draw.rectangle([0, y, 300, y + 10], fill=(140, 95, 60))
            for x in range(10, 280, rng.randint(18, 30)):
                b_col = (rng.randint(50, 220), rng.randint(50, 220), rng.randint(50, 220))
                draw.rectangle([x, y - rng.randint(55, 80), x + 16, y], fill=b_col)
    elif scene == "stars":
        draw.rectangle([0, 0, 300, 300], fill=(15, 18, 28))
        for _ in range(80):
            sx, sy = rng.randint(5, 295), rng.randint(5, 295)
            sr = rng.randint(1, 3)
            draw.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(255, 255, rng.randint(200, 255)))
    else:
        # Abstract fluid gradients
        for i in range(15):
            ax, ay = rng.randint(0, 300), rng.randint(0, 300)
            ar = rng.randint(40, 90)
            col = (rng.randint(50, 240), rng.randint(50, 240), rng.randint(50, 240), 120)
            draw.ellipse([ax - ar, ay - ar, ax + ar, ay + ar], fill=col)


def generate_image_for_category(category: str, index: int) -> Image.Image:
    # Use deterministic yet diverse seed per image
    rng = random.Random(hash(f"{category}_{index}") + 42)
    img = Image.new("RGB", IMAGE_SIZE, (255, 255, 255))
    draw = ImageDraw.Draw(img)

    if category == "cat":
        draw_cat(draw, rng)
    elif category == "dog":
        draw_dog(draw, rng)
    elif category == "car":
        draw_car(draw, rng)
    elif category == "bicycle":
        draw_bicycle(draw, rng)
    elif category == "traffic_light":
        draw_traffic_light(draw, rng)
    elif category == "airplane":
        plane_layer = draw_airplane(draw, rng)
        img.paste(plane_layer, (0, 0), plane_layer)
    elif category == "boat":
        draw_boat(draw, rng)
    elif category == "tree":
        draw_tree(draw, rng)
    elif category == "negative":
        draw_negative(draw, rng)

    # Slight blur to soften edges naturally
    img = img.filter(ImageFilter.SMOOTH_MORE)
    return img


def generate_all_images(count_per_category: int = COUNT_PER_CATEGORY):
    total = 0
    for cat in CATEGORIES:
        cat_dir = IMAGE_BANK_DIR / cat
        cat_dir.mkdir(parents=True, exist_ok=True)
        print(f"Generating {count_per_category} images for category: {cat}...")
        for i in range(1, count_per_category + 1):
            img = generate_image_for_category(cat, i)
            out_file = cat_dir / f"img_{i:02d}.jpg"
            # Strip EXIF by saving without exif info
            img.save(out_file, format="JPEG", quality=85, optimize=True)
            total += 1

    print(f"Successfully generated {total} images across {len(CATEGORIES)} categories in {IMAGE_BANK_DIR}!")


if __name__ == "__main__":
    generate_all_images()
