import os
import math
import wave
import struct
import subprocess
from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QFont, QLinearGradient
)

SUBPROC_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

ASSET_TYPES = [
    "image",
    "gta_title",
    "info_grid",
    "beige_info",
    "bait_badge",
    "rule_card",
    "info_stamp",
    "info_bars",
    "montage_card",
    "info_meter",
    "summary_card",
    "choice_buttons",
    "end_card"
]

NO_HUD_TYPES = {
    "gta_title",
    "info_grid",
    "beige_info",
    "rule_card",
    "info_stamp",
    "info_bars",
    "info_meter",
    "summary_card",
    "choice_buttons",
    "end_card"
}

CAM_MODES = [
    "wide",
    "heartbeat_shake",
    "white_flash",
    "push_slam",
    "zoom_char",
    "closeup_face",
    "closeup_bags",
    "closeup_hands",
    "punch_bags",
    "pan_left",
    "pan_right",
    "dutch_tilt"
]


def ease_out_cubic(t):
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - pow(1.0 - t, 3)


def ease_out_back(t):
    t = max(0.0, min(1.0, float(t)))
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * pow(t - 1.0, 3) + c1 * pow(t - 1.0, 2)


def draw_background_media(painter, w, h, slide, prog, render_images, darken=0.22):
    img_path = slide.get("image_path", "")
    item = render_images.get(img_path) if render_images else None

    qimg = None
    if isinstance(item, list) and item:
        idx = int(prog * (len(item) - 1)) % len(item)
        qimg = item[idx]
    elif item is not None and not item.isNull():
        qimg = item

    if qimg is None:
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor("#12131c"))
        grad.setColorAt(1.0, QColor("#08080c"))
        painter.fillRect(0, 0, w, h, grad)
        return

    cam_mode = slide.get("cam_mode", "wide")
    base_zoom = float(slide.get("zoom", 1.10))

    scale = 1.0 + (base_zoom - 1.0) * prog
    focus_x, focus_y = 0.50, 0.50
    rot_deg = 0.0

    if cam_mode == "heartbeat_shake":
        beat_phase = (prog * 3.5) % 1.0
        bump = math.exp(-beat_phase * 9.0) * math.sin(beat_phase * 28.0)
        scale = 1.04 + (base_zoom - 1.0) * prog + abs(bump) * 0.035
        focus_x = 0.50 + bump * 0.015
        focus_y = 0.50 + bump * 0.010
    elif cam_mode == "push_slam":
        slam_p = min(1.0, prog / 0.18)
        scale = 1.0 + 0.22 * pow(slam_p, 0.5)
    elif cam_mode == "zoom_char":
        scale = 1.04 + (base_zoom - 1.0) * prog * 1.15
        focus_y = 0.42
    elif cam_mode == "closeup_face":
        scale = 1.10 + (base_zoom - 1.0) * prog * 1.35
        focus_y = 0.34
    elif cam_mode in ("closeup_bags", "closeup_hands"):
        scale = 1.08 + (base_zoom - 1.0) * prog * 1.25
        focus_y = 0.60
    elif cam_mode == "punch_bags":
        punch = ease_out_back(min(1.0, prog * 3.0))
        scale = 1.0 + (base_zoom - 1.0 + 0.08) * punch
        focus_y = 0.62
    elif cam_mode == "pan_left":
        scale = max(1.12, base_zoom)
        focus_x = 0.58 - 0.16 * prog
    elif cam_mode == "pan_right":
        scale = max(1.12, base_zoom)
        focus_x = 0.42 + 0.16 * prog
    elif cam_mode == "dutch_tilt":
        scale = max(1.12, base_zoom)
        rot_deg = -2.5 + 5.0 * prog

    iw, ih = qimg.width(), qimg.height()
    base_s = max(w / max(1, iw), h / max(1, ih))
    final_s = base_s * scale

    draw_w = iw * final_s
    draw_h = ih * final_s

    painter.save()
    painter.translate(w * 0.5, h * 0.5)
    if abs(rot_deg) > 0.05:
        painter.rotate(rot_deg)

    ox = -draw_w * focus_x
    oy = -draw_h * focus_y
    painter.drawImage(QRectF(ox, oy, draw_w, draw_h), qimg)
    painter.restore()

    if darken > 0.01:
        painter.fillRect(0, 0, w, h, QColor(5, 6, 10, int(255 * darken)))

    v_top = QLinearGradient(0, 0, 0, h * 0.25)
    v_top.setColorAt(0.0, QColor(0, 0, 0, 175))
    v_top.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillRect(0, 0, w, int(h * 0.25), v_top)

    v_bot = QLinearGradient(0, h * 0.72, 0, h)
    v_bot.setColorAt(0.0, QColor(0, 0, 0, 0))
    v_bot.setColorAt(1.0, QColor(0, 0, 0, 210))
    painter.fillRect(0, int(h * 0.72), w, int(h * 0.28), v_bot)

    if cam_mode == "white_flash" and prog < 0.22:
        flash_alpha = int((1.0 - (prog / 0.22)) * 235)
        painter.fillRect(0, 0, w, h, QColor(255, 255, 255, flash_alpha))


def draw_hud(painter, w, h, slide, layout_cfg, hover_target):
    cash_val = str(slide.get("cash", "")).strip()
    if not cash_val or cash_val.upper() == "HIDE":
        return None
    if slide.get("type") in NO_HUD_TYPES:
        return None

    # ПРИНУДИТЕЛЬНАЯ ОЧИСТКА СТАРЫХ НАЗВАНИЙ ("В ПАМЯТИ", "КУШ", "РИСК ПОТЕРИ")
    layout_cfg["hud_col1"] = "НАЛИЧНЫЕ"
    layout_cfg["hud_col2"] = "В БАНКЕ"
    layout_cfg["hud_col3"] = "ПОДОЗРЕНИЕ"

    c1_lbl = str(slide.get("hud_col1", "")).strip() or "НАЛИЧНЫЕ"
    c2_lbl = str(slide.get("hud_col2", "")).strip() or "В БАНКЕ"
    c3_lbl = str(slide.get("hud_col3", "")).strip() or "ПОДОЗРЕНИЕ"

    if c1_lbl == "КУШ":
        c1_lbl = "НАЛИЧНЫЕ"
    if c2_lbl == "В ПАМЯТИ":
        c2_lbl = "В БАНКЕ"
    if c3_lbl == "РИСК ПОТЕРИ":
        c3_lbl = "ПОДОЗРЕНИЕ"

    scale = float(layout_cfg.get("hud_scale", 1.0)) * (w / 1080.0)
    hud_w = w * 0.90 * float(layout_cfg.get("hud_scale", 1.0))
    hud_h = 148 * scale
    cx = w * float(layout_cfg.get("hud_x", 0.50))
    top_y = h * float(layout_cfg.get("hud_y", 0.03))
    rect = QRectF(cx - hud_w / 2.0, top_y, hud_w, hud_h)

    painter.save()
    bg_col = QColor(14, 15, 22, 230)
    border_col = QColor("#00e5ff") if hover_target == "hud" else QColor(255, 255, 255, 45)
    painter.setBrush(bg_col)
    painter.setPen(QPen(border_col, max(2.0, 3.0 * scale)))
    painter.drawRoundedRect(rect, 22 * scale, 22 * scale)

    cols = [
        (c1_lbl, cash_val, QColor("#2ecc71")),
        (c2_lbl, str(slide.get("mem", "$0")), QColor("#00e5ff")),
        (c3_lbl, str(slide.get("risk", "0%")), QColor("#ff3b6b"))
    ]

    col_w = hud_w / 3.0
    lbl_font = QFont("Segoe UI", max(8, int(22 * scale)), QFont.Bold)
    val_font = QFont("Segoe UI", max(10, int(38 * scale)), QFont.Black)

    for i, (label, val, col) in enumerate(cols):
        cell_x = rect.left() + i * col_w
        if i > 0:
            painter.setPen(QPen(QColor(255, 255, 255, 30), max(1.0, 2.0 * scale)))
            painter.drawLine(
                QPointF(cell_x, rect.top() + 20 * scale),
                QPointF(cell_x, rect.bottom() - 20 * scale)
            )

        painter.setFont(lbl_font)
        painter.setPen(QColor(180, 185, 200))
        painter.drawText(QRectF(cell_x, rect.top() + 16 * scale, col_w, 40 * scale), Qt.AlignCenter, label)

        painter.setFont(val_font)
        painter.setPen(col)
        painter.drawText(QRectF(cell_x, rect.top() + 58 * scale, col_w, 72 * scale), Qt.AlignCenter, val)

    painter.restore()
    return rect


def draw_tag_pill(painter, w, h, tag_text, y_rel=0.14, bg_hex="#ef3b85", fg_hex="#ffffff"):
    if not tag_text:
        return
    s = w / 1080.0
    font = QFont("Segoe UI", max(9, int(28 * s)), QFont.Black)
    painter.save()
    painter.setFont(font)
    fm = painter.fontMetrics()
    tw = fm.horizontalAdvance(tag_text) + int(64 * s)
    th = int(64 * s)
    rect = QRectF((w - tw) / 2.0, h * y_rel, tw, th)

    painter.setBrush(QColor(bg_hex))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(rect, 16 * s, 16 * s)
    painter.setPen(QColor(fg_hex))
    painter.drawText(rect, Qt.AlignCenter, tag_text)
    painter.restore()


def draw_standard_overlays(painter, w, h, slide, prog, layout_cfg, hover_target):
    hits = {}
    s = w / 1080.0
    has_hud = slide.get("cash", "HIDE") != "HIDE" and slide.get("type") not in NO_HUD_TYPES

    tag = slide.get("tag", "").strip()
    if tag:
        draw_tag_pill(painter, w, h, tag, y_rel=0.135 if has_hud else 0.08)

    title = slide.get("title", "").strip()
    if title:
        t_scale = float(slide.get("title_scale", layout_cfg.get("title_scale", 1.0)))
        tx = w * float(slide.get("title_x", layout_cfg.get("title_x", 0.50)))
        ty = h * float(slide.get("title_y", layout_cfg.get("title_y", 0.44)))

        pop = ease_out_back(min(1.0, prog * 3.2))
        font = QFont("Segoe UI", max(10, int(56 * s * t_scale * (0.85 + 0.15 * pop))), QFont.Black)
        painter.save()
        painter.setFont(font)

        box_w, box_h = w * 0.88, 240 * s * t_scale
        t_rect = QRectF(tx - box_w / 2.0, ty - box_h / 2.0, box_w, box_h)

        painter.setBrush(QColor(12, 13, 20, 210))
        pen_col = QColor("#00e5ff") if hover_target == "title" else QColor(255, 255, 255, 45)
        painter.setPen(QPen(pen_col, max(2.0, 3.0 * s)))
        painter.drawRoundedRect(t_rect, 24 * s, 24 * s)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect.adjusted(20 * s, 10 * s, -20 * s, -10 * s), Qt.AlignCenter | Qt.TextWordWrap, title)
        painter.restore()
        hits["title"] = t_rect

    extra = slide.get("extra", "").strip()
    if extra:
        e_scale = float(slide.get("extra_scale", layout_cfg.get("extra_scale", 1.0)))
        ex = w * float(slide.get("extra_x", layout_cfg.get("extra_x", 0.50)))
        ey = h * float(slide.get("extra_y", layout_cfg.get("extra_y", 0.72)))

        font = QFont("Segoe UI", max(9, int(44 * s * e_scale)), QFont.Black)
        painter.save()
        painter.setFont(font)
        box_w, box_h = w * 0.82, 130 * s * e_scale
        e_rect = QRectF(ex - box_w / 2.0, ey - box_h / 2.0, box_w, box_h)

        painter.setBrush(QColor("#ef3b85"))
        pen_col = QColor("#00e5ff") if hover_target == "extra" else QColor(255, 255, 255, 90)
        painter.setPen(QPen(pen_col, max(2.0, 3.0 * s)))
        painter.drawRoundedRect(e_rect, 20 * s, 20 * s)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(e_rect, Qt.AlignCenter | Qt.TextWordWrap, extra)
        painter.restore()
        hits["extra"] = e_rect

    return hits


def draw_frame(painter, w, h, slide, prog, layout_cfg, render_images, hover_target=None):
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    s = w / 1080.0
    stype = slide.get("type", "image")
    hit_boxes = {}

    if stype == "gta_title":
        draw_background_media(painter, w, h, slide, prog, render_images, darken=0.58)
        draw_tag_pill(painter, w, h, slide.get("tag", "ЧАСТЬ 1"), y_rel=0.26, bg_hex="#ef3b85")

        t_rect = QRectF(w * 0.06, h * 0.34, w * 0.88, h * 0.22)
        painter.setFont(QFont("Segoe UI", max(14, int(82 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("title", ""))
        hit_boxes["title"] = t_rect

        extra = slide.get("extra", "")
        if extra and prog > 0.22:
            slam = ease_out_back(min(1.0, (prog - 0.22) * 3.5))
            e_rect = QRectF(w * 0.14, h * 0.58, w * 0.72, h * 0.13)
            painter.save()
            painter.translate(e_rect.center())
            painter.scale(slam, slam)
            painter.rotate(-3.0)
            local_r = QRectF(-e_rect.width() / 2, -e_rect.height() / 2, e_rect.width(), e_rect.height())
            painter.setBrush(QColor("#ef3b85"))
            painter.setPen(QPen(QColor("#ffffff"), 4 * s))
            painter.drawRoundedRect(local_r, 22 * s, 22 * s)
            painter.setFont(QFont("Segoe UI", max(14, int(92 * s)), QFont.Black))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(local_r, Qt.AlignCenter, extra)
            painter.restore()
            hit_boxes["extra"] = e_rect

    elif stype == "info_grid":
        painter.fillRect(0, 0, w, h, QColor("#0d0f17"))
        draw_tag_pill(painter, w, h, slide.get("tag", "ПРОБЛЕМА 1 • ВЕС"), y_rel=0.08, bg_hex="#ef3b85")

        count_num = int(round(100000 * ease_out_cubic(min(1.0, prog * 1.35))))
        raw_title = slide.get("title", "$10М сотнями = {count} купюр")
        title_txt = raw_title.replace("{count}", f"{count_num:,}")

        t_rect = QRectF(w * 0.06, h * 0.16, w * 0.88, h * 0.14)
        painter.setFont(QFont("Segoe UI", max(11, int(54 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, title_txt)
        hit_boxes["title"] = t_rect

        cols, rows = 10, 10
        total_cells = cols * rows
        active_cells = int(round(total_cells * min(1.0, prog * 1.3)))
        grid_w, grid_h = w * 0.82, h * 0.36
        gx, gy = (w - grid_w) / 2.0, h * 0.32
        cw, ch = grid_w / cols, grid_h / rows

        for idx in range(total_cells):
            r, c = divmod(idx, cols)
            cell = QRectF(gx + c * cw + 4 * s, gy + r * ch + 4 * s, cw - 8 * s, ch - 8 * s)
            painter.setBrush(QColor("#2ecc71") if idx < active_cells else QColor(255, 255, 255, 22))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(cell, 6 * s, 6 * s)

        extra = slide.get("extra", "100 кг")
        if extra and prog > 0.35:
            e_rect = QRectF(w * 0.18, h * 0.72, w * 0.64, h * 0.14)
            painter.setBrush(QColor("#ef3b85"))
            painter.setPen(QPen(QColor("#ffffff"), 3 * s))
            painter.drawRoundedRect(e_rect, 24 * s, 24 * s)
            painter.setFont(QFont("Segoe UI", max(14, int(86 * s)), QFont.Black))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(e_rect, Qt.AlignCenter, extra)
            hit_boxes["extra"] = e_rect

    elif stype == "beige_info":
        painter.fillRect(0, 0, w, h, QColor("#e8dfd1"))
        draw_tag_pill(painter, w, h, slide.get("tag", "А ДВАДЦАТКАМИ?"), y_rel=0.10, bg_hex="#181820", fg_hex="#ffffff")

        t_rect = QRectF(w * 0.06, h * 0.24, w * 0.88, h * 0.38)
        painter.setFont(QFont("Segoe UI", max(18, int(138 * s)), QFont.Black))
        painter.setPen(QColor("#14141c"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("title", "ПОЛ-\nТОННЫ"))
        hit_boxes["title"] = t_rect

        e_rect = QRectF(w * 0.07, h * 0.68, w * 0.86, h * 0.14)
        painter.setBrush(QColor("#ef3b85"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(e_rect, 22 * s, 22 * s)
        painter.setFont(QFont("Segoe UI", max(10, int(42 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(e_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("extra", ""))
        hit_boxes["extra"] = e_rect

    elif stype == "bait_badge":
        draw_background_media(painter, w, h, slide, prog, render_images, darken=0.45)
        hud_r = draw_hud(painter, w, h, slide, layout_cfg, hover_target)
        if hud_r:
            hit_boxes["hud"] = hud_r

        draw_tag_pill(painter, w, h, slide.get("tag", "ПРОБЛЕМА 2"), y_rel=0.14, bg_hex="#ef3b85")

        bill_r = QRectF(w * 0.10, h * 0.30, w * 0.80, h * 0.22)
        painter.setBrush(QColor(22, 42, 32, 235))
        painter.setPen(QPen(QColor("#2ecc71"), 4 * s))
        painter.drawRoundedRect(bill_r, 20 * s, 20 * s)

        painter.setFont(QFont("Consolas", max(11, int(52 * s)), QFont.Bold))
        painter.setPen(QColor("#ff3b6b") if int(prog * 10) % 2 == 0 else QColor("#ffffff"))
        painter.drawText(bill_r, Qt.AlignCenter, "SN: DB 84920194 B\n[ В БАЗЕ ФБР ]")

        t_rect = QRectF(w * 0.08, h * 0.58, w * 0.84, h * 0.18)
        painter.setBrush(QColor(14, 15, 22, 230))
        painter.setPen(QPen(QColor("#ef3b85"), 3 * s))
        painter.drawRoundedRect(t_rect, 22 * s, 22 * s)
        painter.setFont(QFont("Segoe UI", max(10, int(46 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("title", ""))
        hit_boxes["title"] = t_rect

    elif stype == "rule_card":
        painter.fillRect(0, 0, w, h, QColor("#0d0e16"))
        card = QRectF(w * 0.08, h * 0.24, w * 0.84, h * 0.48)
        painter.setBrush(QColor("#171926"))
        painter.setPen(QPen(QColor("#ef3b85"), 5 * s))
        painter.drawRoundedRect(card, 32 * s, 32 * s)

        draw_tag_pill(painter, w, h, slide.get("tag", "ПРАВИЛО 1"), y_rel=0.28, bg_hex="#ef3b85")
        t_rect = QRectF(w * 0.12, h * 0.38, w * 0.76, h * 0.26)
        painter.setFont(QFont("Segoe UI", max(12, int(62 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("title", ""))
        hit_boxes["title"] = t_rect

    elif stype == "info_stamp":
        painter.fillRect(0, 0, w, h, QColor("#10121c"))
        draw_tag_pill(painter, w, h, slide.get("tag", "ЛИМИТ $10,000"), y_rel=0.10, bg_hex="#f39c12")

        t_rect = QRectF(w * 0.08, h * 0.24, w * 0.84, h * 0.34)
        painter.setBrush(QColor("#1a1d2e"))
        painter.setPen(QPen(QColor(255, 255, 255, 45), 3 * s))
        painter.drawRoundedRect(t_rect, 26 * s, 26 * s)
        painter.setFont(QFont("Segoe UI", max(11, int(54 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("title", ""))
        hit_boxes["title"] = t_rect

        extra = slide.get("extra", "АВТОМАТИЧЕСКИ")
        if extra and prog > 0.25:
            slam = ease_out_back(min(1.0, (prog - 0.25) * 3.5))
            e_rect = QRectF(w * 0.12, h * 0.65, w * 0.76, h * 0.13)
            painter.save()
            painter.translate(e_rect.center())
            painter.scale(slam, slam)
            painter.rotate(-6.0)
            lr = QRectF(-e_rect.width() / 2, -e_rect.height() / 2, e_rect.width(), e_rect.height())
            painter.setBrush(QColor(239, 59, 133, 45))
            painter.setPen(QPen(QColor("#ef3b85"), 6 * s))
            painter.drawRoundedRect(lr, 18 * s, 18 * s)
            painter.setFont(QFont("Segoe UI", max(12, int(60 * s)), QFont.Black))
            painter.setPen(QColor("#ef3b85"))
            painter.drawText(lr, Qt.AlignCenter, extra)
            painter.restore()
            hit_boxes["extra"] = e_rect

    elif stype == "info_bars":
        painter.fillRect(0, 0, w, h, QColor("#0d1018"))
        draw_tag_pill(painter, w, h, slide.get("tag", "ПЛАН"), y_rel=0.09, bg_hex="#00e5ff", fg_hex="#0d1018")

        t_rect = QRectF(w * 0.08, h * 0.18, w * 0.84, h * 0.10)
        painter.setFont(QFont("Segoe UI", max(11, int(52 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter, slide.get("title", "$9k • $9k • $9k • $9k"))
        hit_boxes["title"] = t_rect

        chart_top, chart_bot = h * 0.34, h * 0.64
        painter.setPen(QPen(QColor("#ff3b6b"), 4 * s, Qt.DashLine))
        painter.drawLine(QPointF(w * 0.10, chart_top), QPointF(w * 0.90, chart_top))

        bar_w = w * 0.14
        for i in range(4):
            local_p = ease_out_cubic(max(0.0, min(1.0, (prog - i * 0.15) * 2.5)))
            bh = (chart_bot - chart_top) * 0.86 * local_p
            bx = w * 0.14 + i * (w * 0.19)
            brect = QRectF(bx, chart_bot - bh, bar_w, bh)
            painter.setBrush(QColor("#2ecc71"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(brect, 12 * s, 12 * s)

        e_rect = QRectF(w * 0.10, h * 0.70, w * 0.80, h * 0.15)
        painter.setBrush(QColor("#181c2b"))
        painter.setPen(QPen(QColor("#00e5ff"), 3 * s))
        painter.drawRoundedRect(e_rect, 22 * s, 22 * s)
        painter.setFont(QFont("Segoe UI", max(11, int(48 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(e_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("extra", ""))
        hit_boxes["extra"] = e_rect

    elif stype == "montage_card":
        draw_background_media(painter, w, h, slide, prog, render_images, darken=0.35)
        hud_r = draw_hud(painter, w, h, slide, layout_cfg, hover_target)
        if hud_r:
            hit_boxes["hud"] = hud_r

        pop = ease_out_back(min(1.0, prog * 3.5))
        t_rect = QRectF(w * 0.12, h * 0.40, w * 0.76, h * 0.18)
        painter.save()
        painter.translate(t_rect.center())
        painter.scale(pop, pop)
        lr = QRectF(-t_rect.width() / 2, -t_rect.height() / 2, t_rect.width(), t_rect.height())
        painter.setBrush(QColor(12, 16, 22, 230))
        painter.setPen(QPen(QColor("#2ecc71"), 5 * s))
        painter.drawRoundedRect(lr, 26 * s, 26 * s)
        painter.setFont(QFont("Segoe UI", max(16, int(105 * s)), QFont.Black))
        painter.setPen(QColor("#2ecc71"))
        painter.drawText(lr, Qt.AlignCenter, slide.get("title", "$9,000"))
        painter.restore()
        hit_boxes["title"] = t_rect

        e_rect = QRectF(w * 0.18, h * 0.62, w * 0.64, h * 0.10)
        painter.setBrush(QColor("#ef3b85"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(e_rect, 18 * s, 18 * s)
        painter.setFont(QFont("Segoe UI", max(10, int(42 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(e_rect, Qt.AlignCenter, slide.get("extra", ""))
        hit_boxes["extra"] = e_rect

    elif stype == "info_meter":
        painter.fillRect(0, 0, w, h, QColor("#120d16"))
        draw_tag_pill(painter, w, h, slide.get("tag", "У ЭТОЙ СХЕМЫ ЕСТЬ ИМЯ"), y_rel=0.09, bg_hex="#ff3b6b")

        t_rect = QRectF(w * 0.07, h * 0.19, w * 0.86, h * 0.22)
        painter.setFont(QFont("Segoe UI", max(11, int(54 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("title", ""))
        hit_boxes["title"] = t_rect

        bar_bg = QRectF(w * 0.10, h * 0.46, w * 0.80, h * 0.08)
        painter.setBrush(QColor("#251b2e"))
        painter.setPen(QPen(QColor(255, 255, 255, 50), 3 * s))
        painter.drawRoundedRect(bar_bg, 20 * s, 20 * s)

        fill_w = bar_bg.width() * 0.35 * ease_out_cubic(min(1.0, prog * 1.6))
        bar_fill = QRectF(bar_bg.left(), bar_bg.top(), max(20 * s, fill_w), bar_bg.height())
        painter.setBrush(QColor("#ff3b6b"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bar_fill, 20 * s, 20 * s)

        e_rect = QRectF(w * 0.12, h * 0.62, w * 0.76, h * 0.13)
        painter.setBrush(QColor("#ff3b6b"))
        painter.setPen(QPen(QColor("#ffffff"), 4 * s))
        painter.drawRoundedRect(e_rect, 22 * s, 22 * s)
        painter.setFont(QFont("Segoe UI", max(12, int(58 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(e_rect, Qt.AlignCenter, slide.get("extra", "УГОЛОВНАЯ СТАТЬЯ"))
        hit_boxes["extra"] = e_rect

    elif stype == "summary_card":
        painter.fillRect(0, 0, w, h, QColor("#0e1018"))
        draw_tag_pill(painter, w, h, slide.get("tag", "ДЕНЬ 3 • РАСКЛАД"), y_rel=0.10, bg_hex="#00e5ff", fg_hex="#0e1018")

        t_rect = QRectF(w * 0.08, h * 0.24, w * 0.84, h * 0.32)
        painter.setBrush(QColor("#181c29"))
        painter.setPen(QPen(QColor("#2ecc71"), 4 * s))
        painter.drawRoundedRect(t_rect, 28 * s, 28 * s)
        painter.setFont(QFont("Segoe UI", max(11, int(54 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("title", ""))
        hit_boxes["title"] = t_rect

        e_rect = QRectF(w * 0.08, h * 0.62, w * 0.84, h * 0.15)
        painter.setBrush(QColor("#2b1420"))
        painter.setPen(QPen(QColor("#ff3b6b"), 4 * s))
        painter.drawRoundedRect(e_rect, 26 * s, 26 * s)
        painter.setFont(QFont("Segoe UI", max(12, int(58 * s)), QFont.Black))
        painter.setPen(QColor("#ff3b6b"))
        painter.drawText(e_rect, Qt.AlignCenter, slide.get("extra", "ПОДОЗРЕНИЕ: 35%"))
        hit_boxes["extra"] = e_rect

    elif stype == "choice_buttons":
        draw_background_media(painter, w, h, slide, prog, render_images, darken=0.55)
        draw_tag_pill(painter, w, h, slide.get("tag", "НАПИШИ ОДНО СЛОВО"), y_rel=0.18, bg_hex="#ffffff", fg_hex="#10121c")

        pulse = 1.0 + 0.04 * math.sin(prog * math.pi * 6.0)
        t_rect = QRectF(w * 0.12, h * 0.36, w * 0.76, h * 0.16)
        painter.save()
        painter.translate(t_rect.center())
        painter.scale(pulse, pulse)
        lr1 = QRectF(-t_rect.width() / 2, -t_rect.height() / 2, t_rect.width(), t_rect.height())
        painter.setBrush(QColor("#00e5ff"))
        painter.setPen(QPen(QColor("#ffffff"), 4 * s))
        painter.drawRoundedRect(lr1, 28 * s, 28 * s)
        painter.setFont(QFont("Segoe UI", max(14, int(74 * s)), QFont.Black))
        painter.setPen(QColor("#0a0c14"))
        painter.drawText(lr1, Qt.AlignCenter, slide.get("title", "БИЗНЕС"))
        painter.restore()
        hit_boxes["title"] = t_rect

        pulse2 = 1.0 + 0.04 * math.cos(prog * math.pi * 6.0)
        e_rect = QRectF(w * 0.12, h * 0.58, w * 0.76, h * 0.16)
        painter.save()
        painter.translate(e_rect.center())
        painter.scale(pulse2, pulse2)
        lr2 = QRectF(-e_rect.width() / 2, -e_rect.height() / 2, e_rect.width(), e_rect.height())
        painter.setBrush(QColor("#ef3b85"))
        painter.setPen(QPen(QColor("#ffffff"), 4 * s))
        painter.drawRoundedRect(lr2, 28 * s, 28 * s)
        painter.setFont(QFont("Segoe UI", max(14, int(74 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(lr2, Qt.AlignCenter, slide.get("extra", "ПОБЕГ"))
        painter.restore()
        hit_boxes["extra"] = e_rect

    elif stype == "end_card":
        painter.fillRect(0, 0, w, h, QColor("#090a10"))
        draw_tag_pill(painter, w, h, slide.get("tag", "КАЖДЫЙ ДОЛЛАР ОСТАВЛЯЕТ СЛЕД"), y_rel=0.14, bg_hex="#ef3b85")

        t_rect = QRectF(w * 0.08, h * 0.28, w * 0.84, h * 0.30)
        painter.setFont(QFont("Segoe UI", max(18, int(115 * s)), QFont.Black))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(t_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("title", "ЧАСТЬ 2\nЗАВТРА"))
        hit_boxes["title"] = t_rect

        e_rect = QRectF(w * 0.08, h * 0.64, w * 0.84, h * 0.18)
        painter.setBrush(QColor("#161926"))
        painter.setPen(QPen(QColor("#00e5ff"), 3 * s))
        painter.drawRoundedRect(e_rect, 24 * s, 24 * s)
        painter.setFont(QFont("Segoe UI", max(10, int(42 * s)), QFont.Bold))
        painter.setPen(QColor("#00e5ff"))
        painter.drawText(e_rect, Qt.AlignCenter | Qt.TextWordWrap, slide.get("extra", ""))
        hit_boxes["extra"] = e_rect

    else:
        draw_background_media(painter, w, h, slide, prog, render_images, darken=0.18)
        hud_r = draw_hud(painter, w, h, slide, layout_cfg, hover_target)
        if hud_r:
            hit_boxes["hud"] = hud_r
        std_hits = draw_standard_overlays(painter, w, h, slide, prog, layout_cfg, hover_target)
        hit_boxes.update(std_hits)

    return hit_boxes


def _add_tone(buf, sr, start_sec, dur_sec, freq_start, freq_end, vol=0.35, wave_type="sine"):
    i0 = max(0, int(start_sec * sr))
    n = int(dur_sec * sr)
    total = len(buf)
    phase = 0.0
    for k in range(n):
        idx = i0 + k
        if idx >= total:
            break
        p = k / float(max(1, n))
        env = math.sin(p * math.pi) if p < 0.15 else pow(1.0 - p, 1.6)
        freq = freq_start + (freq_end - freq_start) * p
        phase += 2.0 * math.pi * freq / sr
        val = math.sin(phase)
        if wave_type == "square":
            val = 1.0 if val >= 0 else -1.0
        elif wave_type == "punch":
            val = math.tanh(val * 2.6)
        buf[idx] += val * env * vol


def generate_sfx_mix_wav(source_voice, slides, out_sfx_wav, projects_dir, bgm_gain=0.65, sfx_gain=0.90):
    sr = 24000
    tmp_voice = os.path.join(projects_dir, "_tmp_voice_24k.wav")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", source_voice,
        "-ac", "1", "-ar", str(sr), tmp_voice
    ], check=True, creationflags=SUBPROC_FLAGS)

    with wave.open(tmp_voice, "rb") as wf:
        n_frames = wf.getnframes()
        voice_samples = list(struct.unpack(f"<{n_frames}h", wf.readframes(n_frames)))

    if os.path.exists(tmp_voice):
        os.remove(tmp_voice)

    last_slide_end = float(slides[-1].get("end", 5.0)) if slides else 5.0
    total_samples = max(len(voice_samples), int((last_slide_end + 0.04) * sr))
    if len(voice_samples) < total_samples:
        voice_samples.extend([0] * (total_samples - len(voice_samples)))

    fx_buf = [0.0] * total_samples

    if bgm_gain > 0.01:
        for i in range(total_samples):
            t = i / float(sr)
            pulse = 0.55 + 0.45 * math.sin(2.0 * math.pi * 1.5 * t)
            drone = math.sin(2.0 * math.pi * 55.0 * t) * 0.6 + math.sin(2.0 * math.pi * 110.0 * t) * 0.25
            fx_buf[i] += drone * pulse * 0.14 * bgm_gain

    if sfx_gain > 0.01:
        for s in slides:
            st = float(s.get("start", 0.0))
            stype = s.get("type", "image")
            cam = s.get("cam_mode", "wide")

            if stype == "gta_title":
                _add_tone(fx_buf, sr, st, 0.55, 110.0, 38.0, vol=0.55 * sfx_gain, wave_type="punch")
                _add_tone(fx_buf, sr, st + 0.45, 0.35, 220.0, 55.0, vol=0.60 * sfx_gain, wave_type="punch")
            elif stype in ("info_stamp", "beige_info", "info_grid", "rule_card"):
                _add_tone(fx_buf, sr, st, 0.30, 140.0, 45.0, vol=0.50 * sfx_gain, wave_type="punch")
            elif stype == "choice_buttons":
                _add_tone(fx_buf, sr, st, 0.16, 680.0, 980.0, vol=0.40 * sfx_gain, wave_type="sine")
                _add_tone(fx_buf, sr, st + 0.35, 0.16, 820.0, 1180.0, vol=0.42 * sfx_gain, wave_type="sine")
            elif cam in ("punch_bags", "push_slam"):
                _add_tone(fx_buf, sr, st, 0.28, 130.0, 42.0, vol=0.50 * sfx_gain, wave_type="punch")
            elif cam == "white_flash":
                _add_tone(fx_buf, sr, st, 0.22, 520.0, 1180.0, vol=0.38 * sfx_gain, wave_type="sine")
            else:
                _add_tone(fx_buf, sr, st, 0.18, 95.0, 48.0, vol=0.25 * sfx_gain, wave_type="sine")

    out_pcm = []
    for i in range(total_samples):
        v = voice_samples[i] / 32768.0
        mixed = v + fx_buf[i]
        clamped = max(-0.98, min(0.98, math.tanh(mixed * 1.05)))
        out_pcm.append(int(clamped * 32767))

    with wave.open(out_sfx_wav, "wb") as wf_out:
        wf_out.setnchannels(1)
        wf_out.setsampwidth(2)
        wf_out.setframerate(sr)
        wf_out.writeframes(struct.pack(f"<{len(out_pcm)}h", *out_pcm))