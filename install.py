"""地形桥接一键安装器。

双击 install.bat 会自动跑本脚本。它会:
  1. 自动检测 Houdini(hython.exe)和 Gaea(Gaea.exe)的安装位置
  2. 让你填(或自动识别)UE 项目路径
  3. 把插件文件复制到 <项目>/Content/Python/TerrainBridge/
  4. 自动生成 config.json(路径都填好)
  5. 建好 gaea_out / houdini_out / backups / logs 目录

跑完后只需要在 UE 里执行一次 ue_bridge.py。
"""

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# 自动检测
# --------------------------------------------------------------------------- #

def find_houdini():
    """找 hython.exe。先查注册表,再扫常见安装目录。"""
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(root, r"SOFTWARE\Side Effects Software\Houdini")
                idx = 0
                while True:
                    try:
                        name, _val, _type = winreg.EnumValue(key, idx)
                        idx += 1
                    except OSError:
                        break
                    # 值名可能是 InstallDir / 版本号,值本身是目录
                    if isinstance(_val, str) and os.path.isdir(_val):
                        exe = os.path.join(_val, "bin", "hython.exe")
                        if os.path.isfile(exe):
                            return exe
            except Exception:
                pass
    except ImportError:
        pass

    candidates = [
        r"C:\Program Files\Side Effects Software",
        r"C:\Program Files (x86)\Side Effects Software",
        r"C:\Games\Houdini",
        r"D:\Houdini",
    ]
    for base in candidates:
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base), reverse=True):
            exe = os.path.join(base, name, "bin", "hython.exe")
            if os.path.isfile(exe):
                return exe
    return None


def find_gaea():
    """找 Gaea.exe。先查注册表,再扫常见安装目录。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\QuadSpinner\Gaea\2.0")
        path, _ = winreg.QueryValueEx(key, "InstallPath")
        exe = os.path.join(path, "Gaea.exe")
        if os.path.isfile(exe):
            return exe
    except Exception:
        pass

    candidates = [
        r"C:\Program Files\QuadSpinner",
        r"C:\Program Files (x86)\QuadSpinner",
        r"C:\Games\Gaea 2",
        r"C:\Games\Gaea1",
        r"C:\Games\Gaea",
        r"C:\Program Files\QuadSpinner\Gaea",
        r"D:\Gaea",
    ]
    for base in candidates:
        exe = os.path.join(base, "Gaea.exe")
        if os.path.isfile(exe):
            return exe
        # 再往下找一层
        if os.path.isdir(base):
            for name in os.listdir(base):
                exe = os.path.join(base, name, "Gaea.exe")
                if os.path.isfile(exe):
                    return exe
    return None


def find_gaea_build_dirs():
    """自动找 Gaea(1/2)的 Build 输出目录,用于填 gaea_extra_roots。"""
    docs = os.path.join(os.path.expanduser("~"), "Documents", "Gaea")
    found = []
    for sub in ("Builds", "Build", "Output"):
        p = os.path.join(docs, sub)
        if os.path.isdir(p):
            found.append(p.replace("\\", "/"))
    return found


def detect_project_from_here():
    """如果本脚本已经在项目里,从当前目录往上找 .uproject。"""
    cur = HERE
    for _ in range(6):
        if os.path.isdir(cur):
            for f in os.listdir(cur):
                if f.endswith(".uproject"):
                    return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def ask_project():
    print()
    print("请把 UE 项目文件夹拖到这里(或输入路径,包含 .uproject 的那一层),回车确认:")
    path = input("> ").strip().strip('"').strip("'")
    if not path:
        return None
    if not os.path.isdir(path):
        print("  路径不存在:", path)
        return None
    if not any(f.endswith(".uproject") for f in os.listdir(path)):
        print("  这个文件夹里没找到 .uproject,请确认是项目根目录。")
        return None
    return path


# --------------------------------------------------------------------------- #
# 安装
# --------------------------------------------------------------------------- #

def build_config(project_root, gaea):
    """基于 config.example.json 生成填好路径的 config。"""
    bridge_root = os.path.join(project_root, "Content", "Python", "TerrainBridge")

    with open(os.path.join(HERE, "config.example.json"), encoding="utf-8") as f:
        cfg = json.load(f)

    def slash(p):
        return p.replace("\\", "/")

    p = cfg["paths"]
    p["project_root"] = slash(project_root)
    p["bridge_root"] = slash(bridge_root)
    p["gaea_out"] = slash(os.path.join(bridge_root, "gaea_out"))
    p["converted"] = slash(os.path.join(bridge_root, "converted"))
    p["backups"] = slash(os.path.join(bridge_root, "backups"))
    p["logs"] = slash(os.path.join(bridge_root, "logs"))
    # 自动检测 Gaea Build 输出目录(支持 Gaea 1/2)
    extra = find_gaea_build_dirs()
    p["gaea_extra_roots"] = extra if extra else [
        slash(os.path.join(os.path.expanduser("~"), "Documents", "Gaea", "Builds"))
    ]

    if gaea:
        cfg["gaea"]["gaea_exe"] = slash(gaea)

    # outputs: 转换后的高度图 + Gaea 直接导出的权重图
    out = cfg["outputs"]
    out["height_png"] = slash(os.path.join(bridge_root, "converted", "height_16bit.png"))
    for key in ("grass", "rock", "dirt"):
        out["weights"][key] = slash(os.path.join(bridge_root, "gaea_out", "W_%s.png" % key))
    return cfg


def install(project_root, gaea):
    bridge_root = os.path.join(project_root, "Content", "Python", "TerrainBridge")
    os.makedirs(bridge_root, exist_ok=True)

    copied = []
    for fname in ("terrain_bridge.py", "ue_import.py", "ue_bridge.py"):
        src = os.path.join(HERE, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(bridge_root, fname))
            copied.append(fname)

    cfg = build_config(project_root, gaea)
    with open(os.path.join(bridge_root, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    for sub in ("gaea_out", "converted", "backups", "logs"):
        os.makedirs(os.path.join(bridge_root, sub), exist_ok=True)

    return bridge_root, copied


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main():
    print("=" * 56)
    print("  Gaea -> UE5 地形桥接  一键安装")
    print("=" * 56)

    print("\n[1/3] 检测 Gaea ...")
    gaea = find_gaea()
    print("  找到:", gaea if gaea else "未找到(稍后可手动改 config.json)")

    print("\n[2/3] 定位 UE 项目 ...")
    project = detect_project_from_here()
    if project:
        print("  从当前目录识别到:", project)
    else:
        project = ask_project()
    if not project:
        print("\n[失败] 没拿到有效的 UE 项目路径,安装中止。")
        return 1

    print("\n[3/3] 复制文件 + 生成 config ...")
    bridge_root, copied = install(project, gaea)
    print("  安装到:", bridge_root)
    for f in copied:
        print("    +", f)
    print("    + config.json(路径已自动填好)")

    print("\n" + "=" * 56)
    print("安装完成!接下来只剩一步:")
    print("=" * 56)
    print("\n打开 UE 编辑器,在 Python 控制台执行一次:")
    print('  exec(open(r"%s", encoding="utf-8").read())' % os.path.join(bridge_root, "ue_bridge.py"))
    print("\n(如果找不到 Python 控制台:窗口 -> 输出日志 -> 左下角下拉选 Python)")
    print("\n然后就能用了:")
    print("  python %s" % os.path.join(bridge_root, "terrain_bridge.py"))
    print("  python %s --watch   # 监听模式" % os.path.join(bridge_root, "terrain_bridge.py"))
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("\n[安装出错]", exc)
        import traceback
        traceback.print_exc()
        sys.exit(1)
