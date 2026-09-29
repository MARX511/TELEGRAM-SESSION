/* Login hero: a real-time 3D scene (three.js, vendored locally). A faceted metallic crystal inside a gold
 * wireframe shell, two orbital rings, and a particle network of linked nodes (the sessions/accounts graph).
 * Theme-aware (reads CSS tokens, reacts to "themechange"), pointer parallax, pauses when the tab is hidden,
 * renders a single still frame under prefers-reduced-motion, and falls back to the CSS gradient if WebGL fails. */
import * as THREE from "./vendor/three.module.min.js";

const canvas = document.getElementById("scene");
if (canvas) {
  try {
    boot(canvas);
  } catch (err) {
    canvas.parentElement && canvas.parentElement.classList.add("no-webgl");
    canvas.remove();
  }
}

function tokenColor(name, fallback) {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const parts = raw.split(/\s+/).map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return new THREE.Color(fallback);
  return new THREE.Color().setRGB(parts[0] / 255, parts[1] / 255, parts[2] / 255, THREE.SRGBColorSpace);
}

function mulberry32(seed) {
  let a = seed;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function radialTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.22, "rgba(255,255,255,0.45)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function boot(canvas) {
  const host = canvas.parentElement;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
  camera.position.set(0, 0, 10);
  const world = new THREE.Group();
  scene.add(world);

  scene.add(new THREE.AmbientLight(0xffffff, 0.45));
  const keyLight = new THREE.PointLight(0xffffff, 90, 40);
  keyLight.position.set(4, 3.5, 6);
  scene.add(keyLight);
  const rimLight = new THREE.PointLight(0xffffff, 70, 40);
  rimLight.position.set(-5, -2.5, 4);
  scene.add(rimLight);
  const fill = new THREE.DirectionalLight(0xffffff, 0.6);
  fill.position.set(0, 5, 5);
  scene.add(fill);

  // crystal core + wireframe shell
  const coreMat = new THREE.MeshStandardMaterial({ metalness: 0.8, roughness: 0.2, flatShading: true });
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.35, 0), coreMat);
  world.add(core);
  const shellMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.6 });
  const shell = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.95, 1)), shellMat);
  world.add(shell);
  const innerMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.35 });
  const inner = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.OctahedronGeometry(2.35, 0)), innerMat);
  world.add(inner);

  // orbital rings
  const ringMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.75 });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(2.9, 0.013, 8, 240), ringMat);
  ring.rotation.set(Math.PI * 0.42, 0.35, 0);
  world.add(ring);
  const ring2Mat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.4 });
  const ring2 = new THREE.Mesh(new THREE.TorusGeometry(3.45, 0.009, 8, 240), ring2Mat);
  ring2.rotation.set(Math.PI * 0.6, 0, 0.5);
  world.add(ring2);

  // particle network (deterministic layout) + links between near neighbours
  const rand = mulberry32(20260929);
  const N = 160;
  const nodes = [];
  for (let i = 0; i < N; i++) {
    const theta = 2 * Math.PI * rand();
    const phi = Math.acos(2 * rand() - 1);
    const r = 3.4 + rand() * 1.6;
    nodes.push(new THREE.Vector3(r * Math.sin(phi) * Math.cos(theta), r * Math.sin(phi) * Math.sin(theta) * 0.6, r * Math.cos(phi)));
  }
  const dotTex = radialTexture();
  const pointMat = new THREE.PointsMaterial({ size: 0.16, map: dotTex, transparent: true, depthWrite: false, sizeAttenuation: true });
  const net = new THREE.Group();   // lives at the scene root: it fills the panel behind everything
  net.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(nodes), pointMat));
  const links = [];
  for (let i = 0; i < N; i++) {
    const near = nodes
      .map((p, j) => [j, p.distanceTo(nodes[i])])
      .filter(([j]) => j !== i)
      .sort((a, b) => a[1] - b[1])
      .slice(0, 2);
    for (const [j, d] of near) if (i < j && d < 1.35) links.push(nodes[i].x, nodes[i].y, nodes[i].z, nodes[j].x, nodes[j].y, nodes[j].z);
  }
  const linkGeo = new THREE.BufferGeometry();
  linkGeo.setAttribute("position", new THREE.Float32BufferAttribute(links, 3));
  const linkMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.28, depthWrite: false });
  net.add(new THREE.LineSegments(linkGeo, linkMat));
  scene.add(net);

  // soft halo behind the crystal
  const haloMat = new THREE.SpriteMaterial({ map: dotTex, transparent: true, depthWrite: false });
  const halo = new THREE.Sprite(haloMat);
  halo.scale.set(8, 8, 1);
  halo.position.z = -1.5;
  world.add(halo);

  function applyTheme() {
    const light = document.documentElement.getAttribute("data-theme") === "light";
    const accent = tokenColor("--accent", "#e2b857");
    const accent2 = tokenColor("--accent-2", "#38bdf8");
    const accent3 = tokenColor("--accent-3", "#8b7cf6");
    const blend = light ? THREE.NormalBlending : THREE.AdditiveBlending;
    // no environment map, so a highly metallic core renders near-black on a light page: soften it there
    coreMat.color.set(light ? 0x8ea6e6 : 0x101b36);
    coreMat.metalness = light ? 0.35 : 0.8;
    coreMat.roughness = light ? 0.4 : 0.2;
    coreMat.emissive.copy(accent2).multiplyScalar(light ? 0.12 : 0.2);
    shellMat.color.copy(accent);
    innerMat.color.copy(accent3);
    ringMat.color.copy(accent2);
    ring2Mat.color.copy(accent3);
    pointMat.color.copy(light ? accent3 : accent2);
    linkMat.color.copy(light ? accent3 : accent2);
    linkMat.opacity = light ? 0.35 : 0.28;
    haloMat.color.copy(accent);
    haloMat.opacity = light ? 0.28 : 0.42;
    for (const m of [pointMat, linkMat, haloMat]) { m.blending = blend; m.needsUpdate = true; }
    keyLight.color.copy(accent);
    rimLight.color.copy(accent2);
  }
  applyTheme();

  // Place the crystal in the free space the copy leaves, measured from the DOM, so it never sits behind text.
  // Side-by-side layout: between the brand (top) and the headline block (bottom). Stacked banner (<= 960px):
  // on the inline-end side, next to the copy. Sizes are in CSS px, converted on the z = 0 plane.
  const VIS_H = 2 * camera.position.z * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const CLUSTER = 2 * 2.35;       // diameter of the crystal cluster (outer octahedron) in world units
  const stacked = window.matchMedia("(max-width: 960px)");
  const topEl = host.querySelector("[data-scene-top]");
  const avoidEl = host.querySelector("[data-scene-avoid]");
  let baseX = 0, baseY = 0;
  function layout() {
    const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    const rtl = document.documentElement.dir === "rtl";
    const upp = VIS_H / h;       // world units per CSS pixel
    let cx, cy, size;
    if (stacked.matches || !topEl || !avoidEl) {
      size = Math.min(w * 0.34, h * 0.72);
      cx = rtl ? w * 0.18 : w * 0.82;
      cy = h * 0.5;
    } else {
      const box = host.getBoundingClientRect();
      const top = topEl.getBoundingClientRect().bottom - box.top + 12;
      const bottom = avoidEl.getBoundingClientRect().top - box.top - 12;
      const zone = Math.max(0, bottom - top);
      size = Math.min(Math.max(zone * 0.94, 150), w * 0.62);
      cx = w / 2;
      cy = top + Math.max(zone, size) / 2;
    }
    world.scale.setScalar((size * upp) / CLUSTER);
    baseX = (cx - w / 2) * upp;
    baseY = (h / 2 - cy) * upp;
    world.position.set(baseX, baseY, 0);
    net.scale.setScalar(Math.max(1, (VIS_H * camera.aspect) / 9));
  }

  const target = { x: 0, y: 0 }, cur = { x: 0, y: 0 };
  host.addEventListener("pointermove", (e) => {
    const r = host.getBoundingClientRect();
    target.x = ((e.clientX - r.left) / r.width - 0.5) * 2;
    target.y = ((e.clientY - r.top) / r.height - 0.5) * 2;
  });
  host.addEventListener("pointerleave", () => { target.x = 0; target.y = 0; });

  const clock = new THREE.Clock();
  let raf = 0, running = false;
  const render = () => renderer.render(scene, camera);
  function pose(t) {
    cur.x += (target.x - cur.x) * 0.05;
    cur.y += (target.y - cur.y) * 0.05;
    core.rotation.set(t * 0.22, t * 0.31, 0);
    shell.rotation.set(-t * 0.1, t * 0.16, t * 0.04);
    inner.rotation.set(t * 0.06, -t * 0.09, 0);
    ring.rotation.z = t * 0.18;
    ring2.rotation.z = -t * 0.12;
    net.rotation.y = t * 0.045;
    net.rotation.x = Math.sin(t * 0.18) * 0.08;
    world.rotation.y = cur.x * 0.35;
    world.rotation.x = cur.y * 0.2;
    world.position.y = baseY + Math.sin(t * 0.8) * 0.09;
  }
  function tick() {
    pose(clock.getElapsedTime());
    render();
    raf = requestAnimationFrame(tick);
  }
  function start() {
    if (running || reduce) return;
    running = true;
    raf = requestAnimationFrame(tick);
  }
  function stop() {
    running = false;
    cancelAnimationFrame(raf);
  }

  const relayout = () => { layout(); if (!running) { pose(2.4); render(); } };
  const ro = new ResizeObserver(relayout);
  ro.observe(host);
  if (avoidEl) ro.observe(avoidEl);       // the copy reflows when web fonts arrive or the language changes
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(relayout);
  layout();
  window.addEventListener("themechange", () => { applyTheme(); if (!running) render(); });
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  pose(2.4);
  render();
  host.classList.add("webgl-ready");
  start();
}
