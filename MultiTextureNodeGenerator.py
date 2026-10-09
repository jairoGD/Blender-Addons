
bl_info = {
    "name": "Multi Texture Node Generator",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "Properties > Material",
    "description": "Builds a node-based material that supports multiple texture inputs. Lets you easily add, remove, and update texture frames for frame-by-frame texture switching.",
    "category": "Material",
}

import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import PointerProperty, CollectionProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper


# --- Property for each image slot ---
class TF_ImageSlot(PropertyGroup):
    image: PointerProperty(type=bpy.types.Image)


# --- Custom properties stored per material ---
class TF_MaterialProps(PropertyGroup):
    images: CollectionProperty(type=TF_ImageSlot)
    active_index: IntProperty(default=0)


# --- Add image: open file browser and load image(s) ---
class TF_OT_AddImage(Operator, ImportHelper):
    bl_idname = "tf.add_image"
    bl_label = "Add texture"

    # allow multiple selection
    files: CollectionProperty(name="File Path", type=bpy.types.OperatorFileListElement)
    directory: StringProperty(subtype='DIR_PATH')

    filter_glob: StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tga;*.tif;*.tiff;*.exr;*.bmp",
        options={'HIDDEN'},
    )

    def execute(self, context):
        obj = context.object
        mat = obj.active_material
        props = mat.tf_props

        # user selected multiple files
        if self.files:
            for f in self.files:
                full_path = self.directory + f.name
                img = bpy.data.images.load(full_path, check_existing=True)
                slot = props.images.add()
                slot.image = img
            props.active_index = max(0, len(props.images) - 1)
        else:
            # single file
            img = bpy.data.images.load(self.filepath, check_existing=True)
            slot = props.images.add()
            slot.image = img
            props.active_index = max(0, len(props.images) - 1)

        return {'FINISHED'}


# --- Remove selected image slot ---
class TF_OT_RemoveImage(Operator):
    bl_idname = "tf.remove_image"
    bl_label = "Remove texture"

    index: IntProperty()

    def execute(self, context):
        mat = context.object.active_material
        props = mat.tf_props
        if 0 <= self.index < len(props.images):
            props.images.remove(self.index)
            props.active_index = max(0, props.active_index - 1)
        return {'FINISHED'}


# --- Build / rebuild node setup ---
class TF_OT_BuildNodes(Operator):
    bl_idname = "tf.build_nodes"
    bl_label = "Rebuild nodes"

    def execute(self, context):
        obj = context.object
        mat = obj.active_material
        props = mat.tf_props

        if not mat.use_nodes:
            mat.use_nodes = True

        nt = mat.node_tree
        nodes = nt.nodes
        links = nt.links

        # remove old TF_ nodes
        for n in list(nodes):
            if n.label.startswith("TF_"):
                nodes.remove(n)

        out = nodes.get("Material Output")
        if not out:
            out = nodes.new("ShaderNodeOutputMaterial")

        if len(props.images) == 0:
            return {'FINISHED'}

        frame_count = len(props.images)

        # value node to control which frame to show
        val = nodes.new("ShaderNodeValue")
        val.label = "TF_FrameValue"
        val.location = (-1200, 200)
        val.outputs[0].default_value = 0.0  # start at frame 0

        # multiply value by number of frames
        mult = nodes.new("ShaderNodeMath")
        mult.label = "TF_MUL"
        mult.operation = 'MULTIPLY'
        mult.inputs[1].default_value = frame_count
        mult.location = (-1000, 200)
        links.new(val.outputs[0], mult.inputs[0])

        # floor to get integer index
        flo = nodes.new("ShaderNodeMath")
        flo.label = "TF_FLOOR"
        flo.operation = 'FLOOR'
        flo.location = (-800, 200)
        links.new(mult.outputs[0], flo.inputs[0])

        prev_shader = None

        for i, slot in enumerate(props.images):
            if not slot.image:
                continue

            # image node
            img_node = nodes.new("ShaderNodeTexImage")
            img_node.label = f"TF_IMG_{i}"
            img_node.image = slot.image
            img_node.location = (-900, -200 * i)

            # base shader
            bsdf = nodes.new("ShaderNodeBsdfPrincipled")
            bsdf.label = f"TF_BSDF_{i}"
            bsdf.location = (-600, -200 * i)
            links.new(img_node.outputs["Color"], bsdf.inputs["Base Color"])
            links.new(img_node.outputs["Alpha"], bsdf.inputs["Alpha"])

            if prev_shader is None:
                prev_shader = bsdf
                continue

            # compare: current index == i ?
            cmpi = nodes.new("ShaderNodeMath")
            cmpi.label = f"TF_EQ_{i}"
            cmpi.operation = 'COMPARE'
            cmpi.inputs[1].default_value = float(i)
            cmpi.inputs[2].default_value = 0.1
            cmpi.location = (-800, 50 - 150 * i)
            links.new(flo.outputs[0], cmpi.inputs[0])

            # mix previous with this one
            mix = nodes.new("ShaderNodeMixShader")
            mix.label = f"TF_MIX_{i}"
            mix.location = (-300, -200 * i)
            links.new(cmpi.outputs[0], mix.inputs[0])
            links.new(prev_shader.outputs[0], mix.inputs[1])
            links.new(bsdf.outputs[0], mix.inputs[2])
            prev_shader = mix

        links.new(prev_shader.outputs[0], out.inputs["Surface"])
        return {'FINISHED'}


# --- UI Panel ---
class TF_PT_Panel(Panel):
    bl_label = "Texture Frames"
    bl_idname = "TF_PT_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.active_material

    def draw(self, context):
        layout = self.layout
        mat = context.object.active_material
        props = mat.tf_props

        row = layout.row()
        row.operator("tf.add_image", icon="ADD", text="Add Texture")

        # show loaded textures
        for i, slot in enumerate(props.images):
            row = layout.row(align=True)
            row.prop(slot, "image", text=f"{i}")
            op = row.operator("tf.remove_image", text="", icon="X")
            op.index = i

        layout.operator("tf.build_nodes", icon="FILE_REFRESH", text="Rebuild Nodes")


# --- Registration ---
classes = (
    TF_ImageSlot,
    TF_MaterialProps,
    TF_OT_AddImage,
    TF_OT_RemoveImage,
    TF_OT_BuildNodes,
    TF_PT_Panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Material.tf_props = PointerProperty(type=TF_MaterialProps)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Material.tf_props


if __name__ == "__main__":
    register()
