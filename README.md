# Iris Relay

Iris Relay 探索跨渲染器的材质转换：以字典建立材质语义对应，以小型神经网络联合校正材质在目标渲染器中的整体外观，通过最小侵入的 DCC 接入层读取和应用材质。

系统只修改材质，不通过改变灯光、相机、曝光或色彩管理来补偿差异。“整体校正”指材质内部多种因素的关联调整，不是一个统一系数，也不是整幅图像的后期处理。

当前已完成初步核心：统一 API、材质图协议、声明式字典、转换与诊断报告、联合校正扩展接口，以及通过同一 API 运行的参考插件。Python 运行时不依赖 DCC 或第三方库。

现已提供 Blender Cycles、C4D Redshift 和 C4DtoA 的真实节点目录与基础字典，包含节点名称、资产 ID、整数 code 和端口绑定。本轮 6 组源材质完成保存重开和原生识别，随后 18 组转换完成目标创建、保存重开及参数／连接核对。详见[原生字典与验证范围](docs/native-dictionaries.md)。

每端目前支持标准表面、图片、切线法线、高度凹凸与显式 R 通道提取五类功能。统一语义层集中定义功能和端口，通过原生 ID 与配置识别具体实现；多个来源实现可归入同一功能，多个目标实现必须指定唯一默认选择。Blender 提供实验性脚本适配器，C4D 提供独立验证脚本。尚无完整生产插件、训练模型或跨渲染器视觉一致性证明。示例中的 `reference.gloss` 和 `reference.rough` 仍仅用于协议测试。

## 快速开始

需要 Python 3.11+ 和 uv。在仓库根目录执行：

```powershell
uv sync --extra dev
uv run iris-relay --dictionaries examples/dictionaries --request examples/convert-request.json
uv run python examples/plugin_demo.py --transport python
uv run python examples/plugin_demo.py --transport process
```

第一条转换命令输出完整 JSON 报告；两种参考插件调用都会新建内存中的目标材质，并验证源材质保持不变。示例将光泽度 `0.8` 转成粗糙度约 `0.2`，保留底色贴图连接、色彩空间和 UV 描述；这是参考规则验证，不是神经校正或视觉等价性证明。

没有 uv 时可使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m iris_relay --dictionaries examples/dictionaries --request examples/convert-request.json
```

## 统一接口

所有插件依赖 `RelayService.handle(request) -> response`。进程内使用 `RelayAPI`；跨进程使用同一 JSON 协议，Python 侧可使用 `JsonCommandService`。插件只读写宿主材质，转换和校正规则集中在核心。

API 提供能力查询、通用功能定义查询（`semantics`）、原生模式识别（`identify`）、原生绑定查询、材质校验和转换。真实配置同时匹配宿主、渲染器与节点系统的版本。版本不匹配、未知节点、无法表达的连接和非法数值均有明确错误；缺少模型时可返回基础转换结果及警告。插件辅助接口默认不应用带警告的方案，也不会自动替换场景中的材质绑定。

## 目录

```text
src/iris_relay/         核心、公开 API、插件辅助接口与命令行
dictionaries/          真实转换字典；catalogs/ 保存原生节点探测目录
plugins/               宿主脚本适配器，目前提供 Blender 实验模块
scripts/               节点探测、字典生成和独立宿主验证
examples/              可执行参考插件、请求和示例字典
tests/                 图转换、接口、失败回退和跨进程测试
docs/                  API 文档和架构决策
```

按实际内容创建目录，不预建空目录。字典的具体 code 与支持边界见[原生字典文档](docs/native-dictionaries.md)。

## 开发检查

```powershell
uv run --extra dev pytest -q
uv run --extra dev coverage run -m pytest -q
uv run --extra dev coverage report
uv run --extra dev ruff check src tests examples scripts plugins
uv run --extra dev ruff format --check src tests examples scripts plugins
uv build
```

测试包含单元、API 集成及 JSON 子进程流程；覆盖率门槛为 80%。`uv.lock` 固定开发依赖，构建生成的 wheel 和源码包位于 `dist/`，不提交构建产物。

## 文档与后续工作

- [文档索引](docs/README.md)
- [ADR-0001：材质转换与神经校正架构](docs/adr/0001-材质转换与神经校正架构.md)
- [ADR-0002：统一插件接口与初步核心实现](docs/adr/0002-统一插件接口与初步核心实现.md)
- [统一插件 API v1](docs/api.md)
- [原生字典与实机验证](docs/native-dictionaries.md)
- [ADR-0003：原生节点绑定与宿主验证](docs/adr/0003-原生节点绑定与宿主验证.md)
- [ADR-0004：统一节点功能定义与原生实现选择](docs/adr/0004-统一节点功能定义与原生实现选择.md)
- [协作与开发约定](AGENTS.md)

下一步扩展真实材质导出与节点覆盖，建立固定场景的渲染对照基线，再训练和验证联合校正。当前核心支持字典声明的图连接，但非恒等变换遇到贴图连接时仍会阻断，尚不自动插入变换节点。
