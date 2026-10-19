# Gaea → UE5 地形一键更新

把 **Gaea** 做的地形,一键导入 **UE5 Landscape** 并重生成 **PCG** —— 不用手动导图、手动摆 Landscape、手动刷图层。

```
Gaea 导出高度图 + 权重图
   ↓ 自动检测
Python 转换:EXR 高度图 → 16-bit PNG(UE 只认这个)
   ↓
UE5:导入高度图 + 权重图,更新 Landscape,重生成 PCG
```

## 依赖

| 依赖 | 版本 | 说明 |
|---|---|---|
| Unreal Engine | 5.6 | 项目需启用 Python 插件 + TCP bridge(见下) |
| Gaea | 1.x / 2.x | 用来做地形、导出高度图 + 权重图 |
| Python 库 | — | `PIL`、`numpy`、`OpenEXR`(UE 自带 Python 已包含) |

> **不需要 Houdini**。高度图格式转换用 Python 完成。

## 安装(推荐:一键安装)

**双击 `install.bat`**,按提示把 UE 项目文件夹拖进去(或输入路径),回车即可。它会自动:

- 检测 Gaea 安装位置
- 把插件复制到 `<项目>/Content/Python/TerrainBridge/`
- 生成填好路径的 `config.json`
- 建好 `gaea_out/ converted/ backups/ logs/` 目录

装完只剩一步:**在 UE 里执行一次 `ue_bridge.py`**(见「UE 端 bridge」)。

### 手动安装(一键安装失败时)

1. 把整个文件夹复制到 `YourProject/Content/Python/TerrainBridge/`
2. 复制 `config.example.json` 为 `config.json`
3. 打开 `config.json`,把所有 `C:/Path/To/...` 改成你的实际路径
4. UE 端挂上 TCP bridge

## 配置说明(config.json)

| 字段 | 改成什么 |
|---|---|
| `paths.project_root` | 你的 UE 项目根目录 |
| `paths.bridge_root` | 本插件所在目录 |
| `paths.gaea_out` | 放 Gaea 导出的高度图 + 权重图 |
| `paths.converted` | 转换后的 16-bit PNG 输出目录 |
| `outputs.height_png` | 转换后的高度图(插件自动生成) |
| `outputs.weights.grass/rock/dirt` | **Gaea 导出的权重图**(`W_grass.png` 等) |
| `unreal.level_path` | 要导入的关卡 |
| `unreal.landscape_label` | 你的 Landscape Actor 名字 |
| `unreal.layer_mapping` | 图层名 → 权重图 |

## Gaea 侧要导出的东西

在 Gaea 里配置输出节点,导出到 `gaea_out/`:

1. **高度图**:`height.exr`(或直接导出 16-bit PNG)
2. **权重图**:`W_grass.png`、`W_rock.png`、`W_dirt.png`(8-bit PNG)

> Gaea 1 的导出目录通常在 `文档/Gaea/Builds/...`,可以把该目录加进 `paths.gaea_extra_roots`。

## Gaea 1 用户注意

- **用 watch 模式**(默认),不要用 cli 模式
- Gaea 1 的导出目录:`文档/Gaea/Builds/<项目>/<序号>/<节点>_Out.exr`,把 `Documents/Gaea/Builds` 加进 `gaea_extra_roots`
- Gaea 1 的命令行 `Gaea.Build.exe` 语法和 Gaea 2 不同,实测可能报错,**cli 模式不推荐**

## UE 端 bridge

`ue_import.py` 需要 UE 里有一个 TCP bridge 来接收并执行。把 `ue_bridge.py` 放到
`YourProject/Content/Python/`,然后在 UE 的 Python 控制台执行一次:

```python
exec(open(r"C:/MyProject/Content/Python/ue_bridge.py", encoding="utf-8").read())
```

bridge 监听 `9877`。

## 用法

```bash
python terrain_bridge.py                  # 完整流程
python terrain_bridge.py --watch          # 监听模式(Gaea Build 后自动触发)
python terrain_bridge.py --convert-only   # 只转换高度图(EXR -> 16-bit PNG)
python terrain_bridge.py --ue-only        # 只导入 UE
python terrain_bridge.py --dry-run        # 只打印计划不执行
python terrain_bridge.py --rollback       # 回退到上一次更新前
```

## 工作流

1. 在 Gaea 里做地形,配置输出:高度图 + 3 张权重图
2. 导出到 `gaea_out/`
3. 跑 `terrain_bridge.py`(或开着 `--watch`)
4. 插件把 EXR 转成 16-bit PNG
5. UE 自动导入 Landscape + 图层 + 重生成 PCG

## 常见问题

**Q: 提示"未找到高度图"**
- 先确认 Gaea 导出了高度图到 `gaea_out/` 或 `gaea_extra_roots`

**Q: EXR 转换失败**
- 检查 Python 是否有 OpenEXR:`python -c "import OpenEXR"`
- 没有的话 `pip install OpenEXR`,或在 Gaea 里直接导出 16-bit PNG

**Q: 权重图找不到**
- 在 Gaea 里配置输出 PNG,放到 `gaea_out/`,文件名默认 `W_grass.png` 等
- 或者改 `outputs.weights` 指向你的文件

**Q: UE 导入报"已启用编辑层"**
- 你的 Landscape 启用了 Edit Layer,需要先关闭编辑层再导入

## 许可

MIT
