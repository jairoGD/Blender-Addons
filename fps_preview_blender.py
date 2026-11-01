bl_info = {
    "name": "FPS Scene Preview",
    "author": "OpenAI",
    "version": (1, 1, 0),
    "blender": (4, 1, 0),
    "location": "View3D > Sidebar (N) > FPS Preview",
    "description": "A first-person walk/fly preview inside the Blender viewport",
    "category": "3D View",
}

import bpy
import math
import time
from mathutils import Vector
from bpy.props import BoolProperty, FloatProperty

ROOT_NAME = "FPS_Preview_Player"
CAM_NAME = "FPS_Preview_Camera"
BODY_NAME = "FPS_Preview_Capsule"
COLLECTION_NAME = "FPS PREVIEW (helper objects)"
PLAYER_HEIGHT = 1.80
PLAYER_RADIUS = 0.34
GRAVITY = 17.0
JUMP_SPEED = 5.5
SKIN = 0.018
WALKABLE_NORMAL_Z = 0.67
MAX_PHYSICS_DT = 1.0 / 60.0



def find_rig():
    root = bpy.data.objects.get(ROOT_NAME)
    cam = bpy.data.objects.get(CAM_NAME)
    body = bpy.data.objects.get(BODY_NAME)
    if root is None or cam is None or body is None:
        return None, None, None
    if cam.parent != root or body.parent != root:
        return None, None, None
    return root, cam, body


def make_capsule_mesh(name, radius=PLAYER_RADIUS, height=PLAYER_HEIGHT, sides=12):
    """Single low-poly capsule, bottom at local Z=0."""
    verts = [(0.0, 0.0, 0.0)]
    rings = []
    # Bottom hemisphere, cylinder, and upper hemisphere.
    for i in range(1, 6):
        theta = -math.pi / 2.0 + (math.pi / 2.0) * (i / 5.0)
        rings.append((radius + radius * math.sin(theta), radius * math.cos(theta)))
    rings.append((height - radius, radius))
    for i in range(1, 5):
        theta = (math.pi / 2.0) * (i / 5.0)
        rings.append((height - radius + radius * math.sin(theta), radius * math.cos(theta)))
    for z, r in rings:
        for i in range(sides):
            a = 2.0 * math.pi * i / sides
            verts.append((r * math.cos(a), r * math.sin(a), z))
    top_index = len(verts)
    verts.append((0.0, 0.0, height))

    faces = []
    for i in range(sides):
        faces.append((0, 1 + (i + 1) % sides, 1 + i))
    for ring_idx in range(len(rings) - 1):
        a0 = 1 + ring_idx * sides
        b0 = a0 + sides
        for i in range(sides):
            j = (i + 1) % sides
            faces.append((a0 + i, a0 + j, b0 + j, b0 + i))
    last_start = 1 + (len(rings) - 1) * sides
    for i in range(sides):
        faces.append((last_start + i, last_start + (i + 1) % sides, top_index))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


class FPSPREVIEW_OT_create(bpy.types.Operator):
    bl_idname = "fps_preview.create"
    bl_label = "Criar capsula + camera"
    bl_description = "Create the preview character at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root, camera, body = find_rig()
        if root is not None:
            context.scene.camera = camera
            self.report({'INFO'}, "Rig ja existe. Use 'Mover para cursor' ou 'Jogar'.")
            return {'FINISHED'}

        coll = bpy.data.collections.get(COLLECTION_NAME)
        if coll is None:
            coll = bpy.data.collections.new(COLLECTION_NAME)
            context.scene.collection.children.link(coll)

        root = bpy.data.objects.new(ROOT_NAME, None)
        root.empty_display_type = 'SPHERE'
        root.empty_display_size = 0.22
        root.location = context.scene.cursor.location.copy()
        coll.objects.link(root)
        root['fps_preview_rig'] = True

        body = bpy.data.objects.new(BODY_NAME, make_capsule_mesh("FPS_Capsule_Mesh"))
        coll.objects.link(body)
        body.parent = root
        body.location = (0.0, 0.0, 0.0)
        body.display_type = 'WIRE'
        body.show_in_front = True
        body.hide_render = True

        camera_data = bpy.data.cameras.new("FPS_Camera_Data")
        camera_data.lens = 19.0
        camera_data.clip_start = 0.04
        camera_data.clip_end = 2000.0
        camera = bpy.data.objects.new(CAM_NAME, camera_data)
        coll.objects.link(camera)
        camera.parent = root
        camera.location = (0.0, 0.0, context.scene.fps_eye_height)
        camera.rotation_euler = (math.pi / 2.0, 0.0, 0.0)
        context.scene.camera = camera
        self.report({'INFO'}, "Rig criado no Cursor 3D. Clique em 'Jogar / testar'.")
        return {'FINISHED'}


class FPSPREVIEW_OT_move_to_cursor(bpy.types.Operator):
    bl_idname = "fps_preview.move_to_cursor"
    bl_label = "Mover player para cursor"
    bl_description = "Place the capsule's feet at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root, _, _ = find_rig()
        if root is None:
            self.report({'WARNING'}, "Crie a capsula antes.")
            return {'CANCELLED'}
        root.location = context.scene.cursor.location.copy()
        root.rotation_euler = (0.0, 0.0, 0.0)
        self.report({'INFO'}, "Spawn movido para o Cursor 3D.")
        return {'FINISHED'}


class FPSPREVIEW_OT_play(bpy.types.Operator):
    bl_idname = "fps_preview.play"
    bl_label = "Jogar / testar FPS"
    bl_description = "Start real-time first-person navigation; ESC stops"

    _timer = None

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        self.root, self.camera, self.body = find_rig()
        if self.root is None:
            self.report({'WARNING'}, "Primeiro clique em 'Criar capsula + camera'.")
            return {'CANCELLED'}
        if context.region is None:
            self.report({'WARNING'}, "Execute no Viewport 3D.")
            return {'CANCELLED'}

        self.window = context.window
        self.area = context.area
        self.space = context.space_data
        self.view = self.space.region_3d
        self.scene = context.scene
        self.depsgraph = context.evaluated_depsgraph_get()
        self.window_region = next((r for r in self.area.regions if r.type == 'WINDOW'), None)
        if self.window_region is None:
            self.report({'WARNING'}, "Regiao WINDOW nao encontrada.")
            return {'CANCELLED'}

        self.previous_camera = self.scene.camera
        self.previous_perspective = self.view.view_perspective
        self.previous_zoom = self.view.view_camera_zoom
        self.previous_overlays = self.space.overlay.show_overlays
        self.previous_show_ui = self.space.show_region_ui
        self.previous_hide_set = self.body.hide_get()
        self.previous_hide_viewport = self.body.hide_viewport
        self.start_position = self.root.location.copy()
        self.start_yaw = self.root.rotation_euler.z
        self.start_pitch = self.camera.rotation_euler.x - math.pi / 2.0

        self.yaw = self.start_yaw
        self.pitch = self.start_pitch
        self.vz = 0.0
        self.grounded = False
        # Only consider the character grounded if its feet really touch a floor.
        floor = self._probe_floor(self.root.location, 0.14, 0.14)
        if floor is not None and abs(floor[0] - self.root.location.z) <= 0.13:
            self.root.location.z = floor[0]
            self.grounded = True
        self.fly_mode = False
        self.keys = set()
        self.last_time = time.perf_counter()
        self._timer = None
        self._mouse_ready = False

        self.scene.camera = self.camera
        self.body.hide_set(True)
        # Global viewport exclusion guarantees the helper mesh cannot
        # show up in physics ray casts (it is not the world collision mesh).
        self.body.hide_viewport = True
        self.view.view_perspective = 'CAMERA'
        self.view.view_camera_zoom = 42.0
        self.space.overlay.show_overlays = False
        self.space.show_region_ui = False
        self.camera.location.z = self.scene.fps_eye_height
        self._update_camera_orientation()
        self._refresh_header()
        self._center_mouse()
        self.window.cursor_modal_set('NONE')

        wm = context.window_manager
        self._timer = wm.event_timer_add(1.0 / 60.0, window=self.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _refresh_header(self):
        mode = 'VOO / NOCLIP' if self.fly_mode else 'ANDAR'
        collision = 'ON' if self.scene.fps_collision else 'OFF'
        self.area.header_text_set(
            f"FPS PREVIEW [{mode}]  |  WASD mover  |  Mouse olhar  |  "
            f"Shift correr  |  Space pular  |  F voo  |  E/Q subir/descer no voo  |  "
            f"G colisao {collision}  |  R resetar  |  ESC sair"
        )

    def _center_mouse(self):
        # cursor_warp takes coordinates in the Blender window.
        region = self.window_region
        self.cx = int(region.x + region.width * 0.5)
        self.cy = int(region.y + region.height * 0.5)
        self.window.cursor_warp(self.cx, self.cy)

    def _update_camera_orientation(self):
        self.root.rotation_euler.z = self.yaw
        self.camera.rotation_euler = (math.pi / 2.0 + self.pitch, 0.0, 0.0)

    # --------------------------------------------------------------
    # Collision helpers. World units here are metres if the scene is
    # modelled at 1 Blender unit = 1 metre.
    # --------------------------------------------------------------

    def _ray(self, origin, direction, distance):
        """Ray cast against evaluated visible scene geometry in world coordinates."""
        if distance < 1.0e-5:
            return None
        hit, point, normal, _face, obj, _matrix = self.scene.ray_cast(
            self.depsgraph, origin, direction, distance=distance
        )
        if hit and obj is not self.body and obj is not self.root and obj is not self.camera:
            return (point, normal, obj)
        return None

    def _probe_floor(self, pos, max_up, max_down):
        """Floor near the feet. Prefer centre over neighbouring edge contacts.

        'max_up' is how far ABOVE the feet the searched floor may be;
        'max_down' is how far BELOW them it may be. Never look 90 cm
        below an airborne player and then snap him back up!
        """
        max_up = max(0.0, max_up)
        max_down = max(0.0, max_down)
        top = max_up + 0.025
        ray_length = top + max_down + 0.025
        a = PLAYER_RADIUS * 0.86
        d = a * 0.70710678118
        probes = (
            (0.0, 0.0),
            (a, 0.0), (-a, 0.0), (0.0, a), (0.0, -a),
            (d, d), (d, -d), (-d, d), (-d, -d),
        )
        floors = []
        for idx, (ox, oy) in enumerate(probes):
            origin = Vector((pos.x + ox, pos.y + oy, pos.z + top))
            res = self._ray(origin, Vector((0.0, 0.0, -1.0)), ray_length)
            if res is None:
                continue
            point, normal, _obj = res
            if normal.z < WALKABLE_NORMAL_Z:
                continue
            if not (pos.z - max_down - 0.012 <= point.z <= pos.z + max_up + 0.012):
                continue
            # Closest surface to the feet wins. When climbing a stair,
            # the forward edge can be on its top before the centre is.
            floors.append((point.z, normal, idx))
        if floors:
            z, normal, _ = min(floors, key=lambda f: (abs(f[0] - pos.z), f[2]))
            return z, normal
        return None

    def _ceiling_clearance(self, pos, rise):
        """Allowed upward movement before the capsule hits a ceiling."""
        if rise <= 0.0:
            return 0.0
        permitted = rise
        offsets = (
            (0.0, 0.0),
            (PLAYER_RADIUS * 0.53, 0.0),
            (-PLAYER_RADIUS * 0.53, 0.0),
            (0.0, PLAYER_RADIUS * 0.53),
            (0.0, -PLAYER_RADIUS * 0.53),
        )
        # Start just below the top sphere, accounting for its curvature.
        for ox, oy in offsets:
            cap_drop = PLAYER_RADIUS - math.sqrt(max(0.0, PLAYER_RADIUS ** 2 - ox * ox - oy * oy))
            top_z = pos.z + PLAYER_HEIGHT - cap_drop - 0.045
            origin = Vector((pos.x + ox, pos.y + oy, top_z))
            res = self._ray(origin, Vector((0.0, 0.0, 1.0)), rise + 0.10)
            if res and res[1].z < -0.45:
                gap = res[0].z - top_z - 0.045
                permitted = min(permitted, max(0.0, gap))
        return permitted

    def _sweep_walls(self, pos, delta):
        """Approximate swept capsule using parallel offset horizontal rays.

        Returns (moved_position, collision_normal_or_None). This is not
        an exact convex sweep, but covers both sides of the body and
        several heights, unlike the previous centre-line-only cast.
        """
        length = delta.length
        if length <= 1.0e-8:
            return pos.copy(), None
        direction = delta / length
        side = Vector((-direction.y, direction.x, 0.0))
        best_length = length
        best_normal = None
        for z in (0.22, 0.60, 1.08, 1.55):
            # Three parallel rays sample the width of the capsule.
            for t in (-0.70, 0.0, 0.70):
                lateral = PLAYER_RADIUS * t
                radial_clearance = PLAYER_RADIUS * math.sqrt(1.0 - t * t)
                origin = pos + side * lateral + Vector((0.0, 0.0, z))
                res = self._ray(origin, direction, length + radial_clearance + SKIN)
                if res is None:
                    continue
                point, normal, _obj = res
                if normal.z >= WALKABLE_NORMAL_Z:
                    continue  # a walkable ramp, not a wall
                flat_normal = Vector((normal.x, normal.y, 0.0))
                if flat_normal.length_squared < 1.0e-7:
                    continue
                flat_normal.normalize()
                if flat_normal.dot(direction) >= -0.04:
                    continue  # face not opposing movement
                distance_along = (point - origin).dot(direction)
                allowance = max(0.0, distance_along - radial_clearance - SKIN)
                if allowance < best_length:
                    best_length = allowance
                    best_normal = flat_normal
        return pos + direction * best_length, best_normal

    def _try_step(self, at, horizontal_delta):
        """Move over a small riser by trying the sweep at an elevated height.

        A real stair is a series of low vertical faces; sweeping the capsule
        at its original feet height alone would block on each one.
        """
        step_height = self.scene.fps_step_height
        if step_height <= 0.0:
            return None
        # There must be enough room for the capsule to go up.
        if self._ceiling_clearance(at, step_height + SKIN) < step_height:
            return None
        lifted = at + Vector((0.0, 0.0, step_height + SKIN))
        candidate, normal = self._sweep_walls(lifted, horizontal_delta)
        if normal is not None:
            return None  # taller obstacle / low overhead / wall
        # Search DOWN at the destination: the top of the stair must actually
        # support the player, and it must be above their original feet.
        floor = self._probe_floor(candidate, 0.045, step_height + 0.09)
        if floor is None:
            return None
        target_z = floor[0]
        difference = target_z - at.z
        if difference < 0.025 or difference > step_height + 0.01:
            return None
        candidate.z = target_z
        return candidate

    def _move_horizontal(self, pos, delta, may_step):
        if delta.length_squared < 1.0e-12:
            return pos.copy()
        if not self.scene.fps_collision:
            return pos + delta

        moved, wall_normal = self._sweep_walls(pos, delta)
        if wall_normal is None:
            return moved

        if may_step:
            stepped = self._try_step(pos, delta)
            if stepped is not None:
                return stepped

        # Slide parallel to the wall instead of splitting movement along
        # global X and Y (which caused the jitter around corners).
        taken = moved - pos
        remainder = delta - taken
        projected = remainder - wall_normal * min(0.0, remainder.dot(wall_normal))
        if projected.length_squared > 1.0e-10:
            moved, _ = self._sweep_walls(moved, projected)
        return moved

    def _tick_physics(self, dt):
        speed = self.scene.fps_move_speed
        if 'LEFT_SHIFT' in self.keys or 'RIGHT_SHIFT' in self.keys:
            speed *= self.scene.fps_sprint_multiplier

        xi = int('D' in self.keys) - int('A' in self.keys)
        yi = int('W' in self.keys) - int('S' in self.keys)
        local = Vector((float(xi), float(yi), 0.0))
        if local.length_squared:
            local.normalize()
        cs, sn = math.cos(self.yaw), math.sin(self.yaw)
        delta = Vector((
            (local.x * cs - local.y * sn) * speed * dt,
            (local.x * sn + local.y * cs) * speed * dt,
            0.0,
        ))
        start = self.root.location.copy()
        if self.fly_mode:
            pos = start + delta
            if 'E' in self.keys or 'SPACE' in self.keys:
                pos.z += speed * dt
            if 'Q' in self.keys:
                pos.z -= speed * dt
            self.root.location = pos
            self.vz = 0.0
            self.grounded = False
            return
        if not self.scene.fps_collision:
            # Spectator walk at fixed elevation when physics are off.
            self.root.location = start + delta
            self.vz = 0.0
            self.grounded = False
            return

        was_grounded = self.grounded
        pos = self._move_horizontal(start, delta, may_step=was_grounded and self.vz <= 0.0)

        if was_grounded and self.vz <= 0.0:
            floor = self._probe_floor(
                pos, self.scene.fps_step_height + 0.05, self.scene.fps_ground_snap
            )
            if floor is not None:
                # Step up / follow down through ordinary stairs. Only players
                # that WERE grounded may use the comfortable stair snap.
                rise = floor[0] - start.z
                if rise <= self.scene.fps_step_height + 0.02:
                    pos.z = floor[0]
                    self.grounded = True
                    self.vz = 0.0
                    self.root.location = pos
                    return
            # Walked off a ledge: start falling normally.
            self.grounded = False
            self.vz = min(self.vz, 0.0)

        # Ballistic integration: z(t) = z0 + v0*t - 1/2*g*t^2.
        # NEVER snap down from the top of a jump just because a floor is
        # somewhere below: only land when the feet actually meet the floor.
        old_vz = self.vz
        new_vz = max(-24.0, old_vz - GRAVITY * dt)
        vertical_delta = (old_vz + new_vz) * 0.5 * dt
        old_z = pos.z
        predicted_z = old_z + vertical_delta

        if vertical_delta > 0.0:
            permitted = self._ceiling_clearance(pos, vertical_delta)
            if permitted < vertical_delta - 1.0e-5:
                predicted_z = old_z + permitted
                new_vz = 0.0
            # No ground test on the way up.
            self.grounded = False
        else:
            trial = pos.copy()
            trial.z = predicted_z
            floor = self._probe_floor(
                trial,
                max(0.055, old_z - predicted_z + 0.045),
                0.11,
            )
            if floor is not None and predicted_z <= floor[0] + 0.025:
                predicted_z = floor[0]
                new_vz = 0.0
                self.grounded = True
            else:
                self.grounded = False

        pos.z = predicted_z
        self.vz = new_vz
        self.root.location = pos

    def _tick(self, dt):
        """Sub-stepping prevents jumping over shallow steps on slow frames."""
        if dt <= 0.0:
            return
        num_steps = max(1, math.ceil(dt / MAX_PHYSICS_DT))
        step_dt = dt / num_steps
        for _ in range(num_steps):
            self._tick_physics(step_dt)

    def _stop(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        try:
            self.window.cursor_modal_restore()
        except Exception:
            pass
        self.area.header_text_set(None)
        self.space.overlay.show_overlays = self.previous_overlays
        self.space.show_region_ui = self.previous_show_ui
        self.view.view_camera_zoom = self.previous_zoom
        self.view.view_perspective = self.previous_perspective
        self.body.hide_viewport = self.previous_hide_viewport
        self.body.hide_set(self.previous_hide_set)
        # Leave the FPS camera selected as the scene camera for inspection.
        self.area.tag_redraw()

    def modal(self, context, event):
        try:
            return self._handle_event(context, event)
        except Exception as exc:
            # Do not leave the cursor hidden or viewport overrides in place.
            import traceback
            traceback.print_exc()
            try:
                self._stop(context)
            except Exception:
                traceback.print_exc()
            self.report({'ERROR'}, f"FPS Preview: {exc}")
            return {'CANCELLED'}

    def _handle_event(self, context, event):
        if event.type in {'ESC'} and event.value == 'PRESS':
            self._stop(context)
            return {'FINISHED'}
        if event.type == 'MOUSEMOVE':
            dx = event.mouse_x - self.cx
            dy = event.mouse_y - self.cy
            # Ignore synthetic warp events and large jumps from leaving the viewport.
            if abs(dx) <= 120 and abs(dy) <= 120:
                if dx != 0 or dy != 0:
                    sensitivity = self.scene.fps_mouse_sensitivity
                    self.yaw -= dx * sensitivity
                    self.pitch = max(-1.48, min(1.48, self.pitch + dy * sensitivity))
                    self._update_camera_orientation()
            self._center_mouse()
            self.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.value in {'PRESS', 'RELEASE'}:
            if event.type in {'W', 'A', 'S', 'D', 'LEFT_SHIFT', 'RIGHT_SHIFT', 'E', 'Q', 'SPACE'}:
                if event.value == 'PRESS':
                    self.keys.add(event.type)
                else:
                    self.keys.discard(event.type)
                if event.type == 'SPACE' and event.value == 'PRESS' and self.grounded and not self.fly_mode and self.scene.fps_collision:
                    self.vz = JUMP_SPEED
                    self.grounded = False
                return {'RUNNING_MODAL'}

            if event.value == 'PRESS':
                if event.type == 'F':
                    self.fly_mode = not self.fly_mode
                    self.vz = 0.0
                    self._refresh_header()
                    return {'RUNNING_MODAL'}
                if event.type == 'G':
                    self.scene.fps_collision = not self.scene.fps_collision
                    self.vz = 0.0
                    self._refresh_header()
                    return {'RUNNING_MODAL'}
                if event.type == 'R':
                    self.root.location = self.start_position.copy()
                    self.yaw = self.start_yaw
                    self.pitch = self.start_pitch
                    self.vz = 0.0
                    self.grounded = False
                    self._update_camera_orientation()
                    return {'RUNNING_MODAL'}

        # Blender Event has no `.timer` attribute. The timer event is
        # identified by its type; the timer object is used only for removal.
        if event.type == 'TIMER':
            now = time.perf_counter()
            dt = min(max(now - self.last_time, 0.0), 0.067)
            self.last_time = now
            self._tick(dt)
            self.area.tag_redraw()
            return {'RUNNING_MODAL'}

        # Prevent shortcuts, selections, and transforms while playing.
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._stop(context)


class FPSPREVIEW_PT_panel(bpy.types.Panel):
    bl_label = "FPS Scene Preview"
    bl_idname = "FPSPREVIEW_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FPS Preview"

    def draw(self, context):
        col = self.layout.column(align=True)
        root, cam, body = find_rig()
        if root is None:
            col.operator("fps_preview.create", text="1. Criar capsula + camera", icon='CAMERA_DATA')
        else:
            col.operator("fps_preview.play", text="Jogar / testar FPS", icon='PLAY')
            col.operator("fps_preview.move_to_cursor", text="Mover player para Cursor 3D", icon='PIVOT_CURSOR')
        col.separator()
        col.prop(context.scene, 'fps_move_speed', text='Velocidade')
        col.prop(context.scene, 'fps_sprint_multiplier', text='Multiplicador Shift')
        col.prop(context.scene, 'fps_mouse_sensitivity', text='Mouse')
        col.prop(context.scene, 'fps_eye_height', text='Altura olhos')
        col.prop(context.scene, 'fps_collision', text='Colisoes e gravidade')
        col.prop(context.scene, 'fps_step_height', text='Altura maxima degrau')
        col.prop(context.scene, 'fps_ground_snap', text='Acompanhar descidas')
        col.separator()
        col.label(text='WASD: mover | Mouse: olhar')
        col.label(text='Shift: correr | Espaco: pular')
        col.label(text='F: voo | E/Q: altura no voo')
        col.label(text='G: colisao | R: reset')
        col.label(text='ESC: sair (mantem posicao)')


classes = (
    FPSPREVIEW_OT_create,
    FPSPREVIEW_OT_move_to_cursor,
    FPSPREVIEW_OT_play,
    FPSPREVIEW_PT_panel,
)


def _unregister_previous_version():
    """Allow rerunning this .py from Blender's Text Editor without restart."""
    for cls in reversed(classes):
        base = bpy.types.Panel if issubclass(cls, bpy.types.Panel) else bpy.types.Operator
        previous = base.bl_rna_get_subclass_py(cls.__name__, None)
        if previous is None:
            previous = getattr(bpy.types, cls.__name__, None)
        if previous is not None and previous is not cls:
            try:
                bpy.utils.unregister_class(previous)
            except (RuntimeError, ValueError):
                pass


def register():
    _unregister_previous_version()
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.fps_move_speed = FloatProperty(
        name='Walk speed', default=3.4, min=0.1, max=25.0)
    bpy.types.Scene.fps_sprint_multiplier = FloatProperty(
        name='Sprint multiplier', default=2.0, min=1.0, max=5.0)
    bpy.types.Scene.fps_mouse_sensitivity = FloatProperty(
        name='Mouse sensitivity', default=0.003, min=0.0001, max=0.04, precision=4)
    bpy.types.Scene.fps_eye_height = FloatProperty(
        name='Eye height', default=1.62, min=0.3, max=2.0)
    bpy.types.Scene.fps_collision = BoolProperty(
        name='Collision and gravity', default=True)
    bpy.types.Scene.fps_step_height = FloatProperty(
        name='Maximum stair step height', default=0.43, min=0.0, max=1.0,
        subtype='DISTANCE', description='Max height of a stair to walk up automatically')
    bpy.types.Scene.fps_ground_snap = FloatProperty(
        name='Ground-follow distance', default=0.35, min=0.05, max=1.0,
        subtype='DISTANCE', description='Max height to smoothly follow stairs down')


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.fps_move_speed
    del bpy.types.Scene.fps_sprint_multiplier
    del bpy.types.Scene.fps_mouse_sensitivity
    del bpy.types.Scene.fps_eye_height
    del bpy.types.Scene.fps_collision
    del bpy.types.Scene.fps_step_height
    del bpy.types.Scene.fps_ground_snap


if __name__ == "__main__":
    register()
