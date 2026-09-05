bl_info = {
    "name": "XP Curve for Grease Pencil",
    "author": "Jairo + ChatGPT",
    "version": (1, 8, 0),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > XP Curve",
    "description": "Windows XP Paint-style curve tool for Blender Grease Pencil",
    "category": "Grease Pencil",
}

import bpy
import gpu
from mathutils import Vector
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader

# ------------------------------------------------------------
# Math / geometry
# ------------------------------------------------------------

def cubic_bezier(p0, c1, c2, p1, t):
    u = 1.0 - t
    return (u*u*u) * p0 + (3*u*u*t) * c1 + (3*u*t*t) * c2 + (t*t*t) * p1


def sample_cubic(p0, c1, c2, p1, steps):
    steps = max(4, int(steps))
    return [cubic_bezier(p0, c1, c2, p1, i / steps) for i in range(steps + 1)]


def mouse_to_world(context, x, y, depth_location=None):
    region = context.region
    rv3d = context.region_data
    coord = (x, y)
    if depth_location is None:
        obj = context.object
        depth_location = obj.matrix_world.translation if obj else Vector((0, 0, 0))
    return view3d_utils.region_2d_to_location_3d(region, rv3d, coord, depth_location)


def world_to_region(context, world):
    return view3d_utils.location_3d_to_region_2d(context.region, context.region_data, world)


def get_scene_settings(scene):
    return scene.xp_curve_gp_settings

# ------------------------------------------------------------
# Grease Pencil helpers: supports old GPENCIL and newer GREASEPENCIL when possible.
# ------------------------------------------------------------

def ensure_gp_object(context):
    obj = context.object
    if obj and obj.type in {'GPENCIL', 'GREASEPENCIL'}:
        return obj

    # Create a GP object. Blender 4.x uses grease_pencil_add; older versions use gpencil_add.
    try:
        bpy.ops.object.grease_pencil_add(type='EMPTY', location=(0, 0, 0))
    except Exception:
        bpy.ops.object.gpencil_add(type='EMPTY', location=(0, 0, 0))
    return context.object


def ensure_material(obj, color):
    mat_name = "XP Curve Stroke"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)

    # Old Grease Pencil material API.
    if not hasattr(mat, "grease_pencil") or mat.grease_pencil is None:
        try:
            bpy.data.materials.create_gpencil_data(mat)
        except Exception:
            pass

    if hasattr(mat, "grease_pencil") and mat.grease_pencil:
        try:
            mat.grease_pencil.color = color
            mat.grease_pencil.show_stroke = True
            mat.grease_pencil.show_fill = False
        except Exception:
            pass

    if mat.name not in [m.name for m in obj.data.materials]:
        obj.data.materials.append(mat)
    return list(obj.data.materials).index(mat)


def is_auto_keying_enabled(context=None):
    scene = (context or bpy.context).scene
    ts = getattr(scene, "tool_settings", None)
    return bool(getattr(ts, "use_keyframe_insert_auto", False))


def _find_frame_exact(layer, frame_number):
    frames = getattr(layer, "frames", None)
    if frames is None:
        return None

    try:
        fr = frames.get(frame_number)
        if fr is not None:
            return fr
    except Exception:
        pass

    try:
        for fr in frames:
            if getattr(fr, "frame_number", None) == frame_number:
                return fr
    except Exception:
        pass

    return None


def _new_frame_at(layer, frame_number):
    try:
        return layer.frames.new(frame_number)
    except TypeError:
        return layer.frames.new(frame_number=frame_number)


def ensure_layer_and_frame_old(gp_data, frame_number):
    layer = gp_data.layers.active if gp_data.layers else None
    if layer is None:
        layer = gp_data.layers.new("XP Curves", set_active=True)

    frame = _find_frame_exact(layer, frame_number)
    if frame is not None:
        return layer, frame

    frame = _new_frame_at(layer, frame_number)
    return layer, frame


def add_stroke_old_api(obj, points_local, width_px, color):
    gp = obj.data
    _, frame = ensure_layer_and_frame_old(gp, bpy.context.scene.frame_current)
    material_index = ensure_material(obj, color)

    stroke = frame.strokes.new()
    stroke.display_mode = '3DSPACE'
    stroke.line_width = int(max(1, round(width_px)))
    stroke.material_index = material_index
    try:
        stroke.start_cap_mode = 'ROUND'
        stroke.end_cap_mode = 'ROUND'
    except Exception:
        pass

    stroke.points.add(len(points_local) - 1)
    for p, co in zip(stroke.points, points_local):
        p.co = co
        p.pressure = 1.0
        p.strength = color[3]
        try:
            p.vertex_color = color
        except Exception:
            pass


def _new_gp_get_layer(gp_data):
    layer = getattr(gp_data.layers, "active", None)
    if layer is None:
        try:
            layer = gp_data.layers.new("XP Curves")
        except TypeError:
            layer = gp_data.layers.new(name="XP Curves")
    return layer


def _new_gp_get_frame(layer, frame_number, context=None):
    # current_frame()/active_frame may return the previous drawing when the
    # timeline is parked on an empty frame. When Auto Keying is on, create an
    # actual frame at frame_current, matching the normal Grease Pencil brush.
    exact = _find_frame_exact(layer, frame_number)
    if exact is not None:
        return exact

    if is_auto_keying_enabled(context):
        return _new_frame_at(layer, frame_number)

    for attr in ("current_frame", "active_frame"):
        f = getattr(layer, attr, None)
        if callable(f):
            try:
                fr = f()
                if fr:
                    return fr
            except Exception:
                pass
        elif f:
            return f

    return _new_frame_at(layer, frame_number)


def add_stroke_new_api(obj, points_local, width_px, color):
    gp = obj.data
    layer = _new_gp_get_layer(gp)
    frame = _new_gp_get_frame(layer, bpy.context.scene.frame_current, bpy.context)
    drawing = getattr(frame, "drawing", None)
    if drawing is None:
        raise RuntimeError("No drawing found in active Grease Pencil frame.")

    material_index = ensure_material(obj, color)
    n = len(points_local)

    # GPv3 API: drawing.add_strokes(sizes=[n]) then edit drawing.strokes[-1].
    if hasattr(drawing, "add_strokes"):
        try:
            drawing.add_strokes(sizes=[n])
        except TypeError:
            drawing.add_strokes([n])
        stroke = drawing.strokes[-1]
    else:
        # Some compatibility builds still expose old-like strokes.new().
        stroke = drawing.strokes.new()
        try:
            stroke.points.add(n - 1)
        except Exception:
            pass

    # Stroke-level material / cyclic settings when available.
    for attr, value in (("material_index", material_index), ("use_cyclic", False), ("cyclic", False)):
        try:
            setattr(stroke, attr, value)
        except Exception:
            pass

    # In GPv3, thickness is point radius, not old line_width.
    # This value is intentionally modest; use the panel slider to adjust.
    radius = max(0.001, float(width_px) * 0.005)

    for p, co in zip(stroke.points, points_local):
        # Different 4.x builds exposed either position or co-like properties.
        assigned = False
        for attr in ("position", "co"):
            if hasattr(p, attr):
                try:
                    setattr(p, attr, co)
                    assigned = True
                    break
                except Exception:
                    pass
        if not assigned:
            raise RuntimeError("Could not assign Grease Pencil point coordinates in this Blender build.")

        for attr, value in (("radius", radius), ("opacity", color[3]), ("strength", color[3]), ("vertex_color", color)):
            try:
                setattr(p, attr, value)
            except Exception:
                pass

    try:
        drawing.tag_positions_changed()
    except Exception:
        pass


def add_gp_curve(context, p0_world, p1_world, c1_world, c2_world):
    settings = get_scene_settings(context.scene)
    obj = ensure_gp_object(context)
    color = settings.color
    points_world = sample_cubic(p0_world, c1_world, c2_world, p1_world, settings.resolution)
    inv = obj.matrix_world.inverted()
    points_local = [inv @ p for p in points_world]

    if obj.type == 'GPENCIL':
        add_stroke_old_api(obj, points_local, settings.width, color)
    else:
        add_stroke_new_api(obj, points_local, settings.width, color)

    context.view_layer.update()
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()

# ------------------------------------------------------------
# Modal operator / preview
# ------------------------------------------------------------


def tag_all_view3d_redraw(context):
    screen = getattr(context, "screen", None)
    if not screen:
        return
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _point_in_region(region, event):
    return (
        region.x <= event.mouse_x < region.x + region.width and
        region.y <= event.mouse_y < region.y + region.height
    )


def event_inside_area_window(area, event):
    """Return True only when the mouse is inside the real viewport canvas.

    The modal operator stays alive globally, but it must not steal clicks from
    the sidebar, toolbar, header, menus, timeline, outliner, mode selector, etc.

    Some Blender layouts report the VIEW_3D WINDOW region behind/under UI
    regions. Because of that, reject the event first when the mouse is inside
    any non-WINDOW region of the same area. This fixes the bug where clicking
    the add-on button again starts a curve behind the sidebar instead of
    toggling the tool off.
    """
    if area is None:
        return False

    for region in area.regions:
        if region.type != 'WINDOW' and _point_in_region(region, event):
            return False

    for region in area.regions:
        if region.type == 'WINDOW' and _point_in_region(region, event):
            return True

    return False


def is_grease_pencil_draw_mode(context):
    obj = getattr(context, "object", None)
    if obj is None or obj.type not in {'GPENCIL', 'GREASEPENCIL'}:
        return False
    return getattr(obj, "mode", "") in {'PAINT_GPENCIL', 'PAINT_GREASE_PENCIL'}


def is_grease_pencil_edit_mode(context):
    obj = getattr(context, "object", None)
    if obj is None or obj.type not in {'GPENCIL', 'GREASEPENCIL'}:
        return False
    return getattr(obj, "mode", "") in {'EDIT_GPENCIL', 'EDIT_GREASE_PENCIL', 'EDIT'}

class XP_CURVE_GP_OT_draw(bpy.types.Operator):
    bl_idname = "gpencil.xp_curve_draw"
    bl_label = "XP Curve Draw"
    bl_description = "Draw a Windows XP Paint-style curve on the active Grease Pencil object"
    # Important: do NOT use REGISTER or UNDO here.
    # This operator is a persistent modal tool toggle; registering it makes
    # Blender put Enable/Disable XP Curve into the undo history. Then Ctrl+Z
    # can undo the tool activation instead of the last Grease Pencil stroke.
    bl_options = set()

    _active_instance = None
    _handle = None
    _stop_requested = False
    _area = None
    _region = None
    _paused_by_edit_mode = False
    mode = 'IDLE'  # IDLE, LINE, BEND1, BEND2
    p0 = None
    p1 = None
    c1 = None
    c2 = None
    mouse = None
    dragging = False
    depth_location = None

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        active = XP_CURVE_GP_OT_draw._active_instance
        if active is not None:
            # Toggle off, even if the tool is temporarily paused in GP Edit Mode.
            active._stop_requested = True
            tag_all_view3d_redraw(context)
            return {'FINISHED'}

        ensure_gp_object(context)
        self.mode = 'IDLE'
        self.p0 = self.p1 = self.c1 = self.c2 = self.mouse = None
        self.dragging = False
        self._stop_requested = False
        self._paused_by_edit_mode = False
        obj = context.object
        self.depth_location = obj.matrix_world.translation if obj else Vector((0, 0, 0))
        self._area = context.area
        self._region = context.region
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_preview, (context,), 'WINDOW', 'POST_PIXEL')
        XP_CURVE_GP_OT_draw._active_instance = self
        context.window_manager.modal_handler_add(self)
        tag_all_view3d_redraw(context)
        return {'RUNNING_MODAL'}

    def reset_curve(self):
        self.mode = 'IDLE'
        self.p0 = self.p1 = self.c1 = self.c2 = self.mouse = None
        self.dragging = False

    def pause_for_edit_mode(self, context):
        self.reset_curve()
        self._paused_by_edit_mode = True
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        tag_all_view3d_redraw(context)

    def resume_from_edit_mode(self, context):
        if self._handle is None:
            self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_preview, (context,), 'WINDOW', 'POST_PIXEL')
        self._paused_by_edit_mode = False
        tag_all_view3d_redraw(context)

    def finish(self, context, cancelled=False):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        if XP_CURVE_GP_OT_draw._active_instance is self:
            XP_CURVE_GP_OT_draw._active_instance = None
        self._area = None
        self._region = None
        tag_all_view3d_redraw(context)
        return {'CANCELLED'} if cancelled else {'FINISHED'}

    def modal(self, context, event):
        tag_all_view3d_redraw(context)

        if self._stop_requested:
            return self.finish(context, cancelled=False)

        # Draw Mode: active. Grease Pencil Edit Mode: temporarily paused.
        # Any other mode/object: fully disabled.
        if is_grease_pencil_edit_mode(context):
            if not self._paused_by_edit_mode:
                self.pause_for_edit_mode(context)
            return {'PASS_THROUGH'}

        if is_grease_pencil_draw_mode(context):
            if self._paused_by_edit_mode:
                self.resume_from_edit_mode(context)
        else:
            return self.finish(context, cancelled=False)

        if event.type == 'ESC' and event.value == 'PRESS':
            return self.finish(context, cancelled=True)

        inside_view = event_inside_area_window(self._area, event)
        if not inside_view:
            return {'PASS_THROUGH'}

        # Behave like a normal Blender tool, not like a blocking mode.
        # Only consume the exact mouse events used to build the XP curve.
        # Everything else passes through: Ctrl+Z, Tab/Edit Mode, hotkeys,
        # viewport navigation, menus, etc.

        # RMB cancels only the unfinished curve. When idle, keep Blender's
        # default RMB behavior/context menu available.
        if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            if self.mode != 'IDLE':
                self.reset_curve()
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        # Do not steal modifier-key shortcuts such as Ctrl+Z, Ctrl+S,
        # Alt/OS key operations, etc. Shift alone is allowed so Shift+LMB
        # can still start a curve if your keymap sends it that way.
        if event.ctrl or event.alt or event.oskey:
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            pos = mouse_to_world(context, event.mouse_region_x, event.mouse_region_y, self.depth_location)
            self.dragging = True
            if self.mode == 'IDLE':
                self.p0 = pos
                self.p1 = pos
                self.mouse = pos
                self.mode = 'LINE'
            elif self.mode == 'BEND1':
                self.mouse = pos
                self.c1 = pos
            elif self.mode == 'BEND2':
                self.mouse = pos
                self.c2 = pos
            return {'RUNNING_MODAL'}

        # Mouse move is only consumed while a curve is active. Idle mousemove
        # passes through to keep normal hover/navigation/UI behavior.
        if event.type == 'MOUSEMOVE':
            if self.mode == 'IDLE':
                return {'PASS_THROUGH'}
            pos = mouse_to_world(context, event.mouse_region_x, event.mouse_region_y, self.depth_location)
            self.mouse = pos
            if self.dragging:
                if self.mode == 'LINE':
                    self.p1 = pos
                elif self.mode == 'BEND1':
                    self.c1 = pos
                elif self.mode == 'BEND2':
                    self.c2 = pos
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self.mode == 'IDLE':
                return {'PASS_THROUGH'}

            pos = mouse_to_world(context, event.mouse_region_x, event.mouse_region_y, self.depth_location)
            self.dragging = False
            if self.mode == 'LINE':
                self.p1 = pos
                self.mode = 'BEND1'
                return {'RUNNING_MODAL'}
            if self.mode == 'BEND1':
                self.c1 = pos
                self.mode = 'BEND2'
                return {'RUNNING_MODAL'}
            if self.mode == 'BEND2':
                self.c2 = pos
                try:
                    add_gp_curve(context, self.p0, self.p1, self.c1, self.c2)
                    # Only the actual stroke creation gets an undo point.
                    # Tool enable/disable is excluded by bl_options = set().
                    try:
                        bpy.ops.ed.undo_push(message="XP Curve Stroke")
                    except Exception:
                        pass
                except Exception as ex:
                    self.report({'ERROR'}, str(ex))
                    return self.finish(context, cancelled=True)

                self.reset_curve()
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def draw_preview(self, context):
        if self.mode == 'IDLE' or self.p0 is None:
            return

        settings = get_scene_settings(context.scene)
        color = settings.color
        coords = []

        if self.mode == 'LINE':
            if self.p1 is None:
                return
            coords = [world_to_region(context, self.p0), world_to_region(context, self.p1)]
        elif self.mode == 'BEND1':
            c = self.c1 or self.mouse or self.p1
            pts = sample_cubic(self.p0, c, c, self.p1, settings.resolution)
            coords = [world_to_region(context, p) for p in pts]
        elif self.mode == 'BEND2':
            c2 = self.c2 or self.mouse or self.p1
            pts = sample_cubic(self.p0, self.c1 or self.p1, c2, self.p1, settings.resolution)
            coords = [world_to_region(context, p) for p in pts]

        coords = [c for c in coords if c is not None]
        if len(coords) < 2:
            return

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
        gpu.state.blend_set('ALPHA')
        gpu.state.line_width_set(max(1.0, settings.width))
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')

# ------------------------------------------------------------
# UI
# ------------------------------------------------------------

class XP_CURVE_GP_Settings(bpy.types.PropertyGroup):
    width: bpy.props.FloatProperty(
        name="Width",
        default=3.0,
        min=0.2,
        max=100.0,
        description="Preview width and stroke thickness"
    )
    resolution: bpy.props.IntProperty(
        name="Resolution",
        default=32,
        min=4,
        max=256,
        description="Number of segments used to bake the cubic curve into a Grease Pencil stroke"
    )
    color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.0, 0.0, 0.0, 1.0)
    )
    is_active: bpy.props.BoolProperty(
        name="Active",
        default=False,
        options={'HIDDEN', 'SKIP_SAVE'}
    )


class XP_CURVE_GP_PT_panel(bpy.types.Panel):
    bl_label = "XP Curve"
    bl_idname = "XP_CURVE_GP_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "XP Curve"

    def draw(self, context):
        layout = self.layout
        settings = get_scene_settings(context.scene)
        col = layout.column(align=True)
        active = XP_CURVE_GP_OT_draw._active_instance
        is_active = active is not None
        is_paused = bool(active and active._paused_by_edit_mode)
        if is_paused:
            label = "Disable XP Curve (Paused)"
            icon = 'PAUSE'
        else:
            label = "Disable XP Curve" if is_active else "Enable XP Curve"
            icon = 'CANCEL' if is_active else 'GREASEPENCIL'
        col.operator("gpencil.xp_curve_draw", text=label, icon=icon)
        col.prop(settings, "width")
        col.prop(settings, "resolution")
        col.prop(settings, "color")


classes = (
    XP_CURVE_GP_Settings,
    XP_CURVE_GP_OT_draw,
    XP_CURVE_GP_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.xp_curve_gp_settings = bpy.props.PointerProperty(type=XP_CURVE_GP_Settings)


def unregister():
    if hasattr(bpy.types.Scene, "xp_curve_gp_settings"):
        del bpy.types.Scene.xp_curve_gp_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
