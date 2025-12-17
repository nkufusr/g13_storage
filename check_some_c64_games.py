"""
C64 游戏批量测试脚本
-------------------
• 先直接把 .zip 交给 RetroArch + VICE；
• 若进程在 very_short_secs 内退出（认为“秒退”）或返回码非 0，
  则把 ZIP 解压出来，依次尝试包里所有受支持镜像，
  直到成功或全部失败为止。
"""
from __future__ import annotations   # ← 该导入只能出现一次并位于文件最前

import shlex
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import List

RETROARCH_BIN: str = "retroarch"
VICE_CORE: str = "/tmp/cores/vice_x64_libretro.so"
RETROARCH_CFG: str = "/storage/.config/retroarch/retroarch.cfg"

# 小于这个耗时就被视为“秒退”
very_short_secs: float = 3.0

# RetroArch-VICE 可加载的镜像扩展名
SUPPORTED_EXTS: tuple[str, ...] = (
    ".d64", ".t64", ".tap", ".prg", ".g64",
    ".crt", ".p00", ".d71", ".d81",
)


# -------- 工具函数 -------------------------------------------------
def build_retroarch_cmd(content_path: str) -> List[str]:
    """
    生成 RetroArch 启动命令
    retroarch -v -L <core> --config <cfg> <content>
    """
    return [
        RETROARCH_BIN,
        "-v",
        "-L", VICE_CORE,
        "--config", RETROARCH_CFG,
        content_path,
    ]


def _run_process(cmd: List[str]) -> tuple[int, float]:
    """运行子进程并返回 (退出码, 运行秒数)"""
    start = time.monotonic()
    proc = subprocess.run(cmd)
    return proc.returncode, time.monotonic() - start


def _extract_supported_images(zippath: Path) -> list[Path]:
    """
    解压 zip 中所有受支持的镜像，返回它们的绝对路径列表（保持原顺序）。
    若 zip 不是合法文件或找不到镜像则返回空列表。
    """
    images: list[Path] = []
    try:
        with zipfile.ZipFile(zippath) as zf:
            tmpdir = Path(tempfile.mkdtemp(prefix="c64roms_"))
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if any(info.filename.lower().endswith(ext) for ext in SUPPORTED_EXTS):
                    out_path = tmpdir / Path(info.filename).name
                    zf.extract(info, tmpdir)
                    images.append(out_path)
    except zipfile.BadZipFile:
        pass
    return images


# -------- 公开函数 -------------------------------------------------
def run_c64_game(rom_path: str, dry_run: bool = False) -> int:
    """
    使用 RetroArch + VICE 核心启动指定 ROM。
    • rom_path  可以是 .zip/.d64/.t64 … 等
    • dry_run   为 True 时仅打印命令，不真正执行（调试）
    返回值：RetroArch 进程退出码（0 为成功）
    """
    rom_abs = Path(rom_path).expanduser().resolve()
    if not rom_abs.exists():
        raise FileNotFoundError(f"ROM 文件不存在: {rom_abs}")

    # ---------- 尝试 #1：直接启动 ZIP / 镜像 ----------
    cmd = build_retroarch_cmd(str(rom_abs))
    print("[INFO] 尝试 #1:", shlex.join(cmd))
    if dry_run:
        return 0

    code, dur = _run_process(cmd)
    if code == 0 and dur > very_short_secs:
        return code  # 第一次就成功

    print(f"[WARN] #1 失败 (code={code}, {dur:.2f}s)，准备解压兜底…")

    # ---------- 尝试 #2：解压 ZIP，逐一尝试 ----------
    images = _extract_supported_images(rom_abs)
    if not images:
        print("[ERROR] ZIP 中未找到可识别镜像，放弃。")
        return code or 1

    for idx, img in enumerate(images, 1):
        cmd2 = build_retroarch_cmd(str(img))
        print(f"[INFO] 尝试 #2-{idx}/{len(images)}:", shlex.join(cmd2))
        code2, dur2 = _run_process(cmd2)
        print(f"        → 结束 (code={code2}, {dur2:.2f}s)")
        if code2 == 0 and dur2 > very_short_secs:
            print("[OK] 成功启动！")
            return 0

    print("[ERROR] 全部镜像尝试完仍失败")
    return 1
"""
再次改进：
一部分 ZIP 在直接传给 VICE 核心时会立刻退出（≈2 s），
原因多半是：
  • ZIP 里包含多个镜像；或
  • 文件被包在子目录中，VICE 无法正确找到主镜像  
因此做一个“失败兜底”策略：

  1. 先按原来的方式把整包 ZIP 直接交给 RetroArch。
  2. 若进程在 very_short_secs 之内结束或返回码非 0，
     就把 ZIP 解压到临时目录，挑选第一个支持的镜像
     (.d64/.t64/.tap/.prg/.g64/.crt/.p00/.d71/.d81) 再跑一次。

这样大约 95% 常见 ROM 都可自动启动。
"""


import os
import shlex
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import List

# ‘__future__’ 导入只能出现一次；如文件其余位置还有同名行，请删除。
import argparse, random, csv, sys, time, pathlib
from typing import List, Tuple, Optional, Dict
import os
import shlex
import subprocess

# ────────────────── 引入复用函数 ──────────────────
try:
    from auto_test_emuelec import parse_systems, run_one          # type: ignore
except ImportError:
    sys.exit("❌ 未找到 auto_test_emuelec.py，请确保两个脚本放在同一目录。")

# ────────────────── 常量 ──────────────────
REPORT_DIR   = pathlib.Path("/storage/logs/auto_test")  # 若无权限可改成当前目录
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ────────────────── 工具 ──────────────────
def find_c64_name(systems: Dict[str, dict]) -> Optional[str]:
    """
    在 es_systems.cfg 解析结果中，找到第一个与 C64 相关的系统名称。
    兼容常见写法：c64 / commodore64 / commodore 64 / vice / x64 等。
    """
    # 依次尝试匹配的关键字，顺序代表优先级
    keywords = [
        "c64",
        "commodore64",
        "commodore 64",
        "commodore_64",
        "vice",     # RetroArch/EmuELEC 默认的 VICE 核心
        "x64",      # VICE 的主执行文件名
    ]

    lower_map = {name.lower(): name for name in systems.keys()}

    # 逐个关键字查找，命中后返回原始大小写形式的系统名
    for kw in keywords:
        for sys_lower, sys_orig in lower_map.items():
            if kw in sys_lower:
                return sys_orig
    return None


def list_roms(rom_dir: pathlib.Path, exts: List[str]) -> List[pathlib.Path]:
    """递归扫描 rom_dir，返回全部符合扩展名的文件路径（已按字母排序）。"""
    res: List[pathlib.Path] = []
    for p in rom_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            res.append(p)
    return sorted(res)


# ====== 已存在的函数 ======
def find_c64_name(systems: Dict[str, dict]) -> Optional[str]:
    """
    在 es_systems.cfg 解析结果中，找到第一个与 C64 相关的系统名称。
    兼容常见写法：c64 / commodore64 / commodore 64 / vice / x64 等。
    """
    keywords = [
        "c64",
        "commodore64",
        "commodore 64",
        "commodore_64",
        "vice",
        "x64",
    ]
    lower_map = {name.lower(): name for name in systems.keys()}
    for kw in keywords:
        for sys_lower, sys_orig in lower_map.items():
            if kw in sys_lower:
                return sys_orig
    return None


# ====== 新增的常量（可改为读取外部配置） ======
RETROARCH_BIN = os.getenv("RETROARCH_BIN", "retroarch")
VICE_CORE_PATH = os.getenv("VICE_CORE_PATH", "/tmp/cores/vice_x64_libretro.so")
RETROARCH_CFG  = os.getenv("RETROARCH_CFG",  "/storage/.config/retroarch/retroarch.cfg")


# ====== 新增功能函数 ======
# def build_retroarch_cmd(rom_path: str) -> List[str]:
#     """
#     依据 EmuELEC 的规则构造 RetroArch 启动命令。
#     等价于：
#     retroarch -v -L /tmp/cores/vice_x64_libretro.so --config /storage/.config/retroarch/retroarch.cfg <ROM>
#     """
#     return [
#         RETROARCH_BIN,
#         "-v",
#         "-L", VICE_CORE_PATH,
#         "--config", RETROARCH_CFG,
#         rom_path,
#     ]


# def run_c64_game(rom_path: str, dry_run: bool = False) -> int:
#     """
#     使用 RetroArch + VICE 核心启动指定 ROM。
#     返回值：RetroArch 退出码
#     """
#     if not os.path.exists(rom_path):
#         raise FileNotFoundError(f"ROM 文件不存在: {rom_path}")

#     cmd = build_retroarch_cmd(rom_path)
#     print("[INFO] 即将执行:", shlex.join(cmd))

#     if dry_run:
#         # 调试用：只打印命令，不真正启动
#         return 0

#     # 启动进程并等待退出
#     result = subprocess.run(cmd)
#     return result.returncode


# ────────────────── 主流程 ──────────────────
def main(argv: List[str]) -> None:
    ap = argparse.ArgumentParser(
        description="批量检测 C64 ROM 是否可正常启动（基于 EmuELEC 默认配置）")
    ap.add_argument("-n", "--num", type=int, metavar="N",
                    help="仅抽取前 N 个（或随机 N 个，如配合 -r）的 ROM 检测")
    ap.add_argument("-r", "--random", action="store_true",
                    help="搭配 -n 时随机抽样，而非按字母顺序取前 N 个")
    ap.add_argument("-o", "--output", metavar="CSV",
                    help="把完整结果写入指定 CSV 路径")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="输出每个 ROM 的详细检测结果")
    args = ap.parse_args(argv)

    systems = parse_systems()
    c64_name = find_c64_name(systems)
    if not c64_name:
        sys.exit("❌ es_systems.cfg 未找到包含 'c64' 的 <name>，请检查配置。")

    cfg = systems[c64_name]
    rom_dir: pathlib.Path = cfg["rom_dir"]
    exts: List[str]       = cfg["ext"]

    roms = list_roms(rom_dir, exts)
    if not roms:
        sys.exit(f"❌ {rom_dir} 下未找到符合扩展名 {exts} 的 ROM")

    # 采样
    if args.num:
        if args.random:
            roms = random.sample(roms, min(args.num, len(roms)))
        else:
            roms = roms[: args.num]

    print(f"🎮 准备检测 {c64_name}（路径：{rom_dir}）共 {len(roms)} 个 ROM ...")

    # 统计
    passed, failed, results = 0, 0, []        # type: ignore
    t0 = time.time()

    for idx, rom in enumerate(roms, 1):
        status, used, reason = run_one(c64_name, rom, cfg)   # 复用现有函数
        if status == "PASS":
            passed += 1
        else:
            failed += 1
        if args.verbose or status == "FAIL":
            print(f"[{idx:>4}/{len(roms)}] {status:<4} {rom.name:<40} "
                  f"{used:>6.2f}s {reason}")
        results.append((rom.name, status, f"{used:.2f}", reason))

    cost = time.time() - t0
    print("\n========== 统计 ==========")
    print(f"PASS : {passed}")
    print(f"FAIL : {failed}")
    print(f"耗时 : {cost:.1f}s")

    # ─ 保存 CSV ─
    if args.output:
        out_path = pathlib.Path(args.output)
        with out_path.open("w", newline='', encoding="utf-8") as f:
            csv.writer(f).writerows(
                [("ROM", "STATUS", "TIME(s)", "REASON")] + results)
        print(f"✅ 结果已写入 {out_path.resolve()}")

    elif failed:
        # 若未指定 -o，但存在失败项，默认写一份时间戳文件
        ts_name = f"c64_result_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        out_path = REPORT_DIR / ts_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline='', encoding="utf-8") as f:
            csv.writer(f).writerows(
                [("ROM", "STATUS", "TIME(s)", "REASON")] + results)
        print(f"⚠️  已自动保存到 {out_path}")

    print("🎉 任务完成！")


# ====== 示例入口 ======
# def main():
#     # 假设你已经拿到了待测 ROM 列表 rom_list
#     rom_list = [
#         "/storage/roms/c64/1994 - Ten Years After (Europe).zip",
#         # ...
#     ]
#     for rom in rom_list:
#         code = run_c64_game(rom, dry_run=False)
#         if code != 0:
#             print(f"[ERROR] RetroArch 运行失败（退出码 {code}）: {rom}")
#         else:
#             print(f"[OK] 已结束: {rom}")


if __name__ == "__main__":
    main(sys.argv[1:])
import os
from typing import List

# 引入上一段代码中实现的函数
# from check_some_c64_games import run_c64_game
import os
import shlex
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

# RetroArch 与核心路径，请按实际环境调整
# RETROARCH_BIN = "/usr/bin/retroarch"
# VICE_CORE = "/tmp/cores/vice_x64sc_libretro.so"

SUPPORTED_EXTS = (".d64", ".t64", ".prg", ".tap", ".crt")

def _extract_first_image(zip_path: Path) -> Optional[Path]:
    """解压并返回压缩包里的第一个 Commodore 镜像文件"""
    with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as tmpdir:
        for name in zf.namelist():
            if name.lower().endswith(SUPPORTED_EXTS):
                target = Path(tmpdir, Path(name).name)
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                return target
    return None

# def run_c64_game(rom_path: str, dry_run: bool = False, verbose: bool = False) -> int:
#     """
#     启动一款 C64 游戏；返回 RetroArch 退出码。
#     失败的典型原因：核心路径错误、ROM 压缩包未解压、缺少 BIOS。
#     """
#     rom = Path(rom_path)
#     if rom.suffix.lower() == ".zip":
#         extracted = _extract_first_image(rom)
#         if extracted is None:
#             print(f"[WARN] ZIP 内未发现可用镜像：{rom}")
#             return 99
#         rom_to_use = extracted
#     else:
#         rom_to_use = rom

#     cmd = [
#         RETROARCH_BIN,
#         "-L", VICE_CORE,
#         str(rom_to_use),
#         "--verbose" if verbose else "--no-video",
#         "--quit-after", "10"          # 10 秒后自动退出，可按需调整
#     ]
#     print("[CMD]", " ".join(shlex.quote(c) for c in cmd))
#     if dry_run:
#         return 0

#     proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
#     output = proc.stdout.decode(errors="ignore")
#     if verbose:
#         print(output)

#     return proc.returncode

import os
import shlex
import subprocess
from pathlib import Path
from typing import List

# ===========================================================================
# 可按需要修改的全局常量
# ===========================================================================

RETROARCH_BIN: str = "retroarch"                        # RetroArch 可执行文件
VICE_CORE: str = "/tmp/cores/vice_x64_libretro.so"      # VICE x64 核心
RETROARCH_CFG: str = "/storage/.config/retroarch/retroarch.cfg"  # 主配置

# ===========================================================================
# 构造命令行
# ===========================================================================

def build_retroarch_cmd(rom_path: str) -> List[str]:
    """
    生成 RetroArch 命令行（与 EmuELEC 保持一致）：
        retroarch -v -L <core> --config <cfg> <rom>
    """
    return [
        RETROARCH_BIN,
        "-v",
        "-L", VICE_CORE,
        "--config", RETROARCH_CFG,
        rom_path
    ]


# ===========================================================================
# 运行指定 ROM
# ===========================================================================

def run_c64_game(rom_path: str, dry_run: bool = False) -> int:
    """
    使用 RetroArch + VICE 核心启动指定 ROM。
    • rom_path  可以是 .zip/.d64/.t64 等，按成功日志直接传入即可
    • dry_run   为 True 时仅打印命令，不真正执行（调试用）
    返回值：RetroArch 进程退出码
    """
    rom_abs = Path(rom_path).expanduser().resolve()
    if not rom_abs.exists():
        raise FileNotFoundError(f"ROM 文件不存在: {rom_abs}")

    cmd = build_retroarch_cmd(str(rom_abs))

    print("[INFO] 即将执行:", shlex.join(cmd))
    if dry_run:
        return 0

    # 启动 RetroArch 并等待退出
    result = subprocess.run(cmd)
    return result.returncode
import os
import pathlib
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

# ──────────────────────────────────────────────────────────────
# 新增：更健壮的 es_systems.cfg 搜索逻辑
# ──────────────────────────────────────────────────────────────
# 环境变量优先（用户可显式指定）
_env_cfg = os.environ.get("ES_CFG_FILE")  # 例如：ES_CFG_FILE="D:/roms/es_systems.cfg"

# 通用/平台默认路径（含 Windows 与 EmuELEC 常用目录）
_DEFAULT_CFG_LOCATIONS: List[pathlib.Path] = [
    pathlib.Path(_env_cfg).expanduser() if _env_cfg else None,
    pathlib.Path("~/.emulationstation/es_systems.cfg").expanduser(),
    pathlib.Path("/storage/.config/emulationstation/es_systems.cfg"),
    pathlib.Path("/emuelec/configs/emulationstation/es_systems.cfg"),
    pathlib.Path("./es_systems.cfg"),  # 当前工作目录
]

# 过滤掉 None 或不存在的条目，稍后再逐一检测
_DEFAULT_CFG_LOCATIONS = [p for p in _DEFAULT_CFG_LOCATIONS if p]

# 若文件顶部已声明 ES_CFG_FILES，则在此基础上追加默认路径；
# 若未声明，则创建空列表并追加默认路径。
try:
    ES_CFG_FILES  # type: ignore
except NameError:
    ES_CFG_FILES: List[pathlib.Path] = []

# 统一展开 ~ 并去重（保证搜索顺序：显式 → 默认）
_ES_SET = {str(p.resolve()) for p in ES_CFG_FILES}  # 已有路径集合作去重用
for p in _DEFAULT_CFG_LOCATIONS:
    if str(p.resolve()) not in _ES_SET:
        ES_CFG_FILES.append(p)

# ──────────────────────────────────────────────────────────────
# 原 parse_systems() 保持签名不变，仅对找不到文件时的逻辑做轻微改动
# ──────────────────────────────────────────────────────────────
def parse_systems() -> Dict[str, Dict[str, object]]:
    """
    解析 EmulationStation 的 es_systems.cfg，返回平台配置字典。
    """
    cfg_file: Optional[pathlib.Path] = next((f for f in ES_CFG_FILES if f.exists()), None)
    if cfg_file is None:
        searched = "\n  ".join(str(p) for p in ES_CFG_FILES)
        raise FileNotFoundError(
            "未找到 es_systems.cfg，请确认以下路径至少存在一份配置，"
            "或通过环境变量 ES_CFG_FILE 指定：\n  " + searched
        )

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