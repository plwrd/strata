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
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { GraphSnapshot } from "../../bridge/types";
import { edgeColor, nodeColor, nodeRadius } from "../graph/nodeStyle";
import type { Positions } from "../graph/useGraphLayout";
import {
  EdgeParticles,
  Nebula,
  NodeGlow,
  NodeLabels,
  Starfield,
} from "./effects";
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
      const isHovered = node.id === hoveredId && !isSelected;
      // Hover swells the node slightly — feedback before commitment.
      const scale =
        nodeRadius(node) * (isSelected ? 1.35 : isHovered ? 1.18 : 1);
      UP.position.set(position[0] * 0.1, position[1] * 0.1, position[2] * 0.1);
      UP.scale.setScalar(scale * 0.1);
      UP.updateMatrix();
      mesh.setMatrixAt(index, UP.matrix);
      // Selected stars get a lifted warm tint so they read as lit cores, not
      // chalky white under the shared standard material.
      NODE_COLOR.set(nodeColor(node, isSelected));
      if (isSelected) {
        NODE_COLOR.multiplyScalar(1.15);
        NODE_COLOR.r = Math.min(NODE_COLOR.r, 1);
        NODE_COLOR.g = Math.min(NODE_COLOR.g, 1);
        NODE_COLOR.b = Math.min(NODE_COLOR.b, 1);
      }
      mesh.setColorAt(index, NODE_COLOR);
    });
    mesh.count = nodes.length;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [nodes, positions, selected, hoveredId]);

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
    >
      <sphereGeometry args={[1, 16, 16]} />
      {/* Unlit + fog off: MeshStandardMaterial washed nodes grey under sparse
          lights, and MeshBasicMaterial still picks up the galaxy fog unless
          fog is disabled — that was the remaining dark-grey look. */}
      <meshBasicMaterial toneMapped={false} fog={false} />
    </instancedMesh>
  );
}

function Edges({
  graph,
  positions,
  selectedIds,
  hoveredId,
}: Pick<
  SceneProps,
  "graph" | "positions" | "selectedIds" | "hoveredId"
>): JSX.Element | null {
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);

  const geometry = useMemo(() => {
    const points: number[] = [];
    const colors: number[] = [];
    const color = new THREE.Color();
    for (const edge of graph.edges) {
      const from = positions[edge.source];
      const to = positions[edge.target];
      if (!from || !to) continue;
      // An edge lights up when *both* ends are selected: that is the
      // "constellation" — the shape of the thing the user is about to send.
      const isLit = selected.has(edge.source) && selected.has(edge.target);
      color.set(edgeColor(isLit, edge.origin));
      // Hovering a node ignites its connections; while a selection exists,
      // edges that touch neither the selection nor the hover recede, so the
      // neighbourhood the user is working in stays legible.
      const touchesHover =
        hoveredId !== null &&
        (edge.source === hoveredId || edge.target === hoveredId);
      const touchesSelection =
        selected.has(edge.source) || selected.has(edge.target);
      const factor = touchesHover
        ? 1.7
        : selected.size === 0
          ? 1
          : touchesSelection
            ? 1.25
            : 0.4;
      const r = Math.min(color.r * factor, 1);
      const g = Math.min(color.g * factor, 1);
      const b = Math.min(color.b * factor, 1);
      points.push(from[0] * 0.1, from[1] * 0.1, from[2] * 0.1);
      points.push(to[0] * 0.1, to[1] * 0.1, to[2] * 0.1);
      colors.push(r, g, b, r, g, b);
    }
    const buffer = new THREE.BufferGeometry();
    buffer.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(points, 3),
    );
    buffer.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    return buffer;
  }, [graph.edges, positions, selected, hoveredId]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  if (graph.edges.length === 0) return null;

  return (
    <lineSegments geometry={geometry} frustumCulled={false}>
      <lineBasicMaterial
        vertexColors
        transparent
        opacity={0.85}
        toneMapped={false}
      />
    </lineSegments>
  );
}

function CameraRig({ nodeCount }: { nodeCount: number }): null {
  const { camera } = useThree();
  useEffect(() => {
    const distance = Math.max(24, Math.sqrt(Math.max(nodeCount, 1)) * 6);
    camera.position.set(0, 0, distance);
    camera.updateProjectionMatrix();
  }, [camera, nodeCount]);
  return null;
}

/**
 * Fly the orbit target to the most recently selected node — click a star and
 * the galaxy re-centres on it. The flight eases out and then *stops*: once
 * arrived it never fights the user's own panning. Reduced motion jumps instead.
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
  const controls = useThree(
    (state) =>
      state.controls as unknown as {
        target: THREE.Vector3;
        update: () => void;
      } | null,
  );
  const arrivedRef = useRef<string | null>(null);
  const goal = useRef(new THREE.Vector3());

  useFrame((_, delta) => {
    if (!controls || !focusId || !position) return;
    if (arrivedRef.current === focusId) return;
    goal.current.set(position[0], position[1], position[2]);
    if (reducedMotion) {
      controls.target.copy(goal.current);
      arrivedRef.current = focusId;
    } else {
      controls.target.lerp(goal.current, Math.min(1, delta * 4));
      if (controls.target.distanceTo(goal.current) < 0.05)
        arrivedRef.current = focusId;
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
    () => buildNodeGlow(graph.nodes, positions, selected, SCALE),
    [graph.nodes, positions, selected],
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
      camera={{ fov: 55, near: 0.1, far: 4000, position: [0, 0, 40] }}
      dpr={[1, 1.75]}
      gl={{ antialias: true, powerPreference: "high-performance" }}
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
        // The idle galaxy drifts; the moment something is selected (or motion is
        // reduced) it holds still and lets the user work.
        autoRotate={!reducedMotion && selectedIds.length === 0}
        autoRotateSpeed={0.35}
        makeDefault
      />
    </Canvas>
  );
}
