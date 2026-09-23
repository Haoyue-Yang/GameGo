This item is routed to the dedicated fixed-camera 2.5D production pipeline.

- Use Three.js/WebGL with true 3D geometry, materials, lighting, shadows, depth, and spatial collision.
- Lock camera position, orientation, projection, target, and framing throughout core gameplay; temporary shake must return to the exact pose.
- Keep every interaction readable from that one camera. Forbid orbit, follow, pan, zoom, and camera switching.
- Project screen-relative input onto the fixed gameplay plane so controls always match visible directions.
- Treat the locked composition like a densely art-directed set: define foreground occluder accents, a readable midground play area, distant architecture/terrain, filled corners, layered lighting, material variation, and several subtle ambient motion loops.
- Allocate visible geometry detail to silhouette curvature, bevels, roof/furniture/vegetation facets, connectors, trims, and hero-object subassemblies; use instancing and shared geometry for repeated props rather than reducing them to crude primitives.
