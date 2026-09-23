# Shader FX and Material Differentiation

Use a shader only when an approved scene, material, atmosphere, transition, or feedback requirement benefits from GPU-driven motion or lighting. A shader augments gameplay geometry and visual feedback; it never replaces structural 3D models, collision, navigation, or readable interaction states.

Build the effect from small techniques rather than cloning a complete reference. Define a game-specific visual signature: palette, motion rhythm and direction, spatial scale, surface or edge language, lighting response, and gameplay-triggered variation. Water, fog, portals, shields, lava, holograms, and post-processing must not share one universal preset.

For every inline shader, choose one integration target, list uniforms and texture channels, define blending/depth behavior, set a bounded performance budget, and provide a non-shader fallback. Full-screen raymarching and multi-pass feedback require explicit justification; prefer material-local or screen-local effects that leave budget for the game loop.

Shadertoy-derived references are technique-only inspiration. Do not copy source, reproduce a complete composition, or assume licensing permits reuse. Convert Shadertoy-style uniforms and pass assumptions into explicit project-owned modules.
