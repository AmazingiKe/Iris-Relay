# MaterialX 核心

Iris Relay 0.4.0 使用官方 MaterialX 1.39.5 SDK，提供材质文档校验、节点定义查询、自定义节点保存和受限原生转换。插件仍只调用公开 Python API，不需要 HTTP，也不需要每次经过磁盘文件。

## 最小调用

核心位于仓库根目录的 `iris_relay/`，无需安装项目自身。在仓库根目录运行 `uv sync --extra dev` 安装第三方依赖，然后：

```python
from pathlib import Path
from iris_relay import RelayAPI

api = RelayAPI.from_directory("dictionaries")
document = Path("examples/materialx-example.mtlx").read_text(encoding="utf-8")
response = api.handle({
    "api_version": "1",
    "request_id": "检查材质",
    "operation": "materialx.normalize",
    "payload": {"document": document},
})
if response["status"] != "error":
    normalized = response["result"]["document"]
```

只处理 MaterialX 文档时使用 `RelayAPI()` 即可，不需要宿主字典。仅 `export` 和 `convert` 需要相应字典。调用方应保留响应中的 `source` 和诊断，只有目标材质非空且符合自身警告策略时才创建宿主材质。

完整示例：

```powershell
uv run python examples/materialx_demo.py --out .local/materialx-demo-新编号
```

输出规范化 `.mtlx` 和转换响应 JSON，目录必须不存在。示例中的自定义表面节点通过标准 `standard_surface` 子图实现；演示生成 Blender 原生节点数据，不自动启动 Blender。

## 操作

所有操作使用同一 API v1 请求外层及 `ok / warning / error` 响应。

| operation | payload | 结果 |
| --- | --- | --- |
| `materialx.definitions` | `{}`；可选 `category` 和 `document` | 官方 NodeDef，以及指定文档内联的自定义 NodeDef；端口类型、默认值字符串及原始属性 |
| `materialx.validate` | `{"document": XML字符串}` | `source`、`valid`、`issues`；`document` 为 null，不执行着色 |
| `materialx.normalize` | `{"document": XML字符串, "flatten": false}` | 校验后输出规范化 XML；可选 `flatten=true` 展开自定义节点子图，保留标准着色节点 |
| `materialx.export` | `{"material": 既有原生JSON材质图}` | 标准 MaterialX `document`、源图 `source` 和 `report` |
| `materialx.convert` | `{"document": XML字符串, "target": 渲染器引用}`；可选 `material_name` | 目标 JSON `material`、原文 `source` 和 `report`；多材质文档必须选择顶层 surfacematerial 名称 |

文档校验失败时 `valid=false`，规范化输出为 null；目标转换失败时 `material=null`。输入结构错误或字典缺失时遵循既有 API，`result=null`。合法专有节点没有实现时，文档仍可保存，返回 `MATERIALX_IMPLEMENTATION_MISSING` 警告；缺少目标转换规则则阻断转换。

`capabilities.result.materialx` 给出 SDK 版本、标准 NodeDef 数量、是否可用及执行边界。标准库查询返回的默认值是 MaterialX 字符串，不是另一套自定义参数定义。没有给定 `category` 时返回全部定义。

## 文档与自定义节点

支持内联 NodeDef、标准类型端口、节点图、连接、资源路径和自定义属性。通过 SDK 读写，原始 XML 同时保留在响应 `source`；规范化可能改变空白和属性顺序，不是字节级复制。

自定义节点可用标准／自定义节点子图实现，或仅保存针对某目标的代码实现声明。核心不会执行外部实现，也不因此宣称目标渲染器能执行该节点。子图递归、连接环、类型冲突、未知定义和非有限数值明确报错。输入文档限 8 MiB、20,000 个 XML 元素、64 层；展开前预估规模并检查展开后规模。

自定义库须内联，不能重定义标准库同名元素或未加命名空间的标准类别。输入中的 DTD、实体声明、XInclude 被拒绝；不从文档指定路径加载代码或库。图片路径仅保存为数据，其存在性由宿主适配器检查。

## 当前目标转换边界

文档可保存的范围大于当前目标可转换的范围。桥接支持既有三套字典的五类功能：

| MaterialX | 既有字典语义 | 限制 |
| --- | --- | --- |
| `standard_surface`，默认版本 1.0.1 | `surface.standard` | 已映射基础参数；其他参数仅允许官方默认值，非默认或连接阻断 |
| `image`，color3 | `texture.image` | 非空外部路径、默认 UV／采样；文件输入明确 `raw` 或 `srgb_texture`；不处理 Alpha |
| `normalmap`，float scale | `normal.tangent` | 颜色连接须显式 `convert(color3→vector3)`；几何法线／切线使用默认值 |
| `bump`，vector3 | `bump.height` | 标量高度；高度幅度和空间导数只作近似对应 |
| `extract`，color3→float | `channel.red` | 仅 index=0 |

自定义子图展开后全部相关节点落在以上范围内才可转换；否则保留原文并报告不支持。标准库中的其他节点可查询、校验和保存，但没有自动获得目标转换能力。未知原生节点导出失败时保留原始 JSON，不自动猜测并编造自定义 NodeDef。

当前桥接拒绝尚未处理的单位、文件前缀、通道重排和非图片输入色彩空间上下文。只转换选定材质可达的图；未选用的顶层节点有信息报告，完整内容保留在源文档。资源、节点 metadata 等原生专用信息也保留在响应源快照中，不保证仅凭标准 `.mtlx` 重建所有原生信息。

旧 `convert` 继续使用旧语义链路；新路径显式使用 `materialx.export`／`materialx.convert`。Maya Arnold 的原生读取和字典尚未实现，不应把 C4DtoA 字典套用到 Maya。当前不会生成 USD、自动训练网络或证明外观一致。

## 验证与复现

```powershell
uv run pytest tests/test_materialx_core.py tests/test_materialx_bridge.py -q
uv run python scripts/verify_materialx.py --blender "本机blender.exe路径" --out .local/materialx-check-新编号
```

独立验证脚本自行生成小型纹理，以字典夹具导出两条标准 MaterialX 链路，再转入 Blender，创建、保存、重开并检查参数、连接、资源及失败清理。最后将真实读回结果再次导出 `.mtlx`。脚本只创建独立文件，不打开用户场景。通过条件同时检查宿主退出码、此次 run_id 和报告状态。

2026-11-06 本机验证：Blender 5.2.3 LTS，`run_id=805f2ecd-60aa-48cd-8b3a-da61de23764a`，两条链路通过。忽略目录 `.local/materialx-verify-03/summary.json` 保存报告。没有新增 Maya 验证或渲染图像，所有结果 `appearance_verified=false`。

同日完整自动测试 160 项通过，总覆盖率 91%；MaterialX 文档核心与桥接分别达到 92% 和 88%。检查包含 1,100 层节点连接链、标准库覆盖拒绝、输入限制及本地 JSON 子进程。Ruff 检查通过，公开 API 往返曾在独立 Python 3.11 环境验证；开发环境为 Python 3.13。当前直接使用仓库源码，示例 `.mtlx` 位于 `examples/`，宿主字典位于 `dictionaries/`；不再生成或验证发行包。

完整架构取舍见 [ADR-0005](adr/0005-MaterialX材质图核心与自定义节点扩展.md)。
