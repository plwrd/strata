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

interface SceneProps {
  graph: GraphSnapshot;
  positions: Positions;
  selectedIds: string[];
  hoveredId: string | null;
  reducedMotion: boolean;
  onSelect: (id: string, modifiers: { ctrl: boolean; shift: boolean }) => void;
  onHover: (id: string | null) => void;
  onOpen: (id: string) => void;
}

const UP = new THREE.Object3D();

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

  // Write the transform + colour of every instance whenever anything changes.
  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    nodes.forEach((node, index) => {
      const position = positions[node.id]!;
      const isSelected = selected.has(node.id);
      const scale = nodeRadius(node) * (isSelected ? 1.35 : 1);
      UP.position.set(position[0] * 0.1, position[1] * 0.1, position[2] * 0.1);
      UP.scale.setScalar(scale * 0.1);
      UP.updateMatrix();
      mesh.setMatrixAt(index, UP.matrix);
      mesh.setColorAt(index, new THREE.Color(nodeColor(node, isSelected)));
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
      <sphereGeometry args={[1, 12, 12]} />
      <meshStandardMaterial
        roughness={0.35}
        metalness={0.15}
        toneMapped={false}
      />
    </instancedMesh>
  );
}

function Edges({
  graph,
  positions,
  selectedIds,
}: Pick<
  SceneProps,
  "graph" | "positions" | "selectedIds"
>): JSX.Element | null {
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);

  const geometry = useMemo(() => {
    const points: number[] = [];
    const colors: number[] = [];
    for (const edge of graph.edges) {
      const from = positions[edge.source];
      const to = positions[edge.target];
      if (!from || !to) continue;
      // An edge lights up when *both* ends are selected: that is the
      // "constellation" — the shape of the thing the user is about to send.
      const isLit = selected.has(edge.source) && selected.has(edge.target);
      const color = new THREE.Color(edgeColor(isLit, edge.origin));
      points.push(from[0] * 0.1, from[1] * 0.1, from[2] * 0.1);
      points.push(to[0] * 0.1, to[1] * 0.1, to[2] * 0.1);
      colors.push(color.r, color.g, color.b, color.r, color.g, color.b);
    }
    const buffer = new THREE.BufferGeometry();
    buffer.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(points, 3),
    );
    buffer.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    return buffer;
  }, [graph.edges, positions, selected]);

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

export function GraphScene(props: SceneProps): JSX.Element {
  const { graph, reducedMotion } = props;
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
      <Edges
        graph={graph}
        positions={props.positions}
        selectedIds={props.selectedIds}
      />
      <Nodes {...props} />
      <OrbitControls
        enableDamping={!reducedMotion}
        dampingFactor={0.08}
        rotateSpeed={0.7}
        zoomSpeed={0.9}
        makeDefault
      />
    </Canvas>
  );
}
