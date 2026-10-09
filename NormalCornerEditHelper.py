bl_info = {
    "name": "Normal Corner Edit Helper",
    "author": "AI",
    "version": (1, 1, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Tool > Normal Helper",
    "description": "Edit selected face corner normals using sphere or arrow helpers",
    "category": "Mesh",
}

import bpy
import bmesh
from mathutils import Vector


# ------------------------------------------------------------
# Utils
# ------------------------------------------------------------

def get_selected_face_indices(obj):
    mesh = obj.data

    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        return [f.index for f in bm.faces if f.select]

    return [p.index for p in mesh.polygons if p.select]


def get_current_custom_normals(mesh):
    if mesh.has_custom_normals:
        return [cn.vector.copy() for cn in mesh.corner_normals]

    return [Vector((0.0, 0.0, 0.0)) for _ in mesh.loops]


def get_visible_corner_normals(mesh):
    """
    Guarda as normals visíveis atuais para usar como base de blend.
    Diferente de get_current_custom_normals(), aqui não usamos zero,
    porque precisamos de uma normal real para misturar intensidade.
    """
    return [cn.vector.copy() for cn in mesh.corner_normals]


def selected_region_center_and_radius(obj, face_indices):
    mesh = obj.data
    mw = obj.matrix_world

    points = []

    for poly in mesh.polygons:
        if poly.index not in face_indices:
            continue

        for loop_index in poly.loop_indices:
            v_index = mesh.loops[loop_index].vertex_index
            points.append(mw @ mesh.vertices[v_index].co)

    if not points:
        return obj.location.copy(), 1.0

    center = Vector((0.0, 0.0, 0.0))

    for p in points:
        center += p

    center /= len(points)

    radius = max((p - center).length for p in points)
    radius = max(radius, 0.1)

    return center, radius


def get_active_normal_helper():
    for obj in bpy.context.scene.objects:
        if obj.get("normal_helper_active", False):
            return obj

    return None


def create_helper_sphere(center, radius, target_obj):
    bpy.ops.object.empty_add(
        type='SPHERE',
        location=center
    )

    helper = bpy.context.object
    helper.name = "NORMAL_HELPER_sphere"
    helper.empty_display_size = radius
    helper.show_in_front = True

    helper["normal_helper_target"] = target_obj.name
    helper["normal_helper_active"] = True
    helper["normal_helper_mode"] = "ROUND"

    return helper


def create_helper_arrow(center, radius, target_obj):
    bpy.ops.object.empty_add(
        type='SINGLE_ARROW',
        location=center
    )

    helper = bpy.context.object
    helper.name = "NORMAL_HELPER_arrow"
    helper.empty_display_size = radius
    helper.show_in_front = True

    helper["normal_helper_target"] = target_obj.name
    helper["normal_helper_active"] = True
    helper["normal_helper_mode"] = "FLAT_ARROW"

    return helper


def blend_normal(base_normal, target_normal, strength):
    strength = max(0.0, min(strength, 1.0))

    if base_normal.length < 0.000001:
        return target_normal.copy()

    if target_normal.length < 0.000001:
        return base_normal.copy()

    base = base_normal.normalized()
    target = target_normal.normalized()

    result = base.lerp(target, strength)

    if result.length < 0.000001:
        return target.copy()

    result.normalize()
    return result


def apply_round_helper_normals(obj, helper, face_indices, preserve_base_normals, blend_base_normals, strength):
    mesh = obj.data

    normals = [n.copy() for n in preserve_base_normals]

    obj_mw = obj.matrix_world
    obj_normal_inv = obj_mw.to_3x3().inverted()

    helper_mw = helper.matrix_world
    helper_inv = helper_mw.inverted()
    helper_normal_matrix = helper_inv.to_3x3().transposed()

    face_set = set(face_indices)

    for poly in mesh.polygons:
        if poly.index not in face_set:
            continue

        poly.use_smooth = True

        for loop_index in poly.loop_indices:
            loop = mesh.loops[loop_index]
            vert = mesh.vertices[loop.vertex_index]

            world_pos = obj_mw @ vert.co
            local_pos = helper_inv @ world_pos

            if local_pos.length < 0.000001:
                target_normal_world = world_pos - helper.location

                if target_normal_world.length < 0.000001:
                    target_normal_world = poly.normal.copy()
                else:
                    target_normal_world.normalize()
            else:
                target_normal_world = helper_normal_matrix @ local_pos

                if target_normal_world.length < 0.000001:
                    target_normal_world = poly.normal.copy()
                else:
                    target_normal_world.normalize()

            target_normal_obj = obj_normal_inv @ target_normal_world

            if target_normal_obj.length < 0.000001:
                target_normal_obj = poly.normal.copy()
            else:
                target_normal_obj.normalize()

            base_normal_obj = blend_base_normals[loop_index].copy()

            final_normal = blend_normal(
                base_normal_obj,
                target_normal_obj,
                strength
            )

            normals[loop_index] = final_normal

    mesh.normals_split_custom_set(normals)
    mesh.update()


def apply_arrow_helper_normals(obj, helper, face_indices, preserve_base_normals, blend_base_normals, strength):
    mesh = obj.data

    normals = [n.copy() for n in preserve_base_normals]

    obj_mw = obj.matrix_world
    obj_normal_inv = obj_mw.to_3x3().inverted()

    face_set = set(face_indices)

    # Direção da seta.
    # Empty SINGLE_ARROW usa eixo local Z como direção principal.
    target_normal_world = helper.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))

    if target_normal_world.length < 0.000001:
        target_normal_world = Vector((0.0, 0.0, 1.0))
    else:
        target_normal_world.normalize()

    target_normal_obj = obj_normal_inv @ target_normal_world

    if target_normal_obj.length < 0.000001:
        target_normal_obj = Vector((0.0, 0.0, 1.0))
    else:
        target_normal_obj.normalize()

    for poly in mesh.polygons:
        if poly.index not in face_set:
            continue

        poly.use_smooth = True

        for loop_index in poly.loop_indices:
            base_normal_obj = blend_base_normals[loop_index].copy()

            final_normal = blend_normal(
                base_normal_obj,
                target_normal_obj,
                strength
            )

            normals[loop_index] = final_normal

    mesh.normals_split_custom_set(normals)
    mesh.update()


def restore_normals(obj, original_normals):
    mesh = obj.data
    mesh.normals_split_custom_set(original_normals)
    mesh.update()


def delete_object(obj):
    if obj and obj.name in bpy.data.objects:
        obj["normal_helper_active"] = False
        bpy.data.objects.remove(obj, do_unlink=True)


# ------------------------------------------------------------
# Base Modal Operator
# ------------------------------------------------------------

class NH_OT_base_helper_modal:
    _timer = None
    target_obj_name = None
    helper_name = None
    selected_faces = None
    original_normals = None
    preserve_base_normals = None
    blend_base_normals = None
    helper_mode = "ROUND"

    def apply_current_helper(self, context):
        obj = bpy.data.objects.get(self.target_obj_name)
        helper = bpy.data.objects.get(self.helper_name)

        if obj is None or helper is None:
            return

        strength = context.scene.normal_helper_intensity

        if self.helper_mode == "ROUND":
            apply_round_helper_normals(
                obj,
                helper,
                self.selected_faces,
                self.preserve_base_normals,
                self.blend_base_normals,
                strength
            )

        elif self.helper_mode == "FLAT_ARROW":
            apply_arrow_helper_normals(
                obj,
                helper,
                self.selected_faces,
                self.preserve_base_normals,
                self.blend_base_normals,
                strength
            )

    def modal(self, context, event):
        obj = bpy.data.objects.get(self.target_obj_name)
        helper = bpy.data.objects.get(self.helper_name)

        if obj is None:
            self.cleanup_timer(context)
            return {'CANCELLED'}

        # Se o helper for deletado manualmente, mantém o resultado atual.
        if helper is None:
            self.cleanup_timer(context)
            self.report({'INFO'}, "Normal helper applied")
            return {'FINISHED'}

        if event.type == 'TIMER':
            self.apply_current_helper(context)
            return {'PASS_THROUGH'}

        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            self.apply_current_helper(context)
            delete_object(helper)
            self.cleanup_timer(context)
            self.report({'INFO'}, "Normal helper applied")
            return {'FINISHED'}

        if event.type == 'ESC' and event.value == 'PRESS':
            restore_normals(obj, self.original_normals)
            delete_object(helper)
            self.cleanup_timer(context)
            self.report({'INFO'}, "Normal helper cancelled")
            return {'CANCELLED'}

        if event.type == 'DEL' and event.value == 'PRESS':
            self.apply_current_helper(context)
            delete_object(helper)
            self.cleanup_timer(context)
            self.report({'INFO'}, "Normal helper applied")
            return {'FINISHED'}

        return {'PASS_THROUGH'}

    def cleanup_timer(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


# ------------------------------------------------------------
# Round Sphere Helper
# ------------------------------------------------------------

class NH_OT_create_round_helper(bpy.types.Operator, NH_OT_base_helper_modal):
    bl_idname = "mesh.nh_create_round_helper"
    bl_label = "Create Round Sphere Helper"
    bl_description = "Create a spherical helper to interactively control selected corner normals"
    bl_options = {'REGISTER', 'UNDO'}

    helper_mode = "ROUND"

    def execute(self, context):
        obj = context.object

        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object")
            return {'CANCELLED'}

        self.target_obj_name = obj.name
        self.selected_faces = get_selected_face_indices(obj)

        if not self.selected_faces:
            self.report({'ERROR'}, "Select at least one face")
            return {'CANCELLED'}

        original_mode = obj.mode

        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = obj.data

        self.original_normals = get_current_custom_normals(mesh)
        self.preserve_base_normals = [n.copy() for n in self.original_normals]
        self.blend_base_normals = get_visible_corner_normals(mesh)

        center, radius = selected_region_center_and_radius(obj, self.selected_faces)
        helper = create_helper_sphere(center, radius, obj)
        self.helper_name = helper.name

        context.scene.normal_helper_intensity = 1.0

        bpy.ops.object.select_all(action='DESELECT')
        helper.select_set(True)
        context.view_layer.objects.active = helper

        apply_round_helper_normals(
            obj,
            helper,
            self.selected_faces,
            self.preserve_base_normals,
            self.blend_base_normals,
            context.scene.normal_helper_intensity
        )

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)

        self.report({'INFO'}, "Move/Rotate/Scale sphere. Adjust Intensity in panel. Enter = Apply, Esc = Cancel, Delete = Apply")
        return {'RUNNING_MODAL'}


# ------------------------------------------------------------
# Flat Arrow Helper
# ------------------------------------------------------------

class NH_OT_create_flat_arrow_helper(bpy.types.Operator, NH_OT_base_helper_modal):
    bl_idname = "mesh.nh_create_flat_arrow_helper"
    bl_label = "Create Flat Arrow Helper"
    bl_description = "Create an arrow helper to point selected corner normals in one direction"
    bl_options = {'REGISTER', 'UNDO'}

    helper_mode = "FLAT_ARROW"

    def execute(self, context):
        obj = context.object

        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object")
            return {'CANCELLED'}

        self.target_obj_name = obj.name
        self.selected_faces = get_selected_face_indices(obj)

        if not self.selected_faces:
            self.report({'ERROR'}, "Select at least one face")
            return {'CANCELLED'}

        original_mode = obj.mode

        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = obj.data

        self.original_normals = get_current_custom_normals(mesh)
        self.preserve_base_normals = [n.copy() for n in self.original_normals]
        self.blend_base_normals = get_visible_corner_normals(mesh)

        center, radius = selected_region_center_and_radius(obj, self.selected_faces)
        helper = create_helper_arrow(center, radius, obj)
        self.helper_name = helper.name

        context.scene.normal_helper_intensity = 1.0

        bpy.ops.object.select_all(action='DESELECT')
        helper.select_set(True)
        context.view_layer.objects.active = helper

        apply_arrow_helper_normals(
            obj,
            helper,
            self.selected_faces,
            self.preserve_base_normals,
            self.blend_base_normals,
            context.scene.normal_helper_intensity
        )

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)

        self.report({'INFO'}, "Rotate arrow. Adjust Intensity in panel. Enter = Apply, Esc = Cancel, Delete = Apply")
        return {'RUNNING_MODAL'}


# ------------------------------------------------------------
# Extra Operators
# ------------------------------------------------------------

class NH_OT_clear_selected_normals(bpy.types.Operator):
    bl_idname = "mesh.nh_clear_selected_normals"
    bl_label = "Clear Selected Custom Normals"
    bl_description = "Clear custom normals on selected faces"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object

        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object")
            return {'CANCELLED'}

        selected_faces = get_selected_face_indices(obj)

        if not selected_faces:
            self.report({'ERROR'}, "Select at least one face")
            return {'CANCELLED'}

        original_mode = obj.mode

        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = obj.data
        normals = get_current_custom_normals(mesh)

        for poly in mesh.polygons:
            if poly.index not in selected_faces:
                continue

            for loop_index in poly.loop_indices:
                normals[loop_index] = Vector((0.0, 0.0, 0.0))

        mesh.normals_split_custom_set(normals)
        mesh.update()

        if original_mode == 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')

        self.report({'INFO'}, "Selected custom normals cleared")
        return {'FINISHED'}


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------

class NH_PT_panel(bpy.types.Panel):
    bl_label = "Normal Helper"
    bl_idname = "NH_PT_normal_helper"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"

    def draw(self, context):
        layout = self.layout

        active_helper = get_active_normal_helper()

        col = layout.column(align=True)
        col.label(text="Selected Faces:")

        col.operator(
            "mesh.nh_create_round_helper",
            text="Create Round Sphere Helper",
            icon="SPHERE"
        )

        col.operator(
            "mesh.nh_create_flat_arrow_helper",
            text="Create Flat Arrow Helper",
            icon="EMPTY_SINGLE_ARROW"
        )

        if active_helper:
            layout.separator()

            box = layout.box()
            box.label(text="Active Helper:")

            mode = active_helper.get("normal_helper_mode", "UNKNOWN")

            if mode == "ROUND":
                box.label(text="Mode: Round Sphere")
            elif mode == "FLAT_ARROW":
                box.label(text="Mode: Flat Arrow")
            else:
                box.label(text="Mode: Unknown")

            box.prop(
                context.scene,
                "normal_helper_intensity",
                text="Intensity",
                slider=True
            )

        layout.separator()
        layout.operator("mesh.nh_clear_selected_normals", icon="X")


classes = (
    NH_OT_create_round_helper,
    NH_OT_create_flat_arrow_helper,
    NH_OT_clear_selected_normals,
    NH_PT_panel,
)


def register():
    bpy.types.Scene.normal_helper_intensity = bpy.props.FloatProperty(
        name="Normal Helper Intensity",
        description="Blend strength of the active normal helper",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    if hasattr(bpy.types.Scene, "normal_helper_intensity"):
        del bpy.types.Scene.normal_helper_intensity


if __name__ == "__main__":
    register()