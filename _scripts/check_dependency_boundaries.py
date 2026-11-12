"""静态检查核心与宿主依赖方向；不导入 DCC、不执行材质代码。"""

import argparse
import ast
import json
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_MODULES = {
    "bpy": "blender",
    "mathutils": "blender",
    "maya": "maya",
    "arnold": "maya",
    "c4d": "cinema4d",
    "maxon": "cinema4d",
}
PUBLIC_MODULES = {"api", "model", "protocol", "native", "transport", "http_client", "plugin"}
DEVELOPMENT_MODULES = {"_scripts", "_tests", "_examples"}


def _module_references(path, tree):
    """解析绝对、相对及字面量动态导入；变量构造的模块名不属于静态证明范围。"""
    package = ".".join(path.parent.parts)
    loaders = {"__import__", "importlib.import_module"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                yield node.lineno, item.name
                if item.name == "importlib":
                    loaders.add((item.asname or item.name) + ".import_module")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "importlib":
                loaders.update(
                    item.asname or item.name for item in node.names if item.name == "import_module"
                )
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            yield node.lineno, base
            for item in node.names:
                if item.name != "*":
                    yield node.lineno, base + "." + item.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            if ast.unparse(node.func) in loaders and isinstance(node.args[0], ast.Constant):
                module = node.args[0].value
                if isinstance(module, str):
                    if module.startswith("."):
                        context = (
                            node.args[1]
                            if len(node.args) > 1
                            else next(
                                (kw.value for kw in node.keywords if kw.arg == "package"), None
                            )
                        )
                        if not isinstance(context, ast.Constant) or not isinstance(
                            context.value, str
                        ):
                            continue
                        module = resolve_name(module, context.value)
                    yield node.lineno, module


def _rule(path, module):
    parts = module.split(".")
    if path.parts[0] == "iris_relay":
        if parts[0] in HOST_MODULES or parts[0] == "plugins":
            return "core_host_dependency"
        if parts[0] in DEVELOPMENT_MODULES:
            return "runtime_development_dependency"
    if path.parts[0] == "plugins":
        host = path.parts[1] if len(path.parts) > 2 else None
        if parts[0] == "iris_relay" and len(parts) > 1 and parts[1] not in PUBLIC_MODULES:
            if parts[1] not in {"RelayAPI", "RelayService"}:
                return "plugin_internal_core_dependency"
        if parts[0] == "plugins" and len(parts) > 1 and parts[1] in set(HOST_MODULES.values()):
            if parts[1] != host:
                return "cross_host_dependency"
        if parts[0] in HOST_MODULES and HOST_MODULES[parts[0]] != host:
            return "cross_host_runtime"
        if parts[0] in DEVELOPMENT_MODULES:
            return "runtime_development_dependency"
    return None


def check_source(path, source):
    """检查一份相对仓库路径的源码，返回路径、行号、模块与违反的依赖规则。"""
    path = Path(path)
    try:
        tree = ast.parse(source)
        references = list(_module_references(path, tree))
    except (SyntaxError, ImportError, ValueError) as exc:
        return [
            {
                "path": path.as_posix(),
                "line": getattr(exc, "lineno", 1),
                "module": "",
                "rule": "syntax",
                "message": str(exc),
            }
        ]
    result, seen = [], set()
    for line, module in references:
        rule = _rule(path, module)
        if rule and (line, rule) not in seen:
            seen.add((line, rule))
            result.append({"path": path.as_posix(), "line": line, "module": module, "rule": rule})
    return result


def check_repository(root):
    """遍历实际运行时代码；语法错误计为失败，不跳过无法检查的文件。"""
    root = Path(root)
    violations = []
    checked = 0
    for folder in ("iris_relay", "plugins"):
        for path in sorted((root / folder).rglob("*.py")):
            checked += 1
            violations.extend(check_source(path.relative_to(root), path.read_text("utf-8-sig")))
    if not checked:
        violations.append(
            {"path": str(root), "line": 1, "module": "", "rule": "missing_runtime_sources"}
        )
    return violations


def main():
    """输出可定位的依赖检查结果；有违规时返回非零退出码。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    violations = check_repository(args.root)
    print(
        json.dumps(
            {"status": "error" if violations else "ok", "violations": violations},
            ensure_ascii=False,
        )
    )
    return bool(violations)


if __name__ == "__main__":
    raise SystemExit(main())
