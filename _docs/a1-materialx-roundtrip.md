# A1 材质批量往返验证

- 日期：2026-11-10
- 状态：进行中；以下为已完成盘点、基线和分项修复，不代表全部材质已可往返。
- 范围：文件中的全部材质，含未绑定对象的材质；验收节点、参数、连接及图片语义。
- 不包含：渲染外观验收、神经校正训练或推理。
- 架构依据：[ADR-0005](adr/0005-MaterialX材质图核心与自定义节点扩展.md)。

## 输入与验证条件

源文件为用户提供的 `A1.blend`，大小 1,416,105,317 字节。
初始 SHA-256 为 `31c9c14be388209cf046ff3200dbaf9aca890ae5e190dac27bed5696934376ce`。
基线代码为 `cd7046d`，宿主为 Blender 5.2.3 LTS，核心为 MaterialX 1.39.5。

源文件只读，禁用自动执行脚本。测试输出写入用户指定的外部测试目录，源场景及贴图不提交仓库。
批处理按材质分别记录结果，一个材质失败不会终止后续材质。
回读进程只使用导出的 `.mtlx` 和引用的资源，在空场景中新建材质。

## 全量盘点与初始基线

盘点得到 1,803 个材质、486 个递归引用的节点组、5,121 个图片数据块。
顶层材质共有 5,255 个图片节点、1,851 个 Principled 节点；节点组内部另有数学、混合、坐标、曲线和程序化节点。

初始导出成功 34 个，阻断 1,769 个。下表按每个材质最先出现的阻断分类；解决第一项之后可能继续暴露其他问题。

| 初始结果／首个阻断码 | 材质数 |
| --- | ---: |
| 导出成功（含已报告的近似警告） | 34 |
| `UNSUPPORTED_NODE_CONFIGURATION` | 1,157 |
| `DISPLACEMENT_UNSUPPORTED` | 301 |
| `UV_UNSUPPORTED` | 198 |
| `UNSUPPORTED_NODE` | 71 |
| `MATERIALX_COLORSPACE_UNSUPPORTED` | 23 |
| `LINK_TRANSFORM_UNSUPPORTED` | 9 |
| `INVALID_REQUEST` | 7 |
| `INVALID_LINK` | 1 |
| `OUTPUT_UNSUPPORTED` | 1 |
| `MUTED_NODE` | 1 |

证据文件为同一次运行目录中的 `inventory.json`、`source-before.json`、`baseline-export/summary.json` 和逐材质 JSON；未将大型源图快照放入仓库。

基线成功导出的 34 个文档已在独立空场景中全部创建材质，保存重开后核对 1,294 个目标输入、138 条含材质出口的连接，目标图核对全部通过。证据为 `baseline-import/summary.json`、逐材质读回 JSON 和 `restored.blend`。

## 已定位的问题与处理

### Principled 历史模式没有进入字典

样本包含 `MULTI_GGX + RANDOM_WALK_LEGACY`、`GGX + RANDOM_WALK_SKIN`、`MULTI_GGX + BURLEY`、`MULTI_GGX + RANDOM_WALK_SKIN`、`GGX + RANDOM_WALK_LEGACY` 五种此前未收录的组合。
这些名称同时存在于本版本实机节点目录中；所有已盘点的 Principled 节点均未启用 SSS。

在字典生成器中增加上述来源配置，Cycles 字典内容版本升为 `0.3.3`。
保留唯一目标默认实现；新增配置沿用活动 SSS 的阻断与未启用辅助参数的报告机制。
单元／API 回归先复现 10 个失败，再验证 12 个模式与 SSS 边界组合全部通过；完整已安装字典测试共 34 项通过。
此项只扩展来源识别，不宣称不同 BSDF 分布的外观相同，也不表示复杂材质的后续节点均已支持。
变更后的全部 240 项自动测试通过，核心和控制层合计覆盖率 92%；Ruff 静态及格式检查通过。

全量重新导出得到 447 份文档，另 1,356 个材质仍阻断，未跳过位移或其他参与输出的分支。
447 份文档在独立 Blender 中全部创建、保存重开及读回通过，核对 17,029 个输入、1,810 条含材质出口的连接。
证据为 `variants-export/summary.json`、`variants-import/summary.json` 及逐材质报告。

### C4D 缺少显式亮度加权节点绑定

447 份文档最初在 Arnold、Redshift 各有 232 份可转换，其余被 `color.weighted_sum` 缺少绑定或反射权重范围阻断。
实机重新探测 Redshift `rsmathdotvector` 和 Arnold `dot`；Arnold 没有独立参数头文件，改由动态描述取得 Input1、Input2 的整数 code、向量类型和默认值。
两端都使用未归一化点积，三个系数是原始向量数据，不套用颜色 UI 的 sRGB 编码。
点积含义依据 [Arnold 数学节点文档](https://help.autodesk.com/cloudhelp/ENU/AR-Core/files/ac-shading/arnold_user_guide_ac_shading_ac_math_shaders_html.html)，具体端口依据本版本运行时记录。

新增测试先复现 4 项失败，随后验证常规及负权重、贴图连接和再次导出后返回 Blender 的系数保留。
全部 244 项自动测试通过，核心和控制层合计覆盖率 92%；静态及格式检查通过。

| 对 447 份文档的验证 | Blender Cycles | C4DtoA | C4D Redshift |
| --- | ---: | ---: | ---: |
| 核心目标转换通过（含近似警告） | 447 | 374 | 374 |
| 独立宿主创建、保存重开及读回通过 | 447 | 374 | 374 |
| 仍被目标范围限制阻断 | 0 | 73 | 73 |

C4D 合计 748 个目标材质通过，核对 91,856 个输入、2,934 条连接；证据为 `weighted-targets/summary.json`、`host.json`、`host.c4d`，运行 ID 为 `e7af9cb6-bcdb-41ea-acb7-dfe0d3bebba5`。
剩余 73 份的 `reflection.weight` 超过目标字典支持范围；没有为提高通过数而截断或放宽。
这些结果验证的是统一 API 加宿主写入器，不代表 C4D 自带 MaterialX 导入器，也不代表渲染外观一致。

### 固定桥接表无法继续承载标准数学节点

原桥接只认识少量旧功能契约；继续为每种数学运算手工定义一套同义节点，会偏离 ADR-0005 复用官方 NodeDef 的方向。
增加 `materialx.ND_*` 直接绑定：原生字典只提供实际节点配置与端口对应，MaterialX 操作按官方 SDK 校验定义、端口和类型。
Blender 与 Redshift 首先接入标量加减乘，未钳制的 Blender 数学模式有实机枚举依据。钳制、除法及其他未声明模式仍阻断，Arnold 尚无这三个标量绑定。

11 项新增回归先复现失败，再验证标准节点往返、负数、输入顺序、共享输出、SDK 契约错误及缺失目标绑定。
完整 255 项自动测试通过，核心和控制层总覆盖率 92%；静态及格式检查通过。
三组独立 Blender 原生夹具先导出，再从文件还原、保存重开，核对 108 个输入、12 条连接；Redshift 另核对 255 个输入、12 条连接。
证据为 `math-fixtures/`、`math-import/`、`math-targets/`，C4D 运行 ID 为 `33d2e495-75da-40d1-a220-c834314ada23`。
A1 的 1,803 个材质已完成本轮复测，仍有 447 个成功；相关数学链还受其他节点或配置阻断，不能把新增能力数量当成新增材质覆盖数。
447 份导出文档与上一轮逐字节一致，保留先前独立 Blender 和 C4D 的读回证据，没有为相同文件重复生成整套宿主产物。
本轮证据为 `math-export/summary.json` 和 `math-document-comparison.json`。

### 缺失外链库和图片路径

源文件加载报告缺失 8 个外链库和 9 个链接数据块，涉及 botaniq 植物资产及 `TK_UtilityNode.blend`。
三个 `Decode_NR_Group`／`Decode_MA_Group` 数据块只有缺失占位，没有节点内容。已向用户询问完整外链库的位置，不能按组名猜测其内部计算。

普通文件图片中有 85 个数据块的原路径不存在。限定搜索源文件旁两处已有贴图目录后，50 个找到唯一同名候选，35 个没有候选。
候选清单写入 `missing-resource-candidates.json`；同名只构成定位线索，当前未据此修改源引用。
此外有 102 个 UDIM 图片数据块，其 `<UDIM>` 模板本身不是普通文件，不能简单归入缺图；需要逐 tile 验证。

### 大场景盘点的额外开销

首次全场景只读盘点访问 `image.size` 时触发大量贴图解码，该属性并非节点与资源结构验证所需。
盘点脚本已移除该访问。后续批处理只附加材质及依赖，并可另存材质库缓存，以减少重复加载和求值整场景几何体的成本。
缓存写入使用 Blender 的 `libraries.write`，依赖随材质写入、路径转为绝对路径；API 依据见 [Blender 文档](https://docs.blender.org/api/5.1/bpy.types.BlendDataLibraries.html)。
缓存与源文件的 1,803 个材质名称、顶层节点数量一致；除 4 个材质中缺失 TK 节点组的外链名称后缀变化外，节点记录、连接与底色一致。
这些缺失组仍为空占位，缓存不能恢复原本不存在的内容。原文件和附加进程的工作空间均为 Linear Rec.709；详细差异见 `cache-comparison.json`。

## 复现入口

在仓库根目录安装第三方依赖后，将下列占位路径替换为本机路径；输出目录必须是新的。

```powershell
& "Blender程序路径" --background --factory-startup --disable-autoexec "源A1.blend路径" --python-exit-code 1 --python _scripts/inspect_blender_materials.py -- --out "盘点JSON路径"
& "Blender程序路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_batch.py -- --source "源A1.blend路径" --out "新导出目录" --material-cache "新材质缓存.blend路径"
& "Blender程序路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_batch.py -- --documents "导出目录" --out "新回读目录"
uv run python _scripts/verify_materialx_targets.py --documents "导出目录" --out "新目标验证目录" --c4dpy "c4dpy程序路径"
```

回读报告中的 `target_graph_verified` 检查保存重开后的节点、全部目标输入、连接和图片解释是否符合核心返回的目标图。
源图到 MaterialX 的语义保留、跨 DCC 的实际创建仍须另行核对，不能仅凭目标图核对通过推断全部往返正确。

## 尚需完成

继续处理实际样本中的节点组、数学／颜色链、UV 与非恒等 Mapping、位移、图片 Alpha、UDIM 和色彩空间。
需要分别汇总标准文档校验、Blender 实际还原、已有其他目标的核心转换及宿主创建结果，并复核资源和源文件保护。
本报告会随全量复测更新，当前不存在全部材质完成或跨 DCC 全部兼容的结论。
