import { useEffect, useRef } from 'react'
import * as THREE from 'three'

export function Airplane3DBackground() {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // 1. Scene & Camera
    const scene = new THREE.Scene()
    scene.fog = new THREE.FogExp2(0x060b18, 0.0035)

    const camera = new THREE.PerspectiveCamera(
      45,
      container.clientWidth / container.clientHeight,
      1,
      1000,
    )
    camera.position.set(0, 15, 120)

    // 2. WebGL Renderer
    const renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance',
    })
    renderer.setSize(container.clientWidth, container.clientHeight)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.2
    container.appendChild(renderer.domElement)

    // 3. Lighting
    const ambientLight = new THREE.AmbientLight(0xddeeff, 1.2)
    scene.add(ambientLight)

    const sunLight = new THREE.DirectionalLight(0xffffff, 2.4)
    sunLight.position.set(80, 120, 50)
    scene.add(sunLight)

    const rimLight = new THREE.DirectionalLight(0x38bdf8, 1.6)
    rimLight.position.set(-60, -20, -50)
    scene.add(rimLight)

    // 4. Build 3D Airplane (Jetliner)
    const airplaneGroup = new THREE.Group()

    // Materials
    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      roughness: 0.25,
      metalness: 0.4,
    })
    const blueTrimMat = new THREE.MeshStandardMaterial({
      color: 0x1d4ed8,
      roughness: 0.3,
      metalness: 0.6,
    })
    const glassMat = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      roughness: 0.1,
      metalness: 0.9,
    })
    const chromeMat = new THREE.MeshStandardMaterial({
      color: 0x94a3b8,
      roughness: 0.2,
      metalness: 0.8,
    })
    const engineGlowMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
    })

    // Fuselage (Body)
    const bodyGeo = new THREE.CylinderGeometry(2.4, 2.2, 34, 24)
    bodyGeo.rotateZ(Math.PI / 2)
    const body = new THREE.Mesh(bodyGeo, bodyMat)
    body.position.set(0, 0, 0)
    airplaneGroup.add(body)

    // Nose Cone
    const noseGeo = new THREE.ConeGeometry(2.4, 8, 24)
    noseGeo.rotateZ(-Math.PI / 2)
    const nose = new THREE.Mesh(noseGeo, bodyMat)
    nose.position.set(21, 0, 0)
    airplaneGroup.add(nose)

    // Cockpit Windows
    const cockpitGeo = new THREE.BoxGeometry(4, 1.4, 3.2)
    const cockpit = new THREE.Mesh(cockpitGeo, glassMat)
    cockpit.position.set(16, 1.4, 0)
    airplaneGroup.add(cockpit)

    // Main Wings
    const wingShape = new THREE.Shape()
    wingShape.moveTo(0, 0)
    wingShape.lineTo(-10, 24)
    wingShape.lineTo(-13, 24)
    wingShape.lineTo(-7, 0)
    wingShape.closePath()

    const extrudeSettings = { depth: 0.6, bevelEnabled: true, bevelSegments: 2, steps: 1, bevelSize: 0.1, bevelThickness: 0.1 }
    const wingGeo = new THREE.ExtrudeGeometry(wingShape, extrudeSettings)
    wingGeo.rotateX(Math.PI / 2)

    // Right Wing
    const rightWing = new THREE.Mesh(wingGeo, bodyMat)
    rightWing.position.set(4, 0, 0)
    airplaneGroup.add(rightWing)

    // Left Wing
    const leftWing = new THREE.Mesh(wingGeo, bodyMat)
    leftWing.position.set(4, 0, 0)
    leftWing.scale.set(1, 1, -1)
    airplaneGroup.add(leftWing)

    // Wingtips (Winglets)
    const wingletGeo = new THREE.BoxGeometry(2, 4, 0.4)
    const rightWinglet = new THREE.Mesh(wingletGeo, blueTrimMat)
    rightWinglet.position.set(-8, 2, 24)
    rightWinglet.rotation.z = -0.2
    airplaneGroup.add(rightWinglet)

    const leftWinglet = new THREE.Mesh(wingletGeo, blueTrimMat)
    leftWinglet.position.set(-8, 2, -24)
    leftWinglet.rotation.z = -0.2
    airplaneGroup.add(leftWinglet)

    // Tail Fin (Vertical Stabilizer)
    const tailFinShape = new THREE.Shape()
    tailFinShape.moveTo(0, 0)
    tailFinShape.lineTo(-7, 10)
    tailFinShape.lineTo(-11, 10)
    tailFinShape.lineTo(-7, 0)
    tailFinShape.closePath()

    const tailFinGeo = new THREE.ExtrudeGeometry(tailFinShape, extrudeSettings)
    const tailFin = new THREE.Mesh(tailFinGeo, blueTrimMat)
    tailFin.position.set(-10, 1.8, 0)
    airplaneGroup.add(tailFin)

    // Tail Wings (Horizontal Stabilizers)
    const tailWingGeo = new THREE.BoxGeometry(5, 0.4, 14)
    const tailWings = new THREE.Mesh(tailWingGeo, bodyMat)
    tailWings.position.set(-14, 1.2, 0)
    airplaneGroup.add(tailWings)

    // Jet Engines (Turbofans)
    const engineGeo = new THREE.CylinderGeometry(1.2, 1.1, 7, 16)
    engineGeo.rotateZ(Math.PI / 2)

    const rightEngine = new THREE.Mesh(engineGeo, chromeMat)
    rightEngine.position.set(2, -2, 9)
    airplaneGroup.add(rightEngine)

    const leftEngine = new THREE.Mesh(engineGeo, chromeMat)
    leftEngine.position.set(2, -2, -9)
    airplaneGroup.add(leftEngine)

    // Engine Glow Cores
    const glowGeo = new THREE.CircleGeometry(0.9, 16)
    glowGeo.rotateY(Math.PI / 2)

    const rightGlow = new THREE.Mesh(glowGeo, engineGlowMat)
    rightGlow.position.set(-1.6, -2, 9)
    airplaneGroup.add(rightGlow)

    const leftGlow = new THREE.Mesh(glowGeo, engineGlowMat)
    leftGlow.position.set(-1.6, -2, -9)
    airplaneGroup.add(leftGlow)

    // Nav Lights
    const greenLightGeo = new THREE.SphereGeometry(0.3, 8, 8)
    const greenLight = new THREE.Mesh(greenLightGeo, new THREE.MeshBasicMaterial({ color: 0x22c55e }))
    greenLight.position.set(-8, 0.4, 24.2)
    airplaneGroup.add(greenLight)

    const redLight = new THREE.Mesh(greenLightGeo, new THREE.MeshBasicMaterial({ color: 0xef4444 }))
    redLight.position.set(-8, 0.4, -24.2)
    airplaneGroup.add(redLight)

    // Scale & Orient Airplane
    airplaneGroup.scale.set(0.72, 0.72, 0.72)
    // Face towards top-right gracefully
    airplaneGroup.rotation.y = Math.PI / 4.2
    airplaneGroup.rotation.z = -0.22
    airplaneGroup.position.set(-18, 20, 10)
    scene.add(airplaneGroup)

    // 5. Procedural Dynamic Cloud Layers
    const cloudsGroup = new THREE.Group()
    const cloudGeo = new THREE.DodecahedronGeometry(8, 1)
    const cloudMat = new THREE.MeshStandardMaterial({
      color: 0x334155,
      roughness: 0.9,
      metalness: 0.1,
      transparent: true,
      opacity: 0.4,
      flatShading: true,
    })

    const cloudCount = 35
    const clouds: THREE.Mesh[] = []

    for (let i = 0; i < cloudCount; i++) {
      const cloud = new THREE.Mesh(cloudGeo, cloudMat)
      const scale = 1.2 + Math.random() * 2.8
      cloud.scale.set(scale * 1.8, scale * 0.8, scale * 1.2)
      cloud.position.set(
        (Math.random() - 0.5) * 300,
        -30 + (Math.random() - 0.5) * 40,
        -80 + Math.random() * 120,
      )
      cloud.rotation.x = Math.random() * Math.PI
      cloud.rotation.y = Math.random() * Math.PI
      clouds.push(cloud)
      cloudsGroup.add(cloud)
    }
    scene.add(cloudsGroup)

    // 6. Glowing Starfield / Atmospheric Dust Particles
    const starCount = 300
    const starGeo = new THREE.BufferGeometry()
    const starPositions = new Float32Array(starCount * 3)

    for (let i = 0; i < starCount * 3; i += 3) {
      starPositions[i] = (Math.random() - 0.5) * 400
      starPositions[i + 1] = (Math.random() - 0.5) * 300
      starPositions[i + 2] = -150 + Math.random() * 200
    }
    starGeo.setAttribute('position', new THREE.BufferAttribute(starPositions, 3))

    const starMat = new THREE.PointsMaterial({
      color: 0x93c5fd,
      size: 1.4,
      transparent: true,
      opacity: 0.7,
    })
    const starField = new THREE.Points(starGeo, starMat)
    scene.add(starField)

    // 7. Jet Contrail Trail (Particles trailing behind engines)
    const trailCount = 60
    const trailGeo = new THREE.BufferGeometry()
    const trailPositions = new Float32Array(trailCount * 3)
    const trailSizes = new Float32Array(trailCount)

    for (let i = 0; i < trailCount; i++) {
      trailSizes[i] = (i / trailCount) * 4.0
    }
    trailGeo.setAttribute('position', new THREE.BufferAttribute(trailPositions, 3))

    const trailMat = new THREE.PointsMaterial({
      color: 0x60a5fa,
      size: 2.2,
      transparent: true,
      opacity: 0.35,
      blending: THREE.AdditiveBlending,
    })
    const contrail = new THREE.Points(trailGeo, trailMat)
    scene.add(contrail)

    // Mouse Interaction
    let mouseX = 0
    let mouseY = 0
    let targetCameraX = 0
    let targetCameraY = 15

    const handleMouseMove = (e: MouseEvent) => {
      const windowHalfX = window.innerWidth / 2
      const windowHalfY = window.innerHeight / 2
      mouseX = (e.clientX - windowHalfX) / windowHalfX
      mouseY = (e.clientY - windowHalfY) / windowHalfY

      targetCameraX = mouseX * 18
      targetCameraY = 15 - mouseY * 12
    }
    window.addEventListener('mousemove', handleMouseMove, { passive: true })

    // Resize Handler
    const handleResize = () => {
      if (!container) return
      const width = container.clientWidth
      const height = container.clientHeight
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
    }
    window.addEventListener('resize', handleResize)

    // 8. Animation Loop
    let animationFrameId: number
    let clock = new THREE.Clock()

    const animate = () => {
      animationFrameId = requestAnimationFrame(animate)
      const elapsedTime = clock.getElapsedTime()

      // Smooth camera parallax
      camera.position.x += (targetCameraX - camera.position.x) * 0.04
      camera.position.y += (targetCameraY - camera.position.y) * 0.04
      camera.lookAt(0, 10, 0)

      // Realistic airplane flight motion
      const flightSpeed = 0.8
      const roll = Math.sin(elapsedTime * flightSpeed) * 0.08 + (mouseX * 0.15)
      const pitch = Math.cos(elapsedTime * flightSpeed * 0.8) * 0.05 - (mouseY * 0.1)
      const hoverY = Math.sin(elapsedTime * flightSpeed * 1.2) * 2.2

      airplaneGroup.rotation.z = -0.22 + roll
      airplaneGroup.rotation.x = pitch
      airplaneGroup.position.y = 20 + hoverY
      airplaneGroup.position.x = -20 + Math.sin(elapsedTime * 0.5) * 4

      // Move clouds continuously to simulate forward speed
      clouds.forEach((cloud) => {
        cloud.position.x -= 0.18
        if (cloud.position.x < -160) {
          cloud.position.x = 160
          cloud.position.y = -30 + (Math.random() - 0.5) * 40
        }
      })

      // Update Contrails
      const positions = trailGeo.attributes.position.array as Float32Array
      for (let i = trailCount - 1; i > 0; i--) {
        positions[i * 3] = positions[(i - 1) * 3] - 0.9
        positions[i * 3 + 1] = positions[(i - 1) * 3 + 1]
        positions[i * 3 + 2] = positions[(i - 1) * 3 + 2]
      }
      // Source at engine
      positions[0] = airplaneGroup.position.x - 10
      positions[1] = airplaneGroup.position.y - 1.5
      positions[2] = airplaneGroup.position.z
      trailGeo.attributes.position.needsUpdate = true

      // Slow rotation of starfield
      starField.rotation.y = elapsedTime * 0.015

      renderer.render(scene, camera)
    }

    animate()

    // Cleanup
    return () => {
      cancelAnimationFrame(animationFrameId)
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('resize', handleResize)
      renderer.dispose()
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement)
      }
    }
  }, [])

  return (
    <div
      ref={containerRef}
      className="airplane-3d-canvas-container"
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        zIndex: 0,
        overflow: 'hidden',
      }}
    />
  )
}
