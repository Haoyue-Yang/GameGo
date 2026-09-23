This item is routed to the dedicated movable-camera 3D production pipeline.

- Use Three.js/WebGL with a defined 3D world, camera rig, spatial collision, navigation, lighting, and shadows.
- Define follow target, yaw/pitch limits, smoothing, distance, collision avoidance, recentering, and camera transitions.
- Derive movement from camera-forward and camera-right vectors projected onto the movement plane; visible left/right must never invert because of world-axis mismatch.
- Define occlusion, clipping, spawn orientation, ground detection, slopes, and recovery. For platforming, calculate jump apex/range against actual platform height and gap budgets.
- Define a high-density visual composition for every permitted camera region: hierarchical hero models, foreground/midground/background landmarks, terrain and enclosure detail, scatter props, material/color variation, three-layer lighting, shadows/fog, and ambient motion loops.
- Use richer visible topology for silhouettes, curvature, bevels, joints, trims, roofs, rocks, vegetation, furniture, and hero subassemblies. Control cost with InstancedMesh, shared BufferGeometry/materials, LOD, batching, and procedural modules—not sparse primitives.
