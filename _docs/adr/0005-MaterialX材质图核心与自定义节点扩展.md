# ADR-0005：MaterialX 材质图核心与自定义节点扩展

- 日期：2026-11-06
- 状态：已采纳（文档核心及受限原生桥接）
- 关联：[ADR-0002](0002-统一插件接口与初步核心实现.md)、[ADR-0003](0003-原生节点绑定与宿主验证.md)、[ADR-0004](0004-统一节点功能定义与原生实现选择.md)
- 本决定调整 ADR-0002 的仅标准库依赖，以及 ADR-0004 的自定义通用语义作为唯一中间表示的方向；保留原决定的验证记录与理由。

## 背景

用户确认首条后续宿主链路为 Maya Arnold → Blender Cycles，并提出复用 MaterialX 的标准节点、文档格式和自定义节点机制，避免自行维护通用节点大全。MaterialX 标准库不能覆盖所有原生节点，因此文档保存能力与目标转换能力必须分别声明。

## 决定

1. 新的材质图核心采用官方 MaterialX Python SDK，固定 `MaterialX==1.39.5`，接受文档版本 `1.39`。节点类型、端口、默认值、继承及版本解析读取官方库，不复制标准定义。升级 SDK／库版本须重新验证。
2. MaterialX 标准节点与自定义 `NodeDef`、`NodeGraph` 共存。自定义定义内联在文档中，不得覆盖标准库。官方库通过 SDK 的数据库引用提供，不嵌入每个输出文件；每次请求的自定义文档相互隔离。
3. 延续 `RelayService.handle(request) -> response` 和 API v1，新增 `materialx.definitions`、`materialx.validate`、`materialx.normalize`、`materialx.export`、`materialx.convert`。内容在 Python 内存中传递；`.mtlx` 文件读写由调用方负责，不引入 HTTP。
4. 校验和规范化支持文档保存、类型和连接检查、有限数值、循环检测，以及自定义子图的受限展开。外部代码实现仅保存声明，不读取或执行。没有实现的自定义节点可以保存，但返回警告，不宣称可以渲染。
5. 核心只自动加载随 SDK 发布的标准库。输入文档禁止 DTD、实体声明及 XInclude，限制大小、深度和展开规模。纹理 URI 只作为数据保留，不自动读取像素、不验证文件存在。外部库依赖应由调用方准备为内联定义。
6. 既有 JSON 原生材质协议、字典和 `convert` 操作保留，作为兼容接入。`materialx.export` 经受限桥接导出标准图；`materialx.convert` 从 MaterialX 图检查已实现的能力，再生成目标 JSON 材质数据。桥接表只记录语义对应，类型和默认值来自 SDK。现阶段内部复用既有字典语义转换，不宣称所有旧链路已迁移到 MaterialX。
7. 自定义节点只有能展开到已支持标准节点时才可进行目标转换。专有节点可以在 MaterialX 文档中保留，但缺少目标规则时阻断转换，并保留完整源文档和报告。保存节点不等于支持其转换。
8. SDK 按需加载。正常安装核心包会安装依赖；只使用源码的旧宿主环境若没有兼容 SDK，仍可使用旧操作，MaterialX 操作报告 `MATERIALX_UNAVAILABLE`。宿主进程内调用需要为其 Python ABI 安装兼容依赖；已有本地子进程接口继续可用。

## 替代方案

- 继续扩大自定义节点词汇与存储格式：不作为新方向，重复维护类型、默认值及节点图规则。
- 所有节点强制转换成标准节点：不采用，无法表达的专有信息会丢失。
- 将 Arnold 导出的 `.mtlx` 一律视为标准图：不采用；其文件可以引用 Arnold 专有定义，需要逐项检查。
- 直接执行文档引用的外部 OSL／着色代码：不采用，本核心负责文档和转换，不是着色执行环境。
- 一次性删除旧协议和字典：不采用，现有宿主读写已有验证依据，先通过桥接接入再逐步迁移。

## 后果与验证

初版复用 807 个官方 NodeDef 的查询与校验；这不代表可以将 807 种节点转为 Cycles。原生桥接只覆盖五类既有功能对应的 `standard_surface`、`image(color3)`、`normalmap(float)`、`bump`、`extract(color3,index=0)`，以及法线颜色需要的显式 `color3 → vector3` 转换。

自动测试验证标准文档、自定义子图、专有定义保留、独立文档转换、原生字典矩阵、非法图和失败源信息保留。独立 Blender 验证两条 MaterialX 桥接链路的创建、保存重开和读回，再次导出 MaterialX。没有 Maya 实机读取、跨渲染器外观或神经校正的新增证据。

具体调用、限制与复现方法见 [MaterialX 核心](../materialx-core.md)。

## 未决问题

- Maya／MtoA 对应版本的原生字典与读取适配器。
- OpenPBR、更多标准数学节点、图片 Alpha、更多通道和复杂坐标的目标转换。
- 基于样本核验 Standard Surface 与 Cycles Principled 的外观差异。
- 用户自定义库的分发、依赖解析及稳定版本策略。
- MaterialX 图上下文驱动的联合校正；本轮没有训练模型或着色代码生成器。

## 依据

- [MaterialX 规范：节点、自定义定义与实现](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/documents/Specification/MaterialX.Specification.md)
- [MaterialX 1.39.5 官方发行包](https://pypi.org/project/MaterialX/1.39.5/)
- [Arnold MaterialX 导出范围](https://help.autodesk.com/cloudhelp/ENU/AR-Maya/files/am-Arnold_for_Maya_User_Guide/utils/arnold_for_maya_utils_am_Export_Selection_to_Material_X_html.html)
