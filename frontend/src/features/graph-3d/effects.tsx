/**
 * The galaxy layers of the 3D graph: node glow, background stars, and particle
 * flow along edges — plus floating labels.
 *
 * Three rules keep this fast and honest:
 *
 * 1. **Animation is a uniform, not a loop.** Every moving thing is positioned in
 *    the vertex shader from a single `uTime`; the CPU writes one float per frame
 *    no matter how many thousand particles exist.
 * 2. **Reduced motion is respected, not approximated.** With reduced motion the
 *    glow and stars render statically and the flow particles do not mount —
 *    state stays legible, nothing moves.
 * 3. **Additive blending is the bloom.** The glow halos are drawn additively so
 *    overlapping light accumulates like a long-exposure photograph; no
 *    post-processing pass, no extra dependency, and `bloom_enabled` simply
 *    scales the halo intensity.
 */

import { Billboard, Text } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { GraphNode } from "../../bridge/types";
import type { Positions } from "../graph/useGraphLayout";
import type { EdgeParticleData, GlowData, StarfieldData } from "./galaxy";

const GLOW_VERTEX = /* glsl */ `
  attribute float aSize;
  attribute vec3 aColor;
  attribute float aSelected;
  uniform float uTime;
  uniform float uPulse;
  uniform float uIntensity;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vColor = aColor;
    float pulse = 1.0 + aSelected * uPulse * (0.5 + 0.5 * sin(uTime * 3.0));
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * pulse * uIntensity * (320.0 / -mv.z);
    vAlpha = 0.5 + aSelected * 0.4;
    gl_Position = projectionMatrix * mv;
  }
`;

const GLOW_FRAGMENT = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float d = length(gl_PointCoord - vec2(0.5));
    float a = smoothstep(0.5, 0.0, d);
    a *= a * a; // cubic falloff: a tight core with a long soft tail
    gl_FragColor = vec4(vColor, a * vAlpha);
  }
`;

const STAR_VERTEX = /* glsl */ `
  attribute float aSize;
  attribute vec3 aColor;
  attribute float aPhase;
  uniform float uTime;
  uniform float uTwinkle;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vColor = aColor;
    vAlpha = 0.55 + uTwinkle * 0.45 * sin(uTime * 0.8 + aPhase);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * (140.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;

const STAR_FRAGMENT = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float d = length(gl_PointCoord - vec2(0.5));
    float a = smoothstep(0.5, 0.05, d);
    gl_FragColor = vec4(vColor, a * vAlpha * 0.8);
  }
`;

const FLOW_VERTEX = /* glsl */ `
  attribute vec3 aEnd;
  attribute vec3 aColor;
  attribute float aOffset;
  attribute float aSpeed;
  uniform float uTime;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float t = fract(aOffset + uTime * aSpeed);
    vec3 p = mix(position, aEnd, t);
    vColor = aColor;
    // Fade in near departure and out near arrival, so particles feel emitted.
    vAlpha = smoothstep(0.0, 0.12, t) * smoothstep(1.0, 0.88, t);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_PointSize = 2.6 * (220.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;

const FLOW_FRAGMENT = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float d = length(gl_PointCoord - vec2(0.5));
    float a = smoothstep(0.5, 0.0, d);
    gl_FragColor = vec4(vColor, a * vAlpha * 0.9);
  }
`;

function useDisposable<T extends { dispose: () => void }>(value: T): T {
  useEffect(() => () => value.dispose(), [value]);
  return value;
}

/** Additive halos behind every node; selection ignites gold and pulses. */
export function NodeGlow({
  data,
  reducedMotion,
  bloom,
}: {
  data: GlowData;
  reducedMotion: boolean;
  bloom: boolean;
}): JSX.Element | null {
  const materialRef = useRef<THREE.ShaderMaterial>(null);

  const geometry = useDisposable(
    useMemo(() => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(data.positions, 3));
      g.setAttribute("aColor", new THREE.BufferAttribute(data.colors, 3));
      g.setAttribute("aSize", new THREE.BufferAttribute(data.sizes, 1));
      g.setAttribute("aSelected", new THREE.BufferAttribute(data.selected, 1));
      return g;
    }, [data]),
  );

  useFrame(({ clock }) => {
    if (materialRef.current)
      materialRef.current.uniforms["uTime"]!.value = clock.elapsedTime;
  });

  if (data.count === 0) return null;
  return (
    <points geometry={geometry} frustumCulled={false}>
      <shaderMaterial
        ref={materialRef}
        vertexShader={GLOW_VERTEX}
        fragmentShader={GLOW_FRAGMENT}
        uniforms={{
          uTime: { value: 0 },
          uPulse: { value: reducedMotion ? 0 : 0.35 },
          uIntensity: { value: bloom ? 1.0 : 0.55 },
        }}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}

/** The background starfield; drifts and twinkles unless motion is reduced. */
export function Starfield({
  data,
  reducedMotion,
}: {
  data: StarfieldData;
  reducedMotion: boolean;
}): JSX.Element {
  const materialRef = useRef<THREE.ShaderMaterial>(null);
  const groupRef = useRef<THREE.Group>(null);

  const geometry = useDisposable(
    useMemo(() => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(data.positions, 3));
      g.setAttribute("aColor", new THREE.BufferAttribute(data.colors, 3));
      g.setAttribute("aSize", new THREE.BufferAttribute(data.sizes, 1));
      g.setAttribute("aPhase", new THREE.BufferAttribute(data.phases, 1));
      return g;
    }, [data]),
  );

  useFrame(({ clock }, delta) => {
    if (materialRef.current)
      materialRef.current.uniforms["uTime"]!.value = clock.elapsedTime;
    if (groupRef.current && !reducedMotion)
      groupRef.current.rotation.y += delta * 0.004;
  });

  return (
    <group ref={groupRef}>
      <points geometry={geometry} frustumCulled={false}>
        <shaderMaterial
          ref={materialRef}
          vertexShader={STAR_VERTEX}
          fragmentShader={STAR_FRAGMENT}
          uniforms={{
            uTime: { value: 0 },
            uTwinkle: { value: reducedMotion ? 0 : 1 },
          }}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </points>
    </group>
  );
}

/** Light travelling along the edges — the graph's traffic, on the GPU. */
export function EdgeParticles({
  data,
}: {
  data: EdgeParticleData;
}): JSX.Element | null {
  const materialRef = useRef<THREE.ShaderMaterial>(null);

  const geometry = useDisposable(
    useMemo(() => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(data.starts, 3));
      g.setAttribute("aEnd", new THREE.BufferAttribute(data.ends, 3));
      g.setAttribute("aColor", new THREE.BufferAttribute(data.colors, 3));
      g.setAttribute("aOffset", new THREE.BufferAttribute(data.offsets, 1));
      g.setAttribute("aSpeed", new THREE.BufferAttribute(data.speeds, 1));
      return g;
    }, [data]),
  );

  useFrame(({ clock }) => {
    if (materialRef.current)
      materialRef.current.uniforms["uTime"]!.value = clock.elapsedTime;
  });

  if (data.count === 0) return null;
  return (
    <points geometry={geometry} frustumCulled={false}>
      <shaderMaterial
        ref={materialRef}
        vertexShader={FLOW_VERTEX}
        fragmentShader={FLOW_FRAGMENT}
        uniforms={{ uTime: { value: 0 } }}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}

/** Floating names for the landmarks: selected, hovered, and the biggest hubs. */
export function NodeLabels({
  nodes,
  positions,
  selectedIds,
  hoveredId,
  scale,
}: {
  nodes: GraphNode[];
  positions: Positions;
  selectedIds: Set<string>;
  hoveredId: string | null;
  scale: number;
}): JSX.Element {
  return (
    <>
      {nodes.map((node) => {
        const p = positions[node.id]!;
        const active = selectedIds.has(node.id) || node.id === hoveredId;
        return (
          <Billboard
            key={node.id}
            position={[p[0] * scale, p[1] * scale + 1.6, p[2] * scale]}
          >
            <Text
              fontSize={active ? 1.05 : 0.8}
              color={active ? "#f2f7ff" : "#93a1bd"}
              outlineWidth={0.05}
              outlineColor="#04060d"
              anchorX="center"
              anchorY="bottom"
              maxWidth={26}
            >
              {node.label.length > 34
                ? `${node.label.slice(0, 34)}…`
                : node.label}
            </Text>
          </Billboard>
        );
      })}
    </>
  );
}
