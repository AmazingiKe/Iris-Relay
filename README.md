# Iris Relay

Iris Relay 探索跨渲染器的材质转换：以字典建立材质语义对应，以小型神经网络联合校正材质在目标渲染器中的整体外观，通过最小侵入的 DCC 接入层读取和应用材质。

系统只修改材质，不通过改变灯光、相机、曝光或色彩管理来补偿差异。“整体校正”指材质内部多种因素的关联调整，不是一个统一系数，也不是整幅图像的后期处理。

当前已完成初步核心：统一 API、材质图协议、声明式字典、转换与诊断报告、联合校正扩展接口，以及通过同一 API 运行的参考插件。Python 运行时不依赖 DCC 或第三方库。

**尚未提供真实 DCC 插件、RS／Arnold／Cycles 字典、训练模型或渲染效果验证。** 示例中的 `reference.gloss` 和 `reference.rough` 是协议测试配置，不是真实渲染器；示例贴图 URI 是内存资源标识，不附带贴图文件。

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

API 提供能力查询、材质校验、材质转换。版本不匹配、未知节点、无法表达的连接和非法数值均有明确错误；缺少模型时可返回基础转换结果及警告。插件辅助接口默认不应用带警告的方案，也不会自动替换场景中的材质绑定。

## 目录

```text
src/iris_relay/         核心、公开 API、插件辅助接口与命令行
examples/              可执行参考插件、请求和示例字典
tests/                 图转换、接口、失败回退和跨进程测试
docs/                  API 文档和架构决策
```

按实际内容创建目录，未来有具体适配器或训练实现时再扩展，当前不预建空目录。

## 开发检查

```powershell
uv run --extra dev pytest -q
uv run --extra dev coverage run -m pytest -q
uv run --extra dev coverage report
uv run --extra dev ruff check src tests examples
uv run --extra dev ruff format --check src tests examples
uv build
```

测试包含单元、API 集成及 JSON 子进程流程；覆盖率门槛为 80%。`uv.lock` 固定开发依赖，构建生成的 wheel 和源码包位于 `dist/`，不提交构建产物。

## 文档与后续工作

- [文档索引](docs/README.md)
- [ADR-0001：材质转换与神经校正架构](docs/adr/0001-材质转换与神经校正架构.md)
- [ADR-0002：统一插件接口与初步核心实现](docs/adr/0002-统一插件接口与初步核心实现.md)
- [统一插件 API v1](docs/api.md)
- [协作与开发约定](AGENTS.md)

下一步选定一个 DCC 和一个有方向的渲染器组合，依据对应版本的真实 API 编写字典与适配器，建立渲染对照基线，再训练和验证联合校正。当前核心支持字典声明的图连接，但非恒等变换遇到贴图连接时仍会阻断，尚不自动插入变换节点。
