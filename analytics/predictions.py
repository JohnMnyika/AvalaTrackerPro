from __future__ import annotations

import numpy as np
from sqlalchemy.orm import Session

from backend.models import Session as WorkSession

try:
    from sklearn.linear_model import LinearRegression
except Exception:  # pragma: no cover
    LinearRegression = None


def productivity_trend_prediction(db: Session) -> dict:
    sessions = db.query(WorkSession).filter(WorkSession.end_time.is_not(None)).all()
    if len(sessions) < 3:
        return {
            "status": "insufficient_data",
            "message": "Need at least 3 completed sessions for predictions.",
        }

    y = np.array([s.frames_completed for s in sessions], dtype=float)
    x = np.arange(len(y)).reshape(-1, 1)

    if LinearRegression is None:
        slope = float((y[-1] - y[0]) / max(len(y) - 1, 1))
        next_val = float(y[-1] + slope)
        return {
            "status": "fallback",
            "predicted_next_session_frames": round(max(next_val, 0.0), 2),
            "trend_slope": round(slope, 3),
        }

    model = LinearRegression()
    model.fit(x, y)
    next_idx = np.array([[len(y)]])
    next_pred = float(model.predict(next_idx)[0])
    return {
        "status": "ok",
        "predicted_next_session_frames": round(max(next_pred, 0.0), 2),
        "trend_slope": round(float(model.coef_[0]), 3),
    }
