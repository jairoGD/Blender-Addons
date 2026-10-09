import bpy

mat = bpy.data.materials.new(name="SpriteSheet_Mat")
mat.use_nodes = True
nodes = mat.node_tree.nodes
links = mat.node_tree.links

nodes.clear()

output = nodes.new("ShaderNodeOutputMaterial")
output.location = (600, 0)

bsdf = nodes.new("ShaderNodeBsdfPrincipled")
bsdf.location = (300, 0)

tex = nodes.new("ShaderNodeTexImage")
tex.location = (0, 0)
tex.interpolation = 'Closest'

uv = nodes.new("ShaderNodeUVMap")
uv.location = (-1000, 0)

framesX = nodes.new("ShaderNodeValue")
framesX.outputs[0].default_value = 4
framesX.label = "Frames X"
framesX.location = (-1000, 200)

framesY = nodes.new("ShaderNodeValue")
framesY.outputs[0].default_value = 4
framesY.label = "Frames Y"
framesY.location = (-1000, 100)

frame = nodes.new("ShaderNodeValue")
frame.outputs[0].default_value = 0
frame.label = "Frame"
frame.location = (-1000, -100)

# STEP
frameFloor = nodes.new("ShaderNodeMath")
frameFloor.operation = 'FLOOR'
frameFloor.location = (-800, -100)

# coluna
modulo = nodes.new("ShaderNodeMath")
modulo.operation = 'MODULO'
modulo.location = (-600, -100)

# linha base
divideFrame = nodes.new("ShaderNodeMath")
divideFrame.operation = 'DIVIDE'
divideFrame.location = (-600, -250)

floorY = nodes.new("ShaderNodeMath")
floorY.operation = 'FLOOR'
floorY.location = (-400, -250)

# inverter Y
oneMinus = nodes.new("ShaderNodeMath")
oneMinus.operation = 'SUBTRACT'
oneMinus.location = (-200, -250)

oneMinus.inputs[0].default_value = 1

invertY = nodes.new("ShaderNodeMath")
invertY.operation = 'MULTIPLY'
invertY.location = (0, -250)

# inverso escala
invX = nodes.new("ShaderNodeMath")
invX.operation = 'DIVIDE'
invX.inputs[0].default_value = 1
invX.location = (-600, 200)

invY = nodes.new("ShaderNodeMath")
invY.operation = 'DIVIDE'
invY.inputs[0].default_value = 1
invY.location = (-600, 100)

# escala UV
combineScale = nodes.new("ShaderNodeCombineXYZ")
combineScale.location = (-600, 0)

scale = nodes.new("ShaderNodeVectorMath")
scale.operation = 'MULTIPLY'
scale.location = (-400, 0)

# offset
mulX = nodes.new("ShaderNodeMath")
mulX.operation = 'MULTIPLY'
mulX.location = (-200, -100)

mulY = nodes.new("ShaderNodeMath")
mulY.operation = 'MULTIPLY'
mulY.location = (200, -250)

combineOffset = nodes.new("ShaderNodeCombineXYZ")
combineOffset.location = (400, -100)

add = nodes.new("ShaderNodeVectorMath")
add.operation = 'ADD'
add.location = (200, 0)

# LINKS

links.new(frame.outputs[0], frameFloor.inputs[0])

links.new(frameFloor.outputs[0], modulo.inputs[0])
links.new(framesX.outputs[0], modulo.inputs[1])

links.new(frameFloor.outputs[0], divideFrame.inputs[0])
links.new(framesX.outputs[0], divideFrame.inputs[1])
links.new(divideFrame.outputs[0], floorY.inputs[0])

# inverter Y: (framesY - 1 - row)
subFrames = nodes.new("ShaderNodeMath")
subFrames.operation = 'SUBTRACT'
subFrames.location = (-200, -350)

links.new(framesY.outputs[0], subFrames.inputs[0])
subFrames.inputs[1].default_value = 1

invertRow = nodes.new("ShaderNodeMath")
invertRow.operation = 'SUBTRACT'
invertRow.location = (0, -350)

links.new(subFrames.outputs[0], invertRow.inputs[0])
links.new(floorY.outputs[0], invertRow.inputs[1])

# inversos
links.new(framesX.outputs[0], invX.inputs[1])
links.new(framesY.outputs[0], invY.inputs[1])

# escala
links.new(invX.outputs[0], combineScale.inputs["X"])
links.new(invY.outputs[0], combineScale.inputs["Y"])
links.new(uv.outputs["UV"], scale.inputs[0])
links.new(combineScale.outputs["Vector"], scale.inputs[1])

# offset
links.new(modulo.outputs[0], mulX.inputs[0])
links.new(invX.outputs[0], mulX.inputs[1])

links.new(invertRow.outputs[0], mulY.inputs[0])
links.new(invY.outputs[0], mulY.inputs[1])

links.new(mulX.outputs[0], combineOffset.inputs["X"])
links.new(mulY.outputs[0], combineOffset.inputs["Y"])

links.new(scale.outputs["Vector"], add.inputs[0])
links.new(combineOffset.outputs["Vector"], add.inputs[1])

links.new(add.outputs["Vector"], tex.inputs["Vector"])

links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

print("Agora sim: esquerda → direita, cima → baixo ✔")