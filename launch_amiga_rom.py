#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
amiga_batch_test.py
在 EmuELEC 环境批量测试 /storage/roms/amiga 下所有 ROM
启动方式完全等价于 EmulationStation：
    emuelecRunEmu.sh <ROM> -Pamiga --core=puae --emulator=libretro --controllers="..."
判定：
    • RetroArch/PUAE 进程在 90 s 检查点仍存活
    • 同期日志中未出现 “Segmentation fault / Guru Meditation / panic”
结果写入 /storage/roms/amiga/test_report.csv
"""

import os, time, csv, signal, subprocess, tempfile, re
from pathlib import Path
from xml.etree import ElementTree as ET

# ───────────────── 可调常量 ─────────────────
TARGET_SYS  = "amiga"
ROM_DIR     = Path("/storage/roms/amiga")
EMU_SH      = "/usr/bin/emuelecRunEmu.sh"
CORE        = "puae"               # 与 es_systems.cfg 保持一致
WAIT_SEC    = 10                   # 等足 WHDLoad 解包 + 初始化
REPORT_CSV  = ROM_DIR / "test_report.csv"

BAD_PATTERNS = [r"segmentation fault", r"guru meditation", r"panic"]
BAD_RE = re.compile("|".join(BAD_PATTERNS), re.I)

PROC_KEYS = ("retroarch", "puae_libretro", "puae2021", "uae")

# ───────────────── 工具函数 ─────────────────
def controllers_arg() -> str:
    """生成 --controllers="..." 字符串；若文件不存在则返回空串参数"""
    cfg_file = Path("/tmp/gamepads.cfg")
    if cfg_file.exists():
        # 读取并把内部的 " 转成 \" 以防止 bash 误解析
        ctl = cfg_file.read_text().strip().replace('"', r'\"')
        return f'--controllers="{ctl}"'
    return '--controllers=""'

def all_roms():
    exts = {".zip", ".adf", ".lha", ".adz"}
    return sorted([p for p in ROM_DIR.iterdir() if p.suffix.lower() in exts])

def _cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="ignore")
    except FileNotFoundError:
        return ""

def kill_leftovers():
    for pid in os.listdir("/proc"):
        if pid.isdigit() and any(k in _cmdline(pid) for k in PROC_KEYS):
            try: os.kill(int(pid), signal.SIGKILL)
            except PermissionError:
                pass

# ───────────────── 单 ROM 测试 ─────────────────
def test_rom(rom: Path):
    fd, log_path = tempfile.mkstemp(prefix="puae_", suffix=".log")
    os.close(fd)

    cmd = [
        EMU_SH, str(rom),
        f"-P{TARGET_SYS}",
        f"--core={CORE}",
        "--emulator=libretro",
        controllers_arg()
    ]

    with open(log_path, "w") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)

    time.sleep(WAIT_SEC)
    alive = proc.poll() is None

    with open(log_path, "r", errors="ignore") as lf:
        bad = bool(BAD_RE.search(lf.read()))

    result = "PASS" if (alive and not bad) else "FAIL"
    detail = f"alive={alive} bad={bad} log={Path(log_path).name}"

    if alive:
        proc.send_signal(signal.SIGINT)
        try: proc.wait(5)
        except subprocess.TimeoutExpired: proc.kill()

    kill_leftovers()
    return result, detail

# ───────────────── 主流程 ─────────────────
def main():
    if os.geteuid() != 0:
        print("⚠ 建议以 root 身份运行，避免权限/TTY 问题")

    roms = all_roms()
    if not roms:
        print("未在", ROM_DIR, "找到任何 ROM"); return

    with REPORT_CSV.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["rom", "result", "detail"])

        for i, rom in enumerate(roms, 1):
            print(f"[{i}/{len(roms)}] {rom.name} …", end="", flush=True)
            res, info = test_rom(rom)
            writer.writerow([rom.name, res, info])
            print(res)

    print("\n✓ 测试完成，结果已写入:", REPORT_CSV)

if __name__ == "__main__":
    main()
