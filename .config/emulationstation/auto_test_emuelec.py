#!/usr/bin/env python3
"""
auto_test_emuelec.py
批量启动 EmuELEC 上的全部 ROM，依据 runcommand.log 判断是否成功。
结果写入 /storage/logs/auto_test/result_YYYYMMDD_HHMMSS.csv
"""

import time
import subprocess
import pathlib
import re
import xml.etree.ElementTree as ET
import csv                         # ← 新增，仅用标准库
from typing import Dict, Iterator, Tuple
import argparse
import logging          # ← 新增
# ---------------------------------------------------------------------------
ES_CFG_FILES = [
    pathlib.Path("/storage/.config/emulationstation/es_systems.cfg"),
    pathlib.Path("/etc/emulationstation/es_systems.cfg"),
]
RUNSCRIPT = "/usr/bin/emuelecRunEmu.sh"
LOGFILE   = pathlib.Path("/emuelec/logs/runcommand.log")
OUTDIR    = pathlib.Path("/storage/logs/auto_test")
TIMEOUT   = 25
SLEEP_GAP = 1
ERROR_PAT = re.compile(r"(ERROR|Error|Failed|Segmentation fault|Traceback)")

# ---------------------------------------------------------------------------
def parse_systems() -> Dict[str, Dict[str, object]]:
    """
    解析 EmulationStation 的 es_systems.cfg，返回平台配置字典。

    返回示例
    -------
    {
        "psx": {
            "rom_dir":  Path("/storage/roms/psx"),
            "ext":      [".cue", ".chd"],
            "emulator": "pcsx_rearmed",
            "core":     "pcsx_rearmed"
        },
        ...
    }
    """
    cfg_file: pathlib.Path | None = next((f for f in ES_CFG_FILES if f.exists()), None)
    if cfg_file is None:
        raise FileNotFoundError("未找到 es_systems.cfg，请检查 ES_CFG_FILES 常量设置。")

    tree = ET.parse(cfg_file)
    root = tree.getroot()

    systems: Dict[str, Dict[str, object]] = {}
    for sys in root.findall("system"):
        name = sys.findtext("name", "").strip()
        if not name:
            continue

        rom_path = pathlib.Path(sys.findtext("path", "").strip()).expanduser()
        ext_raw  = sys.findtext("extension", "").strip()
        # es_systems.cfg 里的扩展名前可能含点，也可能没有点；统一转成带点的小写
        exts = [e if e.startswith(".") else f".{e}" for e in ext_raw.split()]
        exts = [e.lower() for e in exts]

        emulator = sys.findtext("emulator", "").strip() or "default"
        core     = sys.findtext("core", "").strip() or "default"

        systems[name] = {
            "rom_dir":  rom_path,
            "ext":      exts,
            "emulator": emulator,
            "core":     core,
        }

    if not systems:
        raise RuntimeError(f"{cfg_file} 未解析到任何 <system> 节点。")
    return systems


# ---------------------------------------------------------------------------
def iter_roms(systems: Dict[str, Dict[str, object]]) -> Iterator[Tuple[str, pathlib.Path]]:
    """
    遍历所有平台的 ROM 文件。

    Yields
    ------
    (platform_name, rom_path)
    """
    for plat, cfg in systems.items():
        rom_dir: pathlib.Path = cfg["rom_dir"]
        if not rom_dir.exists():
            continue
        for ext in cfg["ext"]:
            # 使用 rglob 递归遍历，大小写不敏感
            pattern = f"*{ext}"
            for rom in rom_dir.rglob(pattern):
                if rom.is_file():
                    yield plat, rom


# ---------------------------------------------------------------------------
def run_one(plat: str, rom: pathlib.Path, cfg: Dict[str, object]) -> Tuple[str, float, str]:
    """
    启动单个 ROM，判断执行结果。

    Parameters
    ----------
    plat : 平台名称
    rom  : ROM 文件路径
    cfg  : 平台配置（parse_systems 返回值中的子项）

    Returns
    -------
    status : "PASS" | "FAIL"
    used   : float  # 运行耗时（秒）
    reason : str    # 失败时原因，成功返回空串
    """
    start_ts = time.time()

    # ---- 记录 log 文件偏移，便于只分析本次新增内容 ----
    log_offset = 0
    if LOGFILE.exists():
        log_offset = LOGFILE.stat().st_size

    # ---- 调用运行脚本 ----
    cmd = [RUNSCRIPT, plat, str(rom)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        used = time.time() - start_ts
        return "FAIL", used, "TIMEOUT"

    used = time.time() - start_ts

    # ---- 读取新增日志并判定 ----
    reason = ""
    status = "PASS"
    try:
        if LOGFILE.exists():
            with LOGFILE.open("r", encoding="utf-8", errors="ignore") as f:
                f.seek(log_offset)
                new_logs = f.read()
                m = ERROR_PAT.search(new_logs)
                if m:
                    status = "FAIL"
                    reason = m.group(0)
    except Exception as e:  # noqa: BLE001
        status = "FAIL"
        reason = f"log read error: {e}"

    return status, used, reason

# ---------------------------------------------------------------------------
# ……（其余函数保持不变，省略）……

def init_logger(verbose: bool = False) -> None:      # ← 新增
    """
    初始化日志系统。

    Parameters
    ----------
    verbose : True 时使用 DEBUG 级别，否则 INFO
    """
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt)

def main() -> None:
    parser = argparse.ArgumentParser(description="EmuELEC ROM 自动批量测试")
    parser.add_argument("-s", "--system", action="append",
                        help="仅测试指定平台，可重复使用，例：-s c64 -s nes")
    parser.add_argument("-t", "--timeout", type=int, default=25,
                        help="单个 ROM 最大等待秒数 (默认 25)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="输出调试信息")
    args = parser.parse_args()

    global TIMEOUT
    TIMEOUT = args.timeout
    init_logger(args.verbose)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    systems = parse_systems()

    # ---------- 只保留用户指定的平台 ----------
    if args.system:
        lowercase_sel = {s.lower() for s in args.system}
        systems = {k: v for k, v in systems.items() if k.lower() in lowercase_sel}
        if not systems:
            raise SystemExit(f"未找到匹配平台: {', '.join(args.system)}")

    rom_list = list(iter_roms(systems))
    if not rom_list:
        raise SystemExit("未发现任何 ROM，已退出。")

    total = len(rom_list)
    records = []

    for idx, (plat, rom) in enumerate(rom_list, 1):
        cfg = systems[plat]
        status, used, reason = run_one(plat, rom, cfg)
        logging.info("[%4d/%d] [%s] %-8s %s (%.2fs) %s",
                     idx, total, status, plat, rom.name, used, reason)
        records.append((plat, rom.name, cfg["core"], cfg["emulator"],
                        f"{used:.2f}", status, reason))

    # ---- CSV 写入与统计代码保持不变 ----