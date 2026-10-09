bl_info = {
    "name": "Fake Bone Physic",
    "author": "Jairo + ChatGPT",
    "version": (1, 8, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Tool > Fake Bone Physic",
    "description": "Fake bone physics using Damped Track delay, realtime spring simulation, action overshoot, pause loop and auto preview range",
    "category": "Animation",
}

import bpy
import math
from mathutils import Vector, Euler, Quaternion


CONSTRAINT_NAME = "Fake Bone Delay"
STATE_KEY = "fake_bone_physic_spring_state"
PAUSE_TIMER_KEY = "fake_bone_physic_pause_timer_running"
AUTO_RANGE_STATE_KEY = "fake_bone_physic_auto_range_state"
OVERSHOOT_STATE_KEY = "fake_bone_physic_overshoot_state"


# ============================================================
# UTILS
# ============================================================

def short_angle(a):
    while a > math.pi:
        a -= math.tau
    while a < -math.pi:
        a += math.tau
    return a


def quat_delta_to_vec(prev_q, curr_q):
    delta = prev_q.rotation_difference(curr_q)
    e = delta.to_euler("XYZ")

    return Vector((
        short_angle(e.x),
        short_angle(e.y),
        short_angle(e.z),
    ))


def bone_depth(pb):
    d = 0
    p = pb.parent

    while p:
        d += 1
        p = p.parent

    return d


def get_bone_local_quat(pb):
    if pb.rotation_mode == "QUATERNION":
        return pb.rotation_quaternion.copy()

    if pb.rotation_mode == "AXIS_ANGLE":
        angle = pb.rotation_axis_angle[0]
        axis = Vector((
            pb.rotation_axis_angle[1],
            pb.rotation_axis_angle[2],
            pb.rotation_axis_angle[3],
        ))

        if axis.length == 0:
            axis = Vector((0, 0, 1))

        return Quaternion(axis, angle)

    return pb.rotation_euler.to_quaternion()


def apply_quat_to_bone(pb, quat, original_mode):
    pb.rotation_mode = original_mode

    if original_mode == "QUATERNION":
        pb.rotation_quaternion = quat
        return

    if original_mode == "AXIS_ANGLE":
        axis, angle = quat.to_axis_angle()
        pb.rotation_axis_angle[0] = angle
        pb.rotation_axis_angle[1] = axis.x
        pb.rotation_axis_angle[2] = axis.y
        pb.rotation_axis_angle[3] = axis.z
        return

    pb.rotation_euler = quat.to_euler(original_mode)


def get_fcurve_value(action, data_path, index, frame, default):
    if not action:
        return default

    fc = action.fcurves.find(data_path, index=index)

    if not fc:
        return default

    return fc.evaluate(frame)


def get_action_bone_location(obj, pb, frame):
    action = obj.animation_data.action if obj.animation_data else None
    bone_name = pb.name
    path = f'pose.bones["{bone_name}"].location'

    x = get_fcurve_value(action, path, 0, frame, pb.location.x)
    y = get_fcurve_value(action, path, 1, frame, pb.location.y)
    z = get_fcurve_value(action, path, 2, frame, pb.location.z)

    return Vector((x, y, z))


def get_action_bone_quat(obj, pb, frame):
    action = obj.animation_data.action if obj.animation_data else None
    bone_name = pb.name
    mode = pb.rotation_mode

    if mode == "QUATERNION":
        path = f'pose.bones["{bone_name}"].rotation_quaternion'

        w = get_fcurve_value(action, path, 0, frame, pb.rotation_quaternion.w)
        x = get_fcurve_value(action, path, 1, frame, pb.rotation_quaternion.x)
        y = get_fcurve_value(action, path, 2, frame, pb.rotation_quaternion.y)
        z = get_fcurve_value(action, path, 3, frame, pb.rotation_quaternion.z)

        q = Quaternion((w, x, y, z))
        q.normalize()
        return q

    if mode == "AXIS_ANGLE":
        path = f'pose.bones["{bone_name}"].rotation_axis_angle'

        angle = get_fcurve_value(action, path, 0, frame, pb.rotation_axis_angle[0])
        x = get_fcurve_value(action, path, 1, frame, pb.rotation_axis_angle[1])
        y = get_fcurve_value(action, path, 2, frame, pb.rotation_axis_angle[2])
        z = get_fcurve_value(action, path, 3, frame, pb.rotation_axis_angle[3])

        axis = Vector((x, y, z))

        if axis.length == 0:
            axis = Vector((0, 0, 1))

        return Quaternion(axis.normalized(), angle)

    path = f'pose.bones["{bone_name}"].rotation_euler'

    x = get_fcurve_value(action, path, 0, frame, pb.rotation_euler.x)
    y = get_fcurve_value(action, path, 1, frame, pb.rotation_euler.y)
    z = get_fcurve_value(action, path, 2, frame, pb.rotation_euler.z)

    return Euler((x, y, z), mode).to_quaternion()


# ============================================================
# AUTO RANGE
# ============================================================

def get_active_action_frame_range(obj):
    if not obj:
        return None

    if not obj.animation_data:
        return None

    action = obj.animation_data.action

    if not action:
        return None

    frames = []

    for fc in action.fcurves:
        for key in fc.keyframe_points:
            frames.append(key.co.x)

    if not frames:
        return None

    start = int(math.floor(min(frames)))
    end = int(math.ceil(max(frames)))

    if start == end:
        end = start + 1

    return start, end, action.name


def apply_auto_range_from_object(scene, obj, jump_to_start=False):
    frame_range = get_active_action_frame_range(obj)

    if not frame_range:
        return False

    start, end, action_name = frame_range

    scene.use_preview_range = True
    scene.frame_preview_start = start
    scene.frame_preview_end = end

    if jump_to_start:
        scene.frame_set(start)

    bpy.app.driver_namespace[AUTO_RANGE_STATE_KEY] = {
        "object_name": obj.name if obj else "",
        "action_name": action_name,
        "start": start,
        "end": end,
    }

    return True


def update_auto_range(self, context):
    if self.auto_range:
        apply_auto_range_from_object(context.scene, context.object, jump_to_start=True)
    else:
        # Não limpa o preview range.
        # Só para de atualizar automaticamente.
        if AUTO_RANGE_STATE_KEY in bpy.app.driver_namespace:
            del bpy.app.driver_namespace[AUTO_RANGE_STATE_KEY]


def fake_bone_physic_auto_range_timer():
    scene = bpy.context.scene

    if not scene:
        return 0.25

    if not hasattr(scene, "fake_bone_physic_props"):
        return 0.25

    props = scene.fake_bone_physic_props

    if not props.auto_range:
        return 0.25

    obj = bpy.context.object

    if not obj:
        return 0.25

    frame_range = get_active_action_frame_range(obj)

    if not frame_range:
        return 0.25

    start, end, action_name = frame_range

    old = bpy.app.driver_namespace.get(AUTO_RANGE_STATE_KEY, {})

    changed = (
        old.get("object_name") != obj.name or
        old.get("action_name") != action_name or
        old.get("start") != start or
        old.get("end") != end
    )

    if changed:
        scene.use_preview_range = True
        scene.frame_preview_start = start
        scene.frame_preview_end = end

        bpy.app.driver_namespace[AUTO_RANGE_STATE_KEY] = {
            "object_name": obj.name,
            "action_name": action_name,
            "start": start,
            "end": end,
        }

    return 0.25


# ============================================================
# SPRING HANDLER
# ============================================================

def remove_spring_handler():
    handlers = bpy.app.handlers.frame_change_post

    for h in list(handlers):
        if getattr(h, "__name__", "") == "fake_bone_physic_spring_handler":
            handlers.remove(h)


def reset_spring_state(scene):
    state = bpy.app.driver_namespace.get(STATE_KEY)

    if not state:
        return

    obj = bpy.data.objects.get(state["object_name"])

    if not obj:
        return

    state["last_frame"] = None

    for bone_name in state["bones"]:
        pb = obj.pose.bones.get(bone_name)

        if not pb or not pb.parent:
            continue

        state["offsets"][bone_name] = Vector((0.0, 0.0, 0.0))
        state["velocities"][bone_name] = Vector((0.0, 0.0, 0.0))
        state["prev_parent_quats"][bone_name] = pb.parent.matrix.copy().to_quaternion()


def fake_bone_physic_spring_handler(scene):
    state = bpy.app.driver_namespace.get(STATE_KEY)

    if not state:
        return

    obj = bpy.data.objects.get(state["object_name"])

    if not obj:
        return

    current_frame = scene.frame_current
    last_frame = state.get("last_frame")

    if last_frame is None or current_frame <= last_frame or abs(current_frame - last_frame) > 2:
        reset_spring_state(scene)
        state["last_frame"] = current_frame
        return

    props = scene.fake_bone_physic_props

    inertia = props.inertia
    spring = props.spring
    damping = props.damping

    bones_sorted = sorted(
        state["bones"],
        key=lambda name: bone_depth(obj.pose.bones[name]) if obj.pose.bones.get(name) else 0
    )

    for bone_name in bones_sorted:
        pb = obj.pose.bones.get(bone_name)

        if not pb or not pb.parent:
            continue

        original_mode = state["rotation_modes"].get(bone_name, pb.rotation_mode)

        parent_q = pb.parent.matrix.copy().to_quaternion()
        prev_parent_q = state["prev_parent_quats"].get(bone_name, parent_q)

        parent_motion = quat_delta_to_vec(prev_parent_q, parent_q)

        offset = state["offsets"][bone_name]
        velocity = state["velocities"][bone_name]
        base_quat = state["base_quats"][bone_name]

        velocity -= parent_motion * inertia
        velocity += (-offset) * spring
        velocity *= damping
        offset += velocity

        offset_quat = Euler(offset, "XYZ").to_quaternion()
        final_quat = base_quat @ offset_quat

        apply_quat_to_bone(pb, final_quat, original_mode)

        state["offsets"][bone_name] = offset
        state["velocities"][bone_name] = velocity
        state["prev_parent_quats"][bone_name] = parent_q

    state["last_frame"] = current_frame


# ============================================================
# OVERSHOOT
# ============================================================

def remove_overshoot_handler():
    handlers = bpy.app.handlers.frame_change_post

    for h in list(handlers):
        if getattr(h, "__name__", "") == "fake_bone_physic_overshoot_handler":
            handlers.remove(h)


def reset_overshoot_state(scene):
    state = bpy.app.driver_namespace.get(OVERSHOOT_STATE_KEY)

    if not state:
        return

    obj = bpy.data.objects.get(state["object_name"])

    if not obj:
        return

    frame = scene.frame_current
    state["last_frame"] = None

    for bone_name in state["bones"]:
        pb = obj.pose.bones.get(bone_name)

        if not pb:
            continue

        base_q = get_action_bone_quat(obj, pb, frame)
        base_loc = get_action_bone_location(obj, pb, frame)

        state["prev_base_quats"][bone_name] = base_q
        state["prev_base_locs"][bone_name] = base_loc

        state["rot_offsets"][bone_name] = Vector((0.0, 0.0, 0.0))
        state["rot_velocities"][bone_name] = Vector((0.0, 0.0, 0.0))

        state["loc_offsets"][bone_name] = Vector((0.0, 0.0, 0.0))
        state["loc_velocities"][bone_name] = Vector((0.0, 0.0, 0.0))


def fake_bone_physic_overshoot_handler(scene):
    state = bpy.app.driver_namespace.get(OVERSHOOT_STATE_KEY)

    if not state:
        return

    obj = bpy.data.objects.get(state["object_name"])

    if not obj:
        return

    current_frame = scene.frame_current
    last_frame = state.get("last_frame")

    if last_frame is None or current_frame <= last_frame or abs(current_frame - last_frame) > 2:
        reset_overshoot_state(scene)
        state["last_frame"] = current_frame
        return

    props = scene.fake_bone_physic_props

    bones_sorted = sorted(
        state["bones"],
        key=lambda name: bone_depth(obj.pose.bones[name]) if obj.pose.bones.get(name) else 0
    )

    for bone_name in bones_sorted:
        pb = obj.pose.bones.get(bone_name)

        if not pb:
            continue

        original_mode = state["rotation_modes"].get(bone_name, pb.rotation_mode)

        base_q = get_action_bone_quat(obj, pb, current_frame)
        base_loc = get_action_bone_location(obj, pb, current_frame)

        prev_base_q = state["prev_base_quats"].get(bone_name, base_q)
        prev_base_loc = state["prev_base_locs"].get(bone_name, base_loc)

        # Rotation overshoot
        rot_motion = quat_delta_to_vec(prev_base_q, base_q)

        rot_offset = state["rot_offsets"][bone_name]
        rot_velocity = state["rot_velocities"][bone_name]

        rot_velocity += rot_motion * props.overshoot_rot_inertia
        rot_velocity += (-rot_offset) * props.overshoot_rot_spring
        rot_velocity *= props.overshoot_rot_damping

        rot_offset += rot_velocity

        offset_q = Euler(rot_offset, "XYZ").to_quaternion()
        final_q = base_q @ offset_q

        apply_quat_to_bone(pb, final_q, original_mode)

        # Location overshoot
        loc_motion = base_loc - prev_base_loc

        loc_offset = state["loc_offsets"][bone_name]
        loc_velocity = state["loc_velocities"][bone_name]

        loc_velocity += loc_motion * props.overshoot_loc_inertia
        loc_velocity += (-loc_offset) * props.overshoot_loc_spring
        loc_velocity *= props.overshoot_loc_damping

        loc_offset += loc_velocity

        pb.location = base_loc + loc_offset

        state["prev_base_quats"][bone_name] = base_q
        state["prev_base_locs"][bone_name] = base_loc

        state["rot_offsets"][bone_name] = rot_offset
        state["rot_velocities"][bone_name] = rot_velocity

        state["loc_offsets"][bone_name] = loc_offset
        state["loc_velocities"][bone_name] = loc_velocity

    state["last_frame"] = current_frame


# ============================================================
# PAUSE LOOP
# ============================================================

def remove_pause_loop_handler():
    handlers = bpy.app.handlers.frame_change_post

    for h in list(handlers):
        if getattr(h, "__name__", "") == "fake_bone_physic_pause_loop_handler":
            handlers.remove(h)


def get_timeline_end_frame(scene):
    if scene.use_preview_range:
        return scene.frame_preview_end
    return scene.frame_end


def stop_animation_timer():
    bpy.app.driver_namespace[PAUSE_TIMER_KEY] = False

    scene = bpy.context.scene

    if not scene:
        return None

    end_frame = get_timeline_end_frame(scene)

    if scene.frame_current != end_frame:
        scene.frame_set(end_frame)

    wm = bpy.context.window_manager

    if not wm:
        return None

    for window in wm.windows:
        screen = window.screen

        if not screen or not screen.is_animation_playing:
            continue

        area = None
        region = None

        for a in screen.areas:
            if a.type in {"VIEW_3D", "DOPESHEET_EDITOR", "GRAPH_EDITOR", "TIMELINE"}:
                area = a
                break

        if area:
            for r in area.regions:
                if r.type == "WINDOW":
                    region = r
                    break

        try:
            if area and region:
                with bpy.context.temp_override(window=window, screen=screen, area=area, region=region):
                    bpy.ops.screen.animation_cancel(restore_frame=False)
            else:
                with bpy.context.temp_override(window=window, screen=screen):
                    bpy.ops.screen.animation_cancel(restore_frame=False)
        except Exception:
            pass

    return None


def request_pause_animation():
    if bpy.app.driver_namespace.get(PAUSE_TIMER_KEY):
        return

    bpy.app.driver_namespace[PAUSE_TIMER_KEY] = True
    bpy.app.timers.register(stop_animation_timer, first_interval=0.01)


def fake_bone_physic_pause_loop_handler(scene):
    if not hasattr(scene, "fake_bone_physic_props"):
        return

    props = scene.fake_bone_physic_props

    if not props.pause_loop:
        return

    end_frame = get_timeline_end_frame(scene)

    if scene.frame_current >= end_frame:
        request_pause_animation()


# ============================================================
# PROPERTIES
# ============================================================

class FakeBonePhysicProperties(bpy.types.PropertyGroup):
    influence: bpy.props.FloatProperty(
        name="Delay Influence",
        default=0.7,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    inertia: bpy.props.FloatProperty(
        name="Inertia",
        default=0.7,
        min=0.0,
        max=10.0,
    )

    spring: bpy.props.FloatProperty(
        name="Spring",
        default=0.9,
        min=0.0,
        max=10.0,
    )

    damping: bpy.props.FloatProperty(
        name="Damping",
        default=0.74,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


    overshoot_rot_inertia: bpy.props.FloatProperty(
        name="Rot Inertia",
        default=1.0,
        min=0.0,
        max=10.0,
    )

    overshoot_rot_spring: bpy.props.FloatProperty(
        name="Rot Spring",
        default=0.45,
        min=0.0,
        max=10.0,
    )

    overshoot_rot_damping: bpy.props.FloatProperty(
        name="Rot Damping",
        default=0.78,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    overshoot_loc_inertia: bpy.props.FloatProperty(
        name="Loc Inertia",
        default=0.5,
        min=0.0,
        max=10.0,
    )

    overshoot_loc_spring: bpy.props.FloatProperty(
        name="Loc Spring",
        default=0.45,
        min=0.0,
        max=10.0,
    )

    overshoot_loc_damping: bpy.props.FloatProperty(
        name="Loc Damping",
        default=0.58,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    pause_loop: bpy.props.BoolProperty(
        name="Pause loop",
        default=False,
    )

    auto_range: bpy.props.BoolProperty(
        name="Auto Range",
        description="Automatically update preview range from first and last keyframe of active action",
        default=False,
        update=update_auto_range,
    )


# ============================================================
# DELAY
# ============================================================

class FAKEBONEPHYSIC_OT_set_delay(bpy.types.Operator):
    bl_idname = "fake_bone_physic.set_delay"
    bl_label = "Set Delay"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object

        if not obj or obj.type != "ARMATURE":
            self.report({"WARNING"}, "Select an armature")
            return {"CANCELLED"}

        if context.mode != "POSE":
            self.report({"WARNING"}, "Use Pose Mode")
            return {"CANCELLED"}

        selected_bones = context.selected_pose_bones

        if not selected_bones:
            self.report({"WARNING"}, "No selected bones")
            return {"CANCELLED"}

        influence_value = context.scene.fake_bone_physic_props.influence
        count = 0

        for pose_bone in selected_bones:
            bone = pose_bone.bone

            if not bone.children:
                continue

            child_bone = bone.children[0]

            constraint = pose_bone.constraints.get(CONSTRAINT_NAME)

            if not constraint:
                constraint = pose_bone.constraints.new(type="DAMPED_TRACK")
                constraint.name = CONSTRAINT_NAME

            constraint.target = obj
            constraint.subtarget = child_bone.name
            constraint.influence = influence_value

            count += 1

        if count == 0:
            self.report({"WARNING"}, "Selected bones have no child bones")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Delay applied to {count} bone(s)")
        return {"FINISHED"}


class FAKEBONEPHYSIC_OT_clear_delay(bpy.types.Operator):
    bl_idname = "fake_bone_physic.clear_delay"
    bl_label = "Clear Delay"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.mode != "POSE":
            self.report({"WARNING"}, "Use Pose Mode")
            return {"CANCELLED"}

        selected_bones = context.selected_pose_bones

        if not selected_bones:
            self.report({"WARNING"}, "No selected bones")
            return {"CANCELLED"}

        count = 0

        for pose_bone in selected_bones:
            for constraint in list(pose_bone.constraints):
                if constraint.type == "DAMPED_TRACK" and constraint.name == CONSTRAINT_NAME:
                    pose_bone.constraints.remove(constraint)
                    count += 1

        self.report({"INFO"}, f"Delay removed from {count} constraint(s)")
        return {"FINISHED"}


# ============================================================
# SPRING
# ============================================================

class FAKEBONEPHYSIC_OT_set_spring(bpy.types.Operator):
    bl_idname = "fake_bone_physic.set_spring"
    bl_label = "Set Spring"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object

        if not obj or obj.type != "ARMATURE":
            self.report({"WARNING"}, "Select an armature")
            return {"CANCELLED"}

        if context.mode != "POSE":
            self.report({"WARNING"}, "Use Pose Mode")
            return {"CANCELLED"}

        props = context.scene.fake_bone_physic_props

        if props.auto_range:
            if not apply_auto_range_from_object(context.scene, obj, jump_to_start=True):
                self.report({"WARNING"}, "No active action keyframes found for Auto Range")

        selected = [pb for pb in context.selected_pose_bones if pb.parent]

        if not selected:
            self.report({"WARNING"}, "Select bones that have parent bones")
            return {"CANCELLED"}

        remove_spring_handler()

        state = {
            "object_name": obj.name,
            "bones": [pb.name for pb in selected],
            "rotation_modes": {},
            "base_quats": {},
            "offsets": {},
            "velocities": {},
            "prev_parent_quats": {},
            "last_frame": None,
        }

        for pb in selected:
            state["rotation_modes"][pb.name] = pb.rotation_mode
            state["base_quats"][pb.name] = get_bone_local_quat(pb)
            state["offsets"][pb.name] = Vector((0.0, 0.0, 0.0))
            state["velocities"][pb.name] = Vector((0.0, 0.0, 0.0))
            state["prev_parent_quats"][pb.name] = pb.parent.matrix.copy().to_quaternion()

        bpy.app.driver_namespace[STATE_KEY] = state

        if fake_bone_physic_spring_handler not in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.append(fake_bone_physic_spring_handler)

        self.report({"INFO"}, f"Spring enabled on {len(selected)} bone(s)")
        return {"FINISHED"}


class FAKEBONEPHYSIC_OT_clear_spring(bpy.types.Operator):
    bl_idname = "fake_bone_physic.clear_spring"
    bl_label = "Clear Spring"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = bpy.app.driver_namespace.get(STATE_KEY)

        remove_spring_handler()

        if state:
            obj = bpy.data.objects.get(state.get("object_name", ""))

            if obj:
                for bone_name, mode in state.get("rotation_modes", {}).items():
                    pb = obj.pose.bones.get(bone_name)
                    if pb:
                        pb.rotation_mode = mode

        if STATE_KEY in bpy.app.driver_namespace:
            del bpy.app.driver_namespace[STATE_KEY]

        self.report({"INFO"}, "Spring disabled")
        return {"FINISHED"}


# ============================================================
# OVERSHOOT
# ============================================================

class FAKEBONEPHYSIC_OT_set_overshoot(bpy.types.Operator):
    bl_idname = "fake_bone_physic.set_overshoot"
    bl_label = "Set Overshoot"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object

        if not obj or obj.type != "ARMATURE":
            self.report({"WARNING"}, "Select an armature")
            return {"CANCELLED"}

        if context.mode != "POSE":
            self.report({"WARNING"}, "Use Pose Mode")
            return {"CANCELLED"}

        if not obj.animation_data or not obj.animation_data.action:
            self.report({"WARNING"}, "Object needs an active action")
            return {"CANCELLED"}

        props = context.scene.fake_bone_physic_props

        if props.auto_range:
            if not apply_auto_range_from_object(context.scene, obj, jump_to_start=True):
                self.report({"WARNING"}, "No active action keyframes found for Auto Range")

        selected = list(context.selected_pose_bones)

        if not selected:
            self.report({"WARNING"}, "No selected bones")
            return {"CANCELLED"}

        remove_overshoot_handler()

        state = {
            "object_name": obj.name,
            "bones": [pb.name for pb in selected],
            "rotation_modes": {},
            "prev_base_quats": {},
            "prev_base_locs": {},
            "rot_offsets": {},
            "rot_velocities": {},
            "loc_offsets": {},
            "loc_velocities": {},
            "last_frame": None,
        }

        frame = context.scene.frame_current

        for pb in selected:
            state["rotation_modes"][pb.name] = pb.rotation_mode
            state["prev_base_quats"][pb.name] = get_action_bone_quat(obj, pb, frame)
            state["prev_base_locs"][pb.name] = get_action_bone_location(obj, pb, frame)
            state["rot_offsets"][pb.name] = Vector((0.0, 0.0, 0.0))
            state["rot_velocities"][pb.name] = Vector((0.0, 0.0, 0.0))
            state["loc_offsets"][pb.name] = Vector((0.0, 0.0, 0.0))
            state["loc_velocities"][pb.name] = Vector((0.0, 0.0, 0.0))

        bpy.app.driver_namespace[OVERSHOOT_STATE_KEY] = state

        if fake_bone_physic_overshoot_handler not in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.append(fake_bone_physic_overshoot_handler)

        self.report({"INFO"}, f"Overshoot enabled on {len(selected)} bone(s)")
        return {"FINISHED"}


class FAKEBONEPHYSIC_OT_clear_overshoot(bpy.types.Operator):
    bl_idname = "fake_bone_physic.clear_overshoot"
    bl_label = "Clear Overshoot"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = bpy.app.driver_namespace.get(OVERSHOOT_STATE_KEY)

        remove_overshoot_handler()

        if state:
            obj = bpy.data.objects.get(state.get("object_name", ""))

            if obj:
                frame = context.scene.frame_current

                for bone_name, mode in state.get("rotation_modes", {}).items():
                    pb = obj.pose.bones.get(bone_name)
                    if pb:
                        pb.rotation_mode = mode

                        # Volta para a pose original da action no frame atual.
                        if obj.animation_data and obj.animation_data.action:
                            base_q = get_action_bone_quat(obj, pb, frame)
                            base_loc = get_action_bone_location(obj, pb, frame)
                            apply_quat_to_bone(pb, base_q, mode)
                            pb.location = base_loc

        if OVERSHOOT_STATE_KEY in bpy.app.driver_namespace:
            del bpy.app.driver_namespace[OVERSHOOT_STATE_KEY]

        self.report({"INFO"}, "Overshoot disabled")
        return {"FINISHED"}


# ============================================================
# UI
# ============================================================

class VIEW3D_PT_fake_bone_physic_panel(bpy.types.Panel):
    bl_label = "Fake Bone Physic"
    bl_idname = "VIEW3D_PT_fake_bone_physic_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tool"

    def draw(self, context):
        layout = self.layout
        props = context.scene.fake_bone_physic_props

        box = layout.box()
        box.label(text="Delay", icon="CONSTRAINT_BONE")
        box.prop(props, "influence")
        box.operator("fake_bone_physic.set_delay", icon="CONSTRAINT_BONE")
        box.operator("fake_bone_physic.clear_delay", icon="X")

        box = layout.box()
        box.label(text="Spring", icon="MOD_PHYSICS")
        box.prop(props, "inertia")
        box.prop(props, "spring")
        box.prop(props, "damping")
        box.operator("fake_bone_physic.set_spring", icon="PLAY")
        box.operator("fake_bone_physic.clear_spring", icon="X")


        box = layout.box()
        box.label(text="Overshoot", icon="IPO_BACK")
        box.prop(props, "overshoot_rot_inertia")
        box.prop(props, "overshoot_rot_spring")
        box.prop(props, "overshoot_rot_damping")
        box.separator()
        box.prop(props, "overshoot_loc_inertia")
        box.prop(props, "overshoot_loc_spring")
        box.prop(props, "overshoot_loc_damping")
        box.operator("fake_bone_physic.set_overshoot", icon="PLAY")
        box.operator("fake_bone_physic.clear_overshoot", icon="X")

        layout.separator()
        layout.prop(props, "pause_loop")
        layout.prop(props, "auto_range")


# ============================================================
# REGISTER
# ============================================================

classes = (
    FakeBonePhysicProperties,
    FAKEBONEPHYSIC_OT_set_delay,
    FAKEBONEPHYSIC_OT_clear_delay,
    FAKEBONEPHYSIC_OT_set_spring,
    FAKEBONEPHYSIC_OT_clear_spring,
    FAKEBONEPHYSIC_OT_set_overshoot,
    FAKEBONEPHYSIC_OT_clear_overshoot,
    VIEW3D_PT_fake_bone_physic_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.fake_bone_physic_props = bpy.props.PointerProperty(
        type=FakeBonePhysicProperties
    )

    if fake_bone_physic_pause_loop_handler not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(fake_bone_physic_pause_loop_handler)

    if not bpy.app.timers.is_registered(fake_bone_physic_auto_range_timer):
        bpy.app.timers.register(fake_bone_physic_auto_range_timer, first_interval=0.25)


def unregister():
    remove_spring_handler()
    remove_overshoot_handler()
    remove_pause_loop_handler()

    if bpy.app.timers.is_registered(fake_bone_physic_auto_range_timer):
        bpy.app.timers.unregister(fake_bone_physic_auto_range_timer)

    if STATE_KEY in bpy.app.driver_namespace:
        del bpy.app.driver_namespace[STATE_KEY]

    if OVERSHOOT_STATE_KEY in bpy.app.driver_namespace:
        del bpy.app.driver_namespace[OVERSHOOT_STATE_KEY]

    if PAUSE_TIMER_KEY in bpy.app.driver_namespace:
        del bpy.app.driver_namespace[PAUSE_TIMER_KEY]

    if AUTO_RANGE_STATE_KEY in bpy.app.driver_namespace:
        del bpy.app.driver_namespace[AUTO_RANGE_STATE_KEY]

    del bpy.types.Scene.fake_bone_physic_props

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()