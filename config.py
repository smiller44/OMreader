import logging
import os

logger = logging.getLogger(__name__)

CONFIG = {
    "PDF_VIEWPORT_WIDTH":  1100,
    "PDF_VIEWPORT_HEIGHT": 850,
    "PDF_SCALE":           0.80,
    "IMAGE_RESULTS_LIMIT": 10,
    "MIN_IMAGE_BYTES":     5000,
    "MIN_IMAGE_WIDTH":     200,
    "MIN_IMAGE_HEIGHT":    150,
    "MAX_FILE_SIZE_MB":    50,
    "MAX_PDF_TEXT_CHARS":  80_000,
    "SENSITIVITY_RANGE":   [-0.10, -0.05, 0.0, 0.05, 0.10],
    "CLAUDE_MODEL":        os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
}
