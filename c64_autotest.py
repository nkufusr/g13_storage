#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EmuELEC - C64 批量自测脚本
成功准则：retroarch / vice_x64 / x64_libretro 进程启动后持续 ≥ 5 秒即 PASS
"""

import os, sys, time, csv, signal, subprocess
from xml.etree import ElementTree as ET

# ============ 基本常量 ============
CFG_PATH      = "/storage/.config/emulationstation/es_systems.cfg"
TARGET_SYSTEM = "c64"
EMU_SH        = "/usr/bin/emuelecRunEmu.sh"
CHECK_SEC     = 5                       # 运行 >= 5 秒算 PASS
REPORT_CSV    = "/storage/roms/c64/test_report.csv"

ROM_DIR = EXT = CORE = EMULATOR = None  # 运行时自动填充

# ============ 获取系统 & ROM 列表 ============
def get_sys_info():
    global ROM_DIR, EXT, CORE, EMULATOR
    if ROM_DIR: return
    tree = ET.parse(CFG_PATH)
    for node in tree.findall("system"):
        if node.findtext("name") == TARGET_SYSTEM:
            ROM_DIR  = node.findtext("path")
            EXT      = node.findtext("extension").split()
            CORE     = node.find(".//core").text
            EMULATOR = node.find(".//emulator").attrib["name"]
            return
    raise RuntimeError("es_systems.cfg 中找不到 C64 条目！")

def parse_gamelist():
    get_sys_info()
    gl = os.path.join(ROM_DIR, "gamelist.xml")
    out = []
    if os.path.isfile(gl):
        try:
            tree = ET.parse(gl)
            for g in tree.findall("game"):
                p = (g.findtext("path") or "").strip()
                full = p if p.startswith("/") else os.path.join(ROM_DIR, p.lstrip("./"))
                name = g.findtext("name") or os.path.basename(full)
                out.append((full, name))
        except ET.ParseError:
            print("[警告] gamelist.xml 解析失败，改为扫描目录")
    if not out:  # fallback
        out = [(os.path.join(ROM_DIR, f), f)
               for f in sorted(os.listdir(ROM_DIR))
               if any(f.endswith(e) for e in EXT)]
    return out

def get_controllers():
    cfg = "/tmp/gamepads.cfg"
    if os.path.isfile(cfg):
        return open(cfg).read().strip().replace('"', r'\"')
    return ""

# ============ 进程检测 & 清理 ============
MATCH_WORDS = ("retroarch", "vice_x64", "x64_libretro")

def _cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "ignore")
    except FileNotFoundError:
        return ""

def still_running():
    return any(any(w in _cmdline(pid) for w in MATCH_WORDS)
               for pid in os.listdir("/proc") if pid.isdigit())

def wait_until_idle(max_wait=10):
    t = 0.0
    while still_running() and t < max_wait:
        time.sleep(0.5)
        t += 0.5
    if still_running():  # 兜底强杀
        for pid in os.listdir("/proc"):
            if pid.isdigit() and any(w in _cmdline(pid) for w in MATCH_WORDS):
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except PermissionError:
                    pass
        time.sleep(1)

# ============ 单 ROM 测试核心 ============
def run_one(path, name):
    cmd = [EMU_SH, path,
           f"-P{TARGET_SYSTEM}",
           f"--core={CORE}",
           f"--emulator={EMULATOR}",
           f'--controllers="{get_controllers()}"']

    proc = subprocess.Popen(cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT)

    # 等 CHECK_SEC 秒
    time.sleep(CHECK_SEC)
    alive = (proc.poll() is None)

    result = "PASS" if alive else "FAIL"
    detail = f"alive={alive}"

    # 结束进程并清理
    if alive:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    wait_until_idle()

    return result, detail

# ============ 主流程 ============
def main():
    if os.geteuid() != 0:
        print("⚠ 建议 root 运行，避免权限/TTY 问题")
    games = parse_gamelist()
    if not games:
        print("未找到 C64 ROM")
        return

    with open(REPORT_CSV, "w", newline="", encoding="utf-8") as fp:
        w = csv.writer(fp); w.writerow(["rom", "name", "result", "detail"])
        for i, (p, n) in enumerate(games, 1):
            print(f"[{i}/{len(games)}] {n} …", end="", flush=True)
            res, info = run_one(p, n)
            w.writerow([p, n, res, info])
            print(res)

    print("✓ 测试完成，报告已生成:", REPORT_CSV)

# ─────────────────────────────────────
if __name__ == "__main__":
    main()
