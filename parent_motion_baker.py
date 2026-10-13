
bl_info = {
    "name": "Parent Motion Baker",
    "author": "OpenAI",
    "version": (2, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Animation",
    "description": "Bake the active selected bone motion into its direct children",
    "category": "Animation",
}

import bpy


def get_selected_pose_bone(context):
    """選択中のアクティブなボーンを取得する。Root などの名前指定は不要。"""
    if context.mode != 'POSE':
        return None
    if context.object is None or context.object.type != 'ARMATURE':
        return None
    return context.active_pose_bone


def insert_keys(item, frame):
    item.keyframe_insert(
        data_path="location", frame=frame, group=item.name
    )
    item.keyframe_insert(
        data_path="scale", frame=frame, group=item.name
    )

    if item.rotation_mode == 'QUATERNION':
        rotation = "rotation_quaternion"
    elif item.rotation_mode == 'AXIS_ANGLE':
        rotation = "rotation_axis_angle"
    else:
        rotation = "rotation_euler"

    item.keyframe_insert(
        data_path=rotation, frame=frame, group=item.name
    )


def get_transform_paths(item, is_bone):
    if is_bone:
        prefix = item.path_from_id("")
        # path_from_id("") termina em pose.bones["Nome"].
        return {
            prefix + ".location",
            prefix + ".scale",
            prefix + ".rotation_euler",
            prefix + ".rotation_quaternion",
            prefix + ".rotation_axis_angle",
        }

    return {
        "location",
        "scale",
        "rotation_euler",
        "rotation_quaternion",
        "rotation_axis_angle",
    }


def remove_curves(action, paths):
    curves = getattr(action, "fcurves", None)
    if curves is None:
        return

    for curve in list(curves):
        if curve.data_path in paths:
            curves.remove(curve)


def linear_interpolation(action, paths, start, end):
    curves = getattr(action, "fcurves", None)
    if curves is None:
        return

    for curve in curves:
        if curve.data_path in paths:
            for key in curve.keyframe_points:
                if start <= key.co.x <= end:
                    key.interpolation = 'LINEAR'


class ANIM_OT_selected_parent_motion_bake(bpy.types.Operator):
    bl_idname = "anim.selected_parent_motion_bake"
    bl_label = "Bake Selected Parent Motion"
    bl_description = "Bake selected parent motion into its direct children"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode == 'POSE':
            return get_selected_pose_bone(context) is not None

        return context.object is not None

    def execute(self, context):
        scene = context.scene
        start = scene.frame_preview_start
        end = scene.frame_preview_end
        original_frame = scene.frame_current

        if end < start:
            self.report({'ERROR'}, "Invalid Preview Range.")
            return {'CANCELLED'}

        is_bone = context.mode == 'POSE'

        if is_bone:
            arm = context.object
            # 現在アクティブな選択ボーンを使用。ボーン名の入力は不要。
            selected_bone = get_selected_pose_bone(context)
            if selected_bone is None:
                self.report({'ERROR'}, "Pose Modeでボーンを選択してください。")
                return {'CANCELLED'}

            source_name = selected_bone.name
            source = arm.pose.bones[source_name]
            children_names = [child.name for child in source.children]

            if not children_names:
                self.report({'ERROR'}, "Selected bone has no direct children.")
                return {'CANCELLED'}

            try:
                baked = {}
                baseline = None

                # Save evaluated poses before changing animation.
                for frame in range(start, end + 1):
                    scene.frame_set(frame)
                    context.view_layer.update()

                    source = arm.pose.bones[source_name]

                    if frame == start:
                        baseline = source.matrix.copy()

                    baked[frame] = {
                        name: arm.pose.bones[name].matrix.copy()
                        for name in children_names
                    }

                if arm.animation_data is None:
                    arm.animation_data_create()

                if arm.animation_data.action is None:
                    arm.animation_data.action = bpy.data.actions.new(
                        name=f"{arm.name}_MotionBaked"
                    )

                action = arm.animation_data.action
                source = arm.pose.bones[source_name]
                source_paths = get_transform_paths(source, True)

                remove_curves(action, source_paths)

                # Restore source and bake every direct child.
                for frame in range(start, end + 1):
                    scene.frame_set(frame)

                    source = arm.pose.bones[source_name]
                    source.matrix = baseline
                    context.view_layer.update()

                    for name in children_names:
                        child = arm.pose.bones[name]
                        child.matrix = baked[frame][name]
                        context.view_layer.update()

                    insert_keys(source, frame)

                    for name in children_names:
                        insert_keys(arm.pose.bones[name], frame)

                paths = set(source_paths)

                for name in children_names:
                    paths.update(
                        get_transform_paths(arm.pose.bones[name], True)
                    )

                linear_interpolation(action, paths, start, end)

                self.report(
                    {'INFO'},
                    f"Baked motion to {len(children_names)} child bones."
                )
                return {'FINISHED'}

            except Exception as exc:
                self.report({'ERROR'}, f"Bone bake failed: {exc}")
                return {'CANCELLED'}

            finally:
                scene.frame_set(original_frame)
                context.view_layer.update()

        # Object Mode: active object is the source.
        source = context.object
        source_name = source.name
        children_names = [child.name for child in source.children]

        if not children_names:
            self.report({'ERROR'}, "Selected object has no direct children.")
            return {'CANCELLED'}

        try:
            baked = {}
            baseline = None

            # Save evaluated world transforms.
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                context.view_layer.update()

                source = bpy.data.objects[source_name]

                if frame == start:
                    baseline = source.matrix_world.copy()

                baked[frame] = {
                    name: bpy.data.objects[name].matrix_world.copy()
                    for name in children_names
                }

            if source.animation_data is None:
                source.animation_data_create()

            if source.animation_data.action is None:
                source.animation_data.action = bpy.data.actions.new(
                    name=f"{source.name}_MotionBaked"
                )

            action = source.animation_data.action
            source_paths = get_transform_paths(source, False)
            remove_curves(action, source_paths)

            # Keep source static and bake the original world transforms
            # into its direct children.
            for frame in range(start, end + 1):
                scene.frame_set(frame)

                source = bpy.data.objects[source_name]
                source.matrix_world = baseline
                context.view_layer.update()

                for name in children_names:
                    child = bpy.data.objects[name]
                    child.matrix_world = baked[frame][name]
                    context.view_layer.update()

                insert_keys(source, frame)

                for name in children_names:
                    insert_keys(
                        bpy.data.objects[name], frame
                    )

            paths = set(source_paths)

            for name in children_names:
                paths.update(
                    get_transform_paths(
                        bpy.data.objects[name], False
                    )
                )

            # Each object may have its own action.
            for name in [source_name] + children_names:
                obj = bpy.data.objects[name]
                if obj.animation_data and obj.animation_data.action:
                    linear_interpolation(
                        obj.animation_data.action,
                        paths,
                        start,
                        end
                    )

            self.report(
                {'INFO'},
                f"Baked motion to {len(children_names)} child objects."
            )
            return {'FINISHED'}

        except Exception as exc:
            self.report({'ERROR'}, f"Object bake failed: {exc}")
            return {'CANCELLED'}

        finally:
            scene.frame_set(original_frame)
            context.view_layer.update()


class VIEW3D_PT_selected_parent_motion_baker(bpy.types.Panel):
    bl_label = "Selected Parent Motion Baker"
    bl_idname = "VIEW3D_PT_selected_parent_motion_baker"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Animation"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        if context.mode == 'POSE' and context.active_pose_bone:
            source = context.active_pose_bone
            children = list(source.children)

            layout.label(
                text=f"Source Bone: {source.name}",
                icon='BONE_DATA'
            )
            layout.label(text=f"Direct children: {len(children)}")

            for child in children:
                layout.label(text=child.name, icon='BONE_DATA')

        elif context.object:
            source = context.object
            children = list(source.children)

            layout.label(
                text=f"Source Object: {source.name}",
                icon='OBJECT_DATA'
            )
            layout.label(text=f"Direct children: {len(children)}")

            for child in children:
                layout.label(text=child.name, icon='OBJECT_DATA')

        else:
            layout.label(text="Select an object or pose bone.")

        box = layout.box()
        box.label(text="Preview Range (P):")

        row = box.row(align=True)
        row.label(text=str(scene.frame_preview_start))
        row.label(text="to")
        row.label(text=str(scene.frame_preview_end))

        layout.operator(
            "anim.selected_parent_motion_bake",
            icon='ACTION'
        )


classes = (
    ANIM_OT_selected_parent_motion_bake,
    VIEW3D_PT_selected_parent_motion_baker,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
