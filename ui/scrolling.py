"""Smooth, versatile scrolling for lists and scroll areas.

- Kinetic touch scrolling (flick with a finger on touchscreens)
- Drag-to-scroll with the left mouse button (taps still click items)
- Per-pixel wheel scrolling with a gentle step instead of coarse jumps
"""
from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QScroller


def enable_smooth_scroll(area: QAbstractScrollArea) -> None:
    if isinstance(area, QAbstractItemView):
        area.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        area.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
    area.verticalScrollBar().setSingleStep(16)
    QScroller.grabGesture(
        area.viewport(), QScroller.ScrollerGestureType.TouchGesture)
    QScroller.grabGesture(
        area.viewport(),
        QScroller.ScrollerGestureType.LeftMouseButtonGesture)
    scroller = QScroller.scroller(area.viewport())
    props = scroller.scrollerProperties()
    props.setScrollMetric(
        props.ScrollMetric.DecelerationFactor, 0.30)
    props.setScrollMetric(
        props.ScrollMetric.DragStartDistance, 0.006)
    scroller.setScrollerProperties(props)
