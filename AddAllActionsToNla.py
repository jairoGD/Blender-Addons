bl_info = {
    "name": "Add All Actions to NLA",
    "author": "jairo",
    "version": (1, 3, 0),
    "blender": (4, 3, 0),
    "location": "3D View > Sidebar > Animation Tab",
    "description": "Adds all the scene's actions as tracks in the NLA editor for the active object.",
    "category": "Animation",
}

import bpy

class ANIM_OT_AddAllActionsToNLA(bpy.types.Operator):
    """
    Add all actions to the NLA Editor for the active object, with the option to set the first action
    """
    bl_idname = "anim.add_all_actions_nla"
    bl_label = "Add All Actions to NLA"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        first_action_name = context.scene.nla_first_action

        # Ensure an object is selected and has animation data
        if obj is None or obj.animation_data is None:
            self.report({"ERROR"}, "Please select an object with animation data.")
            return {"CANCELLED"}

        # Clear existing NLA tracks
        while obj.animation_data.nla_tracks:
            obj.animation_data.nla_tracks.remove(obj.animation_data.nla_tracks[0])

        # Add all actions to the NLA, placing the selected first action first if specified
        actions = bpy.data.actions
        if first_action_name in actions:
            actions = [actions[first_action_name]] + [act for act in actions if act.name != first_action_name]

        # Reverse the order of adding tracks to ensure the first action is at the top
        for action in reversed(actions):
            track = obj.animation_data.nla_tracks.new()
            track.name = action.name
            strip = track.strips.new(action.name, start=0, action=action)
            strip.extrapolation = 'NOTHING'  # Set extrapolation to 'Nothing'

        self.report({"INFO"}, "All actions added to the NLA Editor!")
        return {"FINISHED"}

class ANIM_PT_AddAllActionsToNLAPanel(bpy.types.Panel):
    """
    UI Panel for the Add All Actions to NLA addon
    """
    bl_label = "Add All Actions to NLA"
    bl_idname = "ANIM_PT_add_all_actions_nla"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Animation"

    def draw(self, context):
        layout = self.layout
        layout.prop_search(context.scene, "nla_first_action", bpy.data, "actions", text="Default pose")
        layout.prop(context.scene, "auto_set_frame_range", text="Auto Set Frame Range")
        layout.operator("anim.add_all_actions_nla", text="Add All Actions")

        if context.scene.auto_set_frame_range:
            layout.label(text="Auto frame range active.")

# Update playback range based on the selected object's animation data
def update_ui_playback_range(scene):
    obj = bpy.context.view_layer.objects.active  # Access the active object from the context
    if obj and obj.animation_data and obj.animation_data.action:
        action = obj.animation_data.action
        scene.use_preview_range = True
        scene.frame_preview_start = int(action.frame_range[0])
        scene.frame_preview_end = int(action.frame_range[1])

# Handler function to check object selection
def handle_object_selection(scene, depsgraph):
    if bpy.context.scene.auto_set_frame_range:
        update_ui_playback_range(scene)

classes = (
    ANIM_OT_AddAllActionsToNLA,
    ANIM_PT_AddAllActionsToNLAPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.nla_first_action = bpy.props.StringProperty(
        name="Default pose",
        description="Select the action to be first in NLA, usually A or T pose",
        default=""
    )
    bpy.types.Scene.auto_set_frame_range = bpy.props.BoolProperty(
        name="Auto Set Frame Range",
        description="Automatically set UI Playback range to the selected object's animation. (Must be disabled if you want to change the animation keyframes)",
        default=False
    )
    bpy.app.handlers.depsgraph_update_post.append(handle_object_selection)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.nla_first_action
    del bpy.types.Scene.auto_set_frame_range
    bpy.app.handlers.depsgraph_update_post.remove(handle_object_selection)

if __name__ == "__main__":
    register()
