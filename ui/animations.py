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
    returned animation alive by parenting (obj is the parent)."""
    anim = QPropertyAnimation(obj, prop, obj)
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
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim
