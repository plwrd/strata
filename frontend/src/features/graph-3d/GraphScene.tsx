/**
 * The 3D knowledge graph.
 *
 * Nodes are one `InstancedMesh` (a draw call per graph, not per node) and edges
 * are one batched `LineSegments`. That is what makes 10k nodes tractable; a
 * `<mesh>` per node would not survive 1k.
 *
 * Interaction lives here; meaning does not. The scene knows a node is selected,
 * never *why* it matters — that is the store's and Python's business.
 */

import { OrbitControls } from "@react-three/drei";
import {
  Canvas,
  useFrame,
  useThree,
  type ThreeEvent,
} from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { GraphSnapshot } from "../../bridge/types";
import { edgeColor, edgeIsLit, neighborIds, nodeColor, nodeRadius } from "../graph/nodeStyle";
import type { Positions } from "../graph/useGraphLayout";
import {
  EdgeParticles,
  Nebula,
  NodeGlow,
  NodeLabels,
  Starfield,
} from "./effects";
import {
  appendEdgeCurveSegments,
  EDGE_CURVE_SEGMENTS,
  edgeSalt,
} from "./edgeCurves";
import {
  buildEdgeParticles,
  buildNebula,
  buildNodeGlow,
  buildStarfield,
  pickLabelled,
} from "./galaxy";

interface SceneProps {
  graph: GraphSnapshot;
  positions: Positions;
  selectedIds: string[];
  hoveredId: string | null;
  reducedMotion: boolean;
  /** From settings: flow particles + star drift on/off. */
  particles?: boolean;
  /** From settings: scales the additive glow (our bloom). */
  bloom?: boolean;
  quality?: "high" | "balanced" | "low-gpu";
  onSelect: (id: string, modifiers: { ctrl: boolean; shift: boolean }) => void;
  onHover: (id: string | null) => void;
  onOpen: (id: string) => void;
}

// Everything in the scene lives at layout coordinates * SCALE.
const SCALE = 0.1;

// Budgets by quality tier. The galaxy must degrade gracefully, not disappear.
const TIERS = {
  high: { stars: 2200, labels: 22, perEdge: 3, particleCap: 6000, nebula: 150 },
  balanced: {
    stars: 1200,
    labels: 14,
    perEdge: 2,
    particleCap: 3000,
    nebula: 90,
  },
  "low-gpu": { stars: 0, labels: 8, perEdge: 0, particleCap: 0, nebula: 0 },
} as const;

const UP = new THREE.Object3D();
const NODE_COLOR = new THREE.Color();

function Nodes({
  graph,
  positions,
  selectedIds,
  hoveredId,
  reducedMotion,
  onSelect,
  onHover,
  onOpen,
}: SceneProps): JSX.Element | null {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  const connected = useMemo(
    () => neighborIds(graph.edges, selected),
    [graph.edges, selected],
  );

  const nodes = useMemo(
    () => graph.nodes.filter((node) => positions[node.id] !== undefined),
    [graph.nodes, positions],
  );

  // The pointer is honest about what is clickable: over a node it is a hand.
  useEffect(() => {
    document.body.style.cursor = hoveredId ? "pointer" : "";
    return () => {
      document.body.style.cursor = "";
    };
  }, [hoveredId]);

  // Write the transform + colour of every instance whenever anything changes.
  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    nodes.forEach((node, index) => {
      const position = positions[node.id]!;
      const isSelected = selected.has(node.id);
      const isConnected = connected.has(node.id);
      const isHovered = node.id === hoveredId && !isSelected;
      // Hover swells the node slightly — feedback before commitment.
      const scale =
        nodeRadius(node) *
        (isSelected ? 1.35 : isConnected ? 1.15 : isHovered ? 1.18 : 1);
      UP.position.set(position[0] * 0.1, position[1] * 0.1, position[2] * 0.1);
      UP.scale.setScalar(scale * 0.1);
      UP.updateMatrix();
      mesh.setMatrixAt(index, UP.matrix);
      // Flat unlit colour from the theme token — no lift/multiply so Basic
      // material shows the exact hex the settings panel edits.
      NODE_COLOR.set(nodeColor(node, isSelected, isConnected));
      mesh.setColorAt(index, NODE_COLOR);
    });
    mesh.count = nodes.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [nodes, positions, selected, connected, hoveredId]);

  // The selection pulse. Reduced motion turns it into a static highlight rather
  // than removing the signal: the state must still be legible, just not moving.
  useFrame(({ clock }) => {
    const mesh = meshRef.current;
    if (!mesh || reducedMotion || selected.size === 0) return;
    const pulse = 1 + Math.sin(clock.elapsedTime * 3) * 0.06;
    nodes.forEach((node, index) => {
      if (!selected.has(node.id)) return;
      const position = positions[node.id]!;
      UP.position.set(position[0] * 0.1, position[1] * 0.1, position[2] * 0.1);
      UP.scale.setScalar(nodeRadius(node) * 1.35 * pulse * 0.1);
      UP.updateMatrix();
      mesh.setMatrixAt(index, UP.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  });

  if (nodes.length === 0) return null;

  const handleClick = (event: ThreeEvent<MouseEvent>): void => {
    event.stopPropagation();
    const node = nodes[event.instanceId ?? -1];
    if (!node) return;
    onSelect(node.id, {
      ctrl: event.ctrlKey || event.metaKey,
      shift: event.shiftKey,
    });
  };

  const handleDoubleClick = (event: ThreeEvent<MouseEvent>): void => {
    event.stopPropagation();
    const node = nodes[event.instanceId ?? -1];
    if (node && !node.locked && node.type !== "tag" && node.type !== "folder")
      onOpen(node.id);
  };

  const handleMove = (event: ThreeEvent<PointerEvent>): void => {
    const node = nodes[event.instanceId ?? -1];
    onHover(node?.id ?? null);
  };

  return (
    <instancedMesh
      ref={meshRef}
      args={[undefined, undefined, Math.max(nodes.length, 1)]}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onPointerMove={handleMove}
      onPointerOut={() => onHover(null)}
      frustumCulled={false}
      renderOrder={6}
    >
      <sphereGeometry args={[1, 16, 16]} />
      <meshBasicMaterial toneMapped={false} fog={false} />
    </instancedMesh>
  );
}

function Edges({
  graph,
  positions,
  selectedIds,
}: Pick<
  SceneProps,
  "graph" | "positions" | "selectedIds" | "hoveredId"
>): JSX.Element | null {
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  const colorScratch = useMemo(() => new THREE.Color(), []);

  // LineBasicMaterial is the line equivalent of MeshBasicMaterial: unlit,
  // no light response — vertex colours render as the raw theme hex.
  const { geometry, material, lines, segmentCount } = useMemo(() => {
    const points: number[] = [];
    const colors: number[] = [];
    let segmentCount = 0;
    for (const edge of graph.edges) {
      const from = positions[edge.source];
      const to = positions[edge.target];
      if (!from || !to) continue;
      const before = points.length;
      appendEdgeCurveSegments(
        points,
        from[0] * SCALE,
        from[1] * SCALE,
        from[2] * SCALE,
        to[0] * SCALE,
        to[1] * SCALE,
        to[2] * SCALE,
        edgeSalt(edge.source, edge.target),
      );
      const added = (points.length - before) / 6;
      segmentCount += added;
      for (let i = 0; i < added; i += 1) {
        colors.push(0.23, 0.25, 0.29, 0.23, 0.25, 0.29);
      }
    }
    const geometry = new THREE.BufferGeometry();
    if (points.length > 0) {
      geometry.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(points, 3),
      );
      geometry.setAttribute(
        "color",
        new THREE.Float32BufferAttribute(colors, 3),
      );
    }
    const material = new THREE.LineBasicMaterial({
      vertexColors: true,
      toneMapped: false,
      fog: false,
      transparent: false,
      depthTest: true,
      depthWrite: true,
    });
    const lines = new THREE.LineSegments(geometry, material);
    lines.frustumCulled = false;
    lines.renderOrder = 1;
    return { geometry, material, lines, segmentCount };
  }, [graph.edges, positions]);

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  useEffect(() => {
    if (graph.edges.length === 0 || segmentCount === 0) return;
    const colors: number[] = [];
    for (const edge of graph.edges) {
      const from = positions[edge.source];
      const to = positions[edge.target];
      if (!from || !to) continue;
      const isLit = edgeIsLit(edge, selected);
      colorScratch.set(edgeColor(isLit, edge.origin));
      const r = colorScratch.r;
      const g = colorScratch.g;
      const b = colorScratch.b;
      for (let i = 0; i < EDGE_CURVE_SEGMENTS; i += 1) {
        colors.push(r, g, b, r, g, b);
      }
    }
    if (colors.length === 0) return;
    const attr = geometry.getAttribute("color");
    if (attr instanceof THREE.BufferAttribute) {
      attr.array.set(colors);
      attr.needsUpdate = true;
    } else {
      geometry.setAttribute(
        "color",
        new THREE.Float32BufferAttribute(colors, 3),
      );
    }
  }, [
    geometry,
    graph.edges,
    positions,
    selected,
    colorScratch,
    segmentCount,
  ]);

  if (graph.edges.length === 0) return null;

  return <primitive object={lines} />;
}

function CameraRig({ nodeCount }: { nodeCount: number }): null {
  const { camera } = useThree();
  const placed = useRef(false);
  useEffect(() => {
    // Only frame the galaxy once per canvas mount. Re-running on nodeCount
    // (unlock / lock) yanked the camera to origin and made Explore look broken.
    if (placed.current) return;
    placed.current = true;
    const distance = Math.max(24, Math.sqrt(Math.max(nodeCount, 1)) * 6);
    camera.position.set(0, 0, distance);
    camera.updateProjectionMatrix();
  }, [camera, nodeCount]);
  return null;
}

/**
 * Fly the camera to the most recently selected node — click a star (or pick it
 * in the Graph list) and the galaxy both re-centres and moves in so the node is
 * actually in view. The flight eases out and then *stops*: once arrived it
 * never fights the user's own panning. Reduced motion jumps instead.
 */
function FocusRig({
  focusId,
  position,
  reducedMotion,
}: {
  focusId: string | null;
  position: [number, number, number] | null;
  reducedMotion: boolean;
}): null {
  const camera = useThree((state) => state.camera);
  const controls = useThree(
    (state) =>
      state.controls as unknown as {
        target: THREE.Vector3;
        update: () => void;
      } | null,
  );
  const arrivedRef = useRef<string | null>(null);
  const goalTarget = useRef(new THREE.Vector3());
  const goalCamera = useRef(new THREE.Vector3());
  const offset = useRef(new THREE.Vector3());

  // Re-arm the flight only when the focus *node* changes — not when layout
  // settles after unlock (that would yank the camera every reload).
  useEffect(() => {
    arrivedRef.current = null;
  }, [focusId]);

  useFrame((_, delta) => {
    if (!controls || !focusId || !position) return;
    if (arrivedRef.current === focusId) return;

    goalTarget.current.set(position[0], position[1], position[2]);
    offset.current.copy(camera.position).sub(controls.target);
    if (offset.current.lengthSq() < 1e-6) {
      offset.current.set(6, 10, 22);
    } else {
      // Pull in to a readable distance without slamming into the node.
      offset.current.setLength(
        Math.min(36, Math.max(12, offset.current.length() * 0.55)),
      );
    }
    goalCamera.current.copy(goalTarget.current).add(offset.current);

    if (reducedMotion) {
      controls.target.copy(goalTarget.current);
      camera.position.copy(goalCamera.current);
      arrivedRef.current = focusId;
    } else {
      const step = Math.min(1, delta * 3.2);
      controls.target.lerp(goalTarget.current, step);
      camera.position.lerp(goalCamera.current, step);
      if (
        controls.target.distanceTo(goalTarget.current) < 0.08 &&
        camera.position.distanceTo(goalCamera.current) < 0.2
      ) {
        arrivedRef.current = focusId;
      }
    }
    controls.update();
  });
  return null;
}

export function GraphScene(props: SceneProps): JSX.Element {
  const {
    graph,
    positions,
    selectedIds,
    hoveredId,
    reducedMotion,
    particles = true,
    bloom = true,
    quality = "balanced",
  } = props;
  const tier = TIERS[quality];
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  const [canvasKey, setCanvasKey] = useState(0);

  const starfield = useMemo(
    () => (tier.stars > 0 ? buildStarfield(tier.stars, 160, 340) : null),
    [tier.stars],
  );

  const nebula = useMemo(
    () => (tier.nebula > 0 ? buildNebula(tier.nebula, 240) : null),
    [tier.nebula],
  );

  // The camera follows the newest member of the selection.
  const focusId = selectedIds[selectedIds.length - 1] ?? null;
  const focusPosition = useMemo<[number, number, number] | null>(() => {
    const p = focusId ? positions[focusId] : undefined;
    return p ? [p[0] * SCALE, p[1] * SCALE, p[2] * SCALE] : null;
  }, [focusId, positions]);

  const glow = useMemo(
    () => buildNodeGlow(graph.nodes, positions, selected, SCALE, graph.edges),
    [graph.nodes, graph.edges, positions, selected],
  );

  // Flow particles cost a mount, so they honour both the setting and reduced
  // motion: a static dot mid-edge is noise, not information.
  const flow = useMemo(
    () =>
      particles && !reducedMotion && tier.perEdge > 0
        ? buildEdgeParticles(
            graph.edges,
            positions,
            selected,
            SCALE,
            tier.perEdge,
            tier.particleCap,
          )
        : null,
    [graph.edges, positions, selected, particles, reducedMotion, tier],
  );

  const labelled = useMemo(
    () =>
      pickLabelled(graph.nodes, positions, selected, hoveredId, tier.labels),
    [graph.nodes, positions, selected, hoveredId, tier.labels],
  );

  return (
    <Canvas
      key={canvasKey}
      camera={{ fov: 55, near: 0.1, far: 4000, position: [0, 0, 40] }}
      // Fixed DPR (not [min,max]): R3F rescaling the drawing buffer mid-session
      // flashes black and has contributed to WebGL context loss under Qt.
      dpr={Math.min(
        typeof window !== "undefined" ? window.devicePixelRatio : 1,
        1.5,
      )}
      gl={{
        antialias: quality === "high",
        // "high-performance" forces the discrete GPU on hybrid laptops; when
        // another process (capture, browser, etc.) contends for that GPU, Qt
        // WebEngine often loses the context and the galaxy flickers.
        powerPreference: "default",
        alpha: false,
        stencil: false,
        depth: true,
        failIfMajorPerformanceCaveat: false,
      }}
      onCreated={({ gl }) => {
        const canvas = gl.domElement;
        const onLost = (event: Event): void => {
          // Without preventDefault the context is unrestorable and Explore
          // stays blank after unlock/dialog teardown.
          event.preventDefault();
          window.setTimeout(() => setCanvasKey((key) => key + 1), 50);
        };
        canvas.addEventListener("webglcontextlost", onLost, false);
      }}
      // The canvas is decorative for assistive technology: the same graph is
      // exposed as a real tree in GraphList. Hiding it prevents a screen reader
      // from announcing an empty <canvas> as the primary content.
      aria-hidden="true"
    >
      <color attach="background" args={["#04060d"]} />
      <fog attach="fog" args={["#04060d", 60, 260]} />
      <ambientLight intensity={0.7} />
      <pointLight position={[30, 30, 30]} intensity={1.1} />
      <pointLight position={[-30, -20, -20]} intensity={0.5} color="#a06bff" />
      <CameraRig nodeCount={graph.nodes.length} />
      <FocusRig
        focusId={focusId}
        position={focusPosition}
        reducedMotion={reducedMotion}
      />
      {nebula && <Nebula data={nebula} reducedMotion={reducedMotion} />}
      {starfield && (
        <Starfield data={starfield} reducedMotion={reducedMotion} />
      )}
      <Edges
        graph={graph}
        positions={positions}
        selectedIds={props.selectedIds}
        hoveredId={hoveredId}
      />
      {flow && <EdgeParticles data={flow} />}
      <NodeGlow data={glow} reducedMotion={reducedMotion} bloom={bloom} />
      <Nodes {...props} />
      <NodeLabels
        nodes={labelled}
        positions={positions}
        selectedIds={selected}
        hoveredId={hoveredId}
        scale={SCALE}
      />
      <OrbitControls
        enableDamping={!reducedMotion}
        dampingFactor={0.08}
        rotateSpeed={0.7}
        zoomSpeed={0.9}
        // Idle drift only on the high tier — continuous camera motion + stacked
        // additive layers was the main idle shimmer on balanced/low-gpu.
        autoRotate={
          !reducedMotion && selectedIds.length === 0 && quality === "high"
        }
        autoRotateSpeed={0.25}
        makeDefault
      />
    </Canvas>
  );
}
