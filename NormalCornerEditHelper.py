bl_info = {
    "name": "Normal Corner Edit Helper",
    "author": "AI",
    "version": (1, 2, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Tool > Normal Helper",
    "description": "Edit corner normals with sphere, arrow, or angular softening",
    "category": "Mesh",
}

import bpy
import bmesh
import math
from mathutils import Vector


# ------------------------------------------------------------
# Utils
# ------------------------------------------------------------

def get_selected_face_indices(obj):
    mesh = obj.data

    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
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
        if _angle_session is not None:
            self.report({'WARNING'}, 'Apply or cancel the active angle edit first')
            return {'CANCELLED'}

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
        if _angle_session is not None:
            self.report({'WARNING'}, 'Apply or cancel the active angle edit first')
            return {'CANCELLED'}

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
# Corner Angle Softening (third mode)
# ------------------------------------------------------------

# Temporary editing state, kept only while the angle preview is active.
# The original custom normals are never used as an input for repeated
# previews, so moving the slider back and forth does not accumulate errors.
_angle_session = None


def nh_compute_angle_targets(mesh, selected_faces):
    """Build corner targets from all incident faces, weighted by vertex angle.

    0 degrees = each polygon's own normal (hard/flat).
    Increasing degrees rotates each selected face-corner normal towards the
    angle-weighted average of all face normals connected to that vertex.
    Once the average normal is reached, further angle changes do nothing.

    This is deliberately PER CORNER: it can make a partially softened edge
    without adding polygons or changing geometry.
    """
    vertex_normal_sum = [Vector((0.0, 0.0, 0.0)) for _ in mesh.vertices]
    incident_count = [0 for _ in mesh.vertices]

    # Use the whole mesh for the target, not just the selected faces, so a
    # single selected face can soften towards its neighbouring faces.
    for polygon in mesh.polygons:
        face_normal = polygon.normal.copy()
        if face_normal.length_squared < 1e-14:
            continue
        face_normal.normalize()

        face_vertices = list(polygon.vertices)
        count = len(face_vertices)
        for i, vertex_idx in enumerate(face_vertices):
            p = mesh.vertices[vertex_idx].co
            a = mesh.vertices[face_vertices[(i - 1) % count]].co - p
            b = mesh.vertices[face_vertices[(i + 1) % count]].co - p
            if a.length_squared > 1e-14 and b.length_squared > 1e-14:
                weight = a.angle(b, 0.0)
            else:
                weight = 1.0
            vertex_normal_sum[vertex_idx] += face_normal * weight
            incident_count[vertex_idx] += 1

    avg = []
    for vertex_idx, summed in enumerate(vertex_normal_sum):
        if incident_count[vertex_idx] and summed.length_squared > 1e-14:
            avg.append(summed.normalized())
        else:
            avg.append(mesh.vertices[vertex_idx].normal.copy())

    selected = set(selected_faces)
    targets = []
    for polygon in mesh.polygons:
        if polygon.index not in selected:
            continue
        face_normal = polygon.normal.copy()
        if face_normal.length_squared < 1e-14:
            continue
        face_normal.normalize()

        for loop_index in polygon.loop_indices:
            vertex_idx = mesh.loops[loop_index].vertex_index
            target = avg[vertex_idx].copy()
            if target.length_squared < 1e-14:
                target = face_normal.copy()
            else:
                target.normalize()

            # Blender's tangent-space custom normals should stay within the
            # face-facing hemisphere. In unusually folded geometry, limit
            # the target to just under 90 degrees from the face normal.
            deviation = face_normal.angle(target, 0.0)
            max_deviation = math.radians(89.9)
            if deviation > max_deviation:
                tangent = target - face_normal * target.dot(face_normal)
                if tangent.length_squared > 1e-14:
                    tangent.normalize()
                    target = (
                        face_normal * math.cos(max_deviation)
                        + tangent * math.sin(max_deviation)
                    ).normalized()
                    deviation = max_deviation
                else:
                    # Opposite vectors have no well-defined rotation axis.
                    target = face_normal.copy()
                    deviation = 0.0

            targets.append((loop_index, face_normal, target, deviation))

    return targets


def nh_angle_session_is_valid():
    if _angle_session is None:
        return False
    obj = bpy.data.objects.get(_angle_session['object_name'])
    return (
        obj is not None
        and obj.type == 'MESH'
        and obj.as_pointer() == _angle_session['object_pointer']
        and obj.data.as_pointer() == _angle_session['mesh_pointer']
        and len(obj.data.loops) == len(_angle_session['original_normals'])
    )


def nh_preview_corner_angle(angle_radians):
    """Preview the requested angular deflection (radians), from baseline."""
    session = _angle_session
    if session is None or not nh_angle_session_is_valid():
        return
    if session['updating']:
        return

    session['updating'] = True
    try:
        mesh = bpy.data.objects[session['object_name']].data
        normals = [normal.copy() for normal in session['original_normals']]
        angle_radians = max(0.0, min(angle_radians, math.pi / 2.0))

        # Exactly 0 = flat face normals; at >= face-to-average deviation,
        # the selected corner becomes fully smoothed.
        for loop_index, face_normal, target, deviation in session['targets']:
            if deviation < 1e-7 or angle_radians <= 1e-7:
                normal = face_normal
            elif angle_radians >= deviation:
                normal = target
            else:
                normal = face_normal.slerp(target, angle_radians / deviation)
            normals[loop_index] = normal.copy()

        for face_index in session['selected_faces']:
            mesh.polygons[face_index].use_smooth = True

        mesh.normals_split_custom_set(normals)
        mesh.update()
    finally:
        session['updating'] = False


def nh_angle_property_changed(scene, context):
    if _angle_session is None:
        return
    if scene.as_pointer() != _angle_session['scene_pointer']:
        return
    nh_preview_corner_angle(scene.normal_helper_corner_angle)


def nh_end_angle_session(context, cancel=False):
    global _angle_session
    session = _angle_session
    if session is None:
        return

    if nh_angle_session_is_valid():
        obj = bpy.data.objects[session['object_name']]
        if cancel:
            mesh = obj.data
            for index, was_smooth in session['face_smooth'].items():
                mesh.polygons[index].use_smooth = was_smooth
            mesh.normals_split_custom_set(session['original_normals'])
            mesh.update()
        else:
            nh_preview_corner_angle(context.scene.normal_helper_corner_angle)

        # Clear state BEFORE switching modes. Mode switching may internally
        # update the scene and the slider's UI.
        _angle_session = None
        if session['original_mode'] == 'EDIT' and obj.mode != 'EDIT':
            try:
                if context.view_layer.objects.active != obj:
                    context.view_layer.objects.active = obj
                obj.select_set(True)
                bpy.ops.object.mode_set(mode='EDIT')
            except RuntimeError:
                pass
    else:
        _angle_session = None


class NH_OT_start_corner_angle(bpy.types.Operator):
    bl_idname = 'mesh.nh_start_corner_angle'
    bl_label = 'Adjust Corner Angle'
    bl_description = 'Set how far selected corner normals tilt away from their face normals, with a live angle slider'
    bl_options = {'REGISTER'}

    def execute(self, context):
        global _angle_session
        if _angle_session is not None:
            self.report({'WARNING'}, 'Finish or cancel the active angle edit first')
            return {'CANCELLED'}
        if get_active_normal_helper() is not None:
            self.report({'WARNING'}, 'Finish the sphere/arrow helper first')
            return {'CANCELLED'}

        obj = context.object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, 'Select a mesh object')
            return {'CANCELLED'}

        selected = get_selected_face_indices(obj)
        if not selected:
            self.report({'ERROR'}, 'Select at least one face')
            return {'CANCELLED'}
        if obj.data.users > 1:
            self.report({'ERROR'}, 'Mesh data is shared; make it single-user first')
            return {'CANCELLED'}

        old_mode = obj.mode
        if old_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        mesh = obj.data
        original_normals = get_current_custom_normals(mesh)
        selected = sorted(set(selected))
        original_flags = {i: mesh.polygons[i].use_smooth for i in selected}
        targets = nh_compute_angle_targets(mesh, selected)
        if not targets:
            if old_mode == 'EDIT':
                bpy.ops.object.mode_set(mode='EDIT')
            self.report({'ERROR'}, 'No valid face normals found')
            return {'CANCELLED'}

        _angle_session = {
            'scene_pointer': context.scene.as_pointer(),
            'object_name': obj.name,
            'object_pointer': obj.as_pointer(),
            'mesh_pointer': mesh.as_pointer(),
            'original_mode': old_mode,
            'original_normals': original_normals,
            'face_smooth': original_flags,
            'selected_faces': selected,
            'targets': targets,
            'updating': False,
        }
        nh_preview_corner_angle(context.scene.normal_helper_corner_angle)
        self.report({'INFO'}, 'Adjust Angle in the Normal Helper panel, then Apply or Cancel')
        return {'FINISHED'}


class NH_OT_finish_corner_angle(bpy.types.Operator):
    bl_idname = 'mesh.nh_finish_corner_angle'
    bl_label = 'Finish Corner Angle'
    bl_description = 'Apply the angle preview or cancel and restore the previous normals'
    bl_options = {'REGISTER', 'UNDO'}

    cancel: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        if _angle_session is None:
            self.report({'WARNING'}, 'No angle edit is active')
            return {'CANCELLED'}
        was_cancelled = self.cancel
        nh_end_angle_session(context, cancel=was_cancelled)
        self.report({'INFO'}, 'Corner angle cancelled' if was_cancelled else 'Corner angle applied')
        return {'FINISHED'}


# ------------------------------------------------------------
# Extra Operators
# ------------------------------------------------------------

class NH_OT_clear_selected_normals(bpy.types.Operator):
    bl_idname = "mesh.nh_clear_selected_normals"
    bl_label = "Clear Selected Custom Normals"
    bl_description = "Clear custom normals on selected faces"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if _angle_session is not None:
            self.report({'WARNING'}, 'Apply or cancel the angle edit first')
            return {'CANCELLED'}

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
        col.enabled = (_angle_session is None)
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

        col.operator(
            "mesh.nh_start_corner_angle",
            text="Adjust Corner Angle",
            icon="MOD_SMOOTH"
        )

        if _angle_session is not None:
            layout.separator()
            box = layout.box()
            box.label(text="Corner Angle (Live Preview):")
            box.prop(
                context.scene,
                "normal_helper_corner_angle",
                text="Angle",
                slider=True
            )
            box.label(text="0 deg = Hard / Flat")
            box.label(text="Higher = Softer / Smooth")
            row = box.row(align=True)
            apply_op = row.operator(
                "mesh.nh_finish_corner_angle", text="Apply", icon="CHECKMARK"
            )
            apply_op.cancel = False
            cancel_op = row.operator(
                "mesh.nh_finish_corner_angle", text="Cancel", icon="CANCEL"
            )
            cancel_op.cancel = True

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
        row = layout.row()
        row.enabled = (_angle_session is None)
        row.operator("mesh.nh_clear_selected_normals", icon="X")


classes = (
    NH_OT_create_round_helper,
    NH_OT_create_flat_arrow_helper,
    NH_OT_start_corner_angle,
    NH_OT_finish_corner_angle,
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

    bpy.types.Scene.normal_helper_corner_angle = bpy.props.FloatProperty(
        name="Corner Normal Angle",
        description="Maximum angle the selected corner normal may deviate from its face normal (0 = flat; higher = smooth)",
        default=math.radians(30.0),
        min=0.0,
        max=math.radians(90.0),
        subtype='ANGLE',
        precision=1,
        update=nh_angle_property_changed
    )

    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    # Do not leave the mesh in a temporary preview state when reloading addon.
    if _angle_session is not None:
        nh_end_angle_session(bpy.context, cancel=True)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    if hasattr(bpy.types.Scene, "normal_helper_corner_angle"):
        del bpy.types.Scene.normal_helper_corner_angle
    if hasattr(bpy.types.Scene, "normal_helper_intensity"):
        del bpy.types.Scene.normal_helper_intensity


if __name__ == "__main__":
    register()