from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)

VEHICLE_CLASSES = {"car", "truck", "bus"}
MODEL_PATH = "yolov8n.pt"


class VisionAnalyzer:
    _model = None
    _device = "cpu"
    _init_error = None

    @classmethod
    def _load_model(cls):
        if cls._model is not None:
            return cls._model

        if cls._init_error:
            logger.warning(f"Vision model previously failed to load: {cls._init_error}")
            raise RuntimeError(f"Vision model not available: {cls._init_error}")

        try:
            import torch
            if torch.cuda.is_available():
                cls._device = "cuda"
            else:
                cls._device = "cpu"
        except Exception:
            cls._device = "cpu"

        try:
            logger.info(f"Loading YOLO model {MODEL_PATH} on device {cls._device}")
            cls._model = YOLO(MODEL_PATH, device=cls._device)
            logger.info("YOLO model loaded successfully")
            return cls._model
        except Exception as exc:
            cls._init_error = str(exc)
            logger.exception(f"Failed to load YOLO model: {exc}")
            raise RuntimeError(f"Failed to load vision model: {exc}")

    @staticmethod
    def _decode_image(image_base64: str) -> np.ndarray:
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(image_base64)
            array = np.frombuffer(image_bytes, dtype=np.uint8)
            img = cv2.imdecode(array, cv2.IMREAD_COLOR)
        except Exception as exc:
            raise ValueError(f"Unable to decode image data: {exc}")
        if img is None:
            raise ValueError("Decoded image is empty")
        return img

    @staticmethod
    def _normalize_box(box: List[float], width: int, height: int) -> List[float]:
        x1, y1, x2, y2 = box
        return [max(0.0, x1 / max(width, 1)), max(0.0, y1 / max(height, 1)), min(1.0, x2 / max(width, 1)), min(1.0, y2 / max(height, 1))]

    @staticmethod
    def _box_area(box: List[float]) -> float:
        x1, y1, x2, y2 = box
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

    @staticmethod
    def _iou(box_a: List[float], box_b: List[float]) -> float:
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b
        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        union_area = VisionAnalyzer._box_area(box_a) + VisionAnalyzer._box_area(box_b) - inter_area
        return inter_area / max(union_area, 1e-6)

    @classmethod
    def analyze_frame(
        cls,
        image_base64: str,
        existing_boxes: Optional[List[Dict[str, Any]]] = None,
        width: int = 0,
        height: int = 0,
        sensitivity: float = 0.5,
    ) -> Dict[str, Any]:
        logger.info(f"Vision analysis starting: width={width}, height={height}, sensitivity={sensitivity}, existing_boxes={len(existing_boxes or [])}")
        
        if not image_base64:
            raise ValueError("No image data provided")
            
        image = cls._decode_image(image_base64)
        image_height, image_width = image.shape[:2]
        logger.info(f"Image decoded: {image_width}x{image_height}")

        if width <= 0 or height <= 0:
            width = image_width
            height = image_height

        model = cls._load_model()
        logger.info(f"Running YOLO detection on {image_width}x{image_height} image")
        results = model(image)
        logger.info(f"YOLO returned {len(results)} result(s)")

        detected: List[Dict[str, Any]] = []
        suggestions: List[Dict[str, Any]] = []
        existing_norm = []

        if existing_boxes:
            for box in existing_boxes:
                if isinstance(box.get("box"), list) and len(box["box"]) == 4:
                    existing_norm.append({
                        "label": box.get("label"),
                        "box": [
                            max(0.0, float(box["box"][0])),
                            max(0.0, float(box["box"][1])),
                            min(1.0, float(box["box"][2])),
                            min(1.0, float(box["box"][3])),
                        ],
                    })
        logger.info(f"Normalized {len(existing_norm)} existing boxes from {len(existing_boxes or [])} input boxes")

        for result in results:
            class_names = getattr(result, "names", {}) or {}
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue

            logger.info(f"Processing result with {len(boxes)} detected boxes")
            for box in boxes:
                cls_idx = int(box.cls.cpu().item()) if hasattr(box, "cls") else None
                label = str(class_names.get(cls_idx, "unknown")).lower() if cls_idx is not None else "unknown"
                if label not in VEHICLE_CLASSES:
                    continue

                xyxy = box.xyxy[0].cpu().numpy().tolist() if hasattr(box, "xyxy") else []
                confidence = float(box.conf.cpu().item()) if hasattr(box, "conf") else 0.0
                normalized = cls._normalize_box(xyxy, width, height)
                detected_entry = {
                    "label": label,
                    "confidence": round(confidence, 4),
                    "box": normalized,
                    "box_pixels": [float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])],
                }
                detected.append(detected_entry)

                if existing_norm:
                    best_match = None
                    best_iou = 0.0
                    for existing in existing_norm:
                        iou = cls._iou(normalized, existing["box"])
                        if iou > best_iou:
                            best_iou = iou
                            best_match = existing

                    if best_match and best_iou >= 0.35:
                        existing_area = cls._box_area(best_match["box"])
                        detected_area = cls._box_area(normalized)
                        if detected_area < existing_area and detected_area > 0:
                            area_improvement = 1.0 - (detected_area / existing_area)
                            threshold = 0.08 + (sensitivity * 0.25)
                            if area_improvement >= threshold:
                                suggestions.append({
                                    "label": label,
                                    "confidence": round(confidence, 4),
                                    "detected_box": normalized,
                                    "existing_box": best_match["box"],
                                    "iou": round(best_iou, 4),
                                    "area_improvement": round(area_improvement, 4),
                                })
                                logger.info(f"Found suggestion: {label} with area improvement {round(area_improvement, 4)}")

        time_saved_estimate = len(suggestions) * 0.0833 * 60.0
        logger.info(f"Vision analysis completed: {len(detected)} boxes detected, {len(suggestions)} suggestions found")
        return {
            "detected_boxes": detected,
            "suggestions": suggestions,
            "suggestions_count": len(suggestions),
            "time_saved_estimate_seconds": round(time_saved_estimate, 2),
            "processed_at": datetime.utcnow().isoformat(),
        }
