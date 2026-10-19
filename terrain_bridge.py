# -*- coding: utf-8 -*-
"""
Gaea -> Houdini -> UE5 地形一键更新编排器.

用法:
  python terrain_bridge.py                  # 完整流程: Gaea -> Houdini -> UE
  python terrain_bridge.py --skip-gaea      # 跳过 Gaea, 直接用 gaea_out/height.*
  python terrain_bridge.py --houdini-only   # 只跑 Houdini 处理
  python terrain_bridge.py --ue-only        # 只跑 UE 导入+PCG重生成
  python terrain_bridge.py --dry-run        # 只打印计划, 不执行

工作流:
  1. Gaea: 等待 gaea_out/ 下的高度图 (用户从 Gaea 导出, 或 CLI 自动导出)
  2. Houdini: hython 运行 houdini_process.py, 生成 16-bit 高度图 + 3 张权重图
  3. UE: 通过 TCP 桥 (9877) 发送 ue_import.py, 导入高度图+权重图, 重生成 PCG
"""
import argparse
import json
import os
import subprocess
import sys
import time
import shutil
import socket
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
UE_PYTHON = r"C:\Program Files\Epic Games\UE_5.6\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print("[%s] %s" % (ts, msg))


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def search_dirs(cfg):
    """返回所有可能的高度图输出目录名单.

    Gaea 的 Build 输出根目录会随偏好/项目设置变化, 因此同时搜索多个候选位置,
    避免要求用户必须在 Gaea 里手动改输出路径.
    """
    p = cfg["paths"]
    roots = [p["gaea_out"]]
    for extra in p.get("gaea_extra_roots", []):
        if extra not in roots:
            roots.append(extra)
    return roots


def find_heightmap(folder):
    """在指定文件夹(含子目录)中查找高度图, 返回最新修改的文件.

    folder 可以是单个路径字符串, 也可以是路径列表.
    Gaea Build Manager 会把输出放在 [Builds]/[Filename]/[Timestamp]/ 子目录下,
    因此需要递归搜索并选择最近修改的高度图.
    """
    dirs = [folder] if isinstance(folder, str) else list(folder)
    exts = (".exr", ".png", ".tif", ".tiff", ".r16", ".raw")
    candidates = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.lower().endswith(exts):
                    candidates.append(os.path.join(root, f))
    if not candidates:
        return None
    # 按修改时间降序, 取最新的
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


# ── Backup / rollback ───────────────────────────────────────────────────────
def backups_root(cfg):
    return cfg["paths"].get("backups") or os.path.join(cfg["paths"]["bridge_root"], "backups")


def applied_output_files(cfg):
    """返回当前"已应用到 UE"的地形数据文件 (高度图 + 3 张权重图)."""
    names = [os.path.basename(cfg["houdini"]["output_height"])]
    names += [os.path.basename(v) for v in cfg["houdini"]["output_weights"].values()]
    return names


def backup_outputs(cfg):
    """把当前已应用的地形数据快照到 backups/<时间戳>/.

    在覆盖 houdini_out 之前调用, 因此最近一份快照就是"上一次更新前的状态",
    回退时重放它即可把地形还原回去.
    """
    src_dir = cfg["paths"]["houdini_out"]
    present = [n for n in applied_output_files(cfg)
               if os.path.exists(os.path.join(src_dir, n))]
    if not present:
        log("[Backup] 无可备份的地形数据, 跳过")
        return None
    dst = os.path.join(backups_root(cfg), datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(dst, exist_ok=True)
    for n in present:
        shutil.copy2(os.path.join(src_dir, n), os.path.join(dst, n))
    log("[Backup] 已备份当前地形数据 -> %s" % dst)
    return dst


def restore_latest_backup(cfg):
    """把最近一次快照恢复到 houdini_out; 返回快照目录 (无备份则 None)."""
    root = backups_root(cfg)
    if not os.path.isdir(root):
        return None
    snaps = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    if not snaps:
        return None
    snap = os.path.join(root, snaps[-1])
    dst_dir = cfg["paths"]["houdini_out"]
    os.makedirs(dst_dir, exist_ok=True)
    for n in os.listdir(snap):
        shutil.copy2(os.path.join(snap, n), os.path.join(dst_dir, n))
    log("[Rollback] 已恢复备份: %s" % snap)
    return snap


# ── Stage 1: Gaea ──────────────────────────────────────────────────────────
def stage_gaea(cfg, dry_run=False):
    """Gaea 导出阶段. watch 模式等待手动导出, cli 模式尝试命令行导出."""
    gaea = cfg["gaea"]
    out_dir = gaea.get("heightmap_dir") or search_dirs(cfg)

    if not gaea.get("enabled", True):
        log("[Gaea] 已禁用, 跳过")
        return find_heightmap(out_dir)

    # 检查是否已有导出的高度图
    existing = find_heightmap(out_dir)
    if existing:
        log("[Gaea] 发现已有高度图: %s" % existing)
        return existing

    if gaea.get("mode") == "watch":
        log("[Gaea] watch 模式: 等待高度图导出到 %s" % out_dir)
        log("[Gaea] 请从 Gaea 中导出高度图 (File > Export > Heightmap)")
        # 等待最多 10 分钟
        timeout = 600
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(5)
            h = find_heightmap(out_dir)
            if h:
                log("[Gaea] 检测到高度图: %s" % h)
                return h
            elapsed = int(time.time() - start)
            if elapsed % 30 == 0:
                log("[Gaea] 等待中... (%ds)" % elapsed)
        log("[Gaea] 超时, 未检测到高度图")
        return None

    # CLI 模式 (Gaea BuildManager)
    gaea_exe = gaea.get("gaea_exe", "")
    gae_proj = gaea.get("gae_project", "")
    if not gaea_exe or not os.path.exists(gaea_exe):
        log("[Gaea] Gaea 可执行文件未找到: %s" % gaea_exe)
        return None
    if not gae_proj or not os.path.exists(gae_proj):
        log("[Gaea] Gaea 项目文件未找到: %s" % gae_proj)
        return None

    out_path = os.path.join(out_dir, gaea.get("heightmap_filename", "height.exr"))
    cmd = [gaea_exe, "-build", gae_proj, "-output", out_path]
    log("[Gaea] 执行: %s" % " ".join(cmd))
    if dry_run:
        return out_path
    try:
        subprocess.run(cmd, check=True, timeout=600)
        if os.path.exists(out_path):
            log("[Gaea] 导出成功: %s" % out_path)
            return out_path
    except Exception as e:
        log("[Gaea] 导出失败: %s" % e)
    return None


# ── Stage 2: Houdini ───────────────────────────────────────────────────────
def stage_houdini(cfg, input_heightmap, dry_run=False):
    """Houdini 处理阶段: 高度图 -> 高度图 + 权重图."""
    h = cfg["houdini"]
    if not h.get("enabled", True):
        log("[Houdini] 已禁用, 跳过")
        return True

    hython = h["hython"]
    script = h["script"]

    if not os.path.exists(hython):
        log("[Houdini] hython 未找到: %s" % hython)
        return False
    if not os.path.exists(script):
        log("[Houdini] 脚本未找到: %s" % script)
        return False

    out_h = h["output_height"]
    out_g = h["output_weights"]["grass"]
    out_r = h["output_weights"]["rock"]
    out_d = h["output_weights"]["dirt"]

    cmd = [hython, script, input_heightmap, out_h, out_g, out_r, out_d]
    log("[Houdini] 执行处理...")
    log("[Houdini] 输入: %s" % input_heightmap)

    if dry_run:
        log("[Houdini] dry-run, 跳过执行")
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        print(result.stdout)
        if result.returncode != 0:
            log("[Houdini] 处理失败 (exit=%d)" % result.returncode)
            if result.stderr:
                print(result.stderr)
            return False
        # 验证输出
        for p in (out_h, out_g, out_r, out_d):
            if not os.path.exists(p):
                log("[Houdini] 输出缺失: %s" % p)
                return False
        log("[Houdini] 处理完成")
        return True
    except subprocess.TimeoutExpired:
        log("[Houdini] 处理超时")
        return False
    except Exception as e:
        log("[Houdini] 执行错误: %s" % e)
        return False


# ── Stage 3: UE import + PCG regen ─────────────────────────────────────────
def send_to_bridge(code, port=9877, timeout=600):
    """发送 Python 代码到 UE TCP 桥."""
    for p in (port, 9876):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", p))
            s.sendall(json.dumps({"code": code}).encode())
            chunks = []
            while True:
                try:
                    b = s.recv(65536)
                    if not b:
                        break
                    chunks.append(b)
                except socket.timeout:
                    break
            return b"".join(chunks).decode("utf-8", "replace")
        except Exception as e:
            last = e
        finally:
            s.close()
    return json.dumps({"ok": False, "error": "bridge connect failed: %s" % last})


def stage_ue(cfg, dry_run=False):
    """UE 导入阶段: 导入高度图+权重图, 重生成 PCG."""
    ue = cfg["unreal"]
    h = cfg["houdini"]

    # 构造注入到 ue_import.py 的配置 JSON
    inject_cfg = {
        "height_png": h["output_height"],
        "weight_grass": h["output_weights"]["grass"],
        "weight_rock": h["output_weights"]["rock"],
        "weight_dirt": h["output_weights"]["dirt"],
        "layer_mapping": ue["layer_mapping"],
        "landscape_label": ue["landscape_label"],
        "regen_pcg_labels": ue["regen_pcg_labels"],
        "level_path": ue.get("level_path", "/Game/ThirdPerson/Lvl_ThirdPerson1"),
        "save_after": ue.get("save_after", True),
        "height_scale_z": ue.get("height_scale_z", 100),
    }

    # 读取 ue_import.py 模板, 替换配置占位符
    ue_script_path = os.path.join(SCRIPT_DIR, "ue_import.py")
    with open(ue_script_path, encoding="utf-8") as f:
        code = f.read()
    # 只替换占位符, 保留 r'''...''' 引号包裹, 确保 json.loads 收到字符串
    code = code.replace("__CFG_JSON__", json.dumps(inject_cfg, ensure_ascii=False))

    log("[UE] 发送导入脚本到 TCP 桥 (port %d)..." % ue["bridge_port"])
    if dry_run:
        log("[UE] dry-run, 跳过执行")
        log("[UE] 配置: %s" % json.dumps(inject_cfg, ensure_ascii=False)[:200])
        return True

    result = send_to_bridge(code, port=ue["bridge_port"], timeout=600)
    try:
        parsed = json.loads(result)
        if parsed.get("ok") is False:
            log("[UE] 桥接错误: %s" % parsed.get("error", "unknown"))
            return False
        if "result" in parsed:
            print(parsed["result"])
        elif "message" in parsed:
            log("[UE] 错误: %s" % parsed["message"])
            return False
        else:
            print(result)
    except Exception:
        print(result)

    log("[UE] 导入完成")
    return True


# ── Watch mode: auto-trigger when Gaea exports a new heightmap ──────────────
def run_pipeline(cfg, dry_run=False):
    """Run Houdini + UE stages (used by watch mode and direct calls)."""
    heightmap = find_heightmap(search_dirs(cfg))
    if not heightmap:
        log("未找到高度图 (检查 %s)" % ", ".join(search_dirs(cfg)))
        return False
    log("[Gaea] 使用高度图: %s" % heightmap)

    if not dry_run:
        backup_outputs(cfg)

    log("--- Houdini 处理 ---")
    if not stage_houdini(cfg, heightmap, dry_run=dry_run):
        log("Houdini 阶段失败")
        return False

    log("--- UE 导入 + PCG 重生成 ---")
    if not stage_ue(cfg, dry_run=dry_run):
        log("UE 阶段失败")
        return False
    return True


def watch_mode(cfg, interval=5.0):
    """监听 Gaea 可能输出高度图的目录, 检测到新文件就自动跑流水线.

    搜索多个候选根目录, 因此不管 Gaea 的 Build 输出落在哪 (默认 Builds 目录
    或 TerrainBridge/gaea_out), 都能被捕获, 用户无需手动改路径.
    """
    watch_dirs = search_dirs(cfg)
    log("[Watch] 监听目录: %s" % "; ".join(watch_dirs))
    log("[Watch] 在 Gaea 中点击 Build 导出高度图即可自动触发更新")
    log("[Watch] 按 Ctrl+C 停止监听")

    last_mtime = 0.0
    # seed with current file mtime so we don't re-trigger on startup
    hm = find_heightmap(watch_dirs)
    if hm and os.path.exists(hm):
        last_mtime = os.path.getmtime(hm)
        log("[Watch] 当前高度图: %s" % os.path.basename(hm))

    while True:
        try:
            hm = find_heightmap(watch_dirs)
            if hm and os.path.exists(hm):
                mtime = os.path.getmtime(hm)
                if mtime > last_mtime + 1.0:
                    log("[Watch] 检测到新高度图: %s" % os.path.basename(hm))
                    last_mtime = mtime
                    ok = run_pipeline(cfg)
                    if ok:
                        log("[Watch] ✅ 自动更新完成, 继续监听...")
                    else:
                        log("[Watch] ❌ 更新失败, 继续监听...")
            time.sleep(interval)
        except KeyboardInterrupt:
            log("[Watch] 已停止监听")
            break
        except Exception as e:
            log("[Watch] 错误: %s" % e)
            time.sleep(interval)


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Gaea -> Houdini -> UE5 地形桥接")
    parser.add_argument("--skip-gaea", action="store_true", help="跳过 Gaea 阶段")
    parser.add_argument("--houdini-only", action="store_true", help="只跑 Houdini")
    parser.add_argument("--ue-only", action="store_true", help="只跑 UE 导入")
    parser.add_argument("--watch", action="store_true", help="监听 Gaea 导出目录, 自动触发更新")
    parser.add_argument("--rollback", action="store_true", help="回退到上一次更新前的地形数据")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划")
    parser.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    args = parser.parse_args()

    cfg = load_config()

    if args.watch:
        log("=" * 60)
        log("Gaea -> Houdini -> UE5 地形桥接 (监听模式)")
        log("=" * 60)
        watch_mode(cfg)
        return

    if args.rollback:
        log("=" * 60)
        log("地形回退: 恢复到上一次更新前的状态")
        log("=" * 60)
        if not restore_latest_backup(cfg):
            log("没有可用备份, 无法回退")
            sys.exit(1)
        sys.exit(0 if stage_ue(cfg, dry_run=args.dry_run) else 1)

    log("=" * 60)
    log("Gaea -> Houdini -> UE5 地形桥接")
    log("=" * 60)

    # 决定运行哪些阶段
    run_gaea = not (args.skip_gaea or args.houdini_only or args.ue_only)
    run_houdini = not args.ue_only
    run_ue = not args.houdini_only

    # Stage 1: Gaea
    heightmap = None
    if run_gaea:
        log("--- Stage 1: Gaea 导出 ---")
        heightmap = stage_gaea(cfg, dry_run=args.dry_run)
        if not heightmap:
            log("Gaea 阶段失败, 终止")
            sys.exit(1)
    else:
        # 跳过 Gaea, 直接查找已有高度图
        heightmap = find_heightmap(search_dirs(cfg))
        if not heightmap:
            log("未找到高度图 (检查 %s)" % ", ".join(search_dirs(cfg)))
            sys.exit(1)
        log("[Gaea] 使用已有高度图: %s" % heightmap)

    # Stage 2: Houdini
    if run_houdini:
        if not args.dry_run:
            backup_outputs(cfg)
        log("--- Stage 2: Houdini 处理 ---")
        ok = stage_houdini(cfg, heightmap, dry_run=args.dry_run)
        if not ok:
            log("Houdini 阶段失败, 终止")
            sys.exit(1)

    # Stage 3: UE
    if run_ue:
        # --ue-only 会直接覆盖地形, 同样需要先快照
        if not args.dry_run and not run_houdini:
            backup_outputs(cfg)
        log("--- Stage 3: UE 导入 + PCG 重生成 ---")
        ok = stage_ue(cfg, dry_run=args.dry_run)
        if not ok:
            log("UE 阶段失败")
            sys.exit(1)

    log("=" * 60)
    log("全部完成!")
    log("=" * 60)


if __name__ == "__main__":
    main()
