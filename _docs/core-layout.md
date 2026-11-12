# 核心模块职责

`iris_relay/` 根目录保留公开入口、共用材质协议和业务模块。MaterialX 实现与格式无关的工具分别归入 `materialx/`、`tools/`，HTTP 服务端继续位于 `server/`。宿主 API 调用留在仓库根目录的 `plugins/`。

## 模块地图

| 位置 | 职责 |
| --- | --- |
| `__init__.py`、`api.py` | 公开 `RelayAPI` / `RelayService`，统一请求分发；按需加载 MaterialX |
| `__main__.py`、`cli.py` | `python -m iris_relay` 的命令行与 JSON 会话入口 |
| `protocol.py`、`model.py` | 请求和错误协议、材质图结构校验 |
| `dictionary.py`、`semantics.py`、`conversion.py` | 字典加载、语义契约、原生与语义图转换 |
| `correction.py`、`neural.py` | 联合校正协议、原子应用和受限模型推理 |
| `native.py`、`plugin.py` | 公开宿主辅助接口与参考插件流程 |
| `transport.py`、`http_client.py` | JSON 子进程与 HTTP 客户端 |
| `server/` | HTTP 服务端、配置与生命周期 |
| `materialx/core.py` | SDK 标准库、文档读写、校验和展开 |
| `materialx/bridge.py` | 原生材质与 MaterialX 的受限互转 |
| `materialx/extensions.py`、`recipes.py`、`lowerings.py` | 内联定义、动态计算子图、目标组合转换 |
| `materialx/images.py`、`coordinates.py` | 图片与 Alpha、UV 和坐标变换 |
| `materialx/editor.py` | 编辑元数据、布局和原名备份的 MaterialX 存取 |
| `materialx/preserve.py` | 无法转换的原生图结构保留 |
| `materialx/assets.py`、`diff.py` | 文档资源引用、计算字段差异 |
| `tools/naming.py` | 拼音、名称清理、备用名称、长度和冲突处理 |

## 依赖边界

- 插件调用公开服务，核心调用具体业务模块；插件不直接导入 MaterialX 内部实现。
- `materialx/` 可以使用根目录的材质模型、字典和 `tools/`；`tools/` 不依赖 MaterialX 或宿主运行时。
- 两个子包的 `__init__.py` 只说明职责，不批量导入模块。导入包本身不加载 MaterialX SDK、拼音库或核心 API。
- `iris_relay.protocol`、`model`、`native`、`transport`、`http_client` 的路径继续保持，旧宿主可单独加载轻量接口。
- 开发脚本放在仓库根目录的 `_scripts/`；`tools/` 只容纳核心运行时确有共用需求的工具。

## 本次路径迁移

本节记录已完成的目录迁移。后续职责与维护遵循 [ADR-0013](adr/0013-按能力族推进转换与隔离宿主差异.md)：原生 API 差异留在宿主，功能与目标实现按 ADR-0012 分离，公共图流程不承接逐资产补丁。`recipes.py` 的配置编译与端口映射已合并登记，报告分析及依赖检查位于 `_scripts/`，见[维护流程](capability-maintenance.md)。文件拆分须由实际职责和重复处理驱动，不以模块数量作为解耦指标。

原 `iris_relay.materialx_<名称>` 移至 `iris_relay.materialx.<名称>`；原 `iris_relay.naming` 移至 `iris_relay.tools.naming`。仓库内测试、开发脚本与文档引用同步更新。上述内部实现路径不额外保留转发文件；直接依赖旧内部模块的外部开发脚本需要相应修改导入。

公开 `from iris_relay import RelayAPI, RelayService`、宿主辅助接口、模型推理入口、命令行及请求协议保持不变。此次仅调整目录和导入路径，不修改名称处理、材质转换或模型校正规则。

## 验证记录

2026-11-11：798 项测试通过，核心覆盖率 91%，Ruff 检查和格式检查通过。12 个迁移模块及两个调用入口通过排除导入语句后的语法树比对，确认业务代码保持一致。Maya 2022 自带 Python 3.7.7 成功加载既有轻量接口，通过独立核心完成能力查询、MaterialX 校验和转换，宿主进程未加载 MaterialX SDK 或拼音库。本次未重新执行 DCC 场景创建或渲染验证。
