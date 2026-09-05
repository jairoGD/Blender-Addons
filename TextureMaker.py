bl_info = {
    "name": "Texture Maker",
    "version": (1, 23, 1),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Texture Maker",
    "description": "Material layer stack with individual texture baking",
    "category": "Material",
}

from array import array
import heapq
import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


TM_BLEND_ITEMS = [
    ('MIX', "Mix", "Mix colors using layer alpha and opacity"),
    ('MULTIPLY', "Multiply", "Multiply with layers below"),
    ('BURN', "Color Burn", "Darken by increasing contrast"),
    ('DODGE', "Color Dodge", "Brighten by decreasing contrast"),
    ('OVERLAY', "Overlay", "Overlay contrast blend"),
    ('SOFT_LIGHT', "Soft Light", "Soft contrast lighting blend"),
    ('LINEAR_LIGHT', "Linear Light", "Strong linear lighting blend"),
    ('SCREEN', "Screen", "Screen blend"),
    ('ADD', "Add", "Additive blend"),
    ('SUBTRACT', "Subtract", "Subtract from layers below"),
    ('DIFFERENCE', "Difference", "Difference between layer colors"),
    ('EXCLUSION', "Exclusion", "Softer difference blend"),
    ('DIVIDE', "Divide", "Divide layers below by this layer"),
    ('DARKEN', "Darken", "Keep darker values"),
    ('LIGHTEN', "Lighten", "Keep lighter values"),
    ('HUE', "Hue", "Apply this layer's hue"),
    ('SATURATION', "Saturation", "Apply this layer's saturation"),
    ('COLOR', "Color", "Apply this layer's hue and saturation"),
    ('VALUE', "Value", "Apply this layer's value"),
    (
        'HEIGHT',
        "Height",
        "Paint color in RGB and use painted alpha as signed bump height",
    ),
    (
        'NORMAL_MAP',
        "Normal Map",
        "Use this layer as a tangent-space normal map (UV, Non-Color)",
    ),
]

TM_SURFACE_BLEND_MODES = {'HEIGHT', 'NORMAL_MAP'}

TM_OBJECT_BAKE_MODE_ITEMS = [
    (
        'SINGLE',
        "Single Texture",
        "Bake the current combined color result into one texture",
    ),
    (
        'MULTI',
        "Multi Channels",
        "Bake selected material channels into separate textures",
    ),
]

TM_OBJECT_BAKE_CHANNELS = (
    ('ALBEDO', "Albedo", "bake_albedo", "output_image"),
    ('NORMAL', "Normal Map", "bake_normal", "output_normal_image"),
    ('HEIGHT', "Height", "bake_height", "output_height_image"),
    ('AO', "Ambient Occlusion", "bake_ao", "output_ao_image"),
    ('ROUGHNESS', "Roughness", "bake_roughness", "output_roughness_image"),
    ('METALNESS', "Metalness", "bake_metalness", "output_metalness_image"),
    ('EMISSION', "Emission", "bake_emission", "output_emission_image"),
    ('ALPHA', "Alpha", "bake_alpha", "output_alpha_image"),
)

TM_BLEND_LABELS = {
    identifier: label
    for identifier, label, _description in TM_BLEND_ITEMS
}

TM_LAYER_TYPE_ITEMS = [
    ('ALBEDO', "Albedo", "Bake material base color"),
    ('AO', "Ambient Occlusion", "Bake ambient occlusion"),
    ('NORMAL', "Normal", "Bake tangent-space normals"),
    ('SHADOW', "Shadow / Light Mask", "Bake scene lighting and shadows"),
    ('HARD_SHADOW', "Hard Shadow", "Bake and threshold scene shadows"),
    ('TOON', "Toon", "Bake direct lighting as toon shading"),
    ('COLOR_ATTRIBUTE', "Color Attribute", "Bake the active color attribute"),
    ('COMBINED', "Combined", "Bake the combined material result"),
    ('SHARP_EDGES', "Paint Edges", "Paint edges using a selected edge marker"),
    ('CURVATURE', "Curvature", "Paint sharp convex or concave corners"),
    (
        'LINEART',
        "Lineart",
        "Project Curve or Grease Pencil strokes into the layer texture",
    ),
    ('RGB', "RGB", "Use a solid color layer"),
    ('IMAGE_TEXTURE', "Image Texture", "Use an existing image without baking"),
]

TM_EDGE_MARKER_ITEMS = [
    ('SHARP', "Sharp Edge", "Use edges marked Sharp"),
    ('CREASE', "Edge Crease", "Use the crease_edge attribute and its weight"),
    (
        'BEVEL_WEIGHT',
        "Edge Bevel Weight",
        "Use the bevel_weight_edge attribute and its weight",
    ),
]

TM_EDGE_MARKER_ATTRIBUTES = {
    'CREASE': 'crease_edge',
    'BEVEL_WEIGHT': 'bevel_weight_edge',
}

TM_LAYER_ICONS = {
    'ALBEDO': 'MATERIAL',
    'AO': 'SHADING_RENDERED',
    'NORMAL': 'NORMALS_FACE',
    'SHADOW': 'LIGHT',
    'HARD_SHADOW': 'MOD_MASK',
    'TOON': 'SHADING_SOLID',
    'COLOR_ATTRIBUTE': 'GROUP_VCOL',
    'COMBINED': 'RENDER_STILL',
    'SHARP_EDGES': 'EDGESEL',
    'CURVATURE': 'MOD_BEVEL',
    'LINEART': 'GREASEPENCIL',
    'RGB': 'COLOR',
    'IMAGE_TEXTURE': 'IMAGE_DATA',
}

TM_COLOR_RAMP_LAYER_TYPES = {
    'ALBEDO',
    'AO',
    'SHADOW',
    'HARD_SHADOW',
    'TOON',
    'SHARP_EDGES',
    'CURVATURE',
    'LINEART',
    'IMAGE_TEXTURE',
}

TM_LUMINANCE_COLOR_RAMP_TYPES = {'ALBEDO', 'IMAGE_TEXTURE'}

TM_MAPPING_ITEMS = [
    ('UV', "UV", "Map the image with the selected UV map"),
    ('TRIPLANAR', "Triplanar", "Project the image from three object axes"),
    ('MATCAP', "MatCap", "Map the image from object-space surface normals"),
    ('OBJECT', "Object", "Map the image with object coordinates"),
]

TM_SHADER_ITEMS = [
    ('PRINCIPLED', "Principled", "Standard physically based shader"),
    ('UNLIT', "Unlit", "Display the layer stack without scene lighting"),
    ('GLOSSY', "Glossy", "Reflective glossy surface shader"),
    ('GLASS', "Glass", "Transparent refractive glass shader"),
]

TM_SHADER_LABELS = {
    identifier: label
    for identifier, label, _description in TM_SHADER_ITEMS
}

TM_SELECTED_TO_ACTIVE_LAYER_TYPES = {
    'ALBEDO',
    'AO',
    'NORMAL',
    'SHADOW',
    'HARD_SHADOW',
    'TOON',
    'COLOR_ATTRIBUTE',
    'COMBINED',
}

_TM_REBUILDING = False


def tm_mesh_object_poll(_layer, obj):
    return bool(obj and obj.type == 'MESH')


def tm_lineart_object_poll(_layer, obj):
    return bool(
        obj
        and obj.type in {'CURVE', 'GREASEPENCIL', 'GPENCIL'}
    )


def tm_layer_supports_selected_to_active(layer):
    return bool(
        layer.layer_type in TM_SELECTED_TO_ACTIVE_LAYER_TYPES
        and not (
            layer.layer_type == 'AO'
            and layer.ao_method == 'APPROXIMATE'
        )
    )


def tm_color_blend_mode(layer):
    return 'MIX' if layer.blend_mode in TM_SURFACE_BLEND_MODES else layer.blend_mode


def tm_layer_mapping_type(layer):
    # Tangent-space normal maps must use the same UVs as the Normal Map node.
    return 'UV' if layer.blend_mode == 'NORMAL_MAP' else layer.mapping_type


def get_active_material(context):
    obj = context.object
    if not obj or obj.type != 'MESH':
        return None
    return obj.active_material


def find_layer_material(layer):
    layer_pointer = layer.as_pointer()
    for material in bpy.data.materials:
        if not hasattr(material, "tm_settings"):
            continue
        for candidate in material.tm_settings.layers:
            if candidate.as_pointer() == layer_pointer:
                return material
    return None


def find_settings_material(settings):
    material = getattr(settings, "id_data", None)
    if isinstance(material, bpy.types.Material):
        return material
    settings_pointer = settings.as_pointer()
    return next(
        (
            candidate
            for candidate in bpy.data.materials
            if hasattr(candidate, "tm_settings")
            and candidate.tm_settings.as_pointer() == settings_pointer
        ),
        None,
    )


def sync_selected_layer_node(material, context=None):
    if (
        not material
        or not material.use_nodes
        or not material.node_tree
        or not material.tm_settings.initialized
    ):
        return
    settings = material.tm_settings
    if not 0 <= settings.layer_index < len(settings.layers):
        return

    layer = settings.layers[settings.layer_index]
    layer_number = settings.layer_index + 1
    nodes = material.node_tree.nodes
    if layer.blend_mode in TM_SURFACE_BLEND_MODES:
        prefix = 'Height' if layer.blend_mode == 'HEIGHT' else 'Normal Map'
        if layer.layer_type == 'RGB':
            node_names = (
                f"TM {prefix} RGB {layer_number}",
                f"TM RGB {layer_number}",
            )
        else:
            node_names = (
                f"TM {prefix} Image {layer_number}",
                f"TM Image {layer_number}",
            )
    elif layer.layer_type == 'RGB':
        node_names = (
            f"TM Mask RGB {layer_number}",
            f"TM RGB {layer_number}",
        )
    else:
        node_names = (
            f"TM Mask Image {layer_number}",
            f"TM Image {layer_number}",
        )
    active_node = next(
        (nodes.get(node_name) for node_name in node_names if nodes.get(node_name)),
        None,
    )
    if active_node:
        for node in nodes:
            node.select = False
        active_node.select = True
        nodes.active = active_node
        material.node_tree.update_tag()

    active_context = context or bpy.context
    obj = getattr(active_context, "object", None)
    if (
        obj
        and obj.type == 'MESH'
        and obj.active_material == material
        and layer.uv_map
    ):
        uv_layer = obj.data.uv_layers.get(layer.uv_map)
        if uv_layer is not None:
            obj.data.uv_layers.active = uv_layer

    if layer.image:
        scene = getattr(active_context, "scene", None)
        if scene:
            try:
                image_paint = scene.tool_settings.image_paint
                image_paint.mode = 'IMAGE'
                image_paint.canvas = layer.image
            except Exception:
                pass
        screen = getattr(active_context, "screen", None)
        if screen:
            for area in screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    area.spaces.active.image = layer.image

def update_selected_layer(settings, context):
    sync_selected_layer_node(find_settings_material(settings), context)


def update_material_from_layer(layer, context):
    if _TM_REBUILDING:
        return
    material = find_layer_material(layer)
    if material and material.tm_settings.initialized:
        rebuild_texture_material(material)


def update_material_shader(settings, context):
    if _TM_REBUILDING:
        return
    material = find_settings_material(settings)
    if material and settings.initialized:
        rebuild_texture_material(material)


def ensure_layer_color_ramp(layer):
    node_group = layer.color_ramp_group
    if node_group:
        ramp_node = node_group.nodes.get("TM Color Ramp")
        if ramp_node:
            return node_group, ramp_node

    node_group = bpy.data.node_groups.new(
        name=f"TM Ramp - {layer.name}",
        type='ShaderNodeTree',
    )
    node_group.interface.new_socket(
        name="Fac",
        in_out='INPUT',
        socket_type='NodeSocketFloat',
    )
    node_group.interface.new_socket(
        name="Color",
        in_out='OUTPUT',
        socket_type='NodeSocketColor',
    )
    node_group.interface.new_socket(
        name="Alpha",
        in_out='OUTPUT',
        socket_type='NodeSocketFloat',
    )
    nodes = node_group.nodes
    links = node_group.links
    group_input = nodes.new("NodeGroupInput")
    group_input.location = (-300.0, 0.0)
    ramp_node = nodes.new("ShaderNodeValToRGB")
    ramp_node.name = "TM Color Ramp"
    ramp_node.location = (-60.0, 0.0)
    if layer.layer_type in TM_LUMINANCE_COLOR_RAMP_TYPES:
        ramp_node.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        ramp_node.color_ramp.elements[-1].color = (1.0, 1.0, 1.0, 1.0)
    else:
        ramp_node.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 0.0)
        ramp_node.color_ramp.elements[-1].color = (0.0, 0.0, 0.0, 1.0)
    group_output = nodes.new("NodeGroupOutput")
    group_output.location = (240.0, 0.0)
    links.new(group_input.outputs["Fac"], ramp_node.inputs["Fac"])
    links.new(ramp_node.outputs["Color"], group_output.inputs["Color"])
    links.new(ramp_node.outputs["Alpha"], group_output.inputs["Alpha"])
    layer.color_ramp_group = node_group
    return node_group, ramp_node


def update_layer_color_ramp(layer, context):
    if layer.use_color_ramp:
        ensure_layer_color_ramp(layer)
    update_material_from_layer(layer, context)


def update_layer_mask_state(layer, context):
    material = find_layer_material(layer)
    if material and not layer.is_mask:
        layer_index = next(
            (
                index
                for index, candidate in enumerate(material.tm_settings.layers)
                if candidate.as_pointer() == layer.as_pointer()
            ),
            -1,
        )
        if layer_index >= 0:
            parent_depth = layer.depth
            child_index = layer_index + 1
            while child_index < len(material.tm_settings.layers):
                child = material.tm_settings.layers[child_index]
                if child.depth <= parent_depth:
                    break
                child.depth = max(parent_depth, child.depth - 1)
                child_index += 1
    update_material_from_layer(layer, context)


def create_layer_mapping_nodes(
    nodes,
    links,
    layer,
    layer_number,
    x_position,
    y_position,
    frame,
    name_prefix,
):
    name_fragment = f"{name_prefix} " if name_prefix else ""
    mapping_type = tm_layer_mapping_type(layer)
    if mapping_type == 'UV':
        coordinate_node = nodes.new("ShaderNodeUVMap")
        coordinate_node.name = f"TM {name_fragment}UV {layer_number + 1}"
        coordinate_node.label = layer.uv_map or "Active UV Map"
        coordinate_node.uv_map = layer.uv_map
        coordinate_node.location = (x_position, y_position - 80.0)
        coordinate_node.parent = frame
        coordinate_socket = coordinate_node.outputs["UV"]
    elif mapping_type in {'TRIPLANAR', 'OBJECT'}:
        coordinate_node = nodes.new("ShaderNodeTexCoord")
        coordinate_node.name = (
            f"TM {name_fragment}{mapping_type.title()} Coordinates "
            f"{layer_number + 1}"
        )
        coordinate_node.location = (x_position, y_position - 80.0)
        coordinate_node.parent = frame
        coordinate_socket = coordinate_node.outputs[
            "Generated" if mapping_type == 'TRIPLANAR' else "Object"
        ]
    else:
        geometry_node = nodes.new("ShaderNodeNewGeometry")
        geometry_node.name = f"TM {name_fragment}MatCap Normal {layer_number + 1}"
        geometry_node.location = (x_position, y_position - 80.0)
        geometry_node.parent = frame

        transform_node = nodes.new("ShaderNodeVectorTransform")
        transform_node.name = (
            f"TM {name_fragment}MatCap Camera Space {layer_number + 1}"
        )
        transform_node.vector_type = 'NORMAL'
        transform_node.convert_from = 'WORLD'
        transform_node.convert_to = 'CAMERA'
        transform_node.location = (x_position + 180.0, y_position - 80.0)
        transform_node.parent = frame
        links.new(geometry_node.outputs["Normal"], transform_node.inputs["Vector"])

        scale_node = nodes.new("ShaderNodeVectorMath")
        scale_node.operation = 'SCALE'
        scale_node.name = f"TM {name_fragment}MatCap Scale {layer_number + 1}"
        scale_node.inputs["Scale"].default_value = 0.5
        scale_node.location = (x_position + 360.0, y_position - 80.0)
        scale_node.parent = frame
        links.new(transform_node.outputs["Vector"], scale_node.inputs[0])

        center_node = nodes.new("ShaderNodeVectorMath")
        center_node.operation = 'ADD'
        center_node.name = f"TM {name_fragment}MatCap Center {layer_number + 1}"
        center_node.inputs[1].default_value = (0.5, 0.5, 0.5)
        center_node.location = (x_position + 540.0, y_position - 80.0)
        center_node.parent = frame
        links.new(scale_node.outputs["Vector"], center_node.inputs[0])
        coordinate_socket = center_node.outputs["Vector"]

    mapping_node = nodes.new("ShaderNodeMapping")
    mapping_node.name = f"TM {name_fragment}Mapping {layer_number + 1}"
    mapping_node.vector_type = 'POINT'
    mapping_node.inputs["Location"].default_value = layer.mapping_position
    mapping_node.inputs["Rotation"].default_value = layer.mapping_rotation
    mapping_node.inputs["Scale"].default_value = layer.mapping_scale
    mapping_node.location = (x_position + 720.0, y_position - 80.0)
    mapping_node.parent = frame
    links.new(coordinate_socket, mapping_node.inputs["Vector"])
    return mapping_node.outputs["Vector"]


def create_layer_image_sample_nodes(
    nodes,
    links,
    layer,
    layer_number,
    x_position,
    y_position,
    frame,
    name_prefix,
):
    name_fragment = f"{name_prefix} " if name_prefix else ""
    mapping_type = tm_layer_mapping_type(layer)
    if (
        layer.blend_mode == 'NORMAL_MAP'
        and layer.image
        and not layer.image.colorspace_settings.is_data
    ):
        # Color-space changes reload the buffer; keep unsaved paint/bake pixels.
        if layer.image.is_dirty:
            layer.image.pack()
        layer.image.colorspace_settings.name = 'Non-Color'
    mapping_socket = create_layer_mapping_nodes(
        nodes,
        links,
        layer,
        layer_number,
        x_position,
        y_position,
        frame,
        name_prefix,
    )

    if layer.blur_radius <= 0.0001:
        image_node = nodes.new("ShaderNodeTexImage")
        image_node.name = f"TM {name_fragment}Image {layer_number + 1}"
        image_node.label = layer.image.name if layer.image else "No Image"
        image_node.image = layer.image
        image_node.interpolation = 'Linear'
        if mapping_type == 'TRIPLANAR':
            image_node.projection = 'BOX'
            image_node.projection_blend = layer.triplanar_blend
        image_node.location = (x_position + 180.0, y_position + 100.0)
        image_node.parent = frame
        links.new(mapping_socket, image_node.inputs["Vector"])
        return image_node.outputs["Color"], image_node.outputs["Alpha"]

    image_width = max(1, layer.image.size[0])
    image_height = max(1, layer.image.size[1])
    if mapping_type == 'TRIPLANAR':
        kernel = (
            (-1, 0, 0, 0.1),
            (1, 0, 0, 0.1),
            (0, -1, 0, 0.1),
            (0, 1, 0, 0.1),
            (0, 0, -1, 0.1),
            (0, 0, 1, 0.1),
            (0, 0, 0, 0.4),
        )
        center_sample_index = 6
    else:
        kernel = (
            (-1, -1, 0, 0.0625),
            (0, -1, 0, 0.125),
            (1, -1, 0, 0.0625),
            (-1, 0, 0, 0.125),
            (0, 0, 0, 0.25),
            (1, 0, 0, 0.125),
            (-1, 1, 0, 0.0625),
            (0, 1, 0, 0.125),
            (1, 1, 0, 0.0625),
        )
        center_sample_index = 4
    accumulated_color_socket = None
    accumulated_alpha_socket = None
    for sample_index, (offset_x, offset_y, offset_z, weight) in enumerate(kernel):
        vector_socket = mapping_socket
        if offset_x or offset_y or offset_z:
            offset_node = nodes.new("ShaderNodeVectorMath")
            offset_node.operation = 'ADD'
            offset_node.name = (
                f"TM {name_fragment}Blur Offset {layer_number + 1}.{sample_index + 1}"
            )
            offset_node.inputs[1].default_value = (
                offset_x * layer.blur_radius / image_width,
                offset_y * layer.blur_radius / image_height,
                offset_z * layer.blur_radius / max(image_width, image_height),
            )
            offset_node.location = (
                x_position + sample_index * 70.0,
                y_position - 260.0,
            )
            offset_node.parent = frame
            links.new(mapping_socket, offset_node.inputs[0])
            vector_socket = offset_node.outputs["Vector"]

        image_node = nodes.new("ShaderNodeTexImage")
        image_node.name = (
            f"TM {name_fragment}Image {layer_number + 1}"
            if sample_index == center_sample_index
            else f"TM {name_fragment}Blur Sample {layer_number + 1}.{sample_index + 1}"
        )
        image_node.label = layer.image.name if layer.image else "No Image"
        image_node.image = layer.image
        image_node.interpolation = 'Linear'
        if mapping_type == 'TRIPLANAR':
            image_node.projection = 'BOX'
            image_node.projection_blend = layer.triplanar_blend
        image_node.location = (
            x_position + 180.0 + (sample_index % 3) * 180.0,
            y_position + 320.0 - (sample_index // 3) * 220.0,
        )
        image_node.parent = frame
        links.new(vector_socket, image_node.inputs["Vector"])

        color_weight_node = nodes.new("ShaderNodeVectorMath")
        color_weight_node.operation = 'SCALE'
        color_weight_node.name = (
            f"TM {name_fragment}Blur Color Weight "
            f"{layer_number + 1}.{sample_index + 1}"
        )
        color_weight_node.inputs["Scale"].default_value = weight
        color_weight_node.location = (
            x_position + 760.0,
            y_position + 320.0 - sample_index * 90.0,
        )
        color_weight_node.parent = frame
        links.new(image_node.outputs["Color"], color_weight_node.inputs[0])

        alpha_weight_node = nodes.new("ShaderNodeMath")
        alpha_weight_node.operation = 'MULTIPLY'
        alpha_weight_node.name = (
            f"TM {name_fragment}Blur Alpha Weight "
            f"{layer_number + 1}.{sample_index + 1}"
        )
        alpha_weight_node.inputs[1].default_value = weight
        alpha_weight_node.location = (
            x_position + 760.0,
            y_position - 520.0 - sample_index * 90.0,
        )
        alpha_weight_node.parent = frame
        links.new(image_node.outputs["Alpha"], alpha_weight_node.inputs[0])

        weighted_color_socket = color_weight_node.outputs["Vector"]
        weighted_alpha_socket = alpha_weight_node.outputs[0]
        if accumulated_color_socket is not None:
            color_add_node = nodes.new("ShaderNodeVectorMath")
            color_add_node.operation = 'ADD'
            color_add_node.name = (
                f"TM {name_fragment}Blur Color Sum "
                f"{layer_number + 1}.{sample_index + 1}"
            )
            color_add_node.location = (
                x_position + 980.0 + sample_index * 70.0,
                y_position + 160.0,
            )
            color_add_node.parent = frame
            links.new(accumulated_color_socket, color_add_node.inputs[0])
            links.new(weighted_color_socket, color_add_node.inputs[1])
            weighted_color_socket = color_add_node.outputs["Vector"]

            alpha_add_node = nodes.new("ShaderNodeMath")
            alpha_add_node.operation = 'ADD'
            alpha_add_node.name = (
                f"TM {name_fragment}Blur Alpha Sum "
                f"{layer_number + 1}.{sample_index + 1}"
            )
            alpha_add_node.location = (
                x_position + 980.0 + sample_index * 70.0,
                y_position - 180.0,
            )
            alpha_add_node.parent = frame
            links.new(accumulated_alpha_socket, alpha_add_node.inputs[0])
            links.new(weighted_alpha_socket, alpha_add_node.inputs[1])
            weighted_alpha_socket = alpha_add_node.outputs[0]

        accumulated_color_socket = weighted_color_socket
        accumulated_alpha_socket = weighted_alpha_socket

    return accumulated_color_socket, accumulated_alpha_socket


def create_mask_factor_nodes(
    nodes,
    links,
    layer,
    layer_number,
    x_position,
    frame,
    mask_number,
):
    y_position = -420.0 - mask_number * 360.0
    if layer.layer_type == 'RGB':
        color_node = nodes.new("ShaderNodeRGB")
        color_node.name = f"TM Mask RGB {layer_number + 1}"
        color_node.label = layer.name
        color_node.outputs["Color"].default_value = layer.rgb_color
        color_node.location = (x_position + 180.0, y_position + 100.0)
        color_node.parent = frame

        source_alpha_node = nodes.new("ShaderNodeValue")
        source_alpha_node.name = f"TM Mask RGB Alpha {layer_number + 1}"
        source_alpha_node.outputs["Value"].default_value = layer.rgb_color[3]
        source_alpha_node.location = (x_position + 180.0, y_position - 80.0)
        source_alpha_node.parent = frame
        mask_color_socket = color_node.outputs["Color"]
        mask_alpha_socket = source_alpha_node.outputs["Value"]
    else:
        mask_color_socket, mask_alpha_socket = create_layer_image_sample_nodes(
            nodes,
            links,
            layer,
            layer_number,
            x_position,
            y_position,
            frame,
            "Mask",
        )
        if (
            layer.use_color_ramp
            and layer.layer_type in TM_COLOR_RAMP_LAYER_TYPES
        ):
            ramp_group, _ramp_node = ensure_layer_color_ramp(layer)
            group_node = nodes.new("ShaderNodeGroup")
            group_node.name = f"TM Mask Color Ramp {layer_number + 1}"
            group_node.label = "Color Ramp"
            group_node.node_tree = ramp_group
            group_node.location = (x_position + 360.0, y_position - 80.0)
            group_node.parent = frame
            if layer.layer_type in TM_LUMINANCE_COLOR_RAMP_TYPES:
                luminance_input_node = nodes.new("ShaderNodeRGBToBW")
                luminance_input_node.name = (
                    f"TM Mask Ramp Luminance {layer_number + 1}"
                )
                luminance_input_node.location = (
                    x_position + 300.0,
                    y_position - 220.0,
                )
                luminance_input_node.parent = frame
                links.new(
                    mask_color_socket,
                    luminance_input_node.inputs["Color"],
                )
                links.new(
                    luminance_input_node.outputs["Val"],
                    group_node.inputs["Fac"],
                )
            else:
                links.new(mask_alpha_socket, group_node.inputs["Fac"])
            mask_color_socket = group_node.outputs["Color"]
            if layer.layer_type not in TM_LUMINANCE_COLOR_RAMP_TYPES:
                mask_alpha_socket = group_node.outputs["Alpha"]

    luminance_node = nodes.new("ShaderNodeRGBToBW")
    luminance_node.name = f"TM Mask Luminance {layer_number + 1}"
    luminance_node.location = (x_position + 380.0, y_position + 160.0)
    luminance_node.parent = frame
    links.new(mask_color_socket, luminance_node.inputs["Color"])

    strength_node = nodes.new("ShaderNodeMath")
    strength_node.operation = 'MULTIPLY'
    strength_node.name = f"TM Mask Strength {layer_number + 1}"
    strength_node.inputs[1].default_value = layer.opacity
    strength_node.location = (x_position + 380.0, y_position - 80.0)
    strength_node.parent = frame
    links.new(mask_alpha_socket, strength_node.inputs[0])

    scaled_mask_node = nodes.new("ShaderNodeMath")
    scaled_mask_node.operation = 'MULTIPLY'
    scaled_mask_node.name = f"TM Mask Scaled {layer_number + 1}"
    scaled_mask_node.location = (x_position + 560.0, y_position + 80.0)
    scaled_mask_node.parent = frame
    links.new(luminance_node.outputs["Val"], scaled_mask_node.inputs[0])
    links.new(strength_node.outputs[0], scaled_mask_node.inputs[1])

    mask_factor_node = nodes.new("ShaderNodeMath")
    mask_factor_node.operation = 'ADD'
    mask_factor_node.use_clamp = True
    mask_factor_node.name = f"TM Mask Factor {layer_number + 1}"
    mask_factor_node.inputs[1].default_value = 1.0 - layer.opacity
    mask_factor_node.location = (x_position + 720.0, y_position + 80.0)
    mask_factor_node.parent = frame
    links.new(scaled_mask_node.outputs[0], mask_factor_node.inputs[0])
    return mask_factor_node.outputs[0]


def composite_layer_sockets(
    nodes,
    links,
    node_suffix,
    x_position,
    frame,
    blend_mode,
    base_color_socket,
    base_alpha_socket,
    layer_color_socket,
    color_alpha_socket,
    coverage_alpha_socket,
):
    blend_node = nodes.new("ShaderNodeMixRGB")
    blend_node.blend_type = blend_mode
    blend_node.inputs[0].default_value = 1.0
    blend_node.name = f"TM Blend {node_suffix}"
    blend_node.location = (x_position + 380.0, 180.0)
    blend_node.parent = frame
    links.new(base_color_socket, blend_node.inputs[1])
    links.new(layer_color_socket, blend_node.inputs[2])

    opacity_mix_node = nodes.new("ShaderNodeMixRGB")
    opacity_mix_node.blend_type = 'MIX'
    opacity_mix_node.name = f"TM Opacity Mix {node_suffix}"
    opacity_mix_node.location = (x_position + 560.0, 180.0)
    opacity_mix_node.parent = frame
    links.new(color_alpha_socket, opacity_mix_node.inputs[0])
    links.new(base_color_socket, opacity_mix_node.inputs[1])
    links.new(blend_node.outputs[0], opacity_mix_node.inputs[2])

    has_base_node = nodes.new("ShaderNodeMath")
    has_base_node.operation = 'GREATER_THAN'
    has_base_node.inputs[1].default_value = 0.000001
    has_base_node.location = (x_position + 380.0, -220.0)
    has_base_node.parent = frame
    links.new(base_alpha_socket, has_base_node.inputs[0])

    color_result_node = nodes.new("ShaderNodeMixRGB")
    color_result_node.blend_type = 'MIX'
    color_result_node.name = f"TM Color Result {node_suffix}"
    color_result_node.location = (x_position + 720.0, 160.0)
    color_result_node.parent = frame
    links.new(has_base_node.outputs[0], color_result_node.inputs[0])
    links.new(layer_color_socket, color_result_node.inputs[1])
    links.new(opacity_mix_node.outputs[0], color_result_node.inputs[2])

    inverse_alpha_node = nodes.new("ShaderNodeMath")
    inverse_alpha_node.operation = 'SUBTRACT'
    inverse_alpha_node.inputs[0].default_value = 1.0
    inverse_alpha_node.location = (x_position + 560.0, -80.0)
    inverse_alpha_node.parent = frame
    links.new(coverage_alpha_socket, inverse_alpha_node.inputs[1])

    remaining_alpha_node = nodes.new("ShaderNodeMath")
    remaining_alpha_node.operation = 'MULTIPLY'
    remaining_alpha_node.location = (x_position + 720.0, -80.0)
    remaining_alpha_node.parent = frame
    links.new(base_alpha_socket, remaining_alpha_node.inputs[0])
    links.new(inverse_alpha_node.outputs[0], remaining_alpha_node.inputs[1])

    output_alpha_node = nodes.new("ShaderNodeMath")
    output_alpha_node.operation = 'ADD'
    output_alpha_node.use_clamp = True
    output_alpha_node.name = f"TM Alpha Result {node_suffix}"
    output_alpha_node.location = (x_position + 720.0, -220.0)
    output_alpha_node.parent = frame
    links.new(coverage_alpha_socket, output_alpha_node.inputs[0])
    links.new(remaining_alpha_node.outputs[0], output_alpha_node.inputs[1])

    return color_result_node.outputs[0], output_alpha_node.outputs[0]


def create_mix_layer_nodes(
    nodes,
    links,
    layer,
    layer_number,
    graph_position,
    base_color_socket,
    base_alpha_socket,
):
    if layer.blend_mode == 'NORMAL_MAP':
        return base_color_socket, base_alpha_socket

    x_position = graph_position * 760.0
    frame = nodes.new("NodeFrame")
    frame.name = f"TM Layer {layer_number + 1}"
    frame.label = f"{layer.name}  |  {layer.layer_type.replace('_', ' ').title()}"

    if layer.layer_type == 'RGB':
        color_node = nodes.new("ShaderNodeRGB")
        color_node.name = f"TM RGB {layer_number + 1}"
        color_node.label = layer.name
        color_node.outputs["Color"].default_value = layer.rgb_color
        color_node.location = (x_position + 180.0, 100.0)
        color_node.parent = frame
        alpha_value = nodes.new("ShaderNodeValue")
        alpha_value.name = f"TM RGB Alpha {layer_number + 1}"
        alpha_value.outputs["Value"].default_value = layer.rgb_color[3]
        alpha_value.location = (x_position + 180.0, -80.0)
        alpha_value.parent = frame
        layer_color_socket = color_node.outputs["Color"]
        layer_alpha_socket = alpha_value.outputs["Value"]
    else:
        layer_color_socket, layer_alpha_socket = create_layer_image_sample_nodes(
            nodes,
            links,
            layer,
            layer_number,
            x_position,
            0.0,
            frame,
            "",
        )
        if (
            layer.use_color_ramp
            and layer.layer_type in TM_COLOR_RAMP_LAYER_TYPES
        ):
            ramp_group, _ramp_node = ensure_layer_color_ramp(layer)
            group_node = nodes.new("ShaderNodeGroup")
            group_node.name = f"TM Color Ramp {layer_number + 1}"
            group_node.label = "Color Ramp"
            group_node.node_tree = ramp_group
            group_node.location = (x_position + 360.0, -80.0)
            group_node.parent = frame
            if layer.layer_type in TM_LUMINANCE_COLOR_RAMP_TYPES:
                luminance_input_node = nodes.new("ShaderNodeRGBToBW")
                luminance_input_node.name = (
                    f"TM Ramp Luminance {layer_number + 1}"
                )
                luminance_input_node.location = (x_position + 300.0, -220.0)
                luminance_input_node.parent = frame
                links.new(
                    layer_color_socket,
                    luminance_input_node.inputs["Color"],
                )
                links.new(
                    luminance_input_node.outputs["Val"],
                    group_node.inputs["Fac"],
                )
            else:
                links.new(layer_alpha_socket, group_node.inputs["Fac"])
            layer_color_socket = group_node.outputs["Color"]
            if layer.layer_type not in TM_LUMINANCE_COLOR_RAMP_TYPES:
                layer_alpha_socket = group_node.outputs["Alpha"]

    alpha_node = nodes.new("ShaderNodeMath")
    alpha_node.operation = 'MULTIPLY'
    alpha_node.name = f"TM Alpha {layer_number + 1}"
    alpha_node.inputs[1].default_value = layer.opacity
    alpha_node.location = (x_position + 380.0, -80.0)
    alpha_node.parent = frame
    links.new(layer_alpha_socket, alpha_node.inputs[0])

    return composite_layer_sockets(
        nodes,
        links,
        str(layer_number + 1),
        x_position,
        frame,
        tm_color_blend_mode(layer),
        base_color_socket,
        base_alpha_socket,
        layer_color_socket,
        alpha_node.outputs[0],
        alpha_node.outputs[0],
    )


def build_layer_hierarchy(settings):
    root_entries = []
    mask_stack = []
    for layer_number, layer in enumerate(settings.layers):
        if not layer.enabled:
            continue
        if layer.layer_type != 'RGB' and not layer.image:
            continue

        requested_depth = max(0, layer.depth)
        while mask_stack and mask_stack[-1]["depth"] >= requested_depth:
            mask_stack.pop()

        parent = None
        if requested_depth > 0:
            for candidate in reversed(mask_stack):
                if candidate["depth"] == requested_depth - 1:
                    parent = candidate
                    break

        effective_depth = parent["depth"] + 1 if parent else 0
        entry = {
            "layer_number": layer_number,
            "layer": layer,
            "depth": effective_depth,
            "children": [],
        }
        if parent:
            parent["children"].append(entry)
        else:
            root_entries.append(entry)

        if layer.is_mask:
            mask_stack.append(entry)

    return root_entries


def create_masked_group_nodes(
    nodes,
    links,
    entry,
    graph_position,
    base_color_socket,
    base_alpha_socket,
    group_color_socket,
    group_alpha_socket,
    preserve_coverage,
):
    layer = entry["layer"]
    layer_number = entry["layer_number"]
    x_position = graph_position * 760.0
    frame = nodes.new("NodeFrame")
    frame.name = f"TM Mask Group {layer_number + 1}"
    frame.label = f"{layer.name}  |  Mask Group"

    mask_factor_socket = create_mask_factor_nodes(
        nodes,
        links,
        layer,
        layer_number,
        x_position,
        frame,
        0,
    )
    masked_group_alpha_node = nodes.new("ShaderNodeMath")
    masked_group_alpha_node.operation = 'MULTIPLY'
    masked_group_alpha_node.name = f"TM Masked Group Alpha {layer_number + 1}"
    masked_group_alpha_node.location = (x_position + 900.0, -340.0)
    masked_group_alpha_node.parent = frame
    links.new(group_alpha_socket, masked_group_alpha_node.inputs[0])
    links.new(mask_factor_socket, masked_group_alpha_node.inputs[1])
    coverage_alpha_socket = (
        group_alpha_socket
        if preserve_coverage
        else masked_group_alpha_node.outputs[0]
    )

    return composite_layer_sockets(
        nodes,
        links,
        f"Mask Group {layer_number + 1}",
        x_position,
        frame,
        tm_color_blend_mode(layer),
        base_color_socket,
        base_alpha_socket,
        group_color_socket,
        masked_group_alpha_node.outputs[0],
        coverage_alpha_socket,
    )


def compose_layer_hierarchy(
    nodes,
    links,
    entries,
    transparent_color_socket,
    transparent_alpha_socket,
    base_color_socket,
    base_alpha_socket,
    graph_position,
    preserve_mask_coverage,
):
    for entry in reversed(entries):
        layer = entry["layer"]
        layer_number = entry["layer_number"]
        if layer.is_mask:
            if not entry["children"]:
                continue
            group_color_socket, group_alpha_socket, graph_position = (
                compose_layer_hierarchy(
                    nodes,
                    links,
                    entry["children"],
                    transparent_color_socket,
                    transparent_alpha_socket,
                    transparent_color_socket,
                    transparent_alpha_socket,
                    graph_position,
                    False,
                )
            )
            base_color_socket, base_alpha_socket = create_masked_group_nodes(
                nodes,
                links,
                entry,
                graph_position,
                base_color_socket,
                base_alpha_socket,
                group_color_socket,
                group_alpha_socket,
                preserve_mask_coverage,
            )
            graph_position += 1
            continue

        base_color_socket, base_alpha_socket = create_mix_layer_nodes(
            nodes,
            links,
            layer,
            layer_number,
            graph_position,
            base_color_socket,
            base_alpha_socket,
        )
        graph_position += 1

    return base_color_socket, base_alpha_socket, graph_position


def tm_layer_property_coverage_socket(nodes, layer, layer_number):
    if layer.blend_mode == 'NORMAL_MAP':
        node = nodes.get(f"TM Normal Map Opacity {layer_number + 1}")
    else:
        node = nodes.get(f"TM Alpha {layer_number + 1}")
    if not node:
        raise RuntimeError(
            f"Missing material-property coverage for layer '{layer.name}'"
        )
    return node.outputs[0]


def composite_material_property_sockets(
    nodes,
    links,
    node_suffix,
    x_position,
    frame,
    base_roughness_socket,
    base_metalness_socket,
    base_alpha_socket,
    layer_roughness,
    layer_metalness,
    color_alpha_socket,
    coverage_alpha_socket,
):
    has_base_node = nodes.new("ShaderNodeMath")
    has_base_node.operation = 'GREATER_THAN'
    has_base_node.name = f"TM Property Has Base {node_suffix}"
    has_base_node.inputs[1].default_value = 0.000001
    has_base_node.location = (x_position + 180.0, -1460.0)
    has_base_node.parent = frame
    links.new(base_alpha_socket, has_base_node.inputs[0])

    results = []
    for property_name, base_socket, layer_value, y_position in (
        ("Roughness", base_roughness_socket, layer_roughness, -1220.0),
        ("Metalness", base_metalness_socket, layer_metalness, -1340.0),
    ):
        opacity_mix = nodes.new("ShaderNodeMix")
        opacity_mix.data_type = 'FLOAT'
        opacity_mix.clamp_factor = True
        opacity_mix.name = f"TM {property_name} Opacity {node_suffix}"
        opacity_mix.location = (x_position + 360.0, y_position)
        opacity_mix.parent = frame
        opacity_mix.inputs[3].default_value = layer_value
        links.new(color_alpha_socket, opacity_mix.inputs[0])
        links.new(base_socket, opacity_mix.inputs[2])

        result = nodes.new("ShaderNodeMix")
        result.data_type = 'FLOAT'
        result.clamp_factor = True
        result.name = f"TM {property_name} Result {node_suffix}"
        result.location = (x_position + 560.0, y_position)
        result.parent = frame
        result.inputs[2].default_value = layer_value
        links.new(has_base_node.outputs[0], result.inputs[0])
        links.new(opacity_mix.outputs[0], result.inputs[3])
        results.append(result.outputs[0])

    inverse_alpha = nodes.new("ShaderNodeMath")
    inverse_alpha.operation = 'SUBTRACT'
    inverse_alpha.inputs[0].default_value = 1.0
    inverse_alpha.location = (x_position + 360.0, -1500.0)
    inverse_alpha.parent = frame
    links.new(coverage_alpha_socket, inverse_alpha.inputs[1])

    remaining_alpha = nodes.new("ShaderNodeMath")
    remaining_alpha.operation = 'MULTIPLY'
    remaining_alpha.location = (x_position + 540.0, -1500.0)
    remaining_alpha.parent = frame
    links.new(base_alpha_socket, remaining_alpha.inputs[0])
    links.new(inverse_alpha.outputs[0], remaining_alpha.inputs[1])

    output_alpha = nodes.new("ShaderNodeMath")
    output_alpha.operation = 'ADD'
    output_alpha.use_clamp = True
    output_alpha.name = f"TM Property Alpha {node_suffix}"
    output_alpha.location = (x_position + 720.0, -1500.0)
    output_alpha.parent = frame
    links.new(coverage_alpha_socket, output_alpha.inputs[0])
    links.new(remaining_alpha.outputs[0], output_alpha.inputs[1])
    return results[0], results[1], output_alpha.outputs[0]


def compose_material_property_hierarchy(
    nodes,
    links,
    entries,
    transparent_roughness_socket,
    transparent_metalness_socket,
    transparent_alpha_socket,
    base_roughness_socket,
    base_metalness_socket,
    base_alpha_socket,
    graph_position,
    preserve_mask_coverage,
):
    for entry in reversed(entries):
        layer = entry["layer"]
        layer_number = entry["layer_number"]
        x_position = graph_position * 760.0
        if layer.is_mask:
            if not entry["children"]:
                continue
            group_roughness, group_metalness, group_alpha, graph_position = (
                compose_material_property_hierarchy(
                    nodes,
                    links,
                    entry["children"],
                    transparent_roughness_socket,
                    transparent_metalness_socket,
                    transparent_alpha_socket,
                    transparent_roughness_socket,
                    transparent_metalness_socket,
                    transparent_alpha_socket,
                    graph_position,
                    False,
                )
            )
            x_position = graph_position * 760.0
            frame = nodes.new("NodeFrame")
            frame.name = f"TM Mask Properties {layer_number + 1}"
            frame.label = f"{layer.name}  |  Mask Material Properties"
            mask_factor_node = nodes.get(f"TM Mask Factor {layer_number + 1}")
            if not mask_factor_node:
                raise RuntimeError(f"Missing mask factor for layer '{layer.name}'")
            masked_alpha = nodes.new("ShaderNodeMath")
            masked_alpha.operation = 'MULTIPLY'
            masked_alpha.name = f"TM Masked Property Alpha {layer_number + 1}"
            masked_alpha.location = (x_position + 40.0, -1460.0)
            masked_alpha.parent = frame
            links.new(group_alpha, masked_alpha.inputs[0])
            links.new(mask_factor_node.outputs[0], masked_alpha.inputs[1])
            coverage = (
                group_alpha if preserve_mask_coverage else masked_alpha.outputs[0]
            )
            base_roughness_socket, base_metalness_socket, base_alpha_socket = (
                composite_material_property_sockets(
                    nodes,
                    links,
                    f"Mask Group {layer_number + 1}",
                    x_position,
                    frame,
                    base_roughness_socket,
                    base_metalness_socket,
                    base_alpha_socket,
                    layer.roughness,
                    layer.metalness,
                    masked_alpha.outputs[0],
                    coverage,
                )
            )
            # A mask's sliders are intentionally ignored; the child subtree
            # already contains its composed property values.
            roughness_result = nodes.get(f"TM Roughness Result Mask Group {layer_number + 1}")
            metalness_result = nodes.get(f"TM Metalness Result Mask Group {layer_number + 1}")
            roughness_result.inputs[2].default_value = 0.0
            metalness_result.inputs[2].default_value = 0.0
            links.new(group_roughness, roughness_result.inputs[2])
            links.new(group_metalness, metalness_result.inputs[2])
            roughness_opacity = nodes.get(f"TM Roughness Opacity Mask Group {layer_number + 1}")
            metalness_opacity = nodes.get(f"TM Metalness Opacity Mask Group {layer_number + 1}")
            roughness_opacity.inputs[3].default_value = 0.0
            metalness_opacity.inputs[3].default_value = 0.0
            links.new(group_roughness, roughness_opacity.inputs[3])
            links.new(group_metalness, metalness_opacity.inputs[3])
            graph_position += 1
            continue

        frame = nodes.new("NodeFrame")
        frame.name = f"TM Layer Properties {layer_number + 1}"
        frame.label = f"{layer.name}  |  Roughness {layer.roughness:.2f}  |  Metalness {layer.metalness:.2f}"
        coverage = tm_layer_property_coverage_socket(nodes, layer, layer_number)
        base_roughness_socket, base_metalness_socket, base_alpha_socket = (
            composite_material_property_sockets(
                nodes,
                links,
                str(layer_number + 1),
                x_position,
                frame,
                base_roughness_socket,
                base_metalness_socket,
                base_alpha_socket,
                layer.roughness,
                layer.metalness,
                coverage,
                coverage,
            )
        )
        graph_position += 1

    return (
        base_roughness_socket,
        base_metalness_socket,
        base_alpha_socket,
        graph_position,
    )


def create_normal_map_nodes(
    nodes, links, layer, layer_number, x_position, frame,
    color_socket, alpha_socket, normal_socket, mask_socket,
    name_suffix="", y_offset=0.0,
):
    """Decode tangent normals, then alpha-composite normalized world vectors."""
    namespace = "TM Normal Map"
    if name_suffix:
        namespace = f"{namespace} {name_suffix}"

    def node(node_type, label, offset):
        result = nodes.new(node_type)
        result.name = f"{namespace} {label} {layer_number + 1}"
        result.location = (x_position + offset, -820.0 + y_offset)
        result.parent = frame
        return result

    normal_map = node("ShaderNodeNormalMap", "Decode", 420.0)
    normal_map.space = 'TANGENT'
    normal_map.uv_map = layer.uv_map
    normal_map.inputs["Strength"].default_value = layer.normal_strength
    links.new(color_socket, normal_map.inputs["Color"])

    opacity = node("ShaderNodeMath", "Opacity", 600.0)
    opacity.operation = 'MULTIPLY'
    opacity.use_clamp = True
    opacity.inputs[1].default_value = layer.opacity
    links.new(alpha_socket, opacity.inputs[0])
    factor_socket = opacity.outputs[0]
    if mask_socket is not None:
        mask = node("ShaderNodeMath", "Mask", 780.0)
        mask.operation = 'MULTIPLY'
        mask.use_clamp = True
        links.new(factor_socket, mask.inputs[0])
        links.new(mask_socket, mask.inputs[1])
        factor_socket = mask.outputs[0]

    if normal_socket is None:
        geometry = node("ShaderNodeNewGeometry", "Base", 600.0)
        geometry.location.y -= 300.0
        normal_socket = geometry.outputs["Normal"]

    # base + opacity * (layer - base). Vector Math keeps negative components.
    difference = node("ShaderNodeVectorMath", "Difference", 960.0)
    difference.operation = 'SUBTRACT'
    links.new(normal_map.outputs["Normal"], difference.inputs[0])
    links.new(normal_socket, difference.inputs[1])
    weighted = node("ShaderNodeVectorMath", "Weight", 1140.0)
    weighted.operation = 'SCALE'
    links.new(difference.outputs["Vector"], weighted.inputs[0])
    links.new(factor_socket, weighted.inputs["Scale"])
    mixed = node("ShaderNodeVectorMath", "Mix", 1320.0)
    mixed.operation = 'ADD'
    links.new(normal_socket, mixed.inputs[0])
    links.new(weighted.outputs["Vector"], mixed.inputs[1])
    result = node("ShaderNodeVectorMath", "Result", 1500.0)
    result.operation = 'NORMALIZE'
    links.new(mixed.outputs["Vector"], result.inputs[0])
    return result.outputs["Vector"]


def create_surface_layer_nodes(
    nodes,
    links,
    layer,
    layer_number,
    graph_position,
    normal_socket,
    normal_without_height_socket,
    height_channel_socket,
    has_height,
    mask_socket,
):
    x_position = graph_position * 760.0
    prefix = 'Normal Map' if layer.blend_mode == 'NORMAL_MAP' else 'Height'
    frame = nodes.new("NodeFrame")
    frame.name = f"TM {prefix} Layer {layer_number + 1}"
    overlay_label = "  |  Overlay Height" if (
        layer.blend_mode == 'HEIGHT' and layer.overlay_height
    ) else ""
    frame.label = f"{layer.name}  |  {prefix}{overlay_label}"

    if layer.layer_type == 'RGB':
        color_node = nodes.new("ShaderNodeRGB")
        color_node.name = f"TM {prefix} RGB {layer_number + 1}"
        color_node.label = layer.name
        color_node.outputs["Color"].default_value = layer.rgb_color
        color_node.location = (x_position + 180.0, -820.0)
        color_node.parent = frame

        alpha_node = nodes.new("ShaderNodeValue")
        alpha_node.name = f"TM {prefix} RGB Alpha {layer_number + 1}"
        alpha_node.outputs["Value"].default_value = layer.rgb_color[3]
        alpha_node.location = (x_position + 180.0, -980.0)
        alpha_node.parent = frame
        color_socket = color_node.outputs["Color"]
        source_alpha_socket = alpha_node.outputs["Value"]
    else:
        color_socket, source_alpha_socket = create_layer_image_sample_nodes(
            nodes,
            links,
            layer,
            layer_number,
            x_position,
            -920.0,
            frame,
            prefix,
        )

    if layer.blend_mode == 'NORMAL_MAP':
        # Color ramps encode scalar effects, not tangent-space directions.
        composed_normal = create_normal_map_nodes(
            nodes, links, layer, layer_number, x_position, frame,
            color_socket, source_alpha_socket, normal_socket, mask_socket,
        )
        if has_height:
            normal_without_height_socket = create_normal_map_nodes(
                nodes, links, layer, layer_number, x_position, frame,
                color_socket, source_alpha_socket,
                normal_without_height_socket, mask_socket,
                name_suffix="Base", y_offset=-380.0,
            )
        else:
            normal_without_height_socket = composed_normal
        return (
            composed_normal,
            normal_without_height_socket,
            height_channel_socket,
            has_height,
        )

    # Height is a paint mask stored independently in alpha. RGB can therefore
    # contain any paint color without changing relief intensity or direction.
    height_socket = source_alpha_socket

    opacity_node = nodes.new("ShaderNodeMath")
    opacity_node.operation = 'MULTIPLY'
    opacity_node.name = f"TM Height Opacity {layer_number + 1}"
    opacity_node.inputs[1].default_value = layer.opacity
    opacity_node.location = (x_position + 860.0, -900.0)
    opacity_node.parent = frame
    links.new(height_socket, opacity_node.inputs[0])
    height_socket = opacity_node.outputs[0]

    if mask_socket is not None:
        mask_node = nodes.new("ShaderNodeMath")
        mask_node.operation = 'MULTIPLY'
        mask_node.name = f"TM Height Mask {layer_number + 1}"
        mask_node.location = (x_position + 1020.0, -900.0)
        mask_node.parent = frame
        links.new(height_socket, mask_node.inputs[0])
        links.new(mask_socket, mask_node.inputs[1])
        height_socket = mask_node.outputs[0]

    signed_height = nodes.new("ShaderNodeMath")
    signed_height.operation = 'MULTIPLY'
    signed_height.name = f"TM Height Channel {layer_number + 1}"
    signed_height.location = (x_position + 1200.0, -1120.0)
    signed_height.parent = frame
    signed_height.inputs[1].default_value = (
        layer.height_direction * layer.height_strength
    )
    links.new(height_socket, signed_height.inputs[0])
    layer_height_socket = signed_height.outputs[0]

    if height_channel_socket is not None:
        if layer.overlay_height:
            height_mix = nodes.new("ShaderNodeMix")
            height_mix.data_type = 'FLOAT'
            height_mix.name = f"TM Height Channel Overlay {layer_number + 1}"
            height_mix.location = (x_position + 1580.0, -1120.0)
            height_mix.parent = frame
            links.new(height_socket, height_mix.inputs[0])
            links.new(height_channel_socket, height_mix.inputs[2])
            links.new(layer_height_socket, height_mix.inputs[3])
            height_channel_socket = height_mix.outputs[0]
        else:
            height_add = nodes.new("ShaderNodeMath")
            height_add.operation = 'ADD'
            height_add.name = f"TM Height Channel Add {layer_number + 1}"
            height_add.location = (x_position + 1580.0, -1120.0)
            height_add.parent = frame
            links.new(height_channel_socket, height_add.inputs[0])
            links.new(layer_height_socket, height_add.inputs[1])
            height_channel_socket = height_add.outputs[0]
    else:
        height_channel_socket = layer_height_socket

    bump_node = nodes.new("ShaderNodeBump")
    bump_node.name = f"TM Height Bump {layer_number + 1}"
    direction = "Elevation" if layer.height_direction >= 0.0 else "Depth"
    effective_strength = abs(layer.height_direction) * layer.height_strength
    bump_node.label = f"{direction}: {effective_strength:.2f}"
    bump_node.invert = layer.height_direction < 0.0
    bump_node.inputs["Strength"].default_value = effective_strength
    bump_node.inputs["Distance"].default_value = 0.1
    bump_node.location = (x_position + 1200.0, -820.0)
    bump_node.parent = frame
    links.new(height_socket, bump_node.inputs["Height"])
    bump_base_socket = normal_socket
    if layer.overlay_height and has_height:
        bump_base_socket = normal_without_height_socket
    if bump_base_socket is not None:
        links.new(bump_base_socket, bump_node.inputs["Normal"])

    composed_normal = bump_node.outputs["Normal"]
    if layer.overlay_height and has_height:
        # Replace only the covered part of the accumulated Height chain. The
        # alternate bump starts from the Normal Map-only baseline, while the
        # effective painted alpha selects it over the previous full normal.
        def overlay_node(node_type, label, offset):
            result = nodes.new(node_type)
            result.name = f"TM Height Overlay {label} {layer_number + 1}"
            result.location = (x_position + offset, -820.0)
            result.parent = frame
            return result

        old_normal_socket = normal_socket
        if old_normal_socket is None:
            geometry = overlay_node("ShaderNodeNewGeometry", "Base", 1200.0)
            geometry.location.y -= 260.0
            old_normal_socket = geometry.outputs["Normal"]

        difference = overlay_node("ShaderNodeVectorMath", "Difference", 1400.0)
        difference.operation = 'SUBTRACT'
        links.new(composed_normal, difference.inputs[0])
        links.new(old_normal_socket, difference.inputs[1])

        weighted = overlay_node("ShaderNodeVectorMath", "Weight", 1580.0)
        weighted.operation = 'SCALE'
        links.new(difference.outputs["Vector"], weighted.inputs[0])
        links.new(height_socket, weighted.inputs["Scale"])

        mixed = overlay_node("ShaderNodeVectorMath", "Mix", 1760.0)
        mixed.operation = 'ADD'
        links.new(old_normal_socket, mixed.inputs[0])
        links.new(weighted.outputs["Vector"], mixed.inputs[1])

        result = overlay_node("ShaderNodeVectorMath", "Result", 1940.0)
        result.operation = 'NORMALIZE'
        links.new(mixed.outputs["Vector"], result.inputs[0])
        composed_normal = result.outputs["Vector"]

    return (
        composed_normal,
        normal_without_height_socket,
        height_channel_socket,
        True,
    )


def hierarchy_contains_surface(entries):
    return any(
        entry["layer"].blend_mode in TM_SURFACE_BLEND_MODES
        or hierarchy_contains_surface(entry["children"])
        for entry in entries
    )


def combine_surface_masks(
    nodes,
    links,
    first_socket,
    second_socket,
    layer_number,
    x_position,
    frame,
):
    if first_socket is None:
        return second_socket
    combine_node = nodes.new("ShaderNodeMath")
    combine_node.operation = 'MULTIPLY'
    combine_node.name = f"TM Surface Combined Mask {layer_number + 1}"
    combine_node.location = (x_position + 900.0, -500.0)
    combine_node.parent = frame
    links.new(first_socket, combine_node.inputs[0])
    links.new(second_socket, combine_node.inputs[1])
    return combine_node.outputs[0]


def compose_surface_hierarchy(
    nodes,
    links,
    entries,
    normal_socket,
    normal_without_height_socket,
    height_channel_socket,
    has_height,
    inherited_mask_socket,
    graph_position,
):
    for entry in reversed(entries):
        layer = entry["layer"]
        layer_number = entry["layer_number"]
        if layer.is_mask:
            if layer.blend_mode in TM_SURFACE_BLEND_MODES:
                (
                    normal_socket,
                    normal_without_height_socket,
                    height_channel_socket,
                    has_height,
                ) = create_surface_layer_nodes(
                    nodes,
                    links,
                    layer,
                    layer_number,
                    graph_position,
                    normal_socket,
                    normal_without_height_socket,
                    height_channel_socket,
                    has_height,
                    inherited_mask_socket,
                )
                graph_position += 1

            if hierarchy_contains_surface(entry["children"]):
                x_position = graph_position * 760.0
                frame = nodes.new("NodeFrame")
                frame.name = f"TM Surface Mask Group {layer_number + 1}"
                frame.label = f"{layer.name}  |  Surface Mask"
                mask_socket = create_mask_factor_nodes(
                    nodes,
                    links,
                    layer,
                    layer_number,
                    x_position,
                    frame,
                    1,
                )
                mask_socket = combine_surface_masks(
                    nodes,
                    links,
                    inherited_mask_socket,
                    mask_socket,
                    layer_number,
                    x_position,
                    frame,
                )
                graph_position += 1
                (
                    normal_socket,
                    normal_without_height_socket,
                    height_channel_socket,
                    has_height,
                    graph_position,
                ) = compose_surface_hierarchy(
                    nodes,
                    links,
                    entry["children"],
                    normal_socket,
                    normal_without_height_socket,
                    height_channel_socket,
                    has_height,
                    mask_socket,
                    graph_position,
                )
            continue

        if layer.blend_mode not in TM_SURFACE_BLEND_MODES:
            continue
        (
            normal_socket,
            normal_without_height_socket,
            height_channel_socket,
            has_height,
        ) = create_surface_layer_nodes(
            nodes,
            links,
            layer,
            layer_number,
            graph_position,
            normal_socket,
            normal_without_height_socket,
            height_channel_socket,
            has_height,
            inherited_mask_socket,
        )
        graph_position += 1
    return (
        normal_socket,
        normal_without_height_socket,
        height_channel_socket,
        has_height,
        graph_position,
    )


def create_height_bake_output_node(nodes, links, height_channel_socket, x_position):
    """Expose signed Height as neutral-gray 0..1 data for multi-channel bake."""
    if height_channel_socket is None:
        output = nodes.new("ShaderNodeValue")
        output.outputs[0].default_value = 0.5
    else:
        output = nodes.new("ShaderNodeMath")
        output.operation = 'MULTIPLY_ADD'
        output.use_clamp = True
        output.inputs[1].default_value = 0.5
        output.inputs[2].default_value = 0.5
        links.new(height_channel_socket, output.inputs[0])
    output.name = "TM Height Bake Output"
    output.label = "Height Bake: 0.5 Neutral"
    output.location = (x_position, -1180.0)
    return output.outputs[0]


def create_material_shader_nodes(
    nodes,
    links,
    settings,
    color_socket,
    alpha_socket,
    normal_socket,
    roughness_socket,
    metalness_socket,
    x_position,
):
    shader_type = settings.shader_type
    if shader_type == 'UNLIT':
        shader_node = nodes.new("ShaderNodeEmission")
        shader_node.name = "TM Unlit Emission"
        shader_node.label = "Unlit"
        shader_node.inputs["Strength"].default_value = 1.0
        links.new(color_socket, shader_node.inputs["Color"])
    elif shader_type == 'GLOSSY':
        shader_node = nodes.new("ShaderNodeBsdfGlossy")
        shader_node.name = "TM Glossy BSDF"
        links.new(color_socket, shader_node.inputs["Color"])
        links.new(roughness_socket, shader_node.inputs["Roughness"])
    elif shader_type == 'GLASS':
        shader_node = nodes.new("ShaderNodeBsdfGlass")
        shader_node.name = "TM Glass BSDF"
        links.new(color_socket, shader_node.inputs["Color"])
        links.new(roughness_socket, shader_node.inputs["Roughness"])
    else:
        shader_node = nodes.new("ShaderNodeBsdfPrincipled")
        shader_node.name = "TM Principled BSDF"
        links.new(color_socket, shader_node.inputs["Base Color"])
        links.new(alpha_socket, shader_node.inputs["Alpha"])
        links.new(roughness_socket, shader_node.inputs["Roughness"])
        links.new(metalness_socket, shader_node.inputs["Metallic"])

    if normal_socket is not None and "Normal" in shader_node.inputs:
        links.new(normal_socket, shader_node.inputs["Normal"])

    shader_node.location = (x_position, 100.0)
    shader_output = shader_node.outputs[0]
    output_x = x_position + 300.0

    if shader_type != 'PRINCIPLED':
        transparent_node = nodes.new("ShaderNodeBsdfTransparent")
        transparent_node.name = "TM Transparent BSDF"
        transparent_node.location = (x_position, -120.0)

        alpha_mix = nodes.new("ShaderNodeMixShader")
        alpha_mix.name = "TM Alpha Mix"
        alpha_mix.location = (x_position + 280.0, 100.0)
        links.new(alpha_socket, alpha_mix.inputs[0])
        links.new(transparent_node.outputs[0], alpha_mix.inputs[1])
        links.new(shader_output, alpha_mix.inputs[2])
        shader_output = alpha_mix.outputs[0]
        output_x = x_position + 580.0

    output_node = nodes.new("ShaderNodeOutputMaterial")
    output_node.name = "TM Material Output"
    output_node.location = (output_x, 100.0)
    links.new(shader_output, output_node.inputs["Surface"])


def rebuild_texture_material(material):
    global _TM_REBUILDING
    if _TM_REBUILDING or not material:
        return

    _TM_REBUILDING = True
    try:
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        nodes.clear()

        base_color_node = nodes.new("ShaderNodeRGB")
        base_color_node.name = "TM Base Color"
        base_color_node.outputs[0].default_value = (0.0, 0.0, 0.0, 1.0)
        base_color_node.location = (-300.0, 160.0)
        base_color_socket = base_color_node.outputs[0]

        base_alpha_node = nodes.new("ShaderNodeValue")
        base_alpha_node.name = "TM Base Alpha"
        base_alpha_node.outputs[0].default_value = 0.0
        base_alpha_node.location = (-300.0, 0.0)
        base_alpha_socket = base_alpha_node.outputs[0]
        property_transparent_alpha_socket = base_alpha_socket

        base_roughness_node = nodes.new("ShaderNodeValue")
        base_roughness_node.name = "TM Base Roughness"
        base_roughness_node.outputs[0].default_value = 0.5
        base_roughness_node.location = (-300.0, -160.0)
        base_roughness_socket = base_roughness_node.outputs[0]

        base_metalness_node = nodes.new("ShaderNodeValue")
        base_metalness_node.name = "TM Base Metalness"
        base_metalness_node.outputs[0].default_value = 0.0
        base_metalness_node.location = (-300.0, -260.0)
        base_metalness_socket = base_metalness_node.outputs[0]

        hierarchy = build_layer_hierarchy(material.tm_settings)
        base_color_socket, base_alpha_socket, composed_layer_count = (
            compose_layer_hierarchy(
                nodes,
                links,
                hierarchy,
                base_color_socket,
                base_alpha_socket,
                base_color_socket,
                base_alpha_socket,
                0,
                True,
            )
        )

        (
            normal_socket,
            _normal_without_height_socket,
            height_channel_socket,
            _has_height,
            total_graph_count,
        ) = compose_surface_hierarchy(
            nodes,
            links,
            hierarchy,
            None,
            None,
            None,
            False,
            None,
            composed_layer_count,
        )
        create_height_bake_output_node(
            nodes,
            links,
            height_channel_socket,
            max(200.0, total_graph_count * 760.0 + 100.0),
        )

        (
            roughness_socket,
            metalness_socket,
            _property_alpha_socket,
            total_graph_count,
        ) = compose_material_property_hierarchy(
            nodes,
            links,
            hierarchy,
            base_roughness_socket,
            base_metalness_socket,
            property_transparent_alpha_socket,
            base_roughness_socket,
            base_metalness_socket,
            property_transparent_alpha_socket,
            total_graph_count,
            True,
        )

        shader_x = max(200.0, total_graph_count * 760.0 + 500.0)
        create_material_shader_nodes(
            nodes,
            links,
            material.tm_settings,
            base_color_socket,
            base_alpha_socket,
            normal_socket,
            roughness_socket,
            metalness_socket,
            shader_x,
        )

        try:
            material.surface_render_method = 'DITHERED'
        except Exception:
            try:
                material.blend_method = 'BLEND'
            except Exception:
                pass
        material.node_tree.update_tag()
        sync_selected_layer_node(material)
    finally:
        _TM_REBUILDING = False


def tm_clear_image(image):
    total = image.size[0] * image.size[1] * 4
    image.pixels.foreach_set(array('f', [0.0]) * total)
    image.update()


def tm_initialize_height_paint_image(image):
    """Create a white color canvas with an empty alpha relief mask."""
    if image.colorspace_settings.name != 'sRGB':
        image.colorspace_settings.name = 'sRGB'
    total_pixels = image.size[0] * image.size[1]
    image.pixels.foreach_set(array('f', [1.0, 1.0, 1.0, 0.0]) * total_pixels)
    image.update()


def tm_clip_uv_line(x1, y1, x2, y2):
    difference_x = x2 - x1
    difference_y = y2 - y1
    minimum_factor = 0.0
    maximum_factor = 1.0
    for direction, distance in (
        (-difference_x, x1),
        (difference_x, 1.0 - x1),
        (-difference_y, y1),
        (difference_y, 1.0 - y1),
    ):
        if abs(direction) < 0.0000001:
            if distance < 0.0:
                return None
            continue
        factor = distance / direction
        if direction < 0.0:
            minimum_factor = max(minimum_factor, factor)
        else:
            maximum_factor = min(maximum_factor, factor)
        if minimum_factor > maximum_factor:
            return None
    return (
        x1 + difference_x * minimum_factor,
        y1 + difference_y * minimum_factor,
        x1 + difference_x * maximum_factor,
        y1 + difference_y * maximum_factor,
    )


def tm_random(value):
    return math.sin(value * 12.9898) * 43758.5453 % 1.0


def tm_brush_mask(mask, width, height, center_x, center_y, radius, opacity, hardness):
    radius = max(0.5, float(radius))
    minimum_x = max(0, int(math.floor(center_x - radius)))
    maximum_x = min(width - 1, int(math.ceil(center_x + radius)))
    minimum_y = max(0, int(math.floor(center_y - radius)))
    maximum_y = min(height - 1, int(math.ceil(center_y + radius)))
    inner_radius = radius * max(0.0, min(1.0, hardness))
    for y in range(minimum_y, maximum_y + 1):
        for x in range(minimum_x, maximum_x + 1):
            dx = (x + 0.5) - center_x
            dy = (y + 0.5) - center_y
            distance = math.sqrt(dx * dx + dy * dy)
            if distance > radius:
                continue
            if distance <= inner_radius or radius <= inner_radius + 0.000001:
                falloff = 1.0
            else:
                falloff = 1.0 - (
                    (distance - inner_radius) / (radius - inner_radius)
                )
            index = y * width + x
            mask[index] = max(mask[index], opacity * falloff)


def tm_line_mask(
    mask,
    width,
    height,
    x1,
    y1,
    x2,
    y2,
    thickness,
    opacity,
    hardness,
    irregularity,
    seed,
):
    dx = x2 - x1
    dy = y2 - y1
    distance = math.sqrt(dx * dx + dy * dy)
    steps = max(1, int(math.ceil(distance * 1.5)))
    for step in range(steps + 1):
        factor = step / steps
        noise = tm_random(seed + step * 0.173)
        radius_factor = 1.0 + (noise - 0.5) * irregularity * 0.9
        opacity_factor = 1.0 - noise * irregularity * 0.75
        tm_brush_mask(
            mask,
            width,
            height,
            x1 + dx * factor,
            y1 + dy * factor,
            max(0.5, thickness * 0.5 * radius_factor),
            opacity * opacity_factor,
            hardness,
        )


def tm_smooth_mask(mask, width, height, radius):
    radius = max(0, int(radius))
    if radius == 0:
        return
    window_size = radius * 2 + 1
    horizontal = array('f', [0.0]) * (width * height)
    for y in range(height):
        row_start = y * width
        running_total = sum(
            mask[row_start + x] for x in range(min(width, radius + 1))
        )
        for x in range(width):
            horizontal[row_start + x] = running_total / window_size
            remove_x = x - radius
            add_x = x + radius + 1
            if remove_x >= 0:
                running_total -= mask[row_start + remove_x]
            if add_x < width:
                running_total += mask[row_start + add_x]
    for x in range(width):
        running_total = sum(
            horizontal[y * width + x]
            for y in range(min(height, radius + 1))
        )
        for y in range(height):
            mask[y * width + x] = running_total / window_size
            remove_y = y - radius
            add_y = y + radius + 1
            if remove_y >= 0:
                running_total -= horizontal[remove_y * width + x]
            if add_y < height:
                running_total += horizontal[add_y * width + x]


def tm_world_bounds(obj):
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    minimum = Vector((
        min(corner.x for corner in corners),
        min(corner.y for corner in corners),
        min(corner.z for corner in corners),
    ))
    maximum = Vector((
        max(corner.x for corner in corners),
        max(corner.y for corner in corners),
        max(corner.z for corner in corners),
    ))
    return minimum, maximum


def tm_bounds_overlap(minimum, maximum, target_minimum, target_maximum):
    return all(
        maximum[axis] >= target_minimum[axis]
        and minimum[axis] <= target_maximum[axis]
        for axis in range(3)
    )


def tm_build_approx_ao_bvh(context, target_obj, layer, target_mesh=None):
    distance = layer.ao_approx_distance
    target_minimum, target_maximum = tm_world_bounds(target_obj)
    padding = Vector((distance, distance, distance))
    expanded_minimum = target_minimum - padding
    expanded_maximum = target_maximum + padding
    target_center = (target_minimum + target_maximum) * 0.5

    candidates = []
    for source_obj in context.scene.objects:
        if source_obj.type != 'MESH' or source_obj.hide_render:
            continue
        if layer.ao_approx_self_only and source_obj != target_obj:
            continue
        try:
            source_minimum, source_maximum = tm_world_bounds(source_obj)
        except Exception:
            continue
        if not tm_bounds_overlap(
            source_minimum,
            source_maximum,
            expanded_minimum,
            expanded_maximum,
        ):
            continue
        source_center = (source_minimum + source_maximum) * 0.5
        candidates.append(((source_center - target_center).length, source_obj))
    candidates.sort(key=lambda item: (item[1] != target_obj, item[0]))

    maximum_triangles = 200000
    vertices = []
    triangles = []
    owners = []
    depsgraph = context.evaluated_depsgraph_get()

    for _candidate_distance, source_obj in candidates:
        evaluated_obj = None
        source_mesh = None
        temporary_mesh = False
        try:
            if source_obj == target_obj:
                source_mesh = target_mesh or source_obj.data
                matrix = source_obj.matrix_world
            else:
                evaluated_obj = source_obj.evaluated_get(depsgraph)
                source_mesh = evaluated_obj.to_mesh()
                matrix = evaluated_obj.matrix_world
                temporary_mesh = True
            source_mesh.calc_loop_triangles()
            owner_pointer = source_obj.as_pointer()
            for triangle in source_mesh.loop_triangles:
                triangle_vertices = [
                    matrix @ source_mesh.vertices[index].co
                    for index in triangle.vertices
                ]
                vertex_offset = len(vertices)
                vertices.extend(triangle_vertices)
                triangles.append(
                    (vertex_offset, vertex_offset + 1, vertex_offset + 2)
                )
                owners.append((owner_pointer, triangle.polygon_index))
                if len(triangles) >= maximum_triangles:
                    break
        finally:
            if temporary_mesh and source_mesh is not None:
                evaluated_obj.to_mesh_clear()
        if len(triangles) >= maximum_triangles:
            break

    if not triangles:
        raise RuntimeError("No nearby mesh geometry was found")
    spatial_tree = BVHTree.FromPolygons(
        vertices,
        triangles,
        all_triangles=True,
    )
    return spatial_tree, owners


def tm_rasterize_approx_ao_targets(obj, width, height):
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if not uv_layer:
        raise RuntimeError("Could not access the selected UV map")
    mesh.calc_loop_triangles()
    matrix = obj.matrix_world
    normal_matrix = matrix.to_3x3().inverted().transposed()
    world_vertices = [matrix @ vertex.co for vertex in mesh.vertices]
    total = width * height
    positions = [None] * total
    normals = [None] * total
    owners = [None] * total
    owner_pointer = obj.as_pointer()

    for triangle in mesh.loop_triangles:
        loop_indices = triangle.loops
        vertex_indices = triangle.vertices
        uv_coordinates = [
            uv_layer.data[loop_index].uv.copy()
            for loop_index in loop_indices
        ]
        denominator = (
            (uv_coordinates[1].y - uv_coordinates[2].y)
            * (uv_coordinates[0].x - uv_coordinates[2].x)
            + (uv_coordinates[2].x - uv_coordinates[1].x)
            * (uv_coordinates[0].y - uv_coordinates[2].y)
        )
        if abs(denominator) < 0.00000001:
            continue
        minimum_x = max(
            0,
            int(math.floor(min(uv.x for uv in uv_coordinates) * width)),
        )
        maximum_x = min(
            width - 1,
            int(math.ceil(max(uv.x for uv in uv_coordinates) * width)),
        )
        minimum_y = max(
            0,
            int(math.floor(min(uv.y for uv in uv_coordinates) * height)),
        )
        maximum_y = min(
            height - 1,
            int(math.ceil(max(uv.y for uv in uv_coordinates) * height)),
        )
        triangle_positions = [world_vertices[index] for index in vertex_indices]
        triangle_normal = (normal_matrix @ triangle.normal).normalized()
        for y in range(minimum_y, maximum_y + 1):
            uv_y = (y + 0.5) / height
            for x in range(minimum_x, maximum_x + 1):
                uv_x = (x + 0.5) / width
                first = (
                    (uv_coordinates[1].y - uv_coordinates[2].y)
                    * (uv_x - uv_coordinates[2].x)
                    + (uv_coordinates[2].x - uv_coordinates[1].x)
                    * (uv_y - uv_coordinates[2].y)
                ) / denominator
                second = (
                    (uv_coordinates[2].y - uv_coordinates[0].y)
                    * (uv_x - uv_coordinates[2].x)
                    + (uv_coordinates[0].x - uv_coordinates[2].x)
                    * (uv_y - uv_coordinates[2].y)
                ) / denominator
                third = 1.0 - first - second
                if min(first, second, third) < -0.00001:
                    continue
                index = y * width + x
                positions[index] = (
                    triangle_positions[0] * first
                    + triangle_positions[1] * second
                    + triangle_positions[2] * third
                )
                normals[index] = triangle_normal.copy()
                owners[index] = (owner_pointer, triangle.polygon_index)
    return positions, normals, owners


def tm_evaluate_approx_ao(
    layer,
    spatial_tree,
    sample_owners,
    target_positions,
    target_normals,
    target_owners,
    smooth_neighbors,
):
    mask = array('f', [0.0]) * len(target_positions)
    distance_limit = layer.ao_approx_distance
    minimum_distance = max(0.00001, distance_limit * 0.002)
    for target_index, target_position in enumerate(target_positions):
        if target_position is None:
            continue
        target_normal = target_normals[target_index]
        target_owner = target_owners[target_index]
        contribution_total = 0.0
        strongest_contribution = 0.0
        accepted = 0
        nearest_by_surface = {}
        for hit in spatial_tree.find_nearest_range(
            target_position,
            distance_limit,
        ):
            sample_position, sample_normal, sample_index, sample_distance = hit
            if sample_distance <= minimum_distance or sample_distance > distance_limit:
                continue
            if sample_owners[sample_index] == target_owner:
                continue
            sample_owner = sample_owners[sample_index]
            if sample_owner[0] == target_owner[0]:
                polygon_pair = (
                    min(sample_owner[1], target_owner[1]),
                    max(sample_owner[1], target_owner[1]),
                )
                if polygon_pair in smooth_neighbors:
                    continue
            previous_hit = nearest_by_surface.get(sample_owner)
            if previous_hit is None or sample_distance < previous_hit[3]:
                nearest_by_surface[sample_owner] = hit

        nearest_hits = sorted(
            nearest_by_surface.values(),
            key=lambda item: item[3],
        )[:layer.ao_approx_neighbors]
        for sample_position, sample_normal, _sample_index, sample_distance in nearest_hits:
            sample_owner = sample_owners[_sample_index]
            direction = sample_position - target_position
            direction /= sample_distance
            hemisphere = target_normal.dot(direction)
            if hemisphere <= 0.01:
                continue
            if sample_owner[0] == target_owner[0]:
                normal_alignment = target_normal.dot(sample_normal)
                if normal_alignment > 0.866 and hemisphere < 0.35:
                    continue
            distance_weight = max(0.0, 1.0 - sample_distance / distance_limit)
            distance_weight **= layer.ao_approx_falloff
            facing = abs(sample_normal.dot(direction))
            contribution = (
                math.sqrt(hemisphere)
                * distance_weight
                * (0.35 + 0.65 * facing)
            )
            contribution_total += contribution
            strongest_contribution = max(strongest_contribution, contribution)
            accepted += 1
            if accepted >= layer.ao_approx_neighbors:
                break
        if accepted:
            average_contribution = contribution_total / accepted
            mask[target_index] = min(
                1.0,
                (
                    strongest_contribution * 0.45
                    + average_contribution * 0.85
                )
                * layer.ao_approx_strength,
            )
    return mask


def tm_smooth_approx_ao(
    mask,
    positions,
    normals,
    owners,
    smooth_neighbors,
    width,
    height,
    iterations,
):
    for _iteration in range(iterations):
        source = mask
        result = array('f', source)
        for y in range(height):
            for x in range(width):
                index = y * width + x
                if positions[index] is None:
                    continue
                total = 0.0
                total_weight = 0.0
                center_normal = normals[index]
                center_owner = owners[index]
                for offset_y in (-1, 0, 1):
                    neighbor_y = y + offset_y
                    if neighbor_y < 0 or neighbor_y >= height:
                        continue
                    for offset_x in (-1, 0, 1):
                        neighbor_x = x + offset_x
                        if neighbor_x < 0 or neighbor_x >= width:
                            continue
                        neighbor_index = neighbor_y * width + neighbor_x
                        if positions[neighbor_index] is None:
                            continue
                        neighbor_owner = owners[neighbor_index]
                        if neighbor_owner != center_owner:
                            polygon_pair = (
                                min(center_owner[1], neighbor_owner[1]),
                                max(center_owner[1], neighbor_owner[1]),
                            )
                            if polygon_pair not in smooth_neighbors:
                                continue
                        normal_alignment = max(
                            0.0,
                            center_normal.dot(normals[neighbor_index]),
                        )
                        if normal_alignment < 0.35:
                            continue
                        spatial_weight = 1.0 / (
                            1.0 + offset_x * offset_x + offset_y * offset_y
                        )
                        weight = spatial_weight * normal_alignment ** 4
                        total += source[neighbor_index] * weight
                        total_weight += weight
                if total_weight > 0.0:
                    result[index] = total / total_weight
        mask = result
    return mask


def tm_create_topology_ao_mesh(obj, subdivisions):
    temporary_mesh = bpy.data.meshes.new("__TM_APPROX_AO_TOPOLOGY__")
    bmesh_data = bmesh.new()
    try:
        bmesh_data.from_mesh(obj.data)
        for _level in range(subdivisions):
            if len(bmesh_data.verts) * 4 > 250000:
                break
            bmesh.ops.subdivide_edges(
                bmesh_data,
                edges=list(bmesh_data.edges),
                cuts=1,
                use_grid_fill=True,
            )
        bmesh_data.to_mesh(temporary_mesh)
        temporary_mesh.update()
        temporary_mesh.calc_loop_triangles()
        return temporary_mesh
    except Exception:
        bpy.data.meshes.remove(temporary_mesh)
        raise
    finally:
        bmesh_data.free()


def tm_topology_concavity(mesh):
    values = array('f', [0.0]) * len(mesh.vertices)
    bmesh_data = bmesh.new()
    try:
        bmesh_data.from_mesh(mesh)
        bmesh_data.normal_update()
        bmesh_data.verts.ensure_lookup_table()
        bmesh_data.verts.index_update()
        minimum_angle = math.radians(30.0)
        angle_range = max(0.001, math.radians(90.0) - minimum_angle)
        for edge in bmesh_data.edges:
            if not edge.is_manifold or edge.is_convex:
                continue
            angle = edge.calc_face_angle(0.0)
            if angle <= minimum_angle:
                continue
            strength = min(1.0, (angle - minimum_angle) / angle_range)
            for vertex in edge.verts:
                values[vertex.index] = max(values[vertex.index], strength)
    finally:
        bmesh_data.free()
    return values


def tm_topology_vertex_neighbors(mesh):
    neighbors = [set() for _vertex in mesh.vertices]
    for edge in mesh.edges:
        first, second = edge.vertices
        neighbors[first].add(second)
        neighbors[second].add(first)
    return neighbors


def tm_smooth_topology_values(values, neighbors, iterations):
    for _iteration in range(iterations):
        source = values
        result = array('f', source)
        for index, connected in enumerate(neighbors):
            if not connected:
                continue
            average = sum(source[neighbor] for neighbor in connected) / len(connected)
            result[index] = source[index] * 0.55 + average * 0.45
        values = result
    return values


def tm_evaluate_topology_ao(
    obj,
    mesh,
    layer,
    spatial_tree,
    sample_owners,
):
    matrix = obj.matrix_world
    normal_matrix = matrix.to_3x3().inverted().transposed()
    world_positions = [matrix @ vertex.co for vertex in mesh.vertices]
    world_normals = [
        (normal_matrix @ vertex.normal).normalized()
        for vertex in mesh.vertices
    ]
    incident_polygons = [set() for _vertex in mesh.vertices]
    for polygon in mesh.polygons:
        for vertex_index in polygon.vertices:
            incident_polygons[vertex_index].add(polygon.index)

    proximity_values = array('f', [0.0]) * len(mesh.vertices)
    distance_limit = layer.ao_approx_distance
    minimum_distance = max(0.00001, distance_limit * 0.002)
    owner_pointer = obj.as_pointer()
    for vertex_index, target_position in enumerate(world_positions):
        target_normal = world_normals[vertex_index]
        nearest_by_surface = {}
        for hit in spatial_tree.find_nearest_range(target_position, distance_limit):
            sample_position, sample_normal, sample_index, sample_distance = hit
            sample_owner = sample_owners[sample_index]
            if sample_distance <= minimum_distance:
                continue
            if (
                sample_owner[0] == owner_pointer
                and sample_owner[1] in incident_polygons[vertex_index]
            ):
                continue
            previous_hit = nearest_by_surface.get(sample_owner)
            if previous_hit is None or sample_distance < previous_hit[3]:
                nearest_by_surface[sample_owner] = hit

        nearest_hits = sorted(
            nearest_by_surface.values(),
            key=lambda item: item[3],
        )[:layer.ao_approx_neighbors]
        contribution_total = 0.0
        strongest_contribution = 0.0
        accepted = 0
        for sample_position, sample_normal, sample_index, sample_distance in nearest_hits:
            sample_owner = sample_owners[sample_index]
            direction = sample_position - target_position
            direction /= sample_distance
            hemisphere = target_normal.dot(direction)
            if hemisphere <= 0.01:
                continue
            if sample_owner[0] == owner_pointer:
                normal_alignment = target_normal.dot(sample_normal)
                if normal_alignment > 0.866 and hemisphere < 0.35:
                    continue
            distance_weight = max(0.0, 1.0 - sample_distance / distance_limit)
            distance_weight **= layer.ao_approx_falloff
            facing = abs(sample_normal.dot(direction))
            contribution = (
                math.sqrt(hemisphere)
                * distance_weight
                * (0.35 + 0.65 * facing)
            )
            contribution_total += contribution
            strongest_contribution = max(strongest_contribution, contribution)
            accepted += 1
        if accepted:
            average_contribution = contribution_total / accepted
            proximity_values[vertex_index] = min(
                1.0,
                strongest_contribution * 0.45
                + average_contribution * 0.85,
            )

    concavity_values = tm_topology_concavity(mesh)
    values = array('f', [0.0]) * len(mesh.vertices)
    for index in range(len(values)):
        combined = 1.0 - (
            (1.0 - proximity_values[index])
            * (1.0 - concavity_values[index])
        )
        values[index] = min(1.0, combined * layer.ao_approx_strength)
    return tm_smooth_topology_values(
        values,
        tm_topology_vertex_neighbors(mesh),
        layer.ao_approx_smooth,
    )


def tm_rasterize_topology_ao(mesh, uv_map_name, values, width, height):
    uv_layer = mesh.uv_layers.get(uv_map_name) or mesh.uv_layers.active
    if not uv_layer:
        raise RuntimeError("Could not access the selected UV map")
    mesh.calc_loop_triangles()
    mask = array('f', [0.0]) * (width * height)
    coverage = bytearray(width * height)
    for triangle in mesh.loop_triangles:
        uv_coordinates = [
            uv_layer.data[loop_index].uv.copy()
            for loop_index in triangle.loops
        ]
        denominator = (
            (uv_coordinates[1].y - uv_coordinates[2].y)
            * (uv_coordinates[0].x - uv_coordinates[2].x)
            + (uv_coordinates[2].x - uv_coordinates[1].x)
            * (uv_coordinates[0].y - uv_coordinates[2].y)
        )
        if abs(denominator) < 0.00000001:
            continue
        minimum_x = max(
            0,
            int(math.floor(min(uv.x for uv in uv_coordinates) * width)),
        )
        maximum_x = min(
            width - 1,
            int(math.ceil(max(uv.x for uv in uv_coordinates) * width)),
        )
        minimum_y = max(
            0,
            int(math.floor(min(uv.y for uv in uv_coordinates) * height)),
        )
        maximum_y = min(
            height - 1,
            int(math.ceil(max(uv.y for uv in uv_coordinates) * height)),
        )
        triangle_values = [values[index] for index in triangle.vertices]
        for y in range(minimum_y, maximum_y + 1):
            uv_y = (y + 0.5) / height
            for x in range(minimum_x, maximum_x + 1):
                uv_x = (x + 0.5) / width
                first = (
                    (uv_coordinates[1].y - uv_coordinates[2].y)
                    * (uv_x - uv_coordinates[2].x)
                    + (uv_coordinates[2].x - uv_coordinates[1].x)
                    * (uv_y - uv_coordinates[2].y)
                ) / denominator
                second = (
                    (uv_coordinates[2].y - uv_coordinates[0].y)
                    * (uv_x - uv_coordinates[2].x)
                    + (uv_coordinates[0].x - uv_coordinates[2].x)
                    * (uv_y - uv_coordinates[2].y)
                ) / denominator
                third = 1.0 - first - second
                if min(first, second, third) < -0.00001:
                    continue
                index = y * width + x
                mask[index] = (
                    triangle_values[0] * first
                    + triangle_values[1] * second
                    + triangle_values[2] * third
                )
                coverage[index] = 1
    return mask, coverage


def tm_dilate_approx_ao_mask(mask, coverage, width, height, iterations):
    coverage = bytearray(coverage)
    for _iteration in range(iterations):
        source_mask = mask
        source_coverage = coverage
        result_mask = array('f', source_mask)
        result_coverage = bytearray(source_coverage)
        changed = False
        for y in range(height):
            for x in range(width):
                index = y * width + x
                if source_coverage[index]:
                    continue
                total = 0.0
                count = 0
                for offset_y in (-1, 0, 1):
                    neighbor_y = y + offset_y
                    if neighbor_y < 0 or neighbor_y >= height:
                        continue
                    for offset_x in (-1, 0, 1):
                        neighbor_x = x + offset_x
                        if neighbor_x < 0 or neighbor_x >= width:
                            continue
                        neighbor_index = neighbor_y * width + neighbor_x
                        if not source_coverage[neighbor_index]:
                            continue
                        total += source_mask[neighbor_index]
                        count += 1
                if count:
                    result_mask[index] = total / count
                    result_coverage[index] = 1
                    changed = True
        mask = result_mask
        coverage = result_coverage
        if not changed:
            break
    return mask, coverage


def tm_write_approx_ao_image(image, mask, coverage, width, height):
    target_width, target_height = image.size
    if image.size[:] != (width, height):
        image.scale(width, height)
    pixels = array('f', [0.0]) * (width * height * 4)
    for index, is_covered in enumerate(coverage):
        if is_covered:
            pixels[index * 4 + 3] = mask[index]
    image.pixels.foreach_set(pixels)
    image.update()
    if (width, height) != (target_width, target_height):
        image.scale(target_width, target_height)
        image.update()


def paint_approximate_ao(context, obj, image, layer, margin):
    width = min(image.size[0], layer.ao_approx_resolution)
    height = min(image.size[1], layer.ao_approx_resolution)
    topology_mesh = tm_create_topology_ao_mesh(
        obj,
        layer.ao_approx_subdivision,
    )
    try:
        spatial_tree, sample_owners = tm_build_approx_ao_bvh(
            context,
            obj,
            layer,
            target_mesh=topology_mesh,
        )
        values = tm_evaluate_topology_ao(
            obj,
            topology_mesh,
            layer,
            spatial_tree,
            sample_owners,
        )
        mask, coverage = tm_rasterize_topology_ao(
            topology_mesh,
            layer.uv_map,
            values,
            width,
            height,
        )
        internal_margin = int(math.ceil(
            margin * width / max(1, image.size[0])
        ))
        mask, coverage = tm_dilate_approx_ao_mask(
            mask,
            coverage,
            width,
            height,
            internal_margin,
        )
        tm_write_approx_ao_image(
            image,
            mask,
            coverage,
            width,
            height,
        )
    finally:
        bpy.data.meshes.remove(topology_mesh)


def tm_composite_mask(image, mask):
    total = image.size[0] * image.size[1] * 4
    pixels = array('f', [0.0]) * total
    image.pixels.foreach_get(pixels)
    for mask_index, coverage in enumerate(mask):
        alpha = max(0.0, min(1.0, coverage))
        if alpha <= 0.000001:
            continue
        pixel_index = mask_index * 4
        pixels[pixel_index] = 0.0
        pixels[pixel_index + 1] = 0.0
        pixels[pixel_index + 2] = 0.0
        pixels[pixel_index + 3] = max(pixels[pixel_index + 3], alpha)
    image.pixels.foreach_set(pixels)
    image.update()


def tm_edge_marker_weight(mesh, edge_index, marker):
    if marker == 'SHARP':
        attribute = mesh.attributes.get('sharp_edge')
        if (
            attribute is not None
            and attribute.domain == 'EDGE'
            and edge_index < len(attribute.data)
        ):
            return 1.0 if attribute.data[edge_index].value else 0.0
        return 1.0 if getattr(mesh.edges[edge_index], 'use_edge_sharp', False) else 0.0

    attribute_name = TM_EDGE_MARKER_ATTRIBUTES.get(marker)
    if not attribute_name:
        return 0.0
    attribute = mesh.attributes.get(attribute_name)
    if (
        attribute is None
        or attribute.domain != 'EDGE'
        or edge_index >= len(attribute.data)
    ):
        return 0.0
    return 1.0 if float(attribute.data[edge_index].value) > 0.000001 else 0.0


def tm_mesh_has_edge_marker(mesh, marker):
    if marker == 'SHARP':
        attribute = mesh.attributes.get('sharp_edge')
        if attribute is not None and attribute.domain == 'EDGE':
            if any(bool(item.value) for item in attribute.data):
                return True
        return any(
            bool(getattr(edge, 'use_edge_sharp', False))
            for edge in mesh.edges
        )

    attribute_name = TM_EDGE_MARKER_ATTRIBUTES.get(marker)
    if not attribute_name:
        return False
    attribute = mesh.attributes.get(attribute_name)
    return bool(
        attribute is not None
        and attribute.domain == 'EDGE'
        and any(float(item.value) > 0.000001 for item in attribute.data)
    )


def tm_bmesh_edge_marker_weight(bmesh_data, edge, mesh, marker):
    attribute_name = TM_EDGE_MARKER_ATTRIBUTES.get(marker)
    if attribute_name:
        marker_layer = bmesh_data.edges.layers.float.get(attribute_name)
        if marker_layer is not None:
            return 1.0 if float(edge[marker_layer]) > 0.000001 else 0.0
    return tm_edge_marker_weight(mesh, edge.index, marker)


def tm_curvature_weight(edge, layer):
    if not edge.is_manifold or len(edge.link_faces) != 2:
        return 0.0
    if layer.curvature_mode == 'CONVEX' and not edge.is_convex:
        return 0.0
    if layer.curvature_mode == 'CONCAVE' and edge.is_convex:
        return 0.0
    angle = edge.calc_face_angle()
    if angle < layer.curvature_angle:
        return 0.0
    normalized = max(
        0.0,
        min(1.0, (angle - layer.curvature_angle) / max(0.001, layer.curvature_falloff)),
    )
    smooth_weight = normalized * normalized * (3.0 - 2.0 * normalized)
    weight = smooth_weight * smooth_weight
    return weight if weight >= 0.01 else 0.0


def paint_direct_edge_layer(context, obj, image, layer):
    width, height = image.size
    mask = array('f', [0.0]) * (width * height)
    source_mesh = obj.data
    evaluated_obj = None
    if (
        layer.layer_type == 'SHARP_EDGES'
        and not tm_mesh_has_edge_marker(source_mesh, layer.edge_marker)
    ):
        depsgraph = context.evaluated_depsgraph_get()
        evaluated_obj = obj.evaluated_get(depsgraph)
        evaluated_mesh = evaluated_obj.to_mesh(
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        if tm_mesh_has_edge_marker(evaluated_mesh, layer.edge_marker):
            source_mesh = evaluated_mesh
        else:
            evaluated_obj.to_mesh_clear()
            evaluated_obj = None

    bmesh_data = bmesh.new()
    try:
        bmesh_data.from_mesh(source_mesh)
        bmesh_data.normal_update()
        bmesh_data.edges.ensure_lookup_table()
        bmesh_data.edges.index_update()
        uv_layer = bmesh_data.loops.layers.uv.get(layer.uv_map)
        if uv_layer is None:
            uv_layer = bmesh_data.loops.layers.uv.active
        if not uv_layer:
            raise RuntimeError("Could not access the selected UV map")
        painted_keys = set()
        for face in bmesh_data.faces:
            for loop in face.loops:
                edge = loop.edge
                if layer.layer_type == 'SHARP_EDGES':
                    weight = tm_bmesh_edge_marker_weight(
                        bmesh_data,
                        edge,
                        source_mesh,
                        layer.edge_marker,
                    )
                    thickness = layer.sharp_thickness
                    opacity = layer.sharp_opacity
                    hardness = layer.sharp_hardness
                    irregularity = layer.sharp_irregularity
                else:
                    weight = tm_curvature_weight(edge, layer)
                    thickness = layer.curvature_thickness
                    opacity = layer.curvature_opacity
                    hardness = layer.curvature_hardness
                    irregularity = layer.curvature_irregularity
                if weight <= 0.0:
                    continue
                uv1 = loop[uv_layer].uv.copy()
                uv2 = loop.link_loop_next[uv_layer].uv.copy()
                clipped = tm_clip_uv_line(uv1.x, uv1.y, uv2.x, uv2.y)
                if clipped is None:
                    continue
                key = (
                    edge.index,
                    *sorted((
                        (round(uv1.x, 6), round(uv1.y, 6)),
                        (round(uv2.x, 6), round(uv2.y, 6)),
                    )),
                )
                if key in painted_keys:
                    continue
                painted_keys.add(key)
                x1, y1, x2, y2 = clipped
                tm_line_mask(
                    mask,
                    width,
                    height,
                    x1 * (width - 1),
                    y1 * (height - 1),
                    x2 * (width - 1),
                    y2 * (height - 1),
                    thickness,
                    opacity * weight,
                    hardness,
                    irregularity,
                    edge.index + face.index * 0.37,
                )
    finally:
        bmesh_data.free()
        if evaluated_obj is not None:
            evaluated_obj.to_mesh_clear()

    smooth = (
        layer.sharp_smooth
        if layer.layer_type == 'SHARP_EDGES'
        else layer.curvature_smooth
    )
    tm_smooth_mask(mask, width, height, smooth)
    tm_composite_mask(image, mask)


def tm_cubic_bezier_point(point_a, handle_a, handle_b, point_b, factor):
    inverse = 1.0 - factor
    return (
        point_a * (inverse ** 3)
        + handle_a * (3.0 * inverse * inverse * factor)
        + handle_b * (3.0 * inverse * factor * factor)
        + point_b * (factor ** 3)
    )


def tm_collect_curve_strokes_world(curve_obj):
    strokes = []
    curve = curve_obj.data
    matrix_world = curve_obj.matrix_world
    for spline in curve.splines:
        points = []
        if spline.type == 'BEZIER':
            bezier_points = spline.bezier_points
            point_count = len(bezier_points)
            if point_count < 2:
                continue
            segment_count = point_count if spline.use_cyclic_u else point_count - 1
            resolution = max(
                2,
                int(getattr(spline, "resolution_u", curve.resolution_u)),
            )
            for segment_index in range(segment_count):
                first = bezier_points[segment_index]
                second = bezier_points[(segment_index + 1) % point_count]
                for sample_index in range(resolution):
                    factor = sample_index / resolution
                    point = tm_cubic_bezier_point(
                        first.co,
                        first.handle_right,
                        second.handle_left,
                        second.co,
                        factor,
                    )
                    points.append(matrix_world @ point)
            endpoint = bezier_points[0 if spline.use_cyclic_u else -1].co
            points.append(matrix_world @ endpoint)
        else:
            for spline_point in spline.points:
                coordinate = Vector(spline_point.co[:3])
                weight = float(spline_point.co[3])
                if abs(weight) > 0.000001:
                    coordinate /= weight
                points.append(matrix_world @ coordinate)
            if spline.use_cyclic_u and len(points) > 2:
                points.append(points[0].copy())
        if len(points) >= 2:
            strokes.append(points)
    return strokes


def tm_collect_grease_pencil_strokes_world(grease_pencil_obj):
    strokes = []
    data = grease_pencil_obj.data
    matrix_world = grease_pencil_obj.matrix_world
    for grease_layer in getattr(data, "layers", ()):
        if getattr(grease_layer, "hide", False):
            continue
        frame = None
        current_frame = getattr(grease_layer, "current_frame", None)
        if callable(current_frame):
            frame = current_frame()
        if frame is None and getattr(grease_layer, "active_frame", None):
            frame = grease_layer.active_frame
        if frame is None and len(getattr(grease_layer, "frames", ())) > 0:
            frame = grease_layer.frames[-1]
        if frame is None:
            continue

        drawing = getattr(frame, "drawing", None)
        source_strokes = (
            getattr(drawing, "strokes", ())
            if drawing is not None
            else getattr(frame, "strokes", ())
        )
        for stroke in source_strokes:
            points = []
            for point in getattr(stroke, "points", ()):
                coordinate = getattr(point, "position", None)
                if coordinate is None:
                    coordinate = getattr(point, "co", None)
                if coordinate is not None:
                    points.append(matrix_world @ Vector(coordinate))
            if getattr(stroke, "cyclic", False) and len(points) > 2:
                points.append(points[0].copy())
            if len(points) >= 2:
                strokes.append(points)
    return strokes


def tm_collect_lineart_strokes_world(guide_object):
    if guide_object.type == 'CURVE':
        return tm_collect_curve_strokes_world(guide_object)
    if guide_object.type in {'GREASEPENCIL', 'GPENCIL'}:
        return tm_collect_grease_pencil_strokes_world(guide_object)
    return []


def tm_build_lineart_target(context, obj, uv_map_name):
    depsgraph = context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    mesh = evaluated_obj.to_mesh(
        preserve_all_data_layers=True,
        depsgraph=depsgraph,
    )
    if mesh is None:
        raise RuntimeError("Could not evaluate the target mesh")
    uv_layer = mesh.uv_layers.get(uv_map_name) or mesh.uv_layers.active
    if uv_layer is None:
        evaluated_obj.to_mesh_clear()
        raise RuntimeError("Could not access the selected UV map")
    mesh.calc_loop_triangles()
    triangles = list(mesh.loop_triangles)
    if not triangles:
        evaluated_obj.to_mesh_clear()
        raise RuntimeError("The target mesh has no faces")
    matrix_world = evaluated_obj.matrix_world
    vertices_world = [matrix_world @ vertex.co for vertex in mesh.vertices]
    polygons = [tuple(triangle.vertices) for triangle in triangles]
    bvh = BVHTree.FromPolygons(
        vertices_world,
        polygons,
        all_triangles=True,
    )
    return evaluated_obj, mesh, uv_layer, triangles, vertices_world, bvh


def tm_lineart_point_to_uv(
    point_world,
    mesh,
    uv_layer,
    triangles,
    vertices_world,
    bvh,
    projection_distance,
):
    hit_position, _normal, triangle_index, _distance = bvh.find_nearest(
        point_world,
        projection_distance,
    )
    if hit_position is None or triangle_index is None:
        return None
    triangle = triangles[triangle_index]
    point_a = vertices_world[triangle.vertices[0]]
    point_b = vertices_world[triangle.vertices[1]]
    point_c = vertices_world[triangle.vertices[2]]
    edge_ab = point_b - point_a
    edge_ac = point_c - point_a
    point_offset = hit_position - point_a
    dot_ab_ab = edge_ab.dot(edge_ab)
    dot_ab_ac = edge_ab.dot(edge_ac)
    dot_ac_ac = edge_ac.dot(edge_ac)
    dot_offset_ab = point_offset.dot(edge_ab)
    dot_offset_ac = point_offset.dot(edge_ac)
    denominator = dot_ab_ab * dot_ac_ac - dot_ab_ac * dot_ab_ac
    if abs(denominator) <= 0.000000000001:
        return None
    weight_b = (
        dot_ac_ac * dot_offset_ab - dot_ab_ac * dot_offset_ac
    ) / denominator
    weight_c = (
        dot_ab_ab * dot_offset_ac - dot_ab_ac * dot_offset_ab
    ) / denominator
    weight_a = 1.0 - weight_b - weight_c
    uv_a = uv_layer.data[triangle.loops[0]].uv
    uv_b = uv_layer.data[triangle.loops[1]].uv
    uv_c = uv_layer.data[triangle.loops[2]].uv
    return uv_a * weight_a + uv_b * weight_b + uv_c * weight_c


def tm_sample_lineart_segment(point_a, point_b, sample_step):
    distance = (point_b - point_a).length
    steps = max(1, int(math.ceil(distance / max(0.000001, sample_step))))
    return [point_a.lerp(point_b, index / steps) for index in range(steps + 1)]


def paint_lineart_layer(context, obj, image, layer):
    guide_object = layer.lineart_object
    if guide_object is None:
        raise RuntimeError("Select or create a Lineart guide")
    strokes = tm_collect_lineart_strokes_world(guide_object)
    if not strokes:
        raise RuntimeError("The Lineart guide has no strokes")

    width, height = image.size
    mask = array('f', [0.0]) * (width * height)
    evaluated_obj = None
    drawn_segments = 0
    try:
        (
            evaluated_obj,
            mesh,
            uv_layer,
            triangles,
            vertices_world,
            bvh,
        ) = tm_build_lineart_target(context, obj, layer.uv_map)
        for stroke_index, stroke in enumerate(strokes):
            for point_index in range(len(stroke) - 1):
                samples = tm_sample_lineart_segment(
                    stroke[point_index],
                    stroke[point_index + 1],
                    layer.lineart_sample_step,
                )
                projected = [
                    tm_lineart_point_to_uv(
                        sample,
                        mesh,
                        uv_layer,
                        triangles,
                        vertices_world,
                        bvh,
                        layer.lineart_projection_distance,
                    )
                    for sample in samples
                ]
                for sample_index in range(len(projected) - 1):
                    uv_a = projected[sample_index]
                    uv_b = projected[sample_index + 1]
                    if uv_a is None or uv_b is None:
                        continue
                    if (
                        layer.lineart_break_uv_seams
                        and (uv_b - uv_a).length > layer.lineart_uv_jump
                    ):
                        continue
                    clipped = tm_clip_uv_line(
                        uv_a.x,
                        uv_a.y,
                        uv_b.x,
                        uv_b.y,
                    )
                    if clipped is None:
                        continue
                    x1, y1, x2, y2 = clipped
                    tm_line_mask(
                        mask,
                        width,
                        height,
                        x1 * (width - 1),
                        y1 * (height - 1),
                        x2 * (width - 1),
                        y2 * (height - 1),
                        layer.lineart_thickness,
                        layer.lineart_opacity,
                        layer.lineart_hardness,
                        0.0,
                        stroke_index * 100003 + point_index * 101 + sample_index,
                    )
                    drawn_segments += 1
    finally:
        if evaluated_obj is not None:
            evaluated_obj.to_mesh_clear()

    if drawn_segments == 0:
        raise RuntimeError(
            "No strokes reached the mesh; increase Projection Distance"
        )
    tm_smooth_mask(mask, width, height, layer.lineart_smooth)
    tm_composite_mask(image, mask)
    return drawn_segments


def tm_threshold_image(image, threshold):
    total = image.size[0] * image.size[1] * 4
    pixels = array('f', [0.0]) * total
    image.pixels.foreach_get(pixels)
    threshold = max(0.0, min(1.0, threshold))
    for index in range(0, total, 4):
        value = (pixels[index] + pixels[index + 1] + pixels[index + 2]) / 3.0
        output = 1.0 if value >= threshold else 0.0
        pixels[index] = output
        pixels[index + 1] = output
        pixels[index + 2] = output
    image.pixels.foreach_set(pixels)
    image.update()


def tm_make_dark_mask(image):
    total = image.size[0] * image.size[1] * 4
    pixels = array('f', [0.0]) * total
    image.pixels.foreach_get(pixels)
    for index in range(0, total, 4):
        luminance = max(
            0.0,
            min(
                1.0,
                pixels[index] * 0.2126
                + pixels[index + 1] * 0.7152
                + pixels[index + 2] * 0.0722,
            ),
        )
        pixels[index] = 0.0
        pixels[index + 1] = 0.0
        pixels[index + 2] = 0.0
        pixels[index + 3] = 1.0 - luminance
    image.pixels.foreach_set(pixels)
    image.update()


def get_active_color_attribute_name(obj):
    attributes = getattr(obj.data, "color_attributes", None)
    if not attributes:
        return None
    try:
        if attributes.active_color:
            return attributes.active_color.name
    except Exception:
        pass
    return attributes[0].name if len(attributes) else None


def create_override_bake_material(obj, image, layer_type):
    original_materials = [slot.material for slot in obj.material_slots]
    original_slot_count = len(obj.material_slots)
    material = bpy.data.materials.new("__TM_OVERRIDE_BAKE__")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.name = "__TM_TEMP_BAKE_TARGET__"
    image_node.image = image
    image_node.select = True
    nodes.active = image_node

    if layer_type == 'COLOR_ATTRIBUTE':
        attribute_name = get_active_color_attribute_name(obj)
        if not attribute_name:
            bpy.data.materials.remove(material)
            raise RuntimeError("The object needs an active Color Attribute")
        attribute = nodes.new("ShaderNodeVertexColor")
        attribute.layer_name = attribute_name
        emission = nodes.new("ShaderNodeEmission")
        links.new(attribute.outputs["Color"], emission.inputs["Color"])
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
    else:
        diffuse = nodes.new("ShaderNodeBsdfDiffuse")
        diffuse.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        diffuse.inputs["Roughness"].default_value = 0.5
        links.new(diffuse.outputs["BSDF"], output.inputs["Surface"])

    if not obj.material_slots:
        obj.data.materials.append(material)
    else:
        for slot in obj.material_slots:
            slot.material = material
    return material, original_materials, original_slot_count


def remove_override_bake_material(obj, override_data):
    if not override_data:
        return
    material, original_materials, original_slot_count = override_data
    while len(obj.material_slots) > original_slot_count:
        obj.data.materials.pop(index=len(obj.material_slots) - 1)
    for index, original in enumerate(original_materials):
        if index < len(obj.material_slots):
            obj.material_slots[index].material = original
    if material and material.name in bpy.data.materials:
        bpy.data.materials.remove(material, do_unlink=True)


def tm_object_is_in_view_layer(context, obj):
    return bool(
        obj
        and context.view_layer.objects.get(obj.name) == obj
    )


def tm_select_bake_pair(context, low_poly, high_poly):
    tm_select_only_object(context, low_poly)
    high_poly.select_set(True)


def tm_vertex_group_face_candidates(obj, mesh, names):
    """Return the matching groups that fully contain each authored face."""
    indices = {obj.vertex_groups[name].index: name for name in names}
    memberships = [
        {indices[group.group] for group in vertex.groups
         if group.group in indices and group.weight > 0.0}
        for vertex in mesh.vertices
    ]
    candidates = []
    raw_regions = {name: set() for name in names}
    for face in mesh.polygons:
        matches = set.intersection(*(memberships[index] for index in face.vertices))
        candidates.append(matches)
        for name in matches:
            raw_regions[name].add(face.index)
    return candidates, raw_regions


def tm_vertex_group_face_regions(obj, mesh, names):
    """Resolve overlapping groups using their borders and local mesh distance."""
    candidates, raw_regions = tm_vertex_group_face_candidates(obj, mesh, names)
    regions = {name: set() for name in names}
    regions[None] = set()

    # A smaller group wholly inside a broader one is the more specific region.
    # This is common with head/ear and body/hand deformation groups.
    resolved_candidates = []
    for matches in candidates:
        specific = set(matches)
        for broad in matches:
            if any(
                narrow != broad
                and raw_regions[narrow]
                and raw_regions[narrow] < raw_regions[broad]
                for narrow in matches
            ):
                specific.discard(broad)
        resolved_candidates.append(specific)

    # Build face adjacency. Crossing an edge costs the average distance between
    # face centers, producing a geometric midpoint through an overlapping band.
    edge_faces = {}
    for face in mesh.polygons:
        for edge_key in face.edge_keys:
            edge_faces.setdefault(edge_key, []).append(face.index)
    adjacency = [set() for _face in mesh.polygons]
    for face_indices in edge_faces.values():
        for first in face_indices:
            adjacency[first].update(second for second in face_indices if second != first)

    overlap_names = set().union(*(
        matches for matches in resolved_candidates if len(matches) > 1
    )) if resolved_candidates else set()
    distances = {}
    for name in overlap_names:
        seeds = [
            face_index
            for face_index, matches in enumerate(resolved_candidates)
            if matches == {name}
        ]
        group_distances = [float('inf')] * len(mesh.polygons)
        queue = []
        for face_index in seeds:
            group_distances[face_index] = 0.0
            heapq.heappush(queue, (0.0, face_index))
        while queue:
            distance, face_index = heapq.heappop(queue)
            if distance != group_distances[face_index]:
                continue
            for neighbor in adjacency[face_index]:
                if neighbor not in raw_regions[name]:
                    continue
                step = (
                    mesh.polygons[face_index].center
                    - mesh.polygons[neighbor].center
                ).length
                candidate_distance = distance + max(step, 1.0e-8)
                if candidate_distance < group_distances[neighbor]:
                    group_distances[neighbor] = candidate_distance
                    heapq.heappush(queue, (candidate_distance, neighbor))
        distances[name] = group_distances

    group_areas = {
        name: sum(mesh.polygons[index].area for index in face_indices)
        for name, face_indices in raw_regions.items()
    }
    for face_index, matches in enumerate(resolved_candidates):
        if not matches:
            regions[None].add(face_index)
            continue
        if len(matches) == 1:
            regions[next(iter(matches))].add(face_index)
            continue
        # The nearest non-overlapping interior wins. Area and name provide a
        # stable fallback for identical groups or groups with no exclusive core.
        name = min(
            matches,
            key=lambda candidate: (
                distances[candidate][face_index],
                group_areas[candidate],
                candidate,
            ),
        )
        regions[name].add(face_index)
    return regions


def tm_evaluated_vertex_group_regions(context, obj, names):
    # Classify the authored faces before modifiers interpolate vertex weights.
    # Otherwise Subdivision makes positive weights leak across region borders.
    regions = tm_vertex_group_face_regions(obj, obj.data, names)
    mesh = obj.data.copy()
    temporary = None
    evaluated_mesh = None
    try:
        attribute = mesh.attributes.new(name="__TM_BAKE_REGION__", type='INT', domain='FACE')
        attribute_name = attribute.name
        for region_id, name in enumerate(names, 1):
            for face_index in regions[name]:
                attribute.data[face_index].value = region_id
        temporary = obj.copy()
        temporary.data = mesh
        # The snapshots are evaluated through a viewport depsgraph, so mirror
        # render visibility/detail for the standard bake modifiers on the copy.
        for modifier in temporary.modifiers:
            modifier.show_viewport = modifier.show_render
            if modifier.type in {'SUBSURF', 'MULTIRES'}:
                modifier.levels = modifier.render_levels
        context.scene.collection.objects.link(temporary)
        depsgraph = context.evaluated_depsgraph_get()
        evaluated_mesh = bpy.data.meshes.new_from_object(
            temporary.evaluated_get(depsgraph), preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        evaluated_attribute = evaluated_mesh.attributes.get(attribute_name)
        if evaluated_attribute is None:
            raise RuntimeError(f"Modifiers on '{obj.name}' removed the bake region attribute")
        evaluated_regions = {name: set() for name in names}
        evaluated_regions[None] = set()
        for face_index, item in enumerate(evaluated_attribute.data):
            name = names[item.value - 1] if 1 <= item.value <= len(names) else None
            evaluated_regions[name].add(face_index)
        evaluated_mesh.attributes.remove(evaluated_attribute)
        return evaluated_mesh, evaluated_regions
    except Exception:
        if evaluated_mesh:
            bpy.data.meshes.remove(evaluated_mesh)
        raise
    finally:
        if temporary:
            bpy.data.objects.remove(temporary, do_unlink=True)
        bpy.data.meshes.remove(mesh)


def tm_prepare_vertex_group_bake(context, low_poly, high_poly, layer):
    common_names = sorted(
        set(low_poly.vertex_groups.keys()) & set(high_poly.vertex_groups.keys())
    )
    if not common_names:
        return None
    # A same-name pair is usable only if both objects contain at least one
    # complete face. One-sided, empty, or partial-only groups are simply ignored.
    _low_candidates, low_raw = tm_vertex_group_face_candidates(
        low_poly, low_poly.data, common_names,
    )
    _high_candidates, high_raw = tm_vertex_group_face_candidates(
        high_poly, high_poly.data, common_names,
    )
    names = [
        name for name in common_names
        if low_raw[name] and high_raw[name]
    ]
    if not names:
        return None
    meshes = []
    try:
        low_mesh, low_regions = tm_evaluated_vertex_group_regions(context, low_poly, names)
        meshes.append(low_mesh)
        high_mesh, high_regions = tm_evaluated_vertex_group_regions(context, high_poly, names)
        meshes.append(high_mesh)
        matched_jobs = []
        fallback_faces = set(low_regions[None])
        for name in names:
            if not low_regions[name]:
                continue
            if not high_regions[name]:
                fallback_faces.update(low_regions[name])
                continue
            matched_jobs.append((name, low_regions[name], high_regions[name]))
        if not matched_jobs:
            for mesh in meshes:
                bpy.data.meshes.remove(mesh)
            return None
        # Unassigned destinations, including a pair eliminated by overlap
        # resolution on the source only, retain the ordinary full-source bake.
        jobs = ([(None, fallback_faces, None)] if fallback_faces else []) + matched_jobs
        if not low_mesh.uv_layers.get(layer.uv_map):
            raise RuntimeError("The evaluated low-poly mesh is missing the bake UV map")
        if layer.bake_use_cage and layer.bake_cage_object:
            cage_mesh, _regions = tm_evaluated_vertex_group_regions(
                context, layer.bake_cage_object, [],
            )
            try:
                if (
                    len(cage_mesh.vertices) != len(low_mesh.vertices)
                    or len(cage_mesh.polygons) != len(low_mesh.polygons)
                    or any(tuple(a.vertices) != tuple(b.vertices)
                           for a, b in zip(cage_mesh.polygons, low_mesh.polygons))
                ):
                    raise RuntimeError("Cage must have the same evaluated topology as the low poly")
            finally:
                bpy.data.meshes.remove(cage_mesh)
        return {"low_mesh": low_mesh, "high_mesh": high_mesh, "jobs": jobs}
    except Exception:
        for mesh in meshes:
            bpy.data.meshes.remove(mesh)
        raise


def tm_remove_vertex_group_bake(data):
    if data:
        bpy.data.meshes.remove(data["low_mesh"])
        bpy.data.meshes.remove(data["high_mesh"])


def tm_filter_bake_source(mesh, face_indices):
    if face_indices is None:
        return
    # Deleting adjacent faces otherwise changes smooth normals along group borders.
    normals = mesh.attributes.new(name="__TM_BAKE_NORMAL__", type='FLOAT_VECTOR', domain='CORNER')
    values = array('f', [0.0]) * (len(mesh.loops) * 3)
    mesh.corner_normals.foreach_get("vector", values)
    normals.data.foreach_set("vector", values)
    attribute_name = normals.name
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(
            bm, geom=[face for face in bm.faces if face.index not in face_indices],
            context='FACES_ONLY',
        )
        bm.to_mesh(mesh)
    finally:
        bm.free()
    saved = mesh.attributes[attribute_name]
    corner_normals = [item.vector[:] for item in saved.data]
    mesh.attributes.remove(saved)
    mesh.normals_split_custom_set(corner_normals)
    mesh.update()


def tm_bake_vertex_group_regions(context, low_poly, high_poly, layer, image,
                                 bake_type, data, override_material=None):
    bake = context.scene.render.bake
    original_margin = bake.margin
    original_clear = bake.use_clear
    original_hidden = [(obj, obj.hide_render) for obj in (low_poly, high_poly)]
    # Resolve object-linked material slots and any temporary generation override.
    for obj, mesh in ((low_poly, data["low_mesh"]), (high_poly, data["high_mesh"])):
        for index, slot in enumerate(obj.material_slots):
            if index < len(mesh.materials):
                mesh.materials[index] = slot.material
        if obj == high_poly and override_material:
            for index in range(len(mesh.materials)):
                mesh.materials[index] = override_material
    try:
        for obj, _hidden in original_hidden:
            obj.hide_render = True
        bake.use_clear = False
        # First create padding. Then repaint ALL interiors without padding so a
        # region's margin cannot overwrite a neighbouring region's valid texels.
        margins = (original_margin, 0) if original_margin else (0,)
        for margin in margins:
            bake.margin = margin
            for name, low_faces, high_faces in data["jobs"]:
                temporary_objects = []
                temporary_meshes = []
                target_nodes, active_nodes = [], {}
                try:
                    for original, template in ((low_poly, data["low_mesh"]),
                                               (high_poly, data["high_mesh"])):
                        mesh = template.copy()
                        temporary_meshes.append(mesh)
                        obj = bpy.data.objects.new("__TM_GROUP_BAKE__", mesh)
                        temporary_objects.append(obj)
                        obj.matrix_world = original.matrix_world.copy()
                        obj.color = original.color
                        for vertex_group in original.vertex_groups:
                            obj.vertex_groups.new(name=vertex_group.name)
                        context.scene.collection.objects.link(obj)
                    target, source = temporary_objects
                    # Keep target topology/normals intact, including custom-cage
                    # correspondence. Only the selected region has bakeable UVs.
                    uv = target.data.uv_layers[layer.uv_map]
                    target.data.uv_layers.active = uv
                    uv.active_render = True
                    for face in target.data.polygons:
                        if face.index not in low_faces:
                            for loop_index in face.loop_indices:
                                uv.data[loop_index].uv = (-2.0, -2.0)
                    target.data.update()
                    tm_filter_bake_source(source.data, high_faces)
                    target_nodes, active_nodes = create_temp_bake_nodes(target, image)
                    if not target_nodes:
                        raise RuntimeError("The evaluated low poly needs a material with a bake target")
                    tm_select_bake_pair(context, target, source)
                    result = bpy.ops.object.bake(type=bake_type, uv_layer=layer.uv_map)
                    if 'FINISHED' not in result:
                        raise RuntimeError(f"Bake failed for region '{name or 'Ungrouped'}'")
                finally:
                    remove_temp_bake_nodes(target_nodes, active_nodes)
                    for obj in temporary_objects:
                        bpy.data.objects.remove(obj, do_unlink=True)
                    for mesh in temporary_meshes:
                        bpy.data.meshes.remove(mesh)
    finally:
        for obj, hidden in original_hidden:
            obj.hide_render = hidden
        bake.margin = original_margin
        bake.use_clear = original_clear
        tm_select_only_object(context, low_poly)


def tm_select_only_object(context, obj):
    for selected_object in tuple(context.selected_objects):
        selected_object.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def tm_restore_object_selection(context, selected_objects, active_object):
    for selected_object in tuple(context.selected_objects):
        try:
            selected_object.select_set(False)
        except Exception:
            pass
    for selected_object in selected_objects:
        if tm_object_is_in_view_layer(context, selected_object):
            try:
                selected_object.select_set(True)
            except Exception:
                pass
    if tm_object_is_in_view_layer(context, active_object):
        context.view_layer.objects.active = active_object


def save_shadow_visibility():
    return [
        (obj.name, obj.visible_shadow)
        for obj in bpy.data.objects
        if hasattr(obj, "visible_shadow")
    ]


def set_shadow_visibility(enabled):
    for obj in bpy.data.objects:
        if hasattr(obj, "visible_shadow"):
            obj.visible_shadow = enabled


def restore_shadow_visibility(values):
    for name, value in values:
        obj = bpy.data.objects.get(name)
        if obj and hasattr(obj, "visible_shadow"):
            obj.visible_shadow = value


def ensure_layer_image(material, layer, resolution):
    if layer.image:
        layer.image.use_fake_user = True
        return layer.image

    image = bpy.data.images.new(
        name=f"TM_{material.name}_{layer.name}",
        width=resolution,
        height=resolution,
        alpha=True,
    )
    image.use_fake_user = True
    image.generated_color = (0.0, 0.0, 0.0, 0.0)
    if layer.blend_mode != 'HEIGHT' and layer.layer_type in {
        'AO',
        'NORMAL',
        'SHADOW',
        'HARD_SHADOW',
        'TOON',
        'SHARP_EDGES',
        'CURVATURE',
        'LINEART',
    }:
        try:
            image.colorspace_settings.name = 'Non-Color'
        except Exception:
            pass
    layer.image = image
    return image


def configure_bake_type(scene, layer_type):
    bake = scene.render.bake
    if layer_type == 'ALBEDO':
        bake.use_pass_direct = False
        bake.use_pass_indirect = False
        bake.use_pass_color = True
        return 'DIFFUSE'
    if layer_type in {'SHADOW', 'HARD_SHADOW'}:
        bake.use_pass_direct = True
        bake.use_pass_indirect = True
        bake.use_pass_color = False
        return 'DIFFUSE'
    if layer_type == 'TOON':
        bake.use_pass_direct = True
        bake.use_pass_indirect = False
        bake.use_pass_color = False
        return 'DIFFUSE'
    if layer_type == 'COLOR_ATTRIBUTE':
        return 'EMIT'
    return layer_type


def create_temp_bake_nodes(obj, image, excluded_materials=()):
    temp_nodes = []
    active_nodes = {}
    materials = []
    excluded_materials = set(excluded_materials)
    for slot in obj.material_slots:
        material = slot.material
        if (
            not material
            or material in materials
            or material in excluded_materials
        ):
            continue
        materials.append(material)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        active_nodes[material] = nodes.active
        node = nodes.new("ShaderNodeTexImage")
        node.name = "__TM_TEMP_BAKE_TARGET__"
        node.image = image
        node.select = True
        nodes.active = node
        temp_nodes.append((material, node))
    return temp_nodes, active_nodes


def remove_temp_bake_nodes(temp_nodes, active_nodes):
    for material, node in temp_nodes:
        if not material or not material.node_tree or not node:
            continue
        existing_node = material.node_tree.nodes.get(node.name)
        if existing_node and existing_node.as_pointer() == node.as_pointer():
            material.node_tree.nodes.remove(existing_node)
    for material, node in active_nodes.items():
        if not material or not material.node_tree or not node:
            continue
        existing_node = material.node_tree.nodes.get(node.name)
        if existing_node and existing_node.as_pointer() == node.as_pointer():
            material.node_tree.nodes.active = existing_node


def tm_find_material_alpha_input(material):
    if not material or not material.use_nodes or not material.node_tree:
        return None
    nodes = material.node_tree.nodes
    shader = nodes.get("TM Principled BSDF")
    if shader and shader.bl_idname == "ShaderNodeBsdfPrincipled":
        return shader.inputs.get("Alpha")

    alpha_mix = nodes.get("TM Alpha Mix")
    if alpha_mix and alpha_mix.bl_idname == "ShaderNodeMixShader":
        return alpha_mix.inputs[0]

    output = next(
        (
            node
            for node in nodes
            if node.bl_idname == "ShaderNodeOutputMaterial"
            and node.is_active_output
        ),
        None,
    )
    if output and output.inputs["Surface"].is_linked:
        source_node = output.inputs["Surface"].links[0].from_node
        if source_node.bl_idname == "ShaderNodeBsdfPrincipled":
            return source_node.inputs.get("Alpha")

    shader = next(
        (
            node
            for node in nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        ),
        None,
    )
    return shader.inputs.get("Alpha") if shader else None


def tm_find_material_principled_input(material, input_name):
    if not material or not material.use_nodes or not material.node_tree:
        return None
    nodes = material.node_tree.nodes
    shader = nodes.get("TM Principled BSDF")
    if shader and shader.bl_idname == "ShaderNodeBsdfPrincipled":
        return shader.inputs.get(input_name)

    output = next(
        (
            node
            for node in nodes
            if node.bl_idname == "ShaderNodeOutputMaterial"
            and node.is_active_output
        ),
        None,
    )
    if output and output.inputs["Surface"].is_linked:
        source_node = output.inputs["Surface"].links[0].from_node
        if source_node.bl_idname == "ShaderNodeBsdfPrincipled":
            return source_node.inputs.get(input_name)

    shader = next(
        (
            node
            for node in nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        ),
        None,
    )
    return shader.inputs.get(input_name) if shader else None


def tm_data_channel_source(material, channel):
    if channel == 'ALPHA':
        input_socket = tm_find_material_alpha_input(material)
        default_value = 1.0
    elif channel == 'METALNESS':
        input_socket = tm_find_material_principled_input(material, "Metallic")
        default_value = 0.0
    elif channel == 'HEIGHT':
        node = material.node_tree.nodes.get("TM Height Bake Output")
        if node:
            return node.outputs[0], 0.5
        return None, 0.5
    else:
        raise RuntimeError(f"Unsupported data bake channel: {channel}")

    if input_socket and input_socket.is_linked:
        return input_socket.links[0].from_socket, default_value
    if input_socket:
        return None, float(input_socket.default_value)
    return None, default_value


def tm_create_data_channel_bake_overrides(
    obj,
    image,
    channel,
    excluded_materials=(),
):
    records = []
    materials = []
    excluded_materials = set(excluded_materials)
    for slot in obj.material_slots:
        material = slot.material
        if not material or material in materials or material in excluded_materials:
            continue
        materials.append(material)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        output = next(
            (
                node
                for node in nodes
                if node.bl_idname == "ShaderNodeOutputMaterial"
                and node.is_active_output
            ),
            None,
        )
        if not output:
            output = nodes.new("ShaderNodeOutputMaterial")

        previous_active = nodes.active
        previous_surface_socket = (
            output.inputs["Surface"].links[0].from_socket
            if output.inputs["Surface"].is_linked
            else None
        )
        target = nodes.new("ShaderNodeTexImage")
        target.name = f"__TM_{channel}_TARGET__"
        target.image = image
        target.select = True
        nodes.active = target

        emission = nodes.new("ShaderNodeEmission")
        emission.name = f"__TM_{channel}_EMISSION__"
        emission.inputs["Strength"].default_value = 1.0
        source_socket, default_value = tm_data_channel_source(material, channel)
        if source_socket:
            links.new(source_socket, emission.inputs["Color"])
        else:
            emission.inputs["Color"].default_value = (
                default_value,
                default_value,
                default_value,
                1.0,
            )
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
        records.append(
            (
                material,
                target,
                emission,
                output,
                previous_surface_socket,
                previous_active,
            )
        )
    return records


def tm_create_alpha_bake_overrides(obj, image):
    records = []
    materials = []
    for slot in obj.material_slots:
        material = slot.material
        if not material or material in materials:
            continue
        materials.append(material)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        output = next(
            (
                node
                for node in nodes
                if node.bl_idname == "ShaderNodeOutputMaterial"
                and node.is_active_output
            ),
            None,
        )
        if not output:
            output = nodes.new("ShaderNodeOutputMaterial")

        previous_active = nodes.active
        previous_surface_socket = (
            output.inputs["Surface"].links[0].from_socket
            if output.inputs["Surface"].is_linked
            else None
        )
        target = nodes.new("ShaderNodeTexImage")
        target.name = "__TM_ALPHA_COVERAGE_TARGET__"
        target.image = image
        target.select = True
        nodes.active = target

        emission = nodes.new("ShaderNodeEmission")
        emission.name = "__TM_ALPHA_COVERAGE_EMISSION__"
        emission.inputs["Strength"].default_value = 1.0
        alpha_input = tm_find_material_alpha_input(material)
        if alpha_input and alpha_input.is_linked:
            links.new(alpha_input.links[0].from_socket, emission.inputs["Color"])
        else:
            alpha_value = alpha_input.default_value if alpha_input else 1.0
            emission.inputs["Color"].default_value = (
                alpha_value,
                alpha_value,
                alpha_value,
                1.0,
            )
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
        records.append(
            (
                material,
                target,
                emission,
                output,
                previous_surface_socket,
                previous_active,
            )
        )
    return records


def tm_remove_alpha_bake_overrides(records):
    for (
        material,
        target,
        emission,
        output,
        previous_surface_socket,
        previous_active,
    ) in records:
        if not material or not material.node_tree:
            continue
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        if emission and nodes.get(emission.name) == emission:
            nodes.remove(emission)
        if previous_surface_socket and output in nodes.values():
            links.new(previous_surface_socket, output.inputs["Surface"])
        if target and nodes.get(target.name) == target:
            nodes.remove(target)
        if previous_active and nodes.get(previous_active.name) == previous_active:
            nodes.active = previous_active


def tm_bake_stack_alpha_coverage(context, obj, material, layer, image):
    width, height = image.size
    coverage_image = bpy.data.images.new(
        name="__TM_ALPHA_COVERAGE__",
        width=width,
        height=height,
        alpha=True,
    )
    coverage_image.generated_color = (0.0, 0.0, 0.0, 0.0)
    try:
        coverage_image.colorspace_settings.name = 'Non-Color'
    except Exception:
        pass

    previous_enabled = layer.enabled
    previous_samples = context.scene.cycles.samples
    records = []
    try:
        layer.enabled = False
        rebuild_texture_material(material)
        tm_clear_image(coverage_image)
        records = tm_create_alpha_bake_overrides(obj, coverage_image)
        if not records:
            raise RuntimeError("No node-based material is assigned to the object")
        context.scene.cycles.samples = 1
        bpy.ops.object.bake(type='EMIT')

        total = width * height * 4
        pixels = array('f', [0.0]) * total
        coverage_image.pixels.foreach_get(pixels)
        coverage = array('f', [0.0]) * (width * height)
        for pixel_index in range(width * height):
            offset = pixel_index * 4
            coverage[pixel_index] = max(
                0.0,
                min(
                    1.0,
                    pixels[offset] * 0.2126
                    + pixels[offset + 1] * 0.7152
                    + pixels[offset + 2] * 0.0722,
                ),
            )
        return coverage
    finally:
        tm_remove_alpha_bake_overrides(records)
        context.scene.cycles.samples = previous_samples
        layer.enabled = previous_enabled
        rebuild_texture_material(material)
        bpy.data.images.remove(coverage_image)


def tm_apply_alpha_coverage(image, coverage):
    width, height = image.size
    if len(coverage) != width * height:
        raise RuntimeError("AO coverage size does not match the target image")
    total = width * height * 4
    pixels = array('f', [0.0]) * total
    image.pixels.foreach_get(pixels)
    for pixel_index, alpha_coverage in enumerate(coverage):
        pixels[pixel_index * 4 + 3] *= alpha_coverage
    image.pixels.foreach_set(pixels)
    image.update()


def get_object_output_material(obj, settings):
    material_name = settings.material_name.strip() or f"TM Baked - {obj.name}"
    material = settings.output_material
    if not material or material.name != material_name:
        material = bpy.data.materials.new(name=material_name)
        settings.output_material = material
        # A different output material starts with its own set of baked images.
        for _channel, _label, _toggle_property, image_property in (
            TM_OBJECT_BAKE_CHANNELS
        ):
            setattr(settings, image_property, None)
    settings.material_name = material.name
    return material


def ensure_object_bake_image(settings, material, channel='ALBEDO'):
    channel_data = next(
        (data for data in TM_OBJECT_BAKE_CHANNELS if data[0] == channel),
        None,
    )
    if channel_data is None:
        raise RuntimeError(f"Unsupported object bake channel: {channel}")
    _identifier, label, _toggle_property, image_property = channel_data
    image = getattr(settings, image_property)
    image_name = f"{material.name} - {label}"
    if not image:
        image = bpy.data.images.new(
            name=image_name,
            width=settings.resolution,
            height=settings.resolution,
            alpha=True,
        )
        setattr(settings, image_property, image)
    else:
        image.name = image_name
        if image.size[:] != (settings.resolution, settings.resolution):
            image.scale(settings.resolution, settings.resolution)
    image.use_fake_user = True
    generated_colors = {
        'NORMAL': (0.5, 0.5, 1.0, 1.0),
        'HEIGHT': (0.5, 0.5, 0.5, 1.0),
        'AO': (1.0, 1.0, 1.0, 1.0),
        'ROUGHNESS': (0.5, 0.5, 0.5, 1.0),
        'METALNESS': (0.0, 0.0, 0.0, 1.0),
        'ALPHA': (1.0, 1.0, 1.0, 1.0),
    }
    image.generated_color = generated_colors.get(
        channel,
        (0.0, 0.0, 0.0, 0.0),
    )
    try:
        image.colorspace_settings.name = (
            'sRGB' if channel in {'ALBEDO', 'EMISSION'} else 'Non-Color'
        )
    except Exception:
        pass
    return image


def rebuild_object_output_material(material, images, uv_map):
    if isinstance(images, bpy.types.Image):
        images = {'ALBEDO': images}
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    uv_node = nodes.new("ShaderNodeUVMap")
    uv_node.name = "TM Baked UV Map"
    uv_node.uv_map = uv_map
    uv_node.location = (-600.0, 0.0)

    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.name = "TM Baked Principled BSDF"
    shader.location = (240.0, 80.0)
    shader.inputs["Roughness"].default_value = 0.5

    image_nodes = {}
    for index, (channel, label, _toggle, _pointer) in enumerate(
        TM_OBJECT_BAKE_CHANNELS
    ):
        image = images.get(channel)
        if image is None:
            continue
        image_node = nodes.new("ShaderNodeTexImage")
        image_node.name = f"TM Baked {label}"
        image_node.label = label
        image_node.image = image
        image_node.interpolation = 'Linear'
        image_node.location = (-360.0, 260.0 - index * 220.0)
        links.new(uv_node.outputs["UV"], image_node.inputs["Vector"])
        image_nodes[channel] = image_node

    albedo = image_nodes.get('ALBEDO')
    if albedo:
        links.new(albedo.outputs["Color"], shader.inputs["Base Color"])
        links.new(albedo.outputs["Alpha"], shader.inputs["Alpha"])

    roughness = image_nodes.get('ROUGHNESS')
    if roughness:
        links.new(roughness.outputs["Color"], shader.inputs["Roughness"])
    metalness = image_nodes.get('METALNESS')
    if metalness:
        links.new(metalness.outputs["Color"], shader.inputs["Metallic"])

    normal_output = None
    normal = image_nodes.get('NORMAL')
    if normal:
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.name = "TM Baked Normal Map"
        normal_map.space = 'TANGENT'
        normal_map.uv_map = uv_map
        normal_map.location = (-80.0, -120.0)
        links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        normal_output = normal_map.outputs["Normal"]

    # A baked Normal already represents the final surface, including bump. Use
    # Height for preview only when Normal was not requested, avoiding duplicate
    # relief while keeping both image nodes available for export.
    height = image_nodes.get('HEIGHT')
    if height and normal_output is None:
        bump = nodes.new("ShaderNodeBump")
        bump.name = "TM Baked Height"
        bump.location = (-80.0, -360.0)
        bump.inputs["Midlevel"].default_value = 0.5
        links.new(height.outputs["Color"], bump.inputs["Height"])
        normal_output = bump.outputs["Normal"]
    if normal_output is not None:
        links.new(normal_output, shader.inputs["Normal"])

    alpha = image_nodes.get('ALPHA')
    if alpha:
        links.new(alpha.outputs["Color"], shader.inputs["Alpha"])
    emission = image_nodes.get('EMISSION')
    emission_input = shader.inputs.get("Emission Color")
    if emission and emission_input:
        links.new(emission.outputs["Color"], emission_input)
        strength_input = shader.inputs.get("Emission Strength")
        if strength_input:
            strength_input.default_value = 1.0

    output = nodes.new("ShaderNodeOutputMaterial")
    output.name = "TM Baked Material Output"
    output.location = (560.0, 80.0)
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    try:
        material.surface_render_method = 'DITHERED'
    except Exception:
        try:
            material.blend_method = 'BLEND'
        except Exception:
            pass


class TM_Layer(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(
        name="Name",
        default="Layer",
        update=update_material_from_layer,
    )
    enabled: bpy.props.BoolProperty(
        name="Visible",
        default=True,
        update=update_material_from_layer,
    )
    layer_type: bpy.props.EnumProperty(
        name="Layer Type",
        items=TM_LAYER_TYPE_ITEMS,
        default='ALBEDO',
        update=update_material_from_layer,
    )
    image: bpy.props.PointerProperty(
        name="Image",
        type=bpy.types.Image,
        update=update_material_from_layer,
    )
    uv_map: bpy.props.StringProperty(
        name="UV Map",
        update=update_material_from_layer,
    )
    high_poly_object: bpy.props.PointerProperty(
        name="High Poly",
        description="Source mesh used for Selected to Active baking",
        type=bpy.types.Object,
        poll=tm_mesh_object_poll,
    )
    bake_match_vertex_groups: bpy.props.BoolProperty(
        name="Match Vertex Group Name",
        description=(
            "Bake complete faces in identically named vertex groups only from their "
            "matching high-poly region; unassigned faces use the full source"
        ),
        default=False,
    )
    bake_use_cage: bpy.props.BoolProperty(
        name="Cage",
        description="Cast bake rays from an inflated low-poly cage",
        default=False,
    )
    bake_cage_object: bpy.props.PointerProperty(
        name="Cage Object",
        description="Custom cage with the same topology as the low-poly mesh",
        type=bpy.types.Object,
        poll=tm_mesh_object_poll,
    )
    bake_cage_extrusion: bpy.props.FloatProperty(
        name="Extrusion",
        description="Distance used to inflate the automatic cage",
        default=0.0,
        min=0.0,
        soft_max=1.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    bake_max_ray_distance: bpy.props.FloatProperty(
        name="Max Ray Distance",
        description="Maximum ray distance when baking without a cage",
        default=0.0,
        min=0.0,
        soft_max=1.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    mapping_type: bpy.props.EnumProperty(
        name="Mapping",
        items=TM_MAPPING_ITEMS,
        default='UV',
        update=update_material_from_layer,
    )
    mapping_position: bpy.props.FloatVectorProperty(
        name="Position",
        subtype='XYZ',
        size=3,
        default=(0.0, 0.0, 0.0),
        update=update_material_from_layer,
    )
    mapping_rotation: bpy.props.FloatVectorProperty(
        name="Rotation",
        subtype='EULER',
        size=3,
        default=(0.0, 0.0, 0.0),
        update=update_material_from_layer,
    )
    mapping_scale: bpy.props.FloatVectorProperty(
        name="Scale",
        subtype='XYZ',
        size=3,
        default=(1.0, 1.0, 1.0),
        min=0.0001,
        soft_max=20.0,
        update=update_material_from_layer,
    )
    triplanar_blend: bpy.props.FloatProperty(
        name="Blend",
        description="Softness between triplanar projection axes",
        default=0.2,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=update_material_from_layer,
    )
    blend_mode: bpy.props.EnumProperty(
        name="Blend Mode",
        items=TM_BLEND_ITEMS,
        default='MIX',
        update=update_material_from_layer,
    )
    is_mask: bpy.props.BoolProperty(
        name="Mask",
        description="Use this layer as a hierarchical mask for its children",
        default=False,
        update=update_layer_mask_state,
    )
    depth: bpy.props.IntProperty(
        name="Hierarchy Depth",
        default=0,
        min=0,
        max=16,
        options={'HIDDEN'},
    )
    opacity: bpy.props.FloatProperty(
        name="Opacity",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=update_material_from_layer,
    )
    roughness: bpy.props.FloatProperty(
        name="Roughness",
        description="Surface roughness contributed by this layer",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=update_material_from_layer,
    )
    metalness: bpy.props.FloatProperty(
        name="Metalness",
        description="Metalness contributed by this layer",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=update_material_from_layer,
    )
    height_direction: bpy.props.FloatProperty(
        name="Depth / Elevation",
        description=(
            "Signed relief direction: negative carves depth, positive creates elevation"
        ),
        default=1.0,
        min=-1.0,
        max=1.0,
        update=update_material_from_layer,
    )
    height_strength: bpy.props.FloatProperty(
        name="Height Strength",
        description="Relief strength, independent of depth or elevation direction",
        default=1.0,
        min=0.0,
        soft_max=5.0,
        max=20.0,
        update=update_material_from_layer,
    )
    overlay_height: bpy.props.BoolProperty(
        name="Overlay Height",
        description=(
            "Replace Height layers below wherever this layer has painted coverage"
        ),
        default=False,
        update=update_material_from_layer,
    )
    normal_strength: bpy.props.FloatProperty(
        name="Strength",
        description="Strength of the tangent-space normal map",
        default=1.0,
        min=0.0,
        soft_max=5.0,
        max=20.0,
        update=update_material_from_layer,
    )
    blur_radius: bpy.props.FloatProperty(
        name="Blur",
        description="Non-destructive Gaussian blur radius in texture pixels",
        default=0.0,
        min=0.0,
        soft_max=32.0,
        max=256.0,
        precision=1,
        update=update_material_from_layer,
    )
    rgb_color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.8, 0.8, 0.8, 1.0),
        update=update_material_from_layer,
    )
    use_color_ramp: bpy.props.BoolProperty(
        name="Color Ramp",
        description="Remap this effect mask with an editable Color Ramp",
        default=False,
        update=update_layer_color_ramp,
    )
    color_ramp_group: bpy.props.PointerProperty(
        name="Color Ramp Data",
        type=bpy.types.NodeTree,
        options={'HIDDEN'},
    )
    ao_method: bpy.props.EnumProperty(
        name="Method",
        items=[
            ('RAYTRACED', "Raytraced", "Cycles ambient occlusion bake"),
            (
                'APPROXIMATE',
                "Approximate",
                "Smooth proximity-based ambient occlusion without ray tracing",
            ),
        ],
        default='RAYTRACED',
    )
    ao_approx_distance: bpy.props.FloatProperty(
        name="Distance",
        description="Maximum world-space distance considered for occlusion",
        default=1.0,
        min=0.001,
        soft_max=10.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    ao_approx_strength: bpy.props.FloatProperty(
        name="Strength",
        default=1.0,
        min=0.0,
        max=3.0,
    )
    ao_approx_falloff: bpy.props.FloatProperty(
        name="Falloff",
        default=1.5,
        min=0.25,
        max=4.0,
    )
    ao_approx_resolution: bpy.props.IntProperty(
        name="Detail",
        description="Internal mask resolution before smooth upscaling",
        default=256,
        min=32,
        max=512,
    )
    ao_approx_subdivision: bpy.props.IntProperty(
        name="Topology Detail",
        description="Temporary geometry subdivision used only for AO calculation",
        default=2,
        min=0,
        max=3,
    )
    ao_approx_neighbors: bpy.props.IntProperty(
        name="Neighbors",
        description="Maximum nearby surface samples evaluated per texel",
        default=24,
        min=4,
        max=64,
    )
    ao_approx_smooth: bpy.props.IntProperty(
        name="Smooth",
        default=2,
        min=0,
        max=6,
    )
    ao_approx_self_only: bpy.props.BoolProperty(
        name="Self Only",
        description="Ignore nearby geometry from other objects",
        default=False,
    )
    hard_shadow_threshold: bpy.props.FloatProperty(
        name="Threshold",
        default=0.2,
        min=0.0,
        max=1.0,
    )
    toon_threshold: bpy.props.FloatProperty(
        name="Threshold",
        default=0.2,
        min=0.0,
        max=1.0,
    )
    edge_marker: bpy.props.EnumProperty(
        name="Marker",
        description="Mesh edge marker used to generate this layer",
        items=TM_EDGE_MARKER_ITEMS,
        default='SHARP',
    )
    sharp_thickness: bpy.props.IntProperty(
        name="Thickness",
        default=8,
        min=1,
        max=256,
    )
    sharp_opacity: bpy.props.FloatProperty(
        name="Line Opacity",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    sharp_hardness: bpy.props.FloatProperty(
        name="Hardness",
        default=0.65,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    sharp_smooth: bpy.props.IntProperty(
        name="Smooth",
        default=2,
        min=0,
        max=32,
    )
    sharp_irregularity: bpy.props.FloatProperty(
        name="Irregularity",
        default=0.45,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    curvature_mode: bpy.props.EnumProperty(
        name="Edge Type",
        items=[
            ('BOTH', "Ridges + Valleys", "Paint convex and concave corners"),
            ('CONVEX', "Ridges", "Paint exposed convex corners"),
            ('CONCAVE', "Valleys", "Paint recessed concave corners"),
        ],
        default='CONVEX',
    )
    curvature_angle: bpy.props.FloatProperty(
        name="Sharp Angle",
        default=math.radians(60.0),
        min=0.0,
        max=math.radians(179.0),
        subtype='ANGLE',
        unit='ROTATION',
    )
    curvature_falloff: bpy.props.FloatProperty(
        name="Sharpness Range",
        default=math.radians(30.0),
        min=math.radians(0.1),
        max=math.radians(179.0),
        subtype='ANGLE',
        unit='ROTATION',
    )
    curvature_thickness: bpy.props.IntProperty(
        name="Thickness",
        default=12,
        min=1,
        max=256,
    )
    curvature_opacity: bpy.props.FloatProperty(
        name="Strength",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    curvature_hardness: bpy.props.FloatProperty(
        name="Hardness",
        default=0.45,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    curvature_smooth: bpy.props.IntProperty(
        name="Smooth",
        default=3,
        min=0,
        max=32,
    )
    curvature_irregularity: bpy.props.FloatProperty(
        name="Irregularity",
        default=0.25,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    lineart_object: bpy.props.PointerProperty(
        name="Guide",
        description="Curve or Grease Pencil object projected into this layer",
        type=bpy.types.Object,
        poll=tm_lineart_object_poll,
    )
    lineart_thickness: bpy.props.IntProperty(
        name="Thickness",
        description="Uniform line width in texture pixels",
        default=6,
        min=1,
        max=256,
    )
    lineart_opacity: bpy.props.FloatProperty(
        name="Line Opacity",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    lineart_hardness: bpy.props.FloatProperty(
        name="Hardness",
        default=0.8,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    lineart_smooth: bpy.props.IntProperty(
        name="Smooth",
        default=1,
        min=0,
        max=32,
    )
    lineart_sample_step: bpy.props.FloatProperty(
        name="Sample Step",
        description="World-space spacing used to project each stroke",
        default=0.01,
        min=0.0001,
        soft_max=0.25,
        precision=4,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    lineart_projection_distance: bpy.props.FloatProperty(
        name="Projection Distance",
        description="Maximum world-space distance from a stroke to the mesh",
        default=0.1,
        min=0.0001,
        soft_max=2.0,
        precision=4,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    lineart_break_uv_seams: bpy.props.BoolProperty(
        name="Protect UV Seams",
        description="Prevent projected lines from crossing distant UV islands",
        default=True,
    )
    lineart_uv_jump: bpy.props.FloatProperty(
        name="UV Seam Limit",
        description="Maximum UV distance allowed between projected samples",
        default=0.25,
        min=0.001,
        max=2.0,
        precision=3,
    )
class TM_MaterialSettings(bpy.types.PropertyGroup):
    initialized: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    show_shader_settings: bpy.props.BoolProperty(
        name="Shader",
        description="Show the main material shader settings",
        default=False,
    )
    shader_type: bpy.props.EnumProperty(
        name="Type",
        items=TM_SHADER_ITEMS,
        default='PRINCIPLED',
        update=update_material_shader,
    )
    layers: bpy.props.CollectionProperty(type=TM_Layer)
    layer_index: bpy.props.IntProperty(
        default=0,
        update=update_selected_layer,
    )
    resolution: bpy.props.IntProperty(
        name="Resolution",
        default=1024,
        min=16,
        max=8192,
    )
    margin: bpy.props.IntProperty(
        name="Margin",
        default=16,
        min=0,
        max=128,
    )
    samples: bpy.props.IntProperty(
        name="Samples",
        default=16,
        min=1,
        max=4096,
    )


class TM_ObjectBakeSettings(bpy.types.PropertyGroup):
    material_name: bpy.props.StringProperty(
        name="Material Name",
        description="Name of the material created from the final baked texture",
        default="Baked Material",
    )
    uv_map: bpy.props.StringProperty(name="UV Map")
    bake_mode: bpy.props.EnumProperty(
        name="Mode",
        items=TM_OBJECT_BAKE_MODE_ITEMS,
        default='SINGLE',
    )
    bake_albedo: bpy.props.BoolProperty(name="Albedo", default=True)
    bake_normal: bpy.props.BoolProperty(name="Normal Map", default=True)
    bake_height: bpy.props.BoolProperty(name="Height", default=True)
    bake_ao: bpy.props.BoolProperty(name="Ambient Occlusion", default=True)
    bake_roughness: bpy.props.BoolProperty(name="Roughness", default=True)
    bake_metalness: bpy.props.BoolProperty(name="Metalness", default=True)
    bake_emission: bpy.props.BoolProperty(name="Emission", default=False)
    bake_alpha: bpy.props.BoolProperty(name="Alpha", default=False)
    resolution: bpy.props.IntProperty(
        name="Resolution",
        default=1024,
        min=16,
        max=8192,
    )
    margin: bpy.props.IntProperty(
        name="Margin",
        default=16,
        min=0,
        max=128,
    )
    samples: bpy.props.IntProperty(
        name="Samples",
        default=16,
        min=1,
        max=4096,
    )
    output_image: bpy.props.PointerProperty(
        name="Baked Texture",
        type=bpy.types.Image,
    )
    output_material: bpy.props.PointerProperty(
        name="Baked Material",
        type=bpy.types.Material,
    )
    output_normal_image: bpy.props.PointerProperty(type=bpy.types.Image)
    output_height_image: bpy.props.PointerProperty(type=bpy.types.Image)
    output_ao_image: bpy.props.PointerProperty(type=bpy.types.Image)
    output_roughness_image: bpy.props.PointerProperty(type=bpy.types.Image)
    output_metalness_image: bpy.props.PointerProperty(type=bpy.types.Image)
    output_emission_image: bpy.props.PointerProperty(type=bpy.types.Image)
    output_alpha_image: bpy.props.PointerProperty(type=bpy.types.Image)


class TM_OT_create_material(bpy.types.Operator):
    bl_idname = "tm.create_material"
    bl_label = "Create Texture Maker Material"
    bl_description = "Create and assign a material managed by Texture Maker"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.object and context.object.type == 'MESH')

    def execute(self, context):
        obj = context.object
        material = bpy.data.materials.new(name=f"Texture Maker - {obj.name}")
        material.use_nodes = True
        material.tm_settings.initialized = True
        obj.data.materials.append(material)
        obj.active_material_index = len(obj.material_slots) - 1
        rebuild_texture_material(material)
        self.report({'INFO'}, f"Created '{material.name}'")
        return {'FINISHED'}


class TM_OT_initialize_material(bpy.types.Operator):
    bl_idname = "tm.initialize_material"
    bl_label = "Initialize Active Material"
    bl_description = "Convert the active material into a Texture Maker material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(get_active_material(context))

    def execute(self, context):
        material = get_active_material(context)
        material.tm_settings.initialized = True
        rebuild_texture_material(material)
        return {'FINISHED'}


def get_layer_subtree_end(layers, index):
    if not 0 <= index < len(layers):
        return index
    parent_depth = layers[index].depth
    subtree_end = index + 1
    while subtree_end < len(layers) and layers[subtree_end].depth > parent_depth:
        subtree_end += 1
    return subtree_end


class TM_OT_add_layer(bpy.types.Operator):
    bl_idname = "tm.add_layer"
    bl_label = "Add Layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        material = get_active_material(context)
        if not material or not material.tm_settings.initialized:
            return {'CANCELLED'}
        settings = material.tm_settings
        layer = settings.layers.add()
        layer.name = f"Layer {len(settings.layers)}"
        obj = context.object
        if obj.data.uv_layers.active:
            layer.uv_map = obj.data.uv_layers.active.name
        new_layer_index = len(settings.layers) - 1
        if new_layer_index > 0:
            settings.layers.move(new_layer_index, 0)
        settings.layer_index = 0
        rebuild_texture_material(material)
        return {'FINISHED'}


class TM_OT_remove_layer(bpy.types.Operator):
    bl_idname = "tm.remove_layer"
    bl_label = "Remove Layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        material = get_active_material(context)
        if not material:
            return {'CANCELLED'}
        settings = material.tm_settings
        index = settings.layer_index
        if 0 <= index < len(settings.layers):
            ramp_group = settings.layers[index].color_ramp_group
            removed_depth = settings.layers[index].depth
            subtree_end = get_layer_subtree_end(settings.layers, index)
            for child_index in range(index + 1, subtree_end):
                child = settings.layers[child_index]
                child.depth = max(removed_depth, child.depth - 1)
            settings.layers.remove(index)
            settings.layer_index = min(index, len(settings.layers) - 1)
            rebuild_texture_material(material)
            if ramp_group and ramp_group.users == 0:
                bpy.data.node_groups.remove(ramp_group)
        return {'FINISHED'}


class TM_OT_move_layer(bpy.types.Operator):
    bl_idname = "tm.move_layer"
    bl_label = "Move Layer"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.EnumProperty(
        items=[('UP', "Up", "Move up"), ('DOWN', "Down", "Move down")]
    )

    def execute(self, context):
        material = get_active_material(context)
        if not material:
            return {'CANCELLED'}
        settings = material.tm_settings
        index = settings.layer_index
        if not 0 <= index < len(settings.layers):
            return {'CANCELLED'}

        layers = settings.layers
        depth = layers[index].depth
        subtree_end = get_layer_subtree_end(layers, index)
        subtree_length = subtree_end - index
        new_index = index

        if self.direction == 'UP':
            previous_index = index - 1
            while previous_index >= 0 and layers[previous_index].depth > depth:
                previous_index -= 1
            if previous_index < 0 or layers[previous_index].depth != depth:
                return {'CANCELLED'}
            for offset in range(subtree_length):
                layers.move(index + offset, previous_index + offset)
            new_index = previous_index
        else:
            next_index = subtree_end
            if next_index >= len(layers) or layers[next_index].depth != depth:
                return {'CANCELLED'}
            next_end = get_layer_subtree_end(layers, next_index)
            next_length = next_end - next_index
            for _offset in range(subtree_length):
                layers.move(index, next_end - 1)
            new_index = index + next_length

        settings.layer_index = new_index
        rebuild_texture_material(material)
        return {'FINISHED'}


class TM_OT_indent_layer(bpy.types.Operator):
    bl_idname = "tm.indent_layer"
    bl_label = "Make Child"
    bl_description = "Make the selected layer a child of the previous Mask"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        material = get_active_material(context)
        if not material:
            return {'CANCELLED'}
        settings = material.tm_settings
        index = settings.layer_index
        if not 0 <= index < len(settings.layers):
            return {'CANCELLED'}

        layers = settings.layers
        depth = layers[index].depth
        previous_index = index - 1
        while previous_index >= 0 and layers[previous_index].depth > depth:
            previous_index -= 1
        if (
            previous_index < 0
            or layers[previous_index].depth != depth
            or not layers[previous_index].is_mask
            or depth >= 16
        ):
            return {'CANCELLED'}

        subtree_end = get_layer_subtree_end(layers, index)
        for child_index in range(index, subtree_end):
            layers[child_index].depth += 1
        rebuild_texture_material(material)
        return {'FINISHED'}


class TM_OT_outdent_layer(bpy.types.Operator):
    bl_idname = "tm.outdent_layer"
    bl_label = "Move Out of Mask"
    bl_description = "Move the selected layer and its children out of their Mask"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        material = get_active_material(context)
        if not material:
            return {'CANCELLED'}
        settings = material.tm_settings
        index = settings.layer_index
        if not 0 <= index < len(settings.layers):
            return {'CANCELLED'}

        layers = settings.layers
        depth = layers[index].depth
        if depth <= 0:
            return {'CANCELLED'}

        parent_index = index - 1
        while parent_index >= 0 and layers[parent_index].depth >= depth:
            parent_index -= 1
        if parent_index < 0 or layers[parent_index].depth != depth - 1:
            return {'CANCELLED'}

        subtree_end = get_layer_subtree_end(layers, index)
        parent_end = get_layer_subtree_end(layers, parent_index)
        subtree_length = subtree_end - index
        following_length = parent_end - subtree_end
        new_index = index
        if following_length > 0:
            for _offset in range(subtree_length):
                layers.move(index, parent_end - 1)
            new_index += following_length

        for child_index in range(new_index, new_index + subtree_length):
            layers[child_index].depth -= 1
        settings.layer_index = new_index
        rebuild_texture_material(material)
        return {'FINISHED'}


class TM_OT_create_lineart_guide(bpy.types.Operator):
    bl_idname = "tm.create_lineart_guide"
    bl_label = "Create / Edit Guide"
    bl_description = "Create a Grease Pencil guide or edit the selected guide"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        material = get_active_material(context)
        return bool(
            material
            and material.tm_settings.initialized
            and 0 <= material.tm_settings.layer_index < len(
                material.tm_settings.layers
            )
            and material.tm_settings.layers[
                material.tm_settings.layer_index
            ].layer_type == 'LINEART'
        )

    def execute(self, context):
        target = context.object
        material = get_active_material(context)
        settings = material.tm_settings
        layer = settings.layers[settings.layer_index]
        guide = layer.lineart_object

        if context.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass

        if guide is None:
            try:
                bpy.ops.object.grease_pencil_add(
                    type='EMPTY',
                    use_in_front=True,
                    location=(0.0, 0.0, 0.0),
                )
            except Exception as error:
                self.report(
                    {'ERROR'},
                    f"Could not create the Grease Pencil guide: {error}",
                )
                return {'CANCELLED'}
            guide = context.object
            guide.name = f"TM Lineart - {target.name} - {layer.name}"
            layer.lineart_object = guide

        try:
            guide.hide_set(False)
            guide.hide_viewport = False
            guide.select_set(True)
            for selected_object in tuple(context.selected_objects):
                if selected_object != guide:
                    selected_object.select_set(False)
            context.view_layer.objects.active = guide
            guide.show_in_front = True
        except Exception:
            pass

        if guide.type in {'GREASEPENCIL', 'GPENCIL'}:
            tool_settings = context.scene.tool_settings
            for property_name in (
                "grease_pencil_stroke_placement_view3d",
                "gpencil_stroke_placement_view3d",
            ):
                if hasattr(tool_settings, property_name):
                    try:
                        setattr(tool_settings, property_name, 'SURFACE')
                    except Exception:
                        pass
            try:
                bpy.ops.object.mode_set(mode='PAINT_GREASE_PENCIL')
            except Exception:
                try:
                    bpy.ops.object.mode_set(mode='PAINT_GPENCIL')
                except Exception as error:
                    self.report({'WARNING'}, f"Guide created: {error}")
                    return {'FINISHED'}
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
            except Exception:
                pass
        elif guide.type == 'CURVE':
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass

        return {'FINISHED'}


class TM_OT_bake_layer(bpy.types.Operator):
    bl_idname = "tm.bake_layer"
    bl_label = "Generate"
    bl_description = "Generate the texture for the currently selected layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        material = get_active_material(context)
        return bool(
            material
            and material.tm_settings.initialized
            and material.tm_settings.layers
        )

    def execute(self, context):
        obj = context.object
        material = get_active_material(context)
        settings = material.tm_settings
        layer = settings.layers[settings.layer_index]
        high_poly = (
            layer.high_poly_object
            if (
                tm_layer_supports_selected_to_active(layer)
                and layer.blend_mode != 'HEIGHT'
            )
            else None
        )

        if layer.layer_type in {'IMAGE_TEXTURE', 'RGB'}:
            layer_name = layer.layer_type.replace('_', ' ').title()
            self.report(
                {'INFO'},
                f"{layer_name} layers do not require generation",
            )
            return {'CANCELLED'}
        if high_poly:
            if high_poly == obj:
                self.report(
                    {'ERROR'},
                    "High Poly must be different from the active low-poly object",
                )
                return {'CANCELLED'}
            if not tm_object_is_in_view_layer(context, high_poly):
                self.report(
                    {'ERROR'},
                    "High Poly must be available in the current View Layer",
                )
                return {'CANCELLED'}
            cage_object = (
                layer.bake_cage_object
                if layer.bake_use_cage
                else None
            )
            if cage_object:
                if cage_object == obj or cage_object == high_poly:
                    self.report(
                        {'ERROR'},
                        "Cage Object must be different from the low and high-poly objects",
                    )
                    return {'CANCELLED'}
                if not tm_object_is_in_view_layer(context, cage_object):
                    self.report(
                        {'ERROR'},
                        "Cage Object must be available in the current View Layer",
                    )
                    return {'CANCELLED'}
        if not obj.data.uv_layers:
            self.report({'ERROR'}, "The object needs a UV map")
            return {'CANCELLED'}
        uv_layer = obj.data.uv_layers.get(layer.uv_map)
        if not uv_layer:
            uv_layer = obj.data.uv_layers.active
            layer.uv_map = uv_layer.name

        image = ensure_layer_image(material, layer, settings.resolution)
        previous_engine = context.scene.render.engine
        previous_mode = obj.mode
        previous_uv = obj.data.uv_layers.active
        previous_selected_objects = tuple(context.selected_objects)
        previous_active_object = context.view_layer.objects.active
        previous_samples = getattr(context.scene.cycles, "samples", None)
        bake = context.scene.render.bake
        previous_bake = {
            "margin": bake.margin,
            "use_clear": bake.use_clear,
            "use_pass_direct": bake.use_pass_direct,
            "use_pass_indirect": bake.use_pass_indirect,
            "use_pass_color": bake.use_pass_color,
            "use_selected_to_active": bake.use_selected_to_active,
            "use_cage": bake.use_cage,
            "cage_extrusion": bake.cage_extrusion,
            "max_ray_distance": bake.max_ray_distance,
            "cage_object": bake.cage_object,
        }
        temp_nodes = []
        active_nodes = {}
        override_data = None
        override_object = None
        shadow_visibility = None
        group_bake_data = None

        try:
            if previous_mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            tm_select_only_object(context, obj)
            obj.data.uv_layers.active = uv_layer
            uv_layer.active_render = True
            context.scene.render.engine = 'CYCLES'
            context.scene.cycles.samples = settings.samples
            bake.margin = settings.margin
            bake.use_clear = True
            bake.use_selected_to_active = False
            bake.use_cage = False
            bake.cage_extrusion = 0.0
            bake.max_ray_distance = 0.0
            bake.cage_object = None
            try:
                bake.target = 'IMAGE_TEXTURES'
            except Exception:
                pass

            if high_poly and layer.bake_match_vertex_groups:
                group_bake_data = tm_prepare_vertex_group_bake(context, obj, high_poly, layer)
                if not group_bake_data:
                    self.report({'WARNING'}, "No matching face regions; using the regular bake")
            alpha_coverage = None
            if layer.blend_mode == 'HEIGHT':
                tm_initialize_height_paint_image(image)
            else:
                tm_clear_image(image)
            if layer.blend_mode != 'HEIGHT' and layer.layer_type == 'AO':
                alpha_coverage = tm_bake_stack_alpha_coverage(
                    context,
                    obj,
                    material,
                    layer,
                    image,
                )
            if layer.blend_mode == 'HEIGHT':
                pass
            elif (
                layer.layer_type == 'AO'
                and layer.ao_method == 'APPROXIMATE'
            ):
                paint_approximate_ao(
                    context,
                    obj,
                    image,
                    layer,
                    settings.margin,
                )
            elif layer.layer_type in {'SHARP_EDGES', 'CURVATURE'}:
                paint_direct_edge_layer(context, obj, image, layer)
            elif layer.layer_type == 'LINEART':
                paint_lineart_layer(context, obj, image, layer)
            else:
                bake.use_selected_to_active = bool(high_poly)
                if high_poly:
                    bake.use_cage = layer.bake_use_cage
                    bake.cage_extrusion = layer.bake_cage_extrusion
                    bake.max_ray_distance = layer.bake_max_ray_distance
                    bake.cage_object = (
                        layer.bake_cage_object
                        if layer.bake_use_cage
                        else None
                    )

                if layer.layer_type in {
                    'SHADOW',
                    'HARD_SHADOW',
                    'TOON',
                    'COLOR_ATTRIBUTE',
                }:
                    override_object = high_poly or obj
                    override_data = create_override_bake_material(
                        override_object,
                        image,
                        layer.layer_type,
                    )
                    if high_poly:
                        temp_nodes, active_nodes = create_temp_bake_nodes(
                            obj,
                            image,
                        )
                        if not temp_nodes:
                            raise RuntimeError(
                                "No node-based material is assigned to the low-poly object"
                            )
                    if layer.layer_type == 'TOON':
                        shadow_visibility = save_shadow_visibility()
                        set_shadow_visibility(False)
                else:
                    temp_nodes, active_nodes = create_temp_bake_nodes(obj, image)
                    if not temp_nodes:
                        raise RuntimeError(
                            "No node-based material is assigned to the object"
                        )

                bake_type = configure_bake_type(
                    context.scene,
                    layer.layer_type,
                )
                if group_bake_data:
                    tm_bake_vertex_group_regions(
                        context, obj, high_poly, layer, image, bake_type, group_bake_data,
                        override_data[0] if override_data else None,
                    )
                else:
                    if high_poly:
                        tm_select_bake_pair(context, obj, high_poly)
                    bpy.ops.object.bake(type=bake_type)

                if layer.layer_type == 'HARD_SHADOW':
                    tm_threshold_image(image, layer.hard_shadow_threshold)
                elif layer.layer_type == 'TOON':
                    tm_threshold_image(image, layer.toon_threshold)

                if layer.layer_type in {
                    'AO',
                    'SHADOW',
                    'HARD_SHADOW',
                    'TOON',
                }:
                    tm_make_dark_mask(image)

            if alpha_coverage is not None:
                tm_apply_alpha_coverage(image, alpha_coverage)

            image.pack()
            remove_temp_bake_nodes(temp_nodes, active_nodes)
            temp_nodes = []
            active_nodes = {}
            remove_override_bake_material(override_object, override_data)
            override_data = None
            override_object = None
            if shadow_visibility is not None:
                restore_shadow_visibility(shadow_visibility)
                shadow_visibility = None
            rebuild_texture_material(material)
            self.report({'INFO'}, f"Baked '{layer.name}' into '{image.name}'")
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, f"Layer bake failed: {error}")
            return {'CANCELLED'}
        finally:
            tm_remove_vertex_group_bake(group_bake_data)
            remove_temp_bake_nodes(temp_nodes, active_nodes)
            remove_override_bake_material(override_object, override_data)
            if shadow_visibility is not None:
                restore_shadow_visibility(shadow_visibility)
            bake.margin = previous_bake["margin"]
            bake.use_clear = previous_bake["use_clear"]
            bake.use_pass_direct = previous_bake["use_pass_direct"]
            bake.use_pass_indirect = previous_bake["use_pass_indirect"]
            bake.use_pass_color = previous_bake["use_pass_color"]
            bake.use_selected_to_active = previous_bake["use_selected_to_active"]
            bake.use_cage = previous_bake["use_cage"]
            bake.cage_extrusion = previous_bake["cage_extrusion"]
            bake.max_ray_distance = previous_bake["max_ray_distance"]
            bake.cage_object = previous_bake["cage_object"]
            if previous_samples is not None:
                context.scene.cycles.samples = previous_samples
            context.scene.render.engine = previous_engine
            if previous_uv:
                obj.data.uv_layers.active = previous_uv
            tm_restore_object_selection(
                context,
                previous_selected_objects,
                previous_active_object,
            )
            if previous_mode != 'OBJECT':
                try:
                    bpy.ops.object.mode_set(mode=previous_mode)
                except Exception:
                    pass


class TM_OT_bake_object_albedo(bpy.types.Operator):
    bl_idname = "tm.bake_object_albedo"
    bl_label = "Bake"
    bl_description = "Bake the object to one texture or selected material channels"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.object and context.object.type == 'MESH')

    def execute(self, context):
        obj = context.object
        settings = obj.tm_bake_settings
        if not obj.data.uv_layers:
            self.report({'ERROR'}, "The object needs a UV map")
            return {'CANCELLED'}

        uv_layer = obj.data.uv_layers.get(settings.uv_map)
        if not uv_layer:
            uv_layer = obj.data.uv_layers.active
            settings.uv_map = uv_layer.name

        output_material = get_object_output_material(obj, settings)
        for polygon in obj.data.polygons:
            slot_index = polygon.material_index
            if (
                slot_index < len(obj.material_slots)
                and obj.material_slots[slot_index].material == output_material
            ):
                self.report(
                    {'ERROR'},
                    "The baked material is assigned to source faces; restore the source materials first",
                )
                return {'CANCELLED'}

        source_materials = {
            slot.material
            for slot in obj.material_slots
            if slot.material and slot.material != output_material
        }
        if not source_materials:
            self.report({'ERROR'}, "The object has no source materials to bake")
            return {'CANCELLED'}

        if settings.bake_mode == 'SINGLE':
            channels = ['ALBEDO']
        else:
            channels = [
                channel
                for channel, _label, toggle_property, _image_property in (
                    TM_OBJECT_BAKE_CHANNELS
                )
                if getattr(settings, toggle_property)
            ]
            if not channels:
                self.report({'ERROR'}, "Select at least one channel to bake")
                return {'CANCELLED'}

        previous_engine = context.scene.render.engine
        previous_mode = obj.mode
        previous_uv = obj.data.uv_layers.active
        previous_samples = getattr(context.scene.cycles, "samples", None)
        bake = context.scene.render.bake
        previous_bake = {
            "margin": bake.margin,
            "use_clear": bake.use_clear,
            "use_pass_direct": bake.use_pass_direct,
            "use_pass_indirect": bake.use_pass_indirect,
            "use_pass_color": bake.use_pass_color,
            "use_selected_to_active": bake.use_selected_to_active,
        }
        temp_nodes = []
        active_nodes = {}
        override_records = []

        try:
            if previous_mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            obj.data.uv_layers.active = uv_layer
            uv_layer.active_render = True
            context.scene.render.engine = 'CYCLES'
            context.scene.cycles.samples = settings.samples
            bake.margin = settings.margin
            bake.use_clear = True
            bake.use_selected_to_active = False
            try:
                bake.target = 'IMAGE_TEXTURES'
            except Exception:
                pass

            images = {}
            for channel in channels:
                image = ensure_object_bake_image(
                    settings,
                    output_material,
                    channel,
                )
                tm_clear_image(image)
                try:
                    if channel in {'HEIGHT', 'METALNESS', 'ALPHA'}:
                        override_records = tm_create_data_channel_bake_overrides(
                            obj,
                            image,
                            channel,
                            excluded_materials={output_material},
                        )
                        if len(override_records) != len(source_materials):
                            raise RuntimeError(
                                "Every source material must use nodes before it can be baked"
                            )
                        bake_type = 'EMIT'
                    else:
                        temp_nodes, active_nodes = create_temp_bake_nodes(
                            obj,
                            image,
                            excluded_materials={output_material},
                        )
                        if len(temp_nodes) != len(source_materials):
                            raise RuntimeError(
                                "Every source material must use nodes before it can be baked"
                            )
                        bake_type = {
                            'ALBEDO': 'DIFFUSE',
                            'NORMAL': 'NORMAL',
                            'AO': 'AO',
                            'ROUGHNESS': 'ROUGHNESS',
                            'EMISSION': 'EMIT',
                        }[channel]

                    bake.use_pass_direct = False
                    bake.use_pass_indirect = False
                    bake.use_pass_color = channel == 'ALBEDO'
                    if channel == 'NORMAL':
                        try:
                            bake.normal_space = 'TANGENT'
                        except Exception:
                            pass
                    bpy.ops.object.bake(type=bake_type)
                    image.pack()
                    images[channel] = image
                finally:
                    remove_temp_bake_nodes(temp_nodes, active_nodes)
                    temp_nodes = []
                    active_nodes = {}
                    tm_remove_alpha_bake_overrides(override_records)
                    override_records = []

            rebuild_object_output_material(
                output_material,
                images,
                uv_layer.name,
            )
            channel_labels = ", ".join(
                next(data[1] for data in TM_OBJECT_BAKE_CHANNELS if data[0] == channel)
                for channel in channels
            )
            self.report(
                {'INFO'},
                f"Baked '{obj.name}' channels: {channel_labels}",
            )
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, f"Object bake failed: {error}")
            return {'CANCELLED'}
        finally:
            remove_temp_bake_nodes(temp_nodes, active_nodes)
            tm_remove_alpha_bake_overrides(override_records)
            bake.margin = previous_bake["margin"]
            bake.use_clear = previous_bake["use_clear"]
            bake.use_pass_direct = previous_bake["use_pass_direct"]
            bake.use_pass_indirect = previous_bake["use_pass_indirect"]
            bake.use_pass_color = previous_bake["use_pass_color"]
            bake.use_selected_to_active = previous_bake["use_selected_to_active"]
            if previous_samples is not None:
                context.scene.cycles.samples = previous_samples
            context.scene.render.engine = previous_engine
            if previous_uv:
                obj.data.uv_layers.active = previous_uv
                previous_uv.active_render = True
            if previous_mode != 'OBJECT':
                try:
                    bpy.ops.object.mode_set(mode=previous_mode)
                except Exception:
                    pass


class TM_UL_layers(bpy.types.UIList):
    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
    ):
        row = layout.row(align=True)
        for _depth in range(item.depth):
            row.label(text="", icon='BLANK1')
        row.prop(item, "enabled", text="")
        row.label(text="", icon=TM_LAYER_ICONS.get(item.layer_type, 'IMAGE_DATA'))
        row.prop(item, "name", text="", emboss=False)
        blend_label = TM_BLEND_LABELS.get(item.blend_mode, item.blend_mode)
        row.label(text=f"Mask | {blend_label}" if item.is_mask else blend_label)


def draw_object_bake_settings(layout, obj):
    settings = obj.tm_bake_settings
    box = layout.box()
    box.label(text="Object Bake", icon='RENDER_STILL')
    box.prop(settings, "bake_mode", expand=True)
    if settings.bake_mode == 'MULTI':
        channels_box = box.box()
        channels_box.label(text="Channels", icon='IMAGE_DATA')
        channel_grid = channels_box.grid_flow(
            row_major=True,
            columns=2,
            even_columns=True,
            align=True,
        )
        for _channel, _label, toggle_property, _image_property in (
            TM_OBJECT_BAKE_CHANNELS
        ):
            channel_grid.prop(settings, toggle_property)
    box.prop(settings, "material_name")
    box.prop_search(
        settings,
        "uv_map",
        obj.data,
        "uv_layers",
        text="UV Map",
    )
    settings_row = box.row(align=True)
    settings_row.prop(settings, "resolution")
    settings_row.prop(settings, "margin")
    box.prop(settings, "samples")
    button = box.row()
    button.scale_y = 1.5
    button.operator(
        "tm.bake_object_albedo",
        text=("Bake Channels" if settings.bake_mode == 'MULTI' else "Bake"),
        icon='RENDER_STILL',
    )


class TM_PT_panel(bpy.types.Panel):
    bl_label = "Texture Maker"
    bl_idname = "TM_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Texture Maker"

    def draw(self, context):
        layout = self.layout
        obj = context.object
        if not obj or obj.type != 'MESH':
            layout.label(text="Select a mesh object", icon='ERROR')
            return

        material = obj.active_material
        layout.label(
            text=f"Material: {material.name}" if material else "No active material",
            icon='MATERIAL',
        )
        if not material:
            layout.operator("tm.create_material", icon='ADD')
        elif not material.tm_settings.initialized:
            layout.label(text="Active material is not managed", icon='INFO')
            layout.operator("tm.initialize_material", icon='NODE_MATERIAL')
            layout.operator("tm.create_material", icon='ADD')
        else:
            settings = material.tm_settings
            shader_box = layout.box()
            shader_header = shader_box.row(align=True)
            shader_header.prop(
                settings,
                "show_shader_settings",
                text=f"Main Shader: {TM_SHADER_LABELS[settings.shader_type]}",
                icon=(
                    'TRIA_DOWN'
                    if settings.show_shader_settings
                    else 'TRIA_RIGHT'
                ),
                emboss=False,
            )
            if settings.show_shader_settings:
                shader_box.prop(settings, "shader_type")

            output_box = layout.box()
            output_box.label(text="Bake Settings")
            output_row = output_box.row(align=True)
            output_row.prop(settings, "resolution")
            output_row.prop(settings, "margin")
            output_box.prop(settings, "samples")

            row = layout.row()
            row.template_list(
                "TM_UL_layers",
                "",
                settings,
                "layers",
                settings,
                "layer_index",
                rows=7,
            )
            buttons = row.column(align=True)
            buttons.operator("tm.add_layer", text="", icon='ADD')
            buttons.operator("tm.remove_layer", text="", icon='REMOVE')
            buttons.separator()
            move_up = buttons.operator("tm.move_layer", text="", icon='TRIA_UP')
            move_up.direction = 'UP'
            move_down = buttons.operator("tm.move_layer", text="", icon='TRIA_DOWN')
            move_down.direction = 'DOWN'
            buttons.separator()
            buttons.operator("tm.outdent_layer", text="", icon='TRIA_LEFT')
            buttons.operator("tm.indent_layer", text="", icon='TRIA_RIGHT')

            if 0 <= settings.layer_index < len(settings.layers):
                layer = settings.layers[settings.layer_index]
                selected_box = layout.box()
                selected_box.label(text="Selected Layer", icon='MATERIAL_DATA')
                identity_column = selected_box.column(align=True)
                identity_column.prop(layer, "name")
                type_row = identity_column.row(align=True)
                type_row.scale_y = 1.15
                type_row.prop(
                    layer,
                    "layer_type",
                    text="Type",
                    icon='RENDER_STILL',
                )

                compositing_box = selected_box.box()
                compositing_box.label(text="Compositing", icon='NODETREE')
                compositing_box.prop(layer, "is_mask", toggle=True)
                blend_row = compositing_box.row(align=True)
                blend_row.scale_y = 1.15
                blend_row.prop(
                    layer,
                    "blend_mode",
                    icon='COLOR',
                )
                compositing_box.prop(layer, "opacity", slider=True)
                if not layer.is_mask:
                    material_row = compositing_box.row(align=True)
                    material_row.prop(layer, "roughness", slider=True)
                    material_row.prop(layer, "metalness", slider=True)
                if layer.blend_mode == 'HEIGHT':
                    compositing_box.prop(
                        layer,
                        "height_direction",
                        text="Depth / Elevation",
                        slider=True,
                    )
                    compositing_box.prop(
                        layer,
                        "height_strength",
                        text="Height Strength",
                        slider=True,
                    )
                    compositing_box.prop(
                        layer,
                        "overlay_height",
                        text="Overlay Height",
                        toggle=True,
                    )
                elif layer.blend_mode == 'NORMAL_MAP':
                    compositing_box.prop(
                        layer,
                        "normal_strength",
                        text="Normal Strength",
                        slider=True,
                    )

                if layer.layer_type == 'RGB':
                    source_box = selected_box.box()
                    source_box.label(text="Source", icon='COLOR')
                    source_box.prop(layer, "rgb_color", text="Color")
                else:
                    texture_box = selected_box.box()
                    texture_box.label(
                        text=(
                            "Texture (RGB Color + Alpha Height)"
                            if layer.blend_mode == 'HEIGHT'
                            else "Texture"
                        ),
                        icon='TEXTURE',
                    )
                    mapping_row = texture_box.row(align=True)
                    mapping_row.scale_y = 1.15
                    if layer.blend_mode == 'NORMAL_MAP':
                        mapping_row.label(text="Mapping: UV (Normal Map)", icon='UV')
                    else:
                        mapping_row.prop(
                            layer,
                            "mapping_type",
                            text="Mapping",
                            icon='UV',
                        )
                    if (
                        tm_layer_mapping_type(layer) == 'UV'
                        or layer.layer_type != 'IMAGE_TEXTURE'
                    ):
                        texture_box.prop_search(
                            layer,
                            "uv_map",
                            obj.data,
                            "uv_layers",
                            text=(
                                "UV Map"
                                if tm_layer_mapping_type(layer) == 'UV'
                                else "Bake UV"
                            ),
                        )
                    texture_box.template_ID(layer, "image", open="image.open")
                    transform_column = texture_box.column(align=True)
                    for label, property_name in (
                        ("Position", "mapping_position"),
                        ("Rotation", "mapping_rotation"),
                        ("Scale", "mapping_scale"),
                    ):
                        transform_row = transform_column.row(align=True)
                        transform_split = transform_row.split(
                            factor=0.22,
                            align=True,
                        )
                        transform_split.label(text=label)
                        axis_row = transform_split.row(align=True)
                        for axis_index, axis_label in enumerate("XYZ"):
                            axis_row.prop(
                                layer,
                                property_name,
                                index=axis_index,
                                text=axis_label,
                            )
                    if tm_layer_mapping_type(layer) == 'TRIPLANAR':
                        texture_box.prop(layer, "triplanar_blend", slider=True)

                    adjustments_box = selected_box.box()
                    adjustments_box.label(text="Adjustments", icon='MODIFIER')
                    adjustments_box.prop(layer, "blur_radius", text="Blur")
                    if (
                        layer.layer_type in TM_COLOR_RAMP_LAYER_TYPES
                        and layer.blend_mode != 'NORMAL_MAP'
                    ):
                        adjustments_box.prop(
                            layer,
                            "use_color_ramp",
                            text="Color Ramp",
                            toggle=True,
                        )
                    if (
                        layer.layer_type in TM_COLOR_RAMP_LAYER_TYPES
                        and layer.use_color_ramp
                        and layer.blend_mode != 'NORMAL_MAP'
                    ):
                        _ramp_group, ramp_node = ensure_layer_color_ramp(layer)
                        adjustments_box.template_color_ramp(
                            ramp_node,
                            "color_ramp",
                            expand=True,
                        )

                if layer.layer_type not in {'IMAGE_TEXTURE', 'RGB'}:
                    generation_box = selected_box.box()
                    generation_box.label(text="Generation", icon='RENDER_STILL')

                    if layer.blend_mode == 'HEIGHT':
                        generation_box.label(
                            text="Generate creates a white paint canvas",
                            icon='BRUSH_DATA',
                        )

                    if (
                        layer.blend_mode != 'HEIGHT'
                        and tm_layer_supports_selected_to_active(layer)
                    ):
                        generation_box.prop(
                            layer,
                            "high_poly_object",
                            text="High Poly",
                        )
                        if layer.high_poly_object:
                            projection_box = generation_box.box()
                            projection_box.prop(layer, "bake_match_vertex_groups")
                            projection_box.prop(
                                layer,
                                "bake_use_cage",
                                text="Cage",
                                toggle=True,
                            )
                            if layer.bake_use_cage:
                                projection_box.prop(
                                    layer,
                                    "bake_cage_object",
                                )
                            extrusion_row = projection_box.row()
                            extrusion_row.enabled = not (
                                layer.bake_use_cage
                                and layer.bake_cage_object is not None
                            )
                            extrusion_row.prop(
                                layer,
                                "bake_cage_extrusion",
                            )
                            projection_box.prop(
                                layer,
                                "bake_max_ray_distance",
                            )

                    if layer.blend_mode != 'HEIGHT' and layer.layer_type == 'HARD_SHADOW':
                        generation_box.prop(layer, "hard_shadow_threshold")
                    elif layer.blend_mode != 'HEIGHT' and layer.layer_type == 'TOON':
                        generation_box.prop(layer, "toon_threshold")
                    elif layer.blend_mode != 'HEIGHT' and layer.layer_type == 'AO':
                        generation_box.prop(layer, "ao_method", expand=True)
                    if (
                        layer.blend_mode != 'HEIGHT'
                        and layer.layer_type == 'AO'
                        and layer.ao_method == 'APPROXIMATE'
                    ):
                        approximate_grid = generation_box.grid_flow(
                            row_major=True,
                            columns=2,
                            even_columns=True,
                            align=True,
                        )
                        approximate_grid.prop(layer, "ao_approx_distance")
                        approximate_grid.prop(layer, "ao_approx_strength")
                        approximate_grid.prop(layer, "ao_approx_falloff")
                        approximate_grid.prop(layer, "ao_approx_resolution")
                        approximate_grid.prop(layer, "ao_approx_subdivision")
                        approximate_grid.prop(layer, "ao_approx_neighbors")
                        approximate_grid.prop(layer, "ao_approx_smooth")
                        generation_box.prop(
                            layer,
                            "ao_approx_self_only",
                            toggle=True,
                        )
                    elif layer.blend_mode != 'HEIGHT' and layer.layer_type == 'COLOR_ATTRIBUTE':
                        attribute_name = get_active_color_attribute_name(obj)
                        generation_box.label(
                            text=(
                                f"Active Attribute: {attribute_name}"
                                if attribute_name
                                else "No active Color Attribute"
                            ),
                            icon='GROUP_VCOL',
                        )
                    elif layer.blend_mode != 'HEIGHT' and layer.layer_type == 'SHARP_EDGES':
                        generation_box.prop(
                            layer,
                            "edge_marker",
                            text="Marker",
                        )
                        sharp_grid = generation_box.grid_flow(
                            row_major=True,
                            columns=2,
                            even_columns=True,
                            align=True,
                        )
                        sharp_grid.prop(layer, "sharp_thickness")
                        sharp_grid.prop(layer, "sharp_opacity")
                        sharp_grid.prop(layer, "sharp_hardness")
                        sharp_grid.prop(layer, "sharp_smooth", slider=True)
                        sharp_grid.prop(layer, "sharp_irregularity")
                    elif layer.blend_mode != 'HEIGHT' and layer.layer_type == 'CURVATURE':
                        generation_box.prop(layer, "curvature_mode")
                        angle_grid = generation_box.grid_flow(
                            row_major=True,
                            columns=2,
                            even_columns=True,
                            align=True,
                        )
                        angle_grid.prop(layer, "curvature_angle")
                        angle_grid.prop(layer, "curvature_falloff")
                        curvature_grid = generation_box.grid_flow(
                            row_major=True,
                            columns=2,
                            even_columns=True,
                            align=True,
                        )
                        curvature_grid.prop(layer, "curvature_thickness")
                        curvature_grid.prop(layer, "curvature_opacity")
                        curvature_grid.prop(layer, "curvature_hardness")
                        curvature_grid.prop(layer, "curvature_smooth", slider=True)
                        curvature_grid.prop(layer, "curvature_irregularity")
                    elif layer.blend_mode != 'HEIGHT' and layer.layer_type == 'LINEART':
                        generation_box.prop(
                            layer,
                            "lineart_object",
                            text="Guide",
                        )
                        guide_button = generation_box.row()
                        guide_button.scale_y = 1.2
                        guide_button.operator(
                            "tm.create_lineart_guide",
                            icon='GREASEPENCIL',
                        )
                        line_grid = generation_box.grid_flow(
                            row_major=True,
                            columns=2,
                            even_columns=True,
                            align=True,
                        )
                        line_grid.prop(layer, "lineart_thickness")
                        line_grid.prop(layer, "lineart_opacity")
                        line_grid.prop(layer, "lineart_hardness")
                        line_grid.prop(layer, "lineart_smooth", slider=True)
                        projection_grid = generation_box.grid_flow(
                            row_major=True,
                            columns=2,
                            even_columns=True,
                            align=True,
                        )
                        projection_grid.prop(layer, "lineart_sample_step")
                        projection_grid.prop(
                            layer,
                            "lineart_projection_distance",
                        )
                        generation_box.prop(
                            layer,
                            "lineart_break_uv_seams",
                            toggle=True,
                        )
                        if layer.lineart_break_uv_seams:
                            generation_box.prop(layer, "lineart_uv_jump")

                    generate_button = generation_box.row()
                    generate_button.scale_y = 1.4
                    generate_button.operator("tm.bake_layer", icon='RENDER_STILL')

        draw_object_bake_settings(layout, obj)


classes = (
    TM_Layer,
    TM_MaterialSettings,
    TM_ObjectBakeSettings,
    TM_OT_create_material,
    TM_OT_initialize_material,
    TM_OT_add_layer,
    TM_OT_remove_layer,
    TM_OT_move_layer,
    TM_OT_indent_layer,
    TM_OT_outdent_layer,
    TM_OT_create_lineart_guide,
    TM_OT_bake_layer,
    TM_OT_bake_object_albedo,
    TM_UL_layers,
    TM_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Material.tm_settings = bpy.props.PointerProperty(
        type=TM_MaterialSettings
    )
    bpy.types.Object.tm_bake_settings = bpy.props.PointerProperty(
        type=TM_ObjectBakeSettings
    )


def unregister():
    if hasattr(bpy.types.Object, "tm_bake_settings"):
        del bpy.types.Object.tm_bake_settings
    if hasattr(bpy.types.Material, "tm_settings"):
        del bpy.types.Material.tm_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
