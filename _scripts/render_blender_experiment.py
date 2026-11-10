"""在独立 Cycles 场景渲染真实源材质、底色和粗糙度空间特征；原缓存只读。"""

import argparse
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _scripts.neural_experiment import fingerprint, save_json, write_uv_png  # noqa: E402
from plugins.blender.adapter import BlenderAdapter  # noqa: E402
from plugins.core import create_service, request  # noqa: E402


def scene_for(job):
    """用同一网格、正交相机和均匀环境辐亮度建立可比较条件。"""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    spec = job["conditions"]
    scene.cycles.device = "CPU"
    scene.cycles.samples = spec["cycles"]["samples"]
    scene.cycles.seed = spec["seed"]
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = spec["cycles"]["denoising"]
    scene.cycles.max_bounces = spec["cycles"]["bounces"]
    scene.render.threads_mode = "FIXED"
    scene.render.threads = spec["cycles"]["threads"]
    scene.render.resolution_x, scene.render.resolution_y = spec["resolution"]
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = spec["pixel_aspect"]
    scene.render.pixel_aspect_y = 1
    scene.render.dither_intensity = spec["cycles"]["dither"]
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "32"
    scene.render.image_settings.exr_codec = "ZIP"
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("FixedUniformEnvironment")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = spec["environment_radiance"] + [1]
    background.inputs["Strength"].default_value = 1
    scene.world = world
    mesh = bpy.data.meshes.new("IdenticalProbeMesh")
    mesh.from_pydata(job["mesh"]["vertices"], [], job["mesh"]["faces"])
    mesh.update()
    for face in mesh.polygons:
        face.use_smooth = True
    mesh.normals_split_custom_set_from_vertices(job["mesh"]["vertices"])
    uv = mesh.uv_layers.new(name="map1")
    for polygon, corners in zip(mesh.polygons, job["mesh"]["uvs"], strict=True):
        for index, value in zip(polygon.loop_indices, corners, strict=True):
            uv.data[index].uv = value
    for name, coordinates in job["mesh"].get("additional_uv_sets", {}).items():
        extra = mesh.uv_layers.new(name=name)
        for polygon, corners in zip(mesh.polygons, coordinates, strict=True):
            for index, value in zip(polygon.loop_indices, corners, strict=True):
                extra.data[index].uv = value
        extra.active_render = False
    uv.active_render = True
    sphere = bpy.data.objects.new("IdenticalProbe", mesh)
    scene.collection.objects.link(sphere)
    camera_data = bpy.data.cameras.new("FixedCamera")
    camera = bpy.data.objects.new("FixedCamera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = spec["camera"]["position"]
    camera.rotation_euler = spec["camera"]["rotation"]
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = spec["camera"]["width"]
    camera_data.clip_start = spec["camera"]["near"]
    camera_data.clip_end = spec["camera"]["far"]
    scene.camera = camera
    return scene, sphere


def render(scene, sphere, material, path):
    """只替换测试球体的材质；图像写出失败必须终止，不能产生成功样本。"""
    sphere.data.materials.clear()
    sphere.data.materials.append(material)
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    if not path.is_file():
        raise RuntimeError("Cycles 未写出图像：" + str(path))


def actual_conditions(scene):
    """只读记录实际比较条件，材质替换和文件名不影响此记录。"""
    camera = scene.camera
    background = scene.world.node_tree.nodes.get("Background")
    return {
        "engine": scene.render.engine,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "samples": scene.cycles.samples,
        "seed": scene.cycles.seed,
        "denoising": scene.cycles.use_denoising,
        "bounces": scene.cycles.max_bounces,
        "camera_matrix": [list(row) for row in camera.matrix_world],
        "ortho_width": camera.data.ortho_scale,
        "world_radiance": list(background.inputs["Color"].default_value),
        "world_strength": background.inputs["Strength"].default_value,
        "pixel_aspect": [scene.render.pixel_aspect_x, scene.render.pixel_aspect_y],
        "view_transform": scene.view_settings.view_transform,
    }


def reference_material(emission=False):
    """独立参考仅用于证明采样与照明条件可比，不作为训练样本或标签。"""
    material = bpy.data.materials.new("ReferenceEmission" if emission else "ReferenceDiffuse")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    node = tree.nodes.new("ShaderNodeEmission" if emission else "ShaderNodeBsdfDiffuse")
    node.inputs["Color"].default_value = [0.2, 0.4, 0.7, 1] if emission else [0.4, 0.4, 0.4, 1]
    tree.links.new(node.outputs[0], output.inputs["Surface"])
    return material


def main():
    """先核对冻结协议，再渲染参考或源材质；源材质通过公开 API 另存目标输入文档。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    args.out.mkdir(parents=True, exist_ok=False)
    job = json.loads(args.job.read_text("utf8"))
    assert fingerprint(job["conditions"]) == job["conditions_sha256"]
    assert fingerprint(job["mesh"]) == job["mesh_sha256"]
    scene, sphere = scene_for(job)
    bpy.context.view_layer.update()
    frozen = actual_conditions(scene)
    if args.probe_only:
        for name, emission in (("diffuse", False), ("emission", True)):
            render(scene, sphere, reference_material(emission), args.out / (name + ".exr"))
        texture_path = args.out / "uv.png"
        write_uv_png(texture_path)
        material = reference_material(True)
        image = bpy.data.images.load(str(texture_path))
        image.colorspace_settings.name = "Non-Color"
        texture = material.node_tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        emission = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeEmission")
        material.node_tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
        render(scene, sphere, material, args.out / "uv.exr")
        coordinate = bpy.data.materials.new("NamedUVMatrixReference")
        coordinate.use_nodes = True
        tree = coordinate.node_tree
        surface = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
        surface.inputs["Base Color"].default_value = [0, 0, 0, 1]
        surface.inputs["Specular IOR Level"].default_value = 0
        surface.inputs["Emission Strength"].default_value = 1
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        uv_node = tree.nodes.new("ShaderNodeUVMap")
        uv_node.uv_map = "DetailUV"
        previous = uv_node.outputs["UV"]
        for location, rotation, scale in (
            ([0.12, -0.07, 0], 0.2, [1.3, 0.7, 1]),
            ([-0.04, 0.11, 0], -0.15, [0.8, 1.2, 1]),
        ):
            mapping = tree.nodes.new("ShaderNodeMapping")
            mapping.vector_type = "POINT"
            mapping.inputs["Location"].default_value = location
            mapping.inputs["Rotation"].default_value = [0, 0, rotation]
            mapping.inputs["Scale"].default_value = scale
            tree.links.new(previous, mapping.inputs["Vector"])
            previous = mapping.outputs["Vector"]
        tree.links.new(previous, texture.inputs["Vector"])
        tree.links.new(texture.outputs["Color"], surface.inputs["Emission Color"])
        with create_service(ROOT) as service:
            native = BlenderAdapter(coordinate, service).read_material()
            converted = request(service, "materialx.export", material=native)
            assert converted["result"]["document"], converted
            (args.out / "coordinates.mtlx").write_text(
                converted["result"]["document"], encoding="utf8"
            )
        render(scene, sphere, coordinate, args.out / "coordinates.exr")
        bpy.ops.wm.save_as_mainfile(filepath=str(args.out / "reference.blend"))
        save_json(
            args.out / "summary.json",
            {
                "conditions_sha256": job["conditions_sha256"],
                "mesh_sha256": job["mesh_sha256"],
                "rendered": 4,
            },
        )
        return
    names = {c["source_name"] for c in job["cases"]}
    with bpy.data.libraries.load(job["source_cache"], link=False) as (available, loaded):
        assert names <= set(available.materials)
        loaded.materials = sorted(names)
    materials = {m.name_full: m for m in loaded.materials}
    documents = args.out / "documents"
    documents.mkdir()
    completed = []
    with create_service(ROOT) as service:
        for index, case in enumerate(job["cases"]):
            directory = args.out / case["id"]
            directory.mkdir()
            material = materials[case["source_name"]].copy()
            material.name = case["id"] + "_SourceVariant"
            tree = material.node_tree
            root = tree.nodes.get(case["source_root"])
            if root is None or root.bl_idname != "ShaderNodeBsdfPrincipled":
                raise RuntimeError("源表面不是清单中的实际节点：" + case["id"])
            assert "cycles.principled." + root.distribution == job["source_shading_model"]
            assert material.node_tree != materials[case["source_name"]].node_tree
            for name, value in case["source_parameters"].items():
                assert not root.inputs[name].is_linked
                root.inputs[name].default_value = value
            native = BlenderAdapter(material, service).read_material()
            response = request(service, "materialx.export", material=native)
            document = (response.get("result") or {}).get("document")
            if not document:
                raise RuntimeError(response)
            (documents / (case["id"] + ".mtlx")).write_text(document, encoding="utf8")
            render(scene, sphere, material, directory / "beauty.exr")
            if case["split"] != "train":
                scene.cycles.seed = job["conditions"]["seed"] + 1
                render(scene, sphere, material, directory / "beauty-repeat.exr")
                scene.cycles.seed = job["conditions"]["seed"]
            output = next(
                n
                for n in tree.nodes
                if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output
            )
            emission = tree.nodes.new("ShaderNodeEmission")
            tree.links.new(emission.outputs[0], output.inputs["Surface"])
            color = root.inputs["Base Color"]
            if color.is_linked:
                tree.links.new(color.links[0].from_socket, emission.inputs["Color"])
            else:
                emission.inputs["Color"].default_value = color.default_value
            render(scene, sphere, material, directory / "albedo.exr")
            for edge in list(emission.inputs["Color"].links):
                tree.links.remove(edge)
            value = root.inputs["Roughness"].default_value
            emission.inputs["Color"].default_value = [value, value, value, 1]
            render(scene, sphere, material, directory / "roughness.exr")
            tree.nodes.remove(emission)
            tree.links.new(root.outputs["BSDF"], output.inputs["Surface"])
            assert actual_conditions(scene) == frozen
            completed.append({"id": case["id"], "rendered": ["beauty", "albedo", "roughness"]})
            save_json(
                args.out / "summary.json",
                {
                    "conditions_sha256": job["conditions_sha256"],
                    "mesh_sha256": job["mesh_sha256"],
                    "completed": completed,
                    "total": len(job["cases"]),
                    "blender_version": bpy.app.version_string,
                    "training_started": False,
                    "actual_scene_conditions": frozen,
                    "scene_conditions_unchanged": True,
                    "noise_repeat_seed": job["conditions"]["seed"] + 1,
                },
            )
            print(json.dumps({"completed": index + 1, "total": len(job["cases"])}), flush=True)
            if index == 0:
                bpy.ops.wm.save_as_mainfile(filepath=str(args.out / "representative.blend"))
            bpy.data.materials.remove(material)


if __name__ == "__main__":
    main()
