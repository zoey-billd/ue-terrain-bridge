# Gaea → Houdini → UE5 地形一键更新

把 **Gaea** 做的地形,一键经过 **Houdini** 处理(生成 16-bit 高度图 + 坡度/海拔/曲率权重图),再自动导入 **UE5 Landscape** 并重生成 **PCG** —— 不用手动导图、手动摆 Landscape、手动刷图层。

```
Gaea 导出高度图
   ↓ 自动检测
Houdini(hython)处理:统一分辨率 + 生成 3 张权重图
   ↓
UE5:导入高度图 + 权重图,更新 Landscape,重生成 PCG
```

## 依赖

| 依赖 | 版本 | 说明 |
|---|---|---|
| Unreal Engine | 5.6 | 项目需启用 Python 插件 + TCP bridge(见下) |
| Houdini | 21.0+ | 用 `hython.exe` 跑处理脚本 |
| Gaea | 2.x | 用来做地形、导出高度图(EXR/PNG) |

> 注意:本插件是「Python 脚本 + UE 端 bridge」,不是 UE 二进制插件,不需要编译。

## 安装(推荐:一键安装)

**双击 `install.bat`**,按提示把 UE 项目文件夹拖进去(或输入路径),回车即可。它会自动:

- 检测 Houdini / Gaea 的安装位置
- 把插件复制到 `<项目>/Content/Python/TerrainBridge/`
- 生成填好路径的 `config.json`
- 建好 `gaea_out/ houdini_out/ backups/ logs/` 目录

装完只剩一步:**在 UE 里执行一次 `ue_bridge.py`**(见「UE 端 bridge」)。

### 手动安装(一键安装失败时)

1. 把整个文件夹复制到 `YourProject/Content/Python/TerrainBridge/`
2. 复制 `config.example.json` 为 `config.json`
3. 打开 `config.json`,把所有 `C:/Path/To/...` 改成你的实际路径(见下)
4. UE 端挂上 TCP bridge(见「UE 端 bridge」)

## 配置说明(config.json)

| 字段 | 改成什么 |
|---|---|
| `paths.project_root` | 你的 UE 项目根目录,如 `C:/MyProject` |
| `paths.bridge_root` | 本插件所在目录,如 `C:/MyProject/Content/Python/TerrainBridge` |
| `paths.gaea_out` | 插件内的 `gaea_out/`(放 Gaea 导出的高度图) |
| `paths.gaea_extra_roots` | Gaea 自己的 Build 输出目录(可选,自动扫描用) |
| `gaea.gaea_exe` | Gaea 安装位置,如 `C:/Program Files/QuadSpinner/Gaea/Gaea.exe` |
| `houdini.hython` | Houdini 的 hython,如 `C:/Program Files/Side Effects Software/Houdini 21.0.631/bin/hython.exe` |
| `unreal.level_path` | 要导入的关卡,如 `/Game/Maps/MainLevel` |
| `unreal.landscape_label` | 你的 Landscape Actor 名字(默认 `Landscape`) |
| `unreal.layer_mapping` | 你的 Landscape 图层名 → 权重图(默认 Layer_1=grass / Layer_2=rock / Layer_3=dirt) |
| `unreal.regen_pcg_labels` | 更新地形后要重生成的 PCG 组件标签(没有可留 `[]`) |

## Gaea 1 用户注意

本插件**支持 Gaea 1**(实测 1.3.1.4):

- **用 watch 模式**(默认),不要用 cli 模式
- Gaea 1 的导出目录通常在:`文档/Gaea/Builds/<项目>/<序号>/<节点>_Out.exr`
- 在 `config.json` 里把 `gaea_extra_roots` 指向它:
  ```json
  "gaea_extra_roots": [
    "C:/Users/你的用户/Documents/Gaea/Builds"
  ]
  ```
- 在 Gaea 1 里 Build 导出后,插件会自动检测到高度图

> 注意:Gaea 1 的命令行 `Gaea.Build.exe` 语法和 Gaea 2 不同(直接传 `.tor` 文件),
> 且实测可能报错,所以 **cli 模式不推荐 Gaea 1 使用**,请用 watch 模式。

## UE 端 bridge

`ue_import.py` 需要 UE 里有一个 TCP bridge 来接收并执行。把 `ue_bridge.py` 放到
`YourProject/Content/Python/`,然后在 UE 的 Python 控制台执行一次:

```python
exec(open(r"C:/MyProject/Content/Python/ue_bridge.py", encoding="utf-8").read())
```

或者在项目的 `init_unreal.py` 里加一行让它每次启动自动挂上。bridge 监听 `9877`。

## 用法

```bash
# 完整流程:Gaea -> Houdini -> UE
python terrain_bridge.py

# 监听模式:在 Gaea 里点 Build 导出后自动触发
python terrain_bridge.py --watch

# 只跑某一段
python terrain_bridge.py --houdini-only   # 只 Houdini 处理
python terrain_bridge.py --ue-only        # 只导入 UE

# 只打印计划不执行
python terrain_bridge.py --dry-run

# 回退到上一次更新前
python terrain_bridge.py --rollback
```

## 工作流

1. **在 Gaea 里做地形**,点 Build 导出高度图(EXR/PNG/r16 都行)
2. 把高度图放到 `gaea_out/`(或让 `--watch` 自动扫描 Gaea 的 Build 目录)
3. 跑 `terrain_bridge.py`(或开着 `--watch`)
4. Houdini 处理 → 输出 16-bit 高度图 + 3 张权重图到 `houdini_out/`
5. UE 自动导入 Landscape,更新图层,重生成 PCG

## 常见问题

**Q: 提示"未找到 Gaea 高度图"**
- 先在 Gaea 里 Build 一次,确认导出到了 `gaea_out/` 或 `gaea_extra_roots` 里

**Q: Houdini 处理失败**
- 检查 `config.json` 里 `houdini.hython` 路径是否指向 `hython.exe`(不是 `houdini.exe`)
- 手动跑一次:`hython houdini_process.py <高度图> <输出高度图> <草> <岩> <土>`

**Q: UE 导入报"已启用编辑层,无法导入"**
- 你的 Landscape 启用了 Edit Layer,需要先在 UE 里关闭编辑层再导入

**Q: 地形导入了但 PCG 没更新**
- 检查 `unreal.regen_pcg_labels` 是否填了你的 PCG 组件标签

## 许可

MIT(可自行修改使用)
