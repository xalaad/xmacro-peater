"""Reusable animation helpers: eased property animations, pulses, fades."""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QVariantAnimation,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

EASE = QEasingCurve.Type.OutCubic


def animate_property(obj, prop: bytes, end, duration_ms: int = 80,
                     easing=EASE) -> QPropertyAnimation:
    """Start an eased animation of a Qt property to `end`. Caller keeps the
    returned animation alive by parenting (obj is the parent).

    The previous animation of the SAME property is stopped first: rapid
    calls (sticks/triggers at 60fps) would otherwise pile up concurrent
    animations fighting over one property — jittery interleaved writes
    and a redundant repaint per write."""
    running = getattr(obj, "_prop_anims", None)
    if running is None:
        running = obj._prop_anims = {}
    prev = running.get(prop)
    if prev is not None:
        try:
            prev.stop()  # DeleteWhenStopped disposes it
        except RuntimeError:
            pass  # its C++ half was already deleted
    anim = QPropertyAnimation(obj, prop, obj)
    running[prop] = anim
    anim.destroyed.connect(
        lambda *_: running.pop(prop, None) if running.get(prop) is anim
        else None)
    anim.setDuration(duration_ms)
    anim.setEndValue(end)
    anim.setEasingCurve(easing)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def animate_color(parent, start: QColor, end: QColor, on_value,
                  duration_ms: int = 250) -> QVariantAnimation:
    """Interpolate a QColor, calling on_value(color) each frame."""
    anim = QVariantAnimation(parent)
    anim.setDuration(duration_ms)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(EASE)
    anim.valueChanged.connect(on_value)
    anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def fade_in(widget: QWidget, duration_ms: int = 180) -> QPropertyAnimation:
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutQuad)
    # Drop the effect when done — stacked opacity effects slow painting.
    def _drop_effect() -> None:
        try:
            widget.setGraphicsEffect(None)
        except RuntimeError:
            pass  # widget already deleted (e.g. trimmed log line)

    anim.finished.connect(_drop_effect)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim
