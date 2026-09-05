"""Dump this digitizer's ACTUAL report layout, slot by slot.

    python tools/hid_dump.py [seconds]

Tap once, then do one slow drag. For every report it prints each contact
slot (HID link collection) with its tip switch and X/Y, so we can see
exactly how this device assigns contacts — the thing that decides
whether a drag stays one gesture or splits.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: F401  pins Per-Monitor-V2

from core.capture import raw_mouse as rm
from core.capture.hid_touch import (
    DIGITIZER_PAGE,
    GENERIC_DESKTOP_PAGE,
    HIDP_CAPS,
    HIDP_STATUS_SUCCESS,
    HIDP_VALUE_CAPS,
    RIDI_PREPARSEDDATA,
    USAGE,
    USAGE_TIP_SWITCH,
    USAGE_X,
    USAGE_Y,
    HidP_Input,
    _hid,
    _user32,
)

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
state: dict = {"map": None, "n": 0, "t0": time.monotonic()}


def build(hdevice):
    size = ctypes.c_uint(0)
    _user32.GetRawInputDeviceInfoW(hdevice, RIDI_PREPARSEDDATA, None,
                                   ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    _user32.GetRawInputDeviceInfoW(hdevice, RIDI_PREPARSEDDATA, buf,
                                   ctypes.byref(size))
    pre = ctypes.cast(buf, ctypes.c_void_p)
    caps = HIDP_CAPS()
    _hid.HidP_GetCaps(pre, ctypes.byref(caps))
    n = ctypes.c_ushort(caps.NumberInputValueCaps)
    arr = (HIDP_VALUE_CAPS * n.value)()
    _hid.HidP_GetValueCaps(HidP_Input, arr, ctypes.byref(n), pre)
    links: dict[int, dict] = {}
    for c in arr[:n.value]:
        if c.UsagePage != GENERIC_DESKTOP_PAGE or c.IsRange:
            continue
        u = c.u.NotRange.Usage
        if u in (USAGE_X, USAGE_Y):
            links.setdefault(c.LinkCollection, {})[u] = (
                c.LogicalMin, c.LogicalMax)
    slots = int(_hid.HidP_MaxUsageListLength(HidP_Input, DIGITIZER_PAGE, pre))
    print(f"device: {caps.NumberLinkCollectionNodes} collections, "
          f"{n.value} value caps, digitizer button slots={slots}")
    print(f"contact slots with X/Y: {sorted(links)}")
    return {"pre": pre, "buf": buf, "links": links, "slots": slots}


def read_slot(m, link, report):
    vx, vy = ctypes.c_ulong(0), ctypes.c_ulong(0)
    rep = ctypes.c_char_p(report)
    ok_x = _hid.HidP_GetUsageValue(
        HidP_Input, GENERIC_DESKTOP_PAGE, link, USAGE_X,
        ctypes.byref(vx), m["pre"], rep, len(report)) == HIDP_STATUS_SUCCESS
    ok_y = _hid.HidP_GetUsageValue(
        HidP_Input, GENERIC_DESKTOP_PAGE, link, USAGE_Y,
        ctypes.byref(vy), m["pre"], rep, len(report)) == HIDP_STATUS_SUCCESS
    tip = None
    if m["slots"]:
        cnt = ctypes.c_ulong(m["slots"])
        lst = (USAGE * m["slots"])()
        if _hid.HidP_GetUsages(HidP_Input, DIGITIZER_PAGE, link, lst,
                               ctypes.byref(cnt), m["pre"], rep,
                               len(report)) == HIDP_STATUS_SUCCESS:
            tip = USAGE_TIP_SWITCH in lst[:cnt.value]
    return (vx.value if ok_x else None, vy.value if ok_y else None, tip)


def on_input(lparam):
    size = wintypes.UINT(0)
    rm._user32.GetRawInputData(lparam, rm.RID_INPUT, None,
                               ctypes.byref(size),
                               ctypes.sizeof(rm.RAWINPUTHEADER))
    if not size.value:
        return
    buf = ctypes.create_string_buffer(size.value)
    if rm._user32.GetRawInputData(lparam, rm.RID_INPUT, buf,
                                  ctypes.byref(size),
                                  ctypes.sizeof(rm.RAWINPUTHEADER)) != size.value:
        return
    header = rm.RAWINPUTHEADER.from_buffer_copy(
        buf[:ctypes.sizeof(rm.RAWINPUTHEADER)])
    raw = buf.raw
    size_hid = int.from_bytes(raw[24:28], "little")
    count = int.from_bytes(raw[28:32], "little")
    if not size_hid or not count:
        return
    if state["map"] is None:
        state["map"] = build(header.hDevice)
    m = state["map"]
    report = raw[32:32 + size_hid]
    state["n"] += 1
    if state["n"] > 400:
        return
    parts = []
    for link in sorted(m["links"]):
        x, y, tip = read_slot(m, link, report)
        if x is None and tip is None:
            continue
        mark = "DOWN" if tip else ("up  " if tip is False else "?   ")
        parts.append(f"[slot{link} {mark} {x},{y}]")
    print(f"  {time.monotonic() - state['t0']:6.2f}s  rep{state['n']:3} "
          f"len={size_hid} " + " ".join(parts), flush=True)


def wndproc(hwnd, msg, wparam, lparam):
    if msg == rm.WM_INPUT:
        try:
            on_input(lparam)
        except Exception as e:  # noqa: BLE001
            print("parse error:", e)
        return 0
    if msg == rm.WM_DESTROY:
        rm._user32.PostQuitMessage(0)
        return 0
    return rm._user32.DefWindowProcW(hwnd, msg, wparam, lparam)


proc = rm.WNDPROC(wndproc)
wc = rm.WNDCLASSW()
wc.lpfnWndProc = proc
wc.lpszClassName = "XMacroHidDump"
wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
rm._user32.RegisterClassW(ctypes.byref(wc))
hwnd = rm._user32.CreateWindowExW(0, "XMacroHidDump", "XMacroHidDump", 0,
                                  0, 0, 0, 0,
                                  wintypes.HWND(rm.HWND_MESSAGE), None,
                                  wc.hInstance, None)
rid = rm.RAWINPUTDEVICE(0x0D, 0x04, rm.RIDEV_INPUTSINK, hwnd)
print("registered:", bool(rm._user32.RegisterRawInputDevices(
    ctypes.byref(rid), 1, ctypes.sizeof(rm.RAWINPUTDEVICE))))
print(f"\n=== TAP ONCE, THEN ONE SLOW DRAG ({DURATION:.0f}s) ===\n")

msg = wintypes.MSG()
end = time.monotonic() + DURATION
while time.monotonic() < end:
    if rm._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        rm._user32.TranslateMessage(ctypes.byref(msg))
        rm._user32.DispatchMessageW(ctypes.byref(msg))
    else:
        time.sleep(0.002)
print(f"\ntotal reports: {state['n']}")
