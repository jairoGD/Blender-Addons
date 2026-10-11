bl_info = {
    "name": "Vertex Group Tools",
    "author": "",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Vertex Groups",
    "description": "Batch rename vertex groups and remove groups without weights",
    "category": "Object",
}

import bpy
from bpy.props import StringProperty, FloatProperty


# ---------------------------------------------------------
# Properties
# ---------------------------------------------------------

class VGToolsProperties(bpy.types.PropertyGroup):

    search_text: StringProperty(
        name="Find",
        description="Text to search for in Vertex Group names",
        default=""
    )

    replace_text: StringProperty(
        name="Replace",
        description="Text that will replace the searched text",
        default=""
    )

    weight_threshold: FloatProperty(
        name="Weight Threshold",
        description="Weights equal to or below this value are considered empty",
        default=0.000001,
        min=0.0,
        max=1.0,
        precision=6
    )


# ---------------------------------------------------------
# Rename Operator
# ---------------------------------------------------------

class VGTOOLS_OT_replace_names(bpy.types.Operator):
    bl_idname = "vgtools.replace_names"
    bl_label = "Replace in All Groups"
    bl_description = "Replace text in all Vertex Group names"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        obj = context.active_object

        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "Select a mesh object")
            return {'CANCELLED'}

        props = context.scene.vg_tools

        search = props.search_text
        replacement = props.replace_text

        if search == "":
            self.report({'WARNING'}, "Find cannot be empty")
            return {'CANCELLED'}

        renamed_count = 0

        for group in obj.vertex_groups:

            if search in group.name:
                old_name = group.name
                new_name = old_name.replace(search, replacement)

                group.name = new_name
                renamed_count += 1

        self.report(
            {'INFO'},
            f"Renamed {renamed_count} Vertex Group(s)"
        )

        return {'FINISHED'}


# ---------------------------------------------------------
# Delete Empty Groups
# ---------------------------------------------------------

class VGTOOLS_OT_delete_empty_groups(bpy.types.Operator):
    bl_idname = "vgtools.delete_empty_groups"
    bl_label = "Delete Empty Groups"
    bl_description = "Delete Vertex Groups that have no vertices with weight"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        obj = context.active_object

        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "Select a mesh object")
            return {'CANCELLED'}

        props = context.scene.vg_tools
        threshold = props.weight_threshold

        mesh = obj.data

        # IDs dos grupos que possuem pelo menos um peso válido
        groups_with_weight = set()

        for vertex in mesh.vertices:

            for group_element in vertex.groups:

                if group_element.weight > threshold:
                    groups_with_weight.add(group_element.group)

        # Precisamos guardar os objetos dos grupos antes de remover
        empty_groups = []

        for group in obj.vertex_groups:

            if group.index not in groups_with_weight:
                empty_groups.append(group)

        empty_count = len(empty_groups)

        for group in empty_groups:
            obj.vertex_groups.remove(group)

        self.report(
            {'INFO'},
            f"Deleted {empty_count} empty Vertex Group(s)"
        )

        return {'FINISHED'}


# ---------------------------------------------------------
# Panel
# ---------------------------------------------------------

class VGTOOLS_PT_panel(bpy.types.Panel):
    bl_label = "Vertex Group Tools"
    bl_idname = "VGTOOLS_PT_panel"

    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Vertex Groups"

    def draw(self, context):

        layout = self.layout
        props = context.scene.vg_tools

        obj = context.active_object

        # -------------------------------------------------
        # Rename
        # -------------------------------------------------

        box = layout.box()
        box.label(text="Batch Rename")

        box.prop(props, "search_text")
        box.prop(props, "replace_text")

        box.operator(
            "vgtools.replace_names",
            icon='SORTALPHA'
        )

        # -------------------------------------------------
        # Empty Groups
        # -------------------------------------------------

        box = layout.box()
        box.label(text="Remove Empty Groups")

        box.prop(props, "weight_threshold")

        box.operator(
            "vgtools.delete_empty_groups",
            icon='TRASH'
        )

        # -------------------------------------------------
        # Info
        # -------------------------------------------------

        if obj and obj.type == 'MESH':

            layout.separator()

            layout.label(
                text=f"Groups: {len(obj.vertex_groups)}",
                icon='GROUP_VERTEX'
            )


# ---------------------------------------------------------
# Register
# ---------------------------------------------------------

classes = (
    VGToolsProperties,
    VGTOOLS_OT_replace_names,
    VGTOOLS_OT_delete_empty_groups,
    VGTOOLS_PT_panel,
)


def register():

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.vg_tools = bpy.props.PointerProperty(
        type=VGToolsProperties
    )


def unregister():

    del bpy.types.Scene.vg_tools

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()