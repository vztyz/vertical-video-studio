import sys
import os
import re
import gc
import math
import json
import wave
import struct
import importlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QListWidgetItem, QLabel, QCheckBox,
    QLineEdit, QTextEdit, QFileDialog, QComboBox, QDoubleSpinBox,
    QProgressBar, QMessageBox, QSplitter, QGroupBox, QFormLayout, QInputDialog, QSlider
)
from PyQt5.QtCore import Qt, QRect, QThread, pyqtSignal, QPointF, QTimer
from PyQt5.QtGui import QImage, QPainter, QColor

import assets_engine

PROJECTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)
SUBPROC_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

DEFAULT_LAYOUT = {
    "hud_x": 0.50, "hud_y": 0.03, "hud_scale": 1.0,
    "hud_col1": "КУШ", "hud_col2": "В ПАМЯТИ", "hud_col3": "РИСК ПОТЕРИ",
    "title_x": 0.50, "title_y": 0.44, "title_scale": 1.0,
    "extra_x": 0.50, "extra_y": 0.72, "extra_scale": 1.0,
    "sub_x": 0.50, "sub_y": 0.80, "sub_scale": 0.95,
    "burn_subtitles": False,
    "bgm_vol": 65,
    "sfx_vol": 90,
    "voice_speed": 1.10,
    "trim_pauses": True
}


def is_valid_audio(file_path):
    return bool(file_path and os.path.exists(file_path) and os.path.getsize(file_path) > 1024)


def get_audio_duration(file_path):
    if not is_valid_audio(file_path):
        return 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, check=True, creationflags=SUBPROC_FLAGS
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def format_srt_time(seconds):
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_scene_number(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    m = re.search(r'(?:сцена|scene|shot|кадр)[\s_-]*(\d+)', base, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m_any = re.search(r'(\d+)', base)
    return int(m_any.group(1)) if m_any else 9999


def parse_flexible_json(raw_text):
    txt = raw_text.strip()
    txt = txt.replace("\u00a0", " ").replace("\u200b", "").replace("“", '"').replace("”", '"')
    start_idx = txt.find("[")
    end_idx = txt.rfind("]")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        txt = txt[start_idx:end_idx + 1]
    txt = re.sub(r',\s*([}\]])', r'\1', txt)
    return json.loads(txt)


def normalize_slides_timeline(slides):
    cursor = 0.0
    for s in slides:
        if "start" in s and "end" in s and float(s["end"]) > float(s["start"]):
            st = round(float(s["start"]), 2)
            en = round(float(s["end"]), 2)
            s["start"] = st
            s["end"] = en
            s["duration"] = round(en - st, 2)
            cursor = en
        else:
            dur = max(0.4, round(float(s.get("duration", 3.0)), 2))
            s["start"] = round(cursor, 2)
            s["end"] = round(cursor + dur, 2)
            s["duration"] = dur
            cursor = s["end"]


class WhisperSRTWorker(QThread):
    status_sig = pyqtSignal(str)
    progress_sig = pyqtSignal(int)
    finished_sig = pyqtSignal(bool, str, str)

    def __init__(self, audio_path, srt_out_path, lang="ru"):
        super().__init__()
        self.audio_path = audio_path
        self.srt_out_path = srt_out_path
        self.lang = lang

    def run(self):
        try:
            self.progress_sig.emit(10)
            self.status_sig.emit("Загрузка локальной модели Whisper (tiny)...")
            import whisper
            model = whisper.load_model("tiny")

            self.progress_sig.emit(30)
            self.status_sig.emit("Распознавание речи и таймкодов...")

            result = model.transcribe(
                self.audio_path, language=self.lang,
                fp16=False, word_timestamps=True, verbose=False
            )
            self.progress_sig.emit(80)

            total_dur = get_audio_duration(self.audio_path)
            segments = result.get("segments", [])
            srt_blocks = []
            ai_lines = [f"TOTAL_AUDIO_DURATION: {total_dur:.2f}s"]
            idx = 1

            all_words = []
            for seg in segments:
                for w in seg.get("words", []):
                    txt = w.get("word", "").strip()
                    if txt:
                        all_words.append({
                            "word": txt,
                            "start": float(w.get("start", seg.get("start", 0.0))),
                            "end": float(w.get("end", seg.get("end", 0.0)))
                        })

            if all_words:
                chunk = []
                for w in all_words:
                    chunk.append(w)
                    dur_now = chunk[-1]["end"] - chunk[0]["start"]
                    ends_punct = any(chunk[-1]["word"].endswith(p) for p in (".", "!", "?", ":", "—"))
                    if len(chunk) >= 6 or (len(chunk) >= 3 and (dur_now >= 1.8 or ends_punct)):
                        t0, t1 = chunk[0]["start"], chunk[-1]["end"]
                        phrase = " ".join(x["word"] for x in chunk)
                        srt_blocks.append(f"{idx}\n{format_srt_time(t0)} --> {format_srt_time(t1)}\n{phrase}\n")
                        ai_lines.append(f"[{t0:05.2f}s -> {t1:05.2f}s] {phrase}")
                        idx += 1
                        chunk = []
                if chunk:
                    t0, t1 = chunk[0]["start"], max(chunk[-1]["end"], total_dur)
                    phrase = " ".join(x["word"] for x in chunk)
                    srt_blocks.append(f"{idx}\n{format_srt_time(t0)} --> {format_srt_time(t1)}\n{phrase}\n")
                    ai_lines.append(f"[{t0:05.2f}s -> {t1:05.2f}s] {phrase}")
            else:
                for seg in segments:
                    t0, t1 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
                    phrase = seg.get("text", "").strip()
                    srt_blocks.append(f"{idx}\n{format_srt_time(t0)} --> {format_srt_time(t1)}\n{phrase}\n")
                    ai_lines.append(f"[{t0:05.2f}s -> {t1:05.2f}s] {phrase}")
                    idx += 1

            with open(self.srt_out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(srt_blocks))

            self.progress_sig.emit(100)
            self.finished_sig.emit(True, "\n".join(ai_lines), self.srt_out_path)
        except Exception as e:
            self.finished_sig.emit(False, f"Ошибка Whisper:\n{e}", "")


class RenderWorker(QThread):
    progress_sig = pyqtSignal(int, str)
    finished_sig = pyqtSignal(bool, str)

    def __init__(self, slides, loaded_images, layout_cfg, audio_path, out_path):
        super().__init__()
        self.slides = json.loads(json.dumps(slides))
        self.loaded_images = loaded_images
        self.layout_cfg = dict(layout_cfg)
        self.audio_path = audio_path
        self.out_path = out_path

    def check_nvenc(self):
        try:
            r = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                 "-c:v", "h264_nvenc", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, creationflags=SUBPROC_FLAGS
            )
            return r.returncode == 0
        except Exception:
            return False

    def prepare_scaled_cache(self, target_w, target_h):
        scaled_cache = {}
        max_w, max_h = int(target_w * 1.35), int(target_h * 1.35)
        for path, item in self.loaded_images.items():
            if isinstance(item, list):
                scaled_cache[path] = item
            elif item and not item.isNull():
                scaled = item.scaled(max_w, max_h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                scaled_cache[path] = scaled.convertToFormat(QImage.Format_RGB32)
        return scaled_cache

    def render_single_frame(self, args):
        width, height, slide, prev_slide, prog, blend_alpha, render_images = args
        img = QImage(width, height, QImage.Format_RGB32)
        painter = QPainter(img)
        assets_engine.draw_frame(painter, width, height, slide, prog, self.layout_cfg, render_images, None)

        if prev_slide is not None and blend_alpha > 0.02:
            prev_img = QImage(width, height, QImage.Format_RGB32)
            p_prev = QPainter(prev_img)
            assets_engine.draw_frame(p_prev, width, height, prev_slide, 1.0, self.layout_cfg, render_images, None)
            p_prev.end()
            painter.setOpacity(blend_alpha)
            painter.drawImage(0, 0, prev_img)
            painter.setOpacity(1.0)

        painter.end()
        ptr = img.bits()
        ptr.setsize(img.byteCount())
        return ptr.asstring()

    def run(self):
        width, height, fps = 1080, 1920, 30
        normalize_slides_timeline(self.slides)

        total_sec = float(self.slides[-1]["end"]) + 0.85 if self.slides else 5.0
        audio_dur = get_audio_duration(self.audio_path)
        if audio_dur > 1.0:
            total_sec = max(total_sec, audio_dur + 0.35)

        total_frames = max(1, int(round(total_sec * fps)))

        tasks = []
        s_idx = 0
        num_s = len(self.slides)
        for f_idx in range(total_frames):
            t = f_idx / float(fps)
            while s_idx + 1 < num_s and t >= float(self.slides[s_idx]["end"]):
                s_idx += 1
            s = self.slides[s_idx]
            prev_s = self.slides[s_idx - 1] if s_idx > 0 else None
            st = float(s["start"])
            dur = max(0.05, float(s["end"]) - st)
            elapsed = max(0.0, t - st)
            prog = min(1.0, elapsed / dur)

            blend_alpha = 0.0
            if prev_s is not None and elapsed < 0.24:
                if (s.get("type") in assets_engine.NO_HUD_TYPES) != (prev_s.get("type") in assets_engine.NO_HUD_TYPES):
                    blend_alpha = 1.0 - (elapsed / 0.24)

            tasks.append((width, height, s, prev_s, prog, blend_alpha))

        cpu_cores = os.cpu_count() or 4
        self.progress_sig.emit(1, f"Кэширование медиафайлов (CPU {cpu_cores} cores + NVENC check)...")
        render_images = self.prepare_scaled_cache(width, height)
        use_nvenc = self.check_nvenc()

        encoder_name = f"CPU ({cpu_cores} cores) + NVIDIA NVENC" if use_nvenc else f"CPU ({cpu_cores} cores, libx264 Ultrafast)"
        vcodec = [
            "h264_nvenc", "-preset", "p2", "-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M"
        ] if use_nvenc else [
            "libx264", "-preset", "ultrafast", "-crf", "20", "-threads", "4"
        ]

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{width}x{height}", "-pix_fmt", "bgra", "-r", str(fps), "-i", "-"
        ]
        if is_valid_audio(self.audio_path):
            cmd += ["-i", self.audio_path, "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-t", f"{total_sec:.2f}", "-c:v"] + vcodec + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", self.out_path]

        log_path = os.path.join(PROJECTS_DIR, "ffmpeg_render.log")
        log_file = open(log_path, "w", encoding="utf-8")

        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=log_file, bufsize=10**7, creationflags=SUBPROC_FLAGS
            )
        except FileNotFoundError:
            log_file.close()
            self.finished_sig.emit(False, "FFmpeg не найден в PATH!")
            return

        rendered = 0
        batch_size = 8

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for i in range(0, total_frames, batch_size):
                    batch = [
                        (w, h, s, ps, p, ba, render_images)
                        for (w, h, s, ps, p, ba) in tasks[i:i + batch_size]
                    ]
                    for frame_bytes in pool.map(self.render_single_frame, batch):
                        try:
                            proc.stdin.write(frame_bytes)
                        except (BrokenPipeError, OSError):
                            break
                        rendered += 1

                    pct = int((rendered / total_frames) * 100)
                    self.progress_sig.emit(pct, f"[{encoder_name}] Кадр {rendered}/{total_frames} ({pct}%)")

            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()
            log_file.close()
            render_images.clear()
            gc.collect()

            if proc.returncode != 0:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    err = f.read()[-400:]
                self.finished_sig.emit(False, f"Ошибка FFmpeg:\n{err}")
                return

            self.finished_sig.emit(True, f"Видео готово ({encoder_name}):\n{self.out_path}")
        except Exception as e:
            try:
                proc.kill()
            except Exception:
                pass
            log_file.close()
            render_images.clear()
            gc.collect()
            self.finished_sig.emit(False, f"Ошибка рендера:\n{e}")


class PreviewCanvas(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setMinimumSize(360, 640)
        self.setMouseTracking(True)
        self.anim_prog = 0.65
        self.hit_boxes = {}
        self.hover_target = None
        self.drag_target = None
        self.drag_offset = QPointF(0, 0)
        self.frame_rect = QRect(0, 0, 360, 640)

        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self.tick_preview_anim)
        self.preview_timer.start(55)

    def tick_preview_anim(self):
        if not self.drag_target and self.editor.live_anim_chk.isChecked():
            self.anim_prog = (self.anim_prog + 0.018) % 1.0
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()
        target_w = min(w, int(h * 9 / 16))
        target_h = int(target_w * 16 / 9)
        ox = (w - target_w) // 2
        oy = (h - target_h) // 2
        self.frame_rect = QRect(ox, oy, target_w, target_h)

        painter.fillRect(0, 0, w, h, QColor("#070709"))
        painter.save()
        painter.translate(ox, oy)
        painter.setClipRect(0, 0, target_w, target_h)

        slide = self.editor.get_current_slide()
        if slide:
            self.hit_boxes = assets_engine.draw_frame(
                painter, target_w, target_h, slide,
                self.anim_prog, self.editor.layout_cfg,
                self.editor.preview_images, self.hover_target
            )
        painter.restore()

    def _to_local(self, pos):
        return QPointF(pos.x() - self.frame_rect.x(), pos.y() - self.frame_rect.y())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            lp = self._to_local(event.pos())
            for name in ("extra", "title", "hud", "sub"):
                rect = self.hit_boxes.get(name)
                if rect and rect.contains(lp):
                    self.drag_target = name
                    self.drag_offset = lp - rect.center()
                    self.setCursor(Qt.ClosedHandCursor)
                    return

    def mouseMoveEvent(self, event):
        lp = self._to_local(event.pos())
        fw = max(1, self.frame_rect.width())
        fh = max(1, self.frame_rect.height())

        if self.drag_target:
            new_center = lp - self.drag_offset
            nx = max(0.05, min(0.95, new_center.x() / fw))
            ny = max(0.02, min(0.94, new_center.y() / fh))
            slide = self.editor.get_current_slide()

            if self.drag_target == "hud":
                rect_h = self.hit_boxes["hud"].height() if "hud" in self.hit_boxes else 0
                hy = max(0.01, min(0.85, (new_center.y() - rect_h / 2.0) / fh))
                self.editor.layout_cfg["hud_x"] = round(nx, 3)
                self.editor.layout_cfg["hud_y"] = round(hy, 3)
            elif self.drag_target in ("title", "extra"):
                if slide:
                    slide[f"{self.drag_target}_x"] = round(nx, 3)
                    slide[f"{self.drag_target}_y"] = round(ny, 3)
            elif self.drag_target == "sub":
                self.editor.layout_cfg["sub_x"] = round(nx, 3)
                self.editor.layout_cfg["sub_y"] = round(ny, 3)

            self.update()
            return

        prev_hover = self.hover_target
        self.hover_target = None
        for name in ("extra", "title", "hud", "sub"):
            rect = self.hit_boxes.get(name)
            if rect and rect.contains(lp):
                self.hover_target = name
                break

        self.setCursor(Qt.OpenHandCursor if self.hover_target else Qt.ArrowCursor)
        if prev_hover != self.hover_target:
            self.update()

    def mouseReleaseEvent(self, event):
        if self.drag_target:
            self.drag_target = None
            self.setCursor(Qt.OpenHandCursor if self.hover_target else Qt.ArrowCursor)
            self.editor.json_edit.setPlainText(json.dumps(self.editor.slides, ensure_ascii=False, indent=2))
            self.editor.autosave_project()

    def wheelEvent(self, event):
        target = self.hover_target or "title"
        delta = 0.05 if event.angleDelta().y() > 0 else -0.05
        slide = self.editor.get_current_slide()
        key = f"{target}_scale"

        if target in ("title", "extra") and slide:
            cur = float(slide.get(key, self.editor.layout_cfg.get(key, 1.0)))
            slide[key] = round(max(0.40, min(1.80, cur + delta)), 2)
        else:
            cur = float(self.editor.layout_cfg.get(key, 1.0))
            self.editor.layout_cfg[key] = round(max(0.45, min(1.60, cur + delta)), 2)

        self.editor.json_edit.setPlainText(json.dumps(self.editor.slides, ensure_ascii=False, indent=2))
        self.editor.autosave_project()
        self.update()


class StickmanVideoEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vertical Video Studio PRO — Procedural Motion & FFmpeg Engine")
        self.resize(1470, 920)
        self.setAcceptDrops(True)

        self.current_project_name = "reel_project_1"
        self.slides = []
        self.layout_cfg = dict(DEFAULT_LAYOUT)
        self.loaded_images = {}
        self.preview_images = {}
        self.image_pool = {}

        self.audio_path = ""
        self.clean_voice_path = ""
        self.orig_voice_path = ""
        self.orig_voice_dur = 0.0

        self.cleanup_zero_kb_files()
        self.apply_dark_theme()
        self.init_ui()
        self.load_or_init_default_project()

    def reload_assets_engine(self):
        """Горячая перезагрузка assets_engine.py на лету без закрытия программы!"""
        try:
            importlib.reload(assets_engine)
            cur_t = self.type_combo.currentText()
            cur_c = self.cam_combo.currentText()
            self.type_combo.blockSignals(True)
            self.cam_combo.blockSignals(True)
            self.type_combo.clear()
            self.type_combo.addItems(assets_engine.ASSET_TYPES)
            self.type_combo.setCurrentText(cur_t)
            self.cam_combo.clear()
            self.cam_combo.addItems(assets_engine.CAM_MODES)
            self.cam_combo.setCurrentText(cur_c)
            self.type_combo.blockSignals(False)
            self.cam_combo.blockSignals(False)
            self.canvas.update()
            self.status_lbl.setText("Модуль assets_engine.py успешно перезагружен на лету!")
            QMessageBox.information(self, "Hot-Reload", f"Движок ассетов перезагружен!\nДоступно шаблонов: {len(assets_engine.ASSET_TYPES)}")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка в assets_engine.py", str(e))

    def cleanup_zero_kb_files(self):
        try:
            for fname in os.listdir(PROJECTS_DIR):
                if fname.endswith((".mp3", ".wav")):
                    fpath = os.path.join(PROJECTS_DIR, fname)
                    if os.path.getsize(fpath) == 0:
                        os.remove(fpath)
        except Exception:
            pass

    def cache_image_pair(self, path):
        if not path or not os.path.exists(path):
            return False
        if path.lower().endswith((".mp4", ".mov", ".webm")):
            frames_1080 = []
            frames_540 = []
            w, h = 540, 960
            cmd = [
                "ffmpeg", "-loglevel", "error", "-i", path,
                "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30",
                "-f", "rawvideo", "-pix_fmt", "bgra", "-"
            ]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=SUBPROC_FLAGS)
                frame_bytes = w * h * 4
                while len(frames_540) < 180:
                    raw = proc.stdout.read(frame_bytes)
                    if len(raw) < frame_bytes:
                        break
                    qimg = QImage(raw, w, h, QImage.Format_RGB32).copy()
                    frames_540.append(qimg)
                    frames_1080.append(qimg.scaled(1080, 1920, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
                proc.kill()
            except Exception:
                return False
            if not frames_540:
                return False
            self.loaded_images[path] = frames_1080
            self.preview_images[path] = frames_540
            return True

        qimg = QImage(path)
        if qimg.isNull():
            return False
        self.loaded_images[path] = qimg
        self.preview_images[path] = qimg.scaled(
            540, 960, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        ).convertToFormat(QImage.Format_RGB32)
        return True

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #141419; color: #f0f0f5; font-family: Segoe UI, sans-serif; font-size: 13px; }
            QGroupBox { border: 1px solid #2b2b36; border-radius: 8px; margin-top: 8px; padding-top: 12px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #ef3b85; }
            QPushButton { background-color: #282834; border: 1px solid #3a3a4c; padding: 6px 10px; border-radius: 6px; font-weight: bold; }
            QPushButton:hover { background-color: #363646; }
            QPushButton#primary { background-color: #ef3b85; color: white; border: none; }
            QPushButton#primary:hover { background-color: #ff529a; }
            QListWidget, QTextEdit, QLineEdit, QComboBox, QDoubleSpinBox {
                background-color: #1d1d26; border: 1px solid #323242; border-radius: 6px; padding: 5px; color: #fff;
            }
            QListWidget::item:selected { background-color: #ef3b85; color: white; font-weight: bold; }
        """)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # --- ЛЕВАЯ ПАНЕЛЬ ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        proj_group = QGroupBox("📁 Проекты + Модуль Ассетов")
        pg_layout = QHBoxLayout(proj_group)
        self.proj_combo = QComboBox()
        self.proj_combo.currentTextChanged.connect(self.on_project_switched)
        pg_layout.addWidget(self.proj_combo, stretch=1)

        btn_new_proj = QPushButton("➕ Новый")
        btn_new_proj.clicked.connect(self.create_new_project)
        pg_layout.addWidget(btn_new_proj)

        btn_reload_eng = QPushButton("🔄 Hot-Reload Ассетов")
        btn_reload_eng.setStyleSheet("color:#00e5ff; border:1px solid #00e5ff;")
        btn_reload_eng.clicked.connect(self.reload_assets_engine)
        pg_layout.addWidget(btn_reload_eng)

        btn_open_dir = QPushButton("📂")
        btn_open_dir.clicked.connect(lambda: os.startfile(PROJECTS_DIR) if os.name == "nt" else None)
        pg_layout.addWidget(btn_open_dir)
        left_layout.addWidget(proj_group)

        btn_load_imgs = QPushButton("🖼 Загрузить все картинки и видео .mp4 пачкой (17+ штук)")
        btn_load_imgs.setObjectName("primary")
        btn_load_imgs.clicked.connect(self.load_images_dialog)
        left_layout.addWidget(btn_load_imgs)

        self.pool_status_lbl = QLabel("В пуле медиа: 0 шт.")
        self.pool_status_lbl.setStyleSheet("color:#2ecc71; font-weight:bold;")
        left_layout.addWidget(self.pool_status_lbl)

        self.slide_list = QListWidget()
        self.slide_list.currentRowChanged.connect(self.on_slide_selected)
        left_layout.addWidget(self.slide_list)

        btn_bind_img = QPushButton("🔄 Выбрать картинку / видео .mp4 для слайда вручную")
        btn_bind_img.clicked.connect(self.bind_image_manually)
        left_layout.addWidget(btn_bind_img)

        json_group = QGroupBox("JSON-сценарий (Безотказный парсер)")
        jg_layout = QVBoxLayout(json_group)
        self.json_edit = QTextEdit()
        self.json_edit.setPlaceholderText("Вставь сюда массив JSON [...] или нажми кнопку ниже...")
        jg_layout.addWidget(self.json_edit)

        btn_paste_json = QPushButton("📋 Вставить из буфера и Применить JSON (1 клик)")
        btn_paste_json.setObjectName("primary")
        btn_paste_json.clicked.connect(self.paste_and_apply_json)
        jg_layout.addWidget(btn_paste_json)

        btn_apply_json = QPushButton("⚡ Применить текст из окна выше")
        btn_apply_json.clicked.connect(self.apply_json_text)
        jg_layout.addWidget(btn_apply_json)
        left_layout.addWidget(json_group)

        splitter.addWidget(left_panel)

        # --- ЦЕНТРАЛЬНАЯ ПАНЕЛЬ ---
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)

        hint_lbl = QLabel(
            "💡 <b>Модульная система:</b> Все шаблоны и звуки подгружаются из <code>assets_engine.py</code>.<br>"
            "Мышкой двигай блоки, колесиком меняй масштаб."
        )
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet("background:#1e1e2a; padding:8px; border-radius:6px; color:#00e5ff;")
        center_layout.addWidget(hint_lbl)

        self.canvas = PreviewCanvas(self)
        center_layout.addWidget(self.canvas, stretch=1)

        self.live_anim_chk = QCheckBox("▶ Крутить анимацию инфографики и видео на превью")
        self.live_anim_chk.setChecked(True)
        center_layout.addWidget(self.live_anim_chk)

        self.burn_sub_chk = QCheckBox("Показывать субтитры на видео (Выключено для CapCut)")
        self.burn_sub_chk.setChecked(False)
        self.burn_sub_chk.stateChanged.connect(self.on_burn_sub_toggled)
        center_layout.addWidget(self.burn_sub_chk)

        splitter.addWidget(center_panel)

        # --- ПРАВАЯ ПАНЕЛЬ ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        srt_group = QGroupBox("🎯 Озвучка, Регулятор Ускорения (1.00x–1.50x), SRT и SFX")
        sg_layout = QVBoxLayout(srt_group)
        self.audio_lbl = QLabel("Аудио не выбрано")
        self.audio_lbl.setStyleSheet("color:#00e5ff; font-weight:bold;")
        sg_layout.addWidget(self.audio_lbl)

        # Строка 1: Загрузка + прослушивание
        top_aud_row = QHBoxLayout()
        btn_load_audio = QPushButton("🎵 1. Загрузить Аудио")
        btn_load_audio.clicked.connect(self.load_audio_dialog)
        top_aud_row.addWidget(btn_load_audio)

        btn_reset_orig = QPushButton("↩ Исходник (1.0x)")
        btn_reset_orig.setToolTip("Вернуть исходную не ускоренную озвучку")
        btn_reset_orig.clicked.connect(self.restore_original_audio)
        top_aud_row.addWidget(btn_reset_orig)

        btn_listen_top = QPushButton("🎧 Прослушать")
        btn_listen_top.clicked.connect(self.listen_current_audio)
        top_aud_row.addWidget(btn_listen_top)
        sg_layout.addLayout(top_aud_row)

        # Строка 2: Ползунок и спинбокс множителя ускорения
        speed_ctrl_row = QHBoxLayout()
        speed_ctrl_row.addWidget(QLabel("Скорость:"))

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(80, 150)  # 0.80x .. 1.50x
        self.speed_slider.setSingleStep(1)
        self.speed_slider.setPageStep(5)
        self.speed_slider.setValue(110)
        self.speed_slider.valueChanged.connect(self.on_speed_slider_changed)
        speed_ctrl_row.addWidget(self.speed_slider, stretch=1)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.80, 1.50)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setSuffix("x")
        self.speed_spin.setValue(1.10)
        self.speed_spin.valueChanged.connect(self.on_speed_spin_changed)
        speed_ctrl_row.addWidget(self.speed_spin)

        self.trim_pauses_chk = QCheckBox("✂ Убирать паузы")
        self.trim_pauses_chk.setChecked(True)
        self.trim_pauses_chk.stateChanged.connect(self.update_speed_preview_label)
        speed_ctrl_row.addWidget(self.trim_pauses_chk)
        sg_layout.addLayout(speed_ctrl_row)

        # Строка 3: Живой индикатор секунд + кнопка применения
        speed_apply_row = QHBoxLayout()
        self.speed_preview_lbl = QLabel("⏱ Итог: загрузите аудио...")
        self.speed_preview_lbl.setStyleSheet("color:#f1c40f; font-weight:bold;")
        speed_apply_row.addWidget(self.speed_preview_lbl, stretch=1)

        self.btn_speed_trim = QPushButton("⚡ Применить 1.10x")
        self.btn_speed_trim.setStyleSheet("background:#2b3a4a; border:1px solid #00e5ff; color:#00e5ff;")
        self.btn_speed_trim.clicked.connect(self.speedup_and_trim_audio)
        speed_apply_row.addWidget(self.btn_speed_trim)
        sg_layout.addLayout(speed_apply_row)

        btn_make_srt = QPushButton("📝 2. Сделать SRT-разметку по секундам (Whisper)")
        btn_make_srt.setObjectName("primary")
        btn_make_srt.clicked.connect(self.run_whisper_srt)
        sg_layout.addWidget(btn_make_srt)

        self.srt_output_box = QTextEdit()
        self.srt_output_box.setPlaceholderText("Здесь появится точная помиллисекундная разметка...")
        self.srt_output_box.setMaximumHeight(75)
        sg_layout.addWidget(self.srt_output_box)

        btn_copy_srt = QPushButton("📋 3. СКОПИРОВАТЬ SRT ДЛЯ ЧАТА С ИИ")
        btn_copy_srt.clicked.connect(self.copy_srt_to_clipboard)
        sg_layout.addWidget(btn_copy_srt)

        vol_form = QFormLayout()
        self.bgm_slider = QSlider(Qt.Horizontal)
        self.bgm_slider.setRange(0, 200)
        self.bgm_slider.setValue(65)
        self.bgm_val_lbl = QLabel("65%")
        self.bgm_slider.valueChanged.connect(lambda v: self.bgm_val_lbl.setText(f"{v}%"))
        bgm_row = QHBoxLayout()
        bgm_row.addWidget(self.bgm_slider)
        bgm_row.addWidget(self.bgm_val_lbl)
        vol_form.addRow("Громкость дарк-баса:", bgm_row)

        self.sfx_slider = QSlider(Qt.Horizontal)
        self.sfx_slider.setRange(0, 200)
        self.sfx_slider.setValue(90)
        self.sfx_val_lbl = QLabel("90%")
        self.sfx_slider.valueChanged.connect(lambda v: self.sfx_val_lbl.setText(f"{v}%"))
        sfx_row = QHBoxLayout()
        sfx_row.addWidget(self.sfx_slider)
        sfx_row.addWidget(self.sfx_val_lbl)
        vol_form.addRow("Громкость SFX-ударов:", sfx_row)
        sg_layout.addLayout(vol_form)

        sfx_btn_row = QHBoxLayout()
        btn_sfx = QPushButton("🔊 4. Вшить / Пересобрать SFX с этой громкостью")
        btn_sfx.setStyleSheet("background:#1e3a2f; border:1px solid #2ecc71; color:#2ecc71;")
        btn_sfx.clicked.connect(self.apply_auto_sfx_mix)
        sfx_btn_row.addWidget(btn_sfx)
        sg_layout.addLayout(sfx_btn_row)

        right_layout.addWidget(srt_group)

        slide_group = QGroupBox("Настройка выбранного слайда")
        sl_form = QFormLayout(slide_group)

        self.type_combo = QComboBox()
        self.type_combo.addItems(assets_engine.ASSET_TYPES)
        self.type_combo.currentTextChanged.connect(self.update_current_slide_from_ui)
        sl_form.addRow("Тип кадра:", self.type_combo)

        self.cam_combo = QComboBox()
        self.cam_combo.addItems(assets_engine.CAM_MODES)
        self.cam_combo.currentTextChanged.connect(self.update_current_slide_from_ui)
        sl_form.addRow("Движение камеры:", self.cam_combo)

        time_layout = QHBoxLayout()
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0.0, 900.0)
        self.start_spin.setDecimals(2)
        self.start_spin.setSingleStep(0.10)
        self.start_spin.valueChanged.connect(self.update_current_slide_from_ui)
        time_layout.addWidget(QLabel("Старт:"))
        time_layout.addWidget(self.start_spin)

        self.end_spin = QDoubleSpinBox()
        self.end_spin.setRange(0.1, 900.0)
        self.end_spin.setDecimals(2)
        self.end_spin.setSingleStep(0.10)
        self.end_spin.valueChanged.connect(self.update_current_slide_from_ui)
        time_layout.addWidget(QLabel("Конец:"))
        time_layout.addWidget(self.end_spin)

        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(1.00, 1.40)
        self.zoom_spin.setDecimals(2)
        self.zoom_spin.setSingleStep(0.02)
        self.zoom_spin.valueChanged.connect(self.update_current_slide_from_ui)
        time_layout.addWidget(QLabel("Zoom:"))
        time_layout.addWidget(self.zoom_spin)
        sl_form.addRow(time_layout)

        hud_row = QHBoxLayout()
        self.cash_inp = QLineEdit()
        self.cash_inp.setPlaceholderText("HIDE = скрыть HUD")
        self.cash_inp.textEdited.connect(self.update_current_slide_from_ui)
        self.mem_inp = QLineEdit()
        self.mem_inp.textEdited.connect(self.update_current_slide_from_ui)
        self.risk_inp = QLineEdit()
        self.risk_inp.textEdited.connect(self.update_current_slide_from_ui)
        hud_row.addWidget(self.cash_inp)
        hud_row.addWidget(self.mem_inp)
        hud_row.addWidget(self.risk_inp)
        sl_form.addRow("HUD (HIDE=скрыть):", hud_row)

        self.tag_inp = QLineEdit()
        self.tag_inp.textEdited.connect(self.update_current_slide_from_ui)
        sl_form.addRow("Тег / Надпись сверху:", self.tag_inp)

        self.title_inp = QTextEdit()
        self.title_inp.setMaximumHeight(46)
        self.title_inp.textChanged.connect(self.update_current_slide_from_ui)
        sl_form.addRow("Блок 1 (Заголовок):", self.title_inp)

        self.extra_inp = QLineEdit()
        self.extra_inp.textEdited.connect(self.update_current_slide_from_ui)
        sl_form.addRow("Блок 2 (Розовая строка/Штамп):", self.extra_inp)

        right_layout.addWidget(slide_group)

        render_group = QGroupBox("Гибридный рендер (Multi-Thread CPU + NVIDIA NVENC)")
        rg_layout = QVBoxLayout(render_group)
        self.progress_bar = QProgressBar()
        rg_layout.addWidget(self.progress_bar)
        self.status_lbl = QLabel("Готов к работе")
        rg_layout.addWidget(self.status_lbl)

        btn_render = QPushButton("🚀 СРЕНДЕРИТЬ MP4 (1080x1920)")
        btn_render.setObjectName("primary")
        btn_render.setMinimumHeight(40)
        btn_render.clicked.connect(self.start_render)
        rg_layout.addWidget(btn_render)

        right_layout.addWidget(render_group)
        splitter.addWidget(right_panel)
        splitter.setSizes([390, 460, 560])

    # --- Синхронизация ползунка скорости и живой расчёт секунд ---
    def on_speed_slider_changed(self, val):
        factor = round(val / 100.0, 2)
        self.speed_spin.blockSignals(True)
        self.speed_spin.setValue(factor)
        self.speed_spin.blockSignals(False)
        self.update_speed_preview_label()

    def on_speed_spin_changed(self, val):
        factor = round(float(val), 2)
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(int(round(factor * 100)))
        self.speed_slider.blockSignals(False)
        self.update_speed_preview_label()

    def update_speed_preview_label(self):
        factor = round(self.speed_spin.value(), 2)
        trim_on = self.trim_pauses_chk.isChecked()
        self.btn_speed_trim.setText(f"⚡ Применить {factor:.2f}x" + (" + паузы" if trim_on else ""))

        base_path = self.orig_voice_path if is_valid_audio(self.orig_voice_path) else (
            self.clean_voice_path if is_valid_audio(self.clean_voice_path) else self.audio_path
        )
        if not is_valid_audio(base_path):
            self.speed_preview_lbl.setText(f"⏱ Выбрано {factor:.2f}x (сначала загрузите аудио)")
            return

        if self.orig_voice_dur <= 0.1:
            self.orig_voice_dur = get_audio_duration(base_path)

        est_speed_only = self.orig_voice_dur / max(0.5, factor)
        if trim_on:
            # Ориентировочная оценка со срезом пауз (~5-8% экономии на паузах)
            est_trimmed = est_speed_only * 0.94
            self.speed_preview_lbl.setText(
                f"⏱ При {factor:.2f}x станет: ~{est_trimmed:.2f}с (без среза пауз: {est_speed_only:.2f}с | исходник: {self.orig_voice_dur:.2f}с)"
            )
        else:
            self.speed_preview_lbl.setText(
                f"⏱ При {factor:.2f}x станет ровно: {est_speed_only:.2f} сек (исходник: {self.orig_voice_dur:.2f} сек)"
            )

    # --- Управление проектами ---
    def get_project_file(self, name=None):
        pname = name or self.current_project_name
        return os.path.join(PROJECTS_DIR, f"{pname}.json")

    def refresh_projects_combo(self):
        self.proj_combo.blockSignals(True)
        self.proj_combo.clear()
        files = [f[:-5] for f in sorted(os.listdir(PROJECTS_DIR)) if f.endswith(".json")]
        if self.current_project_name not in files:
            files.insert(0, self.current_project_name)
        self.proj_combo.addItems(files)
        self.proj_combo.setCurrentText(self.current_project_name)
        self.proj_combo.blockSignals(False)

    def load_or_init_default_project(self):
        self.refresh_projects_combo()
        pfile = self.get_project_file()
        if os.path.exists(pfile):
            self.load_project_from_disk(self.current_project_name)
        else:
            self.slides = []
            self.autosave_project()
            self.refresh_slide_list()

    def autosave_project(self):
        self.layout_cfg["burn_subtitles"] = self.burn_sub_chk.isChecked()
        self.layout_cfg["bgm_vol"] = self.bgm_slider.value()
        self.layout_cfg["sfx_vol"] = self.sfx_slider.value()
        self.layout_cfg["voice_speed"] = round(self.speed_spin.value(), 2)
        self.layout_cfg["trim_pauses"] = self.trim_pauses_chk.isChecked()
        data = {
            "project_name": self.current_project_name,
            "layout_cfg": self.layout_cfg,
            "audio_path": self.audio_path if is_valid_audio(self.audio_path) else "",
            "clean_voice_path": self.clean_voice_path if is_valid_audio(self.clean_voice_path) else "",
            "orig_voice_path": self.orig_voice_path if is_valid_audio(self.orig_voice_path) else "",
            "image_pool": self.image_pool,
            "srt_cache": self.srt_output_box.toPlainText(),
            "slides": self.slides
        }
        try:
            with open(self.get_project_file(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_project_from_disk(self, proj_name):
        pfile = self.get_project_file(proj_name)
        if not os.path.exists(pfile):
            return
        try:
            with open(pfile, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.current_project_name = proj_name
            self.slides = data.get("slides", [])
            normalize_slides_timeline(self.slides)
            self.layout_cfg = data.get("layout_cfg", dict(DEFAULT_LAYOUT))
            self.burn_sub_chk.setChecked(self.layout_cfg.get("burn_subtitles", False))
            self.bgm_slider.setValue(int(self.layout_cfg.get("bgm_vol", 65)))
            self.sfx_slider.setValue(int(self.layout_cfg.get("sfx_vol", 90)))

            saved_speed = float(self.layout_cfg.get("voice_speed", 1.10))
            self.speed_spin.setValue(saved_speed)
            self.trim_pauses_chk.setChecked(bool(self.layout_cfg.get("trim_pauses", True)))

            self.srt_output_box.setPlainText(data.get("srt_cache", ""))

            saved_pool = data.get("image_pool", {})
            self.image_pool = {int(k): v for k, v in saved_pool.items() if os.path.exists(v)}
            for p in self.image_pool.values():
                self.cache_image_pair(p)
            for s in self.slides:
                ip = s.get("image_path", "")
                if ip and os.path.exists(ip):
                    self.cache_image_pair(ip)

            self.pool_status_lbl.setText(f"В пуле медиа: {len(self.image_pool)} шт.")

            saved_audio = data.get("audio_path", "")
            saved_clean = data.get("clean_voice_path", "")
            saved_orig = data.get("orig_voice_path", "")

            self.clean_voice_path = saved_clean if is_valid_audio(saved_clean) else (saved_audio if is_valid_audio(saved_audio) else "")
            self.orig_voice_path = saved_orig if is_valid_audio(saved_orig) else self.clean_voice_path
            self.orig_voice_dur = get_audio_duration(self.orig_voice_path)

            if is_valid_audio(saved_audio):
                self.audio_path = saved_audio
                dur = get_audio_duration(self.audio_path)
                self.audio_lbl.setText(f"Активное аудио ({dur:.2f}с): {os.path.basename(self.audio_path)}")
            else:
                self.audio_path = ""
                self.audio_lbl.setText("Аудио не выбрано")

            self.update_speed_preview_label()
            self.json_edit.setPlainText(json.dumps(self.slides, ensure_ascii=False, indent=2))
            self.refresh_slide_list()
            self.canvas.update()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка загрузки проекта", str(e))

    def on_project_switched(self, name):
        if name and name != self.current_project_name:
            self.load_project_from_disk(name)

    def create_new_project(self):
        name, ok = QInputDialog.getText(self, "Новый рилс", "Введите название нового проекта:")
        if ok and name.strip():
            clean_name = re.sub(r'[^\w\-_]', '_', name.strip())
            self.current_project_name = clean_name
            self.slides = []
            self.image_pool = {}
            self.layout_cfg = dict(DEFAULT_LAYOUT)
            self.audio_path = ""
            self.clean_voice_path = ""
            self.orig_voice_path = ""
            self.orig_voice_dur = 0.0
            self.srt_output_box.clear()
            self.json_edit.clear()
            self.pool_status_lbl.setText("В пуле медиа: 0 шт.")
            self.update_speed_preview_label()
            self.autosave_project()
            self.refresh_projects_combo()
            self.refresh_slide_list()
            self.canvas.update()

    def on_burn_sub_toggled(self):
        self.autosave_project()
        self.canvas.update()

    # --- Пул картинок и видео .mp4 ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        self.auto_assign_images(files)

    def load_images_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите все картинки и видео .mp4 для рилса", "",
            "Media (*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.webm)"
        )
        if files:
            self.auto_assign_images(files)

    def auto_assign_images(self, file_paths):
        valid_files = [p for p in file_paths if self.cache_image_pair(p)]
        if not valid_files:
            return

        valid_files.sort(key=lambda p: (
            extract_scene_number(p),
            1 if p.lower().endswith((".mp4", ".mov", ".webm")) else 0,
            os.path.basename(p)
        ))

        for idx, path in enumerate(valid_files):
            sc_num = extract_scene_number(path)
            if sc_num == 9999:
                sc_num = idx + 1
            self.image_pool[sc_num] = path

        self.pool_status_lbl.setText(f"В пуле медиа: {len(self.image_pool)} шт. (Номера: {sorted(self.image_pool.keys())})")

        if len(self.slides) <= 1:
            self.slides = []
            cursor = 0.0
            for sc_num in sorted(self.image_pool.keys()):
                self.slides.append({
                    "scene_match": sc_num,
                    "cam_mode": "wide",
                    "type": "image",
                    "start": round(cursor, 2),
                    "end": round(cursor + 3.0, 2),
                    "duration": 3.0,
                    "zoom": 1.10,
                    "cash": "$10,000,000", "mem": "0 / 12", "risk": "0%",
                    "tag": f"СЦЕНА {sc_num}",
                    "title": "", "extra": "",
                    "image_path": self.image_pool[sc_num]
                })
                cursor += 3.0
        else:
            self.relink_loaded_images_to_slides()

        self.json_edit.setPlainText(json.dumps(self.slides, ensure_ascii=False, indent=2))
        self.autosave_project()
        self.refresh_slide_list()
        self.on_slide_selected(0)
        self.canvas.update()
        QMessageBox.information(self, "Медиа добавлены", f"Загружено в пул: {len(self.image_pool)} шт.")

    def relink_loaded_images_to_slides(self):
        if not self.image_pool:
            return
        sorted_pool_paths = [self.image_pool[k] for k in sorted(self.image_pool.keys())]
        img_slide_counter = 0

        for s in self.slides:
            if s.get("type") in assets_engine.NO_HUD_TYPES and s.get("type") not in ("gta_title", "choice_buttons"):
                continue
            sc_num = s.get("scene_match", 0)
            fb_num = s.get("fallback_match", sc_num)
            if sc_num in self.image_pool:
                s["image_path"] = self.image_pool[sc_num]
            elif fb_num in self.image_pool:
                s["image_path"] = self.image_pool[fb_num]
            elif sorted_pool_paths:
                s["image_path"] = sorted_pool_paths[img_slide_counter % len(sorted_pool_paths)]
            img_slide_counter += 1

    def bind_image_manually(self):
        idx = self.slide_list.currentRow()
        if idx < 0 or idx >= len(self.slides):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Выберите картинку или .mp4", "", "Media (*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.webm)")
        if path and self.cache_image_pair(path):
            self.slides[idx]["image_path"] = path
            self.json_edit.setPlainText(json.dumps(self.slides, ensure_ascii=False, indent=2))
            self.autosave_project()
            self.refresh_slide_list()
            self.on_slide_selected(idx)
            self.canvas.update()

    def refresh_slide_list(self):
        if self.slides:
            normalize_slides_timeline(self.slides)
        cur = self.slide_list.currentRow()
        self.slide_list.blockSignals(True)
        self.slide_list.clear()
        for i, s in enumerate(self.slides):
            st_type = s.get("type", "image")
            is_info = st_type in assets_engine.NO_HUD_TYPES and st_type not in ("gta_title", "choice_buttons")
            ip = s.get("image_path", "")
            is_vid = ip.lower().endswith((".mp4", ".mov", ".webm")) if ip else False
            has_img = "📊" if is_info else ("🎥" if (is_vid and os.path.exists(ip)) else ("✅" if (ip and os.path.exists(ip)) else "❌"))
            t0, t1 = float(s.get("start", 0.0)), float(s.get("end", 3.0))
            sc_num = s.get("scene_match", 0)
            sc_str = f"Сц.{sc_num}" if sc_num else st_type
            item_text = f"{i+1}. {has_img} [{t0:05.2f}s->{t1:05.2f}s] ({sc_str}/{s.get('cam_mode','wide')}) {s.get('tag', '')}"
            self.slide_list.addItem(QListWidgetItem(item_text))
        self.slide_list.blockSignals(False)
        if self.slides:
            self.slide_list.setCurrentRow(max(0, min(cur, len(self.slides) - 1)))

    def get_current_slide(self):
        idx = self.slide_list.currentRow()
        if 0 <= idx < len(self.slides):
            return self.slides[idx]
        return None

    def on_slide_selected(self, idx):
        if idx < 0 or idx >= len(self.slides):
            return
        s = self.slides[idx]
        self._updating_ui = True
        self.type_combo.setCurrentText(s.get("type", "image"))
        self.cam_combo.setCurrentText(s.get("cam_mode", "wide"))
        self.start_spin.setValue(float(s.get("start", 0.0)))
        self.end_spin.setValue(float(s.get("end", 3.0)))
        self.zoom_spin.setValue(float(s.get("zoom", 1.10)))
        self.cash_inp.setText(s.get("cash", ""))
        self.mem_inp.setText(s.get("mem", ""))
        self.risk_inp.setText(s.get("risk", ""))
        self.tag_inp.setText(s.get("tag", ""))
        self.title_inp.setPlainText(s.get("title", ""))
        self.extra_inp.setText(s.get("extra", ""))
        self._updating_ui = False
        self.canvas.update()

    def update_current_slide_from_ui(self):
        if getattr(self, "_updating_ui", False):
            return
        s = self.get_current_slide()
        if not s:
            return
        s["type"] = self.type_combo.currentText()
        s["cam_mode"] = self.cam_combo.currentText()
        s["start"] = round(self.start_spin.value(), 2)
        s["end"] = max(round(s["start"] + 0.3, 2), round(self.end_spin.value(), 2))
        s["duration"] = round(s["end"] - s["start"], 2)
        s["zoom"] = round(self.zoom_spin.value(), 2)
        s["cash"] = self.cash_inp.text()
        s["mem"] = self.mem_inp.text()
        s["risk"] = self.risk_inp.text()
        s["tag"] = self.tag_inp.text()
        s["title"] = self.title_inp.toPlainText()
        s["extra"] = self.extra_inp.text()
        self.json_edit.setPlainText(json.dumps(self.slides, ensure_ascii=False, indent=2))
        self.autosave_project()
        self.refresh_slide_list()
        self.canvas.update()

    def paste_and_apply_json(self):
        clip_txt = QApplication.clipboard().text()
        if not clip_txt.strip():
            QMessageBox.warning(self, "Буфер пуст", "Сначала скопируйте JSON-сценарий в буфер обмена!")
            return
        self.json_edit.setPlainText(clip_txt)
        self.apply_json_text()

    def apply_json_text(self):
        try:
            data = parse_flexible_json(self.json_edit.toPlainText())
            if isinstance(data, list):
                self.slides = data
                normalize_slides_timeline(self.slides)
                self.relink_loaded_images_to_slides()
                self.json_edit.setPlainText(json.dumps(self.slides, ensure_ascii=False, indent=2))
                self.autosave_project()
                self.refresh_slide_list()
                self.on_slide_selected(0)
                self.canvas.update()
                QMessageBox.information(
                    self, "JSON успешно применен!",
                    f"Загружено сцен по таймкодам: {len(self.slides)}\n"
                    f"Привязано медиа из пула: {len(self.image_pool)} шт."
                )
        except Exception as e:
            QMessageBox.warning(self, "Ошибка чтения JSON", f"Проверьте скопированный блок JSON:\n{e}")

    # --- Аудио: Регулируемое ускорение (1.00x - 1.50x), SRT и Микшер SFX ---
    def load_audio_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите аудиофайл", "", "Audio (*.mp3 *.wav *.m4a)")
        if is_valid_audio(path):
            self.audio_path = path
            self.clean_voice_path = path
            self.orig_voice_path = path
            self.orig_voice_dur = get_audio_duration(path)
            self.audio_lbl.setText(f"Исходное аудио ({self.orig_voice_dur:.2f}с): {os.path.basename(path)}")
            self.update_speed_preview_label()
            self.autosave_project()

    def restore_original_audio(self):
        if not is_valid_audio(self.orig_voice_path):
            QMessageBox.warning(self, "Нет исходника", "Сначала загрузите аудиофайл!")
            return
        self.audio_path = self.orig_voice_path
        self.clean_voice_path = self.orig_voice_path
        self.orig_voice_dur = get_audio_duration(self.orig_voice_path)
        self.audio_lbl.setText(f"Исходное аудио 1.00x ({self.orig_voice_dur:.2f}с): {os.path.basename(self.orig_voice_path)}")
        self.update_speed_preview_label()
        self.autosave_project()

    def speedup_and_trim_audio(self):
        source_path = self.orig_voice_path if is_valid_audio(self.orig_voice_path) else (
            self.clean_voice_path if is_valid_audio(self.clean_voice_path) else self.audio_path
        )
        if not is_valid_audio(source_path):
            QMessageBox.warning(self, "Нет аудио", "Сначала загрузите свой MP3 или WAV!")
            return

        speed_factor = round(self.speed_spin.value(), 2)
        trim_pauses = self.trim_pauses_chk.isChecked()

        tmp_fast_wav = os.path.join(PROJECTS_DIR, "_fast_temp.wav")
        speed_tag = int(round(speed_factor * 100))
        out_clean_wav = os.path.join(PROJECTS_DIR, f"{self.current_project_name}_speed_{speed_tag}.wav")

        try:
            self.status_lbl.setText(f"Обработка голоса ({speed_factor:.2f}x)...")
            QApplication.processEvents()

            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-i", source_path,
                "-af", f"atempo={speed_factor:.2f}", "-ac", "1", "-ar", "24000", tmp_fast_wav
            ], check=True, creationflags=SUBPROC_FLAGS)

            with wave.open(tmp_fast_wav, "rb") as wf:
                sr = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
                samples = struct.unpack(f"<{n_frames}h", raw)

            if trim_pauses:
                win_size = int(sr * 0.02)
                max_silent_windows = int(0.32 / 0.02)
                keep_pad_windows = int(0.10 / 0.02)

                rms_vals = []
                for i in range(0, len(samples), win_size):
                    chunk = samples[i:i + win_size]
                    if chunk:
                        rms = math.sqrt(sum(float(x) * float(x) for x in chunk) / len(chunk))
                        rms_vals.append(rms)

                thresh = max(120.0, sorted(rms_vals)[int(len(rms_vals) * 0.75)] * 0.08) if rms_vals else 200.0

                out_samples = []
                silent_run = []
                for idx, rms in enumerate(rms_vals):
                    chunk = samples[idx * win_size:(idx + 1) * win_size]
                    if rms < thresh:
                        silent_run.append(chunk)
                    else:
                        if len(silent_run) > max_silent_windows:
                            half = max(1, keep_pad_windows // 2)
                            kept = silent_run[:half] + silent_run[-half:]
                            for c in kept:
                                out_samples.extend(c)
                        else:
                            for c in silent_run:
                                out_samples.extend(c)
                        silent_run = []
                        out_samples.extend(chunk)

                for c in silent_run[:keep_pad_windows]:
                    out_samples.extend(c)
            else:
                out_samples = samples

            with wave.open(out_clean_wav, "wb") as wf_out:
                wf_out.setnchannels(1)
                wf_out.setsampwidth(2)
                wf_out.setframerate(sr)
                wf_out.writeframes(struct.pack(f"<{len(out_samples)}h", *out_samples))

            if os.path.exists(tmp_fast_wav):
                os.remove(tmp_fast_wav)

            if is_valid_audio(out_clean_wav):
                self.audio_path = out_clean_wav
                self.clean_voice_path = out_clean_wav
                dur = get_audio_duration(out_clean_wav)
                mode_str = "+ срез пауз" if trim_pauses else "без среза пауз"
                self.audio_lbl.setText(f"Аудио {speed_factor:.2f}x ({dur:.2f}с, {mode_str}): {os.path.basename(out_clean_wav)}")
                self.speed_preview_lbl.setText(
                    f"✅ Готово ({speed_factor:.2f}x): сейчас ровно {dur:.2f} сек (исходник был {self.orig_voice_dur:.2f}с)"
                )
                self.status_lbl.setText(f"Аудио ускорено до {speed_factor:.2f}x -> {dur:.2f} сек!")
                self.autosave_project()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка обработки звука", str(e))

    def apply_auto_sfx_mix(self):
        source_voice = self.clean_voice_path if is_valid_audio(self.clean_voice_path) else self.audio_path
        if not is_valid_audio(source_voice):
            QMessageBox.warning(self, "Нет аудио", "Сначала выберите аудиофайл!")
            return
        if not self.slides:
            QMessageBox.warning(self, "Нет сцен", "Сначала примените JSON-сценарий!")
            return

        bgm_g = self.bgm_slider.value() / 100.0
        sfx_g = self.sfx_slider.value() / 100.0
        out_sfx_wav = os.path.join(PROJECTS_DIR, f"{self.current_project_name}_master_sfx.wav")
        try:
            self.status_lbl.setText("Генерация саунд-дизайна через assets_engine.py...")
            QApplication.processEvents()
            normalize_slides_timeline(self.slides)
            assets_engine.generate_sfx_mix_wav(source_voice, self.slides, out_sfx_wav, PROJECTS_DIR, bgm_gain=bgm_g, sfx_gain=sfx_g)
            if is_valid_audio(out_sfx_wav):
                self.audio_path = out_sfx_wav
                new_dur = get_audio_duration(out_sfx_wav)
                self.audio_lbl.setText(f"Микс ({new_dur:.2f}с, Бас {self.bgm_slider.value()}% / SFX {self.sfx_slider.value()}%): {os.path.basename(out_sfx_wav)}")
                self.autosave_project()
                self.status_lbl.setText("Саунд-дизайн собран! Нажми '🎧 Прослушать' для проверки.")
                QMessageBox.information(
                    self, "Саунд-дизайн готов!",
                    f"Дорожка собрана через assets_engine.py:\n"
                    f"• Громкость дарк-баса: {self.bgm_slider.value()}%\n"
                    f"• Громкость SFX и сирен: {self.sfx_slider.value()}%\n"
                    f"Нажми кнопку '🎧 Прослушать'!"
                )
        except Exception as e:
            QMessageBox.warning(self, "Ошибка генерации SFX", str(e))

    def listen_current_audio(self):
        if not is_valid_audio(self.audio_path):
            QMessageBox.warning(self, "Нет аудио", "Сначала загрузите аудио или соберите SFX-микс!")
            return
        if os.name == "nt":
            os.startfile(self.audio_path)

    def run_whisper_srt(self):
        source_voice = self.clean_voice_path if is_valid_audio(self.clean_voice_path) else self.audio_path
        if not is_valid_audio(source_voice):
            QMessageBox.warning(self, "Нет аудио", "Сначала выберите рабочий MP3/WAV файл озвучки!")
            return
        self.canvas.preview_timer.stop()
        self.progress_bar.setValue(5)
        srt_out = os.path.join(PROJECTS_DIR, f"{self.current_project_name}_subtitles.srt")
        self.srt_worker = WhisperSRTWorker(source_voice, srt_out)
        self.srt_worker.status_sig.connect(self.status_lbl.setText)
        self.srt_worker.progress_sig.connect(self.progress_bar.setValue)
        self.srt_worker.finished_sig.connect(self.on_whisper_srt_finished)
        self.srt_worker.start()

    def on_whisper_srt_finished(self, ok, ai_text, srt_path):
        self.canvas.preview_timer.start(55)
        if not ok:
            self.status_lbl.setText("Ошибка создания SRT")
            QMessageBox.warning(self, "Whisper", ai_text)
            return

        self.srt_output_box.setPlainText(ai_text)
        self.autosave_project()
        QApplication.clipboard().setText(ai_text)
        self.status_lbl.setText(f"SRT готов и скопирован в буфер! ({srt_path})")
        QMessageBox.information(self, "SRT скопирован!", "Точная разметка таймкодов уже в буфере обмена! Нажми Ctrl+V в чат с ИИ.")

    def copy_srt_to_clipboard(self):
        txt = self.srt_output_box.toPlainText().strip()
        if not txt:
            QMessageBox.warning(self, "Пусто", "Сначала нажмите кнопку №2 'Сделать SRT-разметку'!")
            return
        QApplication.clipboard().setText(txt)
        QMessageBox.information(self, "Скопировано", "Разметка SRT скопирована в буфер обмена!")

    def start_render(self):
        if not self.slides:
            QMessageBox.warning(self, "Нет сцен", "Сначала загрузите картинки или вставьте JSON-сценарий!")
            return
        self.autosave_project()
        default_out = os.path.join(PROJECTS_DIR, f"{self.current_project_name}_capcut_ready.mp4")
        out_path, _ = QFileDialog.getSaveFileName(self, "Сохранить видео для CapCut", default_out, "MP4 Video (*.mp4)")
        if not out_path:
            return
        self.canvas.preview_timer.stop()
        self.progress_bar.setValue(0)
        self.worker = RenderWorker(
            self.slides, self.loaded_images, self.layout_cfg,
            self.audio_path, out_path
        )
        self.worker.progress_sig.connect(self.on_render_progress)
        self.worker.finished_sig.connect(self.on_render_finished)
        self.worker.start()

    def on_render_progress(self, pct, msg):
        self.progress_bar.setValue(pct)
        self.status_lbl.setText(msg)

    def on_render_finished(self, ok, msg):
        self.canvas.preview_timer.start(55)
        self.status_lbl.setText(msg)
        if ok:
            QMessageBox.information(self, "Рендер завершен", msg)
        else:
            QMessageBox.critical(self, "Ошибка рендера", msg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = StickmanVideoEditor()
    win.show()
    sys.exit(app.exec_())